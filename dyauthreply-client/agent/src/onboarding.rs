//! Native account credential intake and durable registry/config reconciliation.
//! Publishing credential changes requests one supervised service restart; no second sender is started here.
use crate::{
    credential_store::registry::Registry,
    license::NativeLicense,
    protocol::{
        account_session::NativeAccountSession, credentials::AccountCredentials,
        live_http::ProtocolHttpClient,
    },
    runtime::messaging::{ManualService, MessagingSettings},
};
use anyhow::{Context, Result};
use axum::{
    extract::{DefaultBodyLimit, Path as RoutePath, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    sync::Arc,
};
#[derive(Clone)]
struct App {
    service: Arc<ManualService>,
    license: Arc<NativeLicense>,
    lanes: Arc<tokio::sync::Semaphore>,
    writes: Arc<tokio::sync::Mutex<()>>,
}

/// Shared verified credential publication boundary used by manual and CDP intake.
#[derive(Clone)]
pub struct Importer {
    app: App,
}

impl Importer {
    #[must_use]
    pub fn new(service: Arc<ManualService>, license: Arc<NativeLicense>) -> Self {
        Self {
            app: App {
                service,
                license,
                lanes: Arc::new(tokio::sync::Semaphore::new(2)),
                writes: Arc::new(tokio::sync::Mutex::new(())),
            },
        }
    }

    /// Publishes a CDP-produced package through the same identity/capacity boundary as manual import.
    /// # Errors
    /// Rejects incomplete, unlicensed, duplicate or cross-account credentials.
    pub async fn import_bundle(&self, target: Option<String>, bundle: String) -> Result<Value> {
        import(
            self.app.clone(),
            target,
            Input {
                bundle,
                cookie: String::new(),
                web_protect: String::new(),
                keys: String::new(),
                user_agent: String::new(),
                auto_reply_enabled: false,
                daily_reply_quota: None,
                min_interval_seconds: None,
                max_interval_seconds: None,
                silent_start: None,
                silent_end: None,
                remark: None,
            },
        )
        .await
    }

    /// Verifies a transient CDP candidate without publishing a credential generation.
    /// # Errors
    /// Rejects incomplete material, network/identity failures and cross-account refresh attempts.
    pub async fn verify_bundle(
        &self,
        target: Option<&str>,
        bundle: &str,
    ) -> Result<crate::protocol::account_session::VerifiedSelf> {
        anyhow::ensure!(
            self.app.license.status()["can_use_business"] == true,
            "授权未生效，请先完成授权"
        );
        let requested = target.map_or_else(|| uuid::Uuid::new_v4().to_string(), str::to_owned);
        let base = target
            .map(|id| self.app.service.registry().load(id))
            .transpose()?;
        let document =
            AccountCredentials::extension_input_with_base(requested, bundle.trim(), base.as_ref())?;
        let credentials = AccountCredentials::from_state(document)?;
        let http = ProtocolHttpClient::for_user_agent(2, &credentials.user_agent)?;
        let mut session =
            NativeAccountSession::new(credentials, http, self.app.service.import_signer());
        let verified =
            tokio::time::timeout(std::time::Duration::from_secs(30), session.verify_self())
                .await
                .context("账号身份校验超时")?
                .context("账号身份校验失败")?;
        if let Some(base) = base {
            anyhow::ensure!(
                base.expected_sec_uid == verified.sec_uid,
                "登录的不是当前需要维护的账号"
            );
        }
        Ok(verified)
    }

    /// # Errors
    /// Returns the immutable platform scope for an existing local account.
    pub fn expected_scope(&self, account_id: &str) -> Result<String> {
        let record = self.app.service.registry().load(account_id)?;
        anyhow::ensure!(!record.expected_sec_uid.is_empty(), "账号身份尚未验证");
        Ok(record.expected_sec_uid)
    }

    pub(crate) fn notify_quick_auth(&self, session_id: &str) {
        let _ = self.app.service.workbench().changed.send(json!({
            "type":"quick_auth_changed",
            "data":{"session_id":session_id}
        }));
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    #[serde(default)]
    bundle: String,
    #[serde(default)]
    cookie: String,
    #[serde(default)]
    web_protect: String,
    #[serde(default)]
    keys: String,
    #[serde(default)]
    user_agent: String,
    #[serde(default)]
    auto_reply_enabled: bool,
    #[serde(default)]
    daily_reply_quota: Option<u32>,
    #[serde(default)]
    min_interval_seconds: Option<u32>,
    #[serde(default)]
    max_interval_seconds: Option<u32>,
    #[serde(default)]
    silent_start: Option<String>,
    #[serde(default)]
    silent_end: Option<String>,
    #[serde(default)]
    remark: Option<String>,
}
pub fn router(importer: &Arc<Importer>, token: String) -> Router {
    let app = importer.app.clone();
    let routes = Router::new()
        .route(
            "/api/client/v1/douyin/account/quick-create",
            post(create).options(options),
        )
        .route(
            "/api/client/v1/douyin/account/{id}/import-credential",
            post(update).options(options),
        )
        .route(
            "/api/client/v1/douyin/account/{id}",
            axum::routing::delete(delete).options(options),
        )
        .route(
            "/api/client/v1/runtime/status",
            get(runtime_status).options(options),
        )
        .route(
            "/api/client/v1/runtime/reload",
            post(reload).options(options),
        )
        .layer(DefaultBodyLimit::max(2 * 1024 * 1024))
        .with_state(app);
    crate::runtime::messaging::api::secure_router_with_limit(routes, token, 2 * 1024 * 1024)
}
async fn options() -> StatusCode {
    StatusCode::NO_CONTENT
}
fn response(result: Result<Value>) -> Response {
    match result {
        Ok(value) => Json(value).into_response(),
        Err(error) => (
            StatusCode::CONFLICT,
            Json(json!({"detail":error.to_string()})),
        )
            .into_response(),
    }
}
async fn create(State(app): State<App>, Json(input): Json<Input>) -> Response {
    response(import(app, None, input).await)
}
async fn update(
    State(app): State<App>,
    RoutePath(id): RoutePath<String>,
    Json(input): Json<Input>,
) -> Response {
    response(import(app, Some(id), input).await)
}
async fn import(app: App, target: Option<String>, input: Input) -> Result<Value> {
    anyhow::ensure!(
        app.license.status()["can_use_business"] == true,
        "授权未生效，请先完成授权"
    );
    let _permit = app
        .lanes
        .try_acquire()
        .context("正在处理其他账号导入，请稍后重试")?;
    let _write = app.writes.lock().await;
    let registry = app.service.registry();
    let requested = target
        .clone()
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    let base = if target.is_some() {
        Some(registry.load(&requested)?)
    } else {
        None
    };
    let mut document = if input.bundle.trim().is_empty() {
        AccountCredentials::input_from_fields(
            requested.clone(),
            &json!({"cookie":input.cookie,"web_protect":input.web_protect,"keys":input.keys,"user_agent":input.user_agent}),
            base.as_ref(),
        )?
    } else {
        AccountCredentials::extension_input_with_base(
            requested.clone(),
            input.bundle.trim(),
            base.as_ref(),
        )?
    };
    anyhow::ensure!(
        !document.user_agent.is_empty(),
        "请同时导入浏览器 User-Agent，或使用扩展一键导入串"
    );
    let credentials = AccountCredentials::from_state(document.clone())?;
    let http = ProtocolHttpClient::for_user_agent(2, &credentials.user_agent)?;
    let mut session = NativeAccountSession::new(credentials, http, app.service.import_signer());
    let verified = tokio::time::timeout(std::time::Duration::from_secs(30), session.verify_self())
        .await
        .context("账号身份校验超时，未保存凭证")?
        .context("账号身份校验失败，未保存凭证")?;
    if let Some(base) = &base {
        anyhow::ensure!(
            base.expected_sec_uid == verified.sec_uid,
            "新的登录凭证不属于当前账号"
        );
    }
    document.expected_sec_uid.clone_from(&verified.sec_uid);
    if let Some(existing) = registry.find_scope(&verified.sec_uid)? {
        anyhow::ensure!(
            target.as_ref().is_none_or(|id| id == &existing.id),
            "该登录账号已经存在，请更新对应账号"
        );
        if target.is_none() && !existing.deleted {
            let previous = registry.load(&existing.id)?;
            document = if input.bundle.trim().is_empty() {
                AccountCredentials::input_from_fields(
                    existing.id.clone(),
                    &json!({"cookie":input.cookie,"web_protect":input.web_protect,"keys":input.keys,"user_agent":input.user_agent}),
                    Some(&previous),
                )?
            } else {
                AccountCredentials::extension_input_with_base(
                    existing.id.clone(),
                    input.bundle.trim(),
                    Some(&previous),
                )?
            };
            document.expected_sec_uid.clone_from(&verified.sec_uid);
        }
        document.account_id = existing.id;
    }
    let active = registry.list()?.into_iter().filter(|r| !r.deleted).count();
    let existing = registry.find_scope(&verified.sec_uid)?;
    let capacity = app.service.capacity();
    let limit =
        tokio::task::spawn_blocking(move || capacity.admission_limit(u32::try_from(active)?))
            .await??;
    anyhow::ensure!(
        existing.as_ref().is_some_and(|r| !r.deleted) || active < usize::try_from(limit)?,
        "本机托管账号已达当前承载上限，请查看设置中的承载评估"
    );
    let policy = account_policy(&input, document.account_id.clone())?;
    let (record, changed) = registry.put(
        &document,
        &verified.nickname,
        crate::workbench::now_ms(),
        if existing.as_ref().is_none_or(|r| r.deleted) {
            Some(&policy)
        } else {
            None
        },
    )?;
    app.service.store_imported_profile(&record, &policy)?;
    Ok(
        json!({"success":true,"id":record.id,"nickname":record.nickname,"sec_uid":record.sec_uid,"status":0,"credential_state":"unknown","generation":record.generation,"_runtime_reload":changed||app.service.loaded_generation(&record.id)!=Some(record.generation),"message":"账号身份已验证，发送能力以实际回执为准"}),
    )
}
fn account_policy(
    input: &Input,
    id: String,
) -> Result<crate::runtime::messaging::AutomationPolicy> {
    let policy = crate::runtime::messaging::AutomationPolicy {
        account_id: id,
        enabled: input.auto_reply_enabled,
        enabled_since_us: if input.auto_reply_enabled {
            u64::try_from(crate::workbench::now_ms())? * 1000
        } else {
            0
        },
        daily_quota: input.daily_reply_quota.unwrap_or(100),
        min_interval_seconds: input.min_interval_seconds.unwrap_or(1),
        max_interval_seconds: input.max_interval_seconds.unwrap_or(3),
        silent_start: input.silent_start.clone(),
        silent_end: input.silent_end.clone(),
        daily_peer_limit: false,
        blocked_peers: vec![],
        blocked_content_keywords: vec![],
        blocked_nickname_keywords: vec![],
    };
    anyhow::ensure!(
        input.remark.as_ref().is_none_or(|v| v.len() <= 1024),
        "账号备注过长"
    );
    policy.validate()?;
    Ok(policy)
}
async fn delete(State(app): State<App>, RoutePath(id): RoutePath<String>) -> Response {
    let result = async {
        let _write = app.writes.lock().await;
        let snapshot = app.service.business_snapshot()?;
        anyhow::ensure!(
            !snapshot.policies.get(&id).is_some_and(|p| p.enabled),
            "请先关闭自动回复再移除账号"
        );
        app.service.retire_account(&id).await?;
        Ok(json!({"success":true,"id":id,"_runtime_reload":true}))
    }
    .await;
    response(result)
}
async fn reload(State(app): State<App>) -> Response {
    if std::env::var_os("DY_AGENT_PARENT_STDIN").as_deref() != Some(std::ffi::OsStr::new("1")) {
        return response(Err(anyhow::anyhow!("配置已保存，请重启当前原生服务后生效")));
    }
    app.service.request_reload();
    Json(json!({"queued":true,"instance":app.service.runtime_marker()})).into_response()
}
/// # Errors
/// Uses the existing encrypted snapshot once, then derives runtime account scopes from the native registry.
pub fn bootstrap(
    root: &Path,
    license: Option<&Arc<NativeLicense>>,
    legacy_hosted: Option<crate::runtime::hosted::HostedSettings>,
    settings: Option<MessagingSettings>,
) -> Result<(
    Option<crate::runtime::hosted::HostedSettings>,
    Option<MessagingSettings>,
)> {
    if license.is_none() && settings.is_none() {
        return Ok((None, None));
    }
    let registry = Registry::open(root)?;
    let automatic_snapshot = legacy_credential_snapshot(root, &registry, settings.as_ref())?;
    registry.seed(
        settings
            .as_ref()
            .and_then(|s| s.credential_store.as_deref())
            .or(automatic_snapshot.as_deref()),
        settings
            .as_ref()
            .map_or(&[], |s| s.credential_files.as_slice()),
    )?;
    let accounts = registry
        .list()?
        .into_iter()
        .filter(|r| !r.deleted)
        .collect::<Vec<_>>();
    if !accounts.is_empty() {
        import_legacy_projections(root)?;
    }
    let hosted = if let Some(license) = license {
        license.hosted_settings(&accounts)?
    } else {
        legacy_hosted
    };
    let token = license
        .map(|l| l.token())
        .or_else(|| settings.as_ref().map(|s| s.api_token.clone()))
        .context("本地连接密钥缺失")?;
    let settings = MessagingSettings {
        api_token: token,
        credential_files: vec![],
        credential_store: None,
        registry_root: Some(root.into()),
        rule_config_file: settings.as_ref().and_then(|s| s.rule_config_file.clone()),
        automation: settings.map_or_else(Vec::new, |s| s.automation),
    };
    Ok((hosted, Some(settings)))
}

fn legacy_client_root(root: &Path) -> Result<&Path> {
    anyhow::ensure!(
        root.file_name() == Some(std::ffi::OsStr::new("agent-v2")),
        "native data root must end with agent-v2"
    );
    root.parent()
        .context("native data root requires client parent")
}

fn legacy_credential_snapshot(
    root: &Path,
    registry: &Registry,
    settings: Option<&MessagingSettings>,
) -> Result<Option<PathBuf>> {
    if settings.is_some_and(|settings| {
        settings.credential_store.is_some() || !settings.credential_files.is_empty()
    }) {
        return Ok(None);
    }
    let snapshot = root.join("credentials-v1");
    if snapshot.join("manifest.json").is_file() {
        return Ok(Some(snapshot));
    }
    if !registry.list()?.iter().any(|record| !record.deleted) {
        let client = legacy_client_root(root)?;
        if client.join("db.sqlite3").is_file() && client.join(".env").is_file() {
            crate::credential_store::import_legacy(client, &snapshot)?;
            return Ok(Some(snapshot));
        }
    }
    Ok(None)
}

fn import_legacy_projections(root: &Path) -> Result<()> {
    let source = legacy_client_root(root)?.join("db.sqlite3");
    if !source.is_file() {
        return Ok(());
    }
    if !root.join("business.sqlite3").is_file() {
        crate::business::import_legacy(&source, root, "")?;
    }
    crate::workbench::Workbench::open(root)?.import_legacy(&source)?;
    crate::audit::AuditStore::open(root)?.import_legacy(&source)?;
    Ok(())
}

async fn runtime_status(State(app): State<App>) -> Json<Value> {
    Json(json!({"instance":app.service.runtime_marker()}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn explicit_policy_preserves_zero_and_defaults_to_paused() {
        let input:Input=serde_json::from_value(json!({"bundle":"synthetic","daily_reply_quota":0,"min_interval_seconds":0,"max_interval_seconds":0})).unwrap();
        let policy = account_policy(&input, "a".into()).unwrap();
        assert!(!policy.enabled);
        assert_eq!(policy.daily_quota, 0);
        assert_eq!(policy.min_interval_seconds, 0);
        assert!(serde_json::from_value::<Input>(json!({"bundle":"x","unexpected":"y"})).is_err());
    }
    #[test]
    fn existing_native_snapshot_is_reused_but_explicit_sources_win() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("agent-v2");
        std::fs::create_dir_all(root.join("credentials-v1")).unwrap();
        std::fs::write(root.join("credentials-v1/manifest.json"), b"fixture").unwrap();
        let registry = Registry::open(&root).unwrap();
        assert_eq!(
            legacy_credential_snapshot(&root, &registry, None)
                .unwrap()
                .unwrap(),
            root.join("credentials-v1")
        );
        let explicit = MessagingSettings {
            api_token: "a".repeat(64),
            credential_files: vec![root.join("explicit.json")],
            credential_store: None,
            registry_root: None,
            rule_config_file: None,
            automation: vec![],
        };
        assert!(
            legacy_credential_snapshot(&root, &registry, Some(&explicit))
                .unwrap()
                .is_none()
        );
        assert!(legacy_client_root(dir.path()).is_err());
    }
}
