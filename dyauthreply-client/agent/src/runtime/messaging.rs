//! Native durable manual-command service. HTTP handlers persist intent, then
//! the central bounded executor performs protocol I/O and records its outcome.
use super::{
    account::AccountControl,
    executor::{ExecutionFuture, ExecutionOutcome, WorkExecutor},
    hosted::{HostedController, HostedSettings, LeaseAuthorization},
    model::{AccountId, AdmissionResult, WorkEnvelope, WorkFence, WorkKind},
    supervisor::RuntimeHandle,
};
use crate::{
    protocol::{
        account_session::{AccountRequestError, NativeAccountSession},
        credentials::AccountCredentials,
        live_http::ProtocolHttpClient,
        live_sender::{LiveSender, SendOperation},
        native_signer::NativeSigner,
        DeliveryClass, SendRequestInput,
    },
    state::{InboundState, SendCapability},
    store::{CoreStore, OutboundBatch, OutboundSegmentDraft, SegmentStatus, SegmentTransition},
};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    sync::{Arc, OnceLock},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::Mutex;
use uuid::Uuid;

pub mod api;
mod automation;
mod frontier;
pub use automation::AutomationPolicy;
pub use frontier::FrontierStatus;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MessagingSettings {
    pub api_token: String,
    #[serde(default)]
    pub credential_files: Vec<PathBuf>,
    #[serde(default)]
    pub credential_store: Option<PathBuf>,
    #[serde(default)]
    pub registry_root: Option<PathBuf>,
    #[serde(default)]
    pub rule_config_file: Option<PathBuf>,
    #[serde(default)]
    pub automation: Vec<AutomationPolicy>,
}
/// # Errors
/// Rejects unreadable, oversized, insecure or malformed input.
pub fn read_private<T: serde::de::DeserializeOwned>(path: &Path) -> Result<T> {
    let file = File::open(path)?;
    let meta = file.metadata()?;
    anyhow::ensure!(
        meta.is_file() && meta.len() <= 1024 * 1024,
        "invalid private input file"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        anyhow::ensure!(
            meta.permissions().mode().trailing_zeros() >= 6,
            "input must be owner-only"
        );
    }
    let mut raw = Vec::new();
    file.take(1024 * 1024 + 1).read_to_end(&mut raw)?;
    anyhow::ensure!(raw.len() <= 1024 * 1024, "input too large");
    serde_json::from_slice(&raw).context("invalid private input schema")
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ManualRequest {
    pub request_id: Uuid,
    pub account_id: String,
    pub conversation_id: String,
    /// String at the JSON boundary: these IDs exceed JavaScript's safe integers.
    pub conversation_short_id: String,
    pub text: String,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Route {
    version: u8,
    conversation_id: String,
    short_id: u64,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    automatic: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    rule_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    rule_revision: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    expires_at_ms: Option<i64>,
}
impl Route {
    fn kind(&self) -> Result<WorkKind> {
        match (self.version, self.automatic) {
            (1, false) => Ok(WorkKind::ManualSend),
            (2, true) if self.rule_id.is_some() && self.expires_at_ms.is_some() => {
                Ok(WorkKind::AutomaticReply)
            }
            _ => anyhow::bail!("unsupported route"),
        }
    }
}
impl ManualRequest {
    fn route(&self) -> Result<Route> {
        anyhow::ensure!(
            !self.request_id.is_nil()
                && !self.text.trim().is_empty()
                && self.text.len() <= 4096
                && !self.conversation_id.is_empty()
                && self.conversation_id.len() <= 256,
            "invalid manual message"
        );
        let short_id = self.conversation_short_id.parse::<u64>()?;
        anyhow::ensure!(
            short_id > 0 && i64::try_from(short_id).is_ok(),
            "invalid conversation short ID"
        );
        Ok(Route {
            version: 1,
            conversation_id: self.conversation_id.clone(),
            short_id,
            automatic: false,
            rule_id: None,
            rule_revision: None,
            expires_at_ms: None,
        })
    }
}
#[derive(Serialize)]
pub struct CommandStatus {
    pub command_id: String,
    pub status: &'static str,
    pub success: bool,
    pub message: String,
    pub client_message_id: String,
    pub platform_message_id: Option<String>,
    pub attempt_count: u32,
}

#[derive(Serialize)]
pub struct AccountStatus {
    pub frontier: FrontierStatus,
    pub account_id: String,
    pub state: crate::state::AccountRuntimeState,
    pub label: &'static str,
    pub inbound_processing: ReceiveProcessing,
    pub last_automatic_command_id: Option<String>,
    pub identity_verified: bool,
    pub identity_error: Option<String>,
}
impl CommandStatus {
    fn from_batch(batch: &OutboundBatch) -> Result<Self> {
        let segment = batch.segments.first().context("empty outbound batch")?;
        let (status, success, message) = match batch.status {
            crate::store::BatchStatus::Confirmed => ("done", true, "发送成功".to_owned()),
            crate::store::BatchStatus::Rejected => (
                "done",
                false,
                batch
                    .segments
                    .iter()
                    .find_map(|s| s.last_error.clone())
                    .unwrap_or_else(|| "发送被拒绝".into()),
            ),
            crate::store::BatchStatus::Partial => {
                ("done", false, "部分消息已发送，其余消息被拒绝".into())
            }
            crate::store::BatchStatus::Sending | crate::store::BatchStatus::Uncertain => {
                ("uncertain", false, "发送结果待核验，请勿重复提交".into())
            }
            crate::store::BatchStatus::Prepared | crate::store::BatchStatus::Retryable => {
                ("pending", false, "排队等待发送".into())
            }
        };
        Ok(Self {
            command_id: batch.id.clone(),
            status,
            success,
            message,
            client_message_id: segment.client_message_id.clone(),
            platform_message_id: segment.platform_message_id.clone(),
            attempt_count: segment.attempt_count,
        })
    }
}
/// Latest bounded receive pass, not lifetime totals. No message bodies or secrets.
#[derive(Default, Clone, Serialize)]
pub struct ReceiveProcessing {
    pub reconciled_sends: usize,
    pub receipt_conflicts: usize,
    pub last_page_baseline: bool,
    pub last_page_inserted: usize,
    pub live_start_us: String,
    pub ignored: usize,
    pub candidates: usize,
    pub deferred: usize,
}
enum SendAdmission {
    Proceed,
    Deferred,
    Cancel,
}
struct SegmentContext<'a> {
    work: &'a WorkEnvelope,
    route: &'a Route,
    batch: &'a OutboundBatch,
    segment: &'a crate::store::OutboundSegment,
    control: tokio::sync::watch::Receiver<AccountControl>,
}
struct AccountSlot {
    credential_generation: u64,
    canonical_sec_uid: String,
    frontier: Mutex<Option<frontier::FrontierOwner>>,
    frontier_status: Mutex<FrontierStatus>,
    session: Mutex<NativeAccountSession>,
    submission: Mutex<()>,
    processing: Mutex<ReceiveProcessing>,
    last_automatic_command: Mutex<Option<String>>,
    restored_binding: Mutex<Option<String>>,
    identity_error: Mutex<Option<String>>,
    profile_read: Mutex<()>,
    works_cache: Mutex<BTreeMap<String, (Instant, crate::protocol::WorksPage)>>,
    http: ProtocolHttpClient,
}

fn retry_transient_works_error(error: &AccountRequestError) -> bool {
    matches!(
        error,
        AccountRequestError::Http {
            step: "self_works",
            status: 403
        }
    )
}

const WORKS_MAX_ATTEMPTS: usize = 3;

struct Inner {
    capacity: Arc<crate::capacity::Capacity>,
    runtime_marker: String,
    registry: Arc<crate::credential_store::registry::Registry>,
    audit: Arc<crate::audit::AuditStore>,
    workbench: Arc<crate::workbench::Workbench>,
    frontier_decode: Arc<tokio::sync::Semaphore>,
    frontier_stopping: std::sync::atomic::AtomicBool,
    accounts: BTreeMap<AccountId, AccountSlot>,
    runtime: RuntimeHandle,
    store: Arc<CoreStore>,
    signer: NativeSigner,
    send_http: ProtocolHttpClient,
    controller: OnceLock<Arc<HostedController>>,
    business: Arc<crate::business::BusinessStore>,
    configuration_gate: tokio::sync::RwLock<()>,
    rule_path: Option<PathBuf>,
    rule_slots: Arc<tokio::sync::Semaphore>,
}
#[derive(Clone)]
pub struct ManualService {
    inner: Arc<Inner>,
}

fn protocol_http_clients() -> Result<(ProtocolHttpClient, ProtocolHttpClient)> {
    // Send headers/A-Bogus use the frozen Windows Chrome identity. Keep TLS
    // emulation on that same profile instead of borrowing the UA of a
    // credential-import browser (for example macOS Chrome 152).
    let budget = ProtocolHttpClient::new(8)?;
    let send = ProtocolHttpClient::for_user_agent(8, crate::protocol::live_sender::REFERENCE_UA)?
        .sharing_budget(&budget);
    Ok((budget, send))
}

fn preparation_failure(error: &AccountRequestError) -> String {
    let reason = match error {
        AccountRequestError::AccountMismatch => "登录账号身份不匹配".to_owned(),
        AccountRequestError::Transport { step } => format!("{step} 网络失败"),
        AccountRequestError::Signing { step } => format!("{step} 签名材料未就绪"),
        AccountRequestError::Http { step, status } => format!("{step} HTTP {status}"),
        AccountRequestError::Business { step, code } => {
            format!("{step} 业务状态 {code:?}")
        }
        AccountRequestError::Decode { step } => format!("{step} 响应结构异常"),
    };
    format!("发送准备失败（{reason}），未发出网络请求")
}

impl ManualService {
    /// Loads bounded, owner-only account material. All TLS profiles share one
    /// HTTP admission budget; all accounts share the same native signer pool.
    /// # Errors
    /// Rejects invalid credentials, duplicate accounts, limits or hosted identity mismatches.
    pub fn load(
        settings: &MessagingSettings,
        hosted: Option<&HostedSettings>,
        runtime: RuntimeHandle,
        store: Arc<CoreStore>,
    ) -> Result<Arc<Self>> {
        anyhow::ensure!(
            (32..=256).contains(&settings.api_token.len())
                && settings.credential_files.len() <= 300
                && (settings.registry_root.is_some()
                    || settings.credential_store.is_some() == settings.credential_files.is_empty()),
            "invalid messaging settings"
        );
        let registry = open_registry(&store, settings)?;
        let loaded = load_configured_credentials(settings, hosted)?;
        let generations = registry
            .list()?
            .into_iter()
            .map(|r| (r.id, r.generation))
            .collect::<BTreeMap<_, _>>();
        anyhow::ensure!(loaded.len() <= 300, "invalid loaded account count");
        let seed_snapshot = if let Some(path) = &settings.rule_config_file {
            Some(read_private::<super::rules::RuleSnapshot>(path)?)
        } else {
            None
        };
        let signer = NativeSigner::new(4)?;
        let (budget, send_http) = protocol_http_clients()?;
        let mut profiles = BTreeMap::new();
        let mut accounts = BTreeMap::new();
        for credentials in loaded {
            let id = credentials.account_id.clone();
            anyhow::ensure!(
                hosted.is_none_or(|hosted| hosted
                    .accounts
                    .iter()
                    .any(|a| a.local_account_id == id.as_str()
                        && a.platform_account_id == credentials.expected_sec_uid))
                    && !credentials.expected_sec_uid.is_empty(),
                "account/hosted scope mismatch"
            );
            let candidate = ProtocolHttpClient::for_user_agent(8, &credentials.user_agent)?
                .sharing_budget(&budget);
            let http = profiles
                .entry(candidate.profile_name().to_owned())
                .or_insert(candidate)
                .clone();
            anyhow::ensure!(
                accounts
                    .insert(
                        id.clone(),
                        AccountSlot {
                            credential_generation: generations
                                .get(id.as_str())
                                .copied()
                                .unwrap_or(1),
                            canonical_sec_uid: credentials.expected_sec_uid.clone(),
                            frontier: Mutex::new(None),
                            frontier_status: Mutex::new(FrontierStatus::default()),
                            session: Mutex::new(NativeAccountSession::new(
                                credentials,
                                http.clone(),
                                signer.clone()
                            )),
                            submission: Mutex::new(()),
                            processing: Mutex::new(ReceiveProcessing::default()),
                            last_automatic_command: Mutex::new(None),
                            restored_binding: Mutex::new(None),
                            identity_error: Mutex::new(None),
                            profile_read: Mutex::new(()),
                            works_cache: Mutex::new(BTreeMap::new()),
                            http
                        }
                    )
                    .is_none(),
                "duplicate messaging account"
            );
        }
        let workbench = open_workbench(&store, &accounts)?;
        for record in registry.list()?.into_iter().filter(|r| !r.deleted) {
            workbench.set_account_profile(&record.id, &record.sec_uid, &record.nickname)?;
        }
        let audit = open_audit(&store)?;
        let business =
            open_registry_business(&store, &accounts, seed_snapshot, settings, &registry)?;
        Ok(Arc::new(Self {
            inner: Arc::new(Inner {
                capacity: open_capacity(&store)?,
                runtime_marker: Uuid::new_v4().to_string(),
                workbench,
                registry,
                audit,
                frontier_decode: Arc::new(tokio::sync::Semaphore::new(4)),
                frontier_stopping: std::sync::atomic::AtomicBool::new(false),
                accounts,
                business,
                configuration_gate: tokio::sync::RwLock::new(()),
                runtime,
                store,
                signer,
                send_http,
                controller: OnceLock::new(),
                rule_path: settings.rule_config_file.clone(),
                rule_slots: Arc::new(tokio::sync::Semaphore::new(4)),
            }),
        }))
    }
    #[must_use]
    pub fn registry(&self) -> Arc<crate::credential_store::registry::Registry> {
        self.inner.registry.clone()
    }
    #[must_use]
    pub fn capacity(&self) -> Arc<crate::capacity::Capacity> {
        self.inner.capacity.clone()
    }
    #[must_use]
    pub fn import_signer(&self) -> NativeSigner {
        self.inner.signer.clone()
    }
    #[must_use]
    pub fn loaded_generation(&self, id: &str) -> Option<u64> {
        self.inner.accounts.get(id).map(|s| s.credential_generation)
    }
    #[must_use]
    pub fn runtime_marker(&self) -> &str {
        &self.inner.runtime_marker
    }
    pub fn request_reload(&self) {
        let _ = self
            .inner
            .workbench
            .changed
            .send(serde_json::json!({"type":"runtime_reload_requested"}));
    }
    /// # Errors
    /// Reconciles public metadata/policy after encrypted persistence; retries remain idempotent.
    pub fn store_imported_profile(
        &self,
        record: &crate::credential_store::registry::AccountRecord,
        policy: &AutomationPolicy,
    ) -> Result<()> {
        self.inner
            .workbench
            .set_account_profile(&record.id, &record.sec_uid, &record.nickname)?;
        let snapshot = self.inner.business.snapshot()?;
        if !snapshot.policies.contains_key(&record.id) {
            self.inner.business.change(|doc| {
                doc.policies.push(policy.clone());
                Ok(serde_json::Value::Null)
            })?;
        }
        Ok(())
    }
    /// # Errors
    /// Quiesces this account's manual admission and refuses removal of unresolved sends.
    pub async fn retire_account(&self, id: &str) -> Result<()> {
        if let Some(slot) = self.inner.accounts.get(id) {
            let _submission = slot.submission.lock().await;
            let _configuration = self.inner.configuration_gate.write().await;
            anyhow::ensure!(
                !self
                    .inner
                    .business
                    .snapshot()?
                    .policies
                    .get(id)
                    .is_some_and(|p| p.enabled),
                "请先关闭自动回复再移除账号"
            );
            anyhow::ensure!(
                !self.inner.store.has_unfinished_account(id)?,
                "账号仍有待发送或待核验任务，请处理后再移除"
            );
            self.inner.registry.delete(id)?;
            let account = AccountId::new(id)?;
            if let Some(control) = self.inner.runtime.account_control(&account).await {
                let mut value = *control.borrow();
                value.state.lifecycle = crate::state::LifecycleState::Draining;
                self.inner
                    .runtime
                    .update_account_control(&account, value)
                    .await?;
            }
        } else {
            self.inner.registry.delete(id)?;
        }
        Ok(())
    }
    /// # Errors
    /// Registers dormant actors using persistent credential generations before ownership can be acquired.
    pub async fn register_accounts(&self) -> Result<()> {
        for (id, slot) in &self.inner.accounts {
            self.inner
                .runtime
                .upsert_account(super::supervisor::AccountSpec {
                    account_id: id.clone(),
                    actor_generation: 1,
                    state: crate::state::AccountRuntimeState {
                        lifecycle: crate::state::LifecycleState::PausedAuto,
                        credential_generation: slot.credential_generation,
                        ..Default::default()
                    },
                })
                .await?;
        }
        Ok(())
    }
    #[must_use]
    pub fn audit(&self) -> Arc<crate::audit::AuditStore> {
        self.inner.audit.clone()
    }
    #[must_use]
    pub fn audit_core(&self) -> Arc<CoreStore> {
        self.inner.store.clone()
    }
    /// # Errors
    /// Uses native durable quota counters plus frozen legacy daily aggregates, not removable chat.
    pub fn reply_counts_today(&self) -> Result<std::collections::BTreeMap<String, u64>> {
        let mut counts = self.inner.audit.legacy_today()?;
        for (id, count) in self.inner.store.native_reply_today()? {
            *counts.entry(id).or_default() += count;
        }
        Ok(counts)
    }
    #[must_use]
    pub fn workbench(&self) -> Arc<crate::workbench::Workbench> {
        self.inner.workbench.clone()
    }
    #[must_use]
    pub fn has_account(&self, id: &str) -> bool {
        self.inner.accounts.contains_key(id)
    }
    fn profile_session(&self, id: &str) -> Result<NativeAccountSession> {
        let slot = self.inner.accounts.get(id).context("account not loaded")?;
        let record = self
            .inner
            .registry
            .list()?
            .into_iter()
            .find(|record| record.id == id && !record.deleted)
            .context("account credentials unavailable")?;
        anyhow::ensure!(
            record.generation == slot.credential_generation
                && record.sec_uid == slot.canonical_sec_uid,
            "account credential reload required"
        );
        let credentials = crate::protocol::credentials::AccountCredentials::from_state(
            self.inner.registry.load(id)?,
        )?;
        anyhow::ensure!(
            credentials.expected_sec_uid == slot.canonical_sec_uid,
            "account scope changed"
        );
        Ok(NativeAccountSession::new(
            credentials,
            slot.http.clone(),
            self.inner.signer.clone(),
        ))
    }
    /// Reads cached profile statistics or refreshes them through one isolated account session.
    /// # Errors
    /// Reports unloaded/stale credentials, platform failures and projection failures.
    pub async fn profile_stats(&self, id: &str, force: bool) -> Result<serde_json::Value> {
        const CACHE_MS: i64 = 10 * 60 * 1000;
        let cached = self.inner.workbench.profile_snapshot(id)?;
        let now = crate::workbench::now_ms();
        if !force {
            if let Some((value, at)) = &cached {
                if now.saturating_sub(*at) < CACHE_MS {
                    return Ok(value.clone());
                }
            }
        }
        let slot = self.inner.accounts.get(id).context("account not loaded")?;
        let _read = slot.profile_read.lock().await;
        if !force {
            if let Some((value, at)) = self.inner.workbench.profile_snapshot(id)? {
                if now.saturating_sub(at) < CACHE_MS {
                    return Ok(value);
                }
            }
        }
        let result = async {
            let mut session = self.profile_session(id)?;
            let profile = session.profile_stats(true).await?;
            self.inner
                .workbench
                .set_account_profile_details(id, &profile, now)?;
            let mut value = serde_json::to_value(profile)?;
            value["ok"] = serde_json::json!(true);
            value["error"] = serde_json::Value::Null;
            value["cached"] = serde_json::json!(false);
            value["last_profile_sync_at"] = serde_json::json!(
                chrono::DateTime::from_timestamp_millis(now).map(|at| at.to_rfc3339())
            );
            Ok::<_, anyhow::Error>(value)
        }
        .await;
        match result {
            Ok(value) => Ok(value),
            Err(error) => match cached {
                Some((value, _)) => {
                    tracing::warn!(account_id = id, %error, "profile refresh failed; serving verified cache");
                    Ok(value)
                }
                None => Err(error),
            },
        }
    }
    /// Reads one bounded live works page without persisting post bodies.
    /// # Errors
    /// Reports unloaded/stale credentials or platform response failures.
    pub async fn account_works(
        &self,
        id: &str,
        cursor: &str,
        count: u8,
    ) -> Result<crate::protocol::WorksPage> {
        const CACHE_TTL: Duration = Duration::from_secs(60);
        const STALE_TTL: Duration = Duration::from_secs(600);
        let slot = self.inner.accounts.get(id).context("account not loaded")?;
        let key = format!("{cursor}:{count}");
        if let Some(page) = {
            let cache = slot.works_cache.lock().await;
            cache
                .get(&key)
                .filter(|(at, _)| at.elapsed() < CACHE_TTL)
                .map(|(_, page)| page.clone())
        } {
            return Ok(page);
        }
        let _read = slot.profile_read.lock().await;
        if let Some(page) = {
            let cache = slot.works_cache.lock().await;
            cache
                .get(&key)
                .filter(|(at, _)| at.elapsed() < CACHE_TTL)
                .map(|(_, page)| page.clone())
        } {
            return Ok(page);
        }
        let mut attempt = 0usize;
        let result = loop {
            attempt += 1;
            let mut session = self.profile_session(id)?;
            let result = session.works(cursor, count).await;
            if result.as_ref().is_err_and(retry_transient_works_error)
                && attempt < WORKS_MAX_ATTEMPTS
            {
                tracing::warn!(
                    account_id = id,
                    retry_attempt = attempt,
                    "transient works request rejected; retrying"
                );
                let delay = if attempt == 1 { 350 } else { 900 };
                tokio::time::sleep(Duration::from_millis(delay)).await;
                continue;
            }
            break result;
        };
        match result {
            Ok(page) => {
                let mut cache = slot.works_cache.lock().await;
                if cache.len() >= 8 && !cache.contains_key(&key) {
                    if let Some(oldest) = cache
                        .iter()
                        .min_by_key(|(_, (at, _))| *at)
                        .map(|(key, _)| key.clone())
                    {
                        cache.remove(&oldest);
                    }
                }
                cache.insert(key, (Instant::now(), page.clone()));
                Ok(page)
            }
            Err(error) => {
                let fallback = {
                    let cache = slot.works_cache.lock().await;
                    cache
                        .get(&key)
                        .filter(|(at, _)| at.elapsed() < STALE_TTL)
                        .map(|(_, page)| page.clone())
                };
                fallback.map_or_else(|| Err(error.into()), Ok)
            }
        }
    }
    /// Refreshes one account-scoped conversation peer through authenticated profile lookup.
    /// # Errors
    /// Reports missing scopes, platform failures and verified response mismatches.
    pub async fn refresh_peer(&self, account: &str, conversation: &str) -> Result<()> {
        let slot = self
            .inner
            .accounts
            .get(account)
            .context("account not loaded")?;
        let _read = slot.profile_read.lock().await;
        let peer = self.inner.workbench.peer_scope(account, conversation)?;
        let session = self.profile_session(account)?;
        let profile = session.user_profile(&peer).await?;
        self.inner
            .workbench
            .set_peer_profile(account, conversation, &profile)
    }
    /// # Errors
    /// Returns bounded unresolved command/receipt counts under current verified leases.
    pub async fn administrative_counts(&self) -> Result<(usize, usize)> {
        let leases = self
            .inner
            .accounts
            .keys()
            .filter_map(|id| {
                self.inner
                    .controller
                    .get()
                    .and_then(|controller| controller.authorization(id.as_str()))
                    .map(|grant| grant.token)
            })
            .collect::<Vec<_>>();
        let store = self.inner.store.clone();
        tokio::task::spawn_blocking(move || {
            let mut batches = 0usize;
            let mut receipts = 0usize;
            for lease in leases {
                batches += store.unfinished_outbound_batches(&lease, 64)?.len();
                receipts += store.pending_inbound_receipts(&lease, 129)?.len();
            }
            Ok((batches, receipts))
        })
        .await?
    }
    /// Disables automatic replies, cancels only never-sent tails, then closes every
    /// account transport. Sending/Uncertain evidence remains durable for reconciliation.
    /// # Errors
    /// Reports invalid input, configuration, fencing, cancellation or socket shutdown failures.
    pub async fn emergency_stop(&self, reason: &str) -> Result<serde_json::Value> {
        anyhow::ensure!(
            !reason.trim().is_empty()
                && reason.len() <= 256
                && !reason.chars().any(char::is_control),
            "急停原因无效"
        );
        let (pending_before, _) = self.administrative_counts().await?;
        self.change_business(crate::business::Edit::EmergencyStop)
            .await?;
        for id in self.inner.accounts.keys() {
            let Some(control) = self.inner.runtime.account_control(id).await else {
                continue;
            };
            let state = *control.borrow();
            if let Some(grant) = self
                .inner
                .controller
                .get()
                .and_then(|controller| controller.authorization(id.as_str()))
            {
                let store = self.inner.store.clone();
                let token = grant.token.clone();
                let batches = tokio::task::spawn_blocking(move || {
                    store.unfinished_outbound_batches(&token, 64)
                })
                .await??;
                for batch in batches {
                    let automatic = serde_json::from_str::<Route>(&batch.response_id)
                        .is_ok_and(|route| route.automatic);
                    let work = WorkEnvelope::new(
                        id.clone(),
                        batch.id,
                        if automatic {
                            WorkKind::AutomaticReply
                        } else {
                            WorkKind::ManualSend
                        },
                        WorkFence {
                            actor_generation: state.actor_generation,
                            credential_generation: state.state.credential_generation,
                            lease_epoch: state.state.lease_epoch,
                        },
                        self.inner.runtime.monotonic_now_ms(),
                    );
                    self.cancel_tail(&work, reason).await?;
                }
            }
            let mut stopped = state;
            stopped.state.lifecycle = crate::state::LifecycleState::Draining;
            self.inner
                .runtime
                .update_account_control(id, stopped)
                .await?;
        }
        self.stop_frontier().await?;
        let (pending_after, _) = self.administrative_counts().await?;
        let cleared = pending_before.saturating_sub(pending_after);
        let stopped_at = chrono::DateTime::from_timestamp_millis(crate::workbench::now_ms())
            .map(|date| date.to_rfc3339())
            .unwrap_or_default();
        Ok(serde_json::json!({
            "ok":true,
            "message":"所有账号协议会话已停止；待核验发送证据已保留",
            "accounts_stopped":self.inner.accounts.len(),
            "commands_cleared":cleared,
            "messages_marked_processed":0,
            "stopped_at":stopped_at,
        }))
    }
    #[must_use]
    pub fn start_ui_updates(self: &Arc<Self>) -> tokio::task::JoinHandle<()> {
        let service = self.clone();
        let mut ticks = self.inner.runtime.subscribe_control_ticks();
        tokio::spawn(async move {
            let mut old = String::new();
            let mut tick = 0u64;
            let mut audit_revision = 0u64;
            while ticks.changed().await.is_ok() {
                tick += 1;
                if !tick.is_multiple_of(4) {
                    continue;
                }
                let states = service.account_statuses().await;
                let visible:Vec<_>=states.iter().map(|s|serde_json::json!({"id":s.account_id,"state":s.state,"identity":s.identity_verified,"error":s.identity_error})).collect();
                let current = serde_json::to_string(&visible).unwrap_or_default();
                if current != old {
                    old = current;
                    let _=service.inner.workbench.changed.send(serde_json::json!({"type":"account_state_changed","data":{"revision":tick.to_string()}}));
                }
                let audit = service.audit();
                let core = service.audit_core();
                match tokio::task::spawn_blocking(move || {
                    let result = audit.sync(&core);
                    (result, audit.revision())
                })
                .await
                {
                    Ok((Ok(_), revision)) if revision != audit_revision => {
                        audit_revision = revision;
                        let _=service.inner.workbench.changed.send(serde_json::json!({"type":"reply_log_changed","data":{"revision":revision.to_string()}}));
                    }
                    Ok((Ok(_), _)) => {}
                    _ => tracing::warn!("native audit synchronization pending"),
                }
                if tick.is_multiple_of(240) {
                    let db = service.workbench();
                    let audit = service.audit();
                    if !matches!(
                        tokio::task::spawn_blocking(move || {
                            db.maintain()?;
                            audit.maintain()
                        })
                        .await,
                        Ok(Ok(()))
                    ) {
                        tracing::warn!("workbench maintenance deferred");
                    }
                }
            }
        })
    }
    async fn project_outbound(&self, batch: &OutboundBatch) -> Result<()> {
        let route: Route = serde_json::from_str(&batch.response_id)?;
        let store = self.inner.store.clone();
        let id = batch.id.clone();
        let times =
            tokio::task::spawn_blocking(move || store.outbound_delivery_times(&id)).await??;
        let own = self.inner.accounts[batch.account_id.as_str()]
            .canonical_sec_uid
            .clone();
        let events: Vec<_> = batch
            .segments
            .iter()
            .filter(|s| s.status == SegmentStatus::Confirmed && s.kind == "text")
            .filter_map(|s| {
                s.platform_message_id
                    .as_ref()
                    .map(|server| super::inbound::InboundEvent {
                        version: 1,
                        server_message_id: server.clone(),
                        conversation_id: route.conversation_id.clone(),
                        conversation_short_id: route.short_id.to_string(),
                        sender_uid: String::new(),
                        sender_sec_uid: own.clone(),
                        client_message_id: s.client_message_id.clone(),
                        message_type: 1,
                        // Durable confirmation time is stable across UI polls and restarts.
                        create_time_us: u64::try_from(*times.get(&s.id).unwrap_or(&0)).unwrap_or(0)
                            * 1000,
                        content_json: serde_json::json!({"text":s.payload}).to_string(),
                        text: Some(s.payload.clone()),
                    })
            })
            .collect();
        let db = self.workbench();
        let account = batch.account_id.clone();
        tokio::task::spawn_blocking(move || db.project(&account, &own, &events)).await?
    }
    /// # Errors
    /// Rejects replacement of an already bound controller.
    pub async fn bind_controller(&self, controller: Arc<HostedController>) -> Result<()> {
        self.inner
            .controller
            .set(controller)
            .map_err(|_| anyhow::anyhow!("controller already bound"))?;
        for id in self.inner.accounts.keys() {
            self.inner.runtime.activate_manual_account(id).await?;
        }
        Ok(())
    }
    fn lease(&self, id: &str) -> Result<LeaseAuthorization> {
        self.authorized(id, WorkKind::ManualSend)
    }
    fn authorized(&self, id: &str, kind: WorkKind) -> Result<LeaseAuthorization> {
        let grant = self
            .inner
            .controller
            .get()
            .and_then(|c| c.authorization(id))
            .context("account ownership unavailable")?;
        anyhow::ensure!(
            if kind == WorkKind::AutomaticReply {
                grant.allow_auto
            } else {
                grant.allow_manual
            },
            "reply entitlement disabled"
        );
        Ok(grant)
    }
    /// # Errors
    /// Returns validation, entitlement, capacity or durable-store errors.
    pub async fn submit(&self, input: ManualRequest) -> Result<CommandStatus> {
        let route = input.route()?;
        let id = AccountId::new(input.account_id.clone())?;
        let slot = self
            .inner
            .accounts
            .get(&id)
            .context("账号未加载到 Rust 服务")?;
        let _serial = slot.submission.lock().await;
        let grant = self.lease(id.as_str())?;
        anyhow::ensure!(
            grant
                .deadline
                .saturating_duration_since(std::time::Instant::now())
                >= Duration::from_secs(20),
            "租约正在续签，请稍后重试同一请求"
        );
        self.inner.runtime.activate_manual_account(&id).await?;
        let control = self
            .inner
            .runtime
            .account_control(&id)
            .await
            .context("账号调度器未启动")?;
        let state = *control.borrow();
        anyhow::ensure!(state.allows(WorkKind::ManualSend), "账号当前状态禁止发送");
        let store = self.inner.store.clone();
        let token = grant.token;
        let response_id = serde_json::to_string(&route)?;
        let batch = tokio::task::spawn_blocking(move || -> Result<OutboundBatch> {
            // Serialized per account; execution only reduces this backlog.
            let trigger = format!("manual:{}", input.request_id);
            if store
                .outbound_batch_for_trigger(&token.account_id, &trigger)?
                .is_none()
            {
                anyhow::ensure!(
                    store.unfinished_outbound_batches(&token, 33)?.len() < 32,
                    "待发送任务已满"
                );
            }
            Ok(store.prepare_outbound_batch(
                &token,
                &trigger,
                &response_id,
                &[OutboundSegmentDraft::text(input.text)],
            )?)
        })
        .await??;
        if matches!(
            batch.segments[0].status,
            SegmentStatus::Prepared | SegmentStatus::Retryable
        ) {
            let work = WorkEnvelope::new(
                id,
                batch.id.clone(),
                WorkKind::ManualSend,
                WorkFence {
                    actor_generation: state.actor_generation,
                    credential_generation: state.state.credential_generation,
                    lease_epoch: state.state.lease_epoch,
                },
                self.inner.runtime.monotonic_now_ms(),
            );
            let admitted = self.inner.runtime.enqueue(work).await;
            anyhow::ensure!(
                matches!(
                    admitted,
                    AdmissionResult::Accepted | AdmissionResult::Duplicate
                ),
                "调度队列繁忙，请重试同一请求"
            );
        }
        CommandStatus::from_batch(&batch)
    }
    /// # Errors
    /// Rejects missing/out-of-scope commands or unavailable durable state.
    pub async fn status(&self, id: String) -> Result<CommandStatus> {
        anyhow::ensure!(Uuid::parse_str(&id).is_ok(), "invalid command ID");
        let store = self.inner.store.clone();
        let batch = tokio::task::spawn_blocking(move || store.outbound_batch(&id)).await??;
        anyhow::ensure!(
            self.inner.accounts.contains_key(batch.account_id.as_str()),
            "command account not loaded"
        );
        self.project_outbound(&batch).await?;
        CommandStatus::from_batch(&batch)
    }

    /// Pure rule preview on a bounded blocking lane. Never consumes an inbound
    /// receipt, reserves a reply guard, or sends a message.
    /// # Errors
    /// Rejects unconfigured/unloaded scope, overload, invalid input or matcher errors.
    pub async fn preview_rule(
        &self,
        input: super::rules::MatchInput,
    ) -> Result<Option<super::rules::ReplyPlan>> {
        anyhow::ensure!(
            self.inner.accounts.contains_key(input.account_id.as_str()),
            "rule preview account not loaded"
        );
        let engine = self.inner.business.snapshot()?.engine.clone();
        let permit = self
            .inner
            .rule_slots
            .clone()
            .try_acquire_owned()
            .context("rule preview busy")?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            engine.evaluate(&input)
        })
        .await?
    }
    /// Recompiles the pinned local snapshot path before atomic revision publish.
    /// # Errors
    /// Keeps the previous snapshot on parse/compile/stale/conflict/overload errors.
    pub async fn reload_rules(&self) -> Result<()> {
        let path = self
            .inner
            .rule_path
            .clone()
            .context("请从规则页面更新配置")?;
        let _configuration = self.inner.configuration_gate.write().await;
        let business = self.inner.business.clone();
        tokio::task::spawn_blocking(move || -> Result<()> {
            let snapshot: super::rules::RuleSnapshot = read_private(&path)?;
            business.change(|doc| {
                doc.rules = snapshot
                    .rules
                    .into_iter()
                    .map(serde_json::to_value)
                    .collect::<std::result::Result<_, _>>()?;
                doc.timezone = snapshot.timezone;
                Ok(serde_json::Value::Null)
            })?;
            Ok(())
        })
        .await?
    }
    #[must_use]
    pub fn rule_status(&self) -> serde_json::Value {
        match self.inner.business.snapshot() {
            Ok(config) => {
                let configured = config.policies.values().any(|p| p.enabled);
                serde_json::json!({"mode":if configured {"native_auto_ws"}else{"preview_only"},"revision":config.engine.revision(),"rule_count":config.engine.rule_count(),"worker_capacity":4,"automatic_enabled":configured,"readiness":"per_account_state"})
            }
            Err(_) => serde_json::json!({"mode":"faulted","automatic_enabled":false}),
        }
    }
    /// # Errors
    /// Reports configuration access failures.
    pub fn business_snapshot(&self) -> Result<Arc<crate::business::Snapshot>> {
        self.inner.business.snapshot()
    }
    /// # Errors
    /// Serializes edits against native planning/sending; publishes only a validated durable generation.
    pub async fn change_business(&self, edit: crate::business::Edit) -> Result<serde_json::Value> {
        if let crate::business::Edit::Account { id, input } = &edit {
            anyhow::ensure!(self.has_account(id), "账号未托管");
            if input["auto_reply_enabled"] == true {
                self.authorized(id, WorkKind::AutomaticReply)?;
            }
        }
        let _configuration = self.inner.configuration_gate.write().await;
        let business = self.inner.business.clone();
        let result =
            tokio::task::spawn_blocking(move || business.change(|doc| edit.apply(doc))).await??;
        let snapshot = self.inner.business.snapshot()?;
        for (id, slot) in &self.inner.accounts {
            let enabled = snapshot
                .policies
                .get(id.as_str())
                .is_some_and(|p| p.enabled);
            if !enabled {
                self.inner.runtime.pause_automatic_account(id).await?;
            } else if slot.restored_binding.lock().await.is_some() {
                self.inner.runtime.activate_automatic_account(id).await?;
            }
        }
        self.retire_old_automatic(snapshot.document.revision)
            .await?;
        let _=self.inner.workbench.changed.send(serde_json::json!({"type":"account_state_changed","data":{"revision":snapshot.document.revision.to_string()}}));
        Ok(result)
    }

    async fn retire_old_automatic(&self, revision: u64) -> Result<()> {
        for id in self.inner.accounts.keys() {
            let Some(controller) = self.inner.controller.get() else {
                continue;
            };
            let Some(grant) = controller.authorization(id.as_str()) else {
                continue;
            };
            let Some(control) = self.inner.runtime.account_control(id).await else {
                continue;
            };
            let state = *control.borrow();
            let store = self.inner.store.clone();
            let batches = tokio::task::spawn_blocking(move || {
                store.unfinished_outbound_batches(&grant.token, 32)
            })
            .await??;
            for batch in batches {
                let Ok(route) = serde_json::from_str::<Route>(&batch.response_id) else {
                    continue;
                };
                if route.automatic && route.rule_revision != Some(revision) {
                    let work = WorkEnvelope::new(
                        id.clone(),
                        batch.id,
                        WorkKind::AutomaticReply,
                        WorkFence {
                            actor_generation: state.actor_generation,
                            credential_generation: state.state.credential_generation,
                            lease_epoch: state.state.lease_epoch,
                        },
                        self.inner.runtime.monotonic_now_ms(),
                    );
                    self.cancel_tail(&work, "配置已更新，停止旧方案未发送的后续消息")
                        .await?;
                }
            }
        }
        Ok(())
    }

    /// Returns one bounded in-memory status snapshot, without platform probes.
    pub async fn account_statuses(&self) -> Vec<AccountStatus> {
        let mut result = Vec::with_capacity(self.inner.accounts.len());
        for id in self.inner.accounts.keys() {
            if let Some(control) = self.inner.runtime.account_control(id).await {
                let state = control.borrow().state;
                result.push(AccountStatus {
                    frontier: self.inner.accounts[id].frontier_status.lock().await.clone(),
                    account_id: id.to_string(),
                    identity_error: self.inner.accounts[id].identity_error.lock().await.clone(),
                    identity_verified: self.inner.accounts[id]
                        .restored_binding
                        .lock()
                        .await
                        .is_some(),
                    last_automatic_command_id: self.inner.accounts[id]
                        .last_automatic_command
                        .lock()
                        .await
                        .clone(),
                    inbound_processing: self.inner.accounts[id].processing.lock().await.clone(),
                    label: state.display_status().label(),
                    state,
                });
            }
        }
        result
    }
    /// Queues one bounded reconciliation through the shared account-fair executor.
    /// # Errors
    /// Rejects unloaded accounts, missing leases, stale state and full queues.
    pub async fn reconcile(&self, account: String) -> Result<()> {
        let id = AccountId::new(account)?;
        anyhow::ensure!(self.inner.accounts.contains_key(&id), "account not loaded");
        let control = self
            .inner
            .runtime
            .account_control(&id)
            .await
            .context("account unavailable")?;
        let state = *control.borrow();
        anyhow::ensure!(
            state.allows(WorkKind::Reconcile),
            "account cannot reconcile"
        );
        let work = WorkEnvelope::new(
            id,
            "http-reconcile",
            WorkKind::Reconcile,
            WorkFence {
                actor_generation: state.actor_generation,
                credential_generation: state.state.credential_generation,
                lease_epoch: state.state.lease_epoch,
            },
            self.inner.runtime.monotonic_now_ms(),
        );
        let result = self.inner.runtime.enqueue(work).await;
        anyhow::ensure!(
            matches!(
                result,
                AdmissionResult::Accepted | AdmissionResult::Duplicate
            ),
            "reconcile queue unavailable"
        );
        Ok(())
    }

    async fn run_reconcile(
        &self,
        work: &WorkEnvelope,
        control: tokio::sync::watch::Receiver<AccountControl>,
    ) -> Result<()> {
        let result = self.fetch_and_commit(work, control).await;
        let healthy = self.inner.accounts[&work.account_id]
            .frontier_status
            .lock()
            .await
            .healthy;
        let inbound = if healthy {
            InboundState::WsHealthy
        } else if result.is_ok() {
            InboundState::HttpDegraded
        } else {
            InboundState::Backoff
        };
        self.inner
            .runtime
            .update_inbound_state(&work.account_id, work.fence(), inbound)
            .await?;
        result?;
        self.activate_configured_auto(&work.account_id).await?;
        self.maintain_frontier(work).await?;
        self.recover_prepared(work).await
    }

    async fn initialize_identity(
        &self,
        work: &WorkEnvelope,
        session: &mut NativeAccountSession,
    ) -> Result<()> {
        let profile = match session.verify_self().await {
            Ok(profile) => profile,
            Err(error) => {
                *self.inner.accounts[&work.account_id]
                    .restored_binding
                    .lock()
                    .await = None;
                *self.inner.accounts[&work.account_id]
                    .identity_error
                    .lock()
                    .await = Some(
                    match &error {
                        AccountRequestError::AccountMismatch => "cookie_account_mismatch",
                        AccountRequestError::Http { status: 401, .. } => "authentication_expired",
                        _ => "identity_verification_failed",
                    }
                    .into(),
                );
                let current = self
                    .inner
                    .runtime
                    .account_control(&work.account_id)
                    .await
                    .context("account missing during identity failure")?;
                let old = current.borrow().state.send;
                let failed = if matches!(error, AccountRequestError::Http { status: 401, .. }) {
                    Some(SendCapability::AuthExpired)
                } else if old == SendCapability::Sendable {
                    Some(SendCapability::Unknown)
                } else {
                    None
                };
                if let Some(capability) = failed {
                    self.inner
                        .runtime
                        .update_send_capability(&work.account_id, work.fence(), capability)
                        .await?;
                }
                return Err(error.into());
            }
        };
        *self.inner.accounts[&work.account_id]
            .identity_error
            .lock()
            .await = None;
        let digest = session.credentials.binding_digest();
        let mut restored = self.inner.accounts[&work.account_id]
            .restored_binding
            .lock()
            .await;
        if restored.as_ref() != Some(&digest) {
            let grant = self
                .inner
                .controller
                .get()
                .and_then(|c| c.authorization(work.account_id.as_str()))
                .context("ownership missing during identity initialization")?;
            anyhow::ensure!(
                u64::try_from(grant.token.fence_epoch)? == work.lease_epoch,
                "stale identity initialization"
            );
            let store = self.inner.store.clone();
            let fingerprint = digest.clone();
            let capability = tokio::task::spawn_blocking(move || {
                store.restore_send_observation(&grant.token, &profile.sec_uid, &fingerprint)
            })
            .await??;
            let capability = if !session.credentials.has_signing_material()
                && matches!(
                    capability,
                    SendCapability::Unknown | SendCapability::Sendable
                ) {
                SendCapability::ReceiveOnly
            } else {
                capability
            };
            let admission = self
                .inner
                .runtime
                .update_send_capability(&work.account_id, work.fence(), capability)
                .await?;
            anyhow::ensure!(
                admission == AdmissionResult::Accepted,
                "identity initialization became stale"
            );
            *restored = Some(digest);
        }
        Ok(())
    }

    async fn fetch_inbox(
        &self,
        work: &WorkEnvelope,
        session: &mut NativeAccountSession,
        cursor: u64,
    ) -> Result<crate::protocol::inbox::InboxPage> {
        match session.inbox(cursor, 50).await {
            Ok(page) => Ok(page),
            Err(error) => {
                if matches!(error, AccountRequestError::Http { status: 401, .. }) {
                    self.inner
                        .runtime
                        .update_send_capability(
                            &work.account_id,
                            work.fence(),
                            SendCapability::AuthExpired,
                        )
                        .await?;
                }
                Err(error.into())
            }
        }
    }
    async fn fetch_and_commit(
        &self,
        work: &WorkEnvelope,
        control: tokio::sync::watch::Receiver<AccountControl>,
    ) -> Result<()> {
        let slot = self
            .inner
            .accounts
            .get(&work.account_id)
            .context("account not loaded")?;
        let generation = i64::try_from(work.credential_generation)?;
        let initial = self
            .inner
            .controller
            .get()
            .and_then(|c| c.authorization(work.account_id.as_str()))
            .context("receive lease unavailable")?;
        anyhow::ensure!(
            u64::try_from(initial.token.fence_epoch)? == work.lease_epoch,
            "stale receive lease"
        );
        // Capture the receipt freshness boundary before any identity/network
        // preparation. Otherwise a slow verification silently ages new messages
        // into the initial history baseline.
        let store = self.inner.store.clone();
        let lease = initial.token.clone();
        let cutoff = tokio::task::spawn_blocking(move || {
            store.ensure_inbound_live_start(&lease, generation)
        })
        .await??;
        let mut session = slot.session.lock().await;
        self.initialize_identity(work, &mut session).await?;
        let own_sec_uid = session.credentials.expected_sec_uid.clone();
        let store = self.inner.store.clone();
        let projection = self.workbench();
        let (previous, live_start, before) = tokio::task::spawn_blocking(move || -> Result<_> {
            let previous =
                store.inbound_checkpoint(&initial.token.account_id, super::inbound::STREAM)?;
            let cutoff = u64::try_from(cutoff)?;
            let before = filter_pending(
                &store,
                &initial.token,
                &own_sec_uid,
                cutoff,
                Some(&projection),
            )?;
            Ok((previous, cutoff, before))
        })
        .await??;
        let cursor = super::inbound::cursor_for(previous.as_ref(), generation)?;
        let page = self.fetch_inbox(work, &mut session, cursor).await?;
        let grant = self
            .inner
            .controller
            .get()
            .and_then(|c| c.authorization(work.account_id.as_str()))
            .context("receive lease unavailable")?;
        anyhow::ensure!(
            u64::try_from(grant.token.fence_epoch)? == work.lease_epoch
                && control.borrow().matches(work)
                && control.borrow().allows(WorkKind::Reconcile),
            "stale receive result"
        );
        let store = self.inner.store.clone();
        let own_sec_uid = session.credentials.expected_sec_uid.clone();
        let projection = self.workbench();
        let summary = tokio::task::spawn_blocking(move || -> Result<ReceiveProcessing> {
            let reconciled =
                super::inbound::reconcile_page_receipts(&store, &grant.token, &own_sec_uid, &page)?;
            let history: Vec<_> = page
                .messages
                .iter()
                .map(super::inbound::InboundEvent::from)
                .collect();
            let committed = super::inbound::commit_page(
                &store,
                &grant.token,
                generation,
                previous.as_ref(),
                page,
            )?;
            // Baseline history is advisory only; live receipts remain durable until projection succeeds.
            projection.project(&grant.token.account_id, &own_sec_uid, &history)?;
            let mut summary = filter_pending(
                &store,
                &grant.token,
                &own_sec_uid,
                live_start,
                Some(&projection),
            )?;
            summary.last_page_baseline = committed.baseline;
            summary.last_page_inserted = committed.inserted;
            summary.ignored += before.ignored;
            summary.reconciled_sends += before.reconciled_sends + reconciled.confirmed;
            summary.receipt_conflicts += before.receipt_conflicts + reconciled.conflicts;
            Ok(summary)
        })
        .await??;
        *slot.processing.lock().await = summary;
        Ok(())
    }

    async fn cancel_tail(&self, work: &WorkEnvelope, reason: &str) -> Result<()> {
        let control = self
            .inner
            .runtime
            .account_control(&work.account_id)
            .await
            .context("account missing")?;
        anyhow::ensure!(control.borrow().matches(work), "stale cancellation");
        let grant = self
            .inner
            .controller
            .get()
            .and_then(|c| c.authorization(work.account_id.as_str()))
            .context("ownership missing")?;
        anyhow::ensure!(
            u64::try_from(grant.token.fence_epoch)? == work.lease_epoch,
            "stale cancellation lease"
        );
        let store = self.inner.store.clone();
        let id = work.durable_id.clone();
        let reason = reason.to_owned();
        tokio::task::spawn_blocking(move || -> Result<()> {
            let batch = store.outbound_batch(&id)?;
            anyhow::ensure!(
                batch.account_id == grant.token.account_id,
                "wrong cancellation account"
            );
            for s in batch.segments {
                if matches!(s.status, SegmentStatus::Prepared | SegmentStatus::Retryable) {
                    store.transition_segment(
                        &grant.token,
                        &s.id,
                        SegmentTransition::CancelPrepared {
                            reason: reason.clone(),
                        },
                    )?;
                }
            }
            Ok(())
        })
        .await?
    }
    async fn prepare_operation(
        &self,
        ctx: SegmentContext<'_>,
        session: &mut NativeAccountSession,
    ) -> Result<Option<SendOperation>> {
        let SegmentContext {
            work,
            route,
            batch,
            segment,
            control,
        } = ctx;
        self.initialize_identity(work, session).await?;
        let preparation = async {
            let identity = session.identity().await?;
            let conversation = session
                .conversation_context(&route.conversation_id, route.short_id)
                .await?;
            let credentials = session.send_credentials(work.credential_generation)?;
            Ok::<_, AccountRequestError>((identity, conversation, credentials))
        }
        .await;
        let (identity, conversation, credentials) = match preparation {
            Ok(v) => v,
            Err(error) => {
                if matches!(error, AccountRequestError::Http { status: 401, .. }) {
                    self.inner
                        .runtime
                        .update_send_capability(
                            &work.account_id,
                            work.fence(),
                            SendCapability::AuthExpired,
                        )
                        .await?;
                }
                let reason = preparation_failure(&error);
                tracing::warn!(account_id=%work.account_id, error=?error, "native send preparation failed before network attempt");
                self.cancel_tail(work, &reason).await?;
                return Ok(None);
            }
        };
        let grant = self.authorized(work.account_id.as_str(), work.kind)?;
        anyhow::ensure!(
            u64::try_from(grant.token.fence_epoch)? == work.lease_epoch,
            "ownership changed before send"
        );
        let operation = SendOperation {
            lease: grant.token,
            lease_deadline: grant.deadline,
            control: control.clone(),
            kind: work.kind,
            batch_id: batch.id.clone(),
            segment_id: segment.id.clone(),
            credentials,
            request: SendRequestInput {
                conversation_id: route.conversation_id.clone(),
                conversation_short_id: route.short_id,
                ticket: conversation.ticket,
                text: String::new(),
                user_agent: session.credentials.user_agent.clone(),
                client_msg_id: String::new(),
                sequence_id: 10001,
                stime: SystemTime::now()
                    .duration_since(UNIX_EPOCH)?
                    .as_millis()
                    .to_string(),
                message_type: 7,
                identity_security_token: identity.token,
                identity_security_device_id: identity.device_id,
                mentioned_users: vec![],
                ext: vec![],
            },
        };
        Ok(Some(operation))
    }

    fn send_admission(&self, work: &WorkEnvelope, route: &Route) -> Result<SendAdmission> {
        if work.kind != WorkKind::AutomaticReply {
            return Ok(SendAdmission::Proceed);
        }
        let config = self.inner.business.snapshot()?;
        let engine = &config.engine;
        let policy = config
            .policies
            .get(work.account_id.as_str())
            .context("automatic policy missing")?;
        let now = i64::try_from(SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis())?;
        if !policy.enabled
            || route
                .rule_revision
                .is_some_and(|revision| revision != engine.revision())
            || !engine.rule_enabled_for(
                work.account_id.as_str(),
                route.rule_id.as_deref().unwrap_or(""),
            )
            || route.expires_at_ms.is_none_or(|deadline| now > deadline)
        {
            return Ok(SendAdmission::Cancel);
        }
        if policy.silent(engine.timezone_name(), now)? {
            return Ok(SendAdmission::Deferred);
        }
        Ok(SendAdmission::Proceed)
    }

    async fn run_send(
        &self,
        work: WorkEnvelope,
        control: tokio::sync::watch::Receiver<AccountControl>,
    ) -> Result<()> {
        let _configuration = self.inner.configuration_gate.read().await;
        let slot = self
            .inner
            .accounts
            .get(&work.account_id)
            .context("account not loaded")?;
        let store = self.inner.store.clone();
        let id = work.durable_id.clone();
        let batch = tokio::task::spawn_blocking(move || store.outbound_batch(&id)).await??;
        anyhow::ensure!(
            batch.account_id == work.account_id.as_str(),
            "outbound account mismatch"
        );
        let route: Route = serde_json::from_str(&batch.response_id)?;
        anyhow::ensure!(route.kind()? == work.kind, "send work/route kind mismatch");
        anyhow::ensure!(
            !batch
                .segments
                .iter()
                .any(|s| matches!(s.status, SegmentStatus::Sending | SegmentStatus::Uncertain)),
            "batch outcome must be reconciled"
        );
        if batch
            .segments
            .iter()
            .any(|s| s.status == SegmentStatus::Rejected)
        {
            return self.cancel_tail(&work, "已拒绝批次停止后续消息").await;
        }
        let mut session = slot.session.lock().await;
        for segment in batch
            .segments
            .iter()
            .filter(|s| s.status != SegmentStatus::Confirmed)
        {
            match self.send_admission(&work, &route)? {
                SendAdmission::Proceed => {}
                SendAdmission::Deferred => return Ok(()),
                SendAdmission::Cancel => {
                    return self
                        .cancel_tail(&work, "自动回复已停用或已过期，未继续发送")
                        .await
                }
            }
            anyhow::ensure!(
                control.borrow().matches(&work) && control.borrow().allows(work.kind),
                "current account blocks send"
            );
            let Some(operation) = self
                .prepare_operation(
                    SegmentContext {
                        work: &work,
                        route: &route,
                        batch: &batch,
                        segment,
                        control: control.clone(),
                    },
                    &mut session,
                )
                .await?
            else {
                return Ok(());
            };
            let outcome = LiveSender::new(
                self.inner.store.clone(),
                self.inner.signer.clone(),
                self.inner.send_http.clone(),
            )
            .send(operation)
            .await?;
            let capability = match outcome.classification {
                DeliveryClass::Delivered | DeliveryClass::DeliveredSoft => SendCapability::Sendable,
                DeliveryClass::RiskControlled => SendCapability::RiskControlled,
                DeliveryClass::LoginExpired => SendCapability::AuthExpired,
                _ => SendCapability::Unknown,
            };
            self.inner
                .runtime
                .update_send_capability(&work.account_id, work.fence(), capability)
                .await?;
            if matches!(
                capability,
                SendCapability::RiskControlled | SendCapability::AuthExpired
            ) {
                return self.cancel_tail(&work, "平台拒绝发送，停止后续消息").await;
            }
            anyhow::ensure!(
                capability == SendCapability::Sendable,
                "send outcome requires reconciliation"
            );
        }
        let store = self.inner.store.clone();
        let id = work.durable_id.clone();
        let latest = tokio::task::spawn_blocking(move || store.outbound_batch(&id)).await??;
        self.project_outbound(&latest).await?;
        Ok(())
    }

    async fn recover_prepared(&self, work: &WorkEnvelope) -> Result<()> {
        let grant = self
            .inner
            .controller
            .get()
            .and_then(|c| c.authorization(work.account_id.as_str()))
            .context("ownership unavailable")?;
        let store = self.inner.store.clone();
        let batches = tokio::task::spawn_blocking(move || {
            store.unfinished_outbound_batches(&grant.token, 32)
        })
        .await??;
        for batch in batches {
            if batch
                .segments
                .iter()
                .any(|s| matches!(s.status, SegmentStatus::Sending | SegmentStatus::Uncertain))
            {
                continue;
            }
            let Ok(route) = serde_json::from_str::<Route>(&batch.response_id) else {
                continue;
            };
            let kind = route.kind()?;
            let control = self
                .inner
                .runtime
                .account_control(&work.account_id)
                .await
                .context("account missing")?;
            if !control.borrow().allows(kind) {
                continue;
            }
            let result = self
                .inner
                .runtime
                .enqueue(WorkEnvelope::new(
                    work.account_id.clone(),
                    batch.id,
                    kind,
                    work.fence(),
                    self.inner.runtime.monotonic_now_ms(),
                ))
                .await;
            anyhow::ensure!(
                matches!(
                    result,
                    AdmissionResult::Accepted | AdmissionResult::Duplicate
                ),
                "recovery queue unavailable"
            );
        }
        self.plan_automatic(work).await
    }

    async fn cancel_prepared_if_current(&self, work: &WorkEnvelope) -> Result<bool> {
        let control = self
            .inner
            .runtime
            .account_control(&work.account_id)
            .await
            .context("account unavailable")?;
        anyhow::ensure!(control.borrow().matches(work), "stale failure observation");
        let grant = self
            .inner
            .controller
            .get()
            .and_then(|c| c.authorization(work.account_id.as_str()))
            .context("lease unavailable")?;
        anyhow::ensure!(
            u64::try_from(grant.token.fence_epoch)? == work.lease_epoch,
            "stale failure fence"
        );
        let store = self.inner.store.clone();
        let id = work.durable_id.clone();
        tokio::task::spawn_blocking(move || -> Result<bool> {
            let batch = store.outbound_batch(&id)?;
            anyhow::ensure!(
                batch.account_id == grant.token.account_id,
                "wrong command account"
            );
            let segment = &batch.segments[0];
            if segment.status == SegmentStatus::Prepared {
                store.transition_segment(
                    &grant.token,
                    &segment.id,
                    SegmentTransition::CancelPrepared {
                        reason: "发送前检查失败，未发起本次消息请求".into(),
                    },
                )?;
                return Ok(true);
            }
            Ok(matches!(
                segment.status,
                SegmentStatus::Confirmed | SegmentStatus::Rejected
            ))
        })
        .await?
    }
}
fn filter_pending(
    store: &CoreStore,
    lease: &crate::store::LeaseToken,
    own_sec_uid: &str,
    live_start: u64,
    projection: Option<&crate::workbench::Workbench>,
) -> Result<ReceiveProcessing> {
    let mut summary = ReceiveProcessing {
        live_start_us: live_start.to_string(),
        ..ReceiveProcessing::default()
    };
    let now_us = u64::try_from(SystemTime::now().duration_since(UNIX_EPOCH)?.as_micros())?;
    let pending = store.pending_inbound_receipts(lease, 128)?;
    if let Some(projection) = projection {
        let events = pending
            .iter()
            .filter_map(|r| r.payload.as_ref())
            .filter_map(|p| serde_json::from_slice::<super::inbound::InboundEvent>(p).ok())
            .collect::<Vec<_>>();
        projection.project(&lease.account_id, own_sec_uid, &events)?;
    }
    // Reconcile before self/history decisions clear durable receipt payloads.
    let evidence: Vec<_> = pending
        .iter()
        .filter_map(|r| r.payload.as_ref())
        .filter_map(|p| serde_json::from_slice::<super::inbound::InboundEvent>(p).ok())
        .filter_map(|e| super::inbound::sent_evidence(&e, own_sec_uid))
        .collect();
    if !evidence.is_empty() {
        let result = store.reconcile_sent_receipts(lease, own_sec_uid, &evidence)?;
        summary.reconciled_sends = result.confirmed;
        summary.receipt_conflicts = result.conflicts;
    }
    for receipt in pending {
        let Some(payload) = receipt.payload.as_ref() else {
            continue;
        };
        let Ok(event) = serde_json::from_slice::<super::inbound::InboundEvent>(payload) else {
            summary.deferred += 1;
            continue;
        };
        let decision = super::inbound_policy::eligibility(&event, own_sec_uid, live_start, now_us);
        if decision.is_terminal_ignore() {
            store.consume_inbound(
                lease,
                crate::store::InboundReceiptKey {
                    stream: &receipt.stream,
                    generation: receipt.stream_generation,
                    event_id: &receipt.event_id,
                    payload_hash: &receipt.payload_hash,
                },
                None,
            )?;
            summary.ignored += 1;
        } else if decision == super::inbound_policy::Eligibility::Candidate {
            summary.candidates += 1;
        } else {
            summary.deferred += 1;
        }
    }
    Ok(summary)
}

fn load_configured_credentials(
    settings: &MessagingSettings,
    hosted: Option<&HostedSettings>,
) -> Result<Vec<AccountCredentials>> {
    if let Some(root) = &settings.registry_root {
        let registry = crate::credential_store::registry::Registry::open(root)?;
        return registry
            .list()?
            .into_iter()
            .filter(|r| {
                !r.deleted
                    && hosted.is_none_or(|h| h.accounts.iter().any(|a| a.local_account_id == r.id))
            })
            .map(|r| AccountCredentials::from_state(registry.load(&r.id)?).map_err(Into::into))
            .collect();
    }
    if let Some(root) = &settings.credential_store {
        let wanted = hosted
            .context("hosted scope missing")?
            .accounts
            .iter()
            .map(|a| a.local_account_id.clone())
            .collect::<Vec<_>>();
        return crate::credential_store::load_accounts(root, &wanted);
    }
    settings.credential_files.iter().map(|path| {
        let input:crate::protocol::credentials::CredentialImport=read_private(path)?;
        let raw=serde_json::json!({"account_id":input.account_id,"user_agent":input.user_agent,"expected_sec_uid":input.expected_sec_uid,"storage_state":input.storage_state});
        Ok(AccountCredentials::import_json(&serde_json::to_vec(&raw)?)?)
    }).collect()
}

impl WorkExecutor for ManualService {
    fn execute(
        &self,
        work: WorkEnvelope,
        control: tokio::sync::watch::Receiver<AccountControl>,
    ) -> ExecutionFuture {
        let service = self.clone();
        Box::pin(async move {
            let result = match work.kind {
                WorkKind::ManualSend | WorkKind::AutomaticReply => {
                    service.run_send(work.clone(), control).await
                }
                WorkKind::PendingRecovery | WorkKind::InboundWakeup => {
                    service.recover_prepared(&work).await
                }
                WorkKind::Reconcile => service.run_reconcile(&work, control).await,
                // Remote renewal remains in the installation-level controller;
                // central KeepaliveLease ticks drive socket ping/reconnect only.
                WorkKind::KeepaliveLease => service.maintain_frontier(&work).await,
                WorkKind::Maintenance => {
                    Err(anyhow::anyhow!("maintenance work is not enabled here"))
                }
            };
            if result.is_ok()
                || (work.kind == WorkKind::ManualSend
                    && service
                        .cancel_prepared_if_current(&work)
                        .await
                        .unwrap_or(false))
            {
                ExecutionOutcome::Finished
            } else {
                ExecutionOutcome::RecoveryNeeded
            }
        })
    }
}

fn open_registry(
    store: &CoreStore,
    settings: &MessagingSettings,
) -> Result<Arc<crate::credential_store::registry::Registry>> {
    let registry = Arc::new(crate::credential_store::registry::Registry::open(
        store
            .database_path()
            .parent()
            .context("missing native root")?,
    )?);
    if settings.registry_root.is_none() {
        registry.seed(
            settings.credential_store.as_deref(),
            &settings.credential_files,
        )?;
    }
    Ok(registry)
}

fn open_capacity(store: &CoreStore) -> Result<Arc<crate::capacity::Capacity>> {
    crate::capacity::Capacity::open(
        store
            .database_path()
            .parent()
            .context("missing data root")?,
    )
}
fn open_registry_business(
    store: &CoreStore,
    accounts: &BTreeMap<AccountId, AccountSlot>,
    snapshot: Option<super::rules::RuleSnapshot>,
    settings: &MessagingSettings,
    registry: &crate::credential_store::registry::Registry,
) -> Result<Arc<crate::business::BusinessStore>> {
    let mut policies = settings.automation.clone();
    for row in registry.list()?.into_iter().filter(|r| !r.deleted) {
        if let Some(policy) = registry.policy(&row.id)? {
            policies.retain(|p| p.account_id != row.id);
            policies.push(policy);
        }
    }
    let business = open_business(store, accounts, snapshot, &policies)?;
    reconcile_registry_policies(&business, registry)?;
    Ok(business)
}

fn reconcile_registry_policies(
    business: &crate::business::BusinessStore,
    registry: &crate::credential_store::registry::Registry,
) -> Result<()> {
    let records = registry
        .list()?
        .into_iter()
        .filter(|r| !r.deleted)
        .collect::<Vec<_>>();
    let active: std::collections::BTreeSet<_> = records.iter().map(|r| r.id.clone()).collect();
    let snapshot = business.snapshot()?;
    let previous: std::collections::BTreeSet<_> = snapshot.policies.keys().cloned().collect();
    if active == previous {
        return Ok(());
    }
    business.change(|doc| {
        doc.policies.retain(|p| active.contains(&p.account_id));
        for record in &records {
            if !doc.policies.iter().any(|p| p.account_id == record.id) {
                let policy = registry.policy(&record.id)?.unwrap_or(AutomationPolicy {
                    account_id: record.id.clone(),
                    enabled: false,
                    enabled_since_us: 0,
                    daily_quota: 100,
                    min_interval_seconds: 1,
                    max_interval_seconds: 3,
                    silent_start: None,
                    silent_end: None,
                    daily_peer_limit: false,
                    blocked_peers: vec![],
                    blocked_content_keywords: vec![],
                    blocked_nickname_keywords: vec![],
                });
                doc.policies.push(policy);
            }
        }
        for rule in &mut doc.rules {
            if let Some(ids) = rule["account_ids"].as_array_mut() {
                let had = !ids.is_empty();
                ids.retain(|id| id.as_str().is_some_and(|id| active.contains(id)));
                if had && ids.is_empty() {
                    rule["status"] = serde_json::json!(false);
                }
            }
        }
        Ok(serde_json::Value::Null)
    })?;
    Ok(())
}

fn open_audit(store: &CoreStore) -> Result<Arc<crate::audit::AuditStore>> {
    store.enable_audit_journal()?;
    let audit = Arc::new(crate::audit::AuditStore::open(
        store
            .database_path()
            .parent()
            .context("missing native root")?,
    )?);
    audit.sync(store)?;
    Ok(audit)
}

fn open_workbench(
    store: &CoreStore,
    accounts: &BTreeMap<AccountId, AccountSlot>,
) -> Result<Arc<crate::workbench::Workbench>> {
    let projection = Arc::new(crate::workbench::Workbench::open(
        store
            .database_path()
            .parent()
            .context("missing native data root")?,
    )?);
    for (id, slot) in accounts {
        projection.ensure_account(id.as_str(), &slot.canonical_sec_uid)?;
    }
    Ok(projection)
}

fn open_business(
    store: &CoreStore,
    accounts: &BTreeMap<AccountId, AccountSlot>,
    rules: Option<super::rules::RuleSnapshot>,
    policies: &[AutomationPolicy],
) -> Result<Arc<crate::business::BusinessStore>> {
    let mut seed = policies.to_vec();
    for id in accounts.keys() {
        if !seed.iter().any(|p| p.account_id == id.as_str()) {
            seed.push(AutomationPolicy {
                account_id: id.to_string(),
                enabled: false,
                enabled_since_us: 0,
                daily_quota: 100,
                min_interval_seconds: 1,
                max_interval_seconds: 3,
                silent_start: None,
                silent_end: None,
                daily_peer_limit: false,
                blocked_peers: vec![],
                blocked_content_keywords: vec![],
                blocked_nickname_keywords: vec![],
            });
        }
    }
    let mut doc = crate::business::Document::seed(Vec::new(), seed);
    if let Some(rules) = rules {
        doc.timezone = rules.timezone;
        doc.revision = rules.revision;
        doc.rules = rules
            .rules
            .into_iter()
            .map(serde_json::to_value)
            .collect::<std::result::Result<_, _>>()?;
    }
    let business = crate::business::BusinessStore::open(
        store
            .database_path()
            .parent()
            .context("missing native root")?,
        doc,
    )?;
    anyhow::ensure!(
        accounts.keys().all(|id| business
            .snapshot()
            .is_ok_and(|s| s.policies.contains_key(id.as_str()))),
        "业务配置缺少托管账号"
    );
    Ok(Arc::new(business))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn works_retry_is_limited_to_transient_platform_rejection() {
        assert_eq!(WORKS_MAX_ATTEMPTS, 3);
        assert!(retry_transient_works_error(&AccountRequestError::Http {
            step: "self_works",
            status: 403,
        }));
        assert!(!retry_transient_works_error(&AccountRequestError::Http {
            step: "self_works",
            status: 401,
        }));
        assert!(!retry_transient_works_error(&AccountRequestError::Http {
            step: "self_query",
            status: 403,
        }));
    }
    #[test]
    fn browser_short_ids_remain_exact_strings_and_payloads_are_bounded() {
        let mut request = ManualRequest {
            request_id: Uuid::new_v4(),
            account_id: "account".into(),
            conversation_id: "conversation".into(),
            conversation_short_id: "7681885293746357819".into(),
            text: "hello".into(),
        };
        assert_eq!(request.route().unwrap().short_id, 7_681_885_293_746_357_819);
        let mut value = serde_json::to_value(&request).unwrap();
        value["conversation_short_id"] = serde_json::json!(123);
        assert!(serde_json::from_value::<ManualRequest>(value).is_err());
        request.text = "x".repeat(4097);
        assert!(request.route().is_err());
        request.text = " ".into();
        assert!(request.route().is_err());
    }
    #[test]
    fn pending_self_receipt_settles_outbox_before_payload_ack() {
        let dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(dir.path()).unwrap();
        let now = i64::try_from(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_millis(),
        )
        .unwrap();
        let lease = store
            .install_verified_account_lease("account", "instance", "boot", 1, now + 60_000)
            .unwrap()
            .token();
        store
            .restore_send_observation(&lease, "self", &"a".repeat(64))
            .unwrap();
        let batch = store
            .prepare_outbound_batch(
                &lease,
                "trigger",
                r#"{"version":1,"conversation_id":"conversation","short_id":123}"#,
                &[OutboundSegmentDraft::text("hello")],
            )
            .unwrap();
        store
            .transition_segment(
                &lease,
                &batch.segments[0].id,
                SegmentTransition::StartAttempt,
            )
            .unwrap();
        let payload=serde_json::to_vec(&serde_json::json!({"version":1,"server_message_id":"987","conversation_id":"conversation","conversation_short_id":"123","sender_uid":"42","sender_sec_uid":"self","client_message_id":batch.segments[0].client_message_id,"message_type":1,"create_time_us":now*1000,"content_json":"{\"text\":\"hello\"}","text":"hello"})).unwrap();
        store
            .record_inbound_page(
                &lease,
                super::super::inbound::STREAM,
                1,
                100,
                &[crate::store::InboundReceiptDraft {
                    event_id: "987".into(),
                    payload,
                    payload_hash: "hash".into(),
                }],
            )
            .unwrap();
        let summary = filter_pending(
            &store,
            &lease,
            "self",
            u64::try_from(now).unwrap() * 1000,
            None,
        )
        .unwrap();
        assert_eq!(summary.reconciled_sends, 1);
        assert_eq!(summary.ignored, 1);
        assert!(store
            .pending_inbound_receipts(&lease, 128)
            .unwrap()
            .is_empty());
        let current = store.outbound_batch(&batch.id).unwrap();
        assert_eq!(current.segments[0].status, SegmentStatus::Confirmed);
        assert_eq!(current.segments[0].attempt_count, 1);
    }

    #[test]
    fn old_full_spool_is_reclaimed_before_new_page_admission() {
        let dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(dir.path()).unwrap();
        let now = i64::try_from(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_millis(),
        )
        .unwrap();
        let lease = store
            .install_verified_account_lease("account", "instance", "boot", 1, now + 60_000)
            .unwrap()
            .token();
        let cutoff = u64::try_from(store.ensure_inbound_live_start(&lease, 1).unwrap()).unwrap();
        let events:Vec<_>=(1..=128).map(|id| {
            let payload=serde_json::to_vec(&serde_json::json!({"version":1,"server_message_id":id.to_string(),
                "conversation_id":"conversation","conversation_short_id":"123","sender_uid":"42","sender_sec_uid":"peer",
                "client_message_id":"","message_type":1,"create_time_us":cutoff-1,
                "content_json":"{\"text\":\"fixture\"}","text":"fixture"})).unwrap();
            crate::store::InboundReceiptDraft{event_id:id.to_string(),payload,payload_hash:format!("hash-{id}")}
        }).collect();
        store
            .record_inbound_page(&lease, super::super::inbound::STREAM, 1, 100, &events)
            .unwrap();
        let summary = filter_pending(&store, &lease, "self", cutoff, None).unwrap();
        assert_eq!(summary.ignored, 128);
        assert!(store
            .pending_inbound_receipts(&lease, 128)
            .unwrap()
            .is_empty());
        let next = crate::store::InboundReceiptDraft {
            event_id: "129".into(),
            payload: vec![1],
            payload_hash: "next".into(),
        };
        store
            .record_bounded_inbound_page(
                &lease,
                crate::store::InboundPageDraft {
                    stream: super::super::inbound::STREAM,
                    stream_generation: 1,
                    checkpoint: 101,
                    receipts: &[next],
                },
                crate::store::InboundLimits {
                    pending_records: 128,
                    pending_bytes: 1_048_576,
                },
            )
            .unwrap();
    }
    #[test]
    fn manual_route_bytes_stay_compatible_and_auto_route_is_distinct() {
        let manual: Route =
            serde_json::from_str(r#"{"version":1,"conversation_id":"c","short_id":123}"#).unwrap();
        assert_eq!(manual.kind().unwrap(), WorkKind::ManualSend);
        assert_eq!(
            serde_json::to_string(&manual).unwrap(),
            r#"{"version":1,"conversation_id":"c","short_id":123}"#
        );
        let auto:Route=serde_json::from_str(r#"{"version":2,"conversation_id":"c","short_id":123,"automatic":true,"rule_id":"r","expires_at_ms":123}"#).unwrap();
        assert_eq!(auto.kind().unwrap(), WorkKind::AutomaticReply);
    }
    #[test]
    fn partial_batch_status_does_not_report_first_segment_as_full_success() {
        let dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(dir.path()).unwrap();
        let now = i64::try_from(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_millis(),
        )
        .unwrap();
        let lease = store
            .install_verified_account_lease("a", "i", "b", 1, now + 60_000)
            .unwrap()
            .token();
        let batch = store
            .prepare_outbound_batch(
                &lease,
                "trigger",
                "route",
                &[
                    OutboundSegmentDraft::text("one"),
                    OutboundSegmentDraft::text("two"),
                ],
            )
            .unwrap();
        store
            .transition_segment(
                &lease,
                &batch.segments[0].id,
                SegmentTransition::StartAttempt,
            )
            .unwrap();
        store
            .transition_segment(
                &lease,
                &batch.segments[0].id,
                SegmentTransition::Confirm {
                    platform_message_id: "1".into(),
                },
            )
            .unwrap();
        let final_batch = store
            .transition_segment(
                &lease,
                &batch.segments[1].id,
                SegmentTransition::CancelPrepared {
                    reason: "cancel".into(),
                },
            )
            .unwrap()
            .batch;
        let status = CommandStatus::from_batch(&final_batch).unwrap();
        assert_eq!(status.status, "done");
        assert!(!status.success);
    }
}
