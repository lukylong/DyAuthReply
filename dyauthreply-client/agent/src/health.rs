use std::sync::Arc;

use axum::{extract::State, routing::get, Json, Router};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{
    protocol::fixtures::ParityReport,
    state::LifecycleState,
    storage::{retention::DiskPressure, RecoveryReport},
    CORE_SCHEMA_VERSION, PROTOCOL_MODE,
};

pub const HEALTH_API_VERSION: u32 = 4;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[allow(clippy::struct_excessive_bools)]
pub struct StorageHealthResponse {
    pub database_integrity_verified: bool,
    pub segment_integrity_verified: bool,
    pub pressure: String,
    pub disposable_writes_allowed: bool,
    pub background_work_paused: bool,
    pub sealed_segment_count: usize,
    pub sealed_segment_bytes: u64,
    pub active_segment_count: usize,
    pub cleanup_deleted_segments: usize,
    pub recovery_adopted_segments: usize,
    pub recovery_truncated_active_tails: usize,
    pub recovery_completed_deletions: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageStartupSnapshot {
    pub pressure: DiskPressure,
    pub disposable_writes_allowed: bool,
    pub background_work_paused: bool,
    pub sealed_segment_count: usize,
    pub sealed_segment_bytes: u64,
    pub active_segment_count: usize,
    pub cleanup_deleted_segments: usize,
    pub recovery: RecoveryReport,
}

impl StorageHealthResponse {
    #[must_use]
    pub fn startup(snapshot: &StorageStartupSnapshot) -> Self {
        Self {
            database_integrity_verified: true,
            segment_integrity_verified: true,
            pressure: match snapshot.pressure {
                DiskPressure::Normal => "normal",
                DiskPressure::High => "high",
                DiskPressure::Critical => "critical",
            }
            .to_owned(),
            disposable_writes_allowed: snapshot.disposable_writes_allowed,
            background_work_paused: snapshot.background_work_paused,
            sealed_segment_count: snapshot.sealed_segment_count,
            sealed_segment_bytes: snapshot.sealed_segment_bytes,
            active_segment_count: snapshot.active_segment_count,
            cleanup_deleted_segments: snapshot.cleanup_deleted_segments,
            recovery_adopted_segments: snapshot.recovery.adopted_segments,
            recovery_truncated_active_tails: snapshot.recovery.truncated_active_tails,
            recovery_completed_deletions: snapshot.recovery.completed_deletions,
        }
    }

    #[cfg(test)]
    fn healthy_empty() -> Self {
        Self::startup(&StorageStartupSnapshot {
            pressure: DiskPressure::Normal,
            disposable_writes_allowed: true,
            background_work_paused: false,
            sealed_segment_count: 0,
            sealed_segment_bytes: 0,
            active_segment_count: 0,
            cleanup_deleted_segments: 0,
            recovery: RecoveryReport::default(),
        })
    }
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[allow(clippy::struct_excessive_bools)]
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
    pub protocol_parity_all_verified: bool,
    pub protocol_corpus: String,
    pub protocol_corpus_sha256: String,
    pub protocol_reference_revision: String,
    pub protocol_request_cases: usize,
    pub protocol_response_cases: usize,
    pub protocol_request_plan_verified: bool,
    pub protocol_request_plan_corpus: String,
    pub protocol_request_plan_corpus_sha256: String,
    pub protocol_request_plan_reference_revision: String,
    pub protocol_request_plan_cases: usize,
    pub protocol_request_plan_rejection_cases: usize,
    pub storage: StorageHealthResponse,
    pub lifecycle: LifecycleState,
    pub ready: bool,
    pub degraded_reasons: Vec<String>,
}

impl HealthResponse {
    #[must_use]
    pub fn foundation(
        instance_id: Uuid,
        boot_id: Uuid,
        parity: &ParityReport,
        storage: StorageHealthResponse,
    ) -> Self {
        let mut degraded_reasons = Vec::new();
        if !parity.verified {
            degraded_reasons.push("wire_protocol_parity_failed".to_owned());
        }
        if !parity.request_plan_verified {
            degraded_reasons.push("http_request_plan_parity_failed".to_owned());
        }
        if storage.pressure == "high" {
            degraded_reasons.push("storage_high_pressure".to_owned());
        } else if storage.pressure == "critical" {
            degraded_reasons.push("storage_critical_pressure".to_owned());
        }
        if !storage.disposable_writes_allowed {
            degraded_reasons.push("storage_disposable_writes_suppressed".to_owned());
        }
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
            protocol_parity_all_verified: parity.all_verified,
            protocol_corpus: parity.corpus_id.clone(),
            protocol_corpus_sha256: parity.corpus_sha256.clone(),
            protocol_reference_revision: parity.reference_revision.clone(),
            protocol_request_cases: parity.request_cases,
            protocol_response_cases: parity.response_cases,
            protocol_request_plan_verified: parity.request_plan_verified,
            protocol_request_plan_corpus: parity.request_plan_corpus_id.clone(),
            protocol_request_plan_corpus_sha256: parity.request_plan_corpus_sha256.clone(),
            protocol_request_plan_reference_revision: parity
                .request_plan_reference_revision
                .clone(),
            protocol_request_plan_cases: parity.request_plan_cases,
            protocol_request_plan_rejection_cases: parity.request_plan_rejection_cases,
            storage,
            lifecycle: LifecycleState::Running,
            ready: parity.all_verified,
            degraded_reasons,
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
        let response = router(HealthResponse::foundation(
            instance_id,
            boot_id,
            &parity,
            StorageHealthResponse::healthy_empty(),
        ))
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
        assert!(health.protocol_parity_all_verified);
        assert_eq!(health.protocol_corpus, "douyin-pc-im-send-v1");
        assert_eq!(
            health.protocol_reference_revision,
            "9afaf79580b1ee84e8954ff906ff26869d5b7f1f"
        );
        assert_eq!(health.protocol_request_cases, 2);
        assert_eq!(health.protocol_response_cases, 31);
        assert_eq!(health.protocol_corpus_sha256.len(), 64);
        assert!(health.protocol_request_plan_verified);
        assert_eq!(
            health.protocol_request_plan_corpus,
            "douyin-pc-im-http-plan-v1"
        );
        assert_eq!(health.protocol_request_plan_corpus_sha256.len(), 64);
        assert_eq!(
            health.protocol_request_plan_reference_revision,
            "9afaf79580b1ee84e8954ff906ff26869d5b7f1f"
        );
        assert_eq!(health.protocol_request_plan_cases, 2);
        assert_eq!(health.protocol_request_plan_rejection_cases, 30);
        assert_eq!(health.storage.pressure, "normal");
        assert!(health.storage.database_integrity_verified);
        assert!(health.storage.segment_integrity_verified);
        assert!(health.storage.disposable_writes_allowed);
        assert_eq!(health.storage.sealed_segment_count, 0);
        assert_eq!(health.lifecycle, LifecycleState::Running);
        assert!(health.ready);
        assert!(health.degraded_reasons.is_empty());
    }

    #[test]
    fn wire_and_http_plan_parity_remain_diagnostically_distinct() {
        let mut parity = crate::protocol::fixtures::verify_embedded_corpus()
            .expect("embedded protocol corpora must verify");
        parity.request_plan_verified = false;
        parity.all_verified = false;
        let health = HealthResponse::foundation(
            Uuid::new_v4(),
            Uuid::new_v4(),
            &parity,
            StorageHealthResponse::healthy_empty(),
        );

        assert!(health.protocol_parity_verified);
        assert!(!health.protocol_request_plan_verified);
        assert!(!health.protocol_parity_all_verified);
        assert!(!health.ready);
        assert_eq!(
            health.degraded_reasons,
            vec!["http_request_plan_parity_failed"]
        );
    }

    #[test]
    fn critical_storage_pressure_is_explicit_without_collapsing_protocol_parity() {
        let parity = crate::protocol::fixtures::verify_embedded_corpus()
            .expect("embedded protocol corpora must verify");
        let storage = StorageHealthResponse::startup(&StorageStartupSnapshot {
            pressure: DiskPressure::Critical,
            disposable_writes_allowed: false,
            background_work_paused: true,
            sealed_segment_count: 4,
            sealed_segment_bytes: 4096,
            active_segment_count: 0,
            cleanup_deleted_segments: 2,
            recovery: RecoveryReport::default(),
        });
        let health = HealthResponse::foundation(Uuid::new_v4(), Uuid::new_v4(), &parity, storage);

        assert!(health.protocol_parity_all_verified);
        assert!(health.ready);
        assert_eq!(health.storage.pressure, "critical");
        assert_eq!(
            health.degraded_reasons,
            vec![
                "storage_critical_pressure",
                "storage_disposable_writes_suppressed"
            ]
        );
    }
}
