//! Authenticated loopback notifications transported over a Tauri channel, never browser-visible tokens.
use super::native_host::NativeHost;
use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tauri::ipc::Channel;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::{client::IntoClientRequest, Message};
pub struct NativeSocket {
    pub id: String,
    pub sender: mpsc::Sender<Value>,
    pub task: tauri::async_runtime::JoinHandle<()>,
}
impl NativeHost {
    pub async fn connect_socket(
        self: &std::sync::Arc<Self>,
        channel: Channel<Value>,
    ) -> Result<String, String> {
        let auth = self.connection().await?;
        let mut slots = self.socket.lock().await;
        if let Some(old) = slots.take() {
            old.task.abort();
            let _ = old.task.await;
        }
        let mut request = "ws://127.0.0.1:18765/ws/client/douyin/"
            .into_client_request()
            .map_err(|_| "通知连接配置错误")?;
        request.headers_mut().insert(
            "authorization",
            format!("Bearer {}", auth.token)
                .parse()
                .map_err(|_| "通知认证配置错误")?,
        );
        let (mut socket, _) = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            tokio_tungstenite::connect_async(request),
        )
        .await
        .map_err(|_| "通知连接超时")?
        .map_err(|_| "通知连接尚未就绪")?;
        let id = uuid::Uuid::new_v4().to_string();
        let (sender, mut queue) = mpsc::channel::<Value>(16);
        let task = tauri::async_runtime::spawn(async move {
            loop {
                let outgoing = tokio::select! {
                    command=queue.recv()=>match command{
                        Some(v) if v["type"]=="__desktop_close"=>break,
                        Some(v)=>Some(Message::Text(v.to_string().into())),
                        None=>break,
                    },
                    incoming=socket.next()=>match incoming {
                        Some(Ok(Message::Text(text)))=>{if text.len()>65536{break;}
                        if let Ok(data)=serde_json::from_str::<Value>(&text){
                            if data["type"]=="runtime_reload_requested" {
                                // The WebView owns the restart request. Keeping it outside this
                                // socket task prevents the lifecycle path from aborting/awaiting
                                // the very task that observed the reload event.
                                let _=channel.send(json!({"event":"reload_required"}));
                                return;
                            }
                            if channel.send(json!({"event":"message","data":data})).is_err(){break;}}None},
                        Some(Ok(Message::Ping(data)))=>Some(Message::Pong(data)),Some(Ok(Message::Pong(_)))=>None,_=>break,
                    },
                };
                if let Some(outgoing) = outgoing {
                    if !matches!(
                        tokio::time::timeout(
                            std::time::Duration::from_secs(3),
                            socket.send(outgoing)
                        )
                        .await,
                        Ok(Ok(()))
                    ) {
                        break;
                    }
                }
            }
            let _ = channel.send(json!({"event":"closed"}));
        });
        *slots = Some(NativeSocket {
            id: id.clone(),
            sender,
            task,
        });
        Ok(id)
    }
    pub async fn send_socket(&self, id: &str, input: Value) -> Result<(), String> {
        if input.to_string().len() > 4096
            || !matches!(input["type"].as_str(), Some("subscribe" | "ping"))
        {
            return Err("通知订阅内容无效".into());
        }
        let slots = self.socket.lock().await;
        let socket = slots
            .as_ref()
            .filter(|s| s.id == id)
            .ok_or("通知连接已更新")?;
        socket
            .sender
            .try_send(input)
            .map_err(|_| "通知队列繁忙".into())
    }
    pub async fn close_socket(&self, id: &str) {
        let mut slots = self.socket.lock().await;
        if slots.as_ref().is_some_and(|s| s.id == id) {
            if let Some(socket) = slots.take() {
                socket.task.abort();
                let _ = socket.task.await;
            }
        }
    }
}
