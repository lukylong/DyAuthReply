use std::sync::Arc;

use axum::{extract::State, routing::get, Json, Router};
use serde::{Deserialize, Serialize};
use tokio::sync::watch;
use uuid::Uuid;

use crate::{
    protocol::fixtures::ParityReport,
    state::LifecycleState,
    storage::{retention::DiskPressure, RecoveryReport},
    CORE_SCHEMA_VERSION, PROTOCOL_MODE,
};

pub const HEALTH_API_VERSION: u32 = 5;

/// Process-level phase of the bounded account runtime.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RuntimeHealthPhase {
    /// Runtime components have not finished starting.
    #[default]
    Starting,
    /// Ingress, timers, actors, and dispatch are active.
    Running,
    /// New ingress is closed while accepted work settles.
    Draining,
    /// Every owned runtime task has ended.
    Stopped,
    /// A terminal runtime failure requires operator recovery.
    Faulted,
}

/// Queue and dispatch diagnostics for one weighted work class.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct WorkClassHealthResponse {
    /// Stable class name (`manual`, `automatic`, or `background`).
    pub class: String,
    pub depth: usize,
    pub capacity: usize,
    /// Full/stopping admissions rejected for this class.
    pub rejected: u64,
    pub dispatched: u64,
    pub last_dispatch_lag_ms: u64,
    pub max_dispatch_lag_ms: u64,
}

/// Secret-free queue and execution usage for one fixed signer lane.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct SignerLaneHealthResponse {
    /// Zero-based fixed lane index.
    pub lane: usize,
    pub queued: usize,
    pub in_flight: bool,
    /// Accounts that currently have queued work on this lane.
    pub queued_accounts: usize,
}

/// Aggregate dependency-breaker phases across transport and signer breakers.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct BreakerPhaseCounts {
    pub closed: usize,
    pub open: usize,
    pub half_open: usize,
}

/// Bounded storage-maintenance execution diagnostics.
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct MaintenanceHealthResponse {
    pub in_flight: bool,
    pub last_success_ms: Option<u64>,
    pub failures: u64,
    /// A bounded error category; the type cannot carry chains, paths, or credentials.
    pub last_error: Option<MaintenanceHealthError>,
}

/// Sanitized storage-maintenance failure exposed to health consumers.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MaintenanceHealthError {
    /// One maintenance run failed; details remain in access-controlled logs.
    StorageMaintenanceFailed,
}

/// Live, non-secret process-wide scheduler diagnostics.
///
/// Counts describe bounded structures for the whole installation, never one
/// independently allocated worker pool per account.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutorMode {
    #[default]
    Shadow,
    Attached,
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(default)]
pub struct RuntimeHealthResponse {
    pub phase: RuntimeHealthPhase,
    pub accepting_work: bool,
    pub actor_count: usize,
    pub central_timer_tasks: usize,
    pub timer_alive: bool,
    pub dispatcher_alive: bool,
    pub signer_workers_alive: usize,
    pub scheduled_timer_count: usize,
    pub queue_depth: usize,
    pub queue_capacity: usize,
    pub queue_high_water: usize,
    pub queue_rejected: u64,
    pub queue_coalesced: u64,
    pub stale_work_rejected: u64,
    pub invalid_work_rejected: u64,
    pub deferred_background: u64,
    pub dispatched_total: u64,
    pub execution_mode: ExecutorMode,
    pub execution_capacity: usize,
    pub execution_active: usize,
    pub execution_completed: usize,
    pub execution_recovery_needed: usize,
    pub max_fairness_lag_ms: u64,
    pub work_classes: Vec<WorkClassHealthResponse>,
    pub signer_queue_depth: usize,
    pub signer_queue_capacity: usize,
    pub signer_in_flight: usize,
    pub signer_concurrency: usize,
    pub signer_admitted: u64,
    pub signer_rejected: u64,
    pub signer_completed: u64,
    pub signer_failed: u64,
    pub signer_cancelled: u64,
    pub signer_max_queued: usize,
    pub signer_max_in_flight: usize,
    pub signer_lanes: Vec<SignerLaneHealthResponse>,
    pub open_circuit_count: usize,
    pub breaker_phase_counts: BreakerPhaseCounts,
    pub reconnect_available_tokens: u32,
    pub reconnect_throttled: u64,
    pub heartbeat_sequence: u64,
    pub heartbeat_batches: u64,
    pub heartbeat_last_batch_accounts: usize,
    pub heartbeat_dirty_accounts: usize,
    pub storage_cleanup_runs: u64,
    pub maintenance: MaintenanceHealthResponse,
    pub unresolved_work: usize,
}

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

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct ProtocolExecutionStatus {
    pub ticket_guard: String,
    pub a_bogus: String,
    pub http_transport: String,
    pub account_worker: String,
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
    #[serde(default)]
    pub protocol_execution: ProtocolExecutionStatus,
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
    pub runtime: RuntimeHealthResponse,
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
            protocol_execution: ProtocolExecutionStatus {
                ticket_guard: "rustcrypto".to_owned(),
                a_bogus: "embedded_quickjs".to_owned(),
                http_transport: "wreq_boringssl".to_owned(),
                account_worker: "shadow_disabled".to_owned(),
            },
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
            runtime: RuntimeHealthResponse::default(),
            lifecycle: LifecycleState::Running,
            ready: false,
            degraded_reasons,
        }
    }
}

/// Cheap live snapshot handle shared by runtime writers and HTTP readers.
/// Updates replace the complete value atomically and never make a request
/// handler wait on an account actor or disk operation.
#[derive(Clone, Debug)]
pub struct HealthHandle {
    sender: watch::Sender<HealthResponse>,
}

impl HealthHandle {
    #[must_use]
    pub fn new(initial: HealthResponse) -> Self {
        let (sender, _) = watch::channel(initial);
        Self { sender }
    }

    #[must_use]
    pub fn snapshot(&self) -> HealthResponse {
        self.sender.borrow().clone()
    }

    pub fn update_runtime(&self, runtime: RuntimeHealthResponse) {
        self.sender.send_modify(|health| {
            health.runtime = runtime;
            refresh_runtime_readiness(health);
        });
    }

    pub fn update_runtime_with(&self, update: impl FnOnce(&mut RuntimeHealthResponse)) {
        self.sender.send_modify(|health| {
            update(&mut health.runtime);
            refresh_runtime_readiness(health);
        });
    }

    pub fn update_storage(&self, storage: StorageHealthResponse) {
        self.sender.send_modify(|health| {
            health.storage = storage;
            refresh_degraded_reasons(health);
        });
    }

    /// Publishes only a stable failure category; the underlying error stays out
    /// of both the health payload and its degraded-reason list.
    pub fn set_storage_maintenance_failed(&self, failed: bool) {
        self.sender.send_modify(|health| {
            if failed {
                health.storage.background_work_paused = true;
                health.storage.disposable_writes_allowed = false;
                refresh_degraded_reasons(health);
            }
            health
                .degraded_reasons
                .retain(|reason| reason != "storage_maintenance_failed");
            if failed {
                health
                    .degraded_reasons
                    .push("storage_maintenance_failed".to_owned());
            }
        });
    }

    pub fn set_lifecycle(&self, lifecycle: LifecycleState) {
        self.sender.send_modify(|health| {
            health.lifecycle = lifecycle;
            health.runtime.accepting_work = matches!(lifecycle, LifecycleState::Running);
            health.runtime.phase = match lifecycle {
                LifecycleState::Starting => RuntimeHealthPhase::Starting,
                LifecycleState::Running | LifecycleState::PausedAuto => RuntimeHealthPhase::Running,
                LifecycleState::Draining => RuntimeHealthPhase::Draining,
                LifecycleState::Faulted => RuntimeHealthPhase::Faulted,
                LifecycleState::Stopped => RuntimeHealthPhase::Stopped,
            };
            refresh_runtime_readiness(health);
        });
    }
}

pub fn router(health: HealthResponse) -> Router {
    live_router(HealthHandle::new(health))
}

pub fn live_router(health: HealthHandle) -> Router {
    Router::new()
        .route("/health", get(get_health))
        .with_state(Arc::new(health))
}

async fn get_health(State(health): State<Arc<HealthHandle>>) -> Json<HealthResponse> {
    Json(health.snapshot())
}

fn refresh_degraded_reasons(health: &mut HealthResponse) {
    health.degraded_reasons.retain(|reason| {
        !matches!(
            reason.as_str(),
            "storage_high_pressure"
                | "storage_critical_pressure"
                | "storage_disposable_writes_suppressed"
        )
    });
    if health.storage.pressure == "high" {
        health
            .degraded_reasons
            .push("storage_high_pressure".to_owned());
    } else if health.storage.pressure == "critical" {
        health
            .degraded_reasons
            .push("storage_critical_pressure".to_owned());
    }
    if !health.storage.disposable_writes_allowed {
        health
            .degraded_reasons
            .push("storage_disposable_writes_suppressed".to_owned());
    }
}

fn refresh_runtime_readiness(health: &mut HealthResponse) {
    let runtime_ready = matches!(health.lifecycle, LifecycleState::Running)
        && matches!(health.runtime.phase, RuntimeHealthPhase::Running)
        && health.runtime.timer_alive
        && health.runtime.dispatcher_alive
        && health.runtime.signer_workers_alive == health.runtime.signer_concurrency;
    health.ready = health.protocol_parity_all_verified && runtime_ready;
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
        assert_eq!(health.protocol_response_cases, 34);
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
        assert_eq!(health.runtime, RuntimeHealthResponse::default());
        assert_eq!(health.runtime.phase, RuntimeHealthPhase::Starting);
        assert_eq!(health.lifecycle, LifecycleState::Running);
        assert!(!health.ready);
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
        assert!(!health.ready);
        assert_eq!(health.storage.pressure, "critical");
        assert_eq!(
            health.degraded_reasons,
            vec![
                "storage_critical_pressure",
                "storage_disposable_writes_suppressed"
            ]
        );
    }

    #[tokio::test]
    async fn live_router_observes_runtime_replacements() {
        let parity = crate::protocol::fixtures::verify_embedded_corpus()
            .expect("embedded protocol corpora must verify");
        let handle = HealthHandle::new(HealthResponse::foundation(
            Uuid::new_v4(),
            Uuid::new_v4(),
            &parity,
            StorageHealthResponse::healthy_empty(),
        ));
        handle.update_runtime(RuntimeHealthResponse {
            accepting_work: true,
            actor_count: 300,
            central_timer_tasks: 1,
            scheduled_timer_count: 2_400,
            queue_capacity: 8_192,
            signer_concurrency: 4,
            ..RuntimeHealthResponse::default()
        });
        handle.set_lifecycle(LifecycleState::Draining);

        let response = live_router(handle)
            .oneshot(
                Request::builder()
                    .uri("/health")
                    .body(Body::empty())
                    .expect("request must build"),
            )
            .await
            .expect("health route must respond");
        let body = response
            .into_body()
            .collect()
            .await
            .expect("health body must be readable")
            .to_bytes();
        let health: HealthResponse =
            serde_json::from_slice(&body).expect("health body must be valid JSON");

        assert_eq!(health.runtime.actor_count, 300);
        assert_eq!(health.runtime.central_timer_tasks, 1);
        assert!(!health.runtime.accepting_work);
        assert_eq!(health.runtime.phase, RuntimeHealthPhase::Draining);
        assert_eq!(health.lifecycle, LifecycleState::Draining);
        assert!(!health.ready);
    }

    #[test]
    fn runtime_details_are_serialized_and_legacy_payloads_remain_compatible() {
        let runtime = RuntimeHealthResponse {
            phase: RuntimeHealthPhase::Running,
            timer_alive: true,
            dispatcher_alive: true,
            signer_workers_alive: 4,
            deferred_background: 9,
            work_classes: vec![WorkClassHealthResponse {
                class: "manual".to_owned(),
                depth: 2,
                capacity: 8,
                rejected: 3,
                dispatched: 5,
                last_dispatch_lag_ms: 7,
                max_dispatch_lag_ms: 11,
            }],
            signer_lanes: vec![SignerLaneHealthResponse {
                lane: 0,
                queued: 4,
                in_flight: true,
                queued_accounts: 2,
            }],
            breaker_phase_counts: BreakerPhaseCounts {
                closed: 3,
                open: 1,
                half_open: 2,
            },
            maintenance: MaintenanceHealthResponse {
                in_flight: true,
                last_success_ms: Some(101),
                failures: 1,
                last_error: Some(MaintenanceHealthError::StorageMaintenanceFailed),
            },
            ..RuntimeHealthResponse::default()
        };

        let value = serde_json::to_value(runtime).expect("runtime health must serialize");
        assert_eq!(value["phase"], "running");
        assert_eq!(value["timer_alive"], true);
        assert_eq!(value["dispatcher_alive"], true);
        assert_eq!(value["signer_workers_alive"], 4);
        assert_eq!(value["deferred_background"], 9);
        assert_eq!(value["work_classes"][0]["class"], "manual");
        assert_eq!(value["work_classes"][0]["max_dispatch_lag_ms"], 11);
        assert_eq!(value["signer_lanes"][0]["queued_accounts"], 2);
        assert_eq!(value["breaker_phase_counts"]["half_open"], 2);
        assert_eq!(
            value["maintenance"]["last_error"],
            "storage_maintenance_failed"
        );

        let legacy: RuntimeHealthResponse = serde_json::from_value(serde_json::json!({
            "accepting_work": true,
            "actor_count": 10,
            "queue_depth": 4,
            "open_circuit_count": 1,
            "storage_cleanup_runs": 2
        }))
        .expect("pre-detail runtime health JSON must remain readable");
        assert!(legacy.accepting_work);
        assert_eq!(legacy.actor_count, 10);
        assert_eq!(legacy.queue_depth, 4);
        assert_eq!(legacy.open_circuit_count, 1);
        assert_eq!(legacy.storage_cleanup_runs, 2);
        assert_eq!(legacy.phase, RuntimeHealthPhase::Starting);
        assert!(legacy.work_classes.is_empty());
        assert!(legacy.signer_lanes.is_empty());
        assert_eq!(legacy.breaker_phase_counts, BreakerPhaseCounts::default());
        assert_eq!(legacy.maintenance, MaintenanceHealthResponse::default());
    }

    #[test]
    fn lifecycle_controls_readiness_and_runtime_phase() {
        let parity = crate::protocol::fixtures::verify_embedded_corpus()
            .expect("embedded protocol corpora must verify");
        let handle = HealthHandle::new(HealthResponse::foundation(
            Uuid::new_v4(),
            Uuid::new_v4(),
            &parity,
            StorageHealthResponse::healthy_empty(),
        ));

        assert!(!handle.snapshot().ready);
        handle.set_lifecycle(LifecycleState::Running);
        assert!(!handle.snapshot().ready);

        handle.update_runtime(RuntimeHealthResponse {
            phase: RuntimeHealthPhase::Running,
            accepting_work: true,
            timer_alive: true,
            dispatcher_alive: true,
            signer_workers_alive: 4,
            signer_concurrency: 4,
            ..RuntimeHealthResponse::default()
        });
        assert!(handle.snapshot().ready);

        handle.set_lifecycle(LifecycleState::PausedAuto);
        let paused = handle.snapshot();
        assert!(!paused.ready);
        assert!(!paused.runtime.accepting_work);
        assert_eq!(paused.runtime.phase, RuntimeHealthPhase::Running);

        handle.set_lifecycle(LifecycleState::Running);
        assert!(handle.snapshot().ready);

        handle.update_runtime_with(|runtime| runtime.timer_alive = false);
        assert!(!handle.snapshot().ready);
        handle.set_lifecycle(LifecycleState::Running);
        assert!(!handle.snapshot().ready);

        handle.update_runtime_with(|runtime| {
            runtime.timer_alive = true;
            runtime.signer_workers_alive = 3;
        });
        assert!(!handle.snapshot().ready);
        handle.update_runtime_with(|runtime| runtime.signer_workers_alive = 4);
        assert!(handle.snapshot().ready);

        handle.set_lifecycle(LifecycleState::Draining);
        assert!(!handle.snapshot().ready);
        assert_eq!(
            handle.snapshot().runtime.phase,
            RuntimeHealthPhase::Draining
        );

        handle.set_lifecycle(LifecycleState::Stopped);
        assert!(!handle.snapshot().ready);
        assert_eq!(handle.snapshot().runtime.phase, RuntimeHealthPhase::Stopped);

        handle.set_lifecycle(LifecycleState::Faulted);
        assert!(!handle.snapshot().ready);
        assert_eq!(handle.snapshot().runtime.phase, RuntimeHealthPhase::Faulted);

        let mut parity_failed = parity;
        parity_failed.all_verified = false;
        let parity_failed = HealthHandle::new(HealthResponse::foundation(
            Uuid::new_v4(),
            Uuid::new_v4(),
            &parity_failed,
            StorageHealthResponse::healthy_empty(),
        ));
        parity_failed.update_runtime(RuntimeHealthResponse {
            phase: RuntimeHealthPhase::Running,
            accepting_work: true,
            timer_alive: true,
            dispatcher_alive: true,
            signer_workers_alive: 4,
            signer_concurrency: 4,
            ..RuntimeHealthResponse::default()
        });
        parity_failed.set_lifecycle(LifecycleState::Running);
        assert!(!parity_failed.snapshot().ready);
    }

    #[test]
    fn storage_maintenance_degradation_is_idempotent_and_secret_free() {
        let parity = crate::protocol::fixtures::verify_embedded_corpus()
            .expect("embedded protocol corpora must verify");
        let handle = HealthHandle::new(HealthResponse::foundation(
            Uuid::new_v4(),
            Uuid::new_v4(),
            &parity,
            StorageHealthResponse::healthy_empty(),
        ));

        handle.set_storage_maintenance_failed(true);
        handle.set_storage_maintenance_failed(true);
        assert_eq!(
            handle.snapshot().degraded_reasons,
            vec![
                "storage_disposable_writes_suppressed",
                "storage_maintenance_failed"
            ]
        );

        handle.update_storage(StorageHealthResponse::healthy_empty());
        handle.set_storage_maintenance_failed(false);
        assert!(handle.snapshot().degraded_reasons.is_empty());
    }
}
