use super::*;
use axum::{extract::State, http::StatusCode, response::IntoResponse, routing::post, Json, Router};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use ed25519_dalek::{pkcs8::EncodePublicKey, Signer, SigningKey};
use std::sync::atomic::{AtomicUsize, Ordering};
fn key() -> SigningKey {
    SigningKey::from_bytes(&[9; 32])
}
fn public_key() -> String {
    key()
        .verifying_key()
        .to_public_key_pem(rsa::pkcs8::LineEnding::LF)
        .unwrap()
}
fn token(seq: u64, exp: i64) -> String {
    let header = URL_SAFE_NO_PAD.encode(br#"{"alg":"EdDSA","typ":"JWT"}"#);
    let body=URL_SAFE_NO_PAD.encode(serde_json::to_vec(&json!({"iss":"dyauthreply-license","sub":"activation","activation_id":"activation","license_key_id":"license","device_fingerprint":"device","lease_sequence":seq,"iat":now().unwrap()-1800,"exp":exp,"grace_until":exp+3600,"feature_flags":{"auto_reply":true}})).unwrap());
    let input = format!("{header}.{body}");
    format!(
        "{input}.{}",
        URL_SAFE_NO_PAD.encode(key().sign(input.as_bytes()).to_bytes())
    )
}
fn initial() -> Value {
    json!({"activation_id":"activation","license_key_id":"license","device_fingerprint":"device","activation_token":"old-access","refresh_token":"old-refresh","lease_sequence":1,"lease_token":token(1,now().unwrap()-1),"local_state":"active","next_check_in_at":iso(now().unwrap()-1)})
}
#[test]
fn signed_status_binds_device_and_does_not_trust_unsigned_grace_or_flags() {
    let dir = tempfile::tempdir().unwrap();
    let config = LicenseConfig {
        server_url: "http://127.0.0.1".into(),
        public_key_pem: public_key(),
        state_file: dir.path().join("state.json"),
        api_token: "x".repeat(32),
    };
    let mut value = initial();
    assert_eq!(state::public(&config, &value)["state"], "grace");
    value["plan"] = json!({"feature_flags":{"auto_reply":false}});
    assert_eq!(
        state::public(&config, &value)["plan"]["feature_flags"]["auto_reply"],
        true
    );
    value["device_fingerprint"] = json!("other");
    assert_eq!(state::public(&config, &value)["state"], "invalid");
    let mut value = initial();
    value["lease_token"] = json!("bad");
    value["last_valid_until"] = json!("2099-01-01");
    assert_eq!(state::public(&config, &value)["can_use_business"], false);
}
#[derive(Default)]
struct Fake {
    calls: AtomicUsize,
    rotations: AtomicUsize,
    response: Mutex<Option<Value>>,
}
async fn checkin(
    State(fake): State<Arc<Fake>>,
    Json(request): Json<Value>,
) -> axum::response::Response {
    fake.calls.fetch_add(1, Ordering::SeqCst);
    let mut cached = fake.response.lock().await;
    if let Some(value) = &*cached {
        assert_eq!(value["request_id"], request["request_id"]);
        return Json(value.clone()).into_response();
    }
    assert_eq!(request["refresh_token"], "old-refresh");
    fake.rotations.fetch_add(1, Ordering::SeqCst);
    *cached = Some(
        json!({"request_id":request["request_id"],"status":"active","activation_id":"activation","license_key_id":"license","activation_token":"new-access","refresh_token":"new-refresh","lease_sequence":2,"lease_token":token(2,now().unwrap()+1800),"heartbeat_interval_minutes":30,"grace_period_minutes":60}),
    );
    (
        StatusCode::BAD_GATEWAY,
        Json(json!({"detail":"injected lost response after rotation"})),
    )
        .into_response()
}
#[tokio::test]
async fn persisted_request_recovers_lost_response_across_manager_restart() {
    let fake = Arc::new(Fake::default());
    let app = Router::new()
        .route(
            "/api/client-auth/native-capabilities",
            post(|| async { Json(json!({"renewal_idempotency":1})) }),
        )
        .route("/api/client-auth/check-in", post(checkin))
        .with_state(fake.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state.json");
    let mut value = initial();
    value["server_url"] = json!(format!("http://{addr}"));
    state::save(&path, &value).unwrap();
    let config = LicenseConfig {
        server_url: format!("http://{addr}"),
        public_key_pem: public_key(),
        state_file: path.clone(),
        api_token: "x".repeat(32),
    };
    let manager = NativeLicense::new(config.clone()).unwrap();
    assert!(manager.renew(true).await.is_err());
    assert!(path.with_extension("renewal.json").exists());
    assert_eq!(state::load(&path).unwrap()["refresh_token"], "old-refresh");
    drop(manager);
    let restarted = NativeLicense::new(config).unwrap();
    assert_eq!(restarted.renew(false).await.unwrap()["state"], "active");
    assert_eq!(fake.rotations.load(Ordering::SeqCst), 1);
    assert_eq!(fake.calls.load(Ordering::SeqCst), 2);
    assert_eq!(state::load(&path).unwrap()["refresh_token"], "new-refresh");
    assert!(!path.with_extension("renewal.json").exists());
    let public = restarted.status().to_string();
    assert!(!public.contains("new-refresh") && !public.contains("new-access"));
    server.abort();
    let _ = server.await;
}
#[test]
fn legacy_naive_schedule_is_read_in_server_timezone() {
    assert_eq!(
        next_due(&json!({"next_check_in_at":"2026-09-05T18:00:00"})),
        next_due(&json!({"next_check_in_at":"2026-09-05T10:00:00Z"}))
    );
}

#[cfg(unix)]
#[test]
fn legacy_license_state_permissions_are_tightened_without_following_symlinks() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state.json");
    std::fs::write(&path, br#"{"local_state":"active"}"#).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
    assert_eq!(state::load(&path).unwrap()["local_state"], "active");
    assert_eq!(path.metadata().unwrap().permissions().mode() & 0o777, 0o600);

    let link = dir.path().join("linked.json");
    symlink(&path, &link).unwrap();
    assert!(state::load(&link).is_err());
}

#[tokio::test]
async fn local_license_routes_require_token_and_reject_foreign_origin() {
    use axum::http::Request;
    use tower::ServiceExt;
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("license.json");
    let config = LicenseConfig {
        server_url: "http://127.0.0.1:1".into(),
        public_key_pem: public_key(),
        state_file: path,
        api_token: "x".repeat(32),
    };
    let manager = NativeLicense::new(config).unwrap();
    let router = api::router(manager);
    let response = router
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/client/v1/license/status")
                .body(axum::body::Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let response = router
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/client/v1/license/status")
                .header("authorization", format!("Bearer {}", "x".repeat(32)))
                .header("origin", "https://other.example")
                .body(axum::body::Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let response = router
        .oneshot(
            Request::builder()
                .uri("/api/client/v1/license/status")
                .header("authorization", format!("Bearer {}", "x".repeat(32)))
                .body(axum::body::Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
async fn terminal_runtime_tick_stops_renewal_without_overflow_or_network() {
    let dir = tempfile::tempdir().unwrap();
    let manager = NativeLicense::new(LicenseConfig {
        server_url: "http://127.0.0.1:9".into(),
        public_key_pem: public_key(),
        state_file: dir.path().join("state.json"),
        api_token: "x".repeat(64),
    })
    .unwrap();
    let (ticks, rx) = watch::channel(0);
    let task = manager.start(rx);
    ticks.send_replace(u64::MAX);
    tokio::time::timeout(Duration::from_secs(2), task.join)
        .await
        .expect("terminal tick must stop scheduler")
        .expect("no arithmetic overflow panic");
}
