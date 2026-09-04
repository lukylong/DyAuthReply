use std::sync::Arc;

use axum::{extract::State, routing::get, Json, Router};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{state::LifecycleState, CORE_SCHEMA_VERSION, PROTOCOL_MODE};

pub const HEALTH_API_VERSION: u32 = 1;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct HealthResponse {
    pub api_version: u32,
    pub service: String,
    pub version: String,
    pub build_hash: String,
    pub instance_id: Uuid,
    pub boot_id: Uuid,
    pub schema_version: u32,
    pub protocol_mode: String,
    pub lifecycle: LifecycleState,
    pub ready: bool,
    pub degraded_reasons: Vec<String>,
}

impl HealthResponse {
    #[must_use]
    pub fn foundation(instance_id: Uuid, boot_id: Uuid) -> Self {
        Self {
            api_version: HEALTH_API_VERSION,
            service: "dy-agent".to_owned(),
            version: env!("CARGO_PKG_VERSION").to_owned(),
            build_hash: option_env!("DY_AGENT_BUILD_HASH")
                .unwrap_or("development")
                .to_owned(),
            instance_id,
            boot_id,
            schema_version: CORE_SCHEMA_VERSION,
            protocol_mode: PROTOCOL_MODE.to_owned(),
            lifecycle: LifecycleState::Running,
            ready: true,
            degraded_reasons: Vec::new(),
        }
    }
}

pub fn router(health: HealthResponse) -> Router {
    Router::new()
        .route("/health", get(get_health))
        .with_state(Arc::new(health))
}

async fn get_health(State(health): State<Arc<HealthResponse>>) -> Json<HealthResponse> {
    Json((*health).clone())
}

#[cfg(test)]
mod tests {
    use axum::{body::Body, http::Request};
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    use super::*;

    #[tokio::test]
    async fn health_route_reports_foundation_contract() {
        let instance_id = Uuid::new_v4();
        let boot_id = Uuid::new_v4();
        let response = router(HealthResponse::foundation(instance_id, boot_id))
            .oneshot(
                Request::builder()
                    .uri("/health")
                    .body(Body::empty())
                    .expect("request must build"),
            )
            .await
            .expect("health route must respond");

        assert_eq!(response.status(), 200);
        let body = response
            .into_body()
            .collect()
            .await
            .expect("health body must be readable")
            .to_bytes();
        let health: HealthResponse =
            serde_json::from_slice(&body).expect("health body must be valid JSON");

        assert_eq!(health.api_version, HEALTH_API_VERSION);
        assert_eq!(health.service, "dy-agent");
        assert_eq!(health.version, env!("CARGO_PKG_VERSION"));
        assert!(!health.build_hash.is_empty());
        assert_eq!(health.instance_id, instance_id);
        assert_eq!(health.boot_id, boot_id);
        assert_eq!(health.schema_version, CORE_SCHEMA_VERSION);
        assert_eq!(health.protocol_mode, "shadow-disabled");
        assert_eq!(health.lifecycle, LifecycleState::Running);
        assert!(health.ready);
        assert!(health.degraded_reasons.is_empty());
    }
}
