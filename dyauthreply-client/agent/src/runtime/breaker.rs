//! Deterministic dependency circuit breakers and process-wide reconnect budget.
//!
//! Each transport/signer dependency owns an independent [`CircuitBreaker`].
//! Account business outcomes are represented separately and close a probe as a
//! reachable dependency; only [`DependencyOutcome::TransientFailure`] creates
//! retry backoff.  [`ReconnectBudget`] then caps aggregate retries across all
//! accounts so a shared outage cannot cause a reconnect storm.

use std::{collections::BTreeSet, num::NonZeroU64};

use serde::Serialize;
use thiserror::Error;

use super::timer::{apply_jitter_ms, stable_jitter_offset_ms};

const MAX_JITTER_BASIS_POINTS: u16 = 10_000;
const BASIS_POINTS_DENOMINATOR: u128 = 10_000;

/// Circuit-breaker configuration for one account-bound dependency.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct BreakerConfig {
    /// Consecutive transient failures required to open a closed circuit.
    pub failure_threshold: u32,
    /// Delay used by the first open transition.
    pub base_backoff_ms: u64,
    /// Inclusive cap for exponential backoff after jitter.
    pub max_backoff_ms: u64,
    /// Stable symmetric jitter in basis points, at most 10,000.
    pub jitter_basis_points: u16,
    /// Maximum concurrent probes while half-open.
    pub half_open_max_probes: u16,
}

impl Default for BreakerConfig {
    fn default() -> Self {
        Self {
            failure_threshold: 3,
            base_backoff_ms: 1_000,
            max_backoff_ms: 60_000,
            jitter_basis_points: 2_000,
            half_open_max_probes: 1,
        }
    }
}

/// Invalid circuit-breaker or reconnect-budget configuration.
#[derive(Clone, Copy, Debug, Eq, Error, PartialEq)]
pub enum BreakerConfigError {
    /// A closed circuit would open without observing a failure.
    #[error("failure_threshold must be greater than zero")]
    ZeroFailureThreshold,
    /// A zero delay can spin continuously while a dependency is unavailable.
    #[error("base_backoff_ms must be greater than zero")]
    ZeroBaseBackoff,
    /// The backoff cap is lower than the first delay.
    #[error("max_backoff_ms must be greater than or equal to base_backoff_ms")]
    BackoffCapBelowBase,
    /// Jitter exceeded a symmetric 100% bound.
    #[error("jitter_basis_points must be at most 10000")]
    JitterOutOfRange,
    /// A half-open circuit would never admit a recovery probe.
    #[error("half_open_max_probes must be greater than zero")]
    ZeroHalfOpenProbes,
    /// A token bucket with zero capacity cannot make progress.
    #[error("reconnect budget capacity must be greater than zero")]
    ZeroReconnectCapacity,
    /// A token bucket with zero refill cannot recover after its initial burst.
    #[error("reconnect budget refill_tokens must be greater than zero")]
    ZeroReconnectRefill,
    /// A zero refill interval does not define a finite refill rate.
    #[error("reconnect budget refill_interval_ms must be greater than zero")]
    ZeroReconnectInterval,
}

impl BreakerConfig {
    fn validate(self) -> Result<Self, BreakerConfigError> {
        if self.failure_threshold == 0 {
            return Err(BreakerConfigError::ZeroFailureThreshold);
        }
        if self.base_backoff_ms == 0 {
            return Err(BreakerConfigError::ZeroBaseBackoff);
        }
        if self.max_backoff_ms < self.base_backoff_ms {
            return Err(BreakerConfigError::BackoffCapBelowBase);
        }
        if self.jitter_basis_points > MAX_JITTER_BASIS_POINTS {
            return Err(BreakerConfigError::JitterOutOfRange);
        }
        if self.half_open_max_probes == 0 {
            return Err(BreakerConfigError::ZeroHalfOpenProbes);
        }
        Ok(self)
    }
}

/// Operator-visible circuit phase.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum BreakerPhase {
    /// Normal attempts are admitted and transient failures are counted.
    Closed,
    /// Attempts are rejected until the retry deadline.
    Open,
    /// A bounded number of recovery probes may be in flight.
    HalfOpen,
}

/// Completion classification for a dependency attempt.
///
/// Authentication invalidity, risk control, and other hard business responses
/// prove that the dependency path was reachable.  They must update account
/// state in the caller, not drive transport/signing retries here.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DependencyOutcome {
    /// The dependency completed its requested operation.
    Success,
    /// A timeout, connection, or retryable dependency failure occurred.
    TransientFailure,
    /// Credentials were rejected and the account must stop authenticated work.
    AuthenticationInvalid,
    /// The platform accepted the request path but blocked sending as business policy.
    PlatformRiskControlled,
    /// A non-retryable business response was returned.
    PermanentBusinessFailure,
}

impl DependencyOutcome {
    /// Returns whether this outcome should contribute to dependency backoff.
    #[must_use]
    pub const fn is_transient(self) -> bool {
        matches!(self, Self::TransientFailure)
    }
}

/// Opaque admission token that binds a completion to one breaker generation.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct BreakerPermit {
    generation: u64,
    permit_id: u64,
    probe: bool,
}

impl BreakerPermit {
    /// Returns whether this attempt was admitted as a half-open probe.
    #[must_use]
    pub const fn is_probe(self) -> bool {
        self.probe
    }

    /// Returns the opaque permit ID for correlation without dependency data.
    #[must_use]
    pub const fn id(self) -> u64 {
        self.permit_id
    }
}

/// Admission result for one dependency attempt.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BreakerDecision {
    /// The caller may start an attempt and must later record or abandon it.
    Allowed(BreakerPermit),
    /// The circuit is open until this monotonic deadline.
    Open { retry_at_ms: u64 },
    /// Every configured half-open probe slot is already in flight.
    HalfOpenProbeLimit,
}

/// State transition caused by recording one attempt outcome.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BreakerRecord {
    /// The result was applied without changing the circuit phase.
    Applied,
    /// The result opened the circuit until the supplied deadline.
    Opened { retry_at_ms: u64 },
    /// A successful/reachable probe closed and reset the circuit.
    Closed,
    /// The permit belonged to a previous phase/generation and was ignored.
    IgnoredStale,
}

/// Serializable circuit diagnostics that contain no account credentials.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct BreakerSnapshot {
    pub phase: BreakerPhase,
    pub consecutive_failures: u32,
    pub retry_at_ms: Option<u64>,
    pub half_open_in_flight: u16,
    pub open_round: u32,
    pub last_backoff_ms: u64,
    pub generation: u64,
    pub in_flight_attempts: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum BreakerState {
    Closed { consecutive_failures: u32 },
    Open { retry_at_ms: u64 },
    HalfOpen { in_flight: u16 },
}

/// Deterministic closed/open/half-open breaker for one dependency key.
#[derive(Debug)]
pub struct CircuitBreaker {
    identity: String,
    config: BreakerConfig,
    state: BreakerState,
    generation: u64,
    next_permit_id: u64,
    active_permits: BTreeSet<u64>,
    open_round: u32,
    last_backoff_ms: u64,
}

impl CircuitBreaker {
    /// Creates a breaker whose stable jitter is scoped by `identity`.
    ///
    /// `identity` should combine the opaque account key and dependency kind. It
    /// is retained only in memory and never appears in the health snapshot.
    ///
    /// # Errors
    ///
    /// Returns [`BreakerConfigError`] when the configuration could spin or
    /// prevent all recovery probes.
    pub fn new(
        identity: impl Into<String>,
        config: BreakerConfig,
    ) -> Result<Self, BreakerConfigError> {
        Ok(Self {
            identity: identity.into(),
            config: config.validate()?,
            state: BreakerState::Closed {
                consecutive_failures: 0,
            },
            generation: 1,
            next_permit_id: 0,
            active_permits: BTreeSet::new(),
            open_round: 0,
            last_backoff_ms: 0,
        })
    }

    /// Admits a normal attempt, starts a due half-open phase, or rejects it.
    pub fn try_acquire(&mut self, now_ms: u64) -> BreakerDecision {
        match self.state {
            BreakerState::Closed { .. } => {
                let permit = self.allocate_permit(false);
                BreakerDecision::Allowed(permit)
            }
            BreakerState::Open { retry_at_ms } if now_ms < retry_at_ms => {
                BreakerDecision::Open { retry_at_ms }
            }
            BreakerState::Open { .. } => {
                self.bump_generation();
                self.state = BreakerState::HalfOpen { in_flight: 1 };
                let permit = self.allocate_permit(true);
                BreakerDecision::Allowed(permit)
            }
            BreakerState::HalfOpen { in_flight }
                if in_flight >= self.config.half_open_max_probes =>
            {
                BreakerDecision::HalfOpenProbeLimit
            }
            BreakerState::HalfOpen { in_flight } => {
                self.state = BreakerState::HalfOpen {
                    in_flight: in_flight.saturating_add(1),
                };
                let permit = self.allocate_permit(true);
                BreakerDecision::Allowed(permit)
            }
        }
    }

    /// Records one admitted attempt exactly against its breaker generation.
    ///
    /// Only transient outcomes open the breaker.  Business/auth/risk outcomes
    /// reset it because retry policy belongs to account state, not transport.
    pub fn record_outcome(
        &mut self,
        permit: BreakerPermit,
        outcome: DependencyOutcome,
        now_ms: u64,
    ) -> BreakerRecord {
        if permit.generation != self.generation {
            return BreakerRecord::IgnoredStale;
        }
        if !self.active_permits.remove(&permit.permit_id) {
            return BreakerRecord::IgnoredStale;
        }

        match self.state {
            BreakerState::Closed {
                consecutive_failures,
            } if !permit.probe => {
                if outcome.is_transient() {
                    let failures = consecutive_failures.saturating_add(1);
                    if failures >= self.config.failure_threshold {
                        let retry_at_ms = self.open_after_failure(now_ms);
                        BreakerRecord::Opened { retry_at_ms }
                    } else {
                        self.state = BreakerState::Closed {
                            consecutive_failures: failures,
                        };
                        BreakerRecord::Applied
                    }
                } else {
                    self.mark_closed_reachable();
                    BreakerRecord::Closed
                }
            }
            BreakerState::HalfOpen { .. } if permit.probe => {
                if outcome.is_transient() {
                    let retry_at_ms = self.open_after_failure(now_ms);
                    BreakerRecord::Opened { retry_at_ms }
                } else {
                    self.reset_closed();
                    BreakerRecord::Closed
                }
            }
            BreakerState::Closed { .. }
            | BreakerState::Open { .. }
            | BreakerState::HalfOpen { .. } => BreakerRecord::IgnoredStale,
        }
    }

    /// Releases an unfinished half-open probe after cancellation or timeout.
    ///
    /// Abandoning a normal closed-state attempt requires no breaker mutation.
    /// The call returns `false` for stale or phase-mismatched permits.
    pub fn abandon(&mut self, permit: BreakerPermit) -> bool {
        if permit.generation != self.generation {
            return false;
        }
        if !self.active_permits.remove(&permit.permit_id) {
            return false;
        }
        match self.state {
            BreakerState::HalfOpen { in_flight } if permit.probe && in_flight > 0 => {
                self.state = BreakerState::HalfOpen {
                    in_flight: in_flight - 1,
                };
                true
            }
            BreakerState::Closed { .. } if !permit.probe => true,
            BreakerState::Closed { .. }
            | BreakerState::Open { .. }
            | BreakerState::HalfOpen { .. } => false,
        }
    }

    /// Resets the breaker after an explicit dependency/configuration reset.
    pub fn reset(&mut self) {
        self.reset_closed();
    }

    /// Returns the current circuit phase.
    #[must_use]
    pub const fn phase(&self) -> BreakerPhase {
        match self.state {
            BreakerState::Closed { .. } => BreakerPhase::Closed,
            BreakerState::Open { .. } => BreakerPhase::Open,
            BreakerState::HalfOpen { .. } => BreakerPhase::HalfOpen,
        }
    }

    /// Returns a credential-free health snapshot.
    #[must_use]
    pub fn snapshot(&self) -> BreakerSnapshot {
        let (consecutive_failures, retry_at_ms, half_open_in_flight) = match self.state {
            BreakerState::Closed {
                consecutive_failures,
            } => (consecutive_failures, None, 0),
            BreakerState::Open { retry_at_ms } => (0, Some(retry_at_ms), 0),
            BreakerState::HalfOpen { in_flight } => (0, None, in_flight),
        };
        BreakerSnapshot {
            phase: self.phase(),
            consecutive_failures,
            retry_at_ms,
            half_open_in_flight,
            open_round: self.open_round,
            last_backoff_ms: self.last_backoff_ms,
            generation: self.generation,
            in_flight_attempts: self.active_permits.len(),
        }
    }

    fn allocate_permit(&mut self, probe: bool) -> BreakerPermit {
        self.next_permit_id = self.next_permit_id.wrapping_add(1);
        if self.next_permit_id == 0 {
            self.next_permit_id = 1;
        }
        let permit = BreakerPermit {
            generation: self.generation,
            permit_id: self.next_permit_id,
            probe,
        };
        self.active_permits.insert(permit.permit_id);
        permit
    }

    fn open_after_failure(&mut self, now_ms: u64) -> u64 {
        let delay_ms = self.backoff_for_round(self.open_round);
        let retry_at_ms = now_ms.saturating_add(delay_ms);
        self.open_round = self.open_round.saturating_add(1);
        self.last_backoff_ms = delay_ms;
        self.bump_generation();
        self.state = BreakerState::Open { retry_at_ms };
        retry_at_ms
    }

    fn backoff_for_round(&self, round: u32) -> u64 {
        let shift = round.min(u64::BITS - 1);
        let exponential = self
            .config
            .base_backoff_ms
            .checked_shl(shift)
            .unwrap_or(self.config.max_backoff_ms)
            .min(self.config.max_backoff_ms);
        let amplitude = u128::from(exponential)
            .saturating_mul(u128::from(self.config.jitter_basis_points))
            / BASIS_POINTS_DENOMINATOR;
        let amplitude = u64::try_from(amplitude).unwrap_or(u64::MAX);
        let purpose = format!("breaker-open-{round}");
        let jitter = stable_jitter_offset_ms(&self.identity, &purpose, amplitude);
        apply_jitter_ms(exponential, jitter).clamp(1, self.config.max_backoff_ms)
    }

    fn reset_closed(&mut self) {
        self.bump_generation();
        self.mark_closed_reachable();
    }

    fn mark_closed_reachable(&mut self) {
        self.state = BreakerState::Closed {
            consecutive_failures: 0,
        };
        self.open_round = 0;
        self.last_backoff_ms = 0;
    }

    fn bump_generation(&mut self) {
        self.active_permits.clear();
        self.generation = self.generation.wrapping_add(1);
        if self.generation == 0 {
            self.generation = 1;
        }
    }
}

/// Configuration for the process-wide reconnect token bucket.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct ReconnectBudgetConfig {
    /// Maximum number of whole tokens and initial reconnect burst.
    pub capacity: u32,
    /// Tokens generated during each refill interval.
    pub refill_tokens: u32,
    /// Milliseconds over which `refill_tokens` are generated continuously.
    pub refill_interval_ms: u64,
}

impl Default for ReconnectBudgetConfig {
    fn default() -> Self {
        Self {
            capacity: 10,
            refill_tokens: 5,
            refill_interval_ms: 1_000,
        }
    }
}

impl ReconnectBudgetConfig {
    fn validate(self) -> Result<(Self, NonZeroU64), BreakerConfigError> {
        if self.capacity == 0 {
            return Err(BreakerConfigError::ZeroReconnectCapacity);
        }
        if self.refill_tokens == 0 {
            return Err(BreakerConfigError::ZeroReconnectRefill);
        }
        let Some(interval) = NonZeroU64::new(self.refill_interval_ms) else {
            return Err(BreakerConfigError::ZeroReconnectInterval);
        };
        Ok((self, interval))
    }
}

/// Result of consuming one global reconnect token.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ReconnectDecision {
    /// One token was consumed and the reconnect may start.
    Granted,
    /// No whole token is available before this monotonic deadline.
    Throttled { retry_at_ms: u64 },
}

/// Serializable global reconnect-budget diagnostics.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub struct ReconnectBudgetSnapshot {
    pub capacity: u32,
    pub available_whole_tokens: u32,
    pub refill_tokens: u32,
    pub refill_interval_ms: u64,
    pub granted_total: u64,
    pub throttled_total: u64,
    pub next_token_at_ms: Option<u64>,
}

/// Fixed-point token bucket shared by every account reconnect path.
///
/// Credits are stored as token-millisecond numerator units.  This retains
/// fractional refill exactly without floating-point drift: one token costs
/// `refill_interval_ms` units and each elapsed millisecond adds
/// `refill_tokens` units.
#[derive(Debug)]
pub struct ReconnectBudget {
    config: ReconnectBudgetConfig,
    refill_interval: NonZeroU64,
    credit_units: u128,
    last_refill_ms: u64,
    granted_total: u64,
    throttled_total: u64,
}

impl ReconnectBudget {
    /// Creates a full bucket at `now_ms`.
    ///
    /// # Errors
    ///
    /// Returns [`BreakerConfigError`] when capacity, refill, or interval is zero.
    pub fn new(config: ReconnectBudgetConfig, now_ms: u64) -> Result<Self, BreakerConfigError> {
        let (config, refill_interval) = config.validate()?;
        let credit_units = u128::from(config.capacity) * u128::from(refill_interval.get());
        Ok(Self {
            config,
            refill_interval,
            credit_units,
            last_refill_ms: now_ms,
            granted_total: 0,
            throttled_total: 0,
        })
    }

    /// Consumes one token or returns the exact earliest retry deadline.
    pub fn try_acquire(&mut self, now_ms: u64) -> ReconnectDecision {
        self.refill(now_ms);
        let token_cost = u128::from(self.refill_interval.get());
        if self.credit_units >= token_cost {
            self.credit_units -= token_cost;
            self.granted_total = self.granted_total.saturating_add(1);
            return ReconnectDecision::Granted;
        }

        self.throttled_total = self.throttled_total.saturating_add(1);
        ReconnectDecision::Throttled {
            retry_at_ms: self.next_token_at(now_ms),
        }
    }

    /// Returns current capacity, refill, and accounting diagnostics.
    pub fn snapshot(&mut self, now_ms: u64) -> ReconnectBudgetSnapshot {
        self.refill(now_ms);
        let token_cost = u128::from(self.refill_interval.get());
        let available = (self.credit_units / token_cost).min(u128::from(self.config.capacity));
        let available_whole_tokens = u32::try_from(available).unwrap_or(self.config.capacity);
        let next_token_at_ms = (available_whole_tokens == 0).then(|| self.next_token_at(now_ms));
        ReconnectBudgetSnapshot {
            capacity: self.config.capacity,
            available_whole_tokens,
            refill_tokens: self.config.refill_tokens,
            refill_interval_ms: self.refill_interval.get(),
            granted_total: self.granted_total,
            throttled_total: self.throttled_total,
            next_token_at_ms,
        }
    }

    fn refill(&mut self, now_ms: u64) {
        let elapsed_ms = now_ms.saturating_sub(self.last_refill_ms);
        if elapsed_ms == 0 {
            return;
        }
        let added = u128::from(elapsed_ms).saturating_mul(u128::from(self.config.refill_tokens));
        let capacity_units =
            u128::from(self.config.capacity) * u128::from(self.refill_interval.get());
        self.credit_units = self.credit_units.saturating_add(added).min(capacity_units);
        self.last_refill_ms = now_ms;
    }

    fn next_token_at(&self, now_ms: u64) -> u64 {
        let token_cost = u128::from(self.refill_interval.get());
        let missing = token_cost.saturating_sub(self.credit_units);
        let refill_rate = u128::from(self.config.refill_tokens);
        let wait_ms = missing.saturating_add(refill_rate.saturating_sub(1)) / refill_rate;
        let wait_ms = u64::try_from(wait_ms).unwrap_or(u64::MAX);
        now_ms.saturating_add(wait_ms)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn no_jitter_config() -> BreakerConfig {
        BreakerConfig {
            failure_threshold: 2,
            base_backoff_ms: 100,
            max_backoff_ms: 400,
            jitter_basis_points: 0,
            half_open_max_probes: 1,
        }
    }

    fn acquire(breaker: &mut CircuitBreaker, now_ms: u64) -> BreakerPermit {
        let BreakerDecision::Allowed(permit) = breaker.try_acquire(now_ms) else {
            panic!("expected breaker permit")
        };
        permit
    }

    #[test]
    fn transient_failures_open_then_one_probe_closes_the_circuit() {
        let mut breaker =
            CircuitBreaker::new("account-a/ws", no_jitter_config()).expect("valid config");

        let first = acquire(&mut breaker, 0);
        assert_eq!(
            breaker.record_outcome(first, DependencyOutcome::TransientFailure, 0),
            BreakerRecord::Applied
        );
        let second = acquire(&mut breaker, 1);
        assert_eq!(
            breaker.record_outcome(second, DependencyOutcome::TransientFailure, 1),
            BreakerRecord::Opened { retry_at_ms: 101 }
        );
        assert_eq!(
            breaker.try_acquire(100),
            BreakerDecision::Open { retry_at_ms: 101 }
        );

        let probe = acquire(&mut breaker, 101);
        assert!(probe.is_probe());
        assert_eq!(
            breaker.try_acquire(101),
            BreakerDecision::HalfOpenProbeLimit
        );
        assert_eq!(
            breaker.record_outcome(probe, DependencyOutcome::Success, 102),
            BreakerRecord::Closed
        );
        assert_eq!(breaker.phase(), BreakerPhase::Closed);
        assert_eq!(breaker.snapshot().open_round, 0);
    }

    #[test]
    fn failed_probe_uses_exponential_backoff_with_cap() {
        let mut breaker = CircuitBreaker::new(
            "account-a/http",
            BreakerConfig {
                failure_threshold: 1,
                ..no_jitter_config()
            },
        )
        .expect("valid config");

        let initial = acquire(&mut breaker, 0);
        assert_eq!(
            breaker.record_outcome(initial, DependencyOutcome::TransientFailure, 0),
            BreakerRecord::Opened { retry_at_ms: 100 }
        );
        let probe = acquire(&mut breaker, 100);
        assert_eq!(
            breaker.record_outcome(probe, DependencyOutcome::TransientFailure, 100),
            BreakerRecord::Opened { retry_at_ms: 300 }
        );
        let probe = acquire(&mut breaker, 300);
        assert_eq!(
            breaker.record_outcome(probe, DependencyOutcome::TransientFailure, 300),
            BreakerRecord::Opened { retry_at_ms: 700 }
        );
        let probe = acquire(&mut breaker, 700);
        assert_eq!(
            breaker.record_outcome(probe, DependencyOutcome::TransientFailure, 700),
            BreakerRecord::Opened { retry_at_ms: 1_100 }
        );
        assert_eq!(breaker.snapshot().last_backoff_ms, 400);
    }

    #[test]
    fn stable_jitter_repeats_for_the_same_dependency_and_round() {
        let config = BreakerConfig {
            failure_threshold: 1,
            jitter_basis_points: 2_500,
            ..no_jitter_config()
        };
        let mut left = CircuitBreaker::new("account-a/signer", config).expect("valid config");
        let mut right = CircuitBreaker::new("account-a/signer", config).expect("valid config");

        let left_permit = acquire(&mut left, 5_000);
        let right_permit = acquire(&mut right, 5_000);
        let left_result =
            left.record_outcome(left_permit, DependencyOutcome::TransientFailure, 5_000);
        let right_result =
            right.record_outcome(right_permit, DependencyOutcome::TransientFailure, 5_000);

        assert_eq!(left_result, right_result);
        assert!((75..=125).contains(&left.snapshot().last_backoff_ms));
    }

    #[test]
    fn account_business_outcomes_never_drive_dependency_retries() {
        let mut breaker =
            CircuitBreaker::new("account-a/ws", no_jitter_config()).expect("valid config");
        let business_outcomes = [
            DependencyOutcome::AuthenticationInvalid,
            DependencyOutcome::PlatformRiskControlled,
            DependencyOutcome::PermanentBusinessFailure,
        ];

        for outcome in business_outcomes.into_iter().cycle().take(30) {
            let permit = acquire(&mut breaker, 0);
            assert_eq!(
                breaker.record_outcome(permit, outcome, 0),
                BreakerRecord::Closed
            );
        }
        assert_eq!(breaker.phase(), BreakerPhase::Closed);
        assert_eq!(breaker.snapshot().consecutive_failures, 0);
    }

    #[test]
    fn stale_completion_cannot_reopen_a_new_generation() {
        let mut breaker = CircuitBreaker::new(
            "account-a/ws",
            BreakerConfig {
                failure_threshold: 1,
                ..no_jitter_config()
            },
        )
        .expect("valid config");
        let stale = acquire(&mut breaker, 0);
        let opener = acquire(&mut breaker, 0);
        assert!(matches!(
            breaker.record_outcome(opener, DependencyOutcome::TransientFailure, 0),
            BreakerRecord::Opened { .. }
        ));
        assert_eq!(
            breaker.record_outcome(stale, DependencyOutcome::TransientFailure, 1),
            BreakerRecord::IgnoredStale
        );
    }

    #[test]
    fn abandoned_probe_releases_half_open_capacity() {
        let mut breaker = CircuitBreaker::new(
            "account-a/ws",
            BreakerConfig {
                failure_threshold: 1,
                ..no_jitter_config()
            },
        )
        .expect("valid config");
        let opener = acquire(&mut breaker, 0);
        breaker.record_outcome(opener, DependencyOutcome::TransientFailure, 0);
        let probe = acquire(&mut breaker, 100);

        assert!(breaker.abandon(probe));
        assert_eq!(
            breaker.record_outcome(probe, DependencyOutcome::Success, 100),
            BreakerRecord::IgnoredStale
        );
        assert!(matches!(
            breaker.try_acquire(100),
            BreakerDecision::Allowed(new_probe) if new_probe.is_probe()
        ));
    }

    #[test]
    fn an_attempt_completion_is_accounted_at_most_once() {
        let mut breaker =
            CircuitBreaker::new("account-a/ws", no_jitter_config()).expect("valid config");
        let permit = acquire(&mut breaker, 0);

        assert_eq!(
            breaker.record_outcome(permit, DependencyOutcome::TransientFailure, 0),
            BreakerRecord::Applied
        );
        assert_eq!(
            breaker.record_outcome(permit, DependencyOutcome::TransientFailure, 0),
            BreakerRecord::IgnoredStale
        );
        assert_eq!(breaker.snapshot().consecutive_failures, 1);
    }

    #[test]
    fn business_response_from_half_open_probe_closes_breaker() {
        let mut breaker = CircuitBreaker::new(
            "account-a/http",
            BreakerConfig {
                failure_threshold: 1,
                ..no_jitter_config()
            },
        )
        .expect("valid config");
        let opener = acquire(&mut breaker, 0);
        breaker.record_outcome(opener, DependencyOutcome::TransientFailure, 0);
        let probe = acquire(&mut breaker, 100);

        assert_eq!(
            breaker.record_outcome(probe, DependencyOutcome::PlatformRiskControlled, 100),
            BreakerRecord::Closed
        );
        assert_eq!(breaker.phase(), BreakerPhase::Closed);
    }

    #[test]
    fn configured_half_open_probe_limit_is_enforced() {
        let mut breaker = CircuitBreaker::new(
            "account-a/http",
            BreakerConfig {
                failure_threshold: 1,
                half_open_max_probes: 2,
                ..no_jitter_config()
            },
        )
        .expect("valid config");
        let opener = acquire(&mut breaker, 0);
        breaker.record_outcome(opener, DependencyOutcome::TransientFailure, 0);

        let first_probe = acquire(&mut breaker, 100);
        let second_probe = acquire(&mut breaker, 100);
        assert!(first_probe.is_probe());
        assert!(second_probe.is_probe());
        assert_eq!(breaker.snapshot().half_open_in_flight, 2);
        assert_eq!(
            breaker.try_acquire(100),
            BreakerDecision::HalfOpenProbeLimit
        );

        assert_eq!(
            breaker.record_outcome(first_probe, DependencyOutcome::Success, 100),
            BreakerRecord::Closed
        );
        assert_eq!(
            breaker.record_outcome(second_probe, DependencyOutcome::TransientFailure, 101),
            BreakerRecord::IgnoredStale
        );
    }

    #[test]
    fn reconnect_budget_bounds_a_three_hundred_account_storm() {
        let mut budget = ReconnectBudget::new(
            ReconnectBudgetConfig {
                capacity: 20,
                refill_tokens: 10,
                refill_interval_ms: 1_000,
            },
            0,
        )
        .expect("valid budget");

        let mut granted = 0_u64;
        for step in 0..=100 {
            let now_ms = step * 100;
            for _account in 0..300 {
                if budget.try_acquire(now_ms) == ReconnectDecision::Granted {
                    granted += 1;
                }
            }
            let upper_bound = 20 + now_ms * 10 / 1_000;
            assert!(granted <= upper_bound, "step={step} granted={granted}");
        }

        assert_eq!(granted, 120);
        let snapshot = budget.snapshot(10_000);
        assert_eq!(snapshot.available_whole_tokens, 0);
        assert_eq!(snapshot.granted_total, 120);
        assert!(snapshot.throttled_total > 0);
    }

    #[test]
    fn reconnect_budget_reports_fractional_refill_deadline() {
        let mut budget = ReconnectBudget::new(
            ReconnectBudgetConfig {
                capacity: 1,
                refill_tokens: 2,
                refill_interval_ms: 1_000,
            },
            5_000,
        )
        .expect("valid budget");

        assert_eq!(budget.try_acquire(5_000), ReconnectDecision::Granted);
        assert_eq!(
            budget.try_acquire(5_001),
            ReconnectDecision::Throttled { retry_at_ms: 5_500 }
        );
        assert_eq!(
            budget.try_acquire(5_499),
            ReconnectDecision::Throttled { retry_at_ms: 5_500 }
        );
        assert_eq!(budget.try_acquire(5_500), ReconnectDecision::Granted);
    }

    #[test]
    fn invalid_configuration_is_rejected() {
        assert_eq!(
            CircuitBreaker::new(
                "bad",
                BreakerConfig {
                    failure_threshold: 0,
                    ..BreakerConfig::default()
                }
            )
            .expect_err("zero threshold must fail"),
            BreakerConfigError::ZeroFailureThreshold
        );
        assert_eq!(
            ReconnectBudget::new(
                ReconnectBudgetConfig {
                    capacity: 1,
                    refill_tokens: 1,
                    refill_interval_ms: 0,
                },
                0,
            )
            .expect_err("zero interval must fail"),
            BreakerConfigError::ZeroReconnectInterval
        );
    }
}
