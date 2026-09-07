mod native_commands;
mod native_host;
mod native_ws;
use native_commands::*;
use native_host::NativeHost;
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use tauri::ipc::Channel;
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent};
use tauri_plugin_updater::UpdaterExt;

#[tauri::command]
async fn force_quit_for_update(app: AppHandle) -> Result<(), String> {
    let host = app.state::<Arc<NativeHost>>().inner().clone();
    host.stop_owned(true).await?;
    host.exit_ready.store(true, Ordering::Release);
    app.exit(0);
    Ok(())
}
fn request_exit(app: AppHandle) {
    let host = app.state::<Arc<NativeHost>>().inner().clone();
    if host.quit_pending.swap(true, Ordering::AcqRel) {
        return;
    }
    tauri::async_runtime::spawn(async move {
        match host.stop_owned(false).await {
            Ok(()) => {
                host.exit_ready.store(true, Ordering::Release);
                app.exit(0);
            }
            Err(error) => {
                host.quit_pending.store(false, Ordering::Release);
                let _ = app.emit("native-exit-error", error);
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.show();
                }
            }
        }
    });
}

// ==================== 应用内自动更新（多镜像竞速 + tauri-plugin-updater）====================

/// dist 仓 Release 上 latest.json 的 GitHub 原站基础路径（rolling latest）。
const DIST_MANIFEST_BASE: &str =
    "https://github.com/lukylong/DyAuthReply-dist/releases/latest/download";

/// 判断某镜像前缀是否为 GitHub 原站（原站不加镜像前缀，直接用 latest.json）。
fn is_origin_mirror(mirror: &str) -> bool {
    let m = mirror.trim().trim_end_matches('/');
    m.is_empty() || m == "https://github.com" || m == "http://github.com"
}

/// 由镜像前缀派生稳定 slug（与 CI 生成 latest-<slug>.json 的规则保持一致）：
/// 去协议头后保留字母数字并小写。例如 `https://ghproxy.net/` -> `ghproxynet`。
fn mirror_slug(mirror: &str) -> String {
    let s = mirror.trim();
    let s = s
        .strip_prefix("https://")
        .or_else(|| s.strip_prefix("http://"))
        .unwrap_or(s);
    s.chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect::<String>()
        .to_lowercase()
}

/// 计算某镜像对应的 manifest（latest.json 变体）URL。
/// 原站 -> 直接 latest.json；镜像 -> `<mirror>` + 原站 latest-<slug>.json（前缀型代理）。
fn manifest_url_for_mirror(mirror: &str) -> String {
    if is_origin_mirror(mirror) {
        format!("{DIST_MANIFEST_BASE}/latest.json")
    } else {
        let slug = mirror_slug(mirror);
        let prefix = mirror.trim();
        let prefix = if prefix.ends_with('/') {
            prefix.to_string()
        } else {
            format!("{prefix}/")
        };
        format!("{prefix}{DIST_MANIFEST_BASE}/latest-{slug}.json")
    }
}

/// 对各镜像 manifest 做轻量竞速探测（GET + 短超时），按响应快慢排序，
/// 成功的在前（按耗时升序），失败的兜底放后（仍保留以便 updater 顺序回退）。
/// 返回 (manifestUrl, 解析后的 Url) 有序列表。
async fn build_sorted_endpoints(mirrors: Vec<String>) -> Vec<(String, url::Url)> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(4))
        .build();

    let candidates: Vec<String> = mirrors.iter().map(|m| manifest_url_for_mirror(m)).collect();

    let mut futs = Vec::new();
    for url in candidates {
        let client = client.as_ref().ok().cloned();
        futs.push(async move {
            let start = Instant::now();
            let ok = match &client {
                Some(c) => match c.get(&url).send().await {
                    Ok(resp) => resp.status().is_success(),
                    Err(_) => false,
                },
                None => false,
            };
            (url, ok, start.elapsed())
        });
    }

    let mut results = futures::future::join_all(futs).await;
    results.sort_by(|a, b| match (a.1, b.1) {
        (true, false) => std::cmp::Ordering::Less,
        (false, true) => std::cmp::Ordering::Greater,
        _ => a.2.cmp(&b.2),
    });

    results
        .into_iter()
        .filter_map(|(url, _ok, _elapsed)| url::Url::parse(&url).ok().map(|u| (url, u)))
        .collect()
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateCheckResult {
    available: bool,
    current_version: String,
    version: Option<String>,
    notes: Option<String>,
    endpoint_used: Option<String>,
}

/// 下载进度事件（通过 ipc::Channel 推给前端）。变体名小写：started/progress/finished。
#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase", tag = "event")]
enum UpdateProgress {
    Started {
        content_length: Option<u64>,
    },
    Progress {
        downloaded: u64,
        content_length: Option<u64>,
    },
    Finished,
}

const MAX_UPDATE_BYTES: usize = 512 * 1024 * 1024;

#[derive(Default)]
struct UpdateCoordinator {
    active: AtomicBool,
}

struct UpdatePermit<'a> {
    active: &'a AtomicBool,
}

impl Drop for UpdatePermit<'_> {
    fn drop(&mut self) {
        self.active.store(false, Ordering::Release);
    }
}

impl UpdateCoordinator {
    fn acquire(&self) -> Result<UpdatePermit<'_>, String> {
        self.active
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| "已有更新任务正在运行".to_string())?;
        Ok(UpdatePermit {
            active: &self.active,
        })
    }
}

fn validate_update_size(bytes: usize) -> Result<(), String> {
    if bytes == 0 || bytes > MAX_UPDATE_BYTES {
        return Err("更新包大小无效".into());
    }
    Ok(())
}

fn install_failure(error: String, restored: Result<(), String>) -> String {
    match restored {
        Ok(()) => format!("更新安装失败，原服务已恢复：{error}"),
        Err(restart) => format!("更新安装失败且原服务恢复失败：{error}；{restart}"),
    }
}

/// 检查更新：镜像竞速排序后用动态 endpoints 调 updater.check()，返回是否有新版及版本/说明。
#[tauri::command]
async fn check_app_update_mirrors(
    app: AppHandle,
    mirrors: Vec<String>,
) -> Result<UpdateCheckResult, String> {
    let current = app.package_info().version.to_string();
    let endpoints = build_sorted_endpoints(mirrors).await;
    if endpoints.is_empty() {
        return Err("没有可用的更新端点".into());
    }
    let first = endpoints.first().map(|(s, _)| s.clone());
    let urls: Vec<url::Url> = endpoints.into_iter().map(|(_, u)| u).collect();

    let updater = app
        .updater_builder()
        .endpoints(urls)
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?;

    match updater.check().await {
        Ok(Some(update)) => Ok(UpdateCheckResult {
            available: true,
            current_version: current,
            version: Some(update.version.clone()),
            notes: update.body.clone(),
            endpoint_used: first,
        }),
        Ok(None) => Ok(UpdateCheckResult {
            available: false,
            current_version: current,
            version: None,
            notes: None,
            endpoint_used: first,
        }),
        Err(e) => Err(e.to_string()),
    }
}

/// 下载并安装更新：镜像竞速 -> check -> downloadAndInstall（带进度），
/// 下载完成、安装前先释放后端 sidecar（18765/lock），再覆盖安装，最后重启。
#[tauri::command]
async fn download_and_install_update(
    app: AppHandle,
    coordinator: tauri::State<'_, UpdateCoordinator>,
    mirrors: Vec<String>,
    on_event: Channel<UpdateProgress>,
) -> Result<(), String> {
    let _permit = coordinator.acquire()?;
    let endpoints = build_sorted_endpoints(mirrors).await;
    if endpoints.is_empty() {
        return Err("没有可用的更新端点".into());
    }
    let urls: Vec<url::Url> = endpoints.into_iter().map(|(_, u)| u).collect();

    let updater = app
        .updater_builder()
        .endpoints(urls)
        .map_err(|e| e.to_string())?
        .build()
        .map_err(|e| e.to_string())?;

    let update = updater
        .check()
        .await
        .map_err(|e| e.to_string())?
        .ok_or_else(|| "当前已是最新版本".to_string())?;

    let ev_chunk = on_event.clone();
    let mut downloaded: u64 = 0;
    let mut started_sent = false;

    let bytes = update
        .download(
            move |chunk_len: usize, content_len: Option<u64>| {
                if !started_sent {
                    started_sent = true;
                    let _ = ev_chunk.send(UpdateProgress::Started {
                        content_length: content_len,
                    });
                }
                downloaded += chunk_len as u64;
                let _ = ev_chunk.send(UpdateProgress::Progress {
                    downloaded,
                    content_length: content_len,
                });
            },
            || {},
        )
        .await
        .map_err(|e| e.to_string())?;
    validate_update_size(bytes.len())?;

    // Stop and verify our child BEFORE invoking install; a timeout aborts the update.
    let host = app.state::<Arc<NativeHost>>().inner().clone();
    host.stop_owned(true).await?;
    if let Err(error) = update.install(bytes) {
        return Err(install_failure(error.to_string(), host.restart().await));
    }
    let _ = on_event.send(UpdateProgress::Finished);
    host.exit_ready.store(true, Ordering::Release);
    app.restart()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[allow(unused_mut)]
    let mut builder = tauri::Builder::default();

    // 单实例守卫（仅桌面）：再次启动时唤回已运行的窗口，避免第二个进程
    // 重复拉起后端导致 18765 端口占用 (WSAEADDRINUSE / Errno 10048)。
    // 必须在其它插件之前注册。
    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }));
    }

    #[cfg(desktop)]
    {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
    }

    let app = builder
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .setup(move |app| {
            app.manage(UpdateCoordinator::default());
            // Setup Tray Menu
            let quit_i = MenuItemBuilder::with_id("quit", "退出").build(app)?;
            let show_i = MenuItemBuilder::with_id("show", "显示主窗口").build(app)?;

            let menu = MenuBuilder::new(app).items(&[&show_i, &quit_i]).build()?;

            let icon_bytes = include_bytes!("../icons/32x32.png");
            let tray_icon = tauri::image::Image::from_bytes(icon_bytes).unwrap_or_else(|_| {
                app.default_window_icon()
                    .cloned()
                    .expect("failed to load window icon")
            });

            let _tray = TrayIconBuilder::new()
                .icon(tray_icon)
                .menu(&menu)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "quit" => {
                        app.exit(0);
                    }
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                })
                .build(app)?;

            let host = Arc::new(NativeHost::new(
                native_host::data_root()?,
                native_host::executable()?,
            )?);
            app.manage(host.clone());
            tauri::async_runtime::spawn(async move {
                if let Err(error) = host.start().await {
                    eprintln!("[native] {error}");
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_status,
            native_request,
            native_restart,
            native_reload_configuration,
            native_ws_connect,
            native_ws_send,
            native_ws_close,
            force_quit_for_update,
            check_app_update_mirrors,
            download_and_install_update
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| match event {
        RunEvent::ExitRequested { api, .. } => {
            if !app_handle
                .state::<Arc<NativeHost>>()
                .exit_ready
                .load(Ordering::Acquire)
            {
                api.prevent_exit();
                request_exit(app_handle.clone());
            }
        }
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => {
            if let Some(window) = app_handle.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
        _ => {}
    });
}

#[cfg(test)]
mod update_tests {
    use super::*;

    #[test]
    fn update_coordinator_is_single_flight_and_releases_on_drop() {
        let coordinator = UpdateCoordinator::default();
        let first = coordinator.acquire().expect("first update owns gate");
        assert!(coordinator.acquire().is_err());
        drop(first);
        assert!(coordinator.acquire().is_ok());
    }

    #[test]
    fn update_payload_requires_nonempty_bounded_bytes() {
        assert!(validate_update_size(0).is_err());
        assert!(validate_update_size(1).is_ok());
        assert!(validate_update_size(MAX_UPDATE_BYTES + 1).is_err());
    }

    #[test]
    fn install_failure_reports_whether_the_old_service_was_restored() {
        assert_eq!(
            install_failure("broken package".into(), Ok(())),
            "更新安装失败，原服务已恢复：broken package"
        );
        assert_eq!(
            install_failure("broken package".into(), Err("restart failed".into())),
            "更新安装失败且原服务恢复失败：broken package；restart failed"
        );
    }
}
