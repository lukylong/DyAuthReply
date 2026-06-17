use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::{Manager, RunEvent, WindowEvent};
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::TrayIconBuilder;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandChild;

struct BackendChild(Mutex<Option<CommandChild>>);

fn wait_for_api(port: u16) {
    use std::io::{Read, Write};

    let host = "127.0.0.1";
    for _ in 0..120 {
        if let Ok(mut stream) = std::net::TcpStream::connect((host, port)) {
            let req = format!(
                "GET /api/client/v1/health HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
            );
            if stream.write_all(req.as_bytes()).is_ok() {
                let mut buf = [0u8; 512];
                if stream.read(&mut buf).is_ok() {
                    let text = String::from_utf8_lossy(&buf);
                    if text.contains("200") && text.contains("\"ok\"") {
                        println!("[dyauthreply] API ready on {host}:{port}");
                        return;
                    }
                }
            }
        }
        thread::sleep(Duration::from_millis(500));
    }
    eprintln!("[dyauthreply] API health check timeout ({host}:{port})");
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

            // Spawn Backend Sidecar
            let shell = app.shell();
            let sidecar_command = shell.sidecar("launcher");
            let child = match sidecar_command {
                Ok(cmd) => {
                    match cmd.spawn() {
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
                    }
                }
                Err(err) => {
                    eprintln!("[dyauthreply] failed to find sidecar: {err}");
                    None
                }
            };
            app.manage(BackendChild(Mutex::new(child)));

            thread::spawn(|| wait_for_api(8765));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_status])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        match event {
            RunEvent::Exit => {
                if let Some(state) = app_handle.try_state::<BackendChild>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
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
