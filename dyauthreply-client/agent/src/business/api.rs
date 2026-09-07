//! Native rule/template/card CRUD, account automation controls and dry-run.
use super::{Edit, Snapshot};
use crate::{
    license::NativeLicense,
    runtime::{messaging::ManualService, rules::MatchInput},
};
use anyhow::Context;
use axum::{
    extract::{DefaultBodyLimit, Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, patch, post},
    Json, Router,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;
#[derive(Clone)]
struct App {
    service: Arc<ManualService>,
    matchers: Arc<tokio::sync::Semaphore>,
    license: Option<Arc<NativeLicense>>,
    card_writes: Arc<tokio::sync::Mutex<()>>,
}
pub fn router(
    service: Arc<ManualService>,
    license: Option<Arc<NativeLicense>>,
    token: String,
) -> Router {
    let app = App {
        service,
        matchers: Arc::new(tokio::sync::Semaphore::new(4)),
        license,
        card_writes: Arc::new(tokio::sync::Mutex::new(())),
    };
    let routes = Router::new()
        .route(
            "/api/client/v1/douyin/rule",
            get(list_rules).post(create_rule).options(options),
        )
        .route(
            "/api/client/v1/douyin/rule/dry-run-match",
            post(preview).options(options),
        )
        .route(
            "/api/client/v1/douyin/rule/by/account/{id}",
            get(by_account).options(options),
        )
        .route(
            "/api/client/v1/douyin/rule/{id}",
            get(rule)
                .patch(update_rule)
                .delete(delete_rule)
                .options(options),
        )
        .route(
            "/api/client/v1/douyin/rule/{id}/clone",
            post(clone_rule).options(options),
        )
        .route(
            "/api/client/v1/douyin/template",
            get(list_templates).post(create_template).options(options),
        )
        .route(
            "/api/client/v1/douyin/template/all",
            get(templates_all).options(options),
        )
        .route(
            "/api/client/v1/douyin/template/{id}",
            patch(update_template)
                .delete(delete_template)
                .options(options),
        )
        .route(
            "/api/client/v1/douyin/card/all",
            get(cards_all).options(options),
        )
        .route(
            "/api/client/v1/douyin/card",
            get(cards).post(create_card).options(options),
        )
        .route(
            "/api/client/v1/douyin/card/cover",
            post(upload_cover).options(options),
        )
        .route(
            "/api/client/v1/douyin/card/{id}",
            get(card)
                .put(update_card)
                .patch(update_card)
                .delete(delete_card)
                .options(options),
        )
        .route("/api/client/v1/douyin/account/{id}", patch(account))
        .layer(DefaultBodyLimit::max(3 * 1024 * 1024))
        .with_state(app);
    crate::runtime::messaging::api::secure_router_with_limit(routes, token, 3 * 1024 * 1024)
}
async fn options() -> StatusCode {
    StatusCode::NO_CONTENT
}
fn fail(error: impl std::fmt::Display) -> Response {
    (
        StatusCode::CONFLICT,
        Json(json!({"detail":error.to_string()})),
    )
        .into_response()
}
fn result(value: anyhow::Result<Value>) -> Response {
    match value {
        Ok(v) => Json(v).into_response(),
        Err(e) => fail(e),
    }
}
#[derive(Deserialize)]
struct ListQuery {
    #[serde(default = "one")]
    page: u32,
    #[serde(default = "hundred", rename = "pageSize", alias = "page_size")]
    size: u32,
    #[serde(default)]
    account_id: String,
    #[serde(default)]
    title: String,
}
fn one() -> u32 {
    1
}
fn hundred() -> u32 {
    100
}
fn paginate(items: Vec<Value>, query: &ListQuery) -> anyhow::Result<Value> {
    anyhow::ensure!(
        query.page > 0 && query.page <= 10000 && (1..=512).contains(&query.size),
        "分页参数无效"
    );
    let total = items.len();
    let offset = usize::try_from((query.page - 1) * query.size)?;
    let items: Vec<_> = items
        .into_iter()
        .skip(offset)
        .take(query.size as usize)
        .collect();
    Ok(json!({"items":items,"total":total}))
}
fn rules_for(
    snapshot: &Snapshot,
    account: &str,
    names: &std::collections::BTreeMap<String, String>,
) -> Vec<Value> {
    let mut rows: Vec<_> = snapshot
        .document
        .rules
        .iter()
        .filter(|r| {
            account.is_empty()
                || r["account_ids"]
                    .as_array()
                    .is_none_or(|ids| ids.is_empty() || ids.contains(&json!(account)))
        })
        .map(|rule| {
            let mut row = rule.clone();
            let ids: Vec<String> = serde_json::from_value(
                row.get("account_ids").cloned().unwrap_or_else(|| json!([])),
            )
            .unwrap_or_default();
            row["account_nicknames"] = json!(ids
                .iter()
                .map(|id| names.get(id).unwrap_or(id))
                .collect::<Vec<_>>());
            row["account_id"] = json!(ids.first());
            row
        })
        .collect();
    rows.sort_by_key(|r| {
        (
            std::cmp::Reverse(r["priority"].as_i64().unwrap_or(0)),
            std::cmp::Reverse(r["created_at_ms"].as_i64().unwrap_or(0)),
        )
    });
    rows
}
async fn rule_rows(app: &App, account: String) -> anyhow::Result<Vec<Value>> {
    let snapshot = app.service.business_snapshot()?;
    let db = app.service.workbench();
    tokio::task::spawn_blocking(move || Ok(rules_for(&snapshot, &account, &db.account_names()?)))
        .await?
}
async fn list_rules(State(app): State<App>, Query(q): Query<ListQuery>) -> Response {
    result(
        rule_rows(&app, q.account_id.clone())
            .await
            .and_then(|rows| paginate(rows, &q)),
    )
}
async fn by_account(State(app): State<App>, Path(id): Path<String>) -> Response {
    result(rule_rows(&app, id).await.map(|rows| json!(rows)))
}
async fn rule(State(app): State<App>, Path(id): Path<String>) -> Response {
    result(app.service.business_snapshot().and_then(|s| {
        s.document
            .rules
            .iter()
            .find(|r| r["id"] == id)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("规则不存在"))
    }))
}
async fn create_rule(State(app): State<App>, Json(input): Json<Value>) -> Response {
    result(
        app.service
            .change_business(Edit::Rule { id: None, input })
            .await,
    )
}
async fn update_rule(
    State(app): State<App>,
    Path(id): Path<String>,
    Json(input): Json<Value>,
) -> Response {
    result(
        app.service
            .change_business(Edit::Rule {
                id: Some(id),
                input,
            })
            .await,
    )
}
async fn delete_rule(State(app): State<App>, Path(id): Path<String>) -> Response {
    result(app.service.change_business(Edit::DeleteRule(id)).await)
}
async fn clone_rule(State(app): State<App>, Path(id): Path<String>) -> Response {
    result(app.service.change_business(Edit::CloneRule(id)).await)
}
async fn list_templates(State(app): State<App>, Query(q): Query<ListQuery>) -> Response {
    result(
        app.service
            .business_snapshot()
            .and_then(|s| paginate(s.document.templates.clone(), &q)),
    )
}
async fn templates_all(State(app): State<App>) -> Response {
    result(
        app.service
            .business_snapshot()
            .map(|s| json!(s.document.templates)),
    )
}
async fn create_template(State(app): State<App>, Json(input): Json<Value>) -> Response {
    result(
        app.service
            .change_business(Edit::Template { id: None, input })
            .await,
    )
}
async fn update_template(
    State(app): State<App>,
    Path(id): Path<String>,
    Json(input): Json<Value>,
) -> Response {
    result(
        app.service
            .change_business(Edit::Template {
                id: Some(id),
                input,
            })
            .await,
    )
}
async fn delete_template(State(app): State<App>, Path(id): Path<String>) -> Response {
    result(app.service.change_business(Edit::DeleteTemplate(id)).await)
}
async fn cards_all(State(app): State<App>) -> Response {
    result(app.service.business_snapshot().map(|s| {
        json!(s
            .document
            .cards
            .iter()
            .filter(|card| {
                card["status"] != false
                    && card["sync_state"]
                        .as_str()
                        .is_none_or(|state| state == "synced")
            })
            .collect::<Vec<_>>())
    }))
}
async fn card(State(app): State<App>, Path(id): Path<String>) -> Response {
    result(app.service.business_snapshot().and_then(|snapshot| {
        snapshot
            .document
            .cards
            .iter()
            .find(|card| card["id"] == id)
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("卡片不存在"))
    }))
}
async fn create_card(State(app): State<App>, Json(input): Json<Value>) -> Response {
    result(write_card(&app, None, input).await)
}
async fn update_card(
    State(app): State<App>,
    Path(id): Path<String>,
    Json(input): Json<Value>,
) -> Response {
    result(write_card(&app, Some(id), input).await)
}
async fn write_card(app: &App, id: Option<String>, input: Value) -> anyhow::Result<Value> {
    let _write = app.card_writes.lock().await;
    let row = app
        .service
        .change_business(Edit::Card { id, input })
        .await?;
    let Some(license) = app.license.as_ref() else {
        return mark_card_sync(app, &row, "failed", None).await;
    };
    match license.sync_card(&row).await {
        Ok(remote) => mark_card_sync(app, &row, "synced", Some(remote)).await,
        Err(_) => mark_card_sync(app, &row, "failed", None).await,
    }
}
async fn mark_card_sync(
    app: &App,
    row: &Value,
    state: &str,
    remote: Option<Value>,
) -> anyhow::Result<Value> {
    app.service
        .change_business(Edit::CardSync {
            id: row["id"].as_str().unwrap_or("").into(),
            sync_state: state.into(),
            landing_url: remote
                .as_ref()
                .and_then(|value| value["landing_url"].as_str())
                .map(str::to_owned),
            cover_url: remote
                .as_ref()
                .and_then(|value| value["cover_url"].as_str())
                .map(str::to_owned),
        })
        .await
}
async fn delete_card(State(app): State<App>, Path(id): Path<String>) -> Response {
    result(delete_card_inner(&app, id).await)
}
async fn delete_card_inner(app: &App, id: String) -> anyhow::Result<Value> {
    let _write = app.card_writes.lock().await;
    let row = app
        .service
        .change_business(Edit::BeginDeleteCard(id.clone()))
        .await?;
    let Some(license) = app.license.as_ref() else {
        app.service
            .change_business(Edit::CardSync {
                id,
                sync_state: "delete_failed".into(),
                landing_url: None,
                cover_url: None,
            })
            .await?;
        anyhow::bail!("公网卡片删除尚未同步，请重试")
    };
    if license.delete_card(&id).await.is_err() {
        mark_card_sync(app, &row, "delete_failed", None).await?;
        anyhow::bail!("公网卡片删除尚未同步，请重试")
    }
    app.service
        .change_business(Edit::FinishDeleteCard(id))
        .await
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Cover {
    name: String,
    mime: String,
    data_base64: String,
}

async fn upload_cover(State(app): State<App>, Json(input): Json<Cover>) -> Response {
    let output = async {
        anyhow::ensure!(input.data_base64.len() <= 2_800_000, "封面数据过大");
        let data = STANDARD.decode(input.data_base64)?;
        let license = app.license.as_ref().context("当前授权不可用")?;
        license
            .upload_card_cover(&input.name, &input.mime, data)
            .await
    }
    .await;
    result(output)
}
async fn cards(State(app): State<App>, Query(q): Query<ListQuery>) -> Response {
    result(app.service.business_snapshot().and_then(|s| {
        paginate(
            s.document
                .cards
                .iter()
                .filter(|c| c["title"].as_str().is_some_and(|s| s.contains(&q.title)))
                .cloned()
                .collect(),
            &q,
        )
    }))
}
async fn account(
    State(app): State<App>,
    Path(id): Path<String>,
    Json(input): Json<Value>,
) -> Response {
    result(
        app.service
            .change_business(Edit::Account { id, input })
            .await,
    )
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Preview {
    text: String,
    #[serde(default)]
    account_id: Option<String>,
    #[serde(default = "dm")]
    channel: String,
}
fn dm() -> String {
    "dm".into()
}
async fn preview(State(app): State<App>, Json(input): Json<Preview>) -> Response {
    let permit = match app.matchers.clone().try_acquire_owned() {
        Ok(p) => p,
        Err(e) => return fail(e),
    };
    let snapshot = match app.service.business_snapshot() {
        Ok(s) => s,
        Err(e) => return fail(e),
    };
    if input
        .account_id
        .as_ref()
        .is_some_and(|id| !id.is_empty() && !snapshot.policies.contains_key(id))
    {
        return fail("账号不存在");
    }
    result(tokio::task::spawn_blocking(move ||->anyhow::Result<Value>{
        let _permit=permit;anyhow::ensure!(!input.text.trim().is_empty(),"测试文本不能为空");
        let plan=snapshot.engine.evaluate(&MatchInput{account_id:input.account_id.filter(|s|!s.is_empty()).unwrap_or_else(||"global-preview".into()),text:input.text,channel:input.channel,at_ms:crate::workbench::now_ms(),peer_nickname:String::new()})?;
        if let Some(plan)=plan {let rule=snapshot.document.rules.iter().find(|r|r["id"]==plan.rule_id);Ok(json!({"matched":true,"rule_id":plan.rule_id,"rule_name":rule.map(|r|&r["name"]),"match_type":rule.map(|r|&r["match_type"]),"reply_preview":plan.segments.join("\n"),"miss_reasons":[],"revision":snapshot.document.revision}))}
        else{Ok(json!({"matched":false,"miss_reasons":["没有命中当前账号、时段及关键词条件下的已启用规则"],"revision":snapshot.document.revision}))}
    }).await.unwrap_or_else(|e|Err(e.into())))
}
