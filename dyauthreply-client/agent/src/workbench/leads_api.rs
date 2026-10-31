//! Customer-lead endpoints merged into the existing authenticated native router.
use super::{leads::Filter, Workbench};
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{delete, get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::Value;
use std::sync::Arc;

pub(super) fn router(db: Arc<Workbench>) -> Router {
    Router::new()
        .route("/api/client/v1/douyin/leads", get(list).options(options))
        .route(
            "/api/client/v1/douyin/leads/status",
            get(status).options(options),
        )
        .route(
            "/api/client/v1/douyin/leads/settings",
            post(settings).options(options),
        )
        .route(
            "/api/client/v1/douyin/leads/scan",
            post(scan).options(options),
        )
        .route(
            "/api/client/v1/douyin/leads/export",
            get(export).options(options),
        )
        .route(
            "/api/client/v1/douyin/leads/{id}/sources",
            get(sources).options(options),
        )
        .route(
            "/api/client/v1/douyin/leads/{id}",
            delete(remove).options(options),
        )
        .route(
            "/api/client/v1/douyin/account/{account}/conversation/{conversation}",
            get(conversation).options(options),
        )
        .with_state(db)
}
async fn options() -> StatusCode {
    StatusCode::NO_CONTENT
}
async fn run(f: impl FnOnce() -> anyhow::Result<Value> + Send + 'static) -> Response {
    match tokio::task::spawn_blocking(f).await {
        Ok(Ok(value)) => Json(value).into_response(),
        _ => (StatusCode::BAD_REQUEST,Json(serde_json::json!({"detail":"留资操作未完成，请检查筛选条件后重试；单次导出最多 10000 条"}))).into_response(),
    }
}
async fn list(State(db): State<Arc<Workbench>>, Query(filter): Query<Filter>) -> Response {
    run(move || db.list_leads(&filter, false)).await
}
async fn export(State(db): State<Arc<Workbench>>, Query(filter): Query<Filter>) -> Response {
    run(move || db.list_leads(&filter, true)).await
}
async fn status(State(db): State<Arc<Workbench>>) -> Response {
    run(move || db.lead_status()).await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Enabled {
    enabled: bool,
}
async fn settings(State(db): State<Arc<Workbench>>, Json(input): Json<Enabled>) -> Response {
    run(move || db.set_lead_enabled(input.enabled)).await
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Scan {
    #[serde(default)]
    account: String,
}
async fn scan(State(db): State<Arc<Workbench>>, Json(input): Json<Scan>) -> Response {
    run(move || db.scan_lead_history(&input.account)).await
}
#[derive(Deserialize)]
struct Page {
    #[serde(default)]
    page: u32,
}
async fn sources(
    State(db): State<Arc<Workbench>>,
    Path(id): Path<String>,
    Query(page): Query<Page>,
) -> Response {
    run(move || db.lead_sources(&id, page.page)).await
}
async fn remove(State(db): State<Arc<Workbench>>, Path(id): Path<String>) -> Response {
    run(move || db.delete_lead(&id)).await
}
async fn conversation(
    State(db): State<Arc<Workbench>>,
    Path((account, id)): Path<(String, String)>,
) -> Response {
    run(move || db.lead_conversation(&account, &id)).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, http::Request};
    use tower::ServiceExt;
    #[tokio::test]
    async fn endpoints_require_native_auth_and_validate_input() {
        let root = tempfile::tempdir().unwrap();
        let db = Arc::new(Workbench::open(root.path()).unwrap());
        let token = "a".repeat(48);
        let app = crate::runtime::messaging::api::secure_router(router(db), token.clone());
        for path in [
            "/api/client/v1/douyin/leads",
            "/api/client/v1/douyin/leads/status",
            "/api/client/v1/douyin/leads/export",
        ] {
            let denied = app
                .clone()
                .oneshot(Request::builder().uri(path).body(Body::empty()).unwrap())
                .await
                .unwrap();
            assert_eq!(denied.status(), StatusCode::UNAUTHORIZED);
            let allowed = app
                .clone()
                .oneshot(
                    Request::builder()
                        .uri(path)
                        .header("authorization", format!("Bearer {token}"))
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(allowed.status(), StatusCode::OK);
        }
        let response = app
            .oneshot(
                Request::builder()
                    .uri("/api/client/v1/douyin/leads?from_ms=-1")
                    .header("authorization", format!("Bearer {token}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    }
    fn seed_browser_fixture(db: &Workbench) {
        db.ensure_account("fixture-a", "self-a").unwrap();
        db.ensure_account("fixture-b", "self-b").unwrap();
        db.set_account_profile("fixture-a", "self-a", "演示接待号 A")
            .unwrap();
        db.set_account_profile("fixture-b", "self-b", "演示接待号 B")
            .unwrap();
        let now = super::super::now_ms();
        let inputs = [
            (
                "fixture-a",
                "self-a",
                "peer-a",
                "客户甲",
                "dy_demo_a",
                "13800138000",
                0,
            ),
            (
                "fixture-b",
                "self-b",
                "peer-b",
                "客户乙",
                "dy_demo_b",
                "13900139000",
                0,
            ),
            (
                "fixture-a",
                "self-a",
                "peer-c",
                "历史客户",
                "dy_demo_c",
                "13700137000",
                -86_400_000,
            ),
        ];
        for (i, (account, own, peer, name, handle, phone, offset)) in inputs.iter().enumerate() {
            let event = crate::runtime::inbound::InboundEvent {
                version: 1,
                server_message_id: format!("fixture-{i}"),
                conversation_id: format!("0:1:1:{peer}"),
                conversation_short_id: "12345".into(),
                sender_uid: "42".into(),
                sender_sec_uid: (*peer).into(),
                client_message_id: String::new(),
                message_type: 1,
                create_time_us: u64::try_from(now + offset).unwrap() * 1000,
                content_json: serde_json::json!({"text":format!("测试消息：请联系 {phone}")})
                    .to_string(),
                text: Some(format!("测试消息：请联系 {phone}")),
            };
            db.project(account, own, &[event]).unwrap();
            let conv = db.conversations(account, 1, 50, "").unwrap()["items"]
                .as_array()
                .unwrap()
                .iter()
                .find(|v| v["peer_sec_uid"] == *peer)
                .unwrap()["id"]
                .as_str()
                .unwrap()
                .to_owned();
            db.set_peer_profile(
                account,
                &conv,
                &crate::protocol::VerifiedSelf {
                    user_id: "42".into(),
                    sec_uid: (*peer).into(),
                    nickname: (*name).into(),
                    avatar: String::new(),
                    unique_id: (*handle).into(),
                    follower_count: 0,
                    following_count: 0,
                    aweme_count: 0,
                    total_favorited: 0,
                },
            )
            .unwrap();
        }
        db.process_lead_jobs().unwrap();
    }
    fn seed_non_phone_conversations(db: &Workbench) {
        let now = super::super::now_ms() + 1000;
        for i in 0..55 {
            let event = crate::runtime::inbound::InboundEvent {
                version: 1,
                server_message_id: format!("filler-{i}"),
                conversation_id: format!("0:1:1:filler-{i}"),
                conversation_short_id: "12345".into(),
                sender_uid: "42".into(),
                sender_sec_uid: format!("filler-{i}"),
                client_message_id: String::new(),
                message_type: 1,
                create_time_us: u64::try_from(now).unwrap() * 1000,
                content_json: serde_json::json!({"text":"无手机号的测试消息"}).to_string(),
                text: Some("无手机号的测试消息".into()),
            };
            db.project("fixture-a", "self-a", &[event]).unwrap();
        }
    }
    #[tokio::test]
    #[ignore = "local browser fixture; requires LEAD_FIXTURE_ROOT, never uses real accounts"]
    async fn browser_fixture() {
        let root = std::path::PathBuf::from(
            std::env::var("LEAD_FIXTURE_ROOT").expect("isolated fixture root"),
        );
        assert!(
            !root.join("workbench.sqlite3").exists(),
            "fixture root must be fresh"
        );
        let db = Arc::new(Workbench::open(&root).unwrap());
        seed_browser_fixture(&db);
        seed_non_phone_conversations(&db);
        let worker = db.clone();
        let task = tokio::spawn(async move {
            loop {
                let db = worker.clone();
                tokio::task::spawn_blocking(move || db.process_lead_jobs())
                    .await
                    .unwrap()
                    .unwrap();
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            }
        });
        let app=router(db.clone())
            .route("/api/client/v1/douyin/account/all",get(||async {Json(serde_json::json!([{"id":"fixture-a","nickname":"演示接待号 A"},{"id":"fixture-b","nickname":"演示接待号 B"}]))}))
            .route("/api/client/v1/douyin/account/{account}/conversations",get({let db=db.clone();move |Path(account):Path<String>|{let db=db.clone();async move {Json(db.conversations(&account,1,50,"").unwrap())}}}))
            .route("/api/client/v1/douyin/account/{account}/conversation/{conversation}/messages",get({let db=db.clone();move |Path((account,conv)):Path<(String,String)>|{let db=db.clone();async move {Json(db.messages(&account,&conv).unwrap())}}}));
        let app = crate::runtime::messaging::api::secure_router(
            app,
            "fixture-token-012345678901234567890123456789".into(),
        );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:18985")
            .await
            .unwrap();
        println!("LEAD_FIXTURE_READY http://127.0.0.1:18985");
        axum::serve(listener, app)
            .with_graceful_shutdown(async {
                tokio::signal::ctrl_c().await.unwrap();
            })
            .await
            .unwrap();
        task.abort();
    }
}
