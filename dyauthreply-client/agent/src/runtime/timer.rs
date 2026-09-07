//! Deterministic state machine for the Agent's single central timer driver.
//!
//! The state machine deliberately accepts an explicit monotonic millisecond
//! value.  The runtime coordinator is responsible for the one Tokio sleep that
//! advances it.  Keeping clock I/O outside this module makes ordering, resume
//! coalescing, and cancellation completely deterministic in tests.

use std::{
    cmp::{Ordering, Reverse},
    collections::{BinaryHeap, HashMap},
    hash::Hash,
    num::NonZeroU64,
};

use serde::Serialize;

const DEFAULT_STALE_ENTRY_SLACK: usize = 64;
const MAX_SIGNED_MILLISECONDS: u64 = 9_223_372_036_854_775_807;
const BASIS_POINTS_DENOMINATOR: u128 = 10_000;
const FNV_OFFSET_BASIS: u64 = 0xcbf2_9ce4_8422_2325;
const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

/// Opaque generation assigned whenever a logical timer key is scheduled.
///
/// Replacing a key returns a new generation.  Consumers can carry this token
/// with queued work and reject a result produced for an older schedule.
#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
pub struct TimerGeneration(u64);

impl TimerGeneration {
    /// Returns the numeric generation for diagnostics and wire-neutral state.
    #[must_use]
    pub const fn get(self) -> u64 {
        self.0
    }
}

/// One logical timer occurrence returned by [`CentralTimer::drain_due`].
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DueTimer<K> {
    /// Logical key supplied at admission.
    pub key: K,
    /// Generation that was current when this occurrence became due.
    pub generation: TimerGeneration,
    /// Original deadline for the occurrence.
    pub scheduled_for_ms: u64,
    /// Monotonic time passed to the drain operation.
    pub observed_at_ms: u64,
    /// Number of periodic occurrences represented by this one event.
    ///
    /// This is one for a normal occurrence.  It is greater than one after a
    /// sleep/resume jump, so the caller can observe missed intervals without
    /// replaying every missed tick.
    pub coalesced_periods: u64,
    /// Next periodic deadline, or `None` for one-shot/overflowed schedules.
    pub next_deadline_ms: Option<u64>,
}

/// Bounded-heap and compaction counters for the central timer.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize)]
pub struct TimerSnapshot {
    /// Number of current logical schedule keys.
    pub live_entries: usize,
    /// Total heap nodes, including bounded lazily-invalidated nodes.
    pub heap_entries: usize,
    /// Current upper-bound estimate for stale heap nodes.
    pub stale_entries: usize,
    /// Number of heap rebuilds caused by replacement/cancellation churn.
    pub compactions: u64,
    /// Number of stale nodes removed by drains, peeks, or compactions.
    pub stale_entries_discarded: u64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct LiveSchedule {
    generation: TimerGeneration,
    deadline_ms: u64,
    period_ms: Option<NonZeroU64>,
}

#[derive(Clone, Debug)]
struct HeapEntry<K> {
    key: K,
    generation: TimerGeneration,
    deadline_ms: u64,
}

impl<K> PartialEq for HeapEntry<K> {
    fn eq(&self, other: &Self) -> bool {
        self.deadline_ms == other.deadline_ms && self.generation == other.generation
    }
}

impl<K> Eq for HeapEntry<K> {}

impl<K> PartialOrd for HeapEntry<K> {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl<K> Ord for HeapEntry<K> {
    fn cmp(&self, other: &Self) -> Ordering {
        self.deadline_ms
            .cmp(&other.deadline_ms)
            .then_with(|| self.generation.cmp(&other.generation))
    }
}

/// Keyed, deterministic state for a single process-wide timer driver.
///
/// Replacements are generation-based, so no heap-wide scan occurs on the hot
/// path.  Lazy stale nodes are periodically compacted and therefore cannot grow
/// in proportion to an unbounded number of reschedules.
#[derive(Debug)]
pub struct CentralTimer<K> {
    live: HashMap<K, LiveSchedule>,
    heap: BinaryHeap<Reverse<HeapEntry<K>>>,
    next_generation: u64,
    stale_entry_slack: usize,
    compactions: u64,
    stale_entries_discarded: u64,
}

impl<K> Default for CentralTimer<K>
where
    K: Clone + Eq + Hash,
{
    fn default() -> Self {
        Self::new()
    }
}

impl<K> CentralTimer<K>
where
    K: Clone + Eq + Hash,
{
    /// Creates an empty timer with the production compaction threshold.
    #[must_use]
    pub fn new() -> Self {
        Self::with_stale_entry_slack(DEFAULT_STALE_ENTRY_SLACK)
    }

    /// Creates an empty timer with a deterministic stale-node allowance.
    ///
    /// A smaller allowance compacts more frequently.  The allowance does not
    /// affect logical timer capacity or delivery behavior.
    #[must_use]
    pub fn with_stale_entry_slack(stale_entry_slack: usize) -> Self {
        Self {
            live: HashMap::new(),
            heap: BinaryHeap::new(),
            next_generation: 0,
            stale_entry_slack,
            compactions: 0,
            stale_entries_discarded: 0,
        }
    }

    /// Adds or replaces a one-shot deadline for `key`.
    ///
    /// Any previously returned generation for the same key becomes stale.
    pub fn schedule_once(&mut self, key: K, deadline_ms: u64) -> TimerGeneration {
        self.schedule(key, deadline_ms, None)
    }

    /// Adds or replaces a periodic deadline for `key`.
    ///
    /// When several periods elapsed during sleep, one due event represents all
    /// elapsed periods and the next deadline is strictly after the observed
    /// time.  This prevents resume-time replay storms and timer spin.
    pub fn schedule_periodic(
        &mut self,
        key: K,
        first_deadline_ms: u64,
        period_ms: NonZeroU64,
    ) -> TimerGeneration {
        self.schedule(key, first_deadline_ms, Some(period_ms))
    }

    /// Removes the current logical schedule for `key`.
    ///
    /// The invalid heap node is removed lazily or by bounded compaction.
    pub fn cancel(&mut self, key: &K) -> bool {
        let removed = self.live.remove(key).is_some();
        if removed {
            self.compact_if_needed();
        }
        removed
    }

    /// Removes every current and stale schedule.
    pub fn clear(&mut self) {
        let discarded = self.heap.len();
        self.live.clear();
        self.heap.clear();
        self.note_stale_discarded(discarded);
    }

    /// Returns the generation currently assigned to `key`.
    #[must_use]
    pub fn generation_for(&self, key: &K) -> Option<TimerGeneration> {
        self.live.get(key).map(|schedule| schedule.generation)
    }

    /// Returns whether `key` currently has a logical schedule.
    #[must_use]
    pub fn contains(&self, key: &K) -> bool {
        self.live.contains_key(key)
    }

    /// Returns the next live deadline after discarding stale heap-front nodes.
    pub fn next_deadline_ms(&mut self) -> Option<u64> {
        self.discard_stale_front();
        self.heap.peek().map(|entry| entry.0.deadline_ms)
    }

    /// Returns every live occurrence due at or before `now_ms`.
    ///
    /// Ordering is deterministic: deadline first, then admission generation.
    pub fn drain_due(&mut self, now_ms: u64) -> Vec<DueTimer<K>> {
        self.drain_due_limited(now_ms, usize::MAX)
    }

    /// Returns at most `limit` live occurrences due at or before `now_ms`.
    ///
    /// The limit lets the async driver yield between large synchronized bursts.
    /// Stale nodes do not consume the limit.
    pub fn drain_due_limited(&mut self, now_ms: u64, limit: usize) -> Vec<DueTimer<K>> {
        let mut due = Vec::new();

        while due.len() < limit {
            let Some(Reverse(front)) = self.heap.peek() else {
                break;
            };
            if front.deadline_ms > now_ms {
                break;
            }

            let Some(Reverse(entry)) = self.heap.pop() else {
                break;
            };
            let Some(schedule) = self.live.get(&entry.key).copied() else {
                self.note_stale_discarded(1);
                continue;
            };
            if schedule.generation != entry.generation || schedule.deadline_ms != entry.deadline_ms
            {
                self.note_stale_discarded(1);
                continue;
            }

            let (coalesced_periods, next_deadline_ms) =
                schedule.period_ms.map_or((1, None), |period| {
                    next_periodic_deadline(entry.deadline_ms, now_ms, period)
                });

            if let Some(next_deadline_ms) = next_deadline_ms {
                let Some(current) = self.live.get_mut(&entry.key) else {
                    self.note_stale_discarded(1);
                    continue;
                };
                current.deadline_ms = next_deadline_ms;
                self.heap.push(Reverse(HeapEntry {
                    key: entry.key.clone(),
                    generation: entry.generation,
                    deadline_ms: next_deadline_ms,
                }));
            } else {
                self.live.remove(&entry.key);
            }

            due.push(DueTimer {
                key: entry.key,
                generation: entry.generation,
                scheduled_for_ms: entry.deadline_ms,
                observed_at_ms: now_ms,
                coalesced_periods,
                next_deadline_ms,
            });
        }

        self.compact_if_needed();
        due
    }

    /// Returns bounded central-timer diagnostics.
    #[must_use]
    pub fn snapshot(&self) -> TimerSnapshot {
        TimerSnapshot {
            live_entries: self.live.len(),
            heap_entries: self.heap.len(),
            stale_entries: self.heap.len().saturating_sub(self.live.len()),
            compactions: self.compactions,
            stale_entries_discarded: self.stale_entries_discarded,
        }
    }

    fn schedule(
        &mut self,
        key: K,
        deadline_ms: u64,
        period_ms: Option<NonZeroU64>,
    ) -> TimerGeneration {
        let generation = self.allocate_generation();
        self.live.insert(
            key.clone(),
            LiveSchedule {
                generation,
                deadline_ms,
                period_ms,
            },
        );
        self.heap.push(Reverse(HeapEntry {
            key,
            generation,
            deadline_ms,
        }));
        self.compact_if_needed();
        generation
    }

    fn allocate_generation(&mut self) -> TimerGeneration {
        let candidate = self.next_generation.wrapping_add(1);
        if candidate != 0 {
            self.next_generation = candidate;
            return TimerGeneration(candidate);
        }

        // On the theoretical u64 wrap boundary, remove every stale node before
        // selecting a collision-free live generation.  The normal hot path
        // never scans live entries.
        self.rebuild_heap();
        let mut wrapped = 1_u64;
        while self
            .live
            .values()
            .any(|schedule| schedule.generation.0 == wrapped)
        {
            wrapped = wrapped.wrapping_add(1);
            if wrapped == 0 {
                wrapped = 1;
            }
        }
        self.next_generation = wrapped;
        TimerGeneration(wrapped)
    }

    fn is_current(&self, entry: &HeapEntry<K>) -> bool {
        self.live.get(&entry.key).is_some_and(|schedule| {
            schedule.generation == entry.generation && schedule.deadline_ms == entry.deadline_ms
        })
    }

    fn discard_stale_front(&mut self) {
        loop {
            let stale = self
                .heap
                .peek()
                .is_some_and(|entry| !self.is_current(&entry.0));
            if !stale {
                return;
            }
            self.heap.pop();
            self.note_stale_discarded(1);
        }
    }

    fn compact_if_needed(&mut self) {
        if self.live.is_empty() {
            if !self.heap.is_empty() {
                let discarded = self.heap.len();
                self.heap.clear();
                self.note_stale_discarded(discarded);
            }
            return;
        }

        let threshold = self
            .live
            .len()
            .saturating_mul(2)
            .saturating_add(self.stale_entry_slack);
        if self.heap.len() > threshold {
            self.rebuild_heap();
        }
    }

    fn rebuild_heap(&mut self) {
        let previous_len = self.heap.len();
        self.heap = self
            .live
            .iter()
            .map(|(key, schedule)| {
                Reverse(HeapEntry {
                    key: key.clone(),
                    generation: schedule.generation,
                    deadline_ms: schedule.deadline_ms,
                })
            })
            .collect();
        self.compactions = self.compactions.saturating_add(1);
        self.note_stale_discarded(previous_len.saturating_sub(self.heap.len()));
    }

    fn note_stale_discarded(&mut self, count: usize) {
        self.stale_entries_discarded = self
            .stale_entries_discarded
            .saturating_add(u64::try_from(count).unwrap_or(u64::MAX));
    }
}

/// Returns a stable symmetric jitter offset for an identity and purpose.
///
/// The hash is explicitly defined rather than using `DefaultHasher`, whose
/// algorithm is not a cross-release persistence contract.  Length-prefixing
/// each component prevents concatenation collisions such as `(ab, c)` and
/// `(a, bc)`.
#[must_use]
pub fn stable_jitter_offset_ms(identity: &str, purpose: &str, amplitude_ms: u64) -> i64 {
    let amplitude_ms = amplitude_ms.min(MAX_SIGNED_MILLISECONDS);
    if amplitude_ms == 0 {
        return 0;
    }

    let hash = stable_hash(&[identity.as_bytes(), purpose.as_bytes()]);
    let span = u128::from(amplitude_ms).saturating_mul(2).saturating_add(1);
    let bucket = u128::from(hash) % span;
    let signed = i128::try_from(bucket).unwrap_or(i128::MAX) - i128::from(amplitude_ms);
    i64::try_from(signed).unwrap_or_else(|_| {
        if signed.is_negative() {
            i64::MIN
        } else {
            i64::MAX
        }
    })
}

/// Applies a signed stable jitter offset to an unsigned millisecond value.
#[must_use]
pub const fn apply_jitter_ms(base_ms: u64, offset_ms: i64) -> u64 {
    if offset_ms.is_negative() {
        base_ms.saturating_sub(offset_ms.unsigned_abs())
    } else {
        base_ms.saturating_add(offset_ms.unsigned_abs())
    }
}

/// Returns `base_ms` with stable bounded basis-point jitter applied.
///
/// Values above 10,000 basis points are clamped to 100%, keeping this helper
/// total for configuration parsing boundaries that validate separately.
#[must_use]
pub fn stable_jittered_delay_ms(
    base_ms: u64,
    identity: &str,
    purpose: &str,
    jitter_basis_points: u16,
) -> u64 {
    let basis_points = u128::from(jitter_basis_points).min(BASIS_POINTS_DENOMINATOR);
    let amplitude = u128::from(base_ms).saturating_mul(basis_points) / BASIS_POINTS_DENOMINATOR;
    let amplitude = u64::try_from(amplitude).unwrap_or(u64::MAX);
    apply_jitter_ms(
        base_ms,
        stable_jitter_offset_ms(identity, purpose, amplitude),
    )
}

fn stable_hash(parts: &[&[u8]]) -> u64 {
    let mut hash = FNV_OFFSET_BASIS;
    for part in parts {
        let length = u64::try_from(part.len()).unwrap_or(u64::MAX);
        for byte in length.to_le_bytes().into_iter().chain(part.iter().copied()) {
            hash ^= u64::from(byte);
            hash = hash.wrapping_mul(FNV_PRIME);
        }
    }
    hash
}

fn next_periodic_deadline(
    scheduled_for_ms: u64,
    observed_at_ms: u64,
    period_ms: NonZeroU64,
) -> (u64, Option<u64>) {
    let elapsed_ms = observed_at_ms.saturating_sub(scheduled_for_ms);
    let elapsed_periods = u128::from(elapsed_ms) / u128::from(period_ms.get()) + 1;
    let next_deadline = u128::from(scheduled_for_ms)
        .saturating_add(elapsed_periods.saturating_mul(u128::from(period_ms.get())));
    let coalesced_periods = u64::try_from(elapsed_periods).unwrap_or(u64::MAX);
    (
        coalesced_periods,
        u64::try_from(next_deadline)
            .ok()
            .filter(|next| *next > observed_at_ms),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replacement_and_cancellation_make_old_generations_harmless() {
        let mut timer = CentralTimer::new();
        let first = timer.schedule_once("account-a", 10);
        let replacement = timer.schedule_once("account-a", 20);

        assert_ne!(first, replacement);
        assert!(timer.drain_due(10).is_empty());
        assert_eq!(timer.next_deadline_ms(), Some(20));
        assert_eq!(
            timer.drain_due(20),
            vec![DueTimer {
                key: "account-a",
                generation: replacement,
                scheduled_for_ms: 20,
                observed_at_ms: 20,
                coalesced_periods: 1,
                next_deadline_ms: None,
            }]
        );

        timer.schedule_once("account-b", 30);
        assert!(timer.cancel(&"account-b"));
        assert!(!timer.cancel(&"account-b"));
        assert!(timer.drain_due(30).is_empty());
        assert_eq!(timer.snapshot().live_entries, 0);
    }

    #[test]
    fn equal_deadlines_preserve_admission_order() {
        let mut timer = CentralTimer::new();
        timer.schedule_once("first", 100);
        timer.schedule_once("second", 100);
        timer.schedule_once("third", 100);

        let keys: Vec<_> = timer
            .drain_due(100)
            .into_iter()
            .map(|event| event.key)
            .collect();
        assert_eq!(keys, vec!["first", "second", "third"]);
    }

    #[test]
    fn one_hundred_thousand_replacements_keep_heap_bounded() {
        let mut timer = CentralTimer::with_stale_entry_slack(8);
        for deadline_ms in 0..100_000 {
            timer.schedule_once(7_u64, deadline_ms);
        }

        let snapshot = timer.snapshot();
        assert_eq!(snapshot.live_entries, 1);
        assert!(snapshot.heap_entries <= 10, "snapshot={snapshot:?}");
        assert!(snapshot.compactions > 0);
        assert!(snapshot.stale_entries_discarded > 99_000);

        let event = timer.drain_due(99_999);
        assert_eq!(event.len(), 1);
        assert_eq!(event[0].scheduled_for_ms, 99_999);
    }

    #[test]
    fn sleep_resume_coalesces_periodic_ticks_once() {
        let mut timer = CentralTimer::new();
        let period = NonZeroU64::new(1_000).expect("test period is non-zero");
        timer.schedule_periodic("heartbeat", 1_000, period);

        let due = timer.drain_due(3_600_000);
        assert_eq!(due.len(), 1);
        assert_eq!(due[0].coalesced_periods, 3_600);
        assert_eq!(due[0].next_deadline_ms, Some(3_601_000));
        assert_eq!(timer.next_deadline_ms(), Some(3_601_000));
        assert!(timer.drain_due(3_600_000).is_empty());
    }

    #[test]
    fn limited_drain_yields_between_due_bursts_without_losing_entries() {
        let mut timer = CentralTimer::new();
        for account in 0..300 {
            timer.schedule_once(account, 50);
        }

        assert_eq!(timer.drain_due_limited(50, 32).len(), 32);
        assert_eq!(timer.snapshot().live_entries, 268);
        assert_eq!(timer.next_deadline_ms(), Some(50));
        assert_eq!(timer.drain_due(50).len(), 268);
    }

    #[test]
    fn stable_jitter_is_repeatable_symmetric_and_bounded() {
        let a = stable_jitter_offset_ms("account-a", "reconnect", 1_000);
        let again = stable_jitter_offset_ms("account-a", "reconnect", 1_000);
        let other_purpose = stable_jitter_offset_ms("account-a", "heartbeat", 1_000);

        assert_eq!(a, again);
        assert_ne!(a, other_purpose);
        assert!((-1_000..=1_000).contains(&a));
        assert_eq!(
            stable_jittered_delay_ms(10_000, "account-a", "ws", 0),
            10_000
        );
        assert!((9_000..=11_000).contains(&stable_jittered_delay_ms(
            10_000,
            "account-a",
            "ws",
            1_000
        )));
    }

    #[test]
    fn periodic_deadline_overflow_turns_into_one_final_occurrence() {
        let mut timer = CentralTimer::new();
        let period = NonZeroU64::new(10).expect("test period is non-zero");
        timer.schedule_periodic("edge", u64::MAX - 5, period);

        let due = timer.drain_due(u64::MAX);
        assert_eq!(due.len(), 1);
        assert_eq!(due[0].next_deadline_ms, None);
        assert_eq!(timer.snapshot().live_entries, 0);
    }
}
