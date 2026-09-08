//! Actual HTTP/controller regression: denied rights stay closed until fresh signed grants.
use super::*;
use crate::{
    config::{RuntimeConfig, StorageConfig},
    health::{HealthHandle, HealthResponse, StorageHealthResponse, StorageStartupSnapshot},
    protocol::fixtures::verify_embedded_corpus,
    storage::{retention::DiskPressure, RecoveryReport, SegmentStore, ZstdCodec},
};
use axum::{extract::State, http::StatusCode, response::IntoResponse, routing::post, Json, Router};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use ed25519_dalek::{Signer, SigningKey};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

#[derive(Default)]
struct Authority {
    calls: AtomicUsize,
    releases: AtomicUsize,
    epoch: AtomicUsize,
    released: AtomicBool,
}

fn runtime(root: &Path, store: Arc<CoreStore>) -> RuntimeHandle {
    let config = StorageConfig::recommended();
    let segments = SegmentStore::open_with_codec(
        root.join("segments"),
        config.segment_policies.clone(),
        store.clone(),
        Arc::new(ZstdCodec::default()),
    )
    .unwrap();
    let health = HealthHandle::new(HealthResponse::foundation(
        Uuid::new_v4(),
        Uuid::new_v4(),
        &verify_embedded_corpus().unwrap(),
        StorageHealthResponse::startup(&StorageStartupSnapshot {
            pressure: DiskPressure::Normal,
            disposable_writes_allowed: true,
            background_work_paused: false,
            sealed_segment_count: 0,
            sealed_segment_bytes: 0,
            active_segment_count: 0,
            cleanup_deleted_segments: 0,
            recovery: RecoveryReport::default(),
        }),
    ));
    RuntimeHandle::start(
        RuntimeConfig::recommended(),
        "recovery-test",
        store,
        segments,
        config,
        health,
    )
    .unwrap()
}

async fn authority(
    State(count): State<Arc<Authority>>,
    Json(request): Json<LeaseSyncRequest>,
) -> axum::response::Response {
    count.calls.fetch_add(1, Ordering::SeqCst);
    if !request.activation_token.starts_with("fresh-synthetic") {
        return (
            if request.activation_token == "unauthorized-synthetic" {
                StatusCode::UNAUTHORIZED
            } else {
                StatusCode::FORBIDDEN
            },
            Json(json!({"detail":"请先续签客户端授权"})),
        )
            .into_response();
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let releasing = request
        .accounts
        .iter()
        .any(|a| a.action == LeaseAction::Release);
    if releasing {
        count.releases.fetch_add(1, Ordering::SeqCst);
        count.released.store(true, Ordering::SeqCst);
    } else if count.epoch.load(Ordering::SeqCst) == 0
        || count.released.swap(false, Ordering::SeqCst)
    {
        count.epoch.fetch_add(1, Ordering::SeqCst);
    }
    let epoch = 40 + count.epoch.load(Ordering::SeqCst);
    let results: Vec<_> = request.accounts.iter().map(|a| json!({
        "platform": a.platform, "platform_account_id":a.platform_account_id, "local_account_id":a.local_account_id,
        "action":a.action, "status":if a.action==LeaseAction::Release {"released"} else {"owned"},
        "fence_epoch":if a.action==LeaseAction::Acquire {epoch as u64} else {a.expected_epoch},
        "lease_until_ms":if a.action==LeaseAction::Release {0} else {(now+45)*1000}
    })).collect();
    let claims = json!({"iss":"dyauthreply-account-lease","aud":"dy-agent","sub":request.activation_id,"ver":1,
        "instance_id":request.instance_id,"boot_id":request.boot_id,"request_id":request.request_id,"sequence":request.sequence,
        "iat":now,"exp":now+45,"server_time_ms":now*1000,"results":results,"allow_manual":true,"allow_auto":true});
    let message = format!(
        "{}.{}",
        URL_SAFE_NO_PAD.encode(br#"{"alg":"EdDSA","typ":"JWT"}"#),
        URL_SAFE_NO_PAD.encode(serde_json::to_vec(&claims).unwrap())
    );
    let signature = SigningKey::from_bytes(&[7; 32]).sign(message.as_bytes());
    Json(json!({"token":format!("{message}.{}",URL_SAFE_NO_PAD.encode(signature.to_bytes()))}))
        .into_response()
}

fn snapshot(path: &Path, id: Uuid, server: &str, token: &str, state: &str) {
    std::fs::write(path, json!({"activation_id":id,"activation_token":token,"server_url":server,"local_state":state}).to_string()).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).unwrap();
    }
}

#[tokio::test]
async fn authorization_403_waits_for_rotation_and_recovers_without_restart() {
    denial_recovery("old-synthetic", "active", 1).await;
}

#[tokio::test]
async fn authorization_401_rotation_recovers_without_restart() {
    denial_recovery("unauthorized-synthetic", "active", 1).await;
}

#[tokio::test]
async fn expired_snapshot_waits_without_requests_then_recovers() {
    denial_recovery("old-synthetic", "expired", 0).await;
}

async fn denial_recovery(old_token: &str, initial_state: &str, expected_requests: usize) {
    let root = tempfile::tempdir().unwrap();
    let store = Arc::new(CoreStore::open(root.path()).unwrap());
    let runtime = runtime(root.path(), store.clone());
    let count = Arc::new(Authority::default());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let server = format!("http://{}", listener.local_addr().unwrap());
    let router = Router::new()
        .route("/api/client-auth/agent/leases/sync", post(authority))
        .with_state(count.clone());
    let server_task = tokio::spawn(async move {
        axum::serve(listener, router).await.unwrap();
    });
    let id = Uuid::new_v4();
    let path = root.path().join("authorization.json");
    snapshot(&path, id, &server, old_token, initial_state);
    let fixture: Value =
        serde_json::from_str(include_str!("../../tests/fixtures/account_lease.json")).unwrap();
    let controller = HostedController::start(
        HostedSettings {
            server: server.clone(),
            public_key_pem: fixture["public_key"].as_str().unwrap().into(),
            activation_id: id,
            activation_token: old_token.into(),
            accounts: vec![AccountLeaseOperation {
                platform: "douyin".into(),
                platform_account_id: "synthetic".into(),
                local_account_id: "synthetic".into(),
                action: LeaseAction::Acquire,
                expected_epoch: 0,
            }],
            auth_state_file: Some(path.clone()),
        },
        Uuid::new_v4(),
        Uuid::new_v4(),
        runtime.clone(),
        store,
    )
    .await
    .unwrap();
    for _ in 0..40 {
        if controller.status().last_error.is_some() || controller.status().stopped {
            break;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    let remained_alive = !controller.status().stopped;
    let denied = controller.authorization("synthetic").is_none();
    tokio::time::sleep(Duration::from_millis(2200)).await;
    let old_requests = count.calls.load(Ordering::SeqCst);
    snapshot(&path, id, &server, "fresh-synthetic", "active");
    for _ in 0..160 {
        if controller.authorization("synthetic").is_some() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    let recovered = controller.authorization("synthetic").is_some();
    let cleanup = controller.shutdown().await;
    runtime.drain().await.unwrap();
    server_task.abort();
    let _ = server_task.await;
    assert!(
        remained_alive,
        "403 killed the controller instead of awaiting renewal"
    );
    assert!(denied, "denied account must not retain send authorization");
    assert_eq!(
        old_requests, expected_requests,
        "unchanged rejected token must not be retried"
    );
    assert!(
        recovered,
        "fresh signed authorization did not restore account ownership"
    );
    cleanup.unwrap();
}

#[tokio::test]
async fn owned_lease_is_released_before_reacquisition_after_local_expiry() {
    let root = tempfile::tempdir().unwrap();
    let store = Arc::new(CoreStore::open(root.path()).unwrap());
    let runtime = runtime(root.path(), store.clone());
    let count = Arc::new(Authority::default());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let server = format!("http://{}", listener.local_addr().unwrap());
    let app = Router::new()
        .route("/api/client-auth/agent/leases/sync", post(authority))
        .with_state(count.clone());
    let task = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    let id = Uuid::new_v4();
    let path = root.path().join("auth.json");
    snapshot(&path, id, &server, "fresh-synthetic", "active");
    let fixture: Value =
        serde_json::from_str(include_str!("../../tests/fixtures/account_lease.json")).unwrap();
    let controller = HostedController::start(
        HostedSettings {
            server: server.clone(),
            public_key_pem: fixture["public_key"].as_str().unwrap().into(),
            activation_id: id,
            activation_token: "fresh-synthetic".into(),
            accounts: vec![AccountLeaseOperation {
                platform: "douyin".into(),
                platform_account_id: "synthetic".into(),
                local_account_id: "synthetic".into(),
                action: LeaseAction::Acquire,
                expected_epoch: 0,
            }],
            auth_state_file: Some(path.clone()),
        },
        Uuid::new_v4(),
        Uuid::new_v4(),
        runtime.clone(),
        store,
    )
    .await
    .unwrap();
    let acquired = wait_ownership(&controller, true).await;
    snapshot(&path, id, &server, "fresh-synthetic", "expired");
    let suspended = wait_ownership(&controller, false).await;
    snapshot(&path, id, &server, "fresh-synthetic-rotated", "active");
    let recovered = wait_ownership(&controller, true).await;
    let epoch = controller
        .authorization("synthetic")
        .map(|v| v.token.fence_epoch);
    let releases = count.releases.load(Ordering::SeqCst);
    let cleanup = controller.shutdown().await;
    runtime.drain().await.unwrap();
    task.abort();
    let _ = task.await;
    assert!(acquired && suspended && recovered);
    assert_eq!(epoch, Some(42), "retired epoch was reused");
    assert_eq!(
        releases, 1,
        "server lease must be released before reacquiring"
    );
    cleanup.unwrap();
}

async fn wait_ownership(controller: &HostedController, wanted: bool) -> bool {
    for _ in 0..640 {
        if controller.authorization("synthetic").is_some() == wanted {
            return true;
        }
        if controller.status().stopped {
            return false;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    false
}
