//! Installation-level signed-lease controller. It consumes the existing central
//! timer, never starts a per-account polling loop, and performs no platform I/O.
use super::{
    model::{AccountId, AdmissionResult},
    supervisor::{AccountSpec, RuntimeHandle},
};
use crate::{
    control_plane::{
        AccountLeaseOperation, ControlError, GrantStatus, HostedControlClient, LeaseAction,
        LeaseSyncRequest, VerifiedGrantBatch,
    },
    state::{AccountRuntimeState, OwnershipState},
    store::{AccountLease, CoreStore, LeaseToken},
};
use anyhow::{bail, Context, Result};
use serde::Deserialize;
use std::{
    collections::BTreeMap,
    fs::File,
    io::Read,
    path::{Path, PathBuf},
    sync::{Arc, RwLock},
    time::{Duration, Instant},
};
use tokio::{
    sync::{watch, Mutex},
    task::JoinHandle,
};
use uuid::Uuid;

const RENEW_AFTER: Duration = Duration::from_secs(12);
const MAX_REQUEST_AGE: Duration = Duration::from_secs(60);

/// Local owner-only configuration. The public key must be provisioned separately
/// from the hosted response. Activation credentials are deliberately not Debug.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostedSettings {
    pub server: String,
    pub public_key_pem: String,
    pub activation_id: Uuid,
    pub activation_token: String,
    pub accounts: Vec<AccountLeaseOperation>,
    #[serde(default)]
    pub auth_state_file: Option<PathBuf>,
}
impl HostedSettings {
    /// Refreshes a rotating token without accepting a changed activation/server.
    /// # Errors
    /// Rejects invalid, unreadable or unbound authorization snapshots.
    pub fn refresh_activation(&mut self) -> Result<(), ControlError> {
        if let Some(path) = &self.auth_state_file {
            let auth = read_auth_snapshot(path)?;
            auth.validate(self.activation_id, self.server.trim_end_matches('/'))?;
            self.activation_token = auth.activation_token;
        }
        Ok(())
    }

    /// # Errors
    /// Rejects oversized/nonregular/world-accessible configuration files.
    pub fn load(path: &Path) -> Result<Self> {
        let file = File::open(path).context("cannot open hosted settings")?;
        let meta = file.metadata()?;
        anyhow::ensure!(
            meta.is_file() && meta.len() <= 1024 * 1024,
            "invalid hosted settings file"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            anyhow::ensure!(
                meta.permissions().mode().trailing_zeros() >= 6,
                "hosted settings must be owner-only"
            );
        }
        let mut raw = Vec::new();
        file.take(1024 * 1024 + 1).read_to_end(&mut raw)?;
        anyhow::ensure!(raw.len() <= 1024 * 1024, "hosted settings too large");
        serde_json::from_slice(&raw).context("invalid hosted settings schema")
    }
}

/// Secret-free live facts. Ownership is not evidence of platform sendability.
#[derive(Clone, Debug, Default)]
pub struct HostedStatus {
    pub owned_accounts: usize,
    pub completed_sequence: u64,
    pub allow_manual: bool,
    pub allow_auto: bool,
    pub stopped: bool,
    pub last_error: Option<&'static str>,
}

pub struct HostedController {
    stop: watch::Sender<bool>,
    status: watch::Receiver<HostedStatus>,
    join: Mutex<Option<JoinHandle<Result<()>>>>,
    authorizations: Arc<RwLock<BTreeMap<String, LeaseAuthorization>>>,
}

/// Produced only by the signed controller; never deserialized from IPC input.
#[derive(Clone)]
pub struct LeaseAuthorization {
    pub token: LeaseToken,
    pub deadline: Instant,
    pub allow_manual: bool,
    pub allow_auto: bool,
}
impl HostedController {
    /// Registers dormant account actors and starts one installation-level HTTP
    /// lane. Initial state never claims credentials/inbound/sending are healthy.
    /// # Errors
    /// Rejects invalid trust/configuration or bounded account registration failure.
    pub async fn start(
        settings: HostedSettings,
        instance_id: Uuid,
        boot_id: Uuid,
        runtime: RuntimeHandle,
        store: Arc<CoreStore>,
    ) -> Result<Arc<Self>> {
        let request = LeaseSyncRequest {
            activation_id: settings.activation_id,
            activation_token: settings.activation_token,
            instance_id,
            boot_id,
            request_id: Uuid::new_v4(),
            sequence: 1,
            accounts: settings.accounts,
        };
        request.validate()?;
        anyhow::ensure!(
            request
                .accounts
                .iter()
                .all(|a| a.action == LeaseAction::Acquire),
            "initial hosted operations must acquire ownership"
        );
        let client = Arc::new(HostedControlClient::new(
            &settings.server,
            &settings.public_key_pem,
        )?);
        let mut registered = Vec::new();
        for item in &request.accounts {
            let account_id = AccountId::new(&item.local_account_id)?;
            if runtime.account_control(&account_id).await.is_some() {
                registered.push(account_id);
                continue;
            }
            let result = runtime
                .upsert_account(AccountSpec {
                    account_id: account_id.clone(),
                    actor_generation: 1,
                    state: AccountRuntimeState {
                        ownership: OwnershipState::Acquiring,
                        credential_generation: 1,
                        ..AccountRuntimeState::default()
                    },
                })
                .await;
            if let Err(error) = result {
                for id in registered {
                    runtime.remove_account(&id).await?;
                }
                return Err(error);
            }
            registered.push(account_id);
        }
        let ticks = runtime.subscribe_control_ticks();
        let (stop, stop_rx) = watch::channel(false);
        let (status_tx, status) = watch::channel(HostedStatus::default());
        let authorizations = Arc::new(RwLock::new(BTreeMap::new()));
        let mut driver = Driver {
            auth_state_file: settings.auth_state_file,
            authority_url: settings.server.trim_end_matches('/').to_owned(),
            authorizations: authorizations.clone(),
            client,
            runtime,
            store,
            desired_accounts: request.accounts.clone(),
            request,
            leases: BTreeMap::new(),
            latest_epochs: BTreeMap::new(),
            deadline: None,
            status: status_tx,
            pending: None,
            next_sync: Instant::now(),
            request_started: Instant::now(),
            failures: 0,
        };
        let join = tokio::spawn(async move {
            let result = driver.run(ticks, stop_rx).await;
            if let Err(error) = &result {
                tracing::error!(%error, "hosted ownership loop failed");
            }
            let cleanup = driver.shutdown().await;
            result.and(cleanup)
        });
        Ok(Arc::new(Self {
            stop,
            status,
            join: Mutex::new(Some(join)),
            authorizations,
        }))
    }

    #[must_use]
    pub fn status(&self) -> HostedStatus {
        self.status.borrow().clone()
    }

    #[must_use]
    pub fn authorization(&self, account_id: &str) -> Option<LeaseAuthorization> {
        self.authorizations
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .get(account_id)
            .filter(|lease| Instant::now() < lease.deadline)
            .cloned()
    }

    /// Idempotently stops HTTP admission, invalidates local grants first, then
    /// makes one bounded remote release attempt and observes the owned task.
    /// # Errors
    /// Returns a controller failure or local lease invalidation error.
    pub async fn shutdown(&self) -> Result<()> {
        self.stop.send_replace(true);
        if let Some(join) = self.join.lock().await.take() {
            join.await.context("hosted controller task failed")??;
        }
        Ok(())
    }
}

type SyncTask = JoinHandle<std::result::Result<VerifiedGrantBatch, ControlError>>;
struct Driver {
    auth_state_file: Option<PathBuf>,
    authority_url: String,
    authorizations: Arc<RwLock<BTreeMap<String, LeaseAuthorization>>>,
    client: Arc<HostedControlClient>,
    runtime: RuntimeHandle,
    store: Arc<CoreStore>,
    request: LeaseSyncRequest,
    desired_accounts: Vec<AccountLeaseOperation>,
    leases: BTreeMap<String, AccountLease>,
    latest_epochs: BTreeMap<String, u64>,
    deadline: Option<Instant>,
    status: watch::Sender<HostedStatus>,
    pending: Option<SyncTask>,
    next_sync: Instant,
    request_started: Instant,
    failures: u32,
}
impl Driver {
    async fn run(
        &mut self,
        mut ticks: watch::Receiver<u64>,
        mut stop: watch::Receiver<bool>,
    ) -> Result<()> {
        loop {
            if *stop.borrow() {
                return Ok(());
            }
            if self.deadline.is_some_and(|d| Instant::now() >= d) {
                if let Some(job) = self.pending.take() {
                    job.abort();
                    let _ = job.await;
                }
                let releases = self.release_operations()?;
                self.invalidate(OwnershipState::Expired).await?;
                self.advance_request()?;
                if !releases.is_empty() {
                    self.request.accounts = releases;
                }
                self.next_sync = Instant::now();
            }
            if self.pending.is_none() && Instant::now() >= self.next_sync {
                if self.request_started.elapsed() >= MAX_REQUEST_AGE {
                    self.refresh_request_identity()?;
                }
                match self.refresh_auth().await {
                    Ok(_) => {
                        let client = self.client.clone();
                        let request = self.request.clone();
                        self.pending =
                            Some(tokio::spawn(async move { client.sync(&request).await }));
                    }
                    Err(ControlError::Transport) => {
                        self.failures = self.failures.saturating_add(1);
                        self.next_sync =
                            Instant::now() + retry_delay(self.failures, self.request.instance_id);
                    }
                    Err(error) => return Err(error.into()),
                }
            }
            tokio::select! {
                biased;
                changed = stop.changed() => { if changed.is_err() || *stop.borrow() { return Ok(()); } }
                changed = ticks.changed() => {
                    if changed.is_err() || *ticks.borrow_and_update() == u64::MAX { bail!("central timer stopped"); }
                }
                outcome = async { self.pending.as_mut().expect("pending branch enabled").await }, if self.pending.is_some() => {
                    self.pending = None;
                    match outcome.context("hosted HTTP task failed")? {
                        Ok(batch) => {
                            self.apply(batch).await?;
                            self.failures = 0;
                            self.advance_request()?;
                            self.next_sync = Instant::now() + if self.leases.is_empty() {
                                Duration::from_secs(2)
                            } else { RENEW_AFTER };
                        }
                        Err(error) => {
                            self.status.send_modify(|s| s.last_error = Some("hosted_sync_failed"));
                            // The shared license writer may have rotated the token
                            // between this request and its response. Retry briefly;
                            // only a new valid server signature can extend rights.
                            if matches!(error, ControlError::Http(401)) && self.auth_state_file.is_some() && self.failures < 2 {
                                self.failures += 1;
                                self.next_sync = Instant::now() + Duration::from_millis(500);
                                continue;
                            }
                            if !retryable(&error) {
                                self.invalidate(OwnershipState::Lost).await?;
                                return Err(error.into());
                            }
                            // A request's nonce/sequence/body stay byte-for-byte
                            // stable after transport/503 ambiguity.
                            self.failures = self.failures.saturating_add(1);
                            self.next_sync = Instant::now() + retry_delay(self.failures, self.request.instance_id);
                        }
                    }
                }
            }
        }
    }

    fn advance_request(&mut self) -> Result<()> {
        self.refresh_request_identity()?;
        // A release batch may contain only the owned subset. Never let that
        // temporary subset erase busy/quota-denied accounts from desired state.
        self.request.accounts = next_operations(&self.desired_accounts, &self.leases)?;
        Ok(())
    }

    async fn refresh_auth(&mut self) -> Result<bool, ControlError> {
        let Some(path) = self.auth_state_file.clone() else {
            return Ok(false);
        };
        let auth: AuthSnapshot = tokio::task::spawn_blocking(move || read_auth_snapshot(&path))
            .await
            .map_err(|_| ControlError::Transport)??;
        auth.validate(self.request.activation_id, &self.authority_url)?;
        let changed = auth.activation_token != self.request.activation_token;
        self.request.activation_token = auth.activation_token;
        Ok(changed)
    }

    fn refresh_request_identity(&mut self) -> Result<()> {
        self.request.sequence = self
            .request
            .sequence
            .checked_add(1)
            .filter(|seq| i64::try_from(*seq).is_ok())
            .context("hosted sequence exhausted")?;
        self.request.request_id = Uuid::new_v4();
        self.request_started = Instant::now();
        Ok(())
    }

    async fn apply(&mut self, batch: VerifiedGrantBatch) -> Result<()> {
        let store = self.store.clone();
        let (batch, installed) = tokio::task::spawn_blocking(move || {
            let installed = batch.install_owned(&store);
            (batch, installed)
        })
        .await?;
        let mut install_error = false;
        for result in installed {
            match result {
                Ok(lease) => {
                    self.leases.insert(lease.account_id.clone(), lease);
                }
                Err(_) => install_error = true,
            }
        }
        if install_error {
            bail!("signed lease could not be installed");
        }
        anyhow::ensure!(
            Instant::now() < batch.deadline(),
            "signed lease expired during installation"
        );
        for grant in batch.results() {
            let id = AccountId::new(&grant.local_account_id)?;
            let ownership = if grant.status == GrantStatus::Owned {
                // Persist the takeover boundary before publishing Owned. The
                // first scheduled receive may be delayed by bounded worker load.
                let control = self
                    .runtime
                    .account_control(&id)
                    .await
                    .context("account missing")?;
                let generation = i64::try_from(control.borrow().state.credential_generation)?;
                let token = self
                    .leases
                    .get(&grant.local_account_id)
                    .context("installed lease missing")?
                    .token();
                let store = self.store.clone();
                tokio::task::spawn_blocking(move || {
                    store.ensure_inbound_live_start(&token, generation)
                })
                .await??;
                self.latest_epochs
                    .insert(grant.local_account_id.clone(), grant.fence_epoch);
                OwnershipState::Owned
            } else {
                self.release_local(&grant.local_account_id).await?;
                OwnershipState::Lost
            };
            let epoch = *self
                .latest_epochs
                .get(&grant.local_account_id)
                .unwrap_or(&0);
            anyhow::ensure!(
                self.runtime
                    .update_account_ownership(&id, epoch, ownership)
                    .await?
                    == AdmissionResult::Accepted,
                "runtime rejected signed ownership update"
            );
        }
        self.deadline = Some(batch.deadline());
        *self
            .authorizations
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = self
            .leases
            .iter()
            .map(|(id, lease)| {
                (
                    id.clone(),
                    LeaseAuthorization {
                        token: lease.token(),
                        deadline: batch.deadline(),
                        allow_manual: batch.allows_manual(),
                        allow_auto: batch.allows_auto(),
                    },
                )
            })
            .collect();
        self.status.send_replace(HostedStatus {
            owned_accounts: self.leases.len(),
            completed_sequence: self.request.sequence,
            allow_manual: batch.allows_manual(),
            allow_auto: batch.allows_auto(),
            stopped: false,
            last_error: None,
        });
        tracing::info!(
            owned_accounts = self.leases.len(),
            sequence = self.request.sequence,
            "hosted ownership synchronized; platform workers remain separately gated"
        );
        Ok(())
    }

    async fn release_local(&mut self, id: &str) -> Result<()> {
        self.authorizations
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(id);
        if let Some(lease) = self.leases.get(id).cloned() {
            let store = self.store.clone();
            tokio::task::spawn_blocking(move || store.invalidate_account_lease(&lease.token()))
                .await??;
            self.leases.remove(id);
        }
        Ok(())
    }

    async fn invalidate(&mut self, ownership: OwnershipState) -> Result<()> {
        self.authorizations
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clear();
        // Disable published permissions even if a later disk operation fails.
        self.status.send_modify(|s| {
            s.owned_accounts = 0;
            s.allow_manual = false;
            s.allow_auto = false;
        });
        self.deadline = None;
        for item in self.desired_accounts.clone() {
            let id = AccountId::new(&item.local_account_id)?;
            let epoch = *self.latest_epochs.get(&item.local_account_id).unwrap_or(&0);
            // In-memory cancellation does not wait behind blocking disk I/O.
            self.runtime
                .update_account_ownership(&id, epoch, ownership)
                .await?;
            self.release_local(&item.local_account_id).await?;
        }
        Ok(())
    }

    async fn shutdown(&mut self) -> Result<()> {
        if let Some(job) = self.pending.take() {
            job.abort();
            let _ = job.await;
        }
        let release_accounts = self.release_operations()?;
        self.invalidate(OwnershipState::Lost).await?;
        self.status.send_modify(|s| s.stopped = true);
        if !release_accounts.is_empty() {
            self.advance_request()?;
            self.request.accounts = release_accounts;
            // Rotation must not leave a healthy server lease busy on restart.
            // If local state is unavailable, release remains best-effort below.
            let _ = self.refresh_auth().await;
            // Local grants are already unusable. Failed remote release naturally
            // expires server-side; never manufacture success or extend a lease.
            if !matches!(tokio::time::timeout(Duration::from_secs(3), self.client.sync(&self.request)).await,
                Ok(Ok(ref batch)) if batch.results().iter().all(|r| r.status == GrantStatus::Released))
            {
                tracing::warn!("remote lease release unconfirmed; awaiting server TTL");
            }
        }
        Ok(())
    }

    fn release_operations(&self) -> Result<Vec<AccountLeaseOperation>> {
        self.desired_accounts
            .iter()
            .filter_map(|a| {
                self.leases.get(&a.local_account_id).map(|lease| {
                    Ok(AccountLeaseOperation {
                        action: LeaseAction::Release,
                        expected_epoch: u64::try_from(lease.fence_epoch)?,
                        ..a.clone()
                    })
                })
            })
            .collect()
    }
}
fn retryable(error: &ControlError) -> bool {
    matches!(
        error,
        ControlError::Transport
            | ControlError::Http(408 | 409 | 429 | 500..=599)
            | ControlError::Time
    )
}

fn next_operations(
    desired: &[AccountLeaseOperation],
    leases: &BTreeMap<String, AccountLease>,
) -> Result<Vec<AccountLeaseOperation>> {
    desired
        .iter()
        .map(|item| {
            let mut item = item.clone();
            if let Some(lease) = leases.get(&item.local_account_id) {
                item.action = LeaseAction::Renew;
                item.expected_epoch = u64::try_from(lease.fence_epoch)?;
            } else {
                item.action = LeaseAction::Acquire;
                item.expected_epoch = 0;
            }
            Ok(item)
        })
        .collect()
}
fn retry_delay(failures: u32, installation: Uuid) -> Duration {
    let seconds = 1_u64 << failures.min(4);
    let jitter = u64::from(installation.as_bytes()[0]) * 2;
    Duration::from_millis(seconds * 1000 + jitter)
}

#[derive(Deserialize)]
struct AuthSnapshot {
    activation_id: Uuid,
    activation_token: String,
    server_url: String,
    local_state: String,
}
impl AuthSnapshot {
    fn validate(&self, activation_id: Uuid, authority_url: &str) -> Result<(), ControlError> {
        if self.activation_id != activation_id
            || self.server_url.trim_end_matches('/') != authority_url
            || !matches!(self.local_state.as_str(), "active" | "grace")
            || self.activation_token.is_empty()
            || self.activation_token.len() > 512
        {
            return Err(ControlError::Binding);
        }
        Ok(())
    }
}

fn read_auth_snapshot(path: &Path) -> Result<AuthSnapshot, ControlError> {
    let file = File::open(path).map_err(|_| ControlError::Transport)?;
    let meta = file.metadata().map_err(|_| ControlError::Transport)?;
    if !meta.is_file() || meta.len() > 256 * 1024 {
        return Err(ControlError::Binding);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if meta.permissions().mode().trailing_zeros() < 6 {
            return Err(ControlError::Binding);
        }
    }
    let mut raw = Vec::new();
    file.take(256 * 1024 + 1)
        .read_to_end(&mut raw)
        .map_err(|_| ControlError::Transport)?;
    if raw.len() > 256 * 1024 {
        return Err(ControlError::Binding);
    }
    serde_json::from_slice(&raw).map_err(|_| ControlError::Transport)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rotating_token_snapshot_preserves_authority_and_activation_binding() {
        let id = Uuid::new_v4();
        let mut auth = AuthSnapshot {
            activation_id: id,
            activation_token: "new-synthetic-token".into(),
            server_url: "https://authority.example/".into(),
            local_state: "active".into(),
        };
        assert!(auth.validate(id, "https://authority.example").is_ok());
        assert!(auth
            .validate(Uuid::new_v4(), "https://authority.example")
            .is_err());
        assert!(auth.validate(id, "https://another.example").is_err());
        auth.local_state = "revoked".into();
        assert!(auth.validate(id, "https://authority.example").is_err());
    }

    #[test]
    fn release_subset_does_not_remove_other_desired_accounts() {
        let desired: Vec<_> = ["owned", "busy"]
            .iter()
            .map(|id| AccountLeaseOperation {
                platform: "douyin".into(),
                platform_account_id: (*id).into(),
                local_account_id: (*id).into(),
                action: LeaseAction::Acquire,
                expected_epoch: 0,
            })
            .collect();
        let leases = BTreeMap::from([(
            "owned".into(),
            AccountLease {
                account_id: "owned".into(),
                owner_instance_id: "instance".into(),
                owner_boot_id: "boot".into(),
                fence_epoch: 7,
                lease_until_ms: 100,
                status: crate::store::LeaseStatus::Active,
                last_observed_at_ms: 1,
            },
        )]);
        let next = next_operations(&desired, &leases).unwrap();
        assert_eq!(next.len(), 2);
        assert_eq!(next[0].action, LeaseAction::Renew);
        assert_eq!(next[0].expected_epoch, 7);
        assert_eq!(next[1].action, LeaseAction::Acquire);
        let after_release = next_operations(&desired, &BTreeMap::new()).unwrap();
        assert_eq!(after_release.len(), 2);
        assert!(after_release
            .iter()
            .all(|a| a.action == LeaseAction::Acquire && a.expected_epoch == 0));
    }
    #[test]
    fn retry_policy_preserves_ambiguity_but_rejects_auth_and_signature_failure() {
        for error in [
            ControlError::Transport,
            ControlError::Http(503),
            ControlError::Time,
        ] {
            assert!(retryable(&error));
        }
        for error in [
            ControlError::Http(401),
            ControlError::Http(403),
            ControlError::Signature,
            ControlError::Binding,
            ControlError::Store,
        ] {
            assert!(!retryable(&error));
        }
        let id = Uuid::from_bytes([255; 16]);
        assert!(retry_delay(99, id) < Duration::from_secs(17));
        assert_eq!(retry_delay(3, id), retry_delay(3, id));
    }

    #[test]
    fn config_loader_rejects_oversize_and_insecure_permissions() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("settings.json");
        std::fs::write(&path, vec![b' '; 1024 * 1024 + 1]).unwrap();
        assert!(HostedSettings::load(&path).is_err());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::write(&path, b"{}").unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
            assert!(HostedSettings::load(&path)
                .err()
                .unwrap()
                .to_string()
                .contains("owner-only"));
        }
    }
}
