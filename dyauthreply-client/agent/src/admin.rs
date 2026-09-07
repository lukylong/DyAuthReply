//! Device-local administrator session, diagnostics and fail-closed emergency stop.
use crate::{health::HealthHandle, runtime::messaging::ManualService};
use anyhow::{Context, Result};
use axum::{
    extract::{Query, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use rand::{rngs::OsRng, RngCore};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::File,
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use subtle::ConstantTimeEq;
use tokio::sync::Mutex;

const SESSION_SECONDS: i64 = 30 * 60;
const MAX_SESSIONS: usize = 16;
const MAX_LOG_FILE_BYTES: u64 = 1024 * 1024;
const MAX_LOG_OUTPUT_BYTES: usize = 512 * 1024;
const LOG_NAMES: [&str; 5] = [
    "native-agent.log.4",
    "native-agent.log.3",
    "native-agent.log.2",
    "native-agent.log.1",
    "native-agent.log",
];

#[derive(Clone)]
struct Admin {
    service: Arc<ManualService>,
    health: HealthHandle,
    log_dir: PathBuf,
    sessions: Arc<SessionRegistry>,
}

#[derive(Default)]
struct SessionRegistry {
    tokens: Mutex<BTreeMap<String, i64>>,
}

pub fn router(
    service: Arc<ManualService>,
    health: HealthHandle,
    token: String,
    log_dir: PathBuf,
) -> Router {
    let state = Admin {
        service,
        health,
        log_dir,
        sessions: Arc::new(SessionRegistry::default()),
    };
    let routes = Router::new()
        .route("/api/client/v1/admin/login", post(login).options(options))
        .route(
            "/api/client/v1/admin/local-session",
            post(local_session).options(options),
        )
        .route("/api/client/v1/admin/logout", post(logout).options(options))
        .route(
            "/api/client/v1/admin/dashboard",
            get(dashboard).options(options),
        )
        .route(
            "/api/client/v1/admin/emergency-stop",
            post(emergency_stop).options(options),
        )
        .route(
            "/api/client/v1/runtime-logs/files",
            get(log_files).options(options),
        )
        .route(
            "/api/client/v1/runtime-logs/tail",
            get(log_tail).options(options),
        )
        .with_state(state);
    crate::runtime::messaging::api::secure_router(routes, token)
}

async fn options() -> StatusCode {
    StatusCode::NO_CONTENT
}

fn now() -> Result<i64> {
    Ok(i64::try_from(
        SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
    )?)
}

fn token_digest(token: &str) -> String {
    format!("{:x}", Sha256::digest(token.as_bytes()))
}

impl SessionRegistry {
    async fn issue(&self) -> Result<Value> {
        let current = now()?;
        let expires = current
            .checked_add(SESSION_SECONDS)
            .context("管理会话时间溢出")?;
        let mut bytes = [0u8; 32];
        OsRng.fill_bytes(&mut bytes);
        let token = URL_SAFE_NO_PAD.encode(bytes);
        let mut sessions = self.tokens.lock().await;
        sessions.retain(|_, deadline| *deadline > current);
        if sessions.len() >= MAX_SESSIONS {
            let oldest = sessions
                .iter()
                .min_by_key(|(_, deadline)| **deadline)
                .map(|(key, _)| key.clone())
                .context("管理会话容量异常")?;
            sessions.remove(&oldest);
        }
        sessions.insert(token_digest(&token), expires);
        Ok(json!({"token":token,"expires_in":SESSION_SECONDS,"expires_at":expires}))
    }

    async fn require(&self, headers: &HeaderMap) -> bool {
        let Some(token) = headers
            .get("x-admin-token")
            .and_then(|value| value.to_str().ok())
            .filter(|value| value.len() <= 128)
        else {
            return false;
        };
        let Ok(current) = now() else {
            return false;
        };
        let mut sessions = self.tokens.lock().await;
        sessions.retain(|_, deadline| *deadline > current);
        sessions
            .get(&token_digest(token))
            .is_some_and(|deadline| *deadline > current)
    }

    async fn revoke(&self, headers: &HeaderMap) {
        if let Some(token) = headers
            .get("x-admin-token")
            .and_then(|value| value.to_str().ok())
        {
            self.tokens.lock().await.remove(&token_digest(token));
        }
    }
}

fn error(status: StatusCode, detail: &str) -> Response {
    (status, Json(json!({"detail":detail}))).into_response()
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Login {
    password: String,
}

async fn login(State(admin): State<Admin>, Json(input): Json<Login>) -> Response {
    let configured = std::env::var("CLIENT_ADMIN_PASSWORD").unwrap_or_default();
    if !password_matches(&configured, input.password.trim()) {
        return error(
            StatusCode::UNAUTHORIZED,
            "密码错误或未配置，请使用本机隐藏入口",
        );
    }
    match admin.sessions.issue().await {
        Ok(value) => Json(value).into_response(),
        Err(_) => error(StatusCode::INTERNAL_SERVER_ERROR, "管理会话签发失败"),
    }
}

fn password_matches(configured: &str, supplied: &str) -> bool {
    (8..=256).contains(&configured.len())
        && configured.len() == supplied.len()
        && bool::from(configured.as_bytes().ct_eq(supplied.as_bytes()))
}

async fn local_session(State(admin): State<Admin>) -> Response {
    match admin.sessions.issue().await {
        Ok(value) => Json(value).into_response(),
        Err(_) => error(StatusCode::INTERNAL_SERVER_ERROR, "管理会话签发失败"),
    }
}

async fn logout(State(admin): State<Admin>, headers: HeaderMap) -> Response {
    if !admin.sessions.require(&headers).await {
        return error(StatusCode::UNAUTHORIZED, "管理会话无效或已过期");
    }
    admin.sessions.revoke(&headers).await;
    Json(json!({"ok":true})).into_response()
}

async fn dashboard(State(admin): State<Admin>, headers: HeaderMap) -> Response {
    if !admin.sessions.require(&headers).await {
        return error(StatusCode::UNAUTHORIZED, "管理会话无效或已过期");
    }
    match dashboard_value(&admin).await {
        Ok(value) => Json(value).into_response(),
        Err(_) => error(StatusCode::SERVICE_UNAVAILABLE, "管理状态读取失败"),
    }
}

async fn dashboard_value(admin: &Admin) -> Result<Value> {
    let statuses = admin.service.account_statuses().await;
    let policies = admin.service.business_snapshot()?;
    let names = admin.service.workbench().account_names()?;
    let replies = admin.service.reply_counts_today()?;
    let (pending, inbound) = admin.service.administrative_counts().await?;
    let core = admin.service.audit_core();
    let database = tokio::task::spawn_blocking(move || {
        let integrity = core.database_integrity()?;
        Ok::<_, anyhow::Error>((core.schema_version()?, integrity))
    })
    .await??;
    let accounts = statuses
        .iter()
        .map(|status| {
            json!({
                "id":status.account_id,
                "nickname":names.get(&status.account_id),
                "status":status.label,
                "auto_reply_enabled":policies.policies.get(&status.account_id).is_some_and(|p|p.enabled),
                "credential_state":send_label(status.state.send),
                "reply_today":replies.get(&status.account_id).copied().unwrap_or(0),
                "runtime_state":status.state,
            })
        })
        .collect::<Vec<_>>();
    let sessions = statuses
        .iter()
        .map(|status| json!({"account_id":status.account_id,"frontier":status.frontier}))
        .collect::<Vec<_>>();
    let checked = chrono::DateTime::from_timestamp(now()?, 0)
        .map(|date| date.to_rfc3339())
        .unwrap_or_default();
    Ok(json!({
        "service":{
            "env":"native-rust",
            "data_dir":admin.log_dir.parent(),
            "http_port":18765,
            "accounts_total":statuses.len(),
            "accounts_auto_reply_on":policies.policies.values().filter(|p|p.enabled).count(),
            "accounts_online":statuses.iter().filter(|s|s.state.can_receive()).count(),
            "accounts":accounts,
            "sessions":sessions,
            "checked_at":checked,
        },
        "processes":{
            "api":{"pid":std::process::id(),"name":"dy-agent","runtime":"rust","status":"running"},
            "related_processes":[],
            "system":{"os":std::env::consts::OS,"arch":std::env::consts::ARCH},
        },
        "database":{
            "schema_version":database.0,
            "quick_check":database.1.quick_check,
            "foreign_key_violations":database.1.foreign_key_violations,
            "business_revision":policies.document.revision,
            "pending_worker_commands":pending,
            "unprocessed_inbound_messages":inbound,
        },
        "runtime_health":admin.health.snapshot(),
    }))
}

const fn send_label(value: crate::state::SendCapability) -> &'static str {
    match value {
        crate::state::SendCapability::Unknown => "unknown",
        crate::state::SendCapability::Sendable => "sendable",
        crate::state::SendCapability::ReceiveOnly => "receive_only",
        crate::state::SendCapability::RiskControlled => "risk_controlled",
        crate::state::SendCapability::AuthExpired => "invalid",
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Stop {
    #[serde(default = "default_reason")]
    reason: String,
}

fn default_reason() -> String {
    "管理员急停".into()
}

async fn emergency_stop(
    State(admin): State<Admin>,
    headers: HeaderMap,
    Json(input): Json<Stop>,
) -> Response {
    if !admin.sessions.require(&headers).await {
        return error(StatusCode::UNAUTHORIZED, "管理会话无效或已过期");
    }
    match admin.service.emergency_stop(input.reason.trim()).await {
        Ok(value) => Json(value).into_response(),
        Err(_) => error(StatusCode::CONFLICT, "急停未完成，请保持服务运行并重试"),
    }
}

async fn log_files(State(admin): State<Admin>, headers: HeaderMap) -> Response {
    if !admin.sessions.require(&headers).await {
        return error(StatusCode::UNAUTHORIZED, "管理会话无效或已过期");
    }
    let directory = admin.log_dir.clone();
    match tokio::task::spawn_blocking(move || list_logs(&directory)).await {
        Ok(Ok(items)) => Json(json!({"items":items,"log_dir":admin.log_dir})).into_response(),
        _ => error(StatusCode::SERVICE_UNAVAILABLE, "运行日志读取失败"),
    }
}

#[derive(Default, Deserialize)]
struct LogQuery {
    #[serde(default)]
    file: String,
    #[serde(default = "default_lines")]
    lines: u16,
}

const fn default_lines() -> u16 {
    400
}

async fn log_tail(
    State(admin): State<Admin>,
    headers: HeaderMap,
    Query(query): Query<LogQuery>,
) -> Response {
    if !admin.sessions.require(&headers).await {
        return error(StatusCode::UNAUTHORIZED, "管理会话无效或已过期");
    }
    if !(50..=2000).contains(&query.lines) || query.file.len() > 64 {
        return error(StatusCode::BAD_REQUEST, "日志查询范围无效");
    }
    let directory = admin.log_dir.clone();
    match tokio::task::spawn_blocking(move || tail_logs(&directory, &query.file, query.lines)).await
    {
        Ok(Ok(value)) => Json(value).into_response(),
        Ok(Err(_)) => error(StatusCode::BAD_REQUEST, "日志文件无效或不可读"),
        Err(_) => error(StatusCode::SERVICE_UNAVAILABLE, "运行日志读取失败"),
    }
}

fn list_logs(directory: &Path) -> Result<Vec<Value>> {
    if !directory.exists() {
        return Ok(Vec::new());
    }
    let root = std::fs::symlink_metadata(directory)?;
    anyhow::ensure!(
        root.is_dir() && !root.file_type().is_symlink(),
        "日志目录无效"
    );
    let mut result = Vec::new();
    for name in LOG_NAMES.iter().rev() {
        let path = directory.join(name);
        let Ok(metadata) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        anyhow::ensure!(
            metadata.is_file() && !metadata.file_type().is_symlink(),
            "日志文件无效"
        );
        let modified = metadata.modified()?.duration_since(UNIX_EPOCH)?.as_secs();
        result.push(json!({"name":name,"path":path,"size":metadata.len(),"modified_at":modified}));
    }
    Ok(result)
}

fn tail_logs(directory: &Path, selected: &str, lines: u16) -> Result<Value> {
    let names = if selected.is_empty() {
        LOG_NAMES.to_vec()
    } else {
        anyhow::ensure!(LOG_NAMES.contains(&selected), "日志文件名无效");
        vec![selected]
    };
    let mut body = String::new();
    let mut files = Vec::new();
    for name in names {
        let path = directory.join(name);
        if !path.exists() {
            continue;
        }
        let bytes = read_tail(&path)?;
        files.push(name.to_string());
        if !body.is_empty() {
            body.push('\n');
        }
        body.push_str(&String::from_utf8_lossy(&bytes));
    }
    let mut selected_lines = body
        .lines()
        .rev()
        .take(usize::from(lines))
        .collect::<Vec<_>>();
    selected_lines.reverse();
    let mut content = selected_lines.join("\n");
    if content.len() > MAX_LOG_OUTPUT_BYTES {
        let mut start = content.len() - MAX_LOG_OUTPUT_BYTES;
        while !content.is_char_boundary(start) {
            start += 1;
        }
        content = content[start..].to_string();
    }
    Ok(json!({
        "files":files,
        "content":content,
        "message":if files.is_empty(){"暂无运行日志"}else{"仅显示日志尾部"},
        "log_dir":directory,
    }))
}

fn read_tail(path: &Path) -> Result<Vec<u8>> {
    let metadata = std::fs::symlink_metadata(path)?;
    anyhow::ensure!(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        "日志文件无效"
    );
    let length = metadata.len();
    let start = length.saturating_sub(MAX_LOG_FILE_BYTES);
    let mut file = File::open(path)?;
    file.seek(SeekFrom::Start(start))?;
    let mut bytes = Vec::with_capacity(usize::try_from(length - start)?);
    file.take(MAX_LOG_FILE_BYTES).read_to_end(&mut bytes)?;
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn passwords_require_an_explicit_bounded_configuration() {
        assert!(!password_matches("", ""));
        assert!(!password_matches("12345678", "12345679"));
        assert!(password_matches("12345678", "12345678"));
    }

    #[tokio::test]
    async fn admin_session_is_random_bounded_and_revocable() {
        let sessions = SessionRegistry::default();
        let issued = sessions.issue().await.unwrap();
        let token = issued["token"].as_str().unwrap();
        assert!((40..=64).contains(&token.len()));
        assert_eq!(issued["expires_in"], SESSION_SECONDS);
        let mut headers = HeaderMap::new();
        headers.insert("x-admin-token", token.parse().unwrap());
        assert!(sessions.require(&headers).await);
        sessions.revoke(&headers).await;
        assert!(!sessions.require(&headers).await);
    }

    #[test]
    fn logs_are_fixed_name_bounded_and_tailed() {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("native-agent.log"), "one\ntwo\nthree\n").unwrap();
        std::fs::write(root.path().join("ignored.log"), "secret").unwrap();
        let listed = list_logs(root.path()).unwrap();
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0]["name"], "native-agent.log");
        let tail = tail_logs(root.path(), "native-agent.log", 50).unwrap();
        assert_eq!(tail["content"], "one\ntwo\nthree");
        assert!(tail_logs(root.path(), "../ignored.log", 50).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn symlinked_log_is_rejected() {
        let root = tempfile::tempdir().unwrap();
        std::fs::write(root.path().join("outside"), "data").unwrap();
        std::os::unix::fs::symlink(
            root.path().join("outside"),
            root.path().join("native-agent.log"),
        )
        .unwrap();
        assert!(list_logs(root.path()).is_err());
        assert!(read_tail(&root.path().join("native-agent.log")).is_err());
    }
}
