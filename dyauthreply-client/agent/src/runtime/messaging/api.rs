//! Authenticated loopback IPC. Browser origins are explicit; no secret is
//! returned by health/bootstrap and a cross-origin request cannot acquire it.
use super::{ManualRequest, ManualService};
use axum::{
    extract::{DefaultBodyLimit, Path, Request, State},
    http::{HeaderValue, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde_json::json;
use std::sync::Arc;
use subtle::ConstantTimeEq;
#[derive(Clone)]
struct ApiState {
    service: Arc<ManualService>,
}
#[derive(Clone)]
struct AuthState {
    token: Arc<String>,
    requests: Arc<tokio::sync::Semaphore>,
}
impl AuthState {
    fn new(token: String) -> Self {
        Self {
            token: Arc::new(token),
            requests: Arc::new(tokio::sync::Semaphore::new(32)),
        }
    }
}
pub fn router(service: Arc<ManualService>, token: String) -> Router {
    let state = ApiState { service };
    let auth = AuthState::new(token);
    Router::new()
        .route("/api/agent/v1/manual-send", post(submit).options(preflight))
        .route(
            "/api/agent/v1/commands/{id}",
            get(status).options(preflight),
        )
        .route(
            "/api/agent/v1/accounts/status",
            get(accounts).options(preflight),
        )
        .route(
            "/api/agent/v1/accounts/{id}/reconcile",
            post(reconcile).options(preflight),
        )
        .route(
            "/api/agent/v1/rules/status",
            get(rule_status).options(preflight),
        )
        .route(
            "/api/agent/v1/rules/preview",
            post(rule_preview).options(preflight),
        )
        .route(
            "/api/agent/v1/rules/reload",
            post(rule_reload).options(preflight),
        )
        .layer(DefaultBodyLimit::max(16 * 1024))
        .route_layer(middleware::from_fn_with_state(auth, authorize))
        .with_state(state)
}
/// Applies the same authenticated loopback/origin boundary to other native APIs.
pub fn secure_router(router: Router, token: String) -> Router {
    secure_router_with_limit(router, token, 16 * 1024)
}
pub fn secure_router_with_limit(router: Router, token: String, limit: usize) -> Router {
    router
        .layer(DefaultBodyLimit::max(limit))
        .route_layer(middleware::from_fn_with_state(
            AuthState::new(token),
            authorize,
        ))
}
async fn preflight() -> StatusCode {
    StatusCode::NO_CONTENT
}
async fn authorize(State(state): State<AuthState>, request: Request, next: Next) -> Response {
    let origin = request
        .headers()
        .get("origin")
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned);
    if origin.as_deref().is_some_and(|v| {
        !matches!(
            v,
            "http://127.0.0.1:5173"
                | "http://localhost:5173"
                | "tauri://localhost"
                | "http://tauri.localhost"
        )
    }) {
        return StatusCode::FORBIDDEN.into_response();
    }
    let is_options = request.method() == axum::http::Method::OPTIONS;
    if !is_options {
        let supplied = request
            .headers()
            .get("authorization")
            .and_then(|h| h.to_str().ok())
            .and_then(|h| h.strip_prefix("Bearer "))
            .unwrap_or("");
        if !bool::from(supplied.as_bytes().ct_eq(state.token.as_bytes())) {
            return with_cors((StatusCode::UNAUTHORIZED, Json(json!({
                "code":"native_ipc_unauthorized", "detail":"本地接口认证失败，请刷新客户端连接"
            }))).into_response(), origin);
        }
    }
    let Ok(_permit) = state.requests.clone().try_acquire_owned() else {
        return with_cors(
            (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({"detail":"本地服务繁忙，请稍后重试同一指令"})),
            )
                .into_response(),
            origin,
        );
    };
    with_cors(next.run(request).await, origin)
}

fn with_cors(mut response: Response, origin: Option<String>) -> Response {
    if let Some(origin) = origin {
        if let Ok(value) = HeaderValue::from_str(&origin) {
            response
                .headers_mut()
                .insert("access-control-allow-origin", value);
        }
        response
            .headers_mut()
            .insert("vary", HeaderValue::from_static("Origin"));
        response.headers_mut().insert(
            "access-control-allow-methods",
            HeaderValue::from_static("GET, POST, PATCH, DELETE, OPTIONS"),
        );
        response.headers_mut().insert(
            "access-control-allow-headers",
            HeaderValue::from_static("Authorization, Content-Type"),
        );
    }
    response
}
async fn submit(State(state): State<ApiState>, Json(input): Json<ManualRequest>) -> Response {
    match state.service.submit(input).await {
        Ok(status) => (StatusCode::ACCEPTED, Json(status)).into_response(),
        Err(error) => (
            StatusCode::CONFLICT,
            Json(json!({"detail":error.to_string()})),
        )
            .into_response(),
    }
}
async fn status(State(state): State<ApiState>, Path(id): Path<String>) -> Response {
    match state.service.status(id).await {
        Ok(status) => Json(status).into_response(),
        Err(_) => (StatusCode::NOT_FOUND, Json(json!({"detail":"指令不存在"}))).into_response(),
    }
}

async fn reconcile(State(state): State<ApiState>, Path(id): Path<String>) -> Response {
    match state.service.reconcile(id).await {
        Ok(()) => (StatusCode::ACCEPTED, Json(json!({"status":"queued"}))).into_response(),
        Err(_) => (
            StatusCode::CONFLICT,
            Json(json!({"detail":"账号未就绪或队列繁忙"})),
        )
            .into_response(),
    }
}

async fn rule_reload(State(state): State<ApiState>) -> Response {
    match state.service.reload_rules().await {
        Ok(()) => Json(state.service.rule_status()).into_response(),
        Err(_) => (
            StatusCode::CONFLICT,
            Json(json!({"detail":"规则更新失败，保留原有版本"})),
        )
            .into_response(),
    }
}
async fn rule_status(State(state): State<ApiState>) -> Response {
    Json(state.service.rule_status()).into_response()
}
async fn rule_preview(
    State(state): State<ApiState>,
    Json(input): Json<crate::runtime::rules::MatchInput>,
) -> Response {
    match state.service.preview_rule(input).await {
        Ok(plan) => Json(json!({"mode":"preview_only","plan":plan})).into_response(),
        Err(_) => (
            StatusCode::CONFLICT,
            Json(json!({"detail":"规则预览失败或繁忙，请检查配置与输入"})),
        )
            .into_response(),
    }
}

async fn accounts(State(state): State<ApiState>) -> Response {
    Json(state.service.account_statuses().await).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use tower::ServiceExt;
    fn protected() -> Router {
        Router::new()
            .route("/probe", get(|| async { "ok" }).options(preflight))
            .route_layer(middleware::from_fn_with_state(
                AuthState::new("synthetic-token-for-local-api-test".into()),
                authorize,
            ))
    }
    #[tokio::test]
    async fn bearer_and_origin_are_independent_required_boundaries() {
        for (token, origin, expected) in [
            ("", "", StatusCode::UNAUTHORIZED),
            ("", "http://127.0.0.1:5173", StatusCode::UNAUTHORIZED),
            ("wrong", "", StatusCode::UNAUTHORIZED),
            (
                "synthetic-token-for-local-api-test",
                "https://untrusted.example",
                StatusCode::FORBIDDEN,
            ),
            (
                "synthetic-token-for-local-api-test",
                "http://127.0.0.1:5173",
                StatusCode::OK,
            ),
        ] {
            let mut request = Request::builder().uri("/probe");
            if !token.is_empty() {
                request = request.header("authorization", format!("Bearer {token}"));
            }
            if !origin.is_empty() {
                request = request.header("origin", origin);
            }
            let response = protected()
                .oneshot(request.body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(response.status(), expected);
            if origin == "http://127.0.0.1:5173" {
                assert_eq!(response.headers()["access-control-allow-origin"], origin);
            }
        }
    }
    #[tokio::test]
    async fn preflight_allows_only_explicit_loopback_ui_origin() {
        let response = protected()
            .oneshot(
                Request::builder()
                    .method("OPTIONS")
                    .uri("/probe")
                    .header("origin", "http://127.0.0.1:5173")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::NO_CONTENT);
        assert_eq!(
            response.headers()["access-control-allow-headers"],
            "Authorization, Content-Type"
        );
    }

    #[tokio::test]
    async fn request_admission_is_bounded_and_overload_remains_visible_to_the_ui() {
        let auth = AuthState::new("synthetic-token-for-local-api-test".into());
        let _held = auth.requests.clone().acquire_many_owned(32).await.unwrap();
        let app = Router::new()
            .route("/probe", get(|| async { "ok" }))
            .route_layer(middleware::from_fn_with_state(auth, authorize));
        let response = app
            .oneshot(
                Request::builder()
                    .uri("/probe")
                    .header("authorization", "Bearer synthetic-token-for-local-api-test")
                    .header("origin", "http://127.0.0.1:5173")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::TOO_MANY_REQUESTS);
        assert_eq!(
            response.headers()["access-control-allow-origin"],
            "http://127.0.0.1:5173"
        );
    }
}
