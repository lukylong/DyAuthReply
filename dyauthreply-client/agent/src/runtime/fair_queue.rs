//! Globally and per-account bounded weighted fair queue.
//!
//! Each class maintains an active-account ring and dispatches one item per
//! account turn. Smooth weighted round-robin selects classes at the frozen
//! `16:4:1` ratio, while an explicit manual burst cap guarantees ready
//! automatic/background work cannot be starved.

use std::collections::{HashMap, HashSet, VecDeque};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use super::model::{
    AccountId, AdmissionResult, CapacityScope, FairQueueSnapshot, WorkClass, WorkClassCapacities,
    WorkClassQueueSnapshot, WorkEnvelope,
};

/// Validated hard limits for [`FairQueue`].
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct FairQueueConfig {
    pub global_capacity: usize,
    pub per_account_capacity: usize,
    pub class_capacities: WorkClassCapacities,
    /// Maximum consecutive manual dispatches while another class is ready.
    pub max_manual_burst: usize,
}

impl Default for FairQueueConfig {
    fn default() -> Self {
        Self {
            global_capacity: 4_096,
            per_account_capacity: 64,
            class_capacities: WorkClassCapacities {
                manual: 2_048,
                automatic: 1_536,
                background: 512,
            },
            max_manual_burst: 8,
        }
    }
}

impl FairQueueConfig {
    /// Verifies that every queue boundary is finite, nonzero, and reachable.
    ///
    /// # Errors
    ///
    /// Returns [`FairQueueConfigError`] for a zero capacity/burst, when an
    /// account capacity exceeds the global bound, or when class capacities
    /// cannot collectively reach the configured global bound.
    pub fn validate(self) -> Result<Self, FairQueueConfigError> {
        if self.global_capacity == 0 {
            return Err(FairQueueConfigError::ZeroGlobalCapacity);
        }
        if self.per_account_capacity == 0 {
            return Err(FairQueueConfigError::ZeroAccountCapacity);
        }
        if self.per_account_capacity > self.global_capacity {
            return Err(FairQueueConfigError::AccountExceedsGlobal);
        }
        for class in WorkClass::ALL {
            if self.class_capacities.get(class) == 0 {
                return Err(FairQueueConfigError::ZeroClassCapacity { class });
            }
        }
        let combined = self
            .class_capacities
            .manual
            .saturating_add(self.class_capacities.automatic)
            .saturating_add(self.class_capacities.background);
        if combined < self.global_capacity {
            return Err(FairQueueConfigError::ClassesBelowGlobal);
        }
        if self.max_manual_burst == 0 {
            return Err(FairQueueConfigError::ZeroManualBurst);
        }
        Ok(self)
    }
}

/// Invalid [`FairQueueConfig`] boundary.
#[derive(Clone, Copy, Debug, Eq, Error, PartialEq)]
pub enum FairQueueConfigError {
    #[error("global queue capacity must be nonzero")]
    ZeroGlobalCapacity,
    #[error("per-account queue capacity must be nonzero")]
    ZeroAccountCapacity,
    #[error("per-account queue capacity must not exceed global capacity")]
    AccountExceedsGlobal,
    #[error("{class:?} class queue capacity must be nonzero")]
    ZeroClassCapacity { class: WorkClass },
    #[error("combined class capacities must be at least the global capacity")]
    ClassesBelowGlobal,
    #[error("manual burst bound must be nonzero")]
    ZeroManualBurst,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct DurableKey {
    account_id: AccountId,
    durable_id: String,
}

impl DurableKey {
    fn from_envelope(envelope: &WorkEnvelope) -> Self {
        Self {
            account_id: envelope.account_id.clone(),
            durable_id: envelope.durable_id.clone(),
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct ClassCounters {
    accepted: u64,
    coalesced: u64,
    duplicates: u64,
    rejected_invalid: u64,
    rejected_full: u64,
    dispatched: u64,
    cancelled: u64,
    last_dispatch_lag_ms: u64,
    max_dispatch_lag_ms: u64,
}

struct ClassQueue {
    class: WorkClass,
    capacity: usize,
    depth: usize,
    by_account: HashMap<AccountId, VecDeque<WorkEnvelope>>,
    ready_accounts: VecDeque<AccountId>,
    counters: ClassCounters,
}

impl ClassQueue {
    fn new(class: WorkClass, capacity: usize) -> Self {
        Self {
            class,
            capacity,
            depth: 0,
            by_account: HashMap::new(),
            ready_accounts: VecDeque::new(),
            counters: ClassCounters::default(),
        }
    }

    fn find_coalescible_mut(&mut self, envelope: &WorkEnvelope) -> Option<&mut WorkEnvelope> {
        if !envelope.kind.is_coalescible_timer() {
            return None;
        }
        self.by_account
            .get_mut(&envelope.account_id)?
            .iter_mut()
            .find(|queued| queued.kind == envelope.kind)
    }

    fn push(&mut self, envelope: WorkEnvelope) {
        let account_id = envelope.account_id.clone();
        let queue = self.by_account.entry(account_id.clone()).or_default();
        if queue.is_empty() {
            self.ready_accounts.push_back(account_id);
        }
        queue.push_back(envelope);
        self.depth += 1;
    }

    fn pop(&mut self) -> Option<WorkEnvelope> {
        let account_id = self.ready_accounts.pop_front()?;
        let mut remove_account = false;
        let envelope = {
            let queue = self
                .by_account
                .get_mut(&account_id)
                .expect("ready account must own a queue");
            let envelope = queue
                .pop_front()
                .expect("ready account queue must be nonempty");
            if queue.is_empty() {
                remove_account = true;
            } else {
                self.ready_accounts.push_back(account_id.clone());
            }
            envelope
        };
        if remove_account {
            self.by_account.remove(&account_id);
        }
        self.depth -= 1;
        Some(envelope)
    }

    fn remove_account(&mut self, account_id: &AccountId) -> Vec<WorkEnvelope> {
        let removed = self
            .by_account
            .remove(account_id)
            .map(VecDeque::into_iter)
            .into_iter()
            .flatten()
            .collect::<Vec<_>>();
        if !removed.is_empty() {
            self.ready_accounts.retain(|ready| ready != account_id);
            self.depth -= removed.len();
        }
        removed
    }

    fn snapshot(&self) -> WorkClassQueueSnapshot {
        WorkClassQueueSnapshot {
            class: self.class,
            weight: self.class.weight(),
            depth: self.depth,
            capacity: self.capacity,
            ready_accounts: self.ready_accounts.len(),
            accepted: self.counters.accepted,
            coalesced: self.counters.coalesced,
            duplicates: self.counters.duplicates,
            rejected_invalid: self.counters.rejected_invalid,
            rejected_full: self.counters.rejected_full,
            dispatched: self.counters.dispatched,
            cancelled: self.counters.cancelled,
            last_dispatch_lag_ms: self.counters.last_dispatch_lag_ms,
            max_dispatch_lag_ms: self.counters.max_dispatch_lag_ms,
        }
    }
}

/// Bounded weighted queue with per-class account round-robin service.
pub struct FairQueue {
    config: FairQueueConfig,
    classes: [ClassQueue; 3],
    account_depths: HashMap<AccountId, usize>,
    durable_keys: HashSet<DurableKey>,
    current_weights: [i64; 3],
    consecutive_manual: usize,
    depth: usize,
    peak_depth: usize,
    peak_account_depth: usize,
    accepting: bool,
    accepted: u64,
    coalesced: u64,
    duplicates: u64,
    rejected_invalid: u64,
    rejected_full: u64,
    rejected_stopping: u64,
    dispatched: u64,
    cancelled: u64,
    recovery_needed: bool,
    recovery_needed_count: u64,
}

impl FairQueue {
    /// Creates a queue after validating all hard bounds.
    ///
    /// # Errors
    ///
    /// Returns [`FairQueueConfigError`] when any configured bound is invalid.
    pub fn new(config: FairQueueConfig) -> Result<Self, FairQueueConfigError> {
        let config = config.validate()?;
        let classes = std::array::from_fn(|index| {
            let class = WorkClass::ALL[index];
            ClassQueue::new(class, config.class_capacities.get(class))
        });
        Ok(Self {
            config,
            classes,
            account_depths: HashMap::new(),
            durable_keys: HashSet::new(),
            current_weights: [0; 3],
            consecutive_manual: 0,
            depth: 0,
            peak_depth: 0,
            peak_account_depth: 0,
            accepting: true,
            accepted: 0,
            coalesced: 0,
            duplicates: 0,
            rejected_invalid: 0,
            rejected_full: 0,
            rejected_stopping: 0,
            dispatched: 0,
            cancelled: 0,
            recovery_needed: false,
            recovery_needed_count: 0,
        })
    }

    /// Returns the validated immutable hard limits.
    #[must_use]
    pub const fn config(&self) -> FairQueueConfig {
        self.config
    }

    /// Returns current global queue depth.
    #[must_use]
    pub const fn len(&self) -> usize {
        self.depth
    }

    /// Returns whether no work remains queued.
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.depth == 0
    }

    /// Returns queued depth for one account across all classes.
    #[must_use]
    pub fn account_depth(&self, account_id: &AccountId) -> usize {
        self.account_depths.get(account_id).copied().unwrap_or(0)
    }

    /// Returns queued depth for one class.
    #[must_use]
    pub fn class_depth(&self, class: WorkClass) -> usize {
        self.classes[class.index()].depth
    }

    /// Stops external ingress without discarding already admitted work.
    pub const fn stop_accepting(&mut self) {
        self.accepting = false;
    }

    /// Enqueues one secret-free envelope without waiting or growing any bound.
    ///
    /// Duplicate durable identifiers are acknowledged explicitly. Only kinds
    /// marked by [`super::model::WorkKind::is_coalescible_timer`] can replace a
    /// queued item, and replacement happens before capacity checks because it
    /// consumes no new slot.
    pub fn try_enqueue(&mut self, mut envelope: WorkEnvelope) -> AdmissionResult {
        if !envelope.has_bounded_identity() {
            self.rejected_invalid = self.rejected_invalid.saturating_add(1);
            let counters = &mut self.classes[envelope.class().index()].counters;
            counters.rejected_invalid = counters.rejected_invalid.saturating_add(1);
            return AdmissionResult::Invalid;
        }
        if !self.accepting {
            self.rejected_stopping = self.rejected_stopping.saturating_add(1);
            return AdmissionResult::Stopping;
        }

        let durable_key = DurableKey::from_envelope(&envelope);
        if self.durable_keys.contains(&durable_key) {
            self.duplicates = self.duplicates.saturating_add(1);
            let counters = &mut self.classes[envelope.class().index()].counters;
            counters.duplicates = counters.duplicates.saturating_add(1);
            return AdmissionResult::Duplicate;
        }

        let class = envelope.class();
        let class_index = class.index();
        if let Some(queued) = self.classes[class_index].find_coalescible_mut(&envelope) {
            let old_key = DurableKey::from_envelope(queued);
            envelope.enqueued_at_ms = envelope.enqueued_at_ms.min(queued.enqueued_at_ms);
            *queued = envelope;
            self.durable_keys.remove(&old_key);
            self.durable_keys.insert(durable_key);
            self.coalesced = self.coalesced.saturating_add(1);
            self.classes[class_index].counters.coalesced = self.classes[class_index]
                .counters
                .coalesced
                .saturating_add(1);
            return AdmissionResult::Coalesced;
        }

        if self.depth >= self.config.global_capacity {
            return self.reject_full(class_index, CapacityScope::Global);
        }
        let current_account_depth = self.account_depth(&envelope.account_id);
        if current_account_depth >= self.config.per_account_capacity {
            return self.reject_full(class_index, CapacityScope::Account);
        }
        if self.classes[class_index].depth >= self.classes[class_index].capacity {
            return self.reject_full(class_index, CapacityScope::Class);
        }

        let account_id = envelope.account_id.clone();
        self.classes[class_index].push(envelope);
        self.durable_keys.insert(durable_key);
        self.depth += 1;
        self.peak_depth = self.peak_depth.max(self.depth);
        let account_depth = self.account_depths.entry(account_id).or_default();
        *account_depth += 1;
        self.peak_account_depth = self.peak_account_depth.max(*account_depth);
        self.accepted = self.accepted.saturating_add(1);
        self.classes[class_index].counters.accepted = self.classes[class_index]
            .counters
            .accepted
            .saturating_add(1);
        AdmissionResult::Accepted
    }

    /// Dispatches one item according to weighted class service and per-class
    /// account round-robin. `now_ms` uses the same monotonic origin as ingress.
    pub fn pop(&mut self, now_ms: u64) -> Option<WorkEnvelope> {
        self.pop_excluding(now_ms, &HashSet::new())
    }

    /// Keeps busy accounts in place without dequeuing/dropping their durable
    /// work. Other accounts/classes can progress while a slow I/O job runs.
    pub fn pop_excluding(
        &mut self,
        now_ms: u64,
        busy: &HashSet<AccountId>,
    ) -> Option<WorkEnvelope> {
        let ready = std::array::from_fn(|index| {
            self.classes[index]
                .ready_accounts
                .iter()
                .any(|id| !busy.contains(id))
        });
        let class_index = self.select_class(ready)?;
        let position = self.classes[class_index]
            .ready_accounts
            .iter()
            .position(|id| !busy.contains(id))?;
        self.classes[class_index]
            .ready_accounts
            .rotate_left(position);
        let envelope = self.classes[class_index].pop()?;
        let account_id = envelope.account_id.clone();
        self.durable_keys
            .remove(&DurableKey::from_envelope(&envelope));
        self.decrement_account_depth(&account_id);
        self.depth -= 1;
        self.dispatched = self.dispatched.saturating_add(1);
        let lag = now_ms.saturating_sub(envelope.enqueued_at_ms);
        let counters = &mut self.classes[class_index].counters;
        counters.dispatched = counters.dispatched.saturating_add(1);
        counters.last_dispatch_lag_ms = lag;
        counters.max_dispatch_lag_ms = counters.max_dispatch_lag_ms.max(lag);

        if self.classes[class_index].depth == 0 {
            self.current_weights[class_index] = 0;
        }
        if envelope.class() == WorkClass::Manual {
            self.consecutive_manual = self.consecutive_manual.saturating_add(1);
        } else {
            self.consecutive_manual = 0;
        }
        Some(envelope)
    }

    /// Removes every queued item for an actor that was replaced or stopped.
    ///
    /// Returned envelopes let the coordinator classify durable recovery rather
    /// than silently dropping correctness work.
    pub fn remove_account(&mut self, account_id: &AccountId) -> Vec<WorkEnvelope> {
        let mut removed = Vec::new();
        for class_index in 0..self.classes.len() {
            let class_removed = self.classes[class_index].remove_account(account_id);
            let removed_count = u64::try_from(class_removed.len()).unwrap_or(u64::MAX);
            self.classes[class_index].counters.cancelled = self.classes[class_index]
                .counters
                .cancelled
                .saturating_add(removed_count);
            for envelope in &class_removed {
                self.durable_keys
                    .remove(&DurableKey::from_envelope(envelope));
            }
            removed.extend(class_removed);
            if self.classes[class_index].depth == 0 {
                self.current_weights[class_index] = 0;
            }
        }
        self.account_depths.remove(account_id);
        self.depth -= removed.len();
        let removed_count = u64::try_from(removed.len()).unwrap_or(u64::MAX);
        self.cancelled = self.cancelled.saturating_add(removed_count);
        removed
    }

    /// Clears every remaining envelope for bounded shutdown recovery.
    ///
    /// Returned durable identifiers are owned by the caller; the queue records
    /// each slot as cancelled so exact accounting remains true.
    pub fn abort_all(&mut self) -> Vec<WorkEnvelope> {
        let accounts = self.account_depths.keys().cloned().collect::<Vec<_>>();
        accounts
            .into_iter()
            .flat_map(|account_id| self.remove_account(&account_id))
            .collect()
    }

    /// Atomically clears and returns the latched recovery-needed signal.
    pub fn take_recovery_needed(&mut self) -> bool {
        std::mem::take(&mut self.recovery_needed)
    }

    /// Returns a cheap secret-free health/accounting snapshot.
    #[must_use]
    pub fn snapshot(&self) -> FairQueueSnapshot {
        FairQueueSnapshot {
            accepting: self.accepting,
            depth: self.depth,
            peak_depth: self.peak_depth,
            capacity: self.config.global_capacity,
            per_account_capacity: self.config.per_account_capacity,
            active_accounts: self.account_depths.len(),
            peak_account_depth: self.peak_account_depth,
            accepted: self.accepted,
            coalesced: self.coalesced,
            duplicates: self.duplicates,
            rejected_invalid: self.rejected_invalid,
            rejected_full: self.rejected_full,
            rejected_stopping: self.rejected_stopping,
            dispatched: self.dispatched,
            cancelled: self.cancelled,
            recovery_needed: self.recovery_needed,
            recovery_needed_count: self.recovery_needed_count,
            classes: std::array::from_fn(|index| self.classes[index].snapshot()),
        }
    }

    fn reject_full(&mut self, class_index: usize, scope: CapacityScope) -> AdmissionResult {
        self.rejected_full = self.rejected_full.saturating_add(1);
        self.recovery_needed = true;
        self.recovery_needed_count = self.recovery_needed_count.saturating_add(1);
        self.classes[class_index].counters.rejected_full = self.classes[class_index]
            .counters
            .rejected_full
            .saturating_add(1);
        AdmissionResult::Full {
            scope,
            recovery_needed: true,
        }
    }

    fn decrement_account_depth(&mut self, account_id: &AccountId) {
        let should_remove = if let Some(depth) = self.account_depths.get_mut(account_id) {
            *depth -= 1;
            *depth == 0
        } else {
            debug_assert!(false, "dispatched account depth must exist");
            false
        };
        if should_remove {
            self.account_depths.remove(account_id);
        }
    }

    fn select_class(&mut self, ready: [bool; 3]) -> Option<usize> {
        if !ready.into_iter().any(|is_ready| is_ready) {
            if self.depth == 0 {
                self.consecutive_manual = 0;
            }
            return None;
        }

        let non_manual_ready =
            ready[WorkClass::Automatic.index()] || ready[WorkClass::Background.index()];
        let suppress_manual = ready[WorkClass::Manual.index()]
            && non_manual_ready
            && self.consecutive_manual >= self.config.max_manual_burst;

        let eligible = |index: usize| ready[index] && !(index == 0 && suppress_manual);
        let total_weight = WorkClass::ALL
            .into_iter()
            .filter(|class| eligible(class.index()))
            .map(|class| i64::from(class.weight()))
            .sum::<i64>();

        let mut selected = None;
        for class in WorkClass::ALL {
            let index = class.index();
            if !eligible(index) {
                continue;
            }
            self.current_weights[index] =
                self.current_weights[index].saturating_add(i64::from(class.weight()));
            if selected
                .is_none_or(|best: usize| self.current_weights[index] > self.current_weights[best])
            {
                selected = Some(index);
            }
        }

        let selected = selected.expect("at least one ready class must be eligible");
        self.current_weights[selected] =
            self.current_weights[selected].saturating_sub(total_weight);
        Some(selected)
    }
}

#[cfg(test)]
mod tests {
    use std::collections::{BTreeMap, HashSet};

    use super::*;
    use crate::runtime::model::{WorkFence, WorkKind, MAX_DURABLE_ID_BYTES};

    fn account(value: impl AsRef<str>) -> AccountId {
        AccountId::new(value.as_ref()).expect("valid account ID")
    }

    fn work(account_id: &AccountId, durable_id: impl Into<String>, kind: WorkKind) -> WorkEnvelope {
        WorkEnvelope::new(
            account_id.clone(),
            durable_id,
            kind,
            WorkFence {
                actor_generation: 1,
                credential_generation: 2,
                lease_epoch: 3,
            },
            10,
        )
    }

    fn config(global_capacity: usize, per_account_capacity: usize) -> FairQueueConfig {
        FairQueueConfig {
            global_capacity,
            per_account_capacity,
            class_capacities: WorkClassCapacities {
                manual: global_capacity,
                automatic: global_capacity,
                background: global_capacity,
            },
            max_manual_burst: 8,
        }
    }

    #[test]
    fn validates_all_hard_bounds() {
        let invalid = FairQueueConfig {
            global_capacity: 0,
            ..FairQueueConfig::default()
        };
        assert_eq!(
            invalid.validate(),
            Err(FairQueueConfigError::ZeroGlobalCapacity)
        );

        let invalid = FairQueueConfig {
            per_account_capacity: FairQueueConfig::default().global_capacity + 1,
            ..FairQueueConfig::default()
        };
        assert_eq!(
            invalid.validate(),
            Err(FairQueueConfigError::AccountExceedsGlobal)
        );

        let invalid = FairQueueConfig {
            class_capacities: WorkClassCapacities {
                background: 0,
                ..FairQueueConfig::default().class_capacities
            },
            ..FairQueueConfig::default()
        };
        assert_eq!(
            invalid.validate(),
            Err(FairQueueConfigError::ZeroClassCapacity {
                class: WorkClass::Background
            })
        );

        let invalid = FairQueueConfig {
            max_manual_burst: 0,
            ..FairQueueConfig::default()
        };
        assert_eq!(
            invalid.validate(),
            Err(FairQueueConfigError::ZeroManualBurst)
        );
    }

    #[test]
    fn invalid_durable_ids_never_consume_queue_capacity_and_reach_snapshots() {
        let account = account("invalid-durable");
        let mut queue = FairQueue::new(config(1, 1)).unwrap();

        assert_eq!(
            queue.try_enqueue(work(&account, "", WorkKind::ManualSend)),
            AdmissionResult::Invalid
        );
        assert_eq!(
            queue.try_enqueue(work(
                &account,
                "x".repeat(MAX_DURABLE_ID_BYTES + 1),
                WorkKind::ManualSend,
            )),
            AdmissionResult::Invalid
        );

        let snapshot = queue.snapshot();
        assert_eq!(snapshot.depth, 0);
        assert_eq!(snapshot.accepted, 0);
        assert_eq!(snapshot.rejected_invalid, 2);
        assert_eq!(
            snapshot.classes[WorkClass::Manual.index()].rejected_invalid,
            2
        );
        assert!(snapshot.has_exact_work_accounting());
    }

    #[test]
    fn hot_account_cannot_take_two_turns_while_peer_is_ready() {
        let mut queue = FairQueue::new(config(16, 16)).unwrap();
        let hot = account("hot");
        let peer = account("peer");
        for index in 0..4 {
            assert_eq!(
                queue.try_enqueue(work(&hot, format!("hot-{index}"), WorkKind::AutomaticReply)),
                AdmissionResult::Accepted
            );
        }
        queue.try_enqueue(work(&peer, "peer-0", WorkKind::AutomaticReply));

        let accounts = (0..3)
            .map(|now| queue.pop(now).unwrap().account_id)
            .collect::<Vec<_>>();
        assert_eq!(accounts, vec![hot.clone(), peer, hot]);
    }

    #[test]
    fn weighted_classes_hold_sixteen_four_one_share() {
        let mut queue = FairQueue::new(config(1_000, 1_000)).unwrap();
        let weighted_account = account("weighted");
        for index in 0..210 {
            queue.try_enqueue(work(
                &weighted_account,
                format!("manual-{index}"),
                WorkKind::ManualSend,
            ));
            queue.try_enqueue(work(
                &weighted_account,
                format!("auto-{index}"),
                WorkKind::AutomaticReply,
            ));
            queue.try_enqueue(work(
                &weighted_account,
                format!("inbound-{index}"),
                WorkKind::InboundWakeup,
            ));
        }
        // AutomaticReply + InboundWakeup share one class, so add background
        // without coalescing by using distinct account-scoped recovery kinds.
        for index in 0..210 {
            let background_account = account(format!("background-{index}"));
            queue.try_enqueue(work(
                &background_account,
                format!("background-{index}"),
                WorkKind::PendingRecovery,
            ));
        }

        let mut counts = BTreeMap::new();
        for now in 0..210 {
            let class = queue.pop(now).unwrap().class();
            *counts.entry(class).or_insert(0_usize) += 1;
        }
        assert_eq!(counts.get(&WorkClass::Manual), Some(&160));
        assert_eq!(counts.get(&WorkClass::Automatic), Some(&40));
        assert_eq!(counts.get(&WorkClass::Background), Some(&10));
    }

    #[test]
    fn bounded_manual_burst_cannot_starve_other_ready_classes() {
        let mut cfg = config(128, 128);
        cfg.max_manual_burst = 2;
        let mut queue = FairQueue::new(cfg).unwrap();
        let burst_account = account("burst");
        for index in 0..32 {
            queue.try_enqueue(work(
                &burst_account,
                format!("manual-{index}"),
                WorkKind::ManualSend,
            ));
        }
        for index in 0..8 {
            queue.try_enqueue(work(
                &burst_account,
                format!("auto-{index}"),
                WorkKind::AutomaticReply,
            ));
        }
        let background = account("background");
        queue.try_enqueue(work(&background, "recover", WorkKind::PendingRecovery));

        let mut manual_streak = 0;
        let mut saw_auto = false;
        let mut saw_background = false;
        for now in 0..24 {
            let class = queue.pop(now).unwrap().class();
            if class == WorkClass::Manual {
                manual_streak += 1;
                assert!(manual_streak <= 2);
            } else {
                manual_streak = 0;
                saw_auto |= class == WorkClass::Automatic;
                saw_background |= class == WorkClass::Background;
            }
        }
        assert!(saw_auto);
        assert!(saw_background);
    }

    #[test]
    fn global_account_and_class_capacity_failures_are_explicit() {
        let a = account("a");
        let b = account("b");
        let mut queue = FairQueue::new(config(3, 2)).unwrap();
        assert_eq!(
            queue.try_enqueue(work(&a, "a-1", WorkKind::ManualSend)),
            AdmissionResult::Accepted
        );
        assert_eq!(
            queue.try_enqueue(work(&a, "a-2", WorkKind::ManualSend)),
            AdmissionResult::Accepted
        );
        assert_eq!(
            queue.try_enqueue(work(&a, "a-3", WorkKind::ManualSend)),
            AdmissionResult::Full {
                scope: CapacityScope::Account,
                recovery_needed: true
            }
        );
        assert_eq!(
            queue.try_enqueue(work(&b, "b-1", WorkKind::ManualSend)),
            AdmissionResult::Accepted
        );
        assert_eq!(
            queue.try_enqueue(work(&b, "b-2", WorkKind::ManualSend)),
            AdmissionResult::Full {
                scope: CapacityScope::Global,
                recovery_needed: true
            }
        );
        assert!(queue.snapshot().recovery_needed);
        assert_eq!(queue.snapshot().recovery_needed_count, 2);
        assert!(queue.take_recovery_needed());
        assert!(!queue.take_recovery_needed());

        let mut cfg = config(4, 4);
        cfg.class_capacities.background = 1;
        let mut queue = FairQueue::new(cfg).unwrap();
        queue.try_enqueue(work(&a, "reconcile-a", WorkKind::Reconcile));
        assert_eq!(
            queue.try_enqueue(work(&b, "reconcile-b", WorkKind::Reconcile)),
            AdmissionResult::Full {
                scope: CapacityScope::Class,
                recovery_needed: true
            }
        );
    }

    #[test]
    fn only_explicit_timer_kinds_coalesce_and_preserve_oldest_lag() {
        let account = account("timers");
        let mut queue = FairQueue::new(config(8, 8)).unwrap();
        let mut first = work(&account, "reconcile-v1", WorkKind::Reconcile);
        first.enqueued_at_ms = 5;
        let mut replacement = work(&account, "reconcile-v2", WorkKind::Reconcile);
        replacement.enqueued_at_ms = 20;

        assert_eq!(queue.try_enqueue(first), AdmissionResult::Accepted);
        assert_eq!(queue.try_enqueue(replacement), AdmissionResult::Coalesced);
        assert_eq!(queue.len(), 1);
        let popped = queue.pop(30).unwrap();
        assert_eq!(popped.durable_id, "reconcile-v2");
        assert_eq!(popped.enqueued_at_ms, 5);

        assert_eq!(
            queue.try_enqueue(work(&account, "manual-1", WorkKind::ManualSend)),
            AdmissionResult::Accepted
        );
        assert_eq!(
            queue.try_enqueue(work(&account, "manual-2", WorkKind::ManualSend)),
            AdmissionResult::Accepted
        );
        assert_eq!(queue.len(), 2);
        assert_eq!(
            queue.try_enqueue(work(&account, "manual-2", WorkKind::ManualSend)),
            AdmissionResult::Duplicate
        );
    }

    #[test]
    fn coalescing_still_succeeds_at_capacity_without_growing_depth() {
        let account = account("full-timer");
        let mut queue = FairQueue::new(config(1, 1)).unwrap();
        queue.try_enqueue(work(&account, "old", WorkKind::Maintenance));
        assert_eq!(
            queue.try_enqueue(work(&account, "new", WorkKind::Maintenance)),
            AdmissionResult::Coalesced
        );
        assert_eq!(queue.len(), 1);
        assert_eq!(queue.pop(20).unwrap().durable_id, "new");
    }

    #[test]
    fn busy_accounts_keep_work_and_manual_burst_budget() {
        let a = account("busy");
        let b = account("peer");
        let mut cfg = config(16, 8);
        cfg.max_manual_burst = 1;
        let mut queue = FairQueue::new(cfg).unwrap();
        for id in ["first", "second"] {
            queue.try_enqueue(work(&a, id, WorkKind::ManualSend));
        }
        queue.try_enqueue(work(&a, "auto", WorkKind::AutomaticReply));
        assert_eq!(queue.pop(1).unwrap().durable_id, "first");
        let busy = HashSet::from([a.clone()]);
        assert!(queue.pop_excluding(2, &busy).is_none());
        assert_eq!(queue.len(), 2);
        assert_eq!(queue.pop(3).unwrap().kind, WorkKind::AutomaticReply);
        queue.try_enqueue(work(&b, "peer", WorkKind::ManualSend));
        assert_eq!(queue.pop_excluding(4, &busy).unwrap().account_id, b);
        assert_eq!(queue.pop(5).unwrap().durable_id, "second");
        assert!(queue.snapshot().has_exact_work_accounting());
    }

    #[test]
    fn ten_hundred_and_three_hundred_accounts_progress_in_one_round() {
        for account_count in [10_usize, 100, 300] {
            let mut queue = FairQueue::new(config(account_count + 100, 100)).unwrap();
            let hot = account("account-000");
            for index in 0..50 {
                queue.try_enqueue(work(&hot, format!("hot-{index}"), WorkKind::AutomaticReply));
            }
            for index in 1..account_count {
                let account_id = account(format!("account-{index:03}"));
                queue.try_enqueue(work(
                    &account_id,
                    format!("normal-{index}"),
                    WorkKind::AutomaticReply,
                ));
            }

            let first_round = (0..account_count)
                .map(|now| queue.pop(now as u64).unwrap().account_id)
                .collect::<HashSet<_>>();
            assert_eq!(first_round.len(), account_count);
            assert!(first_round.contains(&hot));

            while queue.pop(1_000).is_some() {}
            let snapshot = queue.snapshot();
            assert_eq!(snapshot.depth, 0);
            assert!(snapshot.has_exact_work_accounting());
            assert_eq!(snapshot.accepted, snapshot.dispatched);
            assert_eq!(snapshot.rejected_full, 0);
        }
    }

    #[test]
    fn removal_and_stopping_preserve_exact_accounting() {
        let a = account("remove-a");
        let b = account("remove-b");
        let mut queue = FairQueue::new(config(16, 8)).unwrap();
        queue.try_enqueue(work(&a, "a-manual", WorkKind::ManualSend));
        queue.try_enqueue(work(&a, "a-auto", WorkKind::AutomaticReply));
        queue.try_enqueue(work(&b, "b-auto", WorkKind::AutomaticReply));

        let removed = queue.remove_account(&a);
        assert_eq!(removed.len(), 2);
        assert_eq!(queue.account_depth(&a), 0);
        assert_eq!(queue.account_depth(&b), 1);
        queue.stop_accepting();
        assert_eq!(
            queue.try_enqueue(work(&b, "late", WorkKind::ManualSend)),
            AdmissionResult::Stopping
        );
        queue.pop(100);

        let snapshot = queue.snapshot();
        assert!(snapshot.has_exact_work_accounting());
        assert_eq!(snapshot.accepted, 3);
        assert_eq!(snapshot.cancelled, 2);
        assert_eq!(snapshot.dispatched, 1);
        assert_eq!(snapshot.rejected_stopping, 1);
    }

    #[test]
    fn abort_all_returns_every_durable_envelope_and_preserves_accounting() {
        let first = account("first");
        let second = account("second");
        let mut queue = FairQueue::new(config(8, 4)).unwrap();
        assert_eq!(
            queue.try_enqueue(work(&first, "first-1", WorkKind::ManualSend)),
            AdmissionResult::Accepted
        );
        assert_eq!(
            queue.try_enqueue(work(&second, "second-1", WorkKind::AutomaticReply)),
            AdmissionResult::Accepted
        );

        let mut aborted = queue.abort_all();
        aborted.sort_by(|left, right| left.durable_id.cmp(&right.durable_id));
        assert_eq!(
            aborted
                .iter()
                .map(|work| work.durable_id.as_str())
                .collect::<Vec<_>>(),
            ["first-1", "second-1"]
        );
        assert!(queue.is_empty());
        assert!(queue.snapshot().has_exact_work_accounting());
    }
}
