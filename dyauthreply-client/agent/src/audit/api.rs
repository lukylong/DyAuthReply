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
        "skipped" => "已跳过",
        _ => "状态待确认",
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

// Presentation only: retain original diagnostic codes in stored audit records.
fn display_error(error: &str) -> String {
    let error = error.trim();
    if error.is_empty() {
        return String::new();
    }
    let reason = match error {
        "rule_cooldown" => Some("距离上次回复不足规则设置的冷却时间，本次已跳过。冷却结束后可用新消息测试，这条消息不会自动补发。"),
        "account_daily_quota" => Some("该账号当日的自动回复次数已达到上限，本次已跳过。请检查账号的每日回复额度。"),
        "peer_daily_quota" => Some("当天已自动回复过这位联系人，本次按每日一次的设置跳过。"),
        "account_interval" => Some("距离上一条发送的间隔过短，正在等待发送间隔结束。"),
        "pending_reply" => Some("已有回复任务正在处理，正在等待前一批任务完成。"),
        "guard_storage_capacity" => Some("本地回复保护记录已达到容量上限，暂缓处理。请查看运行诊断。"),
        "timezone_change_requires_migration" => Some("设备时区与回复记录不一致，本次暂缓处理，请检查时区设置。"),
        "no match" | "no_match" => Some("没有命中可用的回复规则，本次已跳过。"),
        _ => None,
    };
    if let Some(text) = reason {
        return text.into();
    }
    for (code, text) in [
        (
            "RiskControlled",
            "平台限制了该账号的发送，本条消息未送达，请查看账号详情",
        ),
        (
            "LoginExpired",
            "登录已失效，本条消息未送达，请重新登录该账号",
        ),
        ("ProtocolRejected", "平台拒绝了本次请求，本条消息未送达"),
        (
            "BusinessRejected",
            "本条消息被平台拒绝，未送达；这不代表整个账号持续受限",
        ),
        ("TransportUncertain", "尚未确认消息是否送达，请勿重复发送"),
        ("Uncertain", "尚未确认消息是否送达，请勿重复发送"),
        ("Inconclusive", "本次请求未取得明确结果，请查看运行诊断"),
    ] {
        if let Some(extra) = error
            .strip_prefix(code)
            .filter(|rest| rest.is_empty() || rest.starts_with([' ', '[', ':']))
        {
            return with_platform_code(text, extra);
        }
    }
    let prose = error.split(" [").next().unwrap_or(error);
    if prose
        .chars()
        .any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c))
        && !prose.chars().any(|c| c.is_ascii_alphabetic())
    {
        return with_platform_code(prose, error);
    }
    "暂未识别具体处理原因，请查看运行诊断。".into()
}

fn with_platform_code(text: &str, raw: &str) -> String {
    let code = raw
        .split_once("business=")
        .map(|(_, value)| {
            value
                .trim_start()
                .strip_prefix("Some(")
                .unwrap_or(value.trim_start())
                .chars()
                .take_while(char::is_ascii_digit)
                .take(10)
                .collect::<String>()
        })
        .filter(|value| !value.is_empty() && value != "0");
    code.map_or_else(
        || text.into(),
        |code| format!("{text}（平台状态码：{code}）"),
    )
}

#[cfg(test)]
mod reason_copy_tests {
    use super::*;
    #[test]
    fn reply_reason_codes_are_readable_chinese() {
        for (raw, expected) in [
            ("rule_cooldown", "冷却"),
            ("account_daily_quota", "账号"),
            ("peer_daily_quota", "当天"),
            ("account_interval", "间隔"),
            ("pending_reply", "等待"),
            ("guard_storage_capacity", "容量"),
            ("timezone_change_requires_migration", "时区"),
        ] {
            let shown = display_error(raw);
            assert!(shown.contains(expected), "{raw}: {shown}");
            assert!(!shown.chars().any(|c| c.is_ascii_alphabetic()));
        }
        assert!(display_error("rule_cooldown").contains("不会自动补发"));
        assert!(!display_error("rule_cooldown").contains("5 分钟"));
    }
    #[test]
    fn reply_reason_platform_codes_are_preserved_without_raw_protocol_fields() {
        let shown=display_error("BusinessRejected [http=200,outer=Some(0),business=Some(8101),raw_check=Some(1),msg=OK]");
        assert!(shown.contains("8101") && shown.contains("拒绝"));
        assert!(!shown.chars().any(|c| c.is_ascii_alphabetic()));
        assert!(!shown.contains("风控"));
    }
    #[test]
    fn reply_reason_unknown_english_never_leaks_to_user_hint() {
        let shown = display_error("future_internal_reason token=PRIVATE_TEST_VALUE");
        assert!(!shown.chars().any(|c| c.is_ascii_alphabetic()));
        assert!(!shown.is_empty());
        assert_eq!(display_error("静默时段，暂缓处理"), "静默时段，暂缓处理");
        assert_eq!(display_error(""), "");
    }
}
