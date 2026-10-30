use crate::native_host::{NativeHost, NativeResponse, Status};
use std::sync::Arc;
use tauri::{ipc::Channel, State, WebviewWindow};
fn require_local(window: &WebviewWindow) -> Result<(), String> {
    let url = window.url().map_err(|_| "窗口来源验证失败")?;
    let packaged = (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost"));
    let dev = cfg!(debug_assertions)
        && url.scheme() == "http"
        && matches!(url.host_str(), Some("localhost" | "127.0.0.1"))
        && url.port() == Some(5173);
    if window.label() != "main" || !(packaged || dev) {
        return Err("当前窗口没有本地服务访问权限".into());
    }
    Ok(())
}
#[tauri::command]
pub async fn backend_status(
    window: WebviewWindow,
    host: State<'_, Arc<NativeHost>>,
) -> Result<Status, String> {
    require_local(&window)?;
    Ok(host.status().await)
}
#[tauri::command]
pub async fn native_request(
    window: WebviewWindow,
    host: State<'_, Arc<NativeHost>>,
    path: String,
    method: String,
    body: Option<String>,
    admin_token: Option<String>,
) -> Result<NativeResponse, String> {
    require_local(&window)?;
    if crate::browser_input::needs_english_input(&path, &method) {
        return crate::input_policy::start_after_attempt(
            crate::browser_input::prepare(&window),
            || host.request(path, method, body, admin_token),
            |warning| {
                eprintln!(
                    "[quick-auth] input preparation advisory: {warning}; continuing browser launch"
                )
            },
        )
        .await;
    }
    host.request(path, method, body, admin_token).await
}
#[tauri::command]
pub async fn native_request_image(
    window: WebviewWindow,
    host: State<'_, Arc<NativeHost>>,
    path: String,
) -> Result<tauri::ipc::Response, String> {
    require_local(&window)?;
    Ok(tauri::ipc::Response::new(host.request_image(path).await?))
}
#[tauri::command]
pub async fn native_restart(
    window: WebviewWindow,
    host: State<'_, Arc<NativeHost>>,
) -> Result<(), String> {
    require_local(&window)?;
    host.restart().await
}
#[tauri::command]
pub async fn native_ws_connect(
    window: WebviewWindow,
    host: State<'_, Arc<NativeHost>>,
    channel: Channel<serde_json::Value>,
) -> Result<String, String> {
    require_local(&window)?;
    host.connect_socket(channel).await
}
#[tauri::command]
pub async fn native_ws_send(
    window: WebviewWindow,
    host: State<'_, Arc<NativeHost>>,
    connection_id: String,
    input: serde_json::Value,
) -> Result<(), String> {
    require_local(&window)?;
    host.send_socket(&connection_id, input).await
}
#[tauri::command]
pub async fn native_ws_close(
    window: WebviewWindow,
    host: State<'_, Arc<NativeHost>>,
    connection_id: String,
) -> Result<(), String> {
    require_local(&window)?;
    host.close_socket(&connection_id).await;
    Ok(())
}

#[tauri::command]
pub async fn native_reload_configuration(
    window: WebviewWindow,
    host: State<'_, Arc<NativeHost>>,
) -> Result<(), String> {
    require_local(&window)?;
    host.reload_configuration().await
}
