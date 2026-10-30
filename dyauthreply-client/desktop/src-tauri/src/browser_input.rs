//! Only the interactive quick-login entrypoint changes the current input layout.
//! Do not run this on polling, component installation or background account work.
pub fn needs_english_input(path: &str, method: &str) -> bool {
    method == "POST" && path == "/api/client/v1/douyin/quick-auth/start"
}

#[derive(serde::Serialize)]
pub struct InputPreparation {
    pub state: &'static str,
    pub message: String,
}

pub async fn report(window: &tauri::WebviewWindow) -> InputPreparation {
    if !cfg!(target_os = "windows") {
        return InputPreparation {
            state: "not_applicable",
            message: String::new(),
        };
    }
    match prepare(window).await {
        Ok(()) => InputPreparation {
            state: "ready",
            message: "启动前已确认英文键盘；账号登录进度请以下方状态为准".into(),
        },
        Err(error) => {
            eprintln!("[quick-auth] input preparation unconfirmed: {error}");
            InputPreparation {
                state: "unconfirmed",
                message: format!("未确认英文键盘，仍会尝试启动浏览器。{error}"),
            }
        }
    }
}

#[cfg(target_os = "windows")]
pub async fn prepare(window: &tauri::WebviewWindow) -> Result<(), String> {
    use crate::windows_input;
    // The activation must run on the foreground UI thread, not a Tokio worker
    // or the headless Agent. Switching only the worker's HKL is insufficient.
    let hwnd = window.hwnd().map_err(|e| e.to_string())?.0 as usize;
    let (send, receive) = tokio::sync::oneshot::channel();
    window
        .run_on_main_thread(move || {
            // If UI dispatch timed out, do not change input later after the
            // compatibility attempt was abandoned and the browser was started.
            if send.is_closed() {
                return;
            }
            let _ = send.send(windows_input::begin(hwnd));
        })
        .map_err(|e| e.to_string())?;
    let pending = tokio::time::timeout(std::time::Duration::from_secs(2), receive)
        .await
        .map_err(|_| "英文键盘准备超时".to_string())?
        .map_err(|e| e.to_string())??;
    // Let the focused WebView process handle WM_INPUTLANGCHANGEREQUEST before
    // the Agent is asked to spawn Chrome. A successful PostMessage is not proof.
    for _ in 0..20 {
        if windows_input::confirmed(&pending)? {
            eprintln!("[quick-auth] english keyboard preflight confirmed");
            return Ok(());
        }
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    Err(
        "英文键盘切换未确认，请在任务栏选择 ENG（英语·美国），回到 D助手后重试；无需重装浏览器"
            .into(),
    )
}

#[cfg(not(target_os = "windows"))]
pub async fn prepare(_window: &tauri::WebviewWindow) -> Result<(), String> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_interactive_start_changes_input() {
        assert!(needs_english_input(
            "/api/client/v1/douyin/quick-auth/start",
            "POST"
        ));
        for path in [
            "/api/client/v1/douyin/quick-auth/current",
            "/api/client/v1/douyin/quick-auth/component/install",
            "/api/client/v1/douyin/quick-auth/component/repair",
            "/api/client/v1/douyin/quick-auth/abc/cancel",
            "/api/client/v1/douyin/quick-auth/start?background=1",
        ] {
            assert!(!needs_english_input(path, "POST"));
        }
        assert!(!needs_english_input(
            "/api/client/v1/douyin/quick-auth/start",
            "GET"
        ));
    }
}
