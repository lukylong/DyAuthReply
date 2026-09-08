//! Native UI contract and one authenticated, bounded notification socket per window.
use super::Workbench;
use crate::{
    runtime::messaging::{ManualRequest, ManualService},
    state::SendCapability,
};
use axum::{
    extract::{
        ws::{Message, WebSocket},
        Path, Query, State, WebSocketUpgrade,
    },
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;
#[derive(Clone)]
struct App {
    service: Arc<ManualService>,
    db: Arc<Workbench>,
}
pub fn router(service: Arc<ManualService>, db: Arc<Workbench>, token: String) -> Router {
    let router = Router::new()
        .route(
            "/api/client/v1/douyin/account/all",
            get(accounts).options(options),
        )
        .route(
            "/api/client/v1/douyin/account/{account}/conversations",
            get(conversations).options(options),
        )
        .route(
            "/api/client/v1/douyin/account/{account}/profile-stats",
            get(profile_stats).options(options),
        )
        .route(
            "/api/client/v1/douyin/account/{account}/works",
            get(works).options(options),
        )
        .route(
            "/api/client/v1/douyin/account/{account}/conversation/{conversation}/messages",
            get(messages).options(options),
        )
        .route(
            "/api/client/v1/douyin/account/{account}/conversation/{conversation}/refresh-user",
            post(refresh_peer).options(options),
        )
        .route(
            "/api/client/v1/douyin/account/{account}/manual-reply",
            post(submit).options(options),
        )
        .route(
            "/api/client/v1/douyin/worker-command/{command}",
            get(command).options(options),
        )
        .route("/ws/client/douyin/", get(socket))
        .with_state(App { service, db });
    crate::runtime::messaging::api::secure_router(router, token)
}
async fn options() -> StatusCode {
    StatusCode::NO_CONTENT
}
fn error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({"detail":message}))).into_response()
}
fn account_view(
    mut data: Value,
    state: crate::state::AccountRuntimeState,
    identity_verified: bool,
    label: &str,
) -> Value {
    let (credential, detail) = match state.send {
        SendCapability::Sendable => ("sendable", ""),
        SendCapability::RiskControlled => (
            "receive_only",
            "客户端协议发送被平台拒绝；创作者中心发送状态需独立判断，接收状态独立监控",
        ),
        SendCapability::ReceiveOnly => ("receive_only", "发送凭证不完整"),
        SendCapability::AuthExpired => ("invalid", "登录失效，请重新导入凭证"),
        SendCapability::Unknown => ("unknown", "尚未取得本次凭证的发送成功证据"),
    };
    data["credential_state"] = json!(credential);
    let detail = match state.ownership {
        crate::state::OwnershipState::Lost | crate::state::OwnershipState::Expired => {
            "客户端账号租约已失效，等待授权续签与租约恢复；不代表抖音账号登录失效"
        }
        _ => detail,
    };
    data["last_probe_error"] = json!(detail);
    data["status"] = json!(if state.send == SendCapability::AuthExpired {
        2
    } else {
        i32::from(state.can_receive())
    });
    data["auto_reply_enabled"] = json!(identity_verified && state.can_attempt_auto_reply());
    data["runtime_state"] = json!(state);
    data["runtime_label"] = json!(label);
    data["identity_verified"] = json!(identity_verified);
    data
}
async fn accounts(State(app): State<App>) -> Response {
    let statuses = app.service.account_statuses().await;
    let config = match app.service.business_snapshot() {
        Ok(s) => s,
        Err(e) => return error(StatusCode::SERVICE_UNAVAILABLE, &e.to_string()),
    };
    let result = tokio::task::spawn_blocking(move || -> anyhow::Result<Vec<Value>> {
        let counts = app.service.reply_counts_today()?;
        statuses
            .into_iter()
            .map(|s| {
                let mut value = account_view(
                    app.db.account(&s.account_id)?,
                    s.state,
                    s.identity_verified,
                    s.label,
                );
                if let Some(policy) = config.policies.get(&s.account_id) {
                    value["runtime_auto_reply_enabled"] = value["auto_reply_enabled"].clone();
                    value["auto_reply_enabled"] = json!(policy.enabled);
                    value["daily_reply_quota"] = json!(policy.daily_quota);
                    value["min_interval_seconds"] = json!(policy.min_interval_seconds);
                    value["max_interval_seconds"] = json!(policy.max_interval_seconds);
                }
                value["reply_today"] = json!(counts.get(&s.account_id).copied().unwrap_or(0));
                value["business_revision"] = json!(config.document.revision);
                Ok(value)
            })
            .collect()
    })
    .await;
    match result {
        Ok(Ok(rows)) => Json(rows).into_response(),
        _ => error(
            StatusCode::SERVICE_UNAVAILABLE,
            "账号状态读取失败，请稍后重试",
        ),
    }
}
#[derive(Default, Deserialize)]
struct ProfileQuery {
    #[serde(default)]
    refresh: bool,
}
async fn profile_stats(
    State(app): State<App>,
    Path(account): Path<String>,
    Query(query): Query<ProfileQuery>,
) -> Response {
    if !app.service.has_account(&account) {
        return error(StatusCode::NOT_FOUND, "账号未托管");
    }
    match app.service.profile_stats(&account, query.refresh).await {
        Ok(value) => Json(value).into_response(),
        Err(_) => Json(json!({
            "ok":false,
            "error":"拉取失败（凭证失效、平台限制或网络异常），请稍后重试",
            "follower_count":0,
            "following_count":0,
            "aweme_count":0,
            "total_favorited":0,
            "last_profile_sync_at":Value::Null,
            "cached":false
        }))
        .into_response(),
    }
}
#[derive(Deserialize)]
struct WorksQuery {
    #[serde(default = "zero_cursor")]
    cursor: String,
    #[serde(default = "eighteen")]
    count: u8,
}
fn zero_cursor() -> String {
    "0".into()
}
fn eighteen() -> u8 {
    18
}
async fn works(
    State(app): State<App>,
    Path(account): Path<String>,
    Query(query): Query<WorksQuery>,
) -> Response {
    if !app.service.has_account(&account) {
        return error(StatusCode::NOT_FOUND, "账号未托管");
    }
    match app
        .service
        .account_works(&account, &query.cursor, query.count)
        .await
    {
        Ok(page) => {
            let Ok(mut value) = serde_json::to_value(page) else {
                return error(StatusCode::INTERNAL_SERVER_ERROR, "作品结果编码失败");
            };
            value["ok"] = json!(true);
            value["error"] = Value::Null;
            Json(value).into_response()
        }
        Err(error) => {
            tracing::warn!(account_id = account, %error, "native works request failed");
            Json(json!({
                "ok":false,
                "error":"拉取失败（凭证失效、平台限制或网络异常），请稍后重试",
                "items":[],
                "max_cursor":query.cursor,
                "has_more":false
            }))
            .into_response()
        }
    }
}
#[derive(Deserialize)]
struct ListQuery {
    #[serde(default = "one")]
    page: u32,
    #[serde(default = "fifty")]
    page_size: u32,
    #[serde(default)]
    keyword: String,
}
fn one() -> u32 {
    1
}
fn fifty() -> u32 {
    50
}
async fn conversations(
    State(app): State<App>,
    Path(account): Path<String>,
    Query(page): Query<ListQuery>,
) -> Response {
    if !app.service.has_account(&account) {
        return error(StatusCode::NOT_FOUND, "账号未托管");
    }
    match tokio::task::spawn_blocking(move || {
        app.db
            .conversations(&account, page.page, page.page_size, &page.keyword)
    })
    .await
    {
        Ok(Ok(v)) => Json(v).into_response(),
        _ => error(StatusCode::BAD_REQUEST, "会话读取失败，请检查分页参数"),
    }
}
async fn messages(
    State(app): State<App>,
    Path((account, conversation)): Path<(String, String)>,
) -> Response {
    if !app.service.has_account(&account) {
        return error(StatusCode::NOT_FOUND, "账号未托管");
    }
    match tokio::task::spawn_blocking(move || app.db.messages(&account, &conversation)).await {
        Ok(Ok(v)) => Json(v).into_response(),
        _ => error(StatusCode::NOT_FOUND, "会话不存在或消息读取失败"),
    }
}
async fn refresh_peer(
    State(app): State<App>,
    Path((account, conversation)): Path<(String, String)>,
) -> Response {
    if !app.service.has_account(&account) {
        return error(StatusCode::NOT_FOUND, "账号未托管");
    }
    match app.service.refresh_peer(&account, &conversation).await {
        Ok(()) => Json(json!({"success":true,"message":"用户资料已成功更新"})).into_response(),
        Err(_) => Json(json!({"success":false,"message":"接口未返回该用户的最新资料或拉取失败"}))
            .into_response(),
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Send {
    request_id: uuid::Uuid,
    conversation_id: String,
    text: String,
}
async fn submit(
    State(app): State<App>,
    Path(account): Path<String>,
    Json(input): Json<Send>,
) -> Response {
    if !app.service.has_account(&account) {
        return error(StatusCode::NOT_FOUND, "账号未托管");
    }
    let db = app.db.clone();
    let id = account.clone();
    let route = tokio::task::spawn_blocking(move || db.route(&id, &input.conversation_id)).await;
    let Ok(Ok((conversation_id, conversation_short_id))) = route else {
        return error(StatusCode::NOT_FOUND, "会话不存在，未发送消息");
    };
    match app.service.submit(ManualRequest{request_id:input.request_id,account_id:account,conversation_id,conversation_short_id,text:input.text}).await {
        Ok(s)=>(StatusCode::ACCEPTED,Json(json!({"success":true,"command_id":s.command_id,"client_message_id":s.client_message_id,"message":s.message}))).into_response(),
        Err(_)=>error(StatusCode::CONFLICT,"发送未受理：请检查账号状态，重试时保留同一请求")
    }
}
async fn command(State(app): State<App>, Path(id): Path<String>) -> Response {
    match app.service.status(id).await {
        Ok(s)=>Json(json!({"command_id":s.command_id,"consumed":s.status=="done","status":if s.success{"success"}else if s.status=="done"{"failed"}else if s.status=="uncertain"{"unknown"}else{"pending"},"error":if s.success{None}else{Some(s.message)},"message_id":s.platform_message_id,"client_message_id":s.client_message_id})).into_response(),
        Err(_)=>error(StatusCode::NOT_FOUND,"发送指令不存在或状态尚未就绪")
    }
}
async fn socket(State(app): State<App>, ws: WebSocketUpgrade) -> Response {
    let Ok(permit) = app.db.sockets.clone().try_acquire_owned() else {
        return error(StatusCode::TOO_MANY_REQUESTS, "连接数量已达上限");
    };
    let events = app.db.changed.subscribe();
    ws.max_message_size(4096)
        .max_frame_size(4096)
        .on_upgrade(move |socket| run_socket(socket, app, events, permit))
}
async fn run_socket(
    socket: WebSocket,
    app: App,
    mut events: tokio::sync::broadcast::Receiver<Value>,
    _permit: tokio::sync::OwnedSemaphorePermit,
) {
    let (mut sender, mut receiver) = socket.split();
    let mut scope: Option<String> = None;
    loop {
        let message = tokio::select! {
            event=events.recv()=>match event {
                Ok(v)=>{
                    if v["type"]=="shutdown" {break;}
                    if v["type"]=="new_message" && scope.as_ref().is_some_and(|s|v["data"]["account_id"].as_str()!=Some(s.as_str())) {continue;}
                    Message::Text(v.to_string().into())
                },
                Err(_)=>break, // reconnect + REST snapshot repairs notification gaps
            },
            frame=receiver.next()=>match frame {
                Some(Ok(Message::Text(text)))=>{
                    let Ok(v)=serde_json::from_str::<Value>(&text) else{break;};
                    match v["type"].as_str(){
                        Some("subscribe")=>{scope=v["account_id"].as_str().map(str::to_owned);if scope.as_ref().is_some_and(|id|!app.service.has_account(id)){break;}continue;},
                        Some("ping")=>Message::Text(json!({"type":"pong"}).to_string().into()),
                        _=>continue,
                    }
                },
                Some(Ok(Message::Ping(v)))=>Message::Pong(v),
                Some(Ok(Message::Pong(_)))=>continue,
                _=>break,
            },
        };
        if !matches!(
            tokio::time::timeout(std::time::Duration::from_secs(5), sender.send(message)).await,
            Ok(Ok(()))
        ) {
            break;
        }
    }
    let _ = tokio::time::timeout(std::time::Duration::from_secs(1), sender.close()).await;
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::{LifecycleState, OwnershipState};
    #[test]
    fn receive_health_never_proves_sendability_or_erases_risk() {
        let mut state = crate::state::AccountRuntimeState {
            lifecycle: LifecycleState::PausedAuto,
            ownership: OwnershipState::Owned,
            inbound: crate::state::InboundState::WsHealthy,
            ..Default::default()
        };
        for (send, expected) in [
            (SendCapability::Unknown, "unknown"),
            (SendCapability::RiskControlled, "receive_only"),
            (SendCapability::ReceiveOnly, "receive_only"),
            (SendCapability::AuthExpired, "invalid"),
            (SendCapability::Sendable, "sendable"),
        ] {
            state.send = send;
            let value = account_view(
                json!({"credential_state":"sendable","auto_reply_enabled":true}),
                state,
                true,
                "status",
            );
            assert_eq!(value["credential_state"], expected);
            assert_eq!(value["auto_reply_enabled"], false);
            assert_eq!(
                value["last_probe_error"]
                    .as_str()
                    .unwrap()
                    .contains("客户端协议发送"),
                send == SendCapability::RiskControlled
            );
        }
    }
    #[test]
    fn lease_loss_is_not_reported_as_platform_login_failure() {
        let state = crate::state::AccountRuntimeState {
            ownership: OwnershipState::Lost,
            send: SendCapability::Sendable,
            ..Default::default()
        };
        let value = account_view(json!({}), state, true, "租约已丢失");
        assert_eq!(value["credential_state"], "sendable");
        assert!(value["last_probe_error"]
            .as_str()
            .unwrap()
            .contains("等待授权续签"));
        assert_eq!(value["auto_reply_enabled"], false);
    }
    #[test]
    fn running_actor_with_risk_or_unverified_identity_is_not_auto_ready() {
        let mut state = crate::state::AccountRuntimeState {
            lifecycle: LifecycleState::Running,
            ownership: OwnershipState::Owned,
            inbound: crate::state::InboundState::WsHealthy,
            send: SendCapability::Sendable,
            ..Default::default()
        };
        assert_eq!(
            account_view(json!({}), state, true, "")["auto_reply_enabled"],
            true
        );
        assert_eq!(
            account_view(json!({}), state, false, "")["auto_reply_enabled"],
            false
        );
        state.send = SendCapability::RiskControlled;
        assert_eq!(
            account_view(json!({}), state, true, "")["auto_reply_enabled"],
            false
        );
    }
}
