#[cfg(debug_assertions)]
use std::path::PathBuf;
use std::io::{Read, Write};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri::ipc::Channel;
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_updater::UpdaterExt;

struct BackendChild(Mutex<Option<CommandChild>>);

const API_PORT: u16 = 8765;

fn api_is_ready(port: u16) -> bool {
    use std::io::{Read, Write};

    let host = "127.0.0.1";
    if let Ok(mut stream) = std::net::TcpStream::connect((host, port)) {
        let req = format!(
            "GET /api/client/v1/health HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        );
        if stream.write_all(req.as_bytes()).is_ok() {
            let mut buf = [0u8; 512];
            if stream.read(&mut buf).is_ok() {
                let text = String::from_utf8_lossy(&buf);
                return text.contains("200") && text.contains("\"ok\"");
            }
        }
    }
    false
}

fn wait_for_api(port: u16) {
    let host = "127.0.0.1";
    for _ in 0..120 {
        if api_is_ready(port) {
            println!("[dyauthreply] API ready on {host}:{port}");
            return;
        }
        thread::sleep(Duration::from_millis(500));
    }
    eprintln!("[dyauthreply] API health check timeout ({host}:{port})");
}

#[cfg(debug_assertions)]
fn dev_launcher_script() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../launcher/launcher.py")
}

fn request_backend_shutdown(port: u16) {
    let host = "127.0.0.1";
    let Ok(mut stream) = std::net::TcpStream::connect((host, port)) else {
        return;
    };
    let req = format!(
        "POST /api/client/v1/lifecycle/shutdown HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
    );
    let _ = stream.write_all(req.as_bytes());
    let _ = stream.flush();
    let mut buf = [0u8; 256];
    let _ = stream.read(&mut buf);
    thread::sleep(Duration::from_millis(600));
}

fn shutdown_backend(app: &AppHandle) {
    request_backend_shutdown(API_PORT);

    if let Some(state) = app.try_state::<BackendChild>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(child) = guard.take() {
                let _ = child.kill();
            }
        }
    }
}

fn spawn_backend(shell: &tauri_plugin_shell::Shell<tauri::Wry>) -> Option<CommandChild> {
    #[cfg(debug_assertions)]
    {
        if api_is_ready(API_PORT) {
            println!("[dyauthreply] dev: reusing existing API on 127.0.0.1:{API_PORT}");
            return None;
        }
        let script = dev_launcher_script();
        if script.is_file() {
            println!("[dyauthreply] dev: starting python launcher {}", script.display());
            match shell
                .command("python3")
                .args([script.to_string_lossy().to_string()])
                .spawn()
            {
                Ok((mut rx, child)) => {
                    tauri::async_runtime::spawn(async move {
                        use tauri_plugin_shell::process::CommandEvent;
                        while let Some(event) = rx.recv().await {
                            match event {
                                CommandEvent::Stdout(line_bytes) => {
                                    let line = String::from_utf8_lossy(&line_bytes);
                                    print!("[launcher stdout] {}", line);
                                }
                                CommandEvent::Stderr(line_bytes) => {
                                    let line = String::from_utf8_lossy(&line_bytes);
                                    eprint!("[launcher stderr] {}", line);
                                }
                                CommandEvent::Terminated(payload) => {
                                    println!("[launcher terminated] status: {:?}", payload.code);
                                }
                                _ => {}
                            }
                        }
                    });
                    return Some(child);
                }
                Err(err) => eprintln!("[dyauthreply] dev: failed to spawn python launcher: {err}"),
            }
        } else {
            eprintln!(
                "[dyauthreply] dev: launcher script not found at {}",
                script.display()
            );
        }
    }

    match shell.sidecar("launcher") {
        Ok(cmd) => match cmd.spawn() {
            Ok((mut rx, child)) => {
                println!("[dyauthreply] backend sidecar launcher started");
                tauri::async_runtime::spawn(async move {
                    use tauri_plugin_shell::process::CommandEvent;
                    while let Some(event) = rx.recv().await {
                        match event {
                            CommandEvent::Stdout(line_bytes) => {
                                let line = String::from_utf8_lossy(&line_bytes);
                                print!("[sidecar stdout] {}", line);
                            }
                            CommandEvent::Stderr(line_bytes) => {
                                let line = String::from_utf8_lossy(&line_bytes);
                                eprint!("[sidecar stderr] {}", line);
                            }
                            CommandEvent::Terminated(payload) => {
                                println!("[sidecar terminated] status: {:?}", payload.code);
                            }
                            _ => {}
                        }
                    }
                });
                Some(child)
            }
            Err(err) => {
                eprintln!("[dyauthreply] failed to spawn sidecar: {err}");
                None
            }
        },
        Err(err) => {
            eprintln!("[dyauthreply] failed to find sidecar: {err}");
            None
        }
    }
}

#[tauri::command]
fn backend_status() -> String {
    "running".to_string()
}

/// 更新安装前调用：强制整进程退出（含托盘隐藏态）。
/// 先通知后端 sidecar 释放 8765/lock 并 kill，再 app.exit(0)，
/// 确保 updater 覆盖安装前旧进程已完全退出（修复 Windows 进程残留）。
#[tauri::command]
fn force_quit_for_update(app: AppHandle) {
    shutdown_backend(&app);
    app.exit(0);
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

    let candidates: Vec<String> = mirrors
        .iter()
        .map(|m| manifest_url_for_mirror(m))
        .collect();

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
    Started { content_length: Option<u64> },
    Progress { downloaded: u64, content_length: Option<u64> },
    Finished,
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
/// 下载完成、安装前先释放后端 sidecar（8765/lock），再覆盖安装，最后重启。
#[tauri::command]
async fn download_and_install_update(
    app: AppHandle,
    mirrors: Vec<String>,
    on_event: Channel<UpdateProgress>,
) -> Result<(), String> {
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

    let app_for_finish = app.clone();
    let ev_chunk = on_event.clone();
    let ev_finish = on_event.clone();
    let mut downloaded: u64 = 0;
    let mut started_sent = false;

    update
        .download_and_install(
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
            move || {
                // 下载完成、覆盖安装前：先优雅释放后端 sidecar（关闭 8765 / 释放 launcher.lock），
                // 与 NSIS preInstall killMode 形成双保险，杜绝 Windows 进程残留。
                shutdown_backend(&app_for_finish);
                let _ = ev_finish.send(UpdateProgress::Finished);
            },
        )
        .await
        .map_err(|e| e.to_string())?;

    // 安装完成后重启（macOS 需手动；Windows NSIS 多由安装器 /R 处理，single-instance 防重复拉起）。
    app.restart()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[allow(unused_mut)]
    let mut builder = tauri::Builder::default();

    // 单实例守卫（仅桌面）：再次启动时唤回已运行的窗口，避免第二个进程
    // 重复拉起后端导致 8765 端口占用 (WSAEADDRINUSE / Errno 10048)。
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
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![]),
        ))
        .on_window_event(|window, event| match event {
            // 点 × 仅隐藏到托盘，不退出；真正退出走托盘菜单 / Cmd+Q / ExitRequested
            WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = window.hide();
            }
            _ => {}
        })
        .setup(move |app| {
            // Setup Tray Menu
            let quit_i = MenuItemBuilder::with_id("quit", "退出").build(app)?;
            let show_i = MenuItemBuilder::with_id("show", "显示主窗口").build(app)?;

            let menu = MenuBuilder::new(app)
                .items(&[&show_i, &quit_i])
                .build()?;

            let icon_bytes = include_bytes!("../icons/32x32.png");
            let tray_icon = tauri::image::Image::from_bytes(icon_bytes)
                .unwrap_or_else(|_| app.default_window_icon().cloned().expect("failed to load window icon"));

            let _tray = TrayIconBuilder::new()
                .icon(tray_icon)
                .menu(&menu)
                .on_menu_event(|app, event| {
                    match event.id().as_ref() {
                        "quit" => {
                            shutdown_backend(app);
                            app.exit(0);
                        }
                        "show" => {
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                        _ => {}
                    }
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

            // Spawn Backend (dev: python launcher.py；release: PyInstaller sidecar)
            let shell = app.shell();
            let child = spawn_backend(&shell);
            app.manage(BackendChild(Mutex::new(child)));

            thread::spawn(|| wait_for_api(API_PORT));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            backend_status,
            force_quit_for_update,
            check_app_update_mirrors,
            download_and_install_update
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        match event {
            RunEvent::ExitRequested { .. } => {
                shutdown_backend(app_handle);
            }
            RunEvent::Exit => {
                shutdown_backend(app_handle);
            }
            #[cfg(target_os = "macos")]
            RunEvent::Reopen { .. } => {
                if let Some(window) = app_handle.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            _ => {}
        }
    });
}
