//! Native UI-compatible license endpoints. No local Django call is made.
use super::{json, Arc, Deserialize, NativeLicense, Result, Value};
use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
#[derive(Deserialize)]
struct Activate {
    license_code: String,
}
#[derive(Deserialize)]
struct CheckIn {
    #[serde(default)]
    force: bool,
}
pub fn router(manager: Arc<NativeLicense>) -> Router {
    let token = manager.token();
    let router = Router::new()
        .route(
            "/api/client/v1/license/status",
            get(status).options(options),
        )
        .route(
            "/api/client/v1/license/activate",
            post(activate).options(options),
        )
        .route(
            "/api/client/v1/license/check-in",
            post(check_in).options(options),
        )
        .route(
            "/api/client/v1/license/deactivate",
            post(deactivate).options(options),
        )
        .route("/api/client/v1/bootstrap", get(bootstrap).options(options))
        .route("/api/client/v1/health", get(health).options(options))
        .route(
            "/api/client/v1/app-update/check",
            get(app_update).options(options),
        )
        .route(
            "/api/client/v1/announcements",
            get(announcements).options(options),
        )
        .with_state(manager);
    crate::runtime::messaging::api::secure_router(router, token)
}
async fn options() -> StatusCode {
    StatusCode::NO_CONTENT
}
async fn status(State(m): State<Arc<NativeLicense>>) -> Json<Value> {
    Json(m.status())
}
fn result(value: Result<Value>) -> Response {
    match value {
        Ok(v) => Json(v).into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail":error.to_string()})),
        )
            .into_response(),
    }
}
async fn activate(State(m): State<Arc<NativeLicense>>, Json(input): Json<Activate>) -> Response {
    result(m.activate(&input.license_code).await.map(|mut value| {
        value["_runtime_reload"] = json!(true);
        value
    }))
}
async fn check_in(State(m): State<Arc<NativeLicense>>, Json(input): Json<CheckIn>) -> Response {
    result(m.renew(input.force).await)
}
async fn deactivate(State(m): State<Arc<NativeLicense>>) -> Response {
    result(m.deactivate().await.map(|mut value| {
        value["_runtime_reload"] = json!(true);
        value
    }))
}
async fn health() -> Json<Value> {
    Json(
        json!({"ok":true,"env":"native","service":"dyauthreply-client","sign_js_ready":true,"sign_js_detail":"Rust native runtime"}),
    )
}
async fn bootstrap(State(m): State<Arc<NativeLicense>>) -> Json<Value> {
    let s = m.status();
    Json(
        json!({"user_id":s["device_fingerprint"],"username":"本地设备","data_dir":m.state_file().parent(),"http_port":18765,"api_prefix":"/api/client/v1","license":s}),
    )
}

#[derive(Default, Deserialize)]
struct UpdateQuery {
    #[serde(default)]
    current: String,
}

async fn app_update(
    State(manager): State<Arc<NativeLicense>>,
    Query(query): Query<UpdateQuery>,
) -> Response {
    result(manager.app_update(&query.current).await)
}

#[derive(Deserialize)]
struct AnnouncementQuery {
    #[serde(default = "announcement_limit")]
    limit: u16,
}

const fn announcement_limit() -> u16 {
    10
}

async fn announcements(
    State(manager): State<Arc<NativeLicense>>,
    Query(query): Query<AnnouncementQuery>,
) -> Response {
    result(manager.announcements(query.limit).await)
}
