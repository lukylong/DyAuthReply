use super::{Broker, Mode};
use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct StartInput {
    mode: Mode,
    #[serde(default)]
    target_account_id: Option<String>,
}

pub fn router(broker: Arc<Broker>, token: String) -> Router {
    let routes = Router::new()
        .route(
            "/api/client/v1/douyin/creator-session/current",
            get(creator_current).options(options),
        )
        .route(
            "/api/client/v1/douyin/creator-session/start",
            post(creator_start).options(options),
        )
        .route(
            "/api/client/v1/douyin/creator-session/{session_id}/finish",
            post(creator_finish).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/component",
            get(component).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/component/install",
            post(install_component).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/component/rollback",
            post(rollback_component).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/component/repair",
            post(repair_component).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/component/uninstall",
            post(uninstall_component).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/start",
            post(start).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/current",
            get(current).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/{session_id}",
            get(status).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/{session_id}/confirm",
            post(confirm).options(options),
        )
        .route(
            "/api/client/v1/douyin/quick-auth/{session_id}/cancel",
            post(cancel).options(options),
        )
        .with_state(broker);
    crate::runtime::messaging::api::secure_router(routes, token)
}

async fn options() -> StatusCode {
    StatusCode::NO_CONTENT
}

fn result(value: anyhow::Result<Value>) -> Response {
    match value {
        Ok(value) => Json(value).into_response(),
        Err(error) => (
            StatusCode::CONFLICT,
            Json(json!({"detail":error.to_string()})),
        )
            .into_response(),
    }
}

async fn component(State(broker): State<Arc<Broker>>) -> Json<Value> {
    Json(
        serde_json::to_value(broker.component_status()).unwrap_or_else(
            |_| json!({"state":"corrupt","message":"快捷登录组件状态异常","install_required":true}),
        ),
    )
}

async fn install_component(State(broker): State<Arc<Broker>>) -> Response {
    result(
        broker
            .install_component()
            .await
            .and_then(|view| serde_json::to_value(view).map_err(Into::into)),
    )
}

async fn rollback_component(State(broker): State<Arc<Broker>>) -> Response {
    result(
        broker
            .rollback_component()
            .await
            .and_then(|view| serde_json::to_value(view).map_err(Into::into)),
    )
}

async fn repair_component(State(broker): State<Arc<Broker>>) -> Response {
    result(
        broker
            .repair_component()
            .await
            .and_then(|v| serde_json::to_value(v).map_err(Into::into)),
    )
}

async fn uninstall_component(State(broker): State<Arc<Broker>>) -> Response {
    result(
        broker
            .uninstall_component()
            .await
            .and_then(|v| serde_json::to_value(v).map_err(Into::into)),
    )
}

async fn start(State(broker): State<Arc<Broker>>, Json(input): Json<StartInput>) -> Response {
    result(
        broker
            .start(input.mode, input.target_account_id)
            .await
            .and_then(|view| serde_json::to_value(view).map_err(Into::into)),
    )
}

async fn current(State(broker): State<Arc<Broker>>) -> Json<Value> {
    Json(
        broker
            .current()
            .await
            .and_then(|view| serde_json::to_value(view).ok())
            .unwrap_or(Value::Null),
    )
}

async fn status(State(broker): State<Arc<Broker>>, Path(session_id): Path<String>) -> Response {
    result(
        broker
            .status(&session_id)
            .await
            .and_then(|view| serde_json::to_value(view).map_err(Into::into)),
    )
}

async fn confirm(State(broker): State<Arc<Broker>>, Path(session_id): Path<String>) -> Response {
    result(broker.confirm(&session_id).await)
}

async fn cancel(State(broker): State<Arc<Broker>>, Path(session_id): Path<String>) -> Response {
    result(
        broker
            .cancel(&session_id)
            .await
            .and_then(|view| serde_json::to_value(view).map_err(Into::into)),
    )
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CreatorInput {
    account_id: String,
    #[serde(default)]
    confirm_pause: bool,
}
async fn creator_current(State(broker): State<Arc<Broker>>) -> Json<Value> {
    Json(serde_json::to_value(broker.creator_current().await).unwrap_or(Value::Null))
}
async fn creator_start(
    State(broker): State<Arc<Broker>>,
    Json(input): Json<CreatorInput>,
) -> Response {
    result(
        broker
            .creator_start(input.account_id, input.confirm_pause)
            .await
            .and_then(|v| Ok(serde_json::to_value(v)?)),
    )
}
async fn creator_finish(State(broker): State<Arc<Broker>>, Path(id): Path<String>) -> Response {
    result(
        broker
            .creator_finish(&id)
            .await
            .and_then(|v| Ok(serde_json::to_value(v)?)),
    )
}
