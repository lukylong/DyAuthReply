//! Bounded installation-level aggregation of non-secret account status.
//!
//! Producers replace one latest record per account. Network ownership remains
//! outside this module: callers prepare deterministic batches and acknowledge
//! each sequence only after the remote control plane responds.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::state::AccountRuntimeState;

/// Compact health of one account-bound dependency.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DependencyHealth {
    /// Normal attempts are admitted.
    #[default]
    Closed,
    /// Attempts are suppressed until the retry deadline.
    Open,
    /// A bounded recovery probe is in progress.
    HalfOpen,
}

/// Latest non-secret status published by one account actor.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct AccountHeartbeatDelta {
    /// Opaque managed-account identifier.
    pub account_id: String,
    /// Generation of the actor publishing this state.
    pub actor_generation: u64,
    /// Orthogonal runtime axes and their credential/fence generations.
    pub runtime: AccountRuntimeState,
    /// Current bounded actor mailbox depth.
    pub mailbox_depth: u32,
    /// Current signer backlog for this account.
    pub signer_queue_depth: u32,
    /// Last meaningful actor activity in remote/server milliseconds.
    pub last_activity_ms: Option<u64>,
    /// Account transport circuit state.
    pub transport: DependencyHealth,
    /// Account signer circuit state.
    pub signer: DependencyHealth,
}

/// Versioned account state sent inside an aggregate heartbeat.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct AccountHeartbeatState {
    /// Monotonic local revision assigned when the state changed.
    pub revision: u64,
    /// True for an account-removal tombstone.
    pub removed: bool,
    /// Latest account state, absent only for a removal tombstone.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub state: Option<AccountHeartbeatDelta>,
    /// Account ID remains present on tombstones.
    pub account_id: String,
}

/// One deterministic payload for the Step 7 hosted heartbeat adapter.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct HeartbeatBatch {
    /// Non-secret installation identity.
    pub installation_id: String,
    /// Monotonic batch sequence used for acknowledgement.
    pub sequence: u64,
    /// Whether this batch belongs to a periodic full repair snapshot.
    pub full: bool,
    /// Zero-based deterministic shard index.
    pub shard_index: usize,
    /// Total shards produced by this preparation.
    pub shard_count: usize,
    /// Preparation time supplied by the coordinator.
    pub prepared_at_ms: u64,
    /// Account states sorted by account ID.
    pub accounts: Vec<AccountHeartbeatState>,
}

/// Bounds and periodic repair policy for heartbeat aggregation.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct HeartbeatConfig {
    /// Maximum retained latest account records, including dirty tombstones.
    pub max_tracked_accounts: usize,
    /// Maximum account records per network payload.
    pub max_accounts_per_batch: usize,
    /// Maximum serialized JSON bytes per payload.
    pub max_payload_bytes: usize,
    /// Maximum simultaneous unacknowledged batch shards.
    pub max_inflight_batches: usize,
    /// Fallback interval for a complete repair snapshot.
    pub full_refresh_interval_ms: u64,
    /// Maximum UTF-8 byte length of an account identifier.
    pub max_account_id_bytes: usize,
}

impl Default for HeartbeatConfig {
    fn default() -> Self {
        Self {
            max_tracked_accounts: 1_000,
            max_accounts_per_batch: 300,
            max_payload_bytes: 256 * 1_024,
            max_inflight_batches: 16,
            full_refresh_interval_ms: 5 * 60 * 1_000,
            max_account_id_bytes: 256,
        }
    }
}

/// Invalid heartbeat configuration.
#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum HeartbeatConfigError {
    /// An account must fit in the retained map.
    #[error("max_tracked_accounts must be greater than zero")]
    NoTrackedAccounts,
    /// A batch must fit at least one account.
    #[error("max_accounts_per_batch must be greater than zero")]
    NoBatchAccounts,
    /// A payload must have nonzero capacity.
    #[error("max_payload_bytes must be greater than zero")]
    NoPayloadCapacity,
    /// At least one shard must be allowed in flight.
    #[error("max_inflight_batches must be greater than zero")]
    NoInflightCapacity,
    /// Periodic full repair must have a positive interval.
    #[error("full_refresh_interval_ms must be greater than zero")]
    NoFullRefreshInterval,
    /// Account identifiers must have a positive byte bound.
    #[error("max_account_id_bytes must be greater than zero")]
    NoAccountIdCapacity,
}

/// Why a producer update was not stored.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, thiserror::Error)]
pub enum HeartbeatPublishError {
    /// The account identifier is empty or exceeds the configured byte bound.
    #[error("account_id length {actual} is outside 1..={maximum} bytes")]
    InvalidAccountId {
        /// Observed byte length.
        actual: usize,
        /// Configured byte limit.
        maximum: usize,
    },
    /// Retaining another account would exceed the configured account bound.
    #[error("heartbeat account capacity {capacity} is full")]
    Full {
        /// Configured retained-account capacity.
        capacity: usize,
    },
    /// The local monotonic revision space was exhausted.
    #[error("heartbeat revision space exhausted")]
    RevisionExhausted,
}

/// Result of publishing a latest account state.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case", tag = "result")]
pub enum HeartbeatPublish {
    /// A changed state was stored and marked dirty.
    Changed {
        /// Newly assigned monotonic revision.
        revision: u64,
    },
    /// The published state was byte-for-byte equivalent to the latest state.
    Unchanged {
        /// Existing revision retained for the account.
        revision: u64,
    },
    /// The account was already absent and no tombstone was needed.
    AlreadyAbsent,
}

/// Why aggregate batches could not be prepared yet.
#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum HeartbeatPrepareError {
    /// A previous preparation still awaits acknowledgement.
    #[error("{inflight} heartbeat batches still await acknowledgement")]
    Inflight {
        /// Current unacknowledged shard count.
        inflight: usize,
    },
    /// One account record cannot fit the configured payload byte bound.
    #[error("account {account_id} needs {encoded_bytes} bytes, payload limit is {maximum}")]
    RecordTooLarge {
        /// Opaque account identifier.
        account_id: String,
        /// Conservative encoded byte count.
        encoded_bytes: usize,
        /// Configured payload limit.
        maximum: usize,
    },
    /// Deterministic shard count exceeds the bounded in-flight map.
    #[error("heartbeat needs {required} shards, in-flight limit is {maximum}")]
    TooManyShards {
        /// Number of shards required for the snapshot.
        required: usize,
        /// Configured in-flight shard bound.
        maximum: usize,
    },
    /// JSON size measurement unexpectedly failed.
    #[error("heartbeat payload measurement failed: {0}")]
    Serialization(String),
    /// The local monotonic sequence space was exhausted.
    #[error("heartbeat sequence space exhausted")]
    SequenceExhausted,
}

/// Result of acknowledging one prepared sequence.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HeartbeatAck {
    /// Matching revisions were cleared after a successful remote response.
    Applied,
    /// Failure retained every matching revision for retry.
    RetainedForRetry,
    /// The sequence was not currently in flight.
    UnknownSequence,
}

/// Aggregate heartbeat observability without credential or message content.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct HeartbeatSnapshot {
    /// Latest retained account records, including unacknowledged tombstones.
    pub tracked_accounts: usize,
    /// Accounts with changes that still require successful acknowledgement.
    pub dirty_accounts: usize,
    /// Unacknowledged deterministic shards.
    pub inflight_batches: usize,
    /// Next revision that will be assigned.
    pub next_revision: u64,
    /// Next network sequence that will be assigned.
    pub next_sequence: u64,
    /// Accounts included in the most recent preparation.
    pub last_batch_accounts: usize,
    /// Shards included in the most recent preparation.
    pub last_batch_shards: usize,
    /// Most recent full-repair preparation time.
    pub last_full_prepared_at_ms: Option<u64>,
    /// ACK-time anchor from which the next periodic full repair is measured.
    pub full_refresh_anchor_ms: Option<u64>,
    /// Whether a failed full cycle requires an immediate complete retry.
    pub full_retry_forced: bool,
    /// Producer updates rejected by the retained-account bound.
    pub rejected_full: u64,
    /// Failed acknowledgements retained for retry.
    pub failed_acks: u64,
}

#[derive(Clone, Debug)]
struct InflightBatch {
    prepared_at_ms: u64,
    revisions: Vec<(String, u64)>,
}

#[derive(Clone, Copy, Debug)]
struct InflightCycle {
    full: bool,
    failed: bool,
    latest_ack_ms: u64,
}

/// Bounded, deterministic latest-value heartbeat aggregator.
#[derive(Debug)]
pub struct HeartbeatAggregator {
    config: HeartbeatConfig,
    installation_id: String,
    entries: BTreeMap<String, AccountHeartbeatState>,
    dirty: BTreeSet<String>,
    inflight: BTreeMap<u64, InflightBatch>,
    next_revision: u64,
    next_sequence: u64,
    last_batch_accounts: usize,
    last_batch_shards: usize,
    last_full_prepared_at_ms: Option<u64>,
    full_refresh_anchor_ms: Option<u64>,
    full_retry_forced: bool,
    inflight_cycle: Option<InflightCycle>,
    rejected_full: u64,
    failed_acks: u64,
}

impl HeartbeatAggregator {
    /// Creates an empty installation-level aggregator.
    ///
    /// # Errors
    ///
    /// Returns [`HeartbeatConfigError`] if any configured bound is zero.
    pub fn new(
        installation_id: impl Into<String>,
        config: HeartbeatConfig,
    ) -> Result<Self, HeartbeatConfigError> {
        if config.max_tracked_accounts == 0 {
            return Err(HeartbeatConfigError::NoTrackedAccounts);
        }
        if config.max_accounts_per_batch == 0 {
            return Err(HeartbeatConfigError::NoBatchAccounts);
        }
        if config.max_payload_bytes == 0 {
            return Err(HeartbeatConfigError::NoPayloadCapacity);
        }
        if config.max_inflight_batches == 0 {
            return Err(HeartbeatConfigError::NoInflightCapacity);
        }
        if config.full_refresh_interval_ms == 0 {
            return Err(HeartbeatConfigError::NoFullRefreshInterval);
        }
        if config.max_account_id_bytes == 0 {
            return Err(HeartbeatConfigError::NoAccountIdCapacity);
        }
        Ok(Self {
            config,
            installation_id: installation_id.into(),
            entries: BTreeMap::new(),
            dirty: BTreeSet::new(),
            inflight: BTreeMap::new(),
            next_revision: 1,
            next_sequence: 1,
            last_batch_accounts: 0,
            last_batch_shards: 0,
            last_full_prepared_at_ms: None,
            full_refresh_anchor_ms: None,
            full_retry_forced: false,
            inflight_cycle: None,
            rejected_full: 0,
            failed_acks: 0,
        })
    }

    /// Replaces the latest state for an account and marks only real changes dirty.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid IDs, exhausted capacity, or revision space.
    pub fn publish(
        &mut self,
        delta: AccountHeartbeatDelta,
    ) -> Result<HeartbeatPublish, HeartbeatPublishError> {
        self.validate_account_id(&delta.account_id)?;
        if let Some(existing) = self.entries.get(&delta.account_id) {
            if !existing.removed && existing.state.as_ref() == Some(&delta) {
                return Ok(HeartbeatPublish::Unchanged {
                    revision: existing.revision,
                });
            }
        } else if self.entries.len() >= self.config.max_tracked_accounts {
            self.rejected_full = self.rejected_full.saturating_add(1);
            return Err(HeartbeatPublishError::Full {
                capacity: self.config.max_tracked_accounts,
            });
        }

        let revision = self.take_revision()?;
        let account_id = delta.account_id.clone();
        self.entries.insert(
            account_id.clone(),
            AccountHeartbeatState {
                revision,
                removed: false,
                state: Some(delta),
                account_id: account_id.clone(),
            },
        );
        self.dirty.insert(account_id);
        Ok(HeartbeatPublish::Changed { revision })
    }

    /// Publishes a removal tombstone, retaining it only until a matching ACK.
    ///
    /// # Errors
    ///
    /// Returns an error for an invalid ID or exhausted revision space.
    pub fn remove(&mut self, account_id: &str) -> Result<HeartbeatPublish, HeartbeatPublishError> {
        self.validate_account_id(account_id)?;
        let Some(existing) = self.entries.get(account_id) else {
            return Ok(HeartbeatPublish::AlreadyAbsent);
        };
        if existing.removed {
            return Ok(HeartbeatPublish::Unchanged {
                revision: existing.revision,
            });
        }
        let revision = self.take_revision()?;
        self.entries.insert(
            account_id.to_owned(),
            AccountHeartbeatState {
                revision,
                removed: true,
                state: None,
                account_id: account_id.to_owned(),
            },
        );
        self.dirty.insert(account_id.to_owned());
        Ok(HeartbeatPublish::Changed { revision })
    }

    /// Prepares all dirty deltas, or every record when full repair is due.
    ///
    /// An empty vector means there was nothing due. A caller must ACK every
    /// returned sequence before another preparation can be made.
    ///
    /// # Errors
    ///
    /// Returns an error while another batch is in flight, when a record cannot
    /// fit the byte bound, when too many shards are required, or when monotonic
    /// sequence space is exhausted.
    pub fn prepare(&mut self, now_ms: u64) -> Result<Vec<HeartbeatBatch>, HeartbeatPrepareError> {
        if !self.inflight.is_empty() {
            return Err(HeartbeatPrepareError::Inflight {
                inflight: self.inflight.len(),
            });
        }
        let full = self.full_due(now_ms);
        let candidates: Vec<AccountHeartbeatState> = if full {
            self.entries.values().cloned().collect()
        } else {
            self.dirty
                .iter()
                .filter_map(|account_id| self.entries.get(account_id).cloned())
                .collect()
        };
        if candidates.is_empty() {
            return Ok(Vec::new());
        }

        let shards = self.partition(&candidates, full, now_ms)?;
        if shards.len() > self.config.max_inflight_batches {
            return Err(HeartbeatPrepareError::TooManyShards {
                required: shards.len(),
                maximum: self.config.max_inflight_batches,
            });
        }

        let shard_count = shards.len();
        let first_sequence = self.next_sequence;
        let sequence_advance =
            u64::try_from(shard_count).map_err(|_| HeartbeatPrepareError::SequenceExhausted)?;
        let next_sequence = self
            .next_sequence
            .checked_add(sequence_advance)
            .ok_or(HeartbeatPrepareError::SequenceExhausted)?;
        let batches: Vec<HeartbeatBatch> = shards
            .into_iter()
            .enumerate()
            .map(|(shard_index, accounts)| HeartbeatBatch {
                installation_id: self.installation_id.clone(),
                sequence: first_sequence
                    .saturating_add(u64::try_from(shard_index).unwrap_or(sequence_advance)),
                full,
                shard_index,
                shard_count,
                prepared_at_ms: now_ms,
                accounts,
            })
            .collect();

        for batch in &batches {
            self.inflight.insert(
                batch.sequence,
                InflightBatch {
                    prepared_at_ms: now_ms,
                    revisions: batch
                        .accounts
                        .iter()
                        .map(|state| (state.account_id.clone(), state.revision))
                        .collect(),
                },
            );
        }
        self.next_sequence = next_sequence;
        self.last_batch_accounts = candidates.len();
        self.last_batch_shards = shard_count;
        self.inflight_cycle = Some(InflightCycle {
            full,
            failed: false,
            latest_ack_ms: now_ms,
        });
        if full {
            self.last_full_prepared_at_ms = Some(now_ms);
        }
        Ok(batches)
    }

    /// Applies a successful or failed network acknowledgement.
    ///
    /// Success clears only revisions that did not change while the request was
    /// in flight. Failure explicitly retains every matching revision for retry.
    #[must_use]
    pub fn acknowledge(&mut self, sequence: u64, success: bool) -> HeartbeatAck {
        let acknowledged_at_ms = self
            .inflight
            .get(&sequence)
            .map_or(0, |batch| batch.prepared_at_ms);
        self.acknowledge_at(sequence, success, acknowledged_at_ms)
    }

    /// Applies an acknowledgement using its actual local receive time.
    ///
    /// The first completely successful delta cycle establishes the periodic
    /// full-repair clock. A completely successful full cycle resets it. A
    /// failed full shard forces the next preparation to retry the complete
    /// snapshot, even when other shards in that cycle succeeded.
    #[must_use]
    pub fn acknowledge_at(
        &mut self,
        sequence: u64,
        success: bool,
        acknowledged_at_ms: u64,
    ) -> HeartbeatAck {
        let Some(batch) = self.inflight.remove(&sequence) else {
            return HeartbeatAck::UnknownSequence;
        };
        if let Some(cycle) = &mut self.inflight_cycle {
            cycle.failed |= !success;
            cycle.latest_ack_ms = cycle.latest_ack_ms.max(acknowledged_at_ms);
        }
        if success {
            for (account_id, revision) in batch.revisions {
                let matches = self
                    .entries
                    .get(&account_id)
                    .is_some_and(|state| state.revision == revision);
                if matches {
                    self.dirty.remove(&account_id);
                    if self
                        .entries
                        .get(&account_id)
                        .is_some_and(|state| state.removed)
                    {
                        self.entries.remove(&account_id);
                    }
                }
            }
            self.finish_ack_cycle_if_complete();
            HeartbeatAck::Applied
        } else {
            self.failed_acks = self.failed_acks.saturating_add(1);
            for (account_id, revision) in batch.revisions {
                if self
                    .entries
                    .get(&account_id)
                    .is_some_and(|state| state.revision == revision)
                {
                    self.dirty.insert(account_id);
                }
            }
            self.finish_ack_cycle_if_complete();
            HeartbeatAck::RetainedForRetry
        }
    }

    /// Returns bounded aggregate-heartbeat health counters.
    #[must_use]
    pub fn snapshot(&self) -> HeartbeatSnapshot {
        HeartbeatSnapshot {
            tracked_accounts: self.entries.len(),
            dirty_accounts: self.dirty.len(),
            inflight_batches: self.inflight.len(),
            next_revision: self.next_revision,
            next_sequence: self.next_sequence,
            last_batch_accounts: self.last_batch_accounts,
            last_batch_shards: self.last_batch_shards,
            last_full_prepared_at_ms: self.last_full_prepared_at_ms,
            full_refresh_anchor_ms: self.full_refresh_anchor_ms,
            full_retry_forced: self.full_retry_forced,
            rejected_full: self.rejected_full,
            failed_acks: self.failed_acks,
        }
    }

    fn validate_account_id(&self, account_id: &str) -> Result<(), HeartbeatPublishError> {
        let actual = account_id.len();
        if actual == 0 || actual > self.config.max_account_id_bytes {
            return Err(HeartbeatPublishError::InvalidAccountId {
                actual,
                maximum: self.config.max_account_id_bytes,
            });
        }
        Ok(())
    }

    fn take_revision(&mut self) -> Result<u64, HeartbeatPublishError> {
        let revision = self.next_revision;
        self.next_revision = self
            .next_revision
            .checked_add(1)
            .ok_or(HeartbeatPublishError::RevisionExhausted)?;
        Ok(revision)
    }

    fn full_due(&self, now_ms: u64) -> bool {
        self.full_retry_forced
            || self.full_refresh_anchor_ms.is_some_and(|anchor| {
                now_ms.saturating_sub(anchor) >= self.config.full_refresh_interval_ms
            })
    }

    fn finish_ack_cycle_if_complete(&mut self) {
        if !self.inflight.is_empty() {
            return;
        }
        let Some(cycle) = self.inflight_cycle.take() else {
            return;
        };
        if cycle.full {
            if cycle.failed {
                self.full_retry_forced = true;
            } else {
                self.full_retry_forced = false;
                self.full_refresh_anchor_ms = Some(cycle.latest_ack_ms);
            }
        } else if !cycle.failed && self.full_refresh_anchor_ms.is_none() {
            self.full_refresh_anchor_ms = Some(cycle.latest_ack_ms);
        }
    }

    fn partition(
        &self,
        candidates: &[AccountHeartbeatState],
        full: bool,
        now_ms: u64,
    ) -> Result<Vec<Vec<AccountHeartbeatState>>, HeartbeatPrepareError> {
        let mut shards = Vec::new();
        let mut current = Vec::new();

        for candidate in candidates {
            let mut trial = current.clone();
            trial.push(candidate.clone());
            let exceeds_accounts = trial.len() > self.config.max_accounts_per_batch;
            let encoded_bytes = self.measure_conservative(&trial, full, now_ms)?;
            let exceeds_bytes = encoded_bytes > self.config.max_payload_bytes;
            if (exceeds_accounts || exceeds_bytes) && !current.is_empty() {
                shards.push(std::mem::take(&mut current));
                current.push(candidate.clone());
                let single_bytes = self.measure_conservative(&current, full, now_ms)?;
                if single_bytes > self.config.max_payload_bytes {
                    return Err(HeartbeatPrepareError::RecordTooLarge {
                        account_id: candidate.account_id.clone(),
                        encoded_bytes: single_bytes,
                        maximum: self.config.max_payload_bytes,
                    });
                }
            } else if exceeds_bytes {
                return Err(HeartbeatPrepareError::RecordTooLarge {
                    account_id: candidate.account_id.clone(),
                    encoded_bytes,
                    maximum: self.config.max_payload_bytes,
                });
            } else {
                current = trial;
            }
        }
        if !current.is_empty() {
            shards.push(current);
        }
        Ok(shards)
    }

    fn measure_conservative(
        &self,
        accounts: &[AccountHeartbeatState],
        full: bool,
        _now_ms: u64,
    ) -> Result<usize, HeartbeatPrepareError> {
        let batch = HeartbeatBatch {
            installation_id: self.installation_id.clone(),
            sequence: u64::MAX,
            full,
            shard_index: usize::MAX,
            shard_count: usize::MAX,
            prepared_at_ms: u64::MAX,
            accounts: accounts.to_vec(),
        };
        serde_json::to_vec(&batch)
            .map(|payload| payload.len())
            .map_err(|error| HeartbeatPrepareError::Serialization(error.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::{InboundState, LifecycleState, OwnershipState, SendCapability};

    fn config() -> HeartbeatConfig {
        HeartbeatConfig {
            max_tracked_accounts: 400,
            max_accounts_per_batch: 300,
            max_payload_bytes: 512 * 1_024,
            max_inflight_batches: 16,
            full_refresh_interval_ms: 1_000,
            max_account_id_bytes: 64,
        }
    }

    fn delta(account_id: impl Into<String>, generation: u64) -> AccountHeartbeatDelta {
        AccountHeartbeatDelta {
            account_id: account_id.into(),
            actor_generation: generation,
            runtime: AccountRuntimeState {
                lifecycle: LifecycleState::Running,
                ownership: OwnershipState::Owned,
                inbound: InboundState::WsHealthy,
                send: SendCapability::Sendable,
                credential_generation: generation + 10,
                lease_epoch: generation + 20,
            },
            mailbox_depth: 0,
            signer_queue_depth: 0,
            last_activity_ms: Some(100),
            transport: DependencyHealth::Closed,
            signer: DependencyHealth::Closed,
        }
    }

    #[test]
    fn three_hundred_changes_form_one_sorted_installation_batch() {
        let mut aggregator = HeartbeatAggregator::new("installation", config()).unwrap();
        for number in (0..300).rev() {
            aggregator
                .publish(delta(format!("account-{number:03}"), 1))
                .unwrap();
        }

        let batches = aggregator.prepare(100).unwrap();
        assert_eq!(batches.len(), 1);
        assert_eq!(batches[0].accounts.len(), 300);
        assert_eq!(batches[0].accounts[0].account_id, "account-000");
        assert_eq!(batches[0].accounts[299].account_id, "account-299");
        assert_eq!(aggregator.snapshot().inflight_batches, 1);
        assert!(serde_json::to_vec(&batches[0]).unwrap().len() <= config().max_payload_bytes);
    }

    #[test]
    fn failed_ack_retries_without_growing_retained_memory() {
        let mut aggregator = HeartbeatAggregator::new("installation", config()).unwrap();
        aggregator.publish(delta("account", 1)).unwrap();
        for attempt in 0..50 {
            let batch = aggregator.prepare(attempt).unwrap().remove(0);
            assert_eq!(
                aggregator.acknowledge(batch.sequence, false),
                HeartbeatAck::RetainedForRetry
            );
            let snapshot = aggregator.snapshot();
            assert_eq!(snapshot.tracked_accounts, 1);
            assert_eq!(snapshot.dirty_accounts, 1);
            assert_eq!(snapshot.inflight_batches, 0);
        }
        assert_eq!(aggregator.snapshot().failed_acks, 50);
    }

    #[test]
    fn successful_ack_only_clears_an_unchanged_revision() {
        let mut aggregator = HeartbeatAggregator::new("installation", config()).unwrap();
        aggregator.publish(delta("account", 1)).unwrap();
        let old = aggregator.prepare(100).unwrap().remove(0);
        let mut changed = delta("account", 1);
        changed.mailbox_depth = 7;
        aggregator.publish(changed).unwrap();
        assert_eq!(
            aggregator.acknowledge(old.sequence, true),
            HeartbeatAck::Applied
        );
        assert_eq!(aggregator.snapshot().dirty_accounts, 1);

        let current = aggregator.prepare(101).unwrap().remove(0);
        assert_eq!(
            aggregator.acknowledge(current.sequence, true),
            HeartbeatAck::Applied
        );
        assert_eq!(aggregator.snapshot().dirty_accounts, 0);
    }

    #[test]
    fn account_limit_and_deterministic_shards_are_explicit() {
        let mut bounded = config();
        bounded.max_tracked_accounts = 3;
        bounded.max_accounts_per_batch = 2;
        let mut aggregator = HeartbeatAggregator::new("installation", bounded).unwrap();
        for number in 0..3 {
            aggregator
                .publish(delta(format!("account-{number}"), 1))
                .unwrap();
        }
        assert!(matches!(
            aggregator.publish(delta("overflow", 1)),
            Err(HeartbeatPublishError::Full { capacity: 3 })
        ));
        let batches = aggregator.prepare(100).unwrap();
        assert_eq!(batches.len(), 2);
        assert_eq!(batches[0].shard_index, 0);
        assert_eq!(batches[0].shard_count, 2);
        assert_eq!(batches[0].accounts.len(), 2);
        assert_eq!(batches[1].accounts.len(), 1);
    }

    #[test]
    fn periodic_full_repair_sends_clean_accounts_again() {
        let mut aggregator = HeartbeatAggregator::new("installation", config()).unwrap();
        aggregator.publish(delta("account", 1)).unwrap();
        let initial = aggregator.prepare(100).unwrap().remove(0);
        assert!(!initial.full, "the first dirty update is a delta");
        assert_eq!(
            aggregator.acknowledge_at(initial.sequence, true, 500),
            HeartbeatAck::Applied
        );
        assert_eq!(aggregator.snapshot().full_refresh_anchor_ms, Some(500));
        assert!(aggregator.prepare(1_499).unwrap().is_empty());
        let repair = aggregator.prepare(1_500).unwrap().remove(0);
        assert!(repair.full);
        assert_eq!(repair.accounts.len(), 1);
    }

    #[test]
    fn failed_full_cycle_forces_a_complete_retry_after_all_shards_ack() {
        let mut bounded = config();
        bounded.max_accounts_per_batch = 1;
        let mut aggregator = HeartbeatAggregator::new("installation", bounded).unwrap();
        aggregator.publish(delta("account-a", 1)).unwrap();
        aggregator.publish(delta("account-b", 1)).unwrap();
        let initial = aggregator.prepare(100).unwrap();
        assert_eq!(initial.len(), 2);
        for batch in initial {
            assert_eq!(
                aggregator.acknowledge_at(batch.sequence, true, 200),
                HeartbeatAck::Applied
            );
        }

        let repair = aggregator.prepare(1_200).unwrap();
        assert!(repair.iter().all(|batch| batch.full));
        assert_eq!(
            aggregator.acknowledge_at(repair[0].sequence, false, 1_210),
            HeartbeatAck::RetainedForRetry
        );
        assert_eq!(
            aggregator.acknowledge_at(repair[1].sequence, true, 1_220),
            HeartbeatAck::Applied
        );
        assert!(aggregator.snapshot().full_retry_forced);

        let retry = aggregator.prepare(1_221).unwrap();
        assert_eq!(retry.len(), 2);
        assert!(retry.iter().all(|batch| batch.full));
        assert_eq!(
            retry
                .iter()
                .map(|batch| batch.accounts.len())
                .sum::<usize>(),
            2
        );
    }

    #[test]
    fn acknowledged_removal_tombstone_releases_account_capacity() {
        let mut bounded = config();
        bounded.max_tracked_accounts = 1;
        let mut aggregator = HeartbeatAggregator::new("installation", bounded).unwrap();
        aggregator.publish(delta("old", 1)).unwrap();
        let first = aggregator.prepare(10).unwrap().remove(0);
        assert_eq!(
            aggregator.acknowledge(first.sequence, true),
            HeartbeatAck::Applied
        );
        aggregator.remove("old").unwrap();
        let removal = aggregator.prepare(11).unwrap().remove(0);
        assert!(removal.accounts[0].removed);
        assert_eq!(
            aggregator.acknowledge(removal.sequence, true),
            HeartbeatAck::Applied
        );
        assert_eq!(aggregator.snapshot().tracked_accounts, 0);
        assert!(matches!(
            aggregator.publish(delta("new", 1)),
            Ok(HeartbeatPublish::Changed { .. })
        ));
    }

    #[test]
    fn prepare_does_not_duplicate_an_unacknowledged_snapshot() {
        let mut aggregator = HeartbeatAggregator::new("installation", config()).unwrap();
        aggregator.publish(delta("account", 1)).unwrap();
        aggregator.prepare(10).unwrap();
        assert!(matches!(
            aggregator.prepare(11),
            Err(HeartbeatPrepareError::Inflight { inflight: 1 })
        ));
        assert_eq!(aggregator.snapshot().inflight_batches, 1);
    }
}
