//! Process-wide runtime coordinator and owned async task lifecycle.

use std::{
    collections::{HashMap, HashSet},
    num::NonZeroU64,
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Mutex as StdMutex, RwLock as StdRwLock,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::{bail, Context, Result};
use tokio::{
    sync::{mpsc, watch, Mutex, Notify, RwLock},
    task::{JoinHandle, JoinSet},
    time,
};

use crate::{
    config::{RuntimeConfig, StorageConfig},
    health::{
        BreakerPhaseCounts, HealthHandle, MaintenanceHealthError, MaintenanceHealthResponse,
        RuntimeHealthPhase, RuntimeHealthResponse, SignerLaneHealthResponse,
        WorkClassHealthResponse,
    },
    state::{AccountRuntimeState, LifecycleState, OwnershipState, SendCapability},
    storage::SegmentStore,
    store::CoreStore,
};

use super::{
    account::{spawn_account_actor, AccountControl, AccountEndpoint, SpawnedAccountActor},
    breaker::{
        BreakerConfig, BreakerDecision, BreakerPermit, BreakerPhase, BreakerRecord, CircuitBreaker,
        DependencyOutcome, ReconnectBudget, ReconnectBudgetConfig, ReconnectDecision,
    },
    executor::{execute_guarded, ExecutionConfig, ExecutionCounters, ExecutionGuard, WorkExecutor},
    fair_queue::{FairQueue, FairQueueConfig},
    heartbeat::{AccountHeartbeatDelta, DependencyHealth, HeartbeatAggregator, HeartbeatConfig},
    lanes::{
        SignerAdmission, SignerCompletion, SignerJob, SignerLanes, SignerLanesConfig,
        SignerLanesSnapshot, SignerScope, SIGNER_WORK_KINDS,
    },
    metrics::RuntimeCounters,
    model::{
        AccountId, AdmissionResult, FairQueueSnapshot, WorkClass, WorkClassCapacities,
        WorkClassQueueSnapshot, WorkEnvelope, WorkFence, WorkKind,
    },
    storage_maintenance::StorageMaintenance,
    timer::{stable_jittered_delay_ms, CentralTimer, TimerSnapshot},
};

const TIMER_COMMAND_CAPACITY: usize = 1_024;
const MAX_DUE_PER_TURN: usize = 256;
const RECONCILE_HEALTHY_MS: u64 = 300_000;
const PENDING_RECOVERY_MS: u64 = 60_000;
const KEEPALIVE_MS: u64 = 20_000;
const HEALTH_REFRESH_MS: u64 = 250;
const ACCOUNT_TIMER_JITTER_BASIS_POINTS: u16 = 2_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RuntimePhase {
    Running,
    Draining,
    Stopped,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
enum TimerKey {
    Heartbeat,
    StorageCleanup,
    HealthRefresh,
    Account {
        account_id: AccountId,
        kind: WorkKind,
    },
}

#[derive(Debug)]
enum TimerCommand {
    UpsertAccount(AccountId),
    RemoveAccount(AccountId),
    Stop,
}

/// Desired account state installed into the supervisor registry.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountSpec {
    pub account_id: AccountId,
    pub actor_generation: u64,
    pub state: AccountRuntimeState,
}

impl AccountSpec {
    #[must_use]
    pub const fn control(&self) -> AccountControl {
        AccountControl {
            actor_generation: self.actor_generation,
            state: self.state,
        }
    }
}

#[derive(Debug)]
struct ManagedAccount {
    endpoint: AccountEndpoint,
    join: JoinHandle<super::account::AccountActorExit>,
    transport_breaker: Arc<StdMutex<CircuitBreaker>>,
    signer_breaker: Arc<StdMutex<CircuitBreaker>>,
    last_activity_ms: std::sync::atomic::AtomicU64,
}

#[derive(Clone)]
struct ExistingAccountFacts {
    endpoint: AccountEndpoint,
    control: AccountControl,
    transport: BreakerPhase,
    signer: BreakerPhase,
    last_activity_ms: u64,
}

#[derive(Default)]
struct BackgroundJoins {
    timer: Option<JoinHandle<()>>,
    dispatcher: Option<JoinHandle<()>>,
    signer_workers: Vec<JoinHandle<()>>,
}

#[derive(Clone, Debug, Default)]
struct MaintenanceStatus {
    last_success_ms: Option<u64>,
    failures: u64,
    last_error: Option<String>,
}

struct RuntimeInner {
    config: RuntimeConfig,
    started_at: Instant,
    accounts: RwLock<HashMap<AccountId, ManagedAccount>>,
    operations: Mutex<()>,
    fair_queue: Arc<Mutex<FairQueue>>,
    dispatcher_notify: Arc<Notify>,
    counters: Arc<RuntimeCounters>,
    signer_lanes: SignerLanes,
    signer_notifies: Vec<Arc<Notify>>,
    reconnect_budget: StdMutex<ReconnectBudget>,
    reconnect_last_grant: StdMutex<HashMap<AccountId, u64>>,
    heartbeat: StdMutex<HeartbeatAggregator>,
    timer_snapshot: StdRwLock<TimerSnapshot>,
    timer_tx: mpsc::Sender<TimerCommand>,
    phase: watch::Sender<RuntimePhase>,
    drain_complete: watch::Sender<bool>,
    health: HealthHandle,
    maintenance: StorageMaintenance,
    background_work_paused: AtomicBool,
    disposable_writes_allowed: AtomicBool,
    cleanup_in_flight: AtomicBool,
    cleanup_job: StdMutex<Option<JoinHandle<()>>>,
    maintenance_status: StdMutex<MaintenanceStatus>,
    timer_alive: AtomicBool,
    dispatcher_alive: AtomicBool,
    signer_workers_alive: AtomicUsize,
    joins: StdMutex<BackgroundJoins>,
    drain_started: AtomicBool,
    drain_job: StdMutex<Option<JoinHandle<()>>>,
    control_ticks: watch::Sender<u64>,
    executor: StdRwLock<Option<ExecutionBinding>>,
    execution: Arc<ExecutionCounters>,
}

#[derive(Clone)]
struct ExecutionBinding {
    executor: Arc<dyn WorkExecutor>,
    config: ExecutionConfig,
}

#[derive(Clone, Copy)]
enum RuntimeTaskKind {
    Timer,
    Dispatcher,
    Signer,
}

struct RuntimeTaskGuard {
    inner: Arc<RuntimeInner>,
    kind: RuntimeTaskKind,
}

impl RuntimeTaskGuard {
    fn new(inner: Arc<RuntimeInner>, kind: RuntimeTaskKind) -> Self {
        match kind {
            RuntimeTaskKind::Timer => inner.timer_alive.store(true, Ordering::Release),
            RuntimeTaskKind::Dispatcher => {
                inner.dispatcher_alive.store(true, Ordering::Release);
            }
            RuntimeTaskKind::Signer => {
                inner.signer_workers_alive.fetch_add(1, Ordering::AcqRel);
            }
        }
        publish_task_liveness(&inner);
        Self { inner, kind }
    }
}

impl Drop for RuntimeTaskGuard {
    fn drop(&mut self) {
        match self.kind {
            RuntimeTaskKind::Timer => {
                self.inner.timer_alive.store(false, Ordering::Release);
                self.inner.control_ticks.send_replace(u64::MAX);
            }
            RuntimeTaskKind::Dispatcher => {
                self.inner.dispatcher_alive.store(false, Ordering::Release);
            }
            RuntimeTaskKind::Signer => {
                self.inner
                    .signer_workers_alive
                    .fetch_sub(1, Ordering::AcqRel);
            }
        }
        publish_task_liveness(&self.inner);
    }
}

struct DrainCompletionGuard(Arc<RuntimeInner>);

impl Drop for DrainCompletionGuard {
    fn drop(&mut self) {
        if !*self.0.drain_complete.borrow() {
            self.0.health.set_lifecycle(LifecycleState::Faulted);
        }
    }
}

/// Cloneable API for account registration, bounded ingress, live snapshots,
/// and graceful process drain.
#[derive(Clone)]
pub struct RuntimeHandle {
    inner: Arc<RuntimeInner>,
}

/// Account-bound dependency isolated by its own breaker.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DependencyKind {
    Transport,
    Signer,
}

/// Token binding a dependency completion to the current account breaker.
#[derive(Debug)]
pub struct DependencyPermit {
    account_id: AccountId,
    breaker: Arc<StdMutex<CircuitBreaker>>,
    kind: DependencyKind,
    permit: Option<BreakerPermit>,
    fence: WorkFence,
}

impl DependencyPermit {
    fn take_permit(&mut self) -> Option<BreakerPermit> {
        self.permit.take()
    }

    fn abandon_inner(&mut self) -> bool {
        let Some(permit) = self.take_permit() else {
            return false;
        };
        lock_std(&self.breaker).abandon(permit)
    }
}

impl Drop for DependencyPermit {
    fn drop(&mut self) {
        let _ = self.abandon_inner();
    }
}

/// Explicit dependency/reconnect admission result.
#[derive(Debug)]
pub enum DependencyAdmission {
    Allowed(DependencyPermit),
    CircuitOpen { retry_at_ms: u64 },
    HalfOpenProbeLimit,
    DependencyInFlightLimit,
    GlobalReconnectThrottled { retry_at_ms: u64 },
    AccountReconnectThrottled { retry_at_ms: u64 },
    UnknownAccount,
    Stopping,
}

impl RuntimeHandle {
    /// Starts the central timer and fair dispatcher. The returned runtime is a
    /// no-network shadow executor; it cannot perform a Douyin side effect.
    ///
    /// # Errors
    ///
    /// Returns invalid queue/signer/heartbeat configuration diagnostics.
    pub fn start(
        config: RuntimeConfig,
        installation_id: impl Into<String>,
        core: Arc<CoreStore>,
        segments: SegmentStore,
        storage_config: StorageConfig,
        health: HealthHandle,
    ) -> Result<Self> {
        config.validate()?;
        let class_capacities = class_capacities(config.global_queue_capacity);
        let fair_queue = FairQueue::new(FairQueueConfig {
            global_capacity: config.global_queue_capacity,
            per_account_capacity: config.per_account_queue_capacity,
            class_capacities,
            max_manual_burst: config.manual_burst,
        })
        .context("invalid fair queue configuration")?;
        let signer_lanes = SignerLanes::new(SignerLanesConfig {
            lane_count: config.signer_lanes,
            queue_capacity: config.signer_queue_capacity,
            per_account_capacity: config.per_account_queue_capacity.min(32),
        })
        .context("invalid signer lane configuration")?;
        let heartbeat = HeartbeatAggregator::new(
            installation_id,
            HeartbeatConfig {
                max_tracked_accounts: config.heartbeat_max_accounts,
                max_accounts_per_batch: config.heartbeat_max_accounts,
                max_payload_bytes: 512 * 1_024,
                max_inflight_batches: 16,
                full_refresh_interval_ms: config.heartbeat_full_interval_ms,
                max_account_id_bytes: super::model::MAX_ACCOUNT_ID_BYTES,
            },
        )
        .context("invalid heartbeat configuration")?;
        let (timer_tx, timer_rx) = mpsc::channel(TIMER_COMMAND_CAPACITY);
        let (phase, _) = watch::channel(RuntimePhase::Running);
        let (drain_complete, _) = watch::channel(false);
        let initial_storage = health.snapshot().storage;
        let signer_notifies = (0..config.signer_lanes)
            .map(|_| Arc::new(Notify::new()))
            .collect::<Vec<_>>();
        let reconnect_budget = ReconnectBudget::new(
            ReconnectBudgetConfig {
                capacity: config.reconnect_burst,
                refill_tokens: config.reconnect_rate_per_second,
                refill_interval_ms: 1_000,
            },
            0,
        )
        .context("invalid reconnect budget configuration")?;
        let inner = Arc::new(RuntimeInner {
            config,
            started_at: Instant::now(),
            accounts: RwLock::new(HashMap::new()),
            operations: Mutex::new(()),
            fair_queue: Arc::new(Mutex::new(fair_queue)),
            dispatcher_notify: Arc::new(Notify::new()),
            counters: Arc::new(RuntimeCounters::default()),
            signer_lanes,
            signer_notifies,
            reconnect_budget: StdMutex::new(reconnect_budget),
            reconnect_last_grant: StdMutex::new(HashMap::new()),
            heartbeat: StdMutex::new(heartbeat),
            timer_snapshot: StdRwLock::new(TimerSnapshot::default()),
            timer_tx,
            phase,
            drain_complete,
            health,
            maintenance: StorageMaintenance::new(core, segments, storage_config),
            background_work_paused: AtomicBool::new(initial_storage.background_work_paused),
            disposable_writes_allowed: AtomicBool::new(initial_storage.disposable_writes_allowed),
            cleanup_in_flight: AtomicBool::new(false),
            cleanup_job: StdMutex::new(None),
            maintenance_status: StdMutex::new(MaintenanceStatus::default()),
            timer_alive: AtomicBool::new(false),
            dispatcher_alive: AtomicBool::new(false),
            signer_workers_alive: AtomicUsize::new(0),
            joins: StdMutex::new(BackgroundJoins::default()),
            drain_started: AtomicBool::new(false),
            drain_job: StdMutex::new(None),
            control_ticks: watch::channel(0).0,
            executor: StdRwLock::new(None),
            execution: Arc::new(ExecutionCounters::default()),
        });

        let dispatcher = tokio::spawn(run_dispatcher(inner.clone()));
        let timer = tokio::spawn(run_timer(inner.clone(), timer_rx));
        let signer_workers = inner
            .signer_notifies
            .iter()
            .enumerate()
            .map(|(lane, notify)| {
                tokio::spawn(run_signer_lane(inner.clone(), lane, Arc::clone(notify)))
            })
            .collect();
        {
            let mut joins = lock_std(&inner.joins);
            joins.dispatcher = Some(dispatcher);
            joins.timer = Some(timer);
            joins.signer_workers = signer_workers;
        }
        let handle = Self { inner };
        handle.publish_health_from_cached();
        Ok(handle)
    }

    /// Adds, replaces, or updates one account actor. Replacing an actor first
    /// stops and observes the previous generation.
    ///
    /// # Errors
    ///
    /// Returns when the generation is zero, the runtime is draining, aggregate
    /// state exceeds a configured bound, or the central timer has stopped.
    pub async fn upsert_account(&self, spec: AccountSpec) -> Result<()> {
        if spec.actor_generation == 0 {
            bail!("actor generation must be positive");
        }
        let _operation = self.inner.operations.lock().await;
        if !self.inner.counters.accepting_work()
            || *self.inner.phase.borrow() != RuntimePhase::Running
        {
            bail!("runtime is draining");
        }

        let existing_facts = self.existing_account_facts(&spec.account_id).await;

        if let Some(existing) = existing_facts.as_ref() {
            if control_regresses(existing.control, spec.control()) {
                bail!("stale account control update rejected");
            }
        }

        if existing_facts.is_none()
            && self.inner.accounts.read().await.len() >= self.inner.config.heartbeat_max_accounts
        {
            bail!(
                "account capacity {} reached",
                self.inner.config.heartbeat_max_accounts
            );
        }

        if let Some(existing) = existing_facts.as_ref() {
            if existing.control.actor_generation == spec.actor_generation {
                return self.update_existing_account(&spec, existing).await;
            }
        }
        self.replace_account(spec, existing_facts.as_ref()).await
    }

    async fn existing_account_facts(&self, account_id: &AccountId) -> Option<ExistingAccountFacts> {
        let accounts = self.inner.accounts.read().await;
        accounts
            .get(account_id)
            .map(|account| ExistingAccountFacts {
                endpoint: account.endpoint.clone(),
                control: account.endpoint.control_snapshot(),
                transport: lock_std(&account.transport_breaker).phase(),
                signer: lock_std(&account.signer_breaker).phase(),
                last_activity_ms: account.last_activity_ms.load(Ordering::Acquire),
            })
    }

    async fn update_existing_account(
        &self,
        spec: &AccountSpec,
        existing: &ExistingAccountFacts,
    ) -> Result<()> {
        self.publish_heartbeat_values(
            &spec.account_id,
            spec.control(),
            existing.endpoint.mailbox_depth(),
            existing.transport,
            existing.signer,
            existing.last_activity_ms,
        )?;
        if let Err(error) = self
            .inner
            .timer_tx
            .try_send(TimerCommand::UpsertAccount(spec.account_id.clone()))
        {
            let _ = self.publish_heartbeat_values(
                &spec.account_id,
                existing.control,
                existing.endpoint.mailbox_depth(),
                existing.transport,
                existing.signer,
                existing.last_activity_ms,
            );
            return Err(error).context("central timer stopped while updating account");
        }
        existing.endpoint.update_control(spec.control());
        let purged = self.inner.signer_lanes.install_scope(signer_scope(spec));
        self.inner.counters.add_unresolved_work(purged);
        if control_fence_changed(existing.control, spec.control()) {
            lock_std(&self.inner.reconnect_last_grant).remove(&spec.account_id);
            let removed = self
                .inner
                .fair_queue
                .lock()
                .await
                .remove_account(&spec.account_id)
                .len();
            self.inner.counters.add_unresolved_work(removed);
        }
        self.publish_account_heartbeat(&spec.account_id).await?;
        self.publish_health().await;
        Ok(())
    }

    async fn replace_account(
        &self,
        spec: AccountSpec,
        existing_facts: Option<&ExistingAccountFacts>,
    ) -> Result<()> {
        let transport_breaker = new_breaker(&spec.account_id, DependencyKind::Transport)?;
        let signer_breaker = new_breaker(&spec.account_id, DependencyKind::Signer)?;
        self.publish_heartbeat_values(
            &spec.account_id,
            spec.control(),
            0,
            BreakerPhase::Closed,
            BreakerPhase::Closed,
            0,
        )?;
        if let Err(error) = self
            .inner
            .timer_tx
            .try_send(TimerCommand::UpsertAccount(spec.account_id.clone()))
        {
            self.rollback_heartbeat(&spec.account_id, existing_facts);
            return Err(error).context("central timer stopped while upserting account");
        }

        let existing = self.inner.accounts.write().await.remove(&spec.account_id);
        if let Some(existing) = existing {
            existing.endpoint.stop();
            observe_account_until(
                existing,
                Instant::now() + Duration::from_millis(self.inner.config.drain_timeout_ms),
                &self.inner.counters,
            )
            .await;
            let removed = self
                .inner
                .fair_queue
                .lock()
                .await
                .remove_account(&spec.account_id)
                .len();
            let signer_removed = self
                .inner
                .signer_lanes
                .remove_scope(spec.account_id.as_str());
            self.inner
                .counters
                .add_unresolved_work(removed.saturating_add(signer_removed));
        }
        lock_std(&self.inner.reconnect_last_grant).remove(&spec.account_id);
        if !self.inner.counters.accepting_work()
            || *self.inner.phase.borrow() != RuntimePhase::Running
        {
            let _ = lock_std(&self.inner.heartbeat).remove(spec.account_id.as_str());
            let _ = self
                .inner
                .timer_tx
                .try_send(TimerCommand::RemoveAccount(spec.account_id.clone()));
            bail!("runtime began draining while replacing account");
        }

        let mut registry = self.inner.accounts.write().await;
        if !self.inner.counters.accepting_work() {
            bail!("runtime began draining before account installation");
        }
        let SpawnedAccountActor { endpoint, join } = spawn_account_actor(
            spec.account_id.clone(),
            spec.control(),
            self.inner.config.account_mailbox_capacity,
            self.inner.fair_queue.clone(),
            self.inner.dispatcher_notify.clone(),
            self.inner.counters.clone(),
        );
        registry.insert(
            spec.account_id.clone(),
            ManagedAccount {
                endpoint,
                join,
                transport_breaker,
                signer_breaker,
                last_activity_ms: std::sync::atomic::AtomicU64::new(0),
            },
        );
        drop(registry);
        let purged = self.inner.signer_lanes.install_scope(signer_scope(&spec));
        self.inner.counters.add_unresolved_work(purged);
        self.publish_account_heartbeat(&spec.account_id).await?;
        self.refresh_actor_count().await;
        self.publish_health().await;
        Ok(())
    }

    /// Replaces latest account control facts without entering its data mailbox.
    ///
    /// # Errors
    ///
    /// Returns when the bounded heartbeat aggregator cannot retain the new
    /// latest account state.
    pub async fn update_account_control(
        &self,
        account_id: &AccountId,
        control: AccountControl,
    ) -> Result<AdmissionResult> {
        let _operation = self.inner.operations.lock().await;
        self.update_account_control_locked(account_id, control)
            .await
    }

    /// Reuses the central timer's coalesced ticks; callers must never block it
    /// on hosted HTTP or database work.
    #[must_use]
    pub fn subscribe_control_ticks(&self) -> watch::Receiver<u64> {
        self.inner.control_ticks.subscribe()
    }

    #[must_use]
    pub fn monotonic_now_ms(&self) -> u64 {
        monotonic_ms(self.inner.started_at)
    }

    /// Installs the real business handler before account admission. Existing
    /// shadow callers remain unchanged; a configured handler is never bypassed.
    /// # Errors
    /// Rejects replacement, late attachment, invalid limits, or a draining runtime.
    pub async fn install_executor(
        &self,
        executor: Arc<dyn WorkExecutor>,
        config: ExecutionConfig,
    ) -> Result<()> {
        let config = config.validate()?;
        let _operation = self.inner.operations.lock().await;
        anyhow::ensure!(
            self.inner.counters.accepting_work() && self.inner.accounts.read().await.is_empty(),
            "install executor before registering accounts"
        );
        {
            let mut current = write_std(&self.inner.executor);
            anyhow::ensure!(current.is_none(), "executor already installed");
            *current = Some(ExecutionBinding { executor, config });
        }
        self.publish_health().await;
        Ok(())
    }

    /// Observes latest account facts without copying credential material.
    pub async fn account_control(&self, id: &AccountId) -> Option<watch::Receiver<AccountControl>> {
        self.inner
            .accounts
            .read()
            .await
            .get(id)
            .map(|a| a.endpoint.subscribe_control())
    }

    /// Applies a protocol result only to the exact actor/credential/lease that
    /// produced it, preserving independently updated ownership/inbound facts.
    /// # Errors
    /// Returns control publication errors; stale observations have no effect.
    pub async fn update_send_capability(
        &self,
        id: &AccountId,
        fence: WorkFence,
        capability: SendCapability,
    ) -> Result<AdmissionResult> {
        let _operation = self.inner.operations.lock().await;
        let current = self
            .inner
            .accounts
            .read()
            .await
            .get(id)
            .map(|a| a.endpoint.control_snapshot());
        let Some(mut control) = current else {
            return Ok(AdmissionResult::UnknownAccount);
        };
        if !fence.matches(
            control.actor_generation,
            control.state.credential_generation,
            control.state.lease_epoch,
        ) {
            return Ok(AdmissionResult::Stale);
        }
        control.state.send = capability;
        self.update_account_control_locked(id, control).await
    }

    /// Applies a protocol result only to the exact actor/credential/lease that
    /// produced it, preserving independently updated ownership/send facts.
    /// # Errors
    /// Returns control publication errors; stale observations have no effect.
    pub async fn update_inbound_state(
        &self,
        id: &AccountId,
        fence: WorkFence,
        inbound: crate::state::InboundState,
    ) -> Result<AdmissionResult> {
        let _operation = self.inner.operations.lock().await;
        let current = self
            .inner
            .accounts
            .read()
            .await
            .get(id)
            .map(|a| a.endpoint.control_snapshot());
        let Some(mut control) = current else {
            return Ok(AdmissionResult::UnknownAccount);
        };
        if !fence.matches(
            control.actor_generation,
            control.state.credential_generation,
            control.state.lease_epoch,
        ) {
            return Ok(AdmissionResult::Stale);
        }
        control.state.inbound = inbound;
        self.update_account_control_locked(id, control).await
    }

    /// Opens explicit manual admission without claiming successful platform
    /// authentication or sendability. Only a dormant actor can be activated.
    /// # Errors
    /// Returns account/control publication errors.
    pub async fn activate_manual_account(&self, id: &AccountId) -> Result<AdmissionResult> {
        let _operation = self.inner.operations.lock().await;
        let current = self
            .inner
            .accounts
            .read()
            .await
            .get(id)
            .map(|a| a.endpoint.control_snapshot());
        let Some(mut control) = current else {
            return Ok(AdmissionResult::UnknownAccount);
        };
        if control.state.lifecycle == LifecycleState::Starting {
            control.state.lifecycle = LifecycleState::PausedAuto;
        }
        self.update_account_control_locked(id, control).await
    }

    /// Pauses automatic execution without resetting inbound, ownership or send evidence.
    /// # Errors
    /// Reports account/control publication errors.
    pub async fn pause_automatic_account(&self, id: &AccountId) -> Result<AdmissionResult> {
        let _operation = self.inner.operations.lock().await;
        let current = self
            .inner
            .accounts
            .read()
            .await
            .get(id)
            .map(|a| a.endpoint.control_snapshot());
        let Some(mut control) = current else {
            return Ok(AdmissionResult::UnknownAccount);
        };
        if matches!(
            control.state.lifecycle,
            LifecycleState::Running | LifecycleState::Starting
        ) {
            control.state.lifecycle = LifecycleState::PausedAuto;
        }
        self.update_account_control_locked(id, control).await
    }

    /// Resumes configured automation after service identity checks; never creates Sendable.
    /// # Errors
    /// Returns account/control publication errors.
    pub async fn activate_automatic_account(&self, id: &AccountId) -> Result<AdmissionResult> {
        let _operation = self.inner.operations.lock().await;
        let current = self
            .inner
            .accounts
            .read()
            .await
            .get(id)
            .map(|a| a.endpoint.control_snapshot());
        let Some(mut control) = current else {
            return Ok(AdmissionResult::UnknownAccount);
        };
        if control.state.lifecycle == LifecycleState::PausedAuto
            && matches!(
                control.state.send,
                SendCapability::Unknown | SendCapability::Sendable
            )
        {
            control.state.lifecycle = LifecycleState::Running;
        }
        self.update_account_control_locked(id, control).await
    }

    /// Changes only ownership facts under the same gate as credential/platform
    /// updates, preserving risk-control and inbound state during renewals.
    /// # Errors
    /// Returns heartbeat publication/storage-independent runtime errors.
    pub async fn update_account_ownership(
        &self,
        account_id: &AccountId,
        epoch: u64,
        ownership: OwnershipState,
    ) -> Result<AdmissionResult> {
        let _operation = self.inner.operations.lock().await;
        let current = self
            .inner
            .accounts
            .read()
            .await
            .get(account_id)
            .map(|account| account.endpoint.control_snapshot());
        let Some(mut control) = current else {
            return Ok(AdmissionResult::UnknownAccount);
        };
        control.state.lease_epoch = epoch;
        control.state.ownership = ownership;
        self.update_account_control_locked(account_id, control)
            .await
    }

    async fn update_account_control_locked(
        &self,
        account_id: &AccountId,
        control: AccountControl,
    ) -> Result<AdmissionResult> {
        if !self.inner.counters.accepting_work() {
            return Ok(AdmissionResult::Stopping);
        }
        let endpoint = {
            let accounts = self.inner.accounts.read().await;
            accounts
                .get(account_id)
                .map(|account| account.endpoint.clone())
        };
        let Some(endpoint) = endpoint else {
            return Ok(AdmissionResult::UnknownAccount);
        };
        let previous = endpoint.control_snapshot();
        if control_regresses(previous, control) {
            return Ok(AdmissionResult::Stale);
        }
        let (transport, signer, last_activity_ms) = {
            let accounts = self.inner.accounts.read().await;
            let account = accounts
                .get(account_id)
                .context("account disappeared during control update")?;
            let transport = lock_std(&account.transport_breaker).phase();
            let signer = lock_std(&account.signer_breaker).phase();
            let activity = account.last_activity_ms.load(Ordering::Acquire);
            (transport, signer, activity)
        };
        self.publish_heartbeat_values(
            account_id,
            control,
            endpoint.mailbox_depth(),
            transport,
            signer,
            last_activity_ms,
        )?;
        endpoint.update_control(control);
        let purged = self
            .inner
            .signer_lanes
            .install_scope(signer_scope_from_control(account_id, control));
        self.inner.counters.add_unresolved_work(purged);
        if previous.actor_generation != control.actor_generation
            || previous.state.credential_generation != control.state.credential_generation
            || previous.state.lease_epoch != control.state.lease_epoch
        {
            lock_std(&self.inner.reconnect_last_grant).remove(account_id);
            let removed = self
                .inner
                .fair_queue
                .lock()
                .await
                .remove_account(account_id)
                .len();
            self.inner.counters.add_unresolved_work(removed);
        }
        self.publish_account_heartbeat(account_id).await?;
        self.publish_health().await;
        Ok(AdmissionResult::Accepted)
    }

    /// Removes one account, cancels its timers and queued work, and observes
    /// actor termination before returning.
    ///
    /// # Errors
    ///
    /// Returns when the central timer has stopped or the aggregate removal
    /// tombstone cannot be published.
    pub async fn remove_account(&self, account_id: &AccountId) -> Result<bool> {
        let _operation = self.inner.operations.lock().await;
        let account = {
            let mut registry = self.inner.accounts.write().await;
            if !registry.contains_key(account_id) {
                return Ok(false);
            }
            self.inner
                .timer_tx
                .try_send(TimerCommand::RemoveAccount(account_id.clone()))
                .context("central timer busy or stopped; account removal not applied")?;
            registry
                .remove(account_id)
                .context("account missing under exclusive registry lock")?
        };
        account.endpoint.stop();
        observe_account_until(
            account,
            Instant::now() + Duration::from_millis(self.inner.config.drain_timeout_ms),
            &self.inner.counters,
        )
        .await;
        let removed = self
            .inner
            .fair_queue
            .lock()
            .await
            .remove_account(account_id)
            .len();
        let signer_removed = self.inner.signer_lanes.remove_scope(account_id.as_str());
        lock_std(&self.inner.reconnect_last_grant).remove(account_id);
        self.inner
            .counters
            .add_unresolved_work(removed.saturating_add(signer_removed));
        lock_std(&self.inner.heartbeat)
            .remove(account_id.as_str())
            .context("cannot publish heartbeat removal")?;
        self.refresh_actor_count().await;
        self.publish_health().await;
        Ok(true)
    }

    /// Attempts bounded, nonblocking ingress into one account mailbox.
    pub async fn enqueue(&self, work: WorkEnvelope) -> AdmissionResult {
        if !self.inner.counters.accepting_work() {
            return AdmissionResult::Stopping;
        }
        if !work.has_bounded_identity() {
            self.inner.counters.note_invalid_work();
            return AdmissionResult::Invalid;
        }
        if self.inner.background_work_paused.load(Ordering::Acquire)
            && work.kind.is_disposable_background()
        {
            self.inner.counters.note_background_deferred();
            return AdmissionResult::Deferred;
        }
        let endpoint = {
            let accounts = self.inner.accounts.read().await;
            accounts
                .get(&work.account_id)
                .map(|account| account.endpoint.clone())
        };
        let Some(endpoint) = endpoint else {
            return AdmissionResult::UnknownAccount;
        };
        endpoint.enqueue(work).await
    }

    /// Applies the account-local breaker and, for transport reconnects, the
    /// process-wide token budget before allowing an attempt.
    pub async fn try_dependency(
        &self,
        account_id: &AccountId,
        kind: DependencyKind,
    ) -> DependencyAdmission {
        if !self.inner.counters.accepting_work() {
            return DependencyAdmission::Stopping;
        }
        let now_ms = monotonic_ms(self.inner.started_at);
        let (control, breaker, active_accounts) = {
            let accounts = self.inner.accounts.read().await;
            let Some(account) = accounts.get(account_id) else {
                return DependencyAdmission::UnknownAccount;
            };
            (
                account.endpoint.control_snapshot(),
                Arc::clone(dependency_breaker(account, kind)),
                accounts.len(),
            )
        };
        let mut breaker_guard = lock_std(&breaker);
        if breaker_guard.snapshot().in_flight_attempts
            >= self.inner.config.per_account_queue_capacity
        {
            return DependencyAdmission::DependencyInFlightLimit;
        }
        match breaker_guard.try_acquire(now_ms) {
            BreakerDecision::Open { retry_at_ms } => {
                DependencyAdmission::CircuitOpen { retry_at_ms }
            }
            BreakerDecision::HalfOpenProbeLimit => DependencyAdmission::HalfOpenProbeLimit,
            BreakerDecision::Allowed(permit) => {
                if kind == DependencyKind::Transport {
                    let fairness_interval_ms = reconnect_fairness_interval_ms(
                        active_accounts,
                        self.inner.config.reconnect_rate_per_second,
                    );
                    let mut last_grants = lock_std(&self.inner.reconnect_last_grant);
                    if let Some(retry_at_ms) = last_grants
                        .get(account_id)
                        .map(|last_grant| last_grant.saturating_add(fairness_interval_ms))
                    {
                        if now_ms < retry_at_ms {
                            let _ = breaker_guard.abandon(permit);
                            return DependencyAdmission::AccountReconnectThrottled { retry_at_ms };
                        }
                    }
                    match lock_std(&self.inner.reconnect_budget).try_acquire(now_ms) {
                        ReconnectDecision::Granted => {
                            last_grants.insert(account_id.clone(), now_ms);
                        }
                        ReconnectDecision::Throttled { retry_at_ms } => {
                            let _ = breaker_guard.abandon(permit);
                            return DependencyAdmission::GlobalReconnectThrottled { retry_at_ms };
                        }
                    }
                }
                drop(breaker_guard);
                DependencyAdmission::Allowed(DependencyPermit {
                    account_id: account_id.clone(),
                    breaker,
                    kind,
                    permit: Some(permit),
                    fence: WorkFence {
                        actor_generation: control.actor_generation,
                        credential_generation: control.state.credential_generation,
                        lease_epoch: control.state.lease_epoch,
                    },
                })
            }
        }
    }

    /// Records a dependency result against the exact breaker generation that
    /// admitted it. Business/auth/risk results close transport backoff and are
    /// left for the caller to map onto account state.
    pub async fn record_dependency_outcome(
        &self,
        mut permit: DependencyPermit,
        outcome: DependencyOutcome,
    ) -> Option<BreakerRecord> {
        let now_ms = monotonic_ms(self.inner.started_at);
        let accounts = self.inner.accounts.read().await;
        let account = accounts.get(&permit.account_id)?;
        let control = account.endpoint.control_snapshot();
        let current = Arc::ptr_eq(&permit.breaker, dependency_breaker(account, permit.kind))
            && permit.fence.matches(
                control.actor_generation,
                control.state.credential_generation,
                control.state.lease_epoch,
            );
        let raw_permit = permit.take_permit()?;
        let record = if current {
            let record = lock_std(&permit.breaker).record_outcome(raw_permit, outcome, now_ms);
            account
                .last_activity_ms
                .store(unix_epoch_ms(), Ordering::Release);
            record
        } else {
            let _ = lock_std(&permit.breaker).abandon(raw_permit);
            BreakerRecord::IgnoredStale
        };
        drop(accounts);
        let _ = self.publish_account_heartbeat(&permit.account_id).await;
        Some(record)
    }

    /// Releases a dependency attempt that was cancelled or timed out before a
    /// result could be recorded. Callers must use this path for every admitted
    /// permit they do not pass to [`Self::record_dependency_outcome`].
    pub async fn abandon_dependency(&self, mut permit: DependencyPermit) -> bool {
        let account_id = permit.account_id.clone();
        let abandoned = permit.abandon_inner();
        let _ = self.publish_account_heartbeat(&account_id).await;
        abandoned
    }

    /// Admits an account-scoped signer job to one of the fixed no-network
    /// shadow workers. The real protocol signer is wired only in a later gate.
    pub async fn admit_signer(&self, job: SignerJob) -> SignerAdmission {
        if !self.inner.counters.accepting_work() {
            return SignerAdmission::Closed;
        }
        if job.durable_id.is_empty() || job.durable_id.len() > super::model::MAX_DURABLE_ID_BYTES {
            return self.inner.signer_lanes.admit(job);
        }
        let Ok(account_id) = AccountId::new(job.account_id.clone()) else {
            return SignerAdmission::Stale;
        };
        let accounts = self.inner.accounts.read().await;
        let current = accounts
            .get(&account_id)
            .map(|account| account.endpoint.control_snapshot());
        if !current.is_some_and(|control| control_allows_signer(control, &job)) {
            return SignerAdmission::Stale;
        }
        let admission = self.inner.signer_lanes.admit(job);
        drop(accounts);
        if let SignerAdmission::Accepted { lane } = admission {
            if let Some(notify) = self.inner.signer_notifies.get(lane) {
                notify.notify_one();
            }
        }
        admission
    }

    /// Returns the current live health projection after sampling bounded
    /// component snapshots.
    pub async fn snapshot(&self) -> RuntimeHealthResponse {
        self.build_health().await
    }

    /// Stops ingress/timers, drains accepted work until the deadline, stops all
    /// actors, observes every owned task, and publishes `Stopped` health.
    ///
    /// # Errors
    ///
    /// Returns when a bounded runtime component cannot finish its coordinated
    /// shutdown transition.
    pub async fn drain(&self) -> Result<RuntimeHealthResponse> {
        let started = Instant::now();
        let timeout_ms = self.inner.config.drain_timeout_ms;
        let deadline = started + Duration::from_millis(timeout_ms);
        let completion_reserve_ms = (timeout_ms / 10).clamp(1, 25).min(timeout_ms);
        let work_deadline =
            started + Duration::from_millis(timeout_ms.saturating_sub(completion_reserve_ms));
        if self
            .inner
            .drain_started
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
        {
            let runtime = self.clone();
            let job = tokio::spawn(async move {
                runtime.perform_drain(work_deadline).await;
            });
            *lock_std(&self.inner.drain_job) = Some(job);
        }
        self.wait_for_stopped_until(deadline).await?;
        self.reap_drain_job().await?;
        Ok(self.inner.health.snapshot().runtime)
    }

    async fn perform_drain(&self, deadline: Instant) {
        let _completion = DrainCompletionGuard(self.inner.clone());
        self.inner.counters.stop_accepting();
        self.inner.phase.send_replace(RuntimePhase::Draining);
        self.inner.health.set_lifecycle(LifecycleState::Draining);
        self.inner.signer_lanes.close();
        let _ = self.inner.timer_tx.try_send(TimerCommand::Stop);
        // Wait for a pre-existing mutator, then release the gate while accepted
        // handlers settle. Ingress is already closed: later mutations return
        // Stopping, rather than blocking behind the very drain awaiting them.
        {
            let _operation = self.inner.operations.lock().await;
        }

        self.drain_accepted_until(deadline).await;
        if let Some(mut queue) = mutex_lock_until(&self.inner.fair_queue, deadline).await {
            queue.stop_accepting();
        }

        let _operation = self.inner.operations.lock().await;
        self.stop_accounts_until(deadline).await;
        self.stop_background_tasks_until(deadline).await;
        // Executor implementations may own a runtime handle. Break that cycle
        // only after all execution futures have been observed.
        write_std(&self.inner.executor).take();
        self.publish_health().await;
        self.inner.drain_complete.send_replace(true);
        self.inner.health.set_lifecycle(LifecycleState::Stopped);
    }

    async fn drain_accepted_until(&self, deadline: Instant) {
        loop {
            let Some(accounts) = rw_read_until(&self.inner.accounts, deadline).await else {
                break;
            };
            let mailbox_empty = accounts
                .values()
                .all(|account| account.endpoint.mailbox_depth() == 0);
            drop(accounts);
            let Some(queue) = mutex_lock_until(&self.inner.fair_queue, deadline).await else {
                break;
            };
            let queue_empty = queue.is_empty();
            drop(queue);
            if mailbox_empty
                && queue_empty
                && self.inner.signer_lanes.is_drained()
                && self.inner.execution.active.load(Ordering::Acquire) == 0
            {
                break;
            }
            if Instant::now() >= deadline {
                break;
            }
            self.inner.dispatcher_notify.notify_one();
            for notify in &self.inner.signer_notifies {
                notify.notify_one();
            }
            time::sleep(Duration::from_millis(1)).await;
        }
    }

    async fn stop_accounts_until(&self, deadline: Instant) {
        let accounts = {
            let mut registry = self.inner.accounts.write().await;
            std::mem::take(&mut *registry)
        };
        for account in accounts.values() {
            account.endpoint.stop();
        }
        for (_, account) in accounts {
            observe_account_until(account, deadline, &self.inner.counters).await;
        }
        lock_std(&self.inner.reconnect_last_grant).clear();
        let remaining_queue = self.inner.fair_queue.lock().await.abort_all().len();
        let remaining_signer = self.inner.signer_lanes.abort_queued();
        self.inner
            .counters
            .add_unresolved_work(remaining_queue.saturating_add(remaining_signer));
        self.inner.counters.set_actor_count(0);
    }

    async fn stop_background_tasks_until(&self, deadline: Instant) {
        let (timer_join, dispatcher_join, signer_workers) = {
            let mut joins = lock_std(&self.inner.joins);
            (
                joins.timer.take(),
                joins.dispatcher.take(),
                std::mem::take(&mut joins.signer_workers),
            )
        };
        if let Some(mut timer) = timer_join {
            observe_runtime_task_until(&mut timer, deadline, &self.inner.counters).await;
        }
        self.inner.phase.send_replace(RuntimePhase::Stopped);
        self.inner.dispatcher_notify.notify_waiters();
        for notify in &self.inner.signer_notifies {
            notify.notify_waiters();
        }
        if let Some(mut dispatcher) = dispatcher_join {
            observe_runtime_task_until(&mut dispatcher, deadline, &self.inner.counters).await;
        }
        // A forced dispatcher abort drops its JoinSet and cancels children.
        // Keep the coordinator/data-directory owner alive until their guards
        // have actually dropped, rather than merely assuming cancellation ran.
        while self.inner.execution.active.load(Ordering::Acquire) != 0 {
            tokio::task::yield_now().await;
        }
        for mut worker in signer_workers {
            observe_runtime_task_until(&mut worker, deadline, &self.inner.counters).await;
        }
        let cleanup_job = lock_std(&self.inner.cleanup_job).take();
        if let Some(job) = cleanup_job {
            // Blocking filesystem work is not abortable. Retain ownership and
            // keep health Draining until it really finishes. The public drain
            // call remains deadline-bounded and can be retried after a timeout.
            if job.await.is_err() {
                record_storage_maintenance_failure(&self.inner);
                self.inner.counters.add_unresolved_work(1);
            }
        }
    }

    async fn wait_for_stopped_until(&self, deadline: Instant) -> Result<()> {
        let mut drain_complete = self.inner.drain_complete.subscribe();
        if *drain_complete.borrow() {
            return Ok(());
        }
        let wait = async {
            while !*drain_complete.borrow() {
                drain_complete
                    .changed()
                    .await
                    .context("runtime drain completion channel closed")?;
            }
            Ok::<(), anyhow::Error>(())
        };
        time::timeout_at(time::Instant::from_std(deadline), wait)
            .await
            .context("timed out waiting for the active runtime drain")??;
        Ok(())
    }

    async fn reap_drain_job(&self) -> Result<()> {
        let completed = lock_std(&self.inner.drain_job).take();
        if let Some(completed) = completed {
            completed
                .await
                .context("runtime drain coordinator failed")?;
        }
        Ok(())
    }

    async fn publish_account_heartbeat(&self, account_id: &AccountId) -> Result<()> {
        let (control, mailbox_depth, transport, signer, last_activity_ms) = {
            let accounts = self.inner.accounts.read().await;
            let account = accounts
                .get(account_id)
                .context("cannot publish heartbeat for an unknown account")?;
            let transport = lock_std(&account.transport_breaker).phase();
            let signer = lock_std(&account.signer_breaker).phase();
            (
                account.endpoint.control_snapshot(),
                account.endpoint.mailbox_depth(),
                transport,
                signer,
                account.last_activity_ms.load(Ordering::Acquire),
            )
        };
        self.publish_heartbeat_values(
            account_id,
            control,
            mailbox_depth,
            transport,
            signer,
            last_activity_ms,
        )
    }

    fn publish_heartbeat_values(
        &self,
        account_id: &AccountId,
        control: AccountControl,
        mailbox_depth: usize,
        transport: BreakerPhase,
        signer: BreakerPhase,
        last_activity_ms: u64,
    ) -> Result<()> {
        let signer_queue_depth = self
            .inner
            .signer_lanes
            .account_queue_depth(account_id.as_str());
        lock_std(&self.inner.heartbeat)
            .publish(AccountHeartbeatDelta {
                account_id: account_id.to_string(),
                actor_generation: control.actor_generation,
                runtime: control.state,
                mailbox_depth: u32::try_from(mailbox_depth).unwrap_or(u32::MAX),
                signer_queue_depth: u32::try_from(signer_queue_depth).unwrap_or(u32::MAX),
                last_activity_ms: (last_activity_ms > 0).then_some(last_activity_ms),
                transport: breaker_dependency_health(transport),
                signer: breaker_dependency_health(signer),
            })
            .context("cannot publish account heartbeat")?;
        Ok(())
    }

    fn rollback_heartbeat(&self, account_id: &AccountId, existing: Option<&ExistingAccountFacts>) {
        if let Some(existing) = existing {
            let _ = self.publish_heartbeat_values(
                account_id,
                existing.control,
                existing.endpoint.mailbox_depth(),
                existing.transport,
                existing.signer,
                existing.last_activity_ms,
            );
        } else {
            let _ = lock_std(&self.inner.heartbeat).remove(account_id.as_str());
        }
    }

    async fn refresh_actor_count(&self) {
        let live = self
            .inner
            .accounts
            .read()
            .await
            .values()
            .filter(|account| !account.join.is_finished())
            .count();
        self.inner.counters.set_actor_count(live);
    }

    async fn publish_health(&self) {
        let snapshot = self.build_health().await;
        self.inner.health.update_runtime_with(|runtime| {
            *runtime = snapshot;
            runtime.timer_alive = self.inner.timer_alive.load(Ordering::Acquire);
            runtime.central_timer_tasks = usize::from(runtime.timer_alive);
            runtime.dispatcher_alive = self.inner.dispatcher_alive.load(Ordering::Acquire);
            runtime.signer_workers_alive = self.inner.signer_workers_alive.load(Ordering::Acquire);
            if self.inner.drain_started.load(Ordering::Acquire)
                && !*self.inner.drain_complete.borrow()
            {
                runtime.phase = RuntimeHealthPhase::Draining;
            }
        });
    }

    fn publish_health_from_cached(&self) {
        let timer = *read_std(&self.inner.timer_snapshot);
        let signer = self.inner.signer_lanes.snapshot();
        let heartbeat = lock_std(&self.inner.heartbeat).snapshot();
        let reconnect =
            lock_std(&self.inner.reconnect_budget).snapshot(monotonic_ms(self.inner.started_at));
        let maintenance = lock_std(&self.inner.maintenance_status).clone();
        let counters = &self.inner.counters;
        self.inner.health.update_runtime_with(|runtime| {
            runtime.phase = if self.inner.drain_started.load(Ordering::Acquire)
                && !*self.inner.drain_complete.borrow()
            {
                RuntimeHealthPhase::Draining
            } else {
                runtime_health_phase(*self.inner.phase.borrow())
            };
            update_execution_health(&self.inner, runtime);
            runtime.accepting_work = counters.accepting_work();
            runtime.actor_count = counters.actor_count();
            // Read task liveness while holding the health write lock. Sampling
            // it before the closure could overwrite a newer task-guard update.
            runtime.timer_alive = self.inner.timer_alive.load(Ordering::Acquire);
            runtime.central_timer_tasks = usize::from(runtime.timer_alive);
            runtime.dispatcher_alive = self.inner.dispatcher_alive.load(Ordering::Acquire);
            runtime.signer_workers_alive = self.inner.signer_workers_alive.load(Ordering::Acquire);
            runtime.scheduled_timer_count = timer.live_entries;
            runtime.queue_capacity = self.inner.config.global_queue_capacity;
            runtime.invalid_work_rejected = counters.invalid_work_rejected();
            runtime.deferred_background = counters.deferred_background();
            runtime.signer_queue_depth = signer.queued;
            runtime.signer_queue_capacity = signer.queue_capacity;
            runtime.signer_in_flight = signer.in_flight;
            runtime.signer_concurrency = signer.lane_count;
            runtime.signer_admitted = signer.admitted;
            runtime.signer_rejected = signer
                .rejected_full
                .saturating_add(signer.rejected_stale)
                .saturating_add(signer.rejected_closed)
                .saturating_add(signer.rejected_invalid);
            runtime.signer_completed = signer.completed;
            runtime.signer_failed = signer.failed;
            runtime.signer_cancelled = signer.cancelled;
            runtime.signer_max_queued = signer.max_queued;
            runtime.signer_max_in_flight = signer.max_in_flight;
            runtime.signer_lanes = signer_lane_health(&signer);
            runtime.reconnect_available_tokens = reconnect.available_whole_tokens;
            runtime.reconnect_throttled = reconnect.throttled_total;
            runtime.heartbeat_sequence = heartbeat.next_sequence.saturating_sub(1);
            runtime.heartbeat_batches = counters.heartbeat_batches();
            runtime.heartbeat_last_batch_accounts = heartbeat.last_batch_accounts;
            runtime.heartbeat_dirty_accounts = heartbeat.dirty_accounts;
            runtime.storage_cleanup_runs = counters.storage_cleanup_runs();
            runtime.maintenance = maintenance_health(
                &maintenance,
                self.inner.cleanup_in_flight.load(Ordering::Acquire),
            );
            runtime.unresolved_work = counters.unresolved_work();
        });
    }

    async fn account_counts(&self) -> (usize, BreakerPhaseCounts) {
        let accounts = self.inner.accounts.read().await;
        let actor_count = accounts
            .values()
            .filter(|account| !account.join.is_finished())
            .count();
        let mut phases = BreakerPhaseCounts::default();
        for phase in accounts.values().flat_map(|account| {
            [
                lock_std(&account.transport_breaker).phase(),
                lock_std(&account.signer_breaker).phase(),
            ]
        }) {
            match phase {
                BreakerPhase::Closed => phases.closed = phases.closed.saturating_add(1),
                BreakerPhase::Open => phases.open = phases.open.saturating_add(1),
                BreakerPhase::HalfOpen => {
                    phases.half_open = phases.half_open.saturating_add(1);
                }
            }
        }
        (actor_count, phases)
    }

    async fn build_health(&self) -> RuntimeHealthResponse {
        let queue = self.inner.fair_queue.lock().await.snapshot();
        let timer = *read_std(&self.inner.timer_snapshot);
        let signer = self.inner.signer_lanes.snapshot();
        let heartbeat = lock_std(&self.inner.heartbeat).snapshot();
        let reconnect =
            lock_std(&self.inner.reconnect_budget).snapshot(monotonic_ms(self.inner.started_at));
        let (actor_count, breaker_phase_counts) = self.account_counts().await;
        let counters = &self.inner.counters;
        counters.set_actor_count(actor_count);
        let maintenance = lock_std(&self.inner.maintenance_status).clone();
        let timer_alive = self.inner.timer_alive.load(Ordering::Acquire);
        RuntimeHealthResponse {
            phase: if self.inner.drain_started.load(Ordering::Acquire)
                && !*self.inner.drain_complete.borrow()
            {
                RuntimeHealthPhase::Draining
            } else {
                runtime_health_phase(*self.inner.phase.borrow())
            },
            accepting_work: counters.accepting_work(),
            actor_count,
            central_timer_tasks: usize::from(timer_alive),
            timer_alive,
            dispatcher_alive: self.inner.dispatcher_alive.load(Ordering::Acquire),
            signer_workers_alive: self.inner.signer_workers_alive.load(Ordering::Acquire),
            scheduled_timer_count: timer.live_entries,
            queue_depth: queue.depth,
            queue_capacity: queue.capacity,
            queue_high_water: queue.peak_depth,
            queue_rejected: queue
                .rejected_full
                .saturating_add(queue.rejected_stopping)
                .saturating_add(queue.rejected_invalid)
                .saturating_add(counters.mailbox_rejected()),
            queue_coalesced: queue.coalesced,
            stale_work_rejected: counters.stale_work_rejected(),
            invalid_work_rejected: counters
                .invalid_work_rejected()
                .saturating_add(queue.rejected_invalid),
            deferred_background: counters.deferred_background(),
            dispatched_total: counters.dispatched_total(),
            execution_mode: if read_std(&self.inner.executor).is_some() {
                crate::health::ExecutorMode::Attached
            } else {
                crate::health::ExecutorMode::Shadow
            },
            execution_capacity: read_std(&self.inner.executor)
                .as_ref()
                .map_or(0, |b| b.config.max_in_flight),
            execution_active: self.inner.execution.active.load(Ordering::Acquire),
            execution_completed: self.inner.execution.completed.load(Ordering::Acquire),
            execution_recovery_needed: self.inner.execution.recovery_needed.load(Ordering::Acquire),
            max_fairness_lag_ms: max_fairness_lag(counters, &queue),
            work_classes: work_class_health(&queue.classes),
            signer_queue_depth: signer.queued,
            signer_queue_capacity: signer.queue_capacity,
            signer_in_flight: signer.in_flight,
            signer_concurrency: signer.lane_count,
            signer_admitted: signer.admitted,
            signer_rejected: signer
                .rejected_full
                .saturating_add(signer.rejected_stale)
                .saturating_add(signer.rejected_closed)
                .saturating_add(signer.rejected_invalid),
            signer_completed: signer.completed,
            signer_failed: signer.failed,
            signer_cancelled: signer.cancelled,
            signer_max_queued: signer.max_queued,
            signer_max_in_flight: signer.max_in_flight,
            signer_lanes: signer_lane_health(&signer),
            open_circuit_count: breaker_phase_counts
                .open
                .saturating_add(breaker_phase_counts.half_open),
            breaker_phase_counts,
            reconnect_available_tokens: reconnect.available_whole_tokens,
            reconnect_throttled: reconnect.throttled_total,
            heartbeat_sequence: heartbeat.next_sequence.saturating_sub(1),
            heartbeat_batches: counters.heartbeat_batches(),
            heartbeat_last_batch_accounts: heartbeat.last_batch_accounts,
            heartbeat_dirty_accounts: heartbeat.dirty_accounts,
            storage_cleanup_runs: counters.storage_cleanup_runs(),
            maintenance: maintenance_health(
                &maintenance,
                self.inner.cleanup_in_flight.load(Ordering::Acquire),
            ),
            unresolved_work: counters.unresolved_work(),
        }
    }
}

const fn runtime_health_phase(phase: RuntimePhase) -> RuntimeHealthPhase {
    match phase {
        RuntimePhase::Running => RuntimeHealthPhase::Running,
        RuntimePhase::Draining => RuntimeHealthPhase::Draining,
        RuntimePhase::Stopped => RuntimeHealthPhase::Stopped,
    }
}

const fn work_class_name(class: WorkClass) -> &'static str {
    match class {
        WorkClass::Manual => "manual",
        WorkClass::Automatic => "automatic",
        WorkClass::Background => "background",
    }
}

fn signer_lane_health(snapshot: &SignerLanesSnapshot) -> Vec<SignerLaneHealthResponse> {
    snapshot
        .lanes
        .iter()
        .map(|lane| SignerLaneHealthResponse {
            lane: lane.lane,
            queued: lane.queued,
            in_flight: lane.in_flight,
            queued_accounts: lane.queued_accounts,
        })
        .collect()
}

fn work_class_health(classes: &[WorkClassQueueSnapshot; 3]) -> Vec<WorkClassHealthResponse> {
    classes
        .iter()
        .map(|class| WorkClassHealthResponse {
            class: work_class_name(class.class).to_owned(),
            depth: class.depth,
            capacity: class.capacity,
            rejected: class.rejected_full.saturating_add(class.rejected_invalid),
            dispatched: class.dispatched,
            last_dispatch_lag_ms: class.last_dispatch_lag_ms,
            max_dispatch_lag_ms: class.max_dispatch_lag_ms,
        })
        .collect()
}

fn max_fairness_lag(counters: &RuntimeCounters, queue: &FairQueueSnapshot) -> u64 {
    counters.max_fairness_lag_ms().max(
        queue
            .classes
            .iter()
            .map(|class| class.max_dispatch_lag_ms)
            .max()
            .unwrap_or_default(),
    )
}

fn maintenance_health(status: &MaintenanceStatus, in_flight: bool) -> MaintenanceHealthResponse {
    MaintenanceHealthResponse {
        in_flight,
        last_success_ms: status.last_success_ms,
        failures: status.failures,
        last_error: status
            .last_error
            .as_ref()
            .map(|_| MaintenanceHealthError::StorageMaintenanceFailed),
    }
}

fn class_capacities(global: usize) -> WorkClassCapacities {
    let background = (global / 8).max(1);
    let automatic = (global.saturating_mul(3) / 8).max(1);
    let manual = global
        .saturating_sub(background)
        .saturating_sub(automatic)
        .max(1);
    WorkClassCapacities {
        manual,
        automatic,
        background,
    }
}

fn new_breaker(
    account_id: &AccountId,
    kind: DependencyKind,
) -> Result<Arc<StdMutex<CircuitBreaker>>> {
    let breaker = CircuitBreaker::new(
        format!("{}:{kind:?}", account_id.as_str()),
        BreakerConfig {
            failure_threshold: 1,
            base_backoff_ms: 2_000,
            max_backoff_ms: 60_000,
            jitter_basis_points: 2_000,
            half_open_max_probes: 1,
        },
    )
    .context("invalid account dependency breaker")?;
    Ok(Arc::new(StdMutex::new(breaker)))
}

fn dependency_breaker(
    account: &ManagedAccount,
    kind: DependencyKind,
) -> &Arc<StdMutex<CircuitBreaker>> {
    match kind {
        DependencyKind::Transport => &account.transport_breaker,
        DependencyKind::Signer => &account.signer_breaker,
    }
}

const fn control_regresses(current: AccountControl, next: AccountControl) -> bool {
    next.actor_generation < current.actor_generation
        || next.state.credential_generation < current.state.credential_generation
        || next.state.lease_epoch < current.state.lease_epoch
}

const fn control_fence_changed(current: AccountControl, next: AccountControl) -> bool {
    current.actor_generation != next.actor_generation
        || current.state.credential_generation != next.state.credential_generation
        || current.state.lease_epoch != next.state.lease_epoch
}

fn signer_scope(spec: &AccountSpec) -> SignerScope {
    signer_scope_from_control(&spec.account_id, spec.control())
}

fn signer_scope_from_control(account_id: &AccountId, control: AccountControl) -> SignerScope {
    SignerScope {
        account_id: account_id.to_string(),
        actor_generation: control.actor_generation,
        credential_generation: control.state.credential_generation,
        lease_epoch: control.state.lease_epoch,
        allowed_kinds: SIGNER_WORK_KINDS.map(|kind| control.allows(kind)),
    }
}

fn control_allows_signer(control: AccountControl, job: &SignerJob) -> bool {
    WorkFence {
        actor_generation: job.actor_generation,
        credential_generation: job.credential_generation,
        lease_epoch: job.lease_epoch,
    }
    .matches(
        control.actor_generation,
        control.state.credential_generation,
        control.state.lease_epoch,
    ) && control.allows(job.kind)
}

const fn breaker_dependency_health(phase: BreakerPhase) -> DependencyHealth {
    match phase {
        BreakerPhase::Closed => DependencyHealth::Closed,
        BreakerPhase::Open => DependencyHealth::Open,
        BreakerPhase::HalfOpen => DependencyHealth::HalfOpen,
    }
}

fn reconnect_fairness_interval_ms(active_accounts: usize, rate_per_second: u32) -> u64 {
    let active = u64::try_from(active_accounts.max(1)).unwrap_or(u64::MAX / 1_000);
    let rate = u64::from(rate_per_second.max(1));
    active.saturating_mul(1_000).div_ceil(rate).max(1)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum TaskJoinStatus {
    Completed,
    Failed,
    TimedOut,
}

async fn join_until<T>(join: &mut JoinHandle<T>, deadline: Instant) -> TaskJoinStatus {
    if join.is_finished() {
        return match join.await {
            Ok(_) => TaskJoinStatus::Completed,
            Err(_) => TaskJoinStatus::Failed,
        };
    }
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return TaskJoinStatus::TimedOut;
    }
    match time::timeout(remaining, join).await {
        Ok(Ok(_)) => TaskJoinStatus::Completed,
        Ok(Err(_)) => TaskJoinStatus::Failed,
        Err(_) => TaskJoinStatus::TimedOut,
    }
}

async fn observe_runtime_task_until<T>(
    join: &mut JoinHandle<T>,
    deadline: Instant,
    counters: &RuntimeCounters,
) {
    match join_until(join, deadline).await {
        TaskJoinStatus::Completed => {}
        TaskJoinStatus::Failed => counters.add_unresolved_work(1),
        TaskJoinStatus::TimedOut => {
            join.abort();
            let _ = join.await;
            counters.add_unresolved_work(1);
        }
    }
}

async fn mutex_lock_until<T>(
    mutex: &Mutex<T>,
    deadline: Instant,
) -> Option<tokio::sync::MutexGuard<'_, T>> {
    if let Ok(guard) = mutex.try_lock() {
        return Some(guard);
    }
    if Instant::now() >= deadline {
        return None;
    }
    time::timeout_at(time::Instant::from_std(deadline), mutex.lock())
        .await
        .ok()
}

async fn rw_read_until<T>(
    lock: &RwLock<T>,
    deadline: Instant,
) -> Option<tokio::sync::RwLockReadGuard<'_, T>> {
    if let Ok(guard) = lock.try_read() {
        return Some(guard);
    }
    if Instant::now() >= deadline {
        return None;
    }
    time::timeout_at(time::Instant::from_std(deadline), lock.read())
        .await
        .ok()
}

async fn observe_account_until(
    account: ManagedAccount,
    deadline: Instant,
    counters: &RuntimeCounters,
) {
    let buffered = account.endpoint.mailbox_depth();
    let mut join = account.join;
    match join_until(&mut join, deadline).await {
        TaskJoinStatus::Completed => {}
        TaskJoinStatus::Failed => {
            counters.add_unresolved_work(buffered.saturating_add(1));
        }
        TaskJoinStatus::TimedOut => {
            join.abort();
            let _ = join.await;
            counters.add_unresolved_work(buffered.saturating_add(1));
        }
    }
}

async fn run_dispatcher(inner: Arc<RuntimeInner>) {
    let _live = RuntimeTaskGuard::new(inner.clone(), RuntimeTaskKind::Dispatcher);
    let mut phase = inner.phase.subscribe();
    let mut jobs = JoinSet::new();
    let mut owners = HashMap::new();
    let mut busy = HashSet::new();
    loop {
        let binding = read_std(&inner.executor).clone();
        let capacity = binding
            .as_ref()
            .map_or(usize::MAX, |b| b.config.max_in_flight);
        let now_ms = monotonic_ms(inner.started_at);
        let work = if jobs.len() < capacity {
            inner.fair_queue.lock().await.pop_excluding(now_ms, &busy)
        } else {
            None
        };
        if let Some(work) = work {
            let current = {
                let accounts = inner.accounts.read().await;
                accounts.get(&work.account_id).map(|account| {
                    let control = account.endpoint.control_snapshot();
                    if control.matches(&work) && control.allows(work.kind) {
                        account
                            .last_activity_ms
                            .store(unix_epoch_ms(), Ordering::Release);
                    }
                    (control, account.endpoint.subscribe_control())
                })
            };
            if let Some((_, receiver)) =
                current.filter(|(c, _)| c.matches(&work) && c.allows(work.kind))
            {
                inner
                    .counters
                    .note_dispatch(now_ms.saturating_sub(work.enqueued_at_ms));
                if let Some(binding) = binding {
                    let account = work.account_id.clone();
                    let guard =
                        ExecutionGuard::new(inner.execution.clone(), inner.counters.clone());
                    busy.insert(account.clone());
                    let task = jobs.spawn(execute_guarded(
                        binding.executor,
                        work,
                        receiver,
                        binding.config.timeout,
                        guard,
                    ));
                    owners.insert(task.id(), account);
                }
            } else {
                inner.counters.note_stale_work();
                inner.counters.add_unresolved_work(1);
            }
            continue;
        }
        if *phase.borrow() == RuntimePhase::Stopped {
            jobs.abort_all();
            break;
        }
        tokio::select! {
            biased;
            changed = phase.changed() => {
                if changed.is_err() || *phase.borrow() == RuntimePhase::Stopped { jobs.abort_all(); break; }
            }
            result = jobs.join_next_with_id(), if !jobs.is_empty() => {
                if let Some(result) = result { finish_execution(result, &mut owners, &mut busy); }
            }
            () = inner.dispatcher_notify.notified() => {}
        }
    }
    while let Some(result) = jobs.join_next_with_id().await {
        finish_execution(result, &mut owners, &mut busy);
    }
}

fn finish_execution(
    result: Result<(tokio::task::Id, ()), tokio::task::JoinError>,
    owners: &mut HashMap<tokio::task::Id, AccountId>,
    busy: &mut HashSet<AccountId>,
) {
    let id = match result {
        Ok((id, ())) => id,
        Err(error) => {
            tracing::error!(
                "business execution task aborted or panicked; durable recovery retained"
            );
            error.id()
        }
    };
    if let Some(account) = owners.remove(&id) {
        busy.remove(&account);
    }
}

async fn run_signer_lane(inner: Arc<RuntimeInner>, lane: usize, notify: Arc<Notify>) {
    let _live = RuntimeTaskGuard::new(inner.clone(), RuntimeTaskKind::Signer);
    let mut phase = inner.phase.subscribe();
    loop {
        while let Some(permit) = inner.signer_lanes.try_acquire_lane(lane) {
            let account_id = AccountId::new(permit.job().account_id.clone()).ok();
            if permit.is_current() {
                // Yield while holding the fixed lane permit so concurrency and
                // cancellation behavior are exercised without protocol I/O.
                tokio::task::yield_now().await;
                let current = if let Some(account_id) = &account_id {
                    inner
                        .accounts
                        .read()
                        .await
                        .get(account_id)
                        .map(|account| account.endpoint.control_snapshot())
                } else {
                    None
                };
                if permit.is_current()
                    && current.is_some_and(|control| control_allows_signer(control, permit.job()))
                {
                    if let Some(account_id) = &account_id {
                        if let Some(account) = inner.accounts.read().await.get(account_id) {
                            account
                                .last_activity_ms
                                .store(unix_epoch_ms(), Ordering::Release);
                        }
                    }
                    permit.finish(SignerCompletion::Success);
                } else {
                    inner.counters.add_unresolved_work(1);
                    drop(permit);
                }
            } else {
                inner.counters.add_unresolved_work(1);
                drop(permit);
            }
        }

        if *phase.borrow() == RuntimePhase::Stopped {
            break;
        }
        tokio::select! {
            biased;
            changed = phase.changed() => {
                if changed.is_err() || *phase.borrow() == RuntimePhase::Stopped {
                    break;
                }
            }
            () = notify.notified() => {}
        }
    }
}

async fn run_timer(inner: Arc<RuntimeInner>, mut commands: mpsc::Receiver<TimerCommand>) {
    let _live = RuntimeTaskGuard::new(inner.clone(), RuntimeTaskKind::Timer);
    let mut timer = CentralTimer::new();
    let now_ms = monotonic_ms(inner.started_at);
    schedule_periodic(
        &mut timer,
        TimerKey::Heartbeat,
        now_ms,
        inner.config.heartbeat_interval_ms,
    );
    schedule_periodic(
        &mut timer,
        TimerKey::StorageCleanup,
        now_ms,
        inner.config.storage_cleanup_interval_ms,
    );
    schedule_periodic(
        &mut timer,
        TimerKey::HealthRefresh,
        now_ms,
        HEALTH_REFRESH_MS,
    );
    update_timer_snapshot(&inner, timer.snapshot());

    loop {
        let now_ms = monotonic_ms(inner.started_at);
        let next_deadline = timer.next_deadline_ms();
        if next_deadline.is_some_and(|deadline_ms| deadline_ms <= now_ms) {
            process_due_timers(&inner, &mut timer).await;
            update_timer_snapshot(&inner, timer.snapshot());
            continue;
        }
        let command = if let Some(deadline_ms) = next_deadline {
            let delay = Duration::from_millis(deadline_ms.saturating_sub(now_ms));
            tokio::select! {
                biased;
                command = commands.recv() => command,
                () = time::sleep(delay) => {
                    process_due_timers(&inner, &mut timer).await;
                    update_timer_snapshot(&inner, timer.snapshot());
                    continue;
                }
            }
        } else {
            commands.recv().await
        };

        match command {
            Some(TimerCommand::UpsertAccount(account_id)) => {
                schedule_account_timers(&mut timer, &account_id, monotonic_ms(inner.started_at));
                if read_std(&inner.executor).is_some() {
                    schedule_receive_timer(
                        &mut timer,
                        &account_id,
                        monotonic_ms(inner.started_at),
                        crate::state::InboundState::Disconnected,
                    );
                }
            }
            Some(TimerCommand::RemoveAccount(account_id)) => {
                for kind in account_timer_kinds() {
                    timer.cancel(&TimerKey::Account {
                        account_id: account_id.clone(),
                        kind,
                    });
                }
            }
            Some(TimerCommand::Stop) | None => {
                timer.clear();
                update_timer_snapshot(&inner, timer.snapshot());
                break;
            }
        }
        update_timer_snapshot(&inner, timer.snapshot());
    }
}

fn schedule_receive_timer(
    timer: &mut CentralTimer<TimerKey>,
    id: &AccountId,
    now: u64,
    state: crate::state::InboundState,
) {
    let period = match state {
        crate::state::InboundState::WsHealthy => RECONCILE_HEALTHY_MS,
        crate::state::InboundState::Backoff => 60_000,
        _ => 15_000,
    };
    let period = stable_jittered_delay_ms(
        period,
        id.as_str(),
        "native-reconcile",
        ACCOUNT_TIMER_JITTER_BASIS_POINTS,
    );
    schedule_periodic(
        timer,
        TimerKey::Account {
            account_id: id.clone(),
            kind: WorkKind::Reconcile,
        },
        now,
        period,
    );
}

fn schedule_account_timers(
    timer: &mut CentralTimer<TimerKey>,
    account_id: &AccountId,
    now_ms: u64,
) {
    for (kind, base_period_ms) in [
        (WorkKind::Reconcile, RECONCILE_HEALTHY_MS),
        (WorkKind::PendingRecovery, PENDING_RECOVERY_MS),
        (WorkKind::KeepaliveLease, KEEPALIVE_MS),
    ] {
        let purpose = format!("{kind:?}");
        let period_ms = stable_jittered_delay_ms(
            base_period_ms,
            account_id.as_str(),
            &purpose,
            ACCOUNT_TIMER_JITTER_BASIS_POINTS,
        )
        .max(1);
        schedule_periodic(
            timer,
            TimerKey::Account {
                account_id: account_id.clone(),
                kind,
            },
            now_ms,
            period_ms,
        );
    }
}

fn schedule_periodic(
    timer: &mut CentralTimer<TimerKey>,
    key: TimerKey,
    now_ms: u64,
    period_ms: u64,
) {
    let period = NonZeroU64::new(period_ms).expect("validated runtime periods are nonzero");
    timer.schedule_periodic(key, now_ms.saturating_add(period_ms), period);
}

async fn process_due_timers(inner: &Arc<RuntimeInner>, timer: &mut CentralTimer<TimerKey>) {
    let now_ms = monotonic_ms(inner.started_at);
    for due in timer.drain_due_limited(now_ms, MAX_DUE_PER_TURN) {
        match due.key {
            TimerKey::Heartbeat => flush_heartbeat(inner, now_ms).await,
            TimerKey::StorageCleanup => start_storage_cleanup(inner).await,
            TimerKey::HealthRefresh => {
                inner.control_ticks.send_replace(now_ms);
                RuntimeHandle {
                    inner: inner.clone(),
                }
                .publish_health()
                .await;
            }
            TimerKey::Account { account_id, kind } => {
                if inner.background_work_paused.load(Ordering::Acquire)
                    && kind.is_disposable_background()
                {
                    inner.counters.note_background_deferred();
                    continue;
                }
                let endpoint = {
                    let accounts = inner.accounts.read().await;
                    accounts
                        .get(&account_id)
                        .map(|account| account.endpoint.clone())
                };
                if let Some(endpoint) = endpoint {
                    let control = endpoint.control_snapshot();
                    if kind == WorkKind::Reconcile && read_std(&inner.executor).is_some() {
                        schedule_receive_timer(timer, &account_id, now_ms, control.state.inbound);
                    }
                    let work = WorkEnvelope::new(
                        account_id,
                        format!("timer-{kind:?}-{}", due.generation.get()),
                        kind,
                        WorkFence {
                            actor_generation: control.actor_generation,
                            credential_generation: control.state.credential_generation,
                            lease_epoch: control.state.lease_epoch,
                        },
                        now_ms,
                    );
                    let _ = endpoint.try_enqueue_detached(work);
                }
            }
        }
    }
}

async fn flush_heartbeat(inner: &Arc<RuntimeInner>, now_ms: u64) {
    let handle = RuntimeHandle {
        inner: inner.clone(),
    };
    let accounts = inner.accounts.read().await;
    for (account_id, account) in accounts.iter() {
        let _ = handle.publish_heartbeat_values(
            account_id,
            account.endpoint.control_snapshot(),
            account.endpoint.mailbox_depth(),
            lock_std(&account.transport_breaker).phase(),
            lock_std(&account.signer_breaker).phase(),
            account.last_activity_ms.load(Ordering::Acquire),
        );
    }
    drop(accounts);
    let mut heartbeat = lock_std(&inner.heartbeat);
    if let Ok(batches) = heartbeat.prepare(now_ms) {
        inner.counters.note_heartbeat_batches(batches.len());
        for batch in batches {
            // Step 7 will replace this successful no-op ACK with the hosted
            // authenticated aggregate heartbeat sink.
            let _ = heartbeat.acknowledge(batch.sequence, true);
        }
    }
}

async fn start_storage_cleanup(inner: &Arc<RuntimeInner>) {
    let completed = {
        let mut slot = lock_std(&inner.cleanup_job);
        if slot.as_ref().is_some_and(JoinHandle::is_finished) {
            slot.take()
        } else {
            None
        }
    };
    if let Some(completed) = completed {
        if let Err(error) = completed.await {
            tracing::error!(%error, "runtime storage cleanup task failed");
            inner.cleanup_in_flight.store(false, Ordering::Release);
            record_storage_maintenance_failure(inner);
        }
    }
    if inner
        .cleanup_in_flight
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return;
    }
    {
        let slot = lock_std(&inner.cleanup_job);
        if slot.as_ref().is_some_and(|job| !job.is_finished()) {
            inner.cleanup_in_flight.store(false, Ordering::Release);
            return;
        }
        debug_assert!(slot.is_none(), "finished cleanup handle must be reaped");
    }
    let inner_for_job = inner.clone();
    let job = tokio::spawn(async move {
        let _in_flight = CleanupInFlightGuard(inner_for_job.clone());
        match inner_for_job.maintenance.run_once().await {
            Ok(storage) => {
                inner_for_job.counters.note_storage_cleanup();
                inner_for_job
                    .background_work_paused
                    .store(storage.background_work_paused, Ordering::Release);
                inner_for_job
                    .disposable_writes_allowed
                    .store(storage.disposable_writes_allowed, Ordering::Release);
                inner_for_job.health.update_storage(storage);
                inner_for_job.health.set_storage_maintenance_failed(false);
                let mut status = lock_std(&inner_for_job.maintenance_status);
                status.last_success_ms = Some(monotonic_ms(inner_for_job.started_at));
                status.last_error = None;
            }
            Err(error) => {
                tracing::error!(%error, "runtime storage maintenance failed");
                record_storage_maintenance_failure(&inner_for_job);
            }
        }
    });
    *lock_std(&inner.cleanup_job) = Some(job);
}

struct CleanupInFlightGuard(Arc<RuntimeInner>);

impl Drop for CleanupInFlightGuard {
    fn drop(&mut self) {
        self.0.cleanup_in_flight.store(false, Ordering::Release);
        RuntimeHandle {
            inner: self.0.clone(),
        }
        .publish_health_from_cached();
    }
}

fn record_storage_maintenance_failure(inner: &RuntimeInner) {
    inner.background_work_paused.store(true, Ordering::Release);
    inner
        .disposable_writes_allowed
        .store(false, Ordering::Release);
    inner.health.set_storage_maintenance_failed(true);
    let mut status = lock_std(&inner.maintenance_status);
    status.failures = status.failures.saturating_add(1);
    status.last_error = Some("storage_maintenance_failed".to_owned());
}

fn update_timer_snapshot(inner: &RuntimeInner, snapshot: TimerSnapshot) {
    *write_std(&inner.timer_snapshot) = snapshot;
    inner.health.update_runtime_with(|runtime| {
        runtime.scheduled_timer_count = snapshot.live_entries;
        runtime.timer_alive = inner.timer_alive.load(Ordering::Acquire);
        runtime.central_timer_tasks = usize::from(runtime.timer_alive);
    });
}

fn publish_task_liveness(inner: &RuntimeInner) {
    inner.health.update_runtime_with(|runtime| {
        runtime.timer_alive = inner.timer_alive.load(Ordering::Acquire);
        runtime.central_timer_tasks = usize::from(runtime.timer_alive);
        runtime.dispatcher_alive = inner.dispatcher_alive.load(Ordering::Acquire);
        runtime.signer_workers_alive = inner.signer_workers_alive.load(Ordering::Acquire);
        runtime.signer_concurrency = inner.config.signer_lanes;
    });
}

fn account_timer_kinds() -> [WorkKind; 3] {
    [
        WorkKind::Reconcile,
        WorkKind::PendingRecovery,
        WorkKind::KeepaliveLease,
    ]
}

fn monotonic_ms(started_at: Instant) -> u64 {
    u64::try_from(started_at.elapsed().as_millis()).unwrap_or(u64::MAX)
}

fn unix_epoch_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok())
        .unwrap_or_default()
}

fn lock_std<T>(mutex: &StdMutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn read_std<T>(lock: &StdRwLock<T>) -> std::sync::RwLockReadGuard<'_, T> {
    lock.read()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn write_std<T>(lock: &StdRwLock<T>) -> std::sync::RwLockWriteGuard<'_, T> {
    lock.write()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn update_execution_health(inner: &RuntimeInner, health: &mut RuntimeHealthResponse) {
    let binding = read_std(&inner.executor);
    health.execution_mode = if binding.is_some() {
        crate::health::ExecutorMode::Attached
    } else {
        crate::health::ExecutorMode::Shadow
    };
    health.execution_capacity = binding.as_ref().map_or(0, |b| b.config.max_in_flight);
    health.execution_active = inner.execution.active.load(Ordering::Acquire);
    health.execution_completed = inner.execution.completed.load(Ordering::Acquire);
    health.execution_recovery_needed = inner.execution.recovery_needed.load(Ordering::Acquire);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        health::{HealthResponse, StorageHealthResponse, StorageStartupSnapshot},
        protocol::fixtures::verify_embedded_corpus,
        state::{InboundState, OwnershipState, SendCapability},
        storage::{retention::DiskPressure, RecoveryReport, SegmentCatalog, ZstdCodec},
    };
    use uuid::Uuid;
    include!("executor_tests.rs");

    fn healthy_storage() -> StorageHealthResponse {
        StorageHealthResponse::startup(&StorageStartupSnapshot {
            pressure: DiskPressure::Normal,
            disposable_writes_allowed: true,
            background_work_paused: false,
            sealed_segment_count: 0,
            sealed_segment_bytes: 0,
            active_segment_count: 0,
            cleanup_deleted_segments: 0,
            recovery: RecoveryReport::default(),
        })
    }

    fn account_spec(index: usize) -> AccountSpec {
        AccountSpec {
            account_id: AccountId::new(format!("account-{index:03}")).unwrap(),
            actor_generation: 1,
            state: AccountRuntimeState {
                lifecycle: LifecycleState::Running,
                ownership: OwnershipState::Owned,
                inbound: InboundState::WsHealthy,
                send: SendCapability::Sendable,
                credential_generation: 1,
                lease_epoch: 1,
            },
        }
    }

    fn runtime() -> (tempfile::TempDir, RuntimeHandle) {
        runtime_with_config(RuntimeConfig::recommended())
    }

    fn runtime_with_config(runtime_config: RuntimeConfig) -> (tempfile::TempDir, RuntimeHandle) {
        let directory = tempfile::tempdir().expect("temporary runtime directory");
        let core = Arc::new(CoreStore::open(directory.path()).expect("core store"));
        let catalog: Arc<dyn SegmentCatalog> = core.clone();
        let storage_config = StorageConfig::recommended();
        let segments = SegmentStore::open_with_codec(
            directory.path().join("segments"),
            storage_config.segment_policies.clone(),
            catalog,
            Arc::new(ZstdCodec::default()),
        )
        .expect("segment store");
        let parity = verify_embedded_corpus().expect("protocol corpus");
        let health = HealthHandle::new(HealthResponse::foundation(
            Uuid::new_v4(),
            Uuid::new_v4(),
            &parity,
            healthy_storage(),
        ));
        let handle = RuntimeHandle::start(
            runtime_config,
            "installation-test",
            core,
            segments,
            storage_config,
            health,
        )
        .expect("runtime starts");
        (directory, handle)
    }

    #[tokio::test]
    async fn ownership_updates_preserve_platform_facts_and_timer_stop_notifies_controller() {
        let (_directory, runtime) = runtime();
        let mut spec = account_spec(0);
        spec.state.send = SendCapability::RiskControlled;
        spec.state.inbound = InboundState::HttpDegraded;
        runtime.upsert_account(spec.clone()).await.unwrap();
        let next_epoch = spec.state.lease_epoch + 1;
        assert_eq!(
            runtime
                .update_account_ownership(&spec.account_id, next_epoch, OwnershipState::Owned)
                .await
                .unwrap(),
            AdmissionResult::Accepted
        );
        let control = runtime
            .inner
            .accounts
            .read()
            .await
            .get(&spec.account_id)
            .unwrap()
            .endpoint
            .control_snapshot();
        assert_eq!(control.state.send, SendCapability::RiskControlled);
        assert_eq!(control.state.inbound, InboundState::HttpDegraded);
        assert_eq!(
            control.state.credential_generation,
            spec.state.credential_generation
        );
        assert_eq!(
            runtime
                .update_account_ownership(&spec.account_id, next_epoch - 1, OwnershipState::Lost)
                .await
                .unwrap(),
            AdmissionResult::Stale
        );
        let ticks = runtime.subscribe_control_ticks();
        runtime.drain().await.unwrap();
        assert_eq!(*ticks.borrow(), u64::MAX);
    }

    #[tokio::test]
    async fn dependency_attempts_are_bounded_and_drop_restores_capacity() {
        let mut config = RuntimeConfig::recommended();
        config.per_account_queue_capacity = 2;
        let (_directory, runtime) = runtime_with_config(config);
        let spec = account_spec(0);
        runtime.upsert_account(spec.clone()).await.unwrap();
        let first = runtime
            .try_dependency(&spec.account_id, DependencyKind::Signer)
            .await;
        let second = runtime
            .try_dependency(&spec.account_id, DependencyKind::Signer)
            .await;
        assert!(matches!(first, DependencyAdmission::Allowed(_)));
        assert!(matches!(second, DependencyAdmission::Allowed(_)));
        assert!(matches!(
            runtime
                .try_dependency(&spec.account_id, DependencyKind::Signer)
                .await,
            DependencyAdmission::DependencyInFlightLimit
        ));
        drop(first);
        assert!(matches!(
            runtime
                .try_dependency(&spec.account_id, DependencyKind::Signer)
                .await,
            DependencyAdmission::Allowed(_)
        ));
        drop(second);
        runtime.drain().await.unwrap();
    }

    #[tokio::test]
    async fn old_dependency_result_cannot_touch_identically_reimported_account() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(0);
        runtime.upsert_account(spec.clone()).await.unwrap();
        let DependencyAdmission::Allowed(permit) = runtime
            .try_dependency(&spec.account_id, DependencyKind::Signer)
            .await
        else {
            panic!("expected admitted dependency");
        };
        runtime.remove_account(&spec.account_id).await.unwrap();
        runtime.upsert_account(spec.clone()).await.unwrap();
        assert_eq!(
            runtime
                .record_dependency_outcome(permit, DependencyOutcome::TransientFailure)
                .await,
            Some(BreakerRecord::IgnoredStale)
        );
        let accounts = runtime.inner.accounts.read().await;
        let account = accounts.get(&spec.account_id).unwrap();
        assert_eq!(account.last_activity_ms.load(Ordering::Acquire), 0);
        assert_eq!(
            lock_std(&account.signer_breaker).phase(),
            BreakerPhase::Closed
        );
        drop(accounts);
        runtime.drain().await.unwrap();
    }

    #[tokio::test]
    async fn full_timer_channel_keeps_failed_removal_atomic_and_retryable() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(0);
        runtime.upsert_account(spec.clone()).await.unwrap();
        // Reserve every channel slot without sending commands. The timer
        // cannot consume these reservations, so Full is deterministic.
        let mut reservations = Vec::new();
        while let Ok(permit) = runtime.inner.timer_tx.try_reserve() {
            reservations.push(permit);
        }
        assert!(runtime.remove_account(&spec.account_id).await.is_err());
        assert!(runtime
            .inner
            .accounts
            .read()
            .await
            .contains_key(&spec.account_id));
        drop(reservations);
        assert!(runtime.remove_account(&spec.account_id).await.unwrap());
        runtime.drain().await.unwrap();
    }

    #[tokio::test]
    async fn blocked_registry_mutation_never_installs_actor_after_stopped() {
        let mut config = RuntimeConfig::recommended();
        config.drain_timeout_ms = 100;
        let (_directory, runtime) = runtime_with_config(config);
        let registry = runtime.inner.accounts.read().await;
        let other = runtime.clone();
        let upsert = tokio::spawn(async move { other.upsert_account(account_spec(0)).await });
        time::timeout(Duration::from_secs(1), async {
            loop {
                if runtime.inner.operations.try_lock().is_err() {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
        assert!(runtime.drain().await.is_err());
        assert_eq!(
            runtime.inner.health.snapshot().lifecycle,
            LifecycleState::Draining
        );
        assert!(!*runtime.inner.drain_complete.borrow());
        drop(registry);
        assert!(upsert.await.unwrap().is_err());
        let stopped = runtime.drain().await.unwrap();
        assert_eq!(stopped.phase, RuntimeHealthPhase::Stopped);
        assert_eq!(stopped.actor_count, 0);
        assert!(runtime.inner.accounts.read().await.is_empty());
    }

    #[tokio::test]
    async fn cleanup_is_owned_after_drain_timeout_until_completion() {
        let mut config = RuntimeConfig::recommended();
        config.drain_timeout_ms = 100;
        let (_directory, runtime) = runtime_with_config(config);
        let (release, pending) = tokio::sync::oneshot::channel::<()>();
        let inner = runtime.inner.clone();
        inner.cleanup_in_flight.store(true, Ordering::Release);
        *lock_std(&inner.cleanup_job) = Some(tokio::spawn({
            let inner = inner.clone();
            async move {
                let _guard = CleanupInFlightGuard(inner);
                pending.await.unwrap();
            }
        }));
        assert!(runtime.drain().await.is_err());
        assert!(!*runtime.inner.drain_complete.borrow());
        assert!(inner.cleanup_in_flight.load(Ordering::Acquire));
        assert_eq!(runtime.snapshot().await.phase, RuntimeHealthPhase::Draining);
        release.send(()).unwrap();
        let stopped = runtime.drain().await.unwrap();
        assert_eq!(stopped.phase, RuntimeHealthPhase::Stopped);
        assert!(!inner.cleanup_in_flight.load(Ordering::Acquire));
        assert_eq!(stopped.unresolved_work, 0);
    }

    #[tokio::test]
    async fn finished_task_is_observed_even_when_deadline_has_elapsed() {
        let mut task = tokio::spawn(async {});
        while !task.is_finished() {
            tokio::task::yield_now().await;
        }
        assert_eq!(
            join_until(&mut task, Instant::now()).await,
            TaskJoinStatus::Completed
        );
    }

    #[tokio::test]
    async fn cached_health_keeps_dispatch_and_breaker_metrics() {
        let (_directory, runtime) = runtime();
        runtime.inner.health.update_runtime_with(|health| {
            health.dispatched_total = 27;
            health.queue_high_water = 12;
            health.open_circuit_count = 3;
        });
        runtime.publish_health_from_cached();
        let cached = runtime.inner.health.snapshot().runtime;
        assert_eq!(cached.dispatched_total, 27);
        assert_eq!(cached.queue_high_water, 12);
        assert_eq!(cached.open_circuit_count, 3);
        runtime.drain().await.unwrap();
    }

    #[tokio::test]
    async fn central_timer_runs_dynamic_storage_maintenance() {
        let mut config = RuntimeConfig::recommended();
        config.storage_cleanup_interval_ms = 5;
        let (_directory, runtime) = runtime_with_config(config);

        time::timeout(Duration::from_secs(1), async {
            loop {
                if runtime.snapshot().await.storage_cleanup_runs > 0 {
                    break;
                }
                time::sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .expect("central storage cleanup becomes observable");

        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test]
    async fn unexpected_dispatcher_exit_updates_cached_readiness_immediately() {
        let (_directory, runtime) = runtime();
        time::timeout(Duration::from_secs(1), async {
            while !runtime.inner.health.snapshot().ready {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("runtime tasks publish ready health");

        {
            let joins = lock_std(&runtime.inner.joins);
            joins
                .dispatcher
                .as_ref()
                .expect("dispatcher join remains owned")
                .abort();
        }
        time::timeout(Duration::from_secs(1), async {
            while runtime.inner.health.snapshot().runtime.dispatcher_alive {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("task guard publishes dispatcher exit");
        let health = runtime.inner.health.snapshot();
        assert!(!health.runtime.dispatcher_alive);
        assert!(!health.ready);

        let stopped = runtime.drain().await.expect("runtime drains");
        assert_eq!(stopped.phase, RuntimeHealthPhase::Stopped);
        assert!(stopped.unresolved_work >= 1);
    }

    #[tokio::test]
    async fn three_hundred_accounts_use_one_timer_and_drain_cleanly() {
        let (_directory, runtime) = runtime();
        for index in 0..300 {
            runtime
                .upsert_account(account_spec(index))
                .await
                .expect("account starts");
        }
        let snapshot = runtime.snapshot().await;
        assert_eq!(snapshot.actor_count, 300);
        assert_eq!(snapshot.central_timer_tasks, 1);
        assert!(snapshot.scheduled_timer_count <= 8 * 300 + 32);
        assert_eq!(snapshot.heartbeat_dirty_accounts, 300);

        let stopped = runtime.drain().await.expect("runtime drains");
        assert_eq!(stopped.actor_count, 0);
        assert_eq!(stopped.central_timer_tasks, 0);
        assert!(!stopped.accepting_work);
    }

    #[tokio::test]
    async fn stale_work_and_full_mailbox_are_explicit() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(1);
        runtime
            .upsert_account(spec.clone())
            .await
            .expect("account starts");
        let stale = WorkEnvelope::new(
            spec.account_id.clone(),
            "stale",
            WorkKind::ManualSend,
            WorkFence {
                actor_generation: 99,
                credential_generation: 1,
                lease_epoch: 1,
            },
            0,
        );
        assert_eq!(runtime.enqueue(stale).await, AdmissionResult::Stale);
        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test]
    async fn dependency_breaker_and_global_reconnect_budget_are_live() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(1);
        runtime
            .upsert_account(spec.clone())
            .await
            .expect("account starts");

        let permit = match runtime
            .try_dependency(&spec.account_id, DependencyKind::Transport)
            .await
        {
            DependencyAdmission::Allowed(permit) => permit,
            decision => panic!("unexpected first reconnect decision: {decision:?}"),
        };
        assert!(matches!(
            runtime
                .record_dependency_outcome(permit, DependencyOutcome::TransientFailure)
                .await,
            Some(BreakerRecord::Opened { .. })
        ));
        assert!(matches!(
            runtime
                .try_dependency(&spec.account_id, DependencyKind::Transport)
                .await,
            DependencyAdmission::CircuitOpen { .. }
        ));
        assert_eq!(runtime.snapshot().await.open_circuit_count, 1);

        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test]
    async fn hot_account_does_not_block_three_hundred_account_burst() {
        let (_directory, runtime) = runtime();
        let specs: Vec<AccountSpec> = (0..300).map(account_spec).collect();
        for spec in &specs {
            runtime
                .upsert_account(spec.clone())
                .await
                .expect("account starts");
        }
        for index in 0..100 {
            let spec = &specs[0];
            let work = WorkEnvelope::new(
                spec.account_id.clone(),
                format!("hot-{index}"),
                WorkKind::AutomaticReply,
                WorkFence {
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                },
                0,
            );
            enqueue_with_backpressure(&runtime, work).await;
        }
        for spec in &specs[1..] {
            let work = WorkEnvelope::new(
                spec.account_id.clone(),
                format!("cold-{}", spec.account_id),
                WorkKind::AutomaticReply,
                WorkFence {
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                },
                0,
            );
            enqueue_with_backpressure(&runtime, work).await;
        }

        time::timeout(Duration::from_secs(2), async {
            loop {
                if runtime.snapshot().await.dispatched_total >= 399 {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("bounded burst drains");
        let snapshot = runtime.snapshot().await;
        assert_eq!(snapshot.queue_depth, 0);
        assert_eq!(snapshot.dispatched_total, 399);
        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test]
    async fn failed_second_account_capacity_check_leaves_no_orphan_runtime_state() {
        let mut config = RuntimeConfig::recommended();
        config.heartbeat_max_accounts = 1;
        let (_directory, runtime) = runtime_with_config(config);
        let first = account_spec(0);
        let second = account_spec(1);
        runtime
            .upsert_account(first.clone())
            .await
            .expect("first account starts");

        time::timeout(Duration::from_secs(1), async {
            loop {
                if runtime.snapshot().await.scheduled_timer_count == 6 {
                    break;
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("first account timers become visible");
        let timers_before = runtime.snapshot().await.scheduled_timer_count;

        let error = runtime
            .upsert_account(second.clone())
            .await
            .expect_err("second account must hit the aggregate capacity");
        assert!(error.to_string().contains("account capacity 1 reached"));
        tokio::task::yield_now().await;

        assert_eq!(runtime.inner.accounts.read().await.len(), 1);
        assert!(runtime
            .inner
            .accounts
            .read()
            .await
            .contains_key(&first.account_id));
        let heartbeat = lock_std(&runtime.inner.heartbeat).snapshot();
        assert_eq!(heartbeat.tracked_accounts, 1);
        assert_eq!(heartbeat.dirty_accounts, 1);
        assert_eq!(
            runtime.snapshot().await.scheduled_timer_count,
            timers_before
        );
        assert!(matches!(
            runtime
                .admit_signer(SignerJob {
                    account_id: second.account_id.to_string(),
                    durable_id: "failed-account-signer".to_owned(),
                    kind: WorkKind::ManualSend,
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                    plan_digest: [0; 32],
                })
                .await,
            SignerAdmission::Stale
        ));

        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test]
    async fn stale_account_control_cannot_roll_back_registry_or_signer_scope() {
        let (_directory, runtime) = runtime();
        let mut current = account_spec(0);
        current.actor_generation = 2;
        current.state.credential_generation = 3;
        current.state.lease_epoch = 4;
        runtime
            .upsert_account(current.clone())
            .await
            .expect("current account starts");

        let mut stale_actor = current.clone();
        stale_actor.actor_generation = 1;
        let error = runtime
            .upsert_account(stale_actor)
            .await
            .expect_err("an older actor generation cannot replace the registry entry");
        assert!(error.to_string().contains("stale account control"));

        let mut stale_credential = current.control();
        stale_credential.state.credential_generation = 2;
        assert_eq!(
            runtime
                .update_account_control(&current.account_id, stale_credential)
                .await
                .expect("stale credential update is classified"),
            AdmissionResult::Stale
        );
        let mut stale_lease = current.control();
        stale_lease.state.lease_epoch = 3;
        assert_eq!(
            runtime
                .update_account_control(&current.account_id, stale_lease)
                .await
                .expect("stale lease update is classified"),
            AdmissionResult::Stale
        );

        let installed = {
            let accounts = runtime.inner.accounts.read().await;
            assert_eq!(accounts.len(), 1);
            accounts
                .get(&current.account_id)
                .expect("current account remains installed")
                .endpoint
                .control_snapshot()
        };
        assert_eq!(installed, current.control());
        assert!(matches!(
            runtime
                .admit_signer(SignerJob {
                    account_id: current.account_id.to_string(),
                    durable_id: "current-signer-fence".to_owned(),
                    kind: WorkKind::ManualSend,
                    actor_generation: 2,
                    credential_generation: 3,
                    lease_epoch: 4,
                    plan_digest: [3; 32],
                })
                .await,
            SignerAdmission::Accepted { .. }
        ));
        assert_eq!(
            runtime
                .admit_signer(SignerJob {
                    account_id: current.account_id.to_string(),
                    durable_id: "stale-signer-fence".to_owned(),
                    kind: WorkKind::ManualSend,
                    actor_generation: 1,
                    credential_generation: 3,
                    lease_epoch: 4,
                    plan_digest: [1; 32],
                })
                .await,
            SignerAdmission::Stale
        );

        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn concurrent_and_repeated_drain_wait_for_one_complete_shutdown() {
        let mut config = RuntimeConfig::recommended();
        config.drain_timeout_ms = 2_000;
        let (_directory, runtime) = runtime_with_config(config);
        runtime
            .upsert_account(account_spec(0))
            .await
            .expect("account starts");

        // Hold the serialized mutation gate so the first drain announces its
        // ownership but cannot finish. The second call must wait for the same
        // completion signal rather than returning a premature snapshot.
        let operation = runtime.inner.operations.lock().await;
        let first_runtime = runtime.clone();
        let first = tokio::spawn(async move { first_runtime.drain().await });
        time::timeout(Duration::from_secs(1), async {
            while !runtime.inner.drain_started.load(Ordering::Acquire) {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("first drain claims shutdown");
        let second_runtime = runtime.clone();
        let second = tokio::spawn(async move { second_runtime.drain().await });
        tokio::task::yield_now().await;
        assert!(!first.is_finished());
        assert!(!second.is_finished());
        drop(operation);

        let first_snapshot = first.await.expect("first drain task").expect("first drain");
        let second_snapshot = second
            .await
            .expect("second drain task")
            .expect("second drain");
        for snapshot in [&first_snapshot, &second_snapshot] {
            assert_eq!(snapshot.phase, RuntimeHealthPhase::Stopped);
            assert!(!snapshot.accepting_work);
            assert_eq!(snapshot.actor_count, 0);
            assert!(!snapshot.timer_alive);
            assert!(!snapshot.dispatcher_alive);
            assert_eq!(snapshot.signer_workers_alive, 0);
            assert_eq!(snapshot.unresolved_work, 0);
        }

        let repeated = time::timeout(Duration::from_millis(100), runtime.drain())
            .await
            .expect("completed drain is idempotent")
            .expect("repeated drain returns health");
        assert_eq!(repeated.phase, RuntimeHealthPhase::Stopped);
        assert!(!repeated.accepting_work);
    }

    #[tokio::test(flavor = "current_thread")]
    async fn drain_deadline_includes_waiting_for_the_operation_lock() {
        let mut config = RuntimeConfig::recommended();
        config.drain_timeout_ms = 250;
        let (_directory, runtime) = runtime_with_config(config);
        runtime
            .upsert_account(account_spec(0))
            .await
            .expect("account starts");

        let operation = runtime.inner.operations.lock().await;
        let started = Instant::now();
        let result = time::timeout(Duration::from_secs(1), runtime.drain())
            .await
            .expect("drain must not wait forever on the operation lock");
        drop(operation);
        assert!(
            started.elapsed() < Duration::from_secs(1),
            "the shared drain deadline must cover lock acquisition"
        );
        assert!(
            result.is_err(),
            "lock contention must be an explicit timeout"
        );
        assert!(!*runtime.inner.drain_complete.borrow());
        assert_eq!(
            runtime.inner.health.snapshot().lifecycle,
            LifecycleState::Draining
        );
        let repeated = runtime
            .drain()
            .await
            .expect("a repeated drain observes and reaps completion");
        assert_eq!(repeated.phase, RuntimeHealthPhase::Stopped);
    }

    #[tokio::test]
    async fn dependency_permit_abandon_and_credential_fence_release_attempts() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(0);
        runtime
            .upsert_account(spec.clone())
            .await
            .expect("account starts");

        let stale_permit = match runtime
            .try_dependency(&spec.account_id, DependencyKind::Signer)
            .await
        {
            DependencyAdmission::Allowed(permit) => permit,
            decision => panic!("unexpected dependency admission: {decision:?}"),
        };
        let mut rotated = spec.control();
        rotated.state.credential_generation = 2;
        assert_eq!(
            runtime
                .update_account_control(&spec.account_id, rotated)
                .await
                .expect("credential rotation is published"),
            AdmissionResult::Accepted
        );
        assert_eq!(
            runtime
                .record_dependency_outcome(stale_permit, DependencyOutcome::Success)
                .await,
            Some(BreakerRecord::IgnoredStale)
        );
        {
            let accounts = runtime.inner.accounts.read().await;
            assert_eq!(
                accounts
                    .get(&spec.account_id)
                    .expect("account remains installed")
                    .signer_breaker
                    .lock()
                    .unwrap_or_else(std::sync::PoisonError::into_inner)
                    .snapshot()
                    .in_flight_attempts,
                0
            );
        }

        let current_permit = match runtime
            .try_dependency(&spec.account_id, DependencyKind::Signer)
            .await
        {
            DependencyAdmission::Allowed(permit) => permit,
            decision => panic!("unexpected post-rotation admission: {decision:?}"),
        };
        assert!(runtime.abandon_dependency(current_permit).await);
        let accounts = runtime.inner.accounts.read().await;
        assert_eq!(
            accounts
                .get(&spec.account_id)
                .expect("account remains installed")
                .signer_breaker
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .snapshot()
                .in_flight_attempts,
            0
        );
        drop(accounts);

        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn dependency_permit_drop_after_task_abort_releases_breaker_attempt() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(0);
        runtime
            .upsert_account(spec.clone())
            .await
            .expect("account starts");
        let breaker = {
            let accounts = runtime.inner.accounts.read().await;
            Arc::clone(
                &accounts
                    .get(&spec.account_id)
                    .expect("account remains installed")
                    .signer_breaker,
            )
        };

        let (acquired_tx, acquired_rx) = tokio::sync::oneshot::channel();
        let task_runtime = runtime.clone();
        let account_id = spec.account_id.clone();
        let task = tokio::spawn(async move {
            let _permit = match task_runtime
                .try_dependency(&account_id, DependencyKind::Signer)
                .await
            {
                DependencyAdmission::Allowed(permit) => permit,
                decision => panic!("unexpected dependency admission: {decision:?}"),
            };
            acquired_tx.send(()).expect("test receiver remains alive");
            std::future::pending::<()>().await;
        });
        acquired_rx.await.expect("dependency attempt is acquired");
        assert_eq!(lock_std(&breaker).snapshot().in_flight_attempts, 1);

        task.abort();
        assert!(task
            .await
            .expect_err("aborted task cannot complete")
            .is_cancelled());
        time::timeout(Duration::from_secs(1), async {
            while lock_std(&breaker).snapshot().in_flight_attempts != 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("permit drop abandons the breaker attempt");

        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test]
    async fn storage_pressure_defers_only_disposable_background_work() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(0);
        runtime
            .upsert_account(spec.clone())
            .await
            .expect("account starts");
        runtime
            .inner
            .background_work_paused
            .store(true, Ordering::Release);
        runtime
            .inner
            .disposable_writes_allowed
            .store(false, Ordering::Release);
        let fence = WorkFence {
            actor_generation: 1,
            credential_generation: 1,
            lease_epoch: 1,
        };
        assert_eq!(
            runtime
                .enqueue(WorkEnvelope::new(
                    spec.account_id.clone(),
                    "deferred-reconcile",
                    WorkKind::Reconcile,
                    fence,
                    0,
                ))
                .await,
            AdmissionResult::Deferred
        );
        assert!(runtime
            .enqueue(WorkEnvelope::new(
                spec.account_id,
                "foreground-manual",
                WorkKind::ManualSend,
                fence,
                0,
            ))
            .await
            .is_admitted());
        time::timeout(Duration::from_secs(1), async {
            while runtime.snapshot().await.dispatched_total != 1 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("foreground work remains serviceable");
        let snapshot = runtime.snapshot().await;
        assert_eq!(snapshot.deferred_background, 1);
        assert_eq!(snapshot.dispatched_total, 1);
        assert_eq!(snapshot.unresolved_work, 0);

        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn signer_worker_rechecks_scope_after_its_async_yield() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(0);
        runtime
            .upsert_account(spec.clone())
            .await
            .expect("account starts");
        assert!(matches!(
            runtime
                .admit_signer(SignerJob {
                    account_id: spec.account_id.to_string(),
                    durable_id: "rotate-during-sign".to_owned(),
                    kind: WorkKind::ManualSend,
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                    plan_digest: [7; 32],
                })
                .await,
            SignerAdmission::Accepted { .. }
        ));
        assert_eq!(runtime.inner.signer_lanes.snapshot().queued, 1);

        // The worker acquires the fixed lane and deliberately yields. Because
        // this test uses one Tokio thread, this task then rotates the scope
        // before the worker can resume and claim success.
        tokio::task::yield_now().await;
        let in_flight = runtime.inner.signer_lanes.snapshot();
        assert_eq!(in_flight.queued, 0);
        assert_eq!(in_flight.in_flight, 1);
        let mut rotated = spec.control();
        rotated.state.credential_generation = 2;
        runtime
            .update_account_control(&spec.account_id, rotated)
            .await
            .expect("credential scope rotates");

        time::timeout(Duration::from_secs(1), async {
            while runtime.inner.signer_lanes.snapshot().in_flight != 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("stale signer permit is released");
        let signer = runtime.inner.signer_lanes.snapshot();
        assert_eq!(signer.completed, 0);
        assert_eq!(signer.cancelled, 1);
        assert_eq!(signer.in_flight, 0);

        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn signer_worker_rechecks_send_permission_after_its_async_yield() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(0);
        runtime
            .upsert_account(spec.clone())
            .await
            .expect("account starts");
        assert!(matches!(
            runtime
                .admit_signer(SignerJob {
                    account_id: spec.account_id.to_string(),
                    durable_id: "risk-control-during-sign".to_owned(),
                    kind: WorkKind::ManualSend,
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                    plan_digest: [9; 32],
                })
                .await,
            SignerAdmission::Accepted { .. }
        ));
        tokio::task::yield_now().await;
        let in_flight = runtime.inner.signer_lanes.snapshot();
        assert_eq!(in_flight.queued, 0);
        assert_eq!(in_flight.in_flight, 1);

        let mut risk_controlled = spec.control();
        risk_controlled.state.send = SendCapability::RiskControlled;
        assert_eq!(
            runtime
                .update_account_control(&spec.account_id, risk_controlled)
                .await
                .expect("risk-control state is published"),
            AdmissionResult::Accepted
        );
        time::timeout(Duration::from_secs(1), async {
            while runtime.inner.signer_lanes.snapshot().in_flight != 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("permission-revoked signer permit is released");
        let signer = runtime.inner.signer_lanes.snapshot();
        assert_eq!(signer.completed, 0);
        assert_eq!(signer.cancelled, 1);
        assert_eq!(signer.in_flight, 0);
        assert_eq!(
            runtime
                .admit_signer(SignerJob {
                    account_id: spec.account_id.to_string(),
                    durable_id: "risk-controlled-new-send".to_owned(),
                    kind: WorkKind::ManualSend,
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                    plan_digest: [10; 32],
                })
                .await,
            SignerAdmission::Stale
        );

        runtime.drain().await.expect("runtime drains");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn terminal_account_control_purges_queued_signer_work() {
        let (_directory, runtime) = runtime();
        let spec = account_spec(0);
        runtime
            .upsert_account(spec.clone())
            .await
            .expect("account starts");
        assert!(matches!(
            runtime
                .admit_signer(SignerJob {
                    account_id: spec.account_id.to_string(),
                    durable_id: "queued-before-terminal-state".to_owned(),
                    kind: WorkKind::ManualSend,
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                    plan_digest: [11; 32],
                })
                .await,
            SignerAdmission::Accepted { .. }
        ));
        assert_eq!(runtime.inner.signer_lanes.snapshot().queued, 1);

        let mut terminal = spec.control();
        terminal.state.lifecycle = LifecycleState::Faulted;
        assert_eq!(
            runtime
                .update_account_control(&spec.account_id, terminal)
                .await
                .expect("terminal state is published"),
            AdmissionResult::Accepted
        );
        let signer = runtime.inner.signer_lanes.snapshot();
        assert_eq!(signer.queued, 0);
        assert_eq!(signer.rejected_stale, 1);
        assert_eq!(runtime.snapshot().await.unresolved_work, 1);
        assert_eq!(
            runtime
                .admit_signer(SignerJob {
                    account_id: spec.account_id.to_string(),
                    durable_id: "new-work-after-terminal-state".to_owned(),
                    kind: WorkKind::ManualSend,
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                    plan_digest: [12; 32],
                })
                .await,
            SignerAdmission::Stale
        );

        runtime.drain().await.expect("runtime drains");
    }

    async fn enqueue_with_backpressure(runtime: &RuntimeHandle, work: WorkEnvelope) {
        time::timeout(Duration::from_secs(2), async {
            loop {
                match runtime.enqueue(work.clone()).await {
                    result if result.is_admitted() => break,
                    AdmissionResult::Full { .. } => tokio::task::yield_now().await,
                    result => panic!("unexpected admission result: {result:?}"),
                }
            }
        })
        .await
        .expect("bounded producer retry makes progress");
    }
}
