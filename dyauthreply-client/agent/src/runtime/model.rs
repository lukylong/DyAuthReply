//! Secret-free work identity and observability contracts for the account runtime.
//!
//! Work envelopes deliberately carry only opaque identifiers and fencing
//! generations. Message bodies, cookies, signer material, and request headers
//! belong to account-bound owners and must never enter scheduler queues or
//! health snapshots.

use std::{borrow::Borrow, fmt, str::FromStr};

use serde::{Deserialize, Deserializer, Serialize};
use thiserror::Error;

/// Maximum UTF-8 byte length accepted for an opaque account identifier.
pub const MAX_ACCOUNT_ID_BYTES: usize = 256;
/// Maximum UTF-8 byte length retained for a durable receipt/outbox identifier.
pub const MAX_DURABLE_ID_BYTES: usize = 512;

/// A validated, opaque account identifier.
///
/// The runtime never parses or normalizes the identifier. In particular,
/// leading/trailing whitespace remains significant. Only empty and excessively
/// large values are rejected so accidental unbounded health payloads cannot be
/// created.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(transparent)]
pub struct AccountId(String);

impl AccountId {
    /// Validates and owns an opaque account identifier.
    ///
    /// # Errors
    ///
    /// Returns [`AccountIdError`] when `value` is empty or longer than
    /// [`MAX_ACCOUNT_ID_BYTES`] UTF-8 bytes.
    pub fn new(value: impl Into<String>) -> Result<Self, AccountIdError> {
        let value = value.into();
        if value.is_empty() {
            return Err(AccountIdError::Empty);
        }
        if value.len() > MAX_ACCOUNT_ID_BYTES {
            return Err(AccountIdError::TooLong {
                actual: value.len(),
                maximum: MAX_ACCOUNT_ID_BYTES,
            });
        }
        Ok(Self(value))
    }

    /// Returns the identifier exactly as supplied by its owner.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl AsRef<str> for AccountId {
    fn as_ref(&self) -> &str {
        self.as_str()
    }
}

impl Borrow<str> for AccountId {
    fn borrow(&self) -> &str {
        self.as_str()
    }
}

impl fmt::Display for AccountId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for AccountId {
    type Err = AccountIdError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::new(value)
    }
}

impl TryFrom<String> for AccountId {
    type Error = AccountIdError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl TryFrom<&str> for AccountId {
    type Error = AccountIdError;

    fn try_from(value: &str) -> Result<Self, Self::Error> {
        Self::new(value)
    }
}

impl<'de> Deserialize<'de> for AccountId {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Self::new(value).map_err(serde::de::Error::custom)
    }
}

/// Validation failure for an [`AccountId`].
#[derive(Clone, Copy, Debug, Eq, Error, PartialEq)]
pub enum AccountIdError {
    /// Empty account identifiers cannot be isolated or scheduled safely.
    #[error("account ID must not be empty")]
    Empty,
    /// The identifier exceeds the health/scheduler contract bound.
    #[error("account ID is {actual} bytes; maximum is {maximum} bytes")]
    TooLong { actual: usize, maximum: usize },
}

/// Weighted service class used by the fair dispatcher.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkClass {
    /// Operator-initiated outbound work. Weight: 16.
    Manual,
    /// Inbound wakeups and automatic replies. Weight: 4.
    Automatic,
    /// Reconciliation, recovery, leases, and process maintenance. Weight: 1.
    Background,
}

impl WorkClass {
    /// All classes in deterministic priority/tie-break order.
    pub const ALL: [Self; 3] = [Self::Manual, Self::Automatic, Self::Background];

    /// Returns the frozen weighted-service share for this class.
    #[must_use]
    pub const fn weight(self) -> u16 {
        match self {
            Self::Manual => 16,
            Self::Automatic => 4,
            Self::Background => 1,
        }
    }

    /// Returns the stable array index used by bounded runtime storage.
    #[must_use]
    pub const fn index(self) -> usize {
        match self {
            Self::Manual => 0,
            Self::Automatic => 1,
            Self::Background => 2,
        }
    }
}

/// Executable unit type understood by the transport-neutral runtime.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkKind {
    /// Wake an account after an inbound transport event.
    InboundWakeup,
    /// Reconcile durable inbound state with the remote transport.
    Reconcile,
    /// Recover durable work left unresolved by an earlier owner/run.
    PendingRecovery,
    /// Send an operator-authored reply.
    ManualSend,
    /// Send a rule-generated reply.
    AutomaticReply,
    /// Renew ownership or keep an established account transport alive.
    KeepaliveLease,
    /// Run process/account maintenance that is safe to delay.
    Maintenance,
}

impl WorkKind {
    /// Maps a concrete kind onto the frozen `16:4:1` service classes.
    #[must_use]
    pub const fn class(self) -> WorkClass {
        match self {
            Self::ManualSend => WorkClass::Manual,
            Self::InboundWakeup | Self::AutomaticReply => WorkClass::Automatic,
            Self::Reconcile | Self::PendingRecovery | Self::KeepaliveLease | Self::Maintenance => {
                WorkClass::Background
            }
        }
    }

    /// Returns whether one queued timer of this kind may be replaced by its
    /// latest account-scoped generation without increasing queue depth.
    ///
    /// Message-bearing work is intentionally never coalesced.
    #[must_use]
    pub const fn is_coalescible_timer(self) -> bool {
        matches!(
            self,
            Self::Reconcile | Self::PendingRecovery | Self::KeepaliveLease | Self::Maintenance
        )
    }

    /// Returns whether execution may create a fenced remote side effect.
    #[must_use]
    pub const fn requires_fenced_side_effect(self) -> bool {
        matches!(self, Self::ManualSend | Self::AutomaticReply)
    }

    /// Returns whether dynamic disk pressure may defer this work without
    /// blocking correctness recovery, ownership renewal, or inbound handling.
    #[must_use]
    pub const fn is_disposable_background(self) -> bool {
        matches!(self, Self::Reconcile | Self::Maintenance)
    }
}

/// Generations that reject stale actor, credential, and lease work.
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
pub struct WorkFence {
    pub actor_generation: u64,
    pub credential_generation: u64,
    pub lease_epoch: u64,
}

impl WorkFence {
    /// Tests all three stale-work dimensions at the execution boundary.
    #[must_use]
    pub const fn matches(
        self,
        actor_generation: u64,
        credential_generation: u64,
        lease_epoch: u64,
    ) -> bool {
        self.actor_generation == actor_generation
            && self.credential_generation == credential_generation
            && self.lease_epoch == lease_epoch
    }
}

/// Secret-free work admitted to the central dispatcher.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct WorkEnvelope {
    pub account_id: AccountId,
    /// Opaque durable receipt/outbox/timer identifier used for accounting.
    pub durable_id: String,
    pub kind: WorkKind,
    pub actor_generation: u64,
    pub credential_generation: u64,
    pub lease_epoch: u64,
    /// Milliseconds from the runtime's monotonic process clock.
    pub enqueued_at_ms: u64,
}

impl WorkEnvelope {
    /// Builds an envelope without accepting message or credential material.
    #[must_use]
    pub fn new(
        account_id: AccountId,
        durable_id: impl Into<String>,
        kind: WorkKind,
        fence: WorkFence,
        enqueued_at_ms: u64,
    ) -> Self {
        Self {
            account_id,
            durable_id: durable_id.into(),
            kind,
            actor_generation: fence.actor_generation,
            credential_generation: fence.credential_generation,
            lease_epoch: fence.lease_epoch,
            enqueued_at_ms,
        }
    }

    /// Returns this work's weighted service class.
    #[must_use]
    pub const fn class(&self) -> WorkClass {
        self.kind.class()
    }

    /// Returns all stale-work generations as one comparison value.
    #[must_use]
    pub const fn fence(&self) -> WorkFence {
        WorkFence {
            actor_generation: self.actor_generation,
            credential_generation: self.credential_generation,
            lease_epoch: self.lease_epoch,
        }
    }

    /// Validates the only variable-length identity added after `AccountId`.
    #[must_use]
    pub fn has_bounded_identity(&self) -> bool {
        !self.durable_id.is_empty() && self.durable_id.len() <= MAX_DURABLE_ID_BYTES
    }
}

/// Capacity boundary that rejected a queue admission.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CapacityScope {
    Global,
    Account,
    Class,
}

/// Explicit admission result returned by every runtime ingress.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "status")]
pub enum AdmissionResult {
    Accepted,
    /// A latest-generation timer replaced an older queued timer.
    Coalesced,
    /// The same account/durable identifier is already queued.
    Duplicate,
    /// A generation or lease epoch no longer matches the account actor.
    Stale,
    /// A hard queue bound rejected the item and durable recovery is required.
    Full {
        scope: CapacityScope,
        recovery_needed: bool,
    },
    /// Dynamic storage pressure deliberately deferred disposable work.
    Deferred,
    /// A variable-length identity was empty or exceeded its hard byte bound.
    Invalid,
    /// The runtime has closed ingress for bounded drain.
    Stopping,
    /// The account has no live actor/registry entry.
    UnknownAccount,
}

impl AdmissionResult {
    /// Returns whether ingress consumed or replaced one bounded queue slot.
    #[must_use]
    pub const fn is_admitted(self) -> bool {
        matches!(self, Self::Accepted | Self::Coalesced)
    }
}

/// Per-class hard capacities serialized in operator-facing health.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct WorkClassCapacities {
    pub manual: usize,
    pub automatic: usize,
    pub background: usize,
}

impl WorkClassCapacities {
    /// Returns the capacity for one work class.
    #[must_use]
    pub const fn get(self, class: WorkClass) -> usize {
        match class {
            WorkClass::Manual => self.manual,
            WorkClass::Automatic => self.automatic,
            WorkClass::Background => self.background,
        }
    }
}

/// Live accounting for one work class.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct WorkClassQueueSnapshot {
    pub class: WorkClass,
    pub weight: u16,
    pub depth: usize,
    pub capacity: usize,
    pub ready_accounts: usize,
    pub accepted: u64,
    pub coalesced: u64,
    pub duplicates: u64,
    pub rejected_invalid: u64,
    pub rejected_full: u64,
    pub dispatched: u64,
    pub cancelled: u64,
    pub last_dispatch_lag_ms: u64,
    pub max_dispatch_lag_ms: u64,
}

/// Complete, secret-free fair-queue health and exact work accounting.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct FairQueueSnapshot {
    pub accepting: bool,
    pub depth: usize,
    pub peak_depth: usize,
    pub capacity: usize,
    pub per_account_capacity: usize,
    pub active_accounts: usize,
    pub peak_account_depth: usize,
    pub accepted: u64,
    pub coalesced: u64,
    pub duplicates: u64,
    pub rejected_invalid: u64,
    pub rejected_full: u64,
    pub rejected_stopping: u64,
    pub dispatched: u64,
    pub cancelled: u64,
    pub recovery_needed: bool,
    pub recovery_needed_count: u64,
    pub classes: [WorkClassQueueSnapshot; 3],
}

impl FairQueueSnapshot {
    /// Proves every slot-creating admission is dispatched, cancelled, or still
    /// resident. Coalesced and duplicate ingress are accounted separately.
    #[must_use]
    pub fn has_exact_work_accounting(&self) -> bool {
        let terminal_or_queued = self
            .dispatched
            .saturating_add(self.cancelled)
            .saturating_add(u64::try_from(self.depth).unwrap_or(u64::MAX));
        self.accepted == terminal_or_queued
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn account_id_is_opaque_but_bounded_and_nonempty() {
        assert_eq!(AccountId::new(""), Err(AccountIdError::Empty));
        assert!(matches!(
            AccountId::new("x".repeat(MAX_ACCOUNT_ID_BYTES + 1)),
            Err(AccountIdError::TooLong { .. })
        ));

        let opaque = AccountId::new(" account / value ").expect("valid opaque ID");
        assert_eq!(opaque.as_str(), " account / value ");
        assert_eq!(opaque.to_string(), " account / value ");
    }

    #[test]
    fn deserialization_enforces_account_id_invariants() {
        assert!(serde_json::from_str::<AccountId>("\"\"").is_err());
        let account: AccountId = serde_json::from_str("\"account-1\"").expect("valid JSON ID");
        assert_eq!(account.as_str(), "account-1");
    }

    #[test]
    fn work_classes_and_timer_coalescing_are_explicit() {
        assert_eq!(WorkKind::ManualSend.class(), WorkClass::Manual);
        assert_eq!(WorkKind::InboundWakeup.class(), WorkClass::Automatic);
        assert_eq!(WorkKind::AutomaticReply.class(), WorkClass::Automatic);
        assert_eq!(WorkKind::Reconcile.class(), WorkClass::Background);
        assert_eq!(WorkKind::PendingRecovery.class(), WorkClass::Background);
        assert_eq!(WorkKind::KeepaliveLease.class(), WorkClass::Background);
        assert_eq!(WorkKind::Maintenance.class(), WorkClass::Background);

        assert!(WorkKind::Reconcile.is_coalescible_timer());
        assert!(WorkKind::PendingRecovery.is_coalescible_timer());
        assert!(WorkKind::KeepaliveLease.is_coalescible_timer());
        assert!(WorkKind::Maintenance.is_coalescible_timer());
        assert!(!WorkKind::ManualSend.is_coalescible_timer());
        assert!(!WorkKind::AutomaticReply.is_coalescible_timer());
        assert!(!WorkKind::InboundWakeup.is_coalescible_timer());
    }

    #[test]
    fn envelope_serialization_contains_no_body_or_credentials() {
        let envelope = WorkEnvelope::new(
            AccountId::new("account-1").unwrap(),
            "outbox-7",
            WorkKind::ManualSend,
            WorkFence {
                actor_generation: 2,
                credential_generation: 3,
                lease_epoch: 5,
            },
            8,
        );
        let json = serde_json::to_string(&envelope).expect("serialize envelope");

        assert!(json.contains("actor_generation"));
        assert!(json.contains("credential_generation"));
        assert!(json.contains("lease_epoch"));
        assert!(!json.contains("cookie"));
        assert!(!json.contains("body"));
        assert!(!json.contains("header"));
        assert!(envelope.fence().matches(2, 3, 5));
        assert!(!envelope.fence().matches(2, 4, 5));
    }

    #[test]
    fn durable_identity_is_nonempty_and_bounded() {
        let account = AccountId::new("account").unwrap();
        let make = |durable_id: String| {
            WorkEnvelope::new(
                account.clone(),
                durable_id,
                WorkKind::ManualSend,
                WorkFence {
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                },
                0,
            )
        };
        assert!(!make(String::new()).has_bounded_identity());
        assert!(make("x".repeat(MAX_DURABLE_ID_BYTES)).has_bounded_identity());
        assert!(!make("x".repeat(MAX_DURABLE_ID_BYTES + 1)).has_bounded_identity());
    }
}
