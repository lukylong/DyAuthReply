//! Lock-free process-wide runtime counters used to build live health snapshots.

use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};

/// Counters updated on actor/dispatcher/maintenance hot paths.
#[derive(Debug)]
pub struct RuntimeCounters {
    accepting_work: AtomicBool,
    actor_count: AtomicUsize,
    mailbox_rejected: AtomicU64,
    stale_work_rejected: AtomicU64,
    invalid_work_rejected: AtomicU64,
    dispatched_total: AtomicU64,
    max_fairness_lag_ms: AtomicU64,
    storage_cleanup_runs: AtomicU64,
    deferred_background: AtomicU64,
    unresolved_work: AtomicUsize,
    heartbeat_batches: AtomicU64,
}

impl Default for RuntimeCounters {
    fn default() -> Self {
        Self {
            accepting_work: AtomicBool::new(true),
            actor_count: AtomicUsize::new(0),
            mailbox_rejected: AtomicU64::new(0),
            stale_work_rejected: AtomicU64::new(0),
            invalid_work_rejected: AtomicU64::new(0),
            dispatched_total: AtomicU64::new(0),
            max_fairness_lag_ms: AtomicU64::new(0),
            storage_cleanup_runs: AtomicU64::new(0),
            deferred_background: AtomicU64::new(0),
            unresolved_work: AtomicUsize::new(0),
            heartbeat_batches: AtomicU64::new(0),
        }
    }
}

impl RuntimeCounters {
    #[must_use]
    pub fn accepting_work(&self) -> bool {
        self.accepting_work.load(Ordering::Relaxed)
    }

    pub fn stop_accepting(&self) {
        self.accepting_work.store(false, Ordering::Release);
    }

    pub fn set_actor_count(&self, count: usize) {
        self.actor_count.store(count, Ordering::Relaxed);
    }

    #[must_use]
    pub fn actor_count(&self) -> usize {
        self.actor_count.load(Ordering::Relaxed)
    }

    pub fn note_mailbox_rejected(&self) {
        self.mailbox_rejected.fetch_add(1, Ordering::Relaxed);
    }

    #[must_use]
    pub fn mailbox_rejected(&self) -> u64 {
        self.mailbox_rejected.load(Ordering::Relaxed)
    }

    pub fn note_stale_work(&self) {
        self.stale_work_rejected.fetch_add(1, Ordering::Relaxed);
    }

    #[must_use]
    pub fn stale_work_rejected(&self) -> u64 {
        self.stale_work_rejected.load(Ordering::Relaxed)
    }

    pub fn note_invalid_work(&self) {
        self.invalid_work_rejected.fetch_add(1, Ordering::Relaxed);
    }

    #[must_use]
    pub fn invalid_work_rejected(&self) -> u64 {
        self.invalid_work_rejected.load(Ordering::Relaxed)
    }

    pub fn note_dispatch(&self, lag_ms: u64) {
        self.dispatched_total.fetch_add(1, Ordering::Relaxed);
        self.max_fairness_lag_ms
            .fetch_max(lag_ms, Ordering::Relaxed);
    }

    #[must_use]
    pub fn dispatched_total(&self) -> u64 {
        self.dispatched_total.load(Ordering::Relaxed)
    }

    #[must_use]
    pub fn max_fairness_lag_ms(&self) -> u64 {
        self.max_fairness_lag_ms.load(Ordering::Relaxed)
    }

    pub fn note_storage_cleanup(&self) {
        self.storage_cleanup_runs.fetch_add(1, Ordering::Relaxed);
    }

    #[must_use]
    pub fn storage_cleanup_runs(&self) -> u64 {
        self.storage_cleanup_runs.load(Ordering::Relaxed)
    }

    pub fn note_background_deferred(&self) {
        self.deferred_background.fetch_add(1, Ordering::Relaxed);
    }

    #[must_use]
    pub fn deferred_background(&self) -> u64 {
        self.deferred_background.load(Ordering::Relaxed)
    }

    pub fn set_unresolved_work(&self, count: usize) {
        self.unresolved_work.store(count, Ordering::Relaxed);
    }

    pub fn add_unresolved_work(&self, count: usize) {
        self.unresolved_work.fetch_add(count, Ordering::Relaxed);
    }

    #[must_use]
    pub fn unresolved_work(&self) -> usize {
        self.unresolved_work.load(Ordering::Relaxed)
    }

    pub fn note_heartbeat_batches(&self, count: usize) {
        self.heartbeat_batches
            .fetch_add(u64::try_from(count).unwrap_or(u64::MAX), Ordering::Relaxed);
    }

    #[must_use]
    pub fn heartbeat_batches(&self) -> u64 {
        self.heartbeat_batches.load(Ordering::Relaxed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counters_are_monotonic_and_max_lag_never_regresses() {
        let counters = RuntimeCounters::default();
        counters.set_actor_count(300);
        counters.note_dispatch(12);
        counters.note_dispatch(3);
        counters.note_mailbox_rejected();
        counters.note_stale_work();
        counters.note_invalid_work();
        counters.note_heartbeat_batches(2);
        counters.note_background_deferred();
        counters.stop_accepting();

        assert_eq!(counters.actor_count(), 300);
        assert_eq!(counters.dispatched_total(), 2);
        assert_eq!(counters.max_fairness_lag_ms(), 12);
        assert_eq!(counters.mailbox_rejected(), 1);
        assert_eq!(counters.stale_work_rejected(), 1);
        assert_eq!(counters.invalid_work_rejected(), 1);
        assert_eq!(counters.heartbeat_batches(), 2);
        assert_eq!(counters.deferred_background(), 1);
        assert!(!counters.accepting_work());
    }
}
