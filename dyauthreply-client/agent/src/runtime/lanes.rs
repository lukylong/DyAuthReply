//! Bounded, account-isolated admission for protocol signer work.
//!
//! A lane is a concurrency slot rather than an OS thread. Accounts are mapped
//! to one deterministic lane, queues inside a lane are served round-robin, and
//! the returned permit releases capacity even when its owner is cancelled.

use std::collections::{BTreeMap, HashMap, VecDeque};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use serde::{Deserialize, Serialize};

use super::model::{WorkKind, MAX_DURABLE_ID_BYTES};

/// Stable permission-bitmap order used by [`SignerScope::allowed_kinds`].
pub const SIGNER_WORK_KINDS: [WorkKind; 7] = [
    WorkKind::InboundWakeup,
    WorkKind::Reconcile,
    WorkKind::PendingRecovery,
    WorkKind::ManualSend,
    WorkKind::AutomaticReply,
    WorkKind::KeepaliveLease,
    WorkKind::Maintenance,
];

/// Number of [`WorkKind`] variants represented by [`SignerScope::allowed_kinds`].
pub const SIGNER_WORK_KIND_COUNT: usize = SIGNER_WORK_KINDS.len();

/// Immutable, non-secret identity attached to a signer request.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SignerJob {
    /// Opaque managed-account identifier.
    pub account_id: String,
    /// Stable durable operation identifier used by the correctness store.
    pub durable_id: String,
    /// Work category whose current account permission must still allow signing.
    pub kind: WorkKind,
    /// Generation of the lightweight account actor that created the work.
    pub actor_generation: u64,
    /// Generation of the account-bound credential bundle.
    pub credential_generation: u64,
    /// Remote fencing epoch required by the side effect.
    pub lease_epoch: u64,
    /// Digest of the already validated request plan; no credential bytes live here.
    pub plan_digest: [u8; 32],
}

/// Current account scope against which queued signer work is validated.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SignerScope {
    /// Opaque managed-account identifier.
    pub account_id: String,
    /// Current actor generation.
    pub actor_generation: u64,
    /// Current credential generation.
    pub credential_generation: u64,
    /// Current remote fence epoch.
    pub lease_epoch: u64,
    /// Fixed permission bitmap indexed by [`WorkKind`].
    pub allowed_kinds: [bool; SIGNER_WORK_KIND_COUNT],
}

impl SignerScope {
    /// Returns whether this scope currently permits signing for `kind`.
    #[must_use]
    pub const fn allows(&self, kind: WorkKind) -> bool {
        self.allowed_kinds[work_kind_index(kind)]
    }

    fn matches(&self, job: &SignerJob) -> bool {
        self.account_id == job.account_id
            && self.actor_generation == job.actor_generation
            && self.credential_generation == job.credential_generation
            && self.lease_epoch == job.lease_epoch
            && self.allows(job.kind)
    }
}

/// Fixed resource bounds for signer admission.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SignerLanesConfig {
    /// Number of signer futures allowed to execute concurrently.
    pub lane_count: usize,
    /// Maximum number of queued jobs across every account.
    pub queue_capacity: usize,
    /// Maximum queued jobs belonging to one account.
    pub per_account_capacity: usize,
}

impl Default for SignerLanesConfig {
    fn default() -> Self {
        Self {
            lane_count: 4,
            queue_capacity: 256,
            per_account_capacity: 16,
        }
    }
}

/// Invalid signer lane configuration.
#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum SignerConfigError {
    /// At least one lane is required.
    #[error("signer lane_count must be greater than zero")]
    NoLanes,
    /// The global queue must accept at least one job.
    #[error("signer queue_capacity must be greater than zero")]
    NoQueueCapacity,
    /// Every account must be able to queue at least one job.
    #[error("signer per_account_capacity must be greater than zero")]
    NoAccountCapacity,
    /// A per-account limit cannot exceed the global queue limit.
    #[error("signer per_account_capacity cannot exceed queue_capacity")]
    AccountCapacityExceedsGlobal,
}

/// Which bound rejected an otherwise current job.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignerFullScope {
    /// The process-wide signer queue is full.
    Global,
    /// This account reached its own queue limit.
    Account,
}

/// Non-blocking result from signer admission.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "result")]
pub enum SignerAdmission {
    /// The job was queued on the deterministic account lane.
    Accepted {
        /// Zero-based lane index.
        lane: usize,
    },
    /// A configured queue bound was reached.
    Full {
        /// Bound responsible for the rejection.
        scope: SignerFullScope,
        /// Current queue depth for that bound.
        queued: usize,
        /// Configured queue capacity for that bound.
        capacity: usize,
    },
    /// New signer work is no longer accepted during shutdown.
    Closed,
    /// The actor, credential, lease, or work-kind permission is no longer current.
    Stale,
    /// The durable operation identifier was empty or exceeded its byte bound.
    Invalid,
}

/// Completion classification used for resource accounting.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SignerCompletion {
    /// The signing operation completed successfully.
    Success,
    /// The signer returned an error.
    Error,
    /// The future was cancelled before recording a result.
    Cancelled,
}

/// Per-lane queue and execution counters.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SignerLaneSnapshot {
    /// Zero-based lane index.
    pub lane: usize,
    /// Number of queued jobs on this lane.
    pub queued: usize,
    /// Whether this lane currently owns one execution permit.
    pub in_flight: bool,
    /// Number of accounts with queued work on this lane.
    pub queued_accounts: usize,
}

/// Bounded signer resource health exposed by the runtime coordinator.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SignerLanesSnapshot {
    /// Whether new admissions are accepted.
    pub accepting: bool,
    /// Fixed number of execution lanes.
    pub lane_count: usize,
    /// Configured global queue capacity.
    pub queue_capacity: usize,
    /// Current global queue depth.
    pub queued: usize,
    /// Current number of executing signer jobs.
    pub in_flight: usize,
    /// Largest observed queue depth.
    pub max_queued: usize,
    /// Largest observed signer concurrency.
    pub max_in_flight: usize,
    /// Accepted jobs since construction.
    pub admitted: u64,
    /// Jobs rejected because a queue was full.
    pub rejected_full: u64,
    /// Jobs rejected because their durable identifier was invalid.
    pub rejected_invalid: u64,
    /// Jobs rejected or discarded because their scope was stale.
    pub rejected_stale: u64,
    /// Jobs rejected after admission closed.
    pub rejected_closed: u64,
    /// Successfully completed jobs.
    pub completed: u64,
    /// Jobs that completed with a signer error.
    pub failed: u64,
    /// Jobs whose permits were dropped without a recorded result.
    pub cancelled: u64,
    /// Per-lane detail.
    pub lanes: Vec<SignerLaneSnapshot>,
}

#[derive(Debug, Default)]
struct LaneState {
    queues: BTreeMap<String, VecDeque<SignerJob>>,
    round_robin: VecDeque<String>,
    queued: usize,
    in_flight: bool,
}

#[derive(Debug)]
struct SignerInner {
    config: SignerLanesConfig,
    accepting: bool,
    scopes: HashMap<String, SignerScope>,
    incarnations: HashMap<String, Arc<()>>,
    lanes: Vec<LaneState>,
    next_lane: usize,
    queued: usize,
    in_flight: usize,
    max_queued: usize,
    max_in_flight: usize,
    admitted: u64,
    rejected_full: u64,
    rejected_invalid: u64,
    rejected_stale: u64,
    rejected_closed: u64,
    completed: u64,
    failed: u64,
    cancelled: u64,
}

impl SignerInner {
    fn finish(&mut self, lane: usize, completion: SignerCompletion) {
        if let Some(state) = self.lanes.get_mut(lane) {
            if state.in_flight {
                state.in_flight = false;
                self.in_flight = self.in_flight.saturating_sub(1);
                match completion {
                    SignerCompletion::Success => self.completed = self.completed.saturating_add(1),
                    SignerCompletion::Error => self.failed = self.failed.saturating_add(1),
                    SignerCompletion::Cancelled => {
                        self.cancelled = self.cancelled.saturating_add(1);
                    }
                }
            }
        }
    }

    fn pop_lane(&mut self, lane_index: usize) -> Option<SignerJob> {
        loop {
            let (account_id, job, has_more) = {
                let lane = self.lanes.get_mut(lane_index)?;
                if lane.in_flight {
                    return None;
                }
                let account_id = lane.round_robin.pop_front()?;
                let queue = lane.queues.get_mut(&account_id)?;
                let job = queue.pop_front()?;
                let has_more = !queue.is_empty();
                if has_more {
                    lane.round_robin.push_back(account_id.clone());
                } else {
                    lane.queues.remove(&account_id);
                }
                lane.queued = lane.queued.saturating_sub(1);
                (account_id, job, has_more)
            };

            let _ = (account_id, has_more);
            self.queued = self.queued.saturating_sub(1);
            let current = self
                .scopes
                .get(&job.account_id)
                .is_some_and(|scope| scope.matches(&job));
            if current {
                let lane = self.lanes.get_mut(lane_index)?;
                lane.in_flight = true;
                self.in_flight = self.in_flight.saturating_add(1);
                self.max_in_flight = self.max_in_flight.max(self.in_flight);
                return Some(job);
            }
            self.rejected_stale = self.rejected_stale.saturating_add(1);
        }
    }

    fn purge_account(&mut self, account_id: &str) -> usize {
        let lane_index = sticky_lane(account_id, self.config.lane_count);
        let Some(lane) = self.lanes.get_mut(lane_index) else {
            return 0;
        };
        let removed = lane
            .queues
            .remove(account_id)
            .map_or(0, |queue| queue.len());
        if removed > 0 {
            lane.round_robin.retain(|queued| queued != account_id);
            lane.queued = lane.queued.saturating_sub(removed);
            self.queued = self.queued.saturating_sub(removed);
            self.rejected_stale = self
                .rejected_stale
                .saturating_add(u64::try_from(removed).unwrap_or(u64::MAX));
        }
        removed
    }
}

/// Thread-safe, bounded signer admission and concurrency controller.
#[derive(Clone, Debug)]
pub struct SignerLanes {
    inner: Arc<Mutex<SignerInner>>,
}

impl SignerLanes {
    /// Creates signer lanes after validating every capacity.
    ///
    /// # Errors
    ///
    /// Returns [`SignerConfigError`] when a capacity is zero or inconsistent.
    pub fn new(config: SignerLanesConfig) -> Result<Self, SignerConfigError> {
        if config.lane_count == 0 {
            return Err(SignerConfigError::NoLanes);
        }
        if config.queue_capacity == 0 {
            return Err(SignerConfigError::NoQueueCapacity);
        }
        if config.per_account_capacity == 0 {
            return Err(SignerConfigError::NoAccountCapacity);
        }
        if config.per_account_capacity > config.queue_capacity {
            return Err(SignerConfigError::AccountCapacityExceedsGlobal);
        }

        let lanes = std::iter::repeat_with(LaneState::default)
            .take(config.lane_count)
            .collect();
        Ok(Self {
            inner: Arc::new(Mutex::new(SignerInner {
                config,
                accepting: true,
                scopes: HashMap::new(),
                incarnations: HashMap::new(),
                lanes,
                next_lane: 0,
                queued: 0,
                in_flight: 0,
                max_queued: 0,
                max_in_flight: 0,
                admitted: 0,
                rejected_full: 0,
                rejected_invalid: 0,
                rejected_stale: 0,
                rejected_closed: 0,
                completed: 0,
                failed: 0,
                cancelled: 0,
            })),
        })
    }

    /// Installs the coordinator-authoritative scope for one account.
    ///
    /// Already queued work from a different fence or permission snapshot is
    /// removed eagerly so stale work cannot retain scarce signer capacity.
    #[must_use]
    pub fn install_scope(&self, scope: SignerScope) -> usize {
        let mut inner = self.lock();
        let changed = inner.scopes.get(&scope.account_id) != Some(&scope);
        let removed = if changed {
            inner
                .incarnations
                .insert(scope.account_id.clone(), Arc::new(()));
            inner.purge_account(&scope.account_id)
        } else {
            0
        };
        inner.scopes.insert(scope.account_id.clone(), scope);
        removed
    }

    /// Removes an account scope and all of its queued work.
    #[must_use]
    pub fn remove_scope(&self, account_id: &str) -> usize {
        let mut inner = self.lock();
        inner.scopes.remove(account_id);
        inner.incarnations.remove(account_id);
        inner.purge_account(account_id)
    }

    /// Returns the account-sticky lane for diagnostics and worker assignment.
    #[must_use]
    pub fn lane_for(&self, account_id: &str) -> usize {
        let lane_count = self.lock().config.lane_count;
        sticky_lane(account_id, lane_count)
    }

    /// Returns only the queued signer work owned by `account_id`.
    ///
    /// This deliberately excludes other accounts sharing the same sticky lane
    /// so account heartbeat data cannot accidentally report installation-wide
    /// pressure as an account-local fact.
    #[must_use]
    pub fn account_queue_depth(&self, account_id: &str) -> usize {
        let inner = self.lock();
        let lane_index = sticky_lane(account_id, inner.config.lane_count);
        inner.lanes[lane_index]
            .queues
            .get(account_id)
            .map_or(0, VecDeque::len)
    }

    /// Attempts a non-blocking bounded admission.
    #[must_use]
    pub fn admit(&self, job: SignerJob) -> SignerAdmission {
        let mut inner = self.lock();
        if job.durable_id.is_empty() || job.durable_id.len() > MAX_DURABLE_ID_BYTES {
            inner.rejected_invalid = inner.rejected_invalid.saturating_add(1);
            return SignerAdmission::Invalid;
        }
        if !inner.accepting {
            inner.rejected_closed = inner.rejected_closed.saturating_add(1);
            return SignerAdmission::Closed;
        }
        if !inner
            .scopes
            .get(&job.account_id)
            .is_some_and(|scope| scope.matches(&job))
        {
            inner.rejected_stale = inner.rejected_stale.saturating_add(1);
            return SignerAdmission::Stale;
        }
        if inner.queued >= inner.config.queue_capacity {
            inner.rejected_full = inner.rejected_full.saturating_add(1);
            return SignerAdmission::Full {
                scope: SignerFullScope::Global,
                queued: inner.queued,
                capacity: inner.config.queue_capacity,
            };
        }

        let lane_index = sticky_lane(&job.account_id, inner.config.lane_count);
        let account_queued = inner.lanes[lane_index]
            .queues
            .get(&job.account_id)
            .map_or(0, VecDeque::len);
        if account_queued >= inner.config.per_account_capacity {
            inner.rejected_full = inner.rejected_full.saturating_add(1);
            return SignerAdmission::Full {
                scope: SignerFullScope::Account,
                queued: account_queued,
                capacity: inner.config.per_account_capacity,
            };
        }

        {
            let lane = &mut inner.lanes[lane_index];
            let queue = lane.queues.entry(job.account_id.clone()).or_default();
            if queue.is_empty() {
                lane.round_robin.push_back(job.account_id.clone());
            }
            queue.push_back(job);
            lane.queued = lane.queued.saturating_add(1);
        }
        inner.queued = inner.queued.saturating_add(1);
        inner.max_queued = inner.max_queued.max(inner.queued);
        inner.admitted = inner.admitted.saturating_add(1);
        SignerAdmission::Accepted { lane: lane_index }
    }

    /// Acquires the next job from any idle lane using rotating lane preference.
    ///
    /// The returned permit is cancellation-safe: dropping it records a
    /// cancellation and releases the fixed concurrency slot.
    #[must_use]
    pub fn try_acquire(&self) -> Option<SignerPermit> {
        let mut inner = self.lock();
        let lane_count = inner.config.lane_count;
        for offset in 0..lane_count {
            let lane = (inner.next_lane + offset) % lane_count;
            if let Some(job) = inner.pop_lane(lane) {
                inner.next_lane = (lane + 1) % lane_count;
                return Some(SignerPermit {
                    inner: Some(Arc::clone(&self.inner)),
                    lane,
                    incarnation: inner.incarnations.get(&job.account_id)?.clone(),
                    job,
                });
            }
        }
        None
    }

    /// Acquires the next fair job for a specific dedicated lane worker.
    #[must_use]
    pub fn try_acquire_lane(&self, lane: usize) -> Option<SignerPermit> {
        let mut inner = self.lock();
        let job = inner.pop_lane(lane)?;
        Some(SignerPermit {
            inner: Some(Arc::clone(&self.inner)),
            lane,
            incarnation: inner.incarnations.get(&job.account_id)?.clone(),
            job,
        })
    }

    /// Stops new admissions while allowing already queued work to drain.
    pub fn close(&self) {
        self.lock().accepting = false;
    }

    /// Drops every queued job and returns the number left for durable recovery.
    #[must_use]
    pub fn abort_queued(&self) -> usize {
        let mut inner = self.lock();
        let mut removed = 0usize;
        for lane in &mut inner.lanes {
            removed = removed.saturating_add(lane.queued);
            lane.queues.clear();
            lane.round_robin.clear();
            lane.queued = 0;
        }
        inner.queued = 0;
        inner.cancelled = inner
            .cancelled
            .saturating_add(u64::try_from(removed).unwrap_or(u64::MAX));
        removed
    }

    /// Returns whether no queued or executing signer work remains.
    #[must_use]
    pub fn is_drained(&self) -> bool {
        let inner = self.lock();
        inner.queued == 0 && inner.in_flight == 0
    }

    /// Returns a cheap resource and admission snapshot.
    #[must_use]
    pub fn snapshot(&self) -> SignerLanesSnapshot {
        let inner = self.lock();
        SignerLanesSnapshot {
            accepting: inner.accepting,
            lane_count: inner.config.lane_count,
            queue_capacity: inner.config.queue_capacity,
            queued: inner.queued,
            in_flight: inner.in_flight,
            max_queued: inner.max_queued,
            max_in_flight: inner.max_in_flight,
            admitted: inner.admitted,
            rejected_full: inner.rejected_full,
            rejected_invalid: inner.rejected_invalid,
            rejected_stale: inner.rejected_stale,
            rejected_closed: inner.rejected_closed,
            completed: inner.completed,
            failed: inner.failed,
            cancelled: inner.cancelled,
            lanes: inner
                .lanes
                .iter()
                .enumerate()
                .map(|(lane, state)| SignerLaneSnapshot {
                    lane,
                    queued: state.queued,
                    in_flight: state.in_flight,
                    queued_accounts: state.queues.len(),
                })
                .collect(),
        }
    }

    fn lock(&self) -> MutexGuard<'_, SignerInner> {
        self.inner.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

/// RAII execution permit for exactly one signer lane.
#[derive(Debug)]
pub struct SignerPermit {
    inner: Option<Arc<Mutex<SignerInner>>>,
    lane: usize,
    incarnation: Arc<()>,
    job: SignerJob,
}

impl SignerPermit {
    /// Returns the immutable, account-bound signer job.
    #[must_use]
    pub const fn job(&self) -> &SignerJob {
        &self.job
    }

    /// Returns the fixed lane executing this job.
    #[must_use]
    pub const fn lane(&self) -> usize {
        self.lane
    }

    /// Revalidates the job after asynchronous context loading and before signing.
    #[must_use]
    pub fn is_current(&self) -> bool {
        let Some(inner) = &self.inner else {
            return false;
        };
        let inner = inner.lock().unwrap_or_else(PoisonError::into_inner);
        inner
            .scopes
            .get(&self.job.account_id)
            .is_some_and(|scope| scope.matches(&self.job))
            && inner
                .incarnations
                .get(&self.job.account_id)
                .is_some_and(|current| Arc::ptr_eq(current, &self.incarnation))
    }

    /// Records the result and releases the execution slot.
    pub fn finish(mut self, completion: SignerCompletion) {
        self.release(completion);
    }

    fn release(&mut self, completion: SignerCompletion) {
        if let Some(inner) = self.inner.take() {
            inner
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .finish(self.lane, completion);
        }
    }
}

impl Drop for SignerPermit {
    fn drop(&mut self) {
        self.release(SignerCompletion::Cancelled);
    }
}

fn sticky_lane(account_id: &str, lane_count: usize) -> usize {
    let hash = account_id
        .as_bytes()
        .iter()
        .fold(2_166_136_261usize, |hash, byte| {
            hash.wrapping_mul(16_777_619) ^ usize::from(*byte)
        });
    hash % lane_count
}

const fn work_kind_index(kind: WorkKind) -> usize {
    match kind {
        WorkKind::InboundWakeup => 0,
        WorkKind::Reconcile => 1,
        WorkKind::PendingRecovery => 2,
        WorkKind::ManualSend => 3,
        WorkKind::AutomaticReply => 4,
        WorkKind::KeepaliveLease => 5,
        WorkKind::Maintenance => 6,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scope(account_id: &str, generation: u64) -> SignerScope {
        SignerScope {
            account_id: account_id.to_owned(),
            actor_generation: generation,
            credential_generation: generation + 10,
            lease_epoch: generation + 20,
            allowed_kinds: [true; SIGNER_WORK_KIND_COUNT],
        }
    }

    fn job(account_id: &str, durable_id: &str, generation: u64) -> SignerJob {
        job_with_kind(account_id, durable_id, generation, WorkKind::ManualSend)
    }

    fn job_with_kind(
        account_id: &str,
        durable_id: &str,
        generation: u64,
        kind: WorkKind,
    ) -> SignerJob {
        SignerJob {
            account_id: account_id.to_owned(),
            durable_id: durable_id.to_owned(),
            kind,
            actor_generation: generation,
            credential_generation: generation + 10,
            lease_epoch: generation + 20,
            plan_digest: [u8::try_from(generation).unwrap_or(u8::MAX); 32],
        }
    }

    fn lanes(lane_count: usize, capacity: usize, per_account: usize) -> SignerLanes {
        SignerLanes::new(SignerLanesConfig {
            lane_count,
            queue_capacity: capacity,
            per_account_capacity: per_account,
        })
        .expect("valid signer configuration")
    }

    #[test]
    fn removed_scope_never_revalidates_an_old_permit_after_identical_reimport() {
        let lanes = lanes(1, 8, 4);
        let original = scope("account", 1);
        assert_eq!(lanes.install_scope(original.clone()), 0);
        assert!(matches!(
            lanes.admit(job("account", "old", 1)),
            SignerAdmission::Accepted { .. }
        ));
        let permit = lanes.try_acquire().expect("old permit");
        assert_eq!(lanes.remove_scope("account"), 0);
        assert_eq!(lanes.install_scope(original), 0);
        assert!(!permit.is_current());
        drop(permit);
        assert_eq!(lanes.snapshot().cancelled, 1);
    }

    #[test]
    fn one_lane_round_robins_accounts_and_serializes_each_account() {
        let lanes = lanes(1, 8, 4);
        assert_eq!(lanes.install_scope(scope("noisy", 1)), 0);
        assert_eq!(lanes.install_scope(scope("normal", 1)), 0);
        assert_eq!(
            lanes.admit(job("noisy", "noisy-1", 1)),
            SignerAdmission::Accepted { lane: 0 }
        );
        assert!(matches!(
            lanes.admit(job("noisy", "noisy-2", 1)),
            SignerAdmission::Accepted { .. }
        ));
        assert!(matches!(
            lanes.admit(job("normal", "normal-1", 1)),
            SignerAdmission::Accepted { .. }
        ));

        let first = lanes.try_acquire().expect("first permit");
        assert_eq!(first.job().durable_id, "noisy-1");
        assert!(lanes.try_acquire().is_none(), "one lane is serial");
        first.finish(SignerCompletion::Success);

        let second = lanes.try_acquire().expect("second permit");
        assert_eq!(second.job().durable_id, "normal-1");
        second.finish(SignerCompletion::Success);
        let third = lanes.try_acquire().expect("third permit");
        assert_eq!(third.job().durable_id, "noisy-2");
        third.finish(SignerCompletion::Success);
        assert!(lanes.is_drained());
    }

    #[test]
    fn admission_reports_account_global_closed_and_stale_bounds() {
        let lanes = lanes(1, 2, 1);
        assert_eq!(lanes.install_scope(scope("a", 1)), 0);
        assert_eq!(lanes.install_scope(scope("b", 1)), 0);
        assert!(matches!(
            lanes.admit(job("missing", "x", 1)),
            SignerAdmission::Stale
        ));
        assert!(matches!(
            lanes.admit(job("a", "a-1", 1)),
            SignerAdmission::Accepted { .. }
        ));
        assert!(matches!(
            lanes.admit(job("a", "a-2", 1)),
            SignerAdmission::Full {
                scope: SignerFullScope::Account,
                queued: 1,
                capacity: 1
            }
        ));
        assert!(matches!(
            lanes.admit(job("b", "b-1", 1)),
            SignerAdmission::Accepted { .. }
        ));
        assert!(matches!(
            lanes.admit(job("b", "b-2", 1)),
            SignerAdmission::Full {
                scope: SignerFullScope::Global,
                queued: 2,
                capacity: 2
            }
        ));
        lanes.close();
        assert_eq!(lanes.admit(job("a", "closed", 1)), SignerAdmission::Closed);
    }

    #[test]
    fn scope_replacement_purges_queued_stale_jobs() {
        let lanes = lanes(2, 8, 8);
        assert_eq!(lanes.install_scope(scope("account", 1)), 0);
        assert!(matches!(
            lanes.admit(job("account", "old-1", 1)),
            SignerAdmission::Accepted { .. }
        ));
        assert!(matches!(
            lanes.admit(job("account", "old-2", 1)),
            SignerAdmission::Accepted { .. }
        ));
        assert_eq!(lanes.install_scope(scope("account", 2)), 2);
        assert_eq!(lanes.snapshot().queued, 0);
        assert_eq!(lanes.snapshot().rejected_stale, 2);
        assert_eq!(
            lanes.admit(job("account", "old-3", 1)),
            SignerAdmission::Stale
        );
        assert!(matches!(
            lanes.admit(job("account", "new", 2)),
            SignerAdmission::Accepted { .. }
        ));
    }

    #[test]
    fn permission_change_purges_queued_jobs_and_rejects_disallowed_kinds() {
        let lanes = lanes(1, 8, 8);
        assert_eq!(lanes.install_scope(scope("account", 1)), 0);
        assert!(matches!(
            lanes.admit(job("account", "manual", 1)),
            SignerAdmission::Accepted { .. }
        ));
        assert!(matches!(
            lanes.admit(job_with_kind(
                "account",
                "automatic",
                1,
                WorkKind::AutomaticReply,
            )),
            SignerAdmission::Accepted { .. }
        ));

        let mut manual_disabled = scope("account", 1);
        manual_disabled.allowed_kinds[work_kind_index(WorkKind::ManualSend)] = false;
        assert_eq!(lanes.install_scope(manual_disabled), 2);
        assert_eq!(lanes.snapshot().queued, 0);
        assert_eq!(lanes.snapshot().rejected_stale, 2);
        assert_eq!(
            lanes.admit(job("account", "blocked-manual", 1)),
            SignerAdmission::Stale
        );
        assert!(matches!(
            lanes.admit(job_with_kind(
                "account",
                "allowed-automatic",
                1,
                WorkKind::AutomaticReply,
            )),
            SignerAdmission::Accepted { .. }
        ));
    }

    #[test]
    fn invalid_durable_ids_are_rejected_before_hashing_or_queueing() {
        let lanes = lanes(1, 1, 1);
        assert_eq!(lanes.install_scope(scope("account", 1)), 0);
        assert_eq!(lanes.admit(job("account", "", 1)), SignerAdmission::Invalid);
        let oversized = "x".repeat(MAX_DURABLE_ID_BYTES + 1);
        assert_eq!(
            lanes.admit(job("account", &oversized, 1)),
            SignerAdmission::Invalid
        );

        let snapshot = lanes.snapshot();
        assert_eq!(snapshot.queued, 0);
        assert_eq!(snapshot.admitted, 0);
        assert_eq!(snapshot.rejected_invalid, 2);
        assert_eq!(snapshot.rejected_full, 0);
        assert_eq!(snapshot.rejected_stale, 0);
    }

    #[test]
    fn execution_never_exceeds_lane_count_and_drop_releases_permit() {
        let lanes = lanes(2, 16, 8);
        let mut account_by_lane: [Option<String>; 2] = [None, None];
        for number in 0..100 {
            let account = format!("account-{number}");
            let lane = lanes.lane_for(&account);
            if account_by_lane[lane].is_none() {
                account_by_lane[lane] = Some(account);
            }
            if account_by_lane.iter().all(Option::is_some) {
                break;
            }
        }
        let account_zero = account_by_lane[0].clone().expect("account on lane zero");
        let account_one = account_by_lane[1].clone().expect("account on lane one");
        assert_eq!(lanes.install_scope(scope(&account_zero, 1)), 0);
        assert_eq!(lanes.install_scope(scope(&account_one, 1)), 0);
        assert!(matches!(
            lanes.admit(job(&account_zero, "zero", 1)),
            SignerAdmission::Accepted { .. }
        ));
        assert!(matches!(
            lanes.admit(job(&account_one, "one", 1)),
            SignerAdmission::Accepted { .. }
        ));

        let first = lanes.try_acquire().expect("first lane permit");
        let second = lanes.try_acquire().expect("second lane permit");
        assert!(lanes.try_acquire().is_none());
        assert_eq!(lanes.snapshot().in_flight, 2);
        assert_eq!(lanes.snapshot().max_in_flight, 2);
        first.finish(SignerCompletion::Error);
        drop(second);
        let snapshot = lanes.snapshot();
        assert_eq!(snapshot.in_flight, 0);
        assert_eq!(snapshot.failed, 1);
        assert_eq!(snapshot.cancelled, 1);
    }

    #[test]
    fn permit_detects_scope_change_before_signing() {
        let lanes = lanes(1, 4, 4);
        assert_eq!(lanes.install_scope(scope("account", 1)), 0);
        assert!(matches!(
            lanes.admit(job("account", "durable", 1)),
            SignerAdmission::Accepted { .. }
        ));
        let permit = lanes.try_acquire().expect("permit");
        assert!(permit.is_current());
        assert_eq!(lanes.install_scope(scope("account", 2)), 0);
        assert!(!permit.is_current());
        drop(permit);
        assert_eq!(lanes.snapshot().cancelled, 1);
    }

    #[test]
    fn permit_revalidates_kind_permission_before_signing() {
        let lanes = lanes(1, 4, 4);
        assert_eq!(lanes.install_scope(scope("account", 1)), 0);
        assert!(matches!(
            lanes.admit(job_with_kind(
                "account",
                "automatic",
                1,
                WorkKind::AutomaticReply,
            )),
            SignerAdmission::Accepted { .. }
        ));
        let permit = lanes.try_acquire().expect("permit");
        assert!(permit.is_current());

        let mut automatic_disabled = scope("account", 1);
        automatic_disabled.allowed_kinds[work_kind_index(WorkKind::AutomaticReply)] = false;
        assert_eq!(lanes.install_scope(automatic_disabled), 0);
        assert!(!permit.is_current());
        drop(permit);
        assert_eq!(lanes.snapshot().cancelled, 1);
    }

    #[test]
    fn account_queue_depth_never_includes_lane_neighbors() {
        let lanes = lanes(1, 8, 4);
        assert_eq!(lanes.install_scope(scope("a", 1)), 0);
        assert_eq!(lanes.install_scope(scope("b", 1)), 0);
        assert!(matches!(
            lanes.admit(job("a", "a-1", 1)),
            SignerAdmission::Accepted { .. }
        ));
        assert!(matches!(
            lanes.admit(job("b", "b-1", 1)),
            SignerAdmission::Accepted { .. }
        ));
        assert!(matches!(
            lanes.admit(job("b", "b-2", 1)),
            SignerAdmission::Accepted { .. }
        ));

        assert_eq!(lanes.account_queue_depth("a"), 1);
        assert_eq!(lanes.account_queue_depth("b"), 2);
        assert_eq!(lanes.account_queue_depth("missing"), 0);
    }
}
