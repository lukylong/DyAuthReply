//! Actual work execution contract. One process-wide bounded dispatcher owns
//! these futures; account workers never allocate a process/thread/poll loop.
use super::{account::AccountControl, metrics::RuntimeCounters, model::WorkEnvelope};
use std::{
    future::Future,
    pin::Pin,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::sync::watch;

pub type ExecutionFuture = Pin<Box<dyn Future<Output = ExecutionOutcome> + Send + 'static>>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutionOutcome {
    /// The handler completed its durable business transition. This does not
    /// itself mean a platform message was delivered; the outbox records that.
    Finished,
    /// Work remains durable and must be reconciled/recovered, never blindly resent.
    RecoveryNeeded,
}
pub trait WorkExecutor: Send + Sync + 'static {
    /// Implementations recheck account control before each side effect and use
    /// the persistent lease fence when claiming/committing durable records.
    fn execute(
        &self,
        work: WorkEnvelope,
        control: watch::Receiver<AccountControl>,
    ) -> ExecutionFuture;
}
#[derive(Clone, Copy, Debug)]
pub struct ExecutionConfig {
    pub max_in_flight: usize,
    pub timeout: Duration,
}
impl ExecutionConfig {
    /// # Errors
    /// Rejects unbounded concurrency/timeouts or unusably small deadlines.
    pub fn validate(self) -> anyhow::Result<Self> {
        anyhow::ensure!(
            (1..=64).contains(&self.max_in_flight),
            "execution concurrency must be 1..=64"
        );
        anyhow::ensure!(
            (Duration::from_millis(100)..=Duration::from_secs(120)).contains(&self.timeout),
            "execution timeout must be 100ms..=120s"
        );
        Ok(self)
    }
}
#[derive(Default)]
pub(super) struct ExecutionCounters {
    pub active: AtomicUsize,
    pub completed: AtomicUsize,
    pub recovery_needed: AtomicUsize,
}
/// Owns accounting even when a handler panics or the dispatcher is aborted.
/// The dispatcher constructs this before spawning, closing the drain race.
pub(super) struct ExecutionGuard {
    counters: Arc<ExecutionCounters>,
    runtime: Arc<RuntimeCounters>,
    settled: bool,
}
impl ExecutionGuard {
    pub fn new(counters: Arc<ExecutionCounters>, runtime: Arc<RuntimeCounters>) -> Self {
        counters.active.fetch_add(1, Ordering::AcqRel);
        Self {
            counters,
            runtime,
            settled: false,
        }
    }
    fn settle(&mut self, outcome: ExecutionOutcome) {
        self.settled = true;
        match outcome {
            ExecutionOutcome::Finished => {
                self.counters.completed.fetch_add(1, Ordering::AcqRel);
            }
            ExecutionOutcome::RecoveryNeeded => self.recovery(),
        }
    }
    fn recovery(&self) {
        self.counters.recovery_needed.fetch_add(1, Ordering::AcqRel);
        self.runtime.add_unresolved_work(1);
    }
}
impl Drop for ExecutionGuard {
    fn drop(&mut self) {
        if !self.settled {
            self.recovery();
        }
        self.counters.active.fetch_sub(1, Ordering::AcqRel);
    }
}
pub(super) async fn execute_guarded(
    executor: Arc<dyn WorkExecutor>,
    work: WorkEnvelope,
    mut control: watch::Receiver<AccountControl>,
    timeout: Duration,
    mut guard: ExecutionGuard,
) {
    let allowed = |c: AccountControl| c.matches(&work) && c.allows(work.kind);
    let initial = *control.borrow();
    if !allowed(initial) || control.has_changed().is_err() {
        return;
    }
    // A capability update may be this handler's own persisted send result.
    // Let it finish the acknowledgement path. The protocol sender still reads
    // the live capability before every effect; ownership/generation/stop wins.
    let may_settle = |mut current: AccountControl| {
        current.state.send = initial.state.send;
        allowed(current)
    };
    let deadline = tokio::time::Instant::now() + timeout;
    let mut future = executor.execute(work.clone(), control.clone());
    loop {
        tokio::select! {
            biased;
            changed=control.changed()=>{
                if changed.is_err()||!may_settle(*control.borrow_and_update()) {return;}
            }
            ()=tokio::time::sleep_until(deadline)=>{return;}
            outcome=&mut future=>{guard.settle(outcome);return;}
        }
    }
}
