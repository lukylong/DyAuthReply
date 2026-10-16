use super::*;
use axum::{extract::State, http::Uri, response::IntoResponse, routing::any, Router};
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};

#[derive(Clone)]
struct Fixture {
    profile_ok: bool,
    code: u64,
    message: &'static str,
    wrapper: bool,
    probes: Arc<AtomicUsize>,
}
async fn respond(State(f): State<Fixture>, uri: Uri) -> axum::response::Response {
    match uri.path() {
        "/aweme/v1/web/query/user/" => {
            "{\"status_code\":0,\"user_uid\":\"123456\"}".into_response()
        }
        "/aweme/v1/web/user/profile/other/" => {
            if f.profile_ok {
                "{\"status_code\":0,\"user\":{\"uid\":\"123456\",\"sec_uid\":\"synthetic-sec\",\"nickname\":\"synthetic\"}}".into_response()
            } else {
                Vec::<u8>::new().into_response()
            }
        }
        "/v1/message/get_by_user" => {
            f.probes.fetch_add(1, Ordering::SeqCst);
            let mut bytes = Vec::new();
            crate::protocol::wire::append_varint_field(&mut bytes, 3, f.code).unwrap();
            crate::protocol::wire::append_string_field(&mut bytes, 4, f.message).unwrap();
            if f.wrapper {
                let mut inner = Vec::new();
                let mut wrapper = Vec::new();
                crate::protocol::wire::append_varint_field(&mut wrapper, 2, 123).unwrap();
                crate::protocol::wire::append_bytes_field(&mut inner, 200, &wrapper).unwrap();
                crate::protocol::wire::append_bytes_field(&mut bytes, 6, &inner).unwrap();
            }
            bytes.into_response()
        }
        _ => axum::http::StatusCode::NOT_FOUND.into_response(),
    }
}
async fn fixture(
    code: u64,
    message: &'static str,
    wrapper: bool,
) -> (
    NativeAccountSession,
    Arc<AtomicUsize>,
    tokio::task::JoinHandle<()>,
) {
    fixture_with_profile(false, code, message, wrapper).await
}
async fn fixture_with_profile(
    profile_ok: bool,
    code: u64,
    message: &'static str,
    wrapper: bool,
) -> (
    NativeAccountSession,
    Arc<AtomicUsize>,
    tokio::task::JoinHandle<()>,
) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let probes = Arc::new(AtomicUsize::new(0));
    let state = Fixture {
        profile_ok,
        code,
        message,
        wrapper,
        probes: probes.clone(),
    };
    let server = tokio::spawn(async move {
        axum::serve(
            listener,
            Router::new().fallback(any(respond)).with_state(state),
        )
        .await
        .unwrap();
    });
    let raw = serde_json::json!({"account_id":"synthetic","user_agent":"Mozilla/5.0 Chrome/124.0.0.0","expected_sec_uid":"synthetic-sec","storage_state":{"cookies":[{"name":"sessionid","value":"synthetic-session"}]}});
    let credentials = AccountCredentials::import_json(&serde_json::to_vec(&raw).unwrap()).unwrap();
    let mut http = ProtocolHttpClient::new(1).unwrap();
    http.test_endpoint = Some(format!("http://{address}"));
    (
        NativeAccountSession::new(credentials, http, NativeSigner::new(1).unwrap()),
        probes,
        server,
    )
}
#[tokio::test]
async fn session_recovery_maps_im_session_rejection() {
    let (session, _, server) = fixture(1, "unexepcted session length", false).await;
    let result = session.inbox(123, 1).await;
    server.abort();
    assert!(
        result.is_err(),
        "rejected IM session must not be returned as a successful page"
    );
    assert!(result
        .err()
        .unwrap()
        .to_string()
        .contains("authentication expired"));
}
#[tokio::test]
async fn session_recovery_profile_failure_probes_im_once() {
    let (mut session, probes, server) = fixture(1, "unexepcted session length", false).await;
    for _ in 0..2 {
        let error = session.verify_self().await.err().unwrap();
        assert!(
            error.to_string().contains("authentication expired"),
            "unexpected: {error}"
        );
    }
    assert_eq!(probes.load(Ordering::SeqCst), 1);
    server.abort();
}
#[tokio::test]
async fn session_recovery_valid_im_does_not_bypass_identity() {
    let (mut session, probes, server) = fixture(0, "OK", true).await;
    for _ in 0..2 {
        assert!(matches!(
            session.verify_self().await,
            Err(AccountRequestError::Decode {
                step: "self_bound_profile"
            })
        ));
    }
    assert_eq!(probes.load(Ordering::SeqCst), 1);
    server.abort();
}
#[tokio::test]
async fn session_recovery_generic_business_error_is_not_auth_expiry() {
    let (session, _, server) = fixture(1, "generic busy", false).await;
    let result = session.inbox(123, 1).await;
    server.abort();
    assert!(matches!(
        result,
        Err(AccountRequestError::Business { code: Some(1), .. })
    ));
}
#[tokio::test]
async fn session_recovery_missing_wrapper_is_not_success() {
    let (session, _, server) = fixture(0, "OK", false).await;
    let result = session.inbox(123, 1).await;
    server.abort();
    assert!(matches!(
        result,
        Err(AccountRequestError::Decode { step: "inbox" })
    ));
}

#[tokio::test]
async fn session_recovery_import_requires_working_im_even_with_valid_profile() {
    let (mut session, probes, server) =
        fixture_with_profile(true, 1, "unexepcted session length", false).await;
    assert!(session
        .verify_for_import()
        .await
        .err()
        .unwrap()
        .is_authentication_expired());
    assert_eq!(probes.load(Ordering::SeqCst), 1);
    server.abort();
}
#[tokio::test]
async fn session_recovery_import_accepts_verified_identity_and_im() {
    let (mut session, probes, server) = fixture_with_profile(true, 0, "OK", true).await;
    assert_eq!(
        session.verify_for_import().await.unwrap().sec_uid,
        "synthetic-sec"
    );
    assert_eq!(probes.load(Ordering::SeqCst), 1);
    server.abort();
}
