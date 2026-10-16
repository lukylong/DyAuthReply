//! Periodic upload of buffered diagnostics with a runtime snapshot, and the local
//! switch API. Upload failures are recorded in the status only: logging them would
//! feed the buffer that is failing to drain.
use super::{hash_account, shared};
use crate::{health::HealthHandle, license::NativeLicense, runtime::messaging::ManualService};
use axum::{
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{sync::Arc, time::Duration};

const FIRST_DELAY: Duration = Duration::from_secs(60);
const INTERVAL: Duration = Duration::from_mins(15);
/// A crash or forced kill loses at most this much; a clean exit saves in `main`.
const PERSIST_INTERVAL: Duration = Duration::from_secs(30);

#[derive(Clone)]
pub struct Sources {
    pub health: HealthHandle,
    pub messaging: Option<Arc<ManualService>>,
    pub license: Arc<NativeLicense>,
}

fn app_version() -> &'static str {
    option_env!("CLIENT_APP_VERSION").unwrap_or(env!("CARGO_PKG_VERSION"))
}

/// Allow-listed runtime state; account IDs hashed, no identities or content.
pub async fn snapshot(sources: &Sources) -> Value {
    let health = serde_json::to_value(sources.health.snapshot()).unwrap_or_default();
    let runtime = &health["runtime"];
    let mut accounts = Vec::new();
    if let Some(service) = &sources.messaging {
        for status in service.account_statuses().await {
            let state = serde_json::to_value(status.state).unwrap_or_default();
            accounts.push(json!({
                "account": hash_account(&status.account_id),
                "label": status.label,
                "lifecycle": state["lifecycle"],
                "inbound": state["inbound"],
                "send": state["send"],
                "ownership": state["ownership"],
                "identity_verified": status.identity_verified,
            }));
        }
    }
    json!({
        "agent_version": env!("CARGO_PKG_VERSION"),
        "protocol_parity": health["protocol_parity_all_verified"],
        "runtime": {
            "phase": runtime["phase"],
            "actor_count": runtime["actor_count"],
            "queue_depth": runtime["queue_depth"],
            "queue_rejected": runtime["queue_rejected"],
            "open_circuit_count": runtime["open_circuit_count"],
            "signer_failed": runtime["signer_failed"],
        },
        "storage": { "pressure": health["storage"]["pressure"] },
        "accounts": accounts,
    })
}

/// Starts the uploader; it idles while diagnostics are off. It also keeps the
/// saved copy of unsent events current.
#[must_use]
pub fn spawn(sources: Sources) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let mut save = tokio::time::interval(PERSIST_INTERVAL);
        save.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let upload = tokio::time::sleep(FIRST_DELAY);
        tokio::pin!(upload);
        loop {
            tokio::select! {
                _ = save.tick() => save_unsent().await,
                () = &mut upload => {
                    if shared().enabled() {
                        upload_once(&sources).await;
                    }
                    upload.as_mut().reset(tokio::time::Instant::now() + INTERVAL);
                }
            }
        }
    })
}

/// Failures are kept in the status: logging them would feed the buffer.
async fn save_unsent() {
    let _ = tokio::task::spawn_blocking(|| shared().persist()).await;
}

async fn upload_once(sources: &Sources) {
    let state = shared();
    let events = state.begin_upload();
    let count = events.len() as u64;
    let payload = json!({
        "app_version": app_version(),
        "os": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
        "snapshot": snapshot(sources).await,
        "events": events,
    });
    let result = sources.license.upload_diagnostics(payload).await;
    state.finish_upload(result.is_ok());
    match result {
        Ok(_) => {
            state.record(|status| {
                status.last_upload_at = Some(super::now_rfc3339());
                status.last_error.clear();
                status.uploaded_events += count;
            });
            // Accepted events must not be sent again after a restart.
            save_unsent().await;
        }
        Err(error) => {
            let message = super::redact(&error.to_string());
            state.record(|status| status.last_error = message);
        }
    }
}

#[derive(Clone)]
struct Api {
    available: bool,
}

#[derive(Deserialize)]
struct Switch {
    enabled: bool,
}

pub fn router(available: bool, token: String) -> Router {
    let router = Router::new()
        .route(
            "/api/client/v1/diagnostics",
            get(view).put(update).options(options),
        )
        .with_state(Api { available });
    crate::runtime::messaging::api::secure_router(router, token)
}

async fn options() -> StatusCode {
    StatusCode::NO_CONTENT
}

fn current(api: &Api) -> Value {
    let state = shared();
    json!({
        "enabled": state.enabled(),
        "available": api.available,
        "pending_events": state.pending(),
        "status": state.status(),
    })
}

async fn view(State(api): State<Api>) -> Json<Value> {
    Json(current(&api))
}

async fn update(State(api): State<Api>, Json(input): Json<Switch>) -> Response {
    if input.enabled && !api.available {
        return (
            StatusCode::CONFLICT,
            Json(json!({"detail":"激活授权后才能开启运行诊断上报"})),
        )
            .into_response();
    }
    let result = tokio::task::spawn_blocking(move || shared().set_enabled(input.enabled)).await;
    match result {
        Ok(Ok(())) => Json(current(&api)).into_response(),
        _ => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"detail":"诊断开关保存失败"})),
        )
            .into_response(),
    }
}
