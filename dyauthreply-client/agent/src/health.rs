use std::sync::Arc;

use axum::{extract::State, routing::get, Json, Router};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{
    protocol::fixtures::ParityReport, state::LifecycleState, CORE_SCHEMA_VERSION, PROTOCOL_MODE,
};

pub const HEALTH_API_VERSION: u32 = 2;

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
    pub protocol_parity_verified: bool,
    pub protocol_corpus: String,
    pub protocol_corpus_sha256: String,
    pub protocol_reference_revision: String,
    pub protocol_request_cases: usize,
    pub protocol_response_cases: usize,
    pub lifecycle: LifecycleState,
    pub ready: bool,
    pub degraded_reasons: Vec<String>,
}

impl HealthResponse {
    #[must_use]
    pub fn foundation(instance_id: Uuid, boot_id: Uuid, parity: &ParityReport) -> Self {
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
            protocol_parity_verified: parity.verified,
            protocol_corpus: parity.corpus_id.clone(),
            protocol_corpus_sha256: parity.corpus_sha256.clone(),
            protocol_reference_revision: parity.reference_revision.clone(),
            protocol_request_cases: parity.request_cases,
            protocol_response_cases: parity.response_cases,
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
        let parity = crate::protocol::fixtures::verify_embedded_corpus()
            .expect("embedded protocol corpus must verify");
        let response = router(HealthResponse::foundation(instance_id, boot_id, &parity))
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
        assert!(health.protocol_parity_verified);
        assert_eq!(health.protocol_corpus, "douyin-pc-im-send-v1");
        assert_eq!(
            health.protocol_reference_revision,
            "9afaf79580b1ee84e8954ff906ff26869d5b7f1f"
        );
        assert_eq!(health.protocol_request_cases, 2);
        assert_eq!(health.protocol_response_cases, 31);
        assert_eq!(health.protocol_corpus_sha256.len(), 64);
        assert_eq!(health.lifecycle, LifecycleState::Running);
        assert!(health.ready);
        assert!(health.degraded_reasons.is_empty());
    }
}
