use super::{json, Deserialize, Record, Result, Value};
use crate::runtime::messaging::ManualService;
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use std::sync::Arc;
#[derive(Clone)]
struct App {
    service: Arc<ManualService>,
}
pub fn router(service: Arc<ManualService>, token: String) -> Router {
    let app = Router::new()
        .route(
            "/api/client/v1/douyin/reply-log",
            get(list).options(options),
        )
        .route(
            "/api/client/v1/douyin/reply-log/stat/summary",
            get(stats).options(options),
        )
        .route(
            "/api/client/v1/douyin/reply-log/{id}",
            get(detail).options(options),
        )
        .with_state(App { service });
    crate::runtime::messaging::api::secure_router(app, token)
}
async fn options() -> StatusCode {
    StatusCode::NO_CONTENT
}
fn result(r: Result<Value>) -> Response {
    match r {
        Ok(v) => Json(v).into_response(),
        Err(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail":"回复记录同步失败，请稍后重试"})),
        )
            .into_response(),
    }
}
#[derive(Deserialize)]
struct Filter {
    account_id: Option<String>,
    result: Option<String>,
    #[serde(default = "automatic")]
    mode: String,
    #[serde(default = "all")]
    scope: String,
    #[serde(default = "one")]
    page: u32,
    #[serde(rename = "pageSize", alias = "page_size", default = "fifty")]
    size: u32,
}
fn automatic() -> String {
    "automatic".into()
}
fn all() -> String {
    "all".into()
}
fn one() -> u32 {
    1
}
fn fifty() -> u32 {
    50
}
fn present(
    record: &Record,
    names: &std::collections::BTreeMap<String, String>,
    wb: &crate::workbench::Workbench,
) -> Result<Value> {
    let date = chrono::DateTime::from_timestamp_millis(record.created_at_ms).map(|v| {
        v.with_timezone(&chrono_tz::Asia::Shanghai)
            .format("%Y-%m-%d %H:%M:%S")
            .to_string()
    });
    let label = match record.result.as_str() {
        "success" => "发送成功",
        "failed" => "发送失败",
        "partial" => "部分送达",
        "uncertain" => "结果待核验",
        "pending" => "等待处理/发送",
        "cooldown" => "冷却跳过",
        "quota_exceeded" => "额度限制",
        "silent" => "静默等待",
        _ => "已跳过",
    };
    let peer = wb.peer_nickname(&record.account_id, &record.conversation_id)?;
    let mut v = serde_json::to_value(record)?;
    v["account_nickname"] = json!(names.get(&record.account_id));
    v["peer_nickname"] = json!(peer);
    v["matched_rule_id"] = json!(record.rule_id);
    v["sys_create_datetime"] = json!(date);
    v["result_display"] = json!(label);
    v["error_message"] = json!(display_error(&record.error_message));
    v["content_is_excerpt"] = json!(true);
    Ok(v)
}
async fn list(State(app): State<App>, Query(filter): Query<Filter>) -> Response {
    if super::validate(&filter.mode, filter.result.as_deref()).is_err()
        || filter.page == 0
        || filter.page > 10000
        || !(1..=100).contains(&filter.size)
    {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"detail":"记录筛选或分页参数无效"})),
        )
            .into_response();
    }
    let audit = app.service.audit();
    let core = app.service.audit_core();
    let wb = app.service.workbench();
    result(
        tokio::task::spawn_blocking(move || {
            audit.sync(&core)?;
            let mut value = audit.list(
                filter.account_id.as_deref(),
                &filter.mode,
                filter.result.as_deref(),
                filter.page,
                filter.size,
            )?;
            let names = wb.account_names()?;
            let records: Vec<Record> = serde_json::from_value(value["items"].take())?;
            value["items"] = json!(records
                .into_iter()
                .map(|r| present(&r, &names, &wb))
                .collect::<Result<Vec<_>>>()?);
            Ok(value)
        })
        .await
        .unwrap_or_else(|e| Err(e.into())),
    )
}
async fn stats(State(app): State<App>, Query(filter): Query<Filter>) -> Response {
    let audit = app.service.audit();
    let core = app.service.audit_core();
    result(
        tokio::task::spawn_blocking(move || {
            audit.sync(&core)?;
            audit.stats(filter.account_id.as_deref(), &filter.mode, &filter.scope)
        })
        .await
        .unwrap_or_else(|e| Err(e.into())),
    )
}
async fn detail(State(app): State<App>, Path(id): Path<String>) -> Response {
    let audit = app.service.audit();
    let core = app.service.audit_core();
    let wb = app.service.workbench();
    result(
        tokio::task::spawn_blocking(move || {
            audit.sync(&core)?;
            let raw: String =
                audit
                    .lock()?
                    .query_row("SELECT record FROM records WHERE id=?1", [id], |r| r.get(0))?;
            present(&serde_json::from_str(&raw)?, &wb.account_names()?, &wb)
        })
        .await
        .unwrap_or_else(|e| Err(e.into())),
    )
}

fn display_error(error: &str) -> String {
    for (code, text) in [
        ("RiskControlled", "平台返回发送风控，未取得送达回执"),
        ("LoginExpired", "登录凭证失效，消息未送达"),
        ("ProtocolRejected", "平台拒绝当前协议请求，消息未送达"),
        ("BusinessRejected", "发送请求被拒绝，消息未送达"),
    ] {
        if let Some(extra) = error.strip_prefix(code) {
            return format!("{text}{extra}");
        }
    }
    error.into()
}
