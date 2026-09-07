//! Lightweight per-account actor with a bounded data mailbox and independent
//! latest-value control channel.

use std::sync::Arc;

use tokio::{
    sync::{mpsc, oneshot, watch, Mutex, Notify},
    task::JoinHandle,
};

use crate::state::{AccountRuntimeState, LifecycleState, OwnershipState, SendCapability};

use super::{
    fair_queue::FairQueue,
    metrics::RuntimeCounters,
    model::{AccountId, AdmissionResult, CapacityScope, WorkEnvelope, WorkKind},
};

/// Latest control-plane facts for one account. `watch` delivery means a full
/// work mailbox cannot hide stop, lease loss, or credential rotation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct AccountControl {
    pub actor_generation: u64,
    pub state: AccountRuntimeState,
}

impl AccountControl {
    #[must_use]
    pub const fn matches(&self, work: &WorkEnvelope) -> bool {
        work.actor_generation == self.actor_generation
            && work.credential_generation == self.state.credential_generation
            && work.lease_epoch == self.state.lease_epoch
    }

    #[must_use]
    pub const fn is_terminal(&self) -> bool {
        matches!(
            self.state.lifecycle,
            LifecycleState::Stopped | LifecycleState::Faulted
        )
    }

    #[must_use]
    pub const fn allows(&self, kind: WorkKind) -> bool {
        match kind {
            WorkKind::ManualSend => self.state.can_attempt_manual_send(),
            WorkKind::AutomaticReply => self.state.can_attempt_auto_reply(),
            WorkKind::InboundWakeup => self.state.can_receive(),
            WorkKind::Reconcile | WorkKind::PendingRecovery => {
                matches!(
                    self.state.lifecycle,
                    LifecycleState::Running | LifecycleState::PausedAuto
                ) && matches!(self.state.ownership, OwnershipState::Owned)
                    && !matches!(self.state.send, SendCapability::AuthExpired)
            }
            WorkKind::KeepaliveLease => {
                matches!(
                    self.state.lifecycle,
                    LifecycleState::Running | LifecycleState::PausedAuto
                ) && matches!(
                    self.state.ownership,
                    OwnershipState::Owned | OwnershipState::Acquiring
                )
            }
            WorkKind::Maintenance => matches!(
                self.state.lifecycle,
                LifecycleState::Running | LifecycleState::PausedAuto
            ),
        }
    }
}

/// Cloneable ingress/control endpoint retained by the supervisor registry.
#[derive(Clone, Debug)]
pub struct AccountEndpoint {
    account_id: AccountId,
    mailbox: mpsc::Sender<MailboxItem>,
    control: watch::Sender<AccountControl>,
    counters: Arc<RuntimeCounters>,
}

#[derive(Debug)]
struct MailboxItem {
    work: WorkEnvelope,
    receipt: Option<oneshot::Sender<AdmissionResult>>,
}

impl AccountEndpoint {
    #[must_use]
    pub const fn account_id(&self) -> &AccountId {
        &self.account_id
    }

    #[must_use]
    pub fn subscribe_control(&self) -> watch::Receiver<AccountControl> {
        self.control.subscribe()
    }

    #[must_use]
    pub fn control_snapshot(&self) -> AccountControl {
        *self.control.borrow()
    }

    #[must_use]
    pub fn mailbox_depth(&self) -> usize {
        self.mailbox
            .max_capacity()
            .saturating_sub(self.mailbox.capacity())
    }

    /// Waits for the actor to return the central fair-queue admission result.
    pub async fn enqueue(&self, work: WorkEnvelope) -> AdmissionResult {
        if let Some(rejection) = self.preflight(&work) {
            return rejection;
        }

        let (receipt, admitted) = oneshot::channel();
        match self.mailbox.try_send(MailboxItem {
            work,
            receipt: Some(receipt),
        }) {
            Ok(()) => admitted.await.unwrap_or(AdmissionResult::Stopping),
            Err(mpsc::error::TrySendError::Full(_)) => self.reject_mailbox_full(),
            Err(mpsc::error::TrySendError::Closed(_)) => AdmissionResult::Stopping,
        }
    }

    /// Attempts bounded timer ingress without waiting for an actor receipt.
    ///
    /// `Accepted` means only that the mailbox owns the item. If shutdown closes
    /// the fair queue before the actor can transfer it, unresolved-work
    /// accounting retains the durable recovery obligation.
    #[must_use]
    pub fn try_enqueue_detached(&self, work: WorkEnvelope) -> AdmissionResult {
        if let Some(rejection) = self.preflight(&work) {
            return rejection;
        }
        match self.mailbox.try_send(MailboxItem {
            work,
            receipt: None,
        }) {
            Ok(()) => AdmissionResult::Accepted,
            Err(mpsc::error::TrySendError::Full(_)) => self.reject_mailbox_full(),
            Err(mpsc::error::TrySendError::Closed(_)) => AdmissionResult::Stopping,
        }
    }

    fn preflight(&self, work: &WorkEnvelope) -> Option<AdmissionResult> {
        if !work.has_bounded_identity() {
            self.counters.note_invalid_work();
            return Some(AdmissionResult::Invalid);
        }
        if !self.counters.accepting_work() {
            return Some(AdmissionResult::Stopping);
        }
        let control = *self.control.borrow();
        if control.is_terminal() {
            return Some(AdmissionResult::Stopping);
        }
        if work.account_id != self.account_id
            || !control.matches(work)
            || !control.allows(work.kind)
        {
            self.counters.note_stale_work();
            return Some(AdmissionResult::Stale);
        }
        None
    }

    fn reject_mailbox_full(&self) -> AdmissionResult {
        self.counters.note_mailbox_rejected();
        AdmissionResult::Full {
            scope: CapacityScope::Account,
            recovery_needed: true,
        }
    }

    pub fn update_control(&self, control: AccountControl) {
        self.control.send_replace(control);
    }

    pub fn stop(&self) {
        let mut control = self.control_snapshot();
        control.state.lifecycle = LifecycleState::Stopped;
        self.control.send_replace(control);
    }
}

/// Actor completion accounting used by bounded shutdown.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct AccountActorExit {
    pub queued: u64,
    pub rejected_stale_or_state: u64,
    pub rejected_full: u64,
}

/// Endpoint plus owned task handle. The supervisor must observe `join` during
/// removal or process drain.
#[derive(Debug)]
pub struct SpawnedAccountActor {
    pub endpoint: AccountEndpoint,
    pub join: JoinHandle<AccountActorExit>,
}

#[must_use]
pub fn spawn_account_actor(
    account_id: AccountId,
    control: AccountControl,
    mailbox_capacity: usize,
    fair_queue: Arc<Mutex<FairQueue>>,
    dispatcher_notify: Arc<Notify>,
    counters: Arc<RuntimeCounters>,
) -> SpawnedAccountActor {
    let (mailbox, receiver) = mpsc::channel(mailbox_capacity);
    let (control_sender, control_receiver) = watch::channel(control);
    let endpoint = AccountEndpoint {
        account_id: account_id.clone(),
        mailbox,
        control: control_sender,
        counters: counters.clone(),
    };
    let join = tokio::spawn(run_account_actor(
        account_id,
        receiver,
        control_receiver,
        fair_queue,
        dispatcher_notify,
        counters,
    ));
    SpawnedAccountActor { endpoint, join }
}

async fn run_account_actor(
    account_id: AccountId,
    mut mailbox: mpsc::Receiver<MailboxItem>,
    mut control: watch::Receiver<AccountControl>,
    fair_queue: Arc<Mutex<FairQueue>>,
    dispatcher_notify: Arc<Notify>,
    counters: Arc<RuntimeCounters>,
) -> AccountActorExit {
    let mut result = AccountActorExit::default();
    loop {
        if control.borrow().is_terminal() {
            break;
        }
        tokio::select! {
            biased;
            changed = control.changed() => {
                if changed.is_err() || control.borrow().is_terminal() {
                    break;
                }
            }
            maybe_item = mailbox.recv() => {
                let Some(MailboxItem { work, receipt }) = maybe_item else { break; };
                let current = *control.borrow_and_update();
                if work.account_id != account_id
                    || !current.matches(&work)
                    || !current.allows(work.kind)
                {
                    result.rejected_stale_or_state =
                        result.rejected_stale_or_state.saturating_add(1);
                    counters.note_stale_work();
                    if receipt.is_none() {
                        counters.add_unresolved_work(1);
                    }
                    if let Some(receipt) = receipt {
                        let _ = receipt.send(AdmissionResult::Stale);
                    }
                    continue;
                }
                let mut queue = fair_queue.lock().await;
                let current = *control.borrow_and_update();
                if work.account_id != account_id
                    || !current.matches(&work)
                    || !current.allows(work.kind)
                {
                    result.rejected_stale_or_state =
                        result.rejected_stale_or_state.saturating_add(1);
                    counters.note_stale_work();
                    if receipt.is_none() {
                        counters.add_unresolved_work(1);
                    }
                    if let Some(receipt) = receipt {
                        let _ = receipt.send(AdmissionResult::Stale);
                    }
                    continue;
                }
                let admission = queue.try_enqueue(work);
                drop(queue);
                match admission {
                    AdmissionResult::Accepted | AdmissionResult::Coalesced => {
                        result.queued = result.queued.saturating_add(1);
                        dispatcher_notify.notify_one();
                    }
                    AdmissionResult::Full { .. } => {
                        result.rejected_full = result.rejected_full.saturating_add(1);
                    }
                    AdmissionResult::Stopping => {
                        counters.add_unresolved_work(1);
                    }
                    AdmissionResult::Invalid => {
                        counters.note_invalid_work();
                    }
                    AdmissionResult::Stale | AdmissionResult::UnknownAccount => {
                        result.rejected_stale_or_state =
                            result.rejected_stale_or_state.saturating_add(1);
                    }
                    AdmissionResult::Duplicate | AdmissionResult::Deferred => {}
                }
                if let Some(receipt) = receipt {
                    let _ = receipt.send(admission);
                }
            }
        }
    }

    // Anything still buffered has a durable ID and is reported for recovery;
    // it is never silently re-sent during shutdown.
    mailbox.close();
    let unresolved = mailbox.len();
    counters.add_unresolved_work(unresolved);
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        runtime::{
            fair_queue::FairQueueConfig,
            model::{WorkFence, WorkKind, MAX_DURABLE_ID_BYTES},
        },
        state::{InboundState, OwnershipState},
    };

    fn running_control(actor_generation: u64) -> AccountControl {
        AccountControl {
            actor_generation,
            state: AccountRuntimeState {
                lifecycle: LifecycleState::Running,
                ownership: OwnershipState::Owned,
                inbound: InboundState::WsHealthy,
                send: SendCapability::Sendable,
                credential_generation: 7,
                lease_epoch: 11,
            },
        }
    }

    fn work(account_id: AccountId, actor_generation: u64, kind: WorkKind) -> WorkEnvelope {
        work_with_id(account_id, actor_generation, kind, format!("job-{kind:?}"))
    }

    fn work_with_id(
        account_id: AccountId,
        actor_generation: u64,
        kind: WorkKind,
        durable_id: impl Into<String>,
    ) -> WorkEnvelope {
        WorkEnvelope::new(
            account_id,
            durable_id,
            kind,
            WorkFence {
                actor_generation,
                credential_generation: 7,
                lease_epoch: 11,
            },
            1,
        )
    }

    #[tokio::test(flavor = "current_thread")]
    async fn detached_admission_invalidated_before_dequeue_is_recoverable() {
        let queue = Arc::new(Mutex::new(
            FairQueue::new(FairQueueConfig::default()).unwrap(),
        ));
        let counters = Arc::new(RuntimeCounters::default());
        let id = AccountId::new("detached").unwrap();
        let actor = spawn_account_actor(
            id.clone(),
            running_control(1),
            2,
            queue.clone(),
            Arc::new(Notify::new()),
            counters.clone(),
        );
        assert_eq!(
            actor
                .endpoint
                .try_enqueue_detached(work(id, 1, WorkKind::ManualSend)),
            AdmissionResult::Accepted
        );
        let mut restricted = running_control(1);
        restricted.state.send = SendCapability::RiskControlled;
        actor.endpoint.update_control(restricted);
        tokio::time::timeout(std::time::Duration::from_secs(1), async {
            while counters.stale_work_rejected() == 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
        assert_eq!(counters.unresolved_work(), 1);
        assert!(queue.lock().await.is_empty());
        actor.endpoint.stop();
        let exit = actor.join.await.unwrap();
        assert_eq!(exit.queued, 0);
    }

    #[tokio::test]
    async fn stale_generation_is_rejected_before_the_account_mailbox() {
        let queue = Arc::new(Mutex::new(
            FairQueue::new(FairQueueConfig::default()).expect("queue"),
        ));
        let counters = Arc::new(RuntimeCounters::default());
        let account_id = AccountId::new("account-a").unwrap();
        let actor = spawn_account_actor(
            account_id.clone(),
            running_control(2),
            1,
            queue,
            Arc::new(Notify::new()),
            counters.clone(),
        );

        assert_eq!(
            actor
                .endpoint
                .enqueue(work(account_id, 1, WorkKind::ManualSend))
                .await,
            AdmissionResult::Stale
        );
        assert_eq!(counters.stale_work_rejected(), 1);
        actor.endpoint.stop();
        actor.join.await.expect("actor joins");
    }

    #[tokio::test]
    async fn invalid_durable_ids_are_rejected_before_the_account_mailbox() {
        let queue = Arc::new(Mutex::new(
            FairQueue::new(FairQueueConfig::default()).expect("queue"),
        ));
        let counters = Arc::new(RuntimeCounters::default());
        let account_id = AccountId::new("account-invalid").unwrap();
        let actor = spawn_account_actor(
            account_id.clone(),
            running_control(1),
            1,
            queue.clone(),
            Arc::new(Notify::new()),
            counters.clone(),
        );

        assert_eq!(
            actor
                .endpoint
                .enqueue(work_with_id(
                    account_id.clone(),
                    1,
                    WorkKind::ManualSend,
                    "",
                ))
                .await,
            AdmissionResult::Invalid
        );
        assert_eq!(
            actor.endpoint.try_enqueue_detached(work_with_id(
                account_id,
                1,
                WorkKind::ManualSend,
                "x".repeat(MAX_DURABLE_ID_BYTES + 1),
            )),
            AdmissionResult::Invalid
        );
        assert_eq!(actor.endpoint.mailbox_depth(), 0);
        assert_eq!(counters.invalid_work_rejected(), 2);
        assert_eq!(queue.lock().await.snapshot().rejected_invalid, 0);

        actor.endpoint.stop();
        actor.join.await.expect("actor joins");
    }

    #[tokio::test]
    async fn disallowed_work_is_stale_before_entering_the_mailbox() {
        let queue = Arc::new(Mutex::new(
            FairQueue::new(FairQueueConfig::default()).expect("queue"),
        ));
        let notify = Arc::new(Notify::new());
        let counters = Arc::new(RuntimeCounters::default());
        let account_id = AccountId::new("account-a").unwrap();
        let mut control = running_control(1);
        control.state.lifecycle = LifecycleState::PausedAuto;
        let actor = spawn_account_actor(
            account_id.clone(),
            control,
            1,
            queue.clone(),
            notify,
            counters,
        );

        assert_eq!(
            actor
                .endpoint
                .enqueue(work(account_id.clone(), 1, WorkKind::AutomaticReply))
                .await,
            AdmissionResult::Stale
        );
        assert_eq!(actor.endpoint.mailbox_depth(), 0);
        assert_eq!(queue.lock().await.len(), 0);

        assert_eq!(
            actor
                .endpoint
                .enqueue(work(account_id, 1, WorkKind::ManualSend))
                .await,
            AdmissionResult::Accepted
        );
        assert_eq!(queue.lock().await.len(), 1);

        let mut risk_controlled = running_control(1);
        risk_controlled.state.send = SendCapability::RiskControlled;
        actor.endpoint.update_control(risk_controlled);
        assert_eq!(
            actor
                .endpoint
                .enqueue(work_with_id(
                    actor.endpoint.account_id().clone(),
                    1,
                    WorkKind::ManualSend,
                    "risk-controlled-manual",
                ))
                .await,
            AdmissionResult::Stale
        );
        assert_eq!(actor.endpoint.mailbox_depth(), 0);

        actor.endpoint.stop();
        actor.join.await.expect("actor joins");
    }

    #[tokio::test]
    async fn async_enqueue_waits_for_the_actor_fair_queue_ack() {
        let queue = Arc::new(Mutex::new(
            FairQueue::new(FairQueueConfig::default()).expect("queue"),
        ));
        let queue_guard = queue.lock().await;
        let account_id = AccountId::new("account-ack").unwrap();
        let actor = spawn_account_actor(
            account_id.clone(),
            running_control(1),
            1,
            queue.clone(),
            Arc::new(Notify::new()),
            Arc::new(RuntimeCounters::default()),
        );

        let endpoint = actor.endpoint.clone();
        let enqueue = tokio::spawn(async move {
            endpoint
                .enqueue(work(account_id, 1, WorkKind::ManualSend))
                .await
        });
        tokio::task::yield_now().await;
        assert!(!enqueue.is_finished());

        drop(queue_guard);
        assert_eq!(
            enqueue.await.expect("enqueue joins"),
            AdmissionResult::Accepted
        );
        assert_eq!(queue.lock().await.len(), 1);

        actor.endpoint.stop();
        actor.join.await.expect("actor joins");
    }

    #[tokio::test]
    async fn fair_queue_full_is_returned_through_the_actor_receipt() {
        let queue = Arc::new(Mutex::new(
            FairQueue::new(FairQueueConfig {
                global_capacity: 1,
                per_account_capacity: 1,
                class_capacities: super::super::model::WorkClassCapacities {
                    manual: 1,
                    automatic: 1,
                    background: 1,
                },
                max_manual_burst: 1,
            })
            .expect("queue"),
        ));
        let account_id = AccountId::new("account-full").unwrap();
        assert_eq!(
            queue.lock().await.try_enqueue(work_with_id(
                account_id.clone(),
                1,
                WorkKind::ManualSend,
                "already-full",
            )),
            AdmissionResult::Accepted
        );
        let actor = spawn_account_actor(
            account_id.clone(),
            running_control(1),
            1,
            queue,
            Arc::new(Notify::new()),
            Arc::new(RuntimeCounters::default()),
        );

        assert_eq!(
            actor
                .endpoint
                .enqueue(work_with_id(
                    account_id,
                    1,
                    WorkKind::ManualSend,
                    "rejected-full",
                ))
                .await,
            AdmissionResult::Full {
                scope: CapacityScope::Global,
                recovery_needed: true,
            }
        );

        actor.endpoint.stop();
        let exit = actor.join.await.expect("actor joins");
        assert_eq!(exit.rejected_full, 1);
    }

    #[tokio::test]
    async fn fair_queue_stopping_is_returned_and_counted_unresolved() {
        let mut stopped_queue = FairQueue::new(FairQueueConfig::default()).expect("queue");
        stopped_queue.stop_accepting();
        let queue = Arc::new(Mutex::new(stopped_queue));
        let counters = Arc::new(RuntimeCounters::default());
        let account_id = AccountId::new("account-stopping").unwrap();
        let actor = spawn_account_actor(
            account_id.clone(),
            running_control(1),
            1,
            queue,
            Arc::new(Notify::new()),
            counters.clone(),
        );

        assert_eq!(
            actor
                .endpoint
                .enqueue(work(account_id, 1, WorkKind::ManualSend))
                .await,
            AdmissionResult::Stopping
        );
        assert_eq!(counters.unresolved_work(), 1);

        actor.endpoint.stop();
        actor.join.await.expect("actor joins");
    }

    #[tokio::test]
    async fn shutdown_counts_buffered_work_and_drops_its_receipt_as_stopping() {
        let queue = Arc::new(Mutex::new(
            FairQueue::new(FairQueueConfig::default()).expect("queue"),
        ));
        let queue_guard = queue.lock().await;
        let counters = Arc::new(RuntimeCounters::default());
        let account_id = AccountId::new("account-shutdown").unwrap();
        let actor = spawn_account_actor(
            account_id.clone(),
            running_control(1),
            2,
            queue.clone(),
            Arc::new(Notify::new()),
            counters.clone(),
        );

        assert_eq!(
            actor.endpoint.try_enqueue_detached(work_with_id(
                account_id.clone(),
                1,
                WorkKind::ManualSend,
                "in-actor",
            )),
            AdmissionResult::Accepted
        );
        while actor.endpoint.mailbox_depth() != 0 {
            tokio::task::yield_now().await;
        }

        let endpoint = actor.endpoint.clone();
        let buffered = tokio::spawn(async move {
            endpoint
                .enqueue(work_with_id(
                    account_id,
                    1,
                    WorkKind::ManualSend,
                    "buffered",
                ))
                .await
        });
        while actor.endpoint.mailbox_depth() == 0 {
            tokio::task::yield_now().await;
        }

        actor.endpoint.stop();
        drop(queue_guard);
        let exit = actor.join.await.expect("actor joins");
        assert_eq!(
            buffered.await.expect("enqueue joins"),
            AdmissionResult::Stopping
        );
        assert_eq!(counters.unresolved_work(), 2);
        assert_eq!(exit.queued, 0);
        assert_eq!(exit.rejected_stale_or_state, 1);
    }
}
