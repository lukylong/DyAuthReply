#[cfg(debug_assertions)]
use std::path::PathBuf;
use std::io::{Read, Write};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::TrayIconBuilder;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
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
                    if let tauri::tray::TrayIconEvent::Click { .. } = event {
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
        .invoke_handler(tauri::generate_handler![backend_status])
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
