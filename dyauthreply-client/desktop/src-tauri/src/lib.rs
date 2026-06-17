use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::{Manager, RunEvent};

struct BackendChild(Mutex<Option<Child>>);

fn find_project_root() -> PathBuf {
    if let Ok(dir) = std::env::var("DYAUTHREPLY_ROOT") {
        let p = PathBuf::from(dir);
        if p.join("dyauthreply-client").is_dir() {
            return p;
        }
    }

    if let Ok(mut dir) = std::env::current_dir() {
        loop {
            if dir.join("dyauthreply-client").is_dir() && dir.join("backend-django").is_dir() {
                return dir;
            }
            if !dir.pop() {
                break;
            }
        }
    }

    PathBuf::from(".")
}

fn python_bin() -> String {
    std::env::var("DYAUTHREPLY_PYTHON").unwrap_or_else(|_| {
        if cfg!(windows) {
            "python".to_string()
        } else {
            "python3".to_string()
        }
    })
}

fn spawn_backend(root: &Path) -> Option<Child> {
    let launcher = root.join("dyauthreply-client/launcher/launcher.py");
    if !launcher.is_file() {
        eprintln!("[dyauthreply] launcher not found: {}", launcher.display());
        return None;
    }

    let mut cmd = Command::new(python_bin());
    cmd.arg(&launcher)
        .current_dir(root)
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .env("DYAUTHREPLY_ROOT", root);

    match cmd.spawn() {
        Ok(child) => {
            println!("[dyauthreply] backend launcher started pid={}", child.id());
            Some(child)
        }
        Err(err) => {
            eprintln!("[dyauthreply] failed to start launcher: {err}");
            None
        }
    }
}

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
    let root = find_project_root();
    println!("[dyauthreply] project root = {}", root.display());

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(BackendChild(Mutex::new(spawn_backend(&root))))
        .setup(move |_app| {
            thread::spawn(|| wait_for_api(8765));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_status])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            if let Some(state) = app_handle.try_state::<BackendChild>() {
                if let Ok(mut guard) = state.0.lock() {
                    if let Some(mut child) = guard.take() {
                        let _ = child.kill();
                    }
                }
            }
        }
    });
}
