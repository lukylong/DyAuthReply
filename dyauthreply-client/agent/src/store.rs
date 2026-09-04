use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use rusqlite::{params, Connection, OptionalExtension, Transaction, TransactionBehavior};
use thiserror::Error;
use uuid::Uuid;

use crate::CORE_SCHEMA_VERSION;

const DATABASE_FILE_NAME: &str = "core.sqlite3";
const INITIALIZED_MARKER_FILE_NAME: &str = "core.initialized";
const DATABASE_ID_META_KEY: &str = "database_id";
const BUSY_TIMEOUT: Duration = Duration::from_secs(5);
const LEASE_TRANSFER_UNCERTAIN_REASON: &str =
    "lease epoch changed while the previous send outcome was unresolved";
const SCHEMA_V1_SQL: &str = "CREATE TABLE account_leases (
             account_id TEXT PRIMARY KEY NOT NULL,
             owner_instance_id TEXT NOT NULL,
             owner_boot_id TEXT NOT NULL,
             fence_epoch INTEGER NOT NULL CHECK (fence_epoch > 0),
             lease_until_ms INTEGER NOT NULL,
             status TEXT NOT NULL CHECK (status IN ('active', 'released')),
             last_observed_at_ms INTEGER NOT NULL,
             updated_at_ms INTEGER NOT NULL
         );

         CREATE TABLE inbound_receipts (
             account_id TEXT NOT NULL,
             stream TEXT NOT NULL,
             stream_generation INTEGER NOT NULL CHECK (stream_generation > 0),
             event_id TEXT NOT NULL,
             page_checkpoint INTEGER NOT NULL,
             payload BLOB,
             payload_hash TEXT NOT NULL,
             status TEXT NOT NULL CHECK (status IN ('pending', 'processed')),
             fence_epoch INTEGER NOT NULL CHECK (fence_epoch > 0),
             received_at_ms INTEGER NOT NULL,
             processed_at_ms INTEGER,
             processed_fence_epoch INTEGER CHECK (
                 processed_fence_epoch IS NULL OR processed_fence_epoch > 0
             ),
             CHECK (
                 (status = 'pending' AND payload IS NOT NULL
                   AND processed_at_ms IS NULL AND processed_fence_epoch IS NULL)
                 OR
                 (status = 'processed' AND payload IS NULL
                   AND processed_at_ms IS NOT NULL AND processed_fence_epoch IS NOT NULL)
             ),
             PRIMARY KEY (account_id, stream, stream_generation, event_id)
         );

         CREATE TABLE inbound_checkpoints (
             account_id TEXT NOT NULL,
             stream TEXT NOT NULL,
             stream_generation INTEGER NOT NULL CHECK (stream_generation > 0),
             checkpoint INTEGER NOT NULL,
             fence_epoch INTEGER NOT NULL CHECK (fence_epoch > 0),
             updated_at_ms INTEGER NOT NULL,
             PRIMARY KEY (account_id, stream)
         );

         CREATE TABLE outbound_batches (
             id TEXT PRIMARY KEY NOT NULL,
             account_id TEXT NOT NULL,
             trigger_id TEXT NOT NULL,
             response_id TEXT NOT NULL,
             status TEXT NOT NULL CHECK (
                 status IN ('prepared', 'sending', 'retryable', 'partial', 'confirmed', 'rejected', 'uncertain')
             ),
             created_fence_epoch INTEGER NOT NULL CHECK (created_fence_epoch > 0),
             last_fence_epoch INTEGER NOT NULL CHECK (last_fence_epoch > 0),
             created_at_ms INTEGER NOT NULL,
             updated_at_ms INTEGER NOT NULL,
             UNIQUE (account_id, trigger_id)
         );

         CREATE TABLE outbound_segments (
             id TEXT PRIMARY KEY NOT NULL,
             client_message_id TEXT NOT NULL UNIQUE,
             batch_id TEXT NOT NULL REFERENCES outbound_batches(id) ON DELETE CASCADE,
             ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
             kind TEXT NOT NULL,
             payload TEXT NOT NULL,
             status TEXT NOT NULL CHECK (
                 status IN ('prepared', 'sending', 'retryable', 'confirmed', 'rejected', 'uncertain')
             ),
             attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
             platform_message_id TEXT,
             last_error TEXT,
             last_fence_epoch INTEGER NOT NULL CHECK (last_fence_epoch > 0),
             created_at_ms INTEGER NOT NULL,
             updated_at_ms INTEGER NOT NULL,
             UNIQUE (batch_id, ordinal)
         );

         CREATE INDEX outbound_batches_account_status_idx
             ON outbound_batches(account_id, status, updated_at_ms);
         CREATE INDEX outbound_segments_batch_status_idx
             ON outbound_segments(batch_id, status, ordinal);
         CREATE INDEX inbound_receipts_pending_idx
             ON inbound_receipts(account_id, status, received_at_ms, event_id)
             WHERE status = 'pending';";
const REQUIRED_TABLES: &[(&str, &[&str])] = &[
    ("meta", &["key", "value"]),
    (
        "account_leases",
        &[
            "account_id",
            "owner_instance_id",
            "owner_boot_id",
            "fence_epoch",
            "lease_until_ms",
            "status",
            "last_observed_at_ms",
        ],
    ),
    (
        "inbound_receipts",
        &[
            "account_id",
            "stream",
            "stream_generation",
            "event_id",
            "page_checkpoint",
            "payload",
            "payload_hash",
            "status",
            "processed_at_ms",
        ],
    ),
    (
        "inbound_checkpoints",
        &["account_id", "stream", "stream_generation", "checkpoint"],
    ),
    (
        "outbound_batches",
        &["id", "account_id", "trigger_id", "response_id", "status"],
    ),
    (
        "outbound_segments",
        &[
            "id",
            "client_message_id",
            "batch_id",
            "status",
            "attempt_count",
        ],
    ),
];

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("cannot access the core store: {0}")]
    Io(#[from] std::io::Error),
    #[error("core store SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("core store connection mutex was poisoned")]
    LockPoisoned,
    #[error("system clock is before the Unix epoch")]
    SystemClockBeforeUnixEpoch,
    #[error("system time in milliseconds exceeds the SQLite integer range")]
    SystemTimeOverflow,
    #[error(
        "core database {database_path:?} is missing after initialization marker {marker_path:?} was created"
    )]
    DatabaseMissingAfterInitialization {
        database_path: PathBuf,
        marker_path: PathBuf,
    },
    #[error(
        "core database identity mismatch: marker contains {marker_database_id}, database contains {database_database_id}"
    )]
    DatabaseIdentityMismatch {
        marker_database_id: Uuid,
        database_database_id: Uuid,
    },
    #[error("unsupported core schema version {found}; this binary supports {supported}")]
    UnsupportedSchema { found: u32, supported: u32 },
    #[error("invalid schema version value {0:?}")]
    InvalidSchemaVersion(String),
    #[error("invalid persisted {field} value {value:?}")]
    CorruptData { field: &'static str, value: String },
    #[error("core schema invariant failed: {0}")]
    SchemaInvariant(String),
    #[error("invalid store input: {0}")]
    InvalidInput(&'static str),
    #[error(
        "account {account_id:?} is leased by instance {owner_instance_id:?} boot {owner_boot_id:?} until {lease_until_ms}"
    )]
    LeaseHeld {
        account_id: String,
        owner_instance_id: String,
        owner_boot_id: String,
        lease_until_ms: i64,
    },
    #[error("account {account_id:?} does not have a lease")]
    LeaseNotFound { account_id: String },
    #[error(
        "account {account_id:?} lease expired at {lease_until_ms}; operation time is {now_ms}"
    )]
    LeaseExpired {
        account_id: String,
        lease_until_ms: i64,
        now_ms: i64,
    },
    #[error(
        "stale fence for account {account_id:?}: current epoch is {current_epoch}, provided epoch is {provided_epoch}"
    )]
    StaleFence {
        account_id: String,
        current_epoch: i64,
        provided_epoch: i64,
    },
    #[error(
        "lease owner mismatch for account {account_id:?}: current owner is instance {current_instance_id:?} boot {current_boot_id:?}, provided owner is instance {provided_instance_id:?} boot {provided_boot_id:?}"
    )]
    LeaseOwnerMismatch {
        account_id: String,
        current_instance_id: String,
        current_boot_id: String,
        provided_instance_id: String,
        provided_boot_id: String,
    },
    #[error("account {account_id:?} lease epoch {fence_epoch} was released")]
    LeaseReleased {
        account_id: String,
        fence_epoch: i64,
    },
    #[error(
        "clock moved backwards for account {account_id:?}: last observed {last_observed_at_ms}, provided {provided_now_ms}"
    )]
    ClockRegression {
        account_id: String,
        last_observed_at_ms: i64,
        provided_now_ms: i64,
    },
    #[error(
        "stale stream generation for {account_id:?}/{stream:?}: current generation is {current_generation}, provided generation is {provided_generation}"
    )]
    StaleStreamGeneration {
        account_id: String,
        stream: String,
        current_generation: i64,
        provided_generation: i64,
    },
    #[error("idempotency conflict for {entity} {key:?}")]
    IdempotencyConflict { entity: &'static str, key: String },
    #[error("{entity} {id:?} was not found")]
    NotFound { entity: &'static str, id: String },
    #[error("invalid {entity} state transition from {from:?} to {to:?}")]
    InvalidTransition {
        entity: &'static str,
        from: String,
        to: String,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountLease {
    pub account_id: String,
    pub owner_instance_id: String,
    pub owner_boot_id: String,
    pub fence_epoch: i64,
    pub lease_until_ms: i64,
    pub status: LeaseStatus,
    pub last_observed_at_ms: i64,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LeaseStatus {
    Active,
    Released,
}

impl LeaseStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Released => "released",
        }
    }

    fn parse(value: &str) -> Result<Self, StoreError> {
        match value {
            "active" => Ok(Self::Active),
            "released" => Ok(Self::Released),
            _ => Err(StoreError::CorruptData {
                field: "account lease status",
                value: value.to_owned(),
            }),
        }
    }
}

impl AccountLease {
    #[must_use]
    pub fn token(&self) -> LeaseToken {
        LeaseToken {
            account_id: self.account_id.clone(),
            owner_instance_id: self.owner_instance_id.clone(),
            owner_boot_id: self.owner_boot_id.clone(),
            fence_epoch: self.fence_epoch,
        }
    }
}

/// Complete lease identity carried by every fenced mutation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LeaseToken {
    pub account_id: String,
    pub owner_instance_id: String,
    pub owner_boot_id: String,
    pub fence_epoch: i64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InboundReceiptDraft {
    pub event_id: String,
    pub payload: Vec<u8>,
    pub payload_hash: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InboundStatus {
    Pending,
    Processed,
}

impl InboundStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Processed => "processed",
        }
    }

    fn parse(value: &str) -> Result<Self, StoreError> {
        match value {
            "pending" => Ok(Self::Pending),
            "processed" => Ok(Self::Processed),
            _ => Err(StoreError::CorruptData {
                field: "inbound receipt status",
                value: value.to_owned(),
            }),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InboundReceipt {
    pub account_id: String,
    pub stream: String,
    pub stream_generation: i64,
    pub event_id: String,
    pub page_checkpoint: i64,
    pub payload: Option<Vec<u8>>,
    pub payload_hash: String,
    pub status: InboundStatus,
    pub received_at_ms: i64,
    pub processed_at_ms: Option<i64>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InboundCheckpoint {
    pub stream_generation: i64,
    pub checkpoint: i64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InboundPageResult {
    pub inserted_count: usize,
    pub checkpoint: InboundCheckpoint,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct InboundProcessOutcome {
    pub applied: bool,
    pub receipt: InboundReceipt,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutboundSegmentDraft {
    pub kind: String,
    pub payload: String,
}

impl OutboundSegmentDraft {
    #[must_use]
    pub fn text(payload: impl Into<String>) -> Self {
        Self {
            kind: "text".to_owned(),
            payload: payload.into(),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SegmentStatus {
    Prepared,
    Sending,
    Confirmed,
    Retryable,
    Rejected,
    Uncertain,
}

impl SegmentStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Prepared => "prepared",
            Self::Sending => "sending",
            Self::Confirmed => "confirmed",
            Self::Retryable => "retryable",
            Self::Rejected => "rejected",
            Self::Uncertain => "uncertain",
        }
    }

    fn parse(value: &str) -> Result<Self, StoreError> {
        match value {
            "prepared" => Ok(Self::Prepared),
            "sending" => Ok(Self::Sending),
            "confirmed" => Ok(Self::Confirmed),
            "retryable" => Ok(Self::Retryable),
            "rejected" => Ok(Self::Rejected),
            "uncertain" => Ok(Self::Uncertain),
            _ => Err(StoreError::CorruptData {
                field: "outbound segment status",
                value: value.to_owned(),
            }),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum BatchStatus {
    Prepared,
    Sending,
    Partial,
    Confirmed,
    Retryable,
    Rejected,
    Uncertain,
}

impl BatchStatus {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Prepared => "prepared",
            Self::Sending => "sending",
            Self::Partial => "partial",
            Self::Confirmed => "confirmed",
            Self::Retryable => "retryable",
            Self::Rejected => "rejected",
            Self::Uncertain => "uncertain",
        }
    }

    fn parse(value: &str) -> Result<Self, StoreError> {
        match value {
            "prepared" => Ok(Self::Prepared),
            "sending" => Ok(Self::Sending),
            "partial" => Ok(Self::Partial),
            "confirmed" => Ok(Self::Confirmed),
            "retryable" => Ok(Self::Retryable),
            "rejected" => Ok(Self::Rejected),
            "uncertain" => Ok(Self::Uncertain),
            _ => Err(StoreError::CorruptData {
                field: "outbound batch status",
                value: value.to_owned(),
            }),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SegmentTransition {
    StartAttempt,
    Confirm { platform_message_id: String },
    MarkRetryable { reason: String },
    Reject { error: String },
    MarkUncertain { reason: String },
}

impl SegmentTransition {
    const fn target_status(&self) -> SegmentStatus {
        match self {
            Self::StartAttempt => SegmentStatus::Sending,
            Self::Confirm { .. } => SegmentStatus::Confirmed,
            Self::MarkRetryable { .. } => SegmentStatus::Retryable,
            Self::Reject { .. } => SegmentStatus::Rejected,
            Self::MarkUncertain { .. } => SegmentStatus::Uncertain,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutboundSegment {
    pub id: String,
    pub client_message_id: String,
    pub batch_id: String,
    pub ordinal: u32,
    pub kind: String,
    pub payload: String,
    pub status: SegmentStatus,
    pub attempt_count: u32,
    pub platform_message_id: Option<String>,
    pub last_error: Option<String>,
    pub last_fence_epoch: i64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OutboundBatch {
    pub id: String,
    pub account_id: String,
    pub trigger_id: String,
    pub response_id: String,
    pub status: BatchStatus,
    pub created_fence_epoch: i64,
    pub last_fence_epoch: i64,
    pub segments: Vec<OutboundSegment>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TransitionOutcome {
    pub applied: bool,
    pub batch: OutboundBatch,
}

/// Durable correctness store rooted in one Agent data directory.
///
/// `open` creates (or reuses) `<data_dir>/core.sqlite3` and binds it to the
/// durable `<data_dir>/core.initialized` identity marker. One mutex protects the
/// connection inside a process; `SQLite`'s WAL and immediate transactions protect
/// correctness across processes.
pub struct CoreStore {
    database_path: PathBuf,
    connection: Mutex<Connection>,
}

impl CoreStore {
    /// Opens the database under the supplied Agent data directory.
    ///
    /// # Errors
    ///
    /// Returns an error if the directory or database cannot be opened, required
    /// pragmas cannot be enabled, its schema is incompatible, or an initialized
    /// database was deleted or replaced.
    pub fn open(data_dir: &Path) -> Result<Self, StoreError> {
        fs::create_dir_all(data_dir)?;
        let database_path = data_dir.join(DATABASE_FILE_NAME);
        let marker_path = data_dir.join(INITIALIZED_MARKER_FILE_NAME);
        let marker_database_id = read_initialized_marker(&marker_path)?;
        let database_exists = database_path.try_exists()?;

        if marker_database_id.is_some() && !database_exists {
            return Err(StoreError::DatabaseMissingAfterInitialization {
                database_path,
                marker_path,
            });
        }

        let mut connection = Connection::open(&database_path)?;

        connection.busy_timeout(BUSY_TIMEOUT)?;
        connection.pragma_update(None, "foreign_keys", true)?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;

        let may_initialize = !database_exists
            || (marker_database_id.is_none() && database_schema_is_empty(&connection)?);
        let database_id = if may_initialize {
            initialize_new_database(&mut connection)?
        } else {
            validate_existing_database(&connection)?
        };
        if let Some(marker_database_id) = marker_database_id {
            if marker_database_id != database_id {
                return Err(StoreError::DatabaseIdentityMismatch {
                    marker_database_id,
                    database_database_id: database_id,
                });
            }
        } else {
            write_initialized_marker(data_dir, &marker_path, database_id)?;
        }

        Ok(Self {
            database_path,
            connection: Mutex::new(connection),
        })
    }

    #[must_use]
    pub fn database_path(&self) -> &Path {
        &self.database_path
    }

    /// Reads the durable schema version.
    ///
    /// # Errors
    ///
    /// Returns an error if the connection is unavailable or the version row is
    /// missing, malformed, or unreadable.
    pub fn schema_version(&self) -> Result<u32, StoreError> {
        let connection = self.lock_connection()?;
        read_schema_version(&connection)
    }

    /// Installs a lease already verified by the remote control plane.
    ///
    /// The caller must authenticate and validate the remote lease before calling
    /// this method. The supplied `fence_epoch` is globally monotonic for the
    /// account: lower epochs are rejected, an equal epoch may only extend the
    /// exact same installation/boot owner, and only a higher epoch may transfer
    /// ownership. A higher epoch also fences interrupted `Sending` segments into
    /// `Uncertain` before the new owner can inspect recovery work.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid input, a stale/conflicting/released epoch,
    /// backwards time, incompatible persisted data, or database failure.
    pub fn install_verified_account_lease(
        &self,
        account_id: &str,
        owner_instance_id: &str,
        owner_boot_id: &str,
        fence_epoch: i64,
        lease_until_ms: i64,
    ) -> Result<AccountLease, StoreError> {
        self.install_verified_account_lease_with_time(
            account_id,
            owner_instance_id,
            owner_boot_id,
            fence_epoch,
            lease_until_ms,
            OperationTime::System,
        )
    }

    #[cfg(test)]
    fn install_verified_account_lease_at(
        &self,
        account_id: &str,
        owner_instance_id: &str,
        owner_boot_id: &str,
        fence_epoch: i64,
        now_ms: i64,
        lease_until_ms: i64,
    ) -> Result<AccountLease, StoreError> {
        self.install_verified_account_lease_with_time(
            account_id,
            owner_instance_id,
            owner_boot_id,
            fence_epoch,
            lease_until_ms,
            OperationTime::Fixed(now_ms),
        )
    }

    fn install_verified_account_lease_with_time(
        &self,
        account_id: &str,
        owner_instance_id: &str,
        owner_boot_id: &str,
        fence_epoch: i64,
        lease_until_ms: i64,
        operation_time: OperationTime,
    ) -> Result<AccountLease, StoreError> {
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now_ms = operation_time.resolve()?;
        let requested = VerifiedLeaseInstall {
            account_id,
            owner_instance_id,
            owner_boot_id,
            fence_epoch,
            now_ms,
            lease_until_ms,
        };
        requested.validate()?;

        let lease = install_verified_lease_in_transaction(&transaction, &requested)?;
        transaction.commit()?;
        Ok(lease)
    }

    /// Irreversibly releases the supplied lease epoch.
    ///
    /// # Errors
    ///
    /// Returns an error for an expired/stale/already released lease, owner
    /// mismatch, backwards time, or database failure. Reinstalling the same
    /// epoch cannot reactivate it; the control plane must issue a higher epoch.
    pub fn release_account_lease(&self, lease_token: &LeaseToken) -> Result<(), StoreError> {
        self.release_account_lease_with_time(lease_token, OperationTime::System)
    }

    #[cfg(test)]
    fn release_account_lease_at(
        &self,
        lease_token: &LeaseToken,
        now_ms: i64,
    ) -> Result<(), StoreError> {
        self.release_account_lease_with_time(lease_token, OperationTime::Fixed(now_ms))
    }

    fn release_account_lease_with_time(
        &self,
        lease_token: &LeaseToken,
        operation_time: OperationTime,
    ) -> Result<(), StoreError> {
        validate_lease_token(lease_token)?;
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now_ms = operation_time.resolve()?;
        require_fence(&transaction, lease_token, now_ms)?;

        transaction.execute(
            "UPDATE account_leases
             SET status = 'released', last_observed_at_ms = ?2, updated_at_ms = ?2
             WHERE account_id = ?1 AND owner_instance_id = ?3
               AND owner_boot_id = ?4 AND fence_epoch = ?5",
            params![
                lease_token.account_id,
                now_ms,
                lease_token.owner_instance_id,
                lease_token.owner_boot_id,
                lease_token.fence_epoch
            ],
        )?;
        transaction.commit()?;
        Ok(())
    }

    /// Atomically spools one inbound page and commits its cursor.
    ///
    /// Every receipt stores its complete payload as `Pending`; callers process
    /// only rows returned by `pending_inbound_receipts` and then acknowledge them
    /// with `mark_inbound_processed`. A one-event WebSocket delivery is recorded
    /// as a page containing one receipt. A newer `stream_generation` may reset the
    /// cursor, the same generation never moves it backwards, and an older
    /// generation is rejected.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid input, an inactive fencing epoch, an older
    /// stream generation, conflicting duplicate contents, backwards time, or
    /// database failure. Any error rolls back every receipt and the page cursor.
    pub fn record_inbound_page(
        &self,
        lease_token: &LeaseToken,
        stream: &str,
        stream_generation: i64,
        checkpoint: i64,
        receipts: &[InboundReceiptDraft],
    ) -> Result<InboundPageResult, StoreError> {
        self.record_inbound_page_with_time(
            lease_token,
            stream,
            stream_generation,
            checkpoint,
            receipts,
            OperationTime::System,
        )
    }

    #[cfg(test)]
    fn record_inbound_page_at(
        &self,
        lease_token: &LeaseToken,
        stream: &str,
        stream_generation: i64,
        checkpoint: i64,
        receipts: &[InboundReceiptDraft],
        now_ms: i64,
    ) -> Result<InboundPageResult, StoreError> {
        self.record_inbound_page_with_time(
            lease_token,
            stream,
            stream_generation,
            checkpoint,
            receipts,
            OperationTime::Fixed(now_ms),
        )
    }

    fn record_inbound_page_with_time(
        &self,
        lease_token: &LeaseToken,
        stream: &str,
        stream_generation: i64,
        checkpoint: i64,
        receipts: &[InboundReceiptDraft],
        operation_time: OperationTime,
    ) -> Result<InboundPageResult, StoreError> {
        validate_inbound_page_input(lease_token, stream, stream_generation, receipts)?;
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now_ms = operation_time.resolve()?;
        require_fence(&transaction, lease_token, now_ms)?;
        let page = InboundPageWrite {
            lease_token,
            stream,
            stream_generation,
            checkpoint,
            receipts,
            now_ms,
        };
        let stored_checkpoint =
            select_inbound_checkpoint(&transaction, &lease_token.account_id, stream)?;
        reject_stale_stream_generation(&page, stored_checkpoint.as_ref())?;
        let inserted_count = insert_inbound_page_receipts(&transaction, &page)?;
        commit_inbound_page_checkpoint(&transaction, &page, stored_checkpoint.as_ref())?;
        let durable_checkpoint =
            select_inbound_checkpoint(&transaction, &lease_token.account_id, stream)?.ok_or_else(
                || {
                    StoreError::SchemaInvariant(
                        "inbound page committed without a checkpoint row".to_owned(),
                    )
                },
            )?;
        transaction.commit()?;

        Ok(InboundPageResult {
            inserted_count,
            checkpoint: durable_checkpoint,
        })
    }

    /// Reads the durable cursor and credential generation for an inbound stream.
    ///
    /// # Errors
    ///
    /// Returns an error if the connection is unavailable or the query fails.
    pub fn inbound_checkpoint(
        &self,
        account_id: &str,
        stream: &str,
    ) -> Result<Option<InboundCheckpoint>, StoreError> {
        let connection = self.lock_connection()?;
        select_inbound_checkpoint(&connection, account_id, stream)
    }

    /// Enumerates durable pending inbound work for crash recovery.
    ///
    /// # Errors
    ///
    /// Returns an error for an inactive fencing epoch, zero limit, backwards
    /// time, corrupt persisted state, or database failure.
    pub fn pending_inbound_receipts(
        &self,
        lease_token: &LeaseToken,
        limit: u32,
    ) -> Result<Vec<InboundReceipt>, StoreError> {
        self.pending_inbound_receipts_with_time(lease_token, limit, OperationTime::System)
    }

    #[cfg(test)]
    fn pending_inbound_receipts_at(
        &self,
        lease_token: &LeaseToken,
        now_ms: i64,
        limit: u32,
    ) -> Result<Vec<InboundReceipt>, StoreError> {
        self.pending_inbound_receipts_with_time(lease_token, limit, OperationTime::Fixed(now_ms))
    }

    fn pending_inbound_receipts_with_time(
        &self,
        lease_token: &LeaseToken,
        limit: u32,
        operation_time: OperationTime,
    ) -> Result<Vec<InboundReceipt>, StoreError> {
        validate_lease_token(lease_token)?;
        if limit == 0 {
            return Err(StoreError::InvalidInput("limit must be greater than zero"));
        }

        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now_ms = operation_time.resolve()?;
        require_fence(&transaction, lease_token, now_ms)?;
        let receipts = load_pending_inbound_receipts(&transaction, &lease_token.account_id, limit)?;
        transaction.commit()?;
        Ok(receipts)
    }

    /// Marks one pending inbound receipt processed and clears its spool payload.
    ///
    /// The retained receipt/hash remains the idempotency guard. Repeating the
    /// acknowledgement returns `applied = false` and never restores the payload.
    ///
    /// # Errors
    ///
    /// Returns an error for an inactive fencing epoch, missing receipt, backwards
    /// time, corrupt persisted state, or database failure.
    pub fn mark_inbound_processed(
        &self,
        lease_token: &LeaseToken,
        stream: &str,
        stream_generation: i64,
        event_id: &str,
    ) -> Result<InboundProcessOutcome, StoreError> {
        self.mark_inbound_processed_with_time(
            lease_token,
            stream,
            stream_generation,
            event_id,
            OperationTime::System,
        )
    }

    #[cfg(test)]
    fn mark_inbound_processed_at(
        &self,
        lease_token: &LeaseToken,
        stream: &str,
        stream_generation: i64,
        event_id: &str,
        now_ms: i64,
    ) -> Result<InboundProcessOutcome, StoreError> {
        self.mark_inbound_processed_with_time(
            lease_token,
            stream,
            stream_generation,
            event_id,
            OperationTime::Fixed(now_ms),
        )
    }

    fn mark_inbound_processed_with_time(
        &self,
        lease_token: &LeaseToken,
        stream: &str,
        stream_generation: i64,
        event_id: &str,
        operation_time: OperationTime,
    ) -> Result<InboundProcessOutcome, StoreError> {
        validate_lease_token(lease_token)?;
        validate_non_empty(stream, "stream must not be empty")?;
        validate_non_empty(event_id, "event_id must not be empty")?;
        if stream_generation <= 0 {
            return Err(StoreError::InvalidInput(
                "stream_generation must be greater than zero",
            ));
        }

        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now_ms = operation_time.resolve()?;
        require_fence(&transaction, lease_token, now_ms)?;
        let account_id = lease_token.account_id.as_str();
        let current = load_inbound_receipt(
            &transaction,
            account_id,
            stream,
            stream_generation,
            event_id,
        )?;
        if current.status == InboundStatus::Processed {
            transaction.commit()?;
            return Ok(InboundProcessOutcome {
                applied: false,
                receipt: current,
            });
        }

        transaction.execute(
            "UPDATE inbound_receipts
             SET status = 'processed', payload = NULL, processed_at_ms = ?5,
                 processed_fence_epoch = ?6
             WHERE account_id = ?1 AND stream = ?2
               AND stream_generation = ?3 AND event_id = ?4
               AND status = 'pending'",
            params![
                account_id,
                stream,
                stream_generation,
                event_id,
                now_ms,
                lease_token.fence_epoch
            ],
        )?;
        let receipt = load_inbound_receipt(
            &transaction,
            account_id,
            stream,
            stream_generation,
            event_id,
        )?;
        transaction.commit()?;
        Ok(InboundProcessOutcome {
            applied: true,
            receipt,
        })
    }

    /// Claims one trigger and creates its durable outbound plan exactly once.
    ///
    /// `(account_id, trigger_id)` is the persistent reply claim. Repeated calls
    /// return the same batch and segment IDs only when `response_id` and every
    /// segment are byte-identical; changing the response cannot create a second
    /// reply for the same trigger.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid input, an inactive fencing epoch, conflicting
    /// contents for an existing idempotency key, or database failure.
    pub fn prepare_outbound_batch(
        &self,
        lease_token: &LeaseToken,
        trigger_id: &str,
        response_id: &str,
        segments: &[OutboundSegmentDraft],
    ) -> Result<OutboundBatch, StoreError> {
        self.prepare_outbound_batch_with_time(
            lease_token,
            trigger_id,
            response_id,
            segments,
            OperationTime::System,
        )
    }

    #[cfg(test)]
    fn prepare_outbound_batch_at(
        &self,
        lease_token: &LeaseToken,
        trigger_id: &str,
        response_id: &str,
        segments: &[OutboundSegmentDraft],
        now_ms: i64,
    ) -> Result<OutboundBatch, StoreError> {
        self.prepare_outbound_batch_with_time(
            lease_token,
            trigger_id,
            response_id,
            segments,
            OperationTime::Fixed(now_ms),
        )
    }

    fn prepare_outbound_batch_with_time(
        &self,
        lease_token: &LeaseToken,
        trigger_id: &str,
        response_id: &str,
        segments: &[OutboundSegmentDraft],
        operation_time: OperationTime,
    ) -> Result<OutboundBatch, StoreError> {
        validate_lease_token(lease_token)?;
        validate_non_empty(trigger_id, "trigger_id must not be empty")?;
        validate_non_empty(response_id, "response_id must not be empty")?;
        if segments.is_empty() {
            return Err(StoreError::InvalidInput(
                "outbound batch must contain at least one segment",
            ));
        }
        for segment in segments {
            validate_non_empty(&segment.kind, "segment kind must not be empty")?;
            validate_non_empty(&segment.payload, "segment payload must not be empty")?;
        }
        let account_id = lease_token.account_id.as_str();

        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now_ms = operation_time.resolve()?;
        require_fence(&transaction, lease_token, now_ms)?;

        let existing_batch_id = transaction
            .query_row(
                "SELECT id FROM outbound_batches
                 WHERE account_id = ?1 AND trigger_id = ?2",
                params![account_id, trigger_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?;

        if let Some(batch_id) = existing_batch_id {
            let batch = load_batch(&transaction, &batch_id)?;
            if batch.response_id != response_id || !same_segment_plan(&batch.segments, segments) {
                return Err(StoreError::IdempotencyConflict {
                    entity: "outbound batch",
                    key: format!("{account_id}/{trigger_id}"),
                });
            }
            transaction.commit()?;
            return Ok(batch);
        }

        let batch_id = Uuid::new_v4().to_string();
        transaction.execute(
            "INSERT INTO outbound_batches
             (id, account_id, trigger_id, response_id, status,
              created_fence_epoch, last_fence_epoch, created_at_ms, updated_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, ?7, ?7)",
            params![
                batch_id,
                account_id,
                trigger_id,
                response_id,
                BatchStatus::Prepared.as_str(),
                lease_token.fence_epoch,
                now_ms
            ],
        )?;

        for (ordinal, segment) in segments.iter().enumerate() {
            let ordinal = u32::try_from(ordinal)
                .map_err(|_| StoreError::InvalidInput("outbound batch has too many segments"))?;
            transaction.execute(
                "INSERT INTO outbound_segments
                 (id, client_message_id, batch_id, ordinal, kind, payload, status,
                  attempt_count, last_fence_epoch, created_at_ms, updated_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 0, ?8, ?9, ?9)",
                params![
                    Uuid::new_v4().to_string(),
                    Uuid::new_v4().to_string(),
                    batch_id,
                    ordinal,
                    segment.kind,
                    segment.payload,
                    SegmentStatus::Prepared.as_str(),
                    lease_token.fence_epoch,
                    now_ms
                ],
            )?;
        }

        let batch = load_batch(&transaction, &batch_id)?;
        transaction.commit()?;
        Ok(batch)
    }

    /// Loads one outbound batch and all of its ordered segments.
    ///
    /// # Errors
    ///
    /// Returns an error when the batch does not exist, persisted data is invalid,
    /// or the query fails.
    pub fn outbound_batch(&self, batch_id: &str) -> Result<OutboundBatch, StoreError> {
        let connection = self.lock_connection()?;
        load_batch(&connection, batch_id)
    }

    /// Applies the centralized segment state machine under an active account
    /// fencing epoch and recomputes the parent batch status atomically.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid transition details, a missing segment, an
    /// inactive fencing epoch, an invalid state transition, or database failure.
    pub fn transition_segment(
        &self,
        lease_token: &LeaseToken,
        segment_id: &str,
        transition: SegmentTransition,
    ) -> Result<TransitionOutcome, StoreError> {
        self.transition_segment_with_time(
            lease_token,
            segment_id,
            transition,
            OperationTime::System,
        )
    }

    #[cfg(test)]
    fn transition_segment_at(
        &self,
        lease_token: &LeaseToken,
        segment_id: &str,
        transition: SegmentTransition,
        now_ms: i64,
    ) -> Result<TransitionOutcome, StoreError> {
        self.transition_segment_with_time(
            lease_token,
            segment_id,
            transition,
            OperationTime::Fixed(now_ms),
        )
    }

    fn transition_segment_with_time(
        &self,
        lease_token: &LeaseToken,
        segment_id: &str,
        transition: SegmentTransition,
        operation_time: OperationTime,
    ) -> Result<TransitionOutcome, StoreError> {
        validate_lease_token(lease_token)?;
        validate_non_empty(segment_id, "segment_id must not be empty")?;
        validate_transition_details(&transition)?;
        let account_id = lease_token.account_id.as_str();

        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now_ms = operation_time.resolve()?;
        require_fence(&transaction, lease_token, now_ms)?;

        let raw = transaction
            .query_row(
                "SELECT s.batch_id, s.status, s.platform_message_id, s.last_error,
                        b.account_id
                 FROM outbound_segments AS s
                 JOIN outbound_batches AS b ON b.id = s.batch_id
                 WHERE s.id = ?1",
                params![segment_id],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, Option<String>>(2)?,
                        row.get::<_, Option<String>>(3)?,
                        row.get::<_, String>(4)?,
                    ))
                },
            )
            .optional()?
            .ok_or_else(|| StoreError::NotFound {
                entity: "outbound segment",
                id: segment_id.to_owned(),
            })?;
        let (batch_id, raw_status, stored_platform_message_id, stored_error, stored_account_id) =
            raw;
        if stored_account_id != account_id {
            return Err(StoreError::NotFound {
                entity: "outbound segment",
                id: segment_id.to_owned(),
            });
        }

        let current = SegmentStatus::parse(&raw_status)?;
        let target = transition.target_status();
        if current == target {
            validate_repeated_transition(
                segment_id,
                &transition,
                stored_platform_message_id.as_deref(),
                stored_error.as_deref(),
            )?;
            let batch = load_batch(&transaction, &batch_id)?;
            transaction.commit()?;
            return Ok(TransitionOutcome {
                applied: false,
                batch,
            });
        }

        if !valid_segment_transition(current, target) {
            return Err(StoreError::InvalidTransition {
                entity: "outbound segment",
                from: current.as_str().to_owned(),
                to: target.as_str().to_owned(),
            });
        }

        apply_segment_transition(
            &transaction,
            segment_id,
            lease_token.fence_epoch,
            transition,
            now_ms,
        )?;

        let batch_status = derive_batch_status(&transaction, &batch_id)?;
        transaction.execute(
            "UPDATE outbound_batches
             SET status = ?2, last_fence_epoch = ?3, updated_at_ms = ?4
             WHERE id = ?1",
            params![
                batch_id,
                batch_status.as_str(),
                lease_token.fence_epoch,
                now_ms
            ],
        )?;
        let batch = load_batch(&transaction, &batch_id)?;
        transaction.commit()?;
        Ok(TransitionOutcome {
            applied: true,
            batch,
        })
    }

    /// Enumerates outbound batches that still require send or reconciliation work.
    ///
    /// A higher verified lease epoch has already converted prior-epoch `Sending`
    /// segments to `Uncertain`; callers must reconcile those outcomes rather than
    /// sending them again. Fully confirmed/rejected batches are omitted.
    ///
    /// # Errors
    ///
    /// Returns an error for an inactive fencing epoch, zero limit, backwards
    /// time, corrupt persisted state, or database failure.
    pub fn unfinished_outbound_batches(
        &self,
        lease_token: &LeaseToken,
        limit: u32,
    ) -> Result<Vec<OutboundBatch>, StoreError> {
        self.unfinished_outbound_batches_with_time(lease_token, limit, OperationTime::System)
    }

    #[cfg(test)]
    fn unfinished_outbound_batches_at(
        &self,
        lease_token: &LeaseToken,
        now_ms: i64,
        limit: u32,
    ) -> Result<Vec<OutboundBatch>, StoreError> {
        self.unfinished_outbound_batches_with_time(lease_token, limit, OperationTime::Fixed(now_ms))
    }

    fn unfinished_outbound_batches_with_time(
        &self,
        lease_token: &LeaseToken,
        limit: u32,
        operation_time: OperationTime,
    ) -> Result<Vec<OutboundBatch>, StoreError> {
        validate_lease_token(lease_token)?;
        if limit == 0 {
            return Err(StoreError::InvalidInput("limit must be greater than zero"));
        }

        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now_ms = operation_time.resolve()?;
        require_fence(&transaction, lease_token, now_ms)?;
        let batch_ids = {
            let mut statement = transaction.prepare(
                "SELECT b.id
                 FROM outbound_batches AS b
                 WHERE b.account_id = ?1
                   AND EXISTS (
                     SELECT 1 FROM outbound_segments AS s
                     WHERE s.batch_id = b.id
                       AND s.status IN ('prepared', 'sending', 'retryable', 'uncertain')
                   )
                 ORDER BY b.created_at_ms, b.id
                 LIMIT ?2",
            )?;
            let rows = statement
                .query_map(params![lease_token.account_id, i64::from(limit)], |row| {
                    row.get::<_, String>(0)
                })?;
            rows.collect::<Result<Vec<_>, _>>()?
        };
        let mut batches = Vec::with_capacity(batch_ids.len());
        for batch_id in batch_ids {
            batches.push(load_batch(&transaction, &batch_id)?);
        }
        transaction.commit()?;
        Ok(batches)
    }

    fn lock_connection(&self) -> Result<MutexGuard<'_, Connection>, StoreError> {
        self.connection.lock().map_err(|_| StoreError::LockPoisoned)
    }
}

fn initialize_new_database(connection: &mut Connection) -> Result<Uuid, StoreError> {
    let database_id = Uuid::new_v4();
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    transaction.execute_batch(
        "CREATE TABLE meta (
             key TEXT PRIMARY KEY NOT NULL,
             value TEXT NOT NULL
         );",
    )?;
    transaction.execute_batch(SCHEMA_V1_SQL)?;
    transaction.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?1)",
        params![CORE_SCHEMA_VERSION.to_string()],
    )?;
    transaction.execute(
        "INSERT INTO meta(key, value) VALUES (?1, ?2)",
        params![DATABASE_ID_META_KEY, database_id.to_string()],
    )?;
    transaction.pragma_update(None, "user_version", CORE_SCHEMA_VERSION)?;
    transaction.commit()?;
    let durable_database_id = validate_existing_database(connection)?;
    if durable_database_id != database_id {
        return Err(StoreError::SchemaInvariant(
            "new database identity changed during initialization".to_owned(),
        ));
    }
    Ok(database_id)
}

fn validate_existing_database(connection: &Connection) -> Result<Uuid, StoreError> {
    if !table_exists(connection, "meta")? {
        return Err(StoreError::SchemaInvariant(
            "required table \"meta\" is missing".to_owned(),
        ));
    }

    let found = read_schema_version(connection)?;
    if found != CORE_SCHEMA_VERSION {
        return Err(StoreError::UnsupportedSchema {
            found,
            supported: CORE_SCHEMA_VERSION,
        });
    }
    validate_schema_v1(connection)?;
    read_database_id(connection)
}

fn validate_schema_v1(connection: &Connection) -> Result<(), StoreError> {
    let user_version: u32 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    if user_version != CORE_SCHEMA_VERSION {
        return Err(StoreError::SchemaInvariant(format!(
            "PRAGMA user_version is {user_version}, expected {CORE_SCHEMA_VERSION}"
        )));
    }

    for (table, required_columns) in REQUIRED_TABLES {
        if !table_exists(connection, table)? {
            return Err(StoreError::SchemaInvariant(format!(
                "required table {table:?} is missing"
            )));
        }

        let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
        let rows = statement.query_map([], |row| row.get::<_, String>(1))?;
        let columns = rows.collect::<Result<Vec<_>, _>>()?;
        for column in *required_columns {
            if !columns.iter().any(|candidate| candidate == column) {
                return Err(StoreError::SchemaInvariant(format!(
                    "required column {table}.{column} is missing"
                )));
            }
        }
    }

    let pending_index_exists = connection
        .query_row(
            "SELECT 1 FROM sqlite_master
             WHERE type = 'index' AND name = 'inbound_receipts_pending_idx'",
            [],
            |_| Ok(()),
        )
        .optional()?
        .is_some();
    if !pending_index_exists {
        return Err(StoreError::SchemaInvariant(
            "required index inbound_receipts_pending_idx is missing".to_owned(),
        ));
    }
    Ok(())
}

fn table_exists(connection: &Connection, table: &str) -> Result<bool, StoreError> {
    Ok(connection
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?1",
            params![table],
            |_| Ok(()),
        )
        .optional()?
        .is_some())
}

fn database_schema_is_empty(connection: &Connection) -> Result<bool, StoreError> {
    let object_count: i64 =
        connection.query_row("SELECT COUNT(*) FROM sqlite_master", [], |row| row.get(0))?;
    Ok(object_count == 0)
}

fn read_optional_schema_version(connection: &Connection) -> Result<Option<u32>, StoreError> {
    let value = connection
        .query_row(
            "SELECT value FROM meta WHERE key = 'schema_version'",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    value
        .map(|raw| {
            raw.parse::<u32>()
                .map_err(|_| StoreError::InvalidSchemaVersion(raw))
        })
        .transpose()
}

fn read_schema_version(connection: &Connection) -> Result<u32, StoreError> {
    read_optional_schema_version(connection)?
        .ok_or_else(|| StoreError::InvalidSchemaVersion("missing meta.schema_version".to_owned()))
}

fn read_database_id(connection: &Connection) -> Result<Uuid, StoreError> {
    let raw = connection
        .query_row(
            "SELECT value FROM meta WHERE key = ?1",
            params![DATABASE_ID_META_KEY],
            |row| row.get::<_, String>(0),
        )
        .optional()?
        .ok_or_else(|| {
            StoreError::SchemaInvariant("required meta.database_id is missing".to_owned())
        })?;
    parse_database_id(&raw, "meta.database_id")
}

fn read_initialized_marker(marker_path: &Path) -> Result<Option<Uuid>, StoreError> {
    if !marker_path.try_exists()? {
        return Ok(None);
    }
    let raw = fs::read_to_string(marker_path)?;
    parse_database_id(raw.trim(), "core.initialized").map(Some)
}

fn parse_database_id(raw: &str, field: &'static str) -> Result<Uuid, StoreError> {
    Uuid::parse_str(raw).map_err(|_| StoreError::CorruptData {
        field,
        value: raw.to_owned(),
    })
}

fn write_initialized_marker(
    data_dir: &Path,
    marker_path: &Path,
    database_id: Uuid,
) -> Result<(), StoreError> {
    if let Some(existing_database_id) = read_initialized_marker(marker_path)? {
        if existing_database_id != database_id {
            return Err(StoreError::DatabaseIdentityMismatch {
                marker_database_id: existing_database_id,
                database_database_id: database_id,
            });
        }
        return Ok(());
    }

    let temporary_path = data_dir.join(format!(
        ".{INITIALIZED_MARKER_FILE_NAME}.{}.tmp",
        Uuid::new_v4()
    ));
    let write_result = (|| -> Result<(), StoreError> {
        let mut marker = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary_path)?;
        writeln!(marker, "{database_id}")?;
        marker.sync_all()?;
        drop(marker);
        fs::rename(&temporary_path, marker_path)?;
        sync_directory(data_dir)?;
        Ok(())
    })();
    if write_result.is_err() {
        let _ = fs::remove_file(&temporary_path);
    }
    write_result
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), StoreError> {
    fs::File::open(path)?.sync_all()?;
    Ok(())
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), StoreError> {
    Ok(())
}

#[derive(Clone, Copy)]
enum OperationTime {
    System,
    #[cfg(test)]
    Fixed(i64),
}

impl OperationTime {
    fn resolve(self) -> Result<i64, StoreError> {
        match self {
            Self::System => {
                let elapsed = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .map_err(|_| StoreError::SystemClockBeforeUnixEpoch)?;
                i64::try_from(elapsed.as_millis()).map_err(|_| StoreError::SystemTimeOverflow)
            }
            #[cfg(test)]
            Self::Fixed(now_ms) => Ok(now_ms),
        }
    }
}

struct VerifiedLeaseInstall<'a> {
    account_id: &'a str,
    owner_instance_id: &'a str,
    owner_boot_id: &'a str,
    fence_epoch: i64,
    now_ms: i64,
    lease_until_ms: i64,
}

impl VerifiedLeaseInstall<'_> {
    fn validate(&self) -> Result<(), StoreError> {
        validate_non_empty(self.account_id, "account_id must not be empty")?;
        validate_non_empty(
            self.owner_instance_id,
            "owner_instance_id must not be empty",
        )?;
        validate_non_empty(self.owner_boot_id, "owner_boot_id must not be empty")?;
        if self.fence_epoch <= 0 {
            return Err(StoreError::InvalidInput(
                "fence_epoch must be greater than zero",
            ));
        }
        if self.lease_until_ms <= self.now_ms {
            return Err(StoreError::InvalidInput(
                "lease_until_ms must be greater than now_ms",
            ));
        }
        Ok(())
    }

    fn to_lease(&self) -> AccountLease {
        AccountLease {
            account_id: self.account_id.to_owned(),
            owner_instance_id: self.owner_instance_id.to_owned(),
            owner_boot_id: self.owner_boot_id.to_owned(),
            fence_epoch: self.fence_epoch,
            lease_until_ms: self.lease_until_ms,
            status: LeaseStatus::Active,
            last_observed_at_ms: self.now_ms,
        }
    }
}

fn install_verified_lease_in_transaction(
    transaction: &Transaction<'_>,
    requested: &VerifiedLeaseInstall<'_>,
) -> Result<AccountLease, StoreError> {
    match select_lease(transaction, requested.account_id)? {
        None => insert_first_verified_lease(transaction, requested),
        Some(current) => update_verified_lease(transaction, requested, current),
    }
}

fn insert_first_verified_lease(
    transaction: &Transaction<'_>,
    requested: &VerifiedLeaseInstall<'_>,
) -> Result<AccountLease, StoreError> {
    let lease = requested.to_lease();
    transaction.execute(
        "INSERT INTO account_leases
         (account_id, owner_instance_id, owner_boot_id, fence_epoch,
          lease_until_ms, status, last_observed_at_ms, updated_at_ms)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?7)",
        params![
            lease.account_id,
            lease.owner_instance_id,
            lease.owner_boot_id,
            lease.fence_epoch,
            lease.lease_until_ms,
            lease.status.as_str(),
            lease.last_observed_at_ms
        ],
    )?;
    Ok(lease)
}

fn update_verified_lease(
    transaction: &Transaction<'_>,
    requested: &VerifiedLeaseInstall<'_>,
    mut current: AccountLease,
) -> Result<AccountLease, StoreError> {
    if requested.fence_epoch < current.fence_epoch {
        return Err(StoreError::StaleFence {
            account_id: requested.account_id.to_owned(),
            current_epoch: current.fence_epoch,
            provided_epoch: requested.fence_epoch,
        });
    }
    reject_clock_regression(&current, requested.now_ms)?;
    if requested.fence_epoch == current.fence_epoch {
        return extend_verified_lease(transaction, requested, current);
    }

    current = requested.to_lease();
    transaction.execute(
        "UPDATE account_leases
         SET owner_instance_id = ?2, owner_boot_id = ?3,
             fence_epoch = ?4, lease_until_ms = ?5, status = ?6,
             last_observed_at_ms = ?7, updated_at_ms = ?7
         WHERE account_id = ?1",
        params![
            current.account_id,
            current.owner_instance_id,
            current.owner_boot_id,
            current.fence_epoch,
            current.lease_until_ms,
            current.status.as_str(),
            current.last_observed_at_ms
        ],
    )?;
    recover_interrupted_sends_in_transaction(
        transaction,
        requested.account_id,
        requested.fence_epoch,
        requested.now_ms,
    )?;
    Ok(current)
}

fn extend_verified_lease(
    transaction: &Transaction<'_>,
    requested: &VerifiedLeaseInstall<'_>,
    mut current: AccountLease,
) -> Result<AccountLease, StoreError> {
    if current.status == LeaseStatus::Released {
        return Err(StoreError::LeaseReleased {
            account_id: requested.account_id.to_owned(),
            fence_epoch: requested.fence_epoch,
        });
    }
    if current.owner_instance_id != requested.owner_instance_id
        || current.owner_boot_id != requested.owner_boot_id
    {
        return Err(StoreError::LeaseOwnerMismatch {
            account_id: requested.account_id.to_owned(),
            current_instance_id: current.owner_instance_id,
            current_boot_id: current.owner_boot_id,
            provided_instance_id: requested.owner_instance_id.to_owned(),
            provided_boot_id: requested.owner_boot_id.to_owned(),
        });
    }

    current.lease_until_ms = current.lease_until_ms.max(requested.lease_until_ms);
    current.last_observed_at_ms = requested.now_ms;
    transaction.execute(
        "UPDATE account_leases
         SET lease_until_ms = ?2, last_observed_at_ms = ?3, updated_at_ms = ?3
         WHERE account_id = ?1",
        params![
            requested.account_id,
            current.lease_until_ms,
            requested.now_ms
        ],
    )?;
    Ok(current)
}

fn select_lease(
    transaction: &Transaction<'_>,
    account_id: &str,
) -> Result<Option<AccountLease>, StoreError> {
    let raw = transaction
        .query_row(
            "SELECT account_id, owner_instance_id, owner_boot_id, fence_epoch,
                    lease_until_ms, status, last_observed_at_ms
             FROM account_leases WHERE account_id = ?1",
            params![account_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, i64>(3)?,
                    row.get::<_, i64>(4)?,
                    row.get::<_, String>(5)?,
                    row.get::<_, i64>(6)?,
                ))
            },
        )
        .optional()?;
    raw.map(
        |(
            account_id,
            owner_instance_id,
            owner_boot_id,
            fence_epoch,
            lease_until_ms,
            status,
            last_observed_at_ms,
        )| {
            Ok(AccountLease {
                account_id,
                owner_instance_id,
                owner_boot_id,
                fence_epoch,
                lease_until_ms,
                status: LeaseStatus::parse(&status)?,
                last_observed_at_ms,
            })
        },
    )
    .transpose()
}

fn require_fence(
    transaction: &Transaction<'_>,
    lease_token: &LeaseToken,
    now_ms: i64,
) -> Result<AccountLease, StoreError> {
    let account_id = lease_token.account_id.as_str();
    let mut lease =
        select_lease(transaction, account_id)?.ok_or_else(|| StoreError::LeaseNotFound {
            account_id: account_id.to_owned(),
        })?;
    if lease.fence_epoch != lease_token.fence_epoch {
        return Err(StoreError::StaleFence {
            account_id: account_id.to_owned(),
            current_epoch: lease.fence_epoch,
            provided_epoch: lease_token.fence_epoch,
        });
    }
    if lease.owner_instance_id != lease_token.owner_instance_id
        || lease.owner_boot_id != lease_token.owner_boot_id
    {
        return Err(StoreError::LeaseOwnerMismatch {
            account_id: account_id.to_owned(),
            current_instance_id: lease.owner_instance_id,
            current_boot_id: lease.owner_boot_id,
            provided_instance_id: lease_token.owner_instance_id.clone(),
            provided_boot_id: lease_token.owner_boot_id.clone(),
        });
    }
    if lease.status == LeaseStatus::Released {
        return Err(StoreError::LeaseReleased {
            account_id: account_id.to_owned(),
            fence_epoch: lease.fence_epoch,
        });
    }
    reject_clock_regression(&lease, now_ms)?;
    if lease.lease_until_ms <= now_ms {
        return Err(StoreError::LeaseExpired {
            account_id: account_id.to_owned(),
            lease_until_ms: lease.lease_until_ms,
            now_ms,
        });
    }
    transaction.execute(
        "UPDATE account_leases
         SET last_observed_at_ms = ?2, updated_at_ms = ?2
         WHERE account_id = ?1",
        params![account_id, now_ms],
    )?;
    lease.last_observed_at_ms = now_ms;
    Ok(lease)
}

fn reject_clock_regression(lease: &AccountLease, now_ms: i64) -> Result<(), StoreError> {
    if now_ms < lease.last_observed_at_ms {
        Err(StoreError::ClockRegression {
            account_id: lease.account_id.clone(),
            last_observed_at_ms: lease.last_observed_at_ms,
            provided_now_ms: now_ms,
        })
    } else {
        Ok(())
    }
}

struct InboundPageWrite<'a> {
    lease_token: &'a LeaseToken,
    stream: &'a str,
    stream_generation: i64,
    checkpoint: i64,
    receipts: &'a [InboundReceiptDraft],
    now_ms: i64,
}

fn validate_inbound_page_input(
    lease_token: &LeaseToken,
    stream: &str,
    stream_generation: i64,
    receipts: &[InboundReceiptDraft],
) -> Result<(), StoreError> {
    validate_lease_token(lease_token)?;
    validate_non_empty(stream, "stream must not be empty")?;
    if stream_generation <= 0 {
        return Err(StoreError::InvalidInput(
            "stream_generation must be greater than zero",
        ));
    }
    for receipt in receipts {
        validate_non_empty(&receipt.event_id, "event_id must not be empty")?;
        validate_non_empty(&receipt.payload_hash, "payload_hash must not be empty")?;
        if receipt.payload.is_empty() {
            return Err(StoreError::InvalidInput(
                "inbound payload must not be empty",
            ));
        }
    }
    Ok(())
}

fn reject_stale_stream_generation(
    page: &InboundPageWrite<'_>,
    stored: Option<&InboundCheckpoint>,
) -> Result<(), StoreError> {
    if let Some(stored) = stored {
        if page.stream_generation < stored.stream_generation {
            return Err(StoreError::StaleStreamGeneration {
                account_id: page.lease_token.account_id.clone(),
                stream: page.stream.to_owned(),
                current_generation: stored.stream_generation,
                provided_generation: page.stream_generation,
            });
        }
    }
    Ok(())
}

fn insert_inbound_page_receipts(
    transaction: &Transaction<'_>,
    page: &InboundPageWrite<'_>,
) -> Result<usize, StoreError> {
    let account_id = page.lease_token.account_id.as_str();
    let mut inserted_count = 0_usize;
    for receipt in page.receipts {
        let existing = transaction
            .query_row(
                "SELECT payload, payload_hash
                 FROM inbound_receipts
                 WHERE account_id = ?1 AND stream = ?2
                   AND stream_generation = ?3 AND event_id = ?4",
                params![
                    account_id,
                    page.stream,
                    page.stream_generation,
                    receipt.event_id
                ],
                |row| Ok((row.get::<_, Option<Vec<u8>>>(0)?, row.get::<_, String>(1)?)),
            )
            .optional()?;
        match existing {
            None => {
                insert_inbound_receipt(transaction, page, receipt)?;
                inserted_count += 1;
            }
            Some((stored_payload, stored_hash)) => {
                verify_inbound_duplicate(page, receipt, stored_payload.as_deref(), &stored_hash)?;
            }
        }
    }
    Ok(inserted_count)
}

fn insert_inbound_receipt(
    transaction: &Transaction<'_>,
    page: &InboundPageWrite<'_>,
    receipt: &InboundReceiptDraft,
) -> Result<(), StoreError> {
    transaction.execute(
        "INSERT INTO inbound_receipts
         (account_id, stream, stream_generation, event_id, page_checkpoint,
          payload, payload_hash, status, fence_epoch, received_at_ms)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
        params![
            page.lease_token.account_id,
            page.stream,
            page.stream_generation,
            receipt.event_id,
            page.checkpoint,
            receipt.payload,
            receipt.payload_hash,
            InboundStatus::Pending.as_str(),
            page.lease_token.fence_epoch,
            page.now_ms
        ],
    )?;
    Ok(())
}

fn verify_inbound_duplicate(
    page: &InboundPageWrite<'_>,
    receipt: &InboundReceiptDraft,
    stored_payload: Option<&[u8]>,
    stored_hash: &str,
) -> Result<(), StoreError> {
    let payload_conflicts =
        stored_payload.is_some_and(|payload| payload != receipt.payload.as_slice());
    if stored_hash != receipt.payload_hash || payload_conflicts {
        Err(StoreError::IdempotencyConflict {
            entity: "inbound receipt",
            key: format!(
                "{}/{}/{}/{}",
                page.lease_token.account_id, page.stream, page.stream_generation, receipt.event_id
            ),
        })
    } else {
        Ok(())
    }
}

fn commit_inbound_page_checkpoint(
    transaction: &Transaction<'_>,
    page: &InboundPageWrite<'_>,
    stored: Option<&InboundCheckpoint>,
) -> Result<(), StoreError> {
    match stored {
        None => transaction.execute(
            "INSERT INTO inbound_checkpoints
             (account_id, stream, stream_generation, checkpoint,
              fence_epoch, updated_at_ms)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                page.lease_token.account_id,
                page.stream,
                page.stream_generation,
                page.checkpoint,
                page.lease_token.fence_epoch,
                page.now_ms
            ],
        )?,
        Some(stored) if page.stream_generation > stored.stream_generation => transaction.execute(
            "UPDATE inbound_checkpoints
             SET stream_generation = ?3, checkpoint = ?4,
                 fence_epoch = ?5, updated_at_ms = ?6
             WHERE account_id = ?1 AND stream = ?2",
            params![
                page.lease_token.account_id,
                page.stream,
                page.stream_generation,
                page.checkpoint,
                page.lease_token.fence_epoch,
                page.now_ms
            ],
        )?,
        Some(stored) if page.checkpoint > stored.checkpoint => transaction.execute(
            "UPDATE inbound_checkpoints
             SET checkpoint = ?3, fence_epoch = ?4, updated_at_ms = ?5
             WHERE account_id = ?1 AND stream = ?2",
            params![
                page.lease_token.account_id,
                page.stream,
                page.checkpoint,
                page.lease_token.fence_epoch,
                page.now_ms
            ],
        )?,
        Some(_) => 0,
    };
    Ok(())
}

fn select_inbound_checkpoint(
    connection: &Connection,
    account_id: &str,
    stream: &str,
) -> Result<Option<InboundCheckpoint>, StoreError> {
    Ok(connection
        .query_row(
            "SELECT stream_generation, checkpoint
             FROM inbound_checkpoints
             WHERE account_id = ?1 AND stream = ?2",
            params![account_id, stream],
            |row| {
                Ok(InboundCheckpoint {
                    stream_generation: row.get(0)?,
                    checkpoint: row.get(1)?,
                })
            },
        )
        .optional()?)
}

struct PersistedInboundReceipt {
    account_id: String,
    stream: String,
    stream_generation: i64,
    event_id: String,
    page_checkpoint: i64,
    payload: Option<Vec<u8>>,
    payload_hash: String,
    status: String,
    received_at_ms: i64,
    processed_at_ms: Option<i64>,
}

fn read_inbound_receipt_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<PersistedInboundReceipt> {
    Ok(PersistedInboundReceipt {
        account_id: row.get(0)?,
        stream: row.get(1)?,
        stream_generation: row.get(2)?,
        event_id: row.get(3)?,
        page_checkpoint: row.get(4)?,
        payload: row.get(5)?,
        payload_hash: row.get(6)?,
        status: row.get(7)?,
        received_at_ms: row.get(8)?,
        processed_at_ms: row.get(9)?,
    })
}

fn decode_inbound_receipt(raw: PersistedInboundReceipt) -> Result<InboundReceipt, StoreError> {
    let status = InboundStatus::parse(&raw.status)?;
    if (status == InboundStatus::Pending) != raw.payload.is_some() {
        return Err(StoreError::SchemaInvariant(format!(
            "inbound receipt {}/{}/{}/{} has inconsistent status/payload",
            raw.account_id, raw.stream, raw.stream_generation, raw.event_id
        )));
    }
    Ok(InboundReceipt {
        account_id: raw.account_id,
        stream: raw.stream,
        stream_generation: raw.stream_generation,
        event_id: raw.event_id,
        page_checkpoint: raw.page_checkpoint,
        payload: raw.payload,
        payload_hash: raw.payload_hash,
        status,
        received_at_ms: raw.received_at_ms,
        processed_at_ms: raw.processed_at_ms,
    })
}

fn load_inbound_receipt(
    connection: &Connection,
    account_id: &str,
    stream: &str,
    stream_generation: i64,
    event_id: &str,
) -> Result<InboundReceipt, StoreError> {
    let raw = connection
        .query_row(
            "SELECT account_id, stream, stream_generation, event_id,
                    page_checkpoint, payload, payload_hash, status,
                    received_at_ms, processed_at_ms
             FROM inbound_receipts
             WHERE account_id = ?1 AND stream = ?2
               AND stream_generation = ?3 AND event_id = ?4",
            params![account_id, stream, stream_generation, event_id],
            read_inbound_receipt_row,
        )
        .optional()?
        .ok_or_else(|| StoreError::NotFound {
            entity: "inbound receipt",
            id: format!("{account_id}/{stream}/{stream_generation}/{event_id}"),
        })?;
    decode_inbound_receipt(raw)
}

fn load_pending_inbound_receipts(
    connection: &Connection,
    account_id: &str,
    limit: u32,
) -> Result<Vec<InboundReceipt>, StoreError> {
    let mut statement = connection.prepare(
        "SELECT account_id, stream, stream_generation, event_id,
                page_checkpoint, payload, payload_hash, status,
                received_at_ms, processed_at_ms
         FROM inbound_receipts
         WHERE account_id = ?1 AND status = 'pending'
         ORDER BY received_at_ms, stream, stream_generation, event_id
         LIMIT ?2",
    )?;
    let rows = statement.query_map(
        params![account_id, i64::from(limit)],
        read_inbound_receipt_row,
    )?;
    let mut receipts = Vec::new();
    for row in rows {
        receipts.push(decode_inbound_receipt(row?)?);
    }
    Ok(receipts)
}

fn load_batch(connection: &Connection, batch_id: &str) -> Result<OutboundBatch, StoreError> {
    let raw = connection
        .query_row(
            "SELECT id, account_id, trigger_id, response_id, status,
                    created_fence_epoch, last_fence_epoch
             FROM outbound_batches WHERE id = ?1",
            params![batch_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, String>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, i64>(6)?,
                ))
            },
        )
        .optional()?
        .ok_or_else(|| StoreError::NotFound {
            entity: "outbound batch",
            id: batch_id.to_owned(),
        })?;

    let mut statement = connection.prepare(
        "SELECT id, client_message_id, batch_id, ordinal, kind, payload, status,
                attempt_count, platform_message_id, last_error, last_fence_epoch
         FROM outbound_segments WHERE batch_id = ?1 ORDER BY ordinal",
    )?;
    let rows = statement.query_map(params![batch_id], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, u32>(3)?,
            row.get::<_, String>(4)?,
            row.get::<_, String>(5)?,
            row.get::<_, String>(6)?,
            row.get::<_, u32>(7)?,
            row.get::<_, Option<String>>(8)?,
            row.get::<_, Option<String>>(9)?,
            row.get::<_, i64>(10)?,
        ))
    })?;
    let mut segments = Vec::new();
    for row in rows {
        let (
            id,
            client_message_id,
            stored_batch_id,
            ordinal,
            kind,
            payload,
            raw_status,
            attempt_count,
            platform_message_id,
            last_error,
            last_fence_epoch,
        ) = row?;
        segments.push(OutboundSegment {
            id,
            client_message_id,
            batch_id: stored_batch_id,
            ordinal,
            kind,
            payload,
            status: SegmentStatus::parse(&raw_status)?,
            attempt_count,
            platform_message_id,
            last_error,
            last_fence_epoch,
        });
    }

    Ok(OutboundBatch {
        id: raw.0,
        account_id: raw.1,
        trigger_id: raw.2,
        response_id: raw.3,
        status: BatchStatus::parse(&raw.4)?,
        created_fence_epoch: raw.5,
        last_fence_epoch: raw.6,
        segments,
    })
}

fn same_segment_plan(stored: &[OutboundSegment], requested: &[OutboundSegmentDraft]) -> bool {
    stored.len() == requested.len()
        && stored
            .iter()
            .zip(requested)
            .all(|(left, right)| left.kind == right.kind && left.payload == right.payload)
}

fn recover_interrupted_sends_in_transaction(
    transaction: &Transaction<'_>,
    account_id: &str,
    new_fence_epoch: i64,
    now_ms: i64,
) -> Result<(), StoreError> {
    let batch_ids = {
        let mut statement = transaction.prepare(
            "SELECT DISTINCT b.id
             FROM outbound_batches AS b
             JOIN outbound_segments AS s ON s.batch_id = b.id
             WHERE b.account_id = ?1 AND s.status = 'sending'
               AND s.last_fence_epoch < ?2",
        )?;
        let rows = statement.query_map(params![account_id, new_fence_epoch], |row| {
            row.get::<_, String>(0)
        })?;
        rows.collect::<Result<Vec<_>, _>>()?
    };
    if batch_ids.is_empty() {
        return Ok(());
    }

    transaction.execute(
        "UPDATE outbound_segments
         SET status = 'uncertain', last_error = ?3,
             last_fence_epoch = ?2, updated_at_ms = ?4
         WHERE batch_id IN (
             SELECT id FROM outbound_batches WHERE account_id = ?1
         ) AND status = 'sending' AND last_fence_epoch < ?2",
        params![
            account_id,
            new_fence_epoch,
            LEASE_TRANSFER_UNCERTAIN_REASON,
            now_ms
        ],
    )?;
    for batch_id in batch_ids {
        let status = derive_batch_status(transaction, &batch_id)?;
        transaction.execute(
            "UPDATE outbound_batches
             SET status = ?2, last_fence_epoch = ?3, updated_at_ms = ?4
             WHERE id = ?1",
            params![batch_id, status.as_str(), new_fence_epoch, now_ms],
        )?;
    }
    Ok(())
}

fn apply_segment_transition(
    transaction: &Transaction<'_>,
    segment_id: &str,
    fence_epoch: i64,
    transition: SegmentTransition,
    now_ms: i64,
) -> Result<(), StoreError> {
    let target = transition.target_status();
    match transition {
        SegmentTransition::StartAttempt => {
            transaction.execute(
                "UPDATE outbound_segments
                 SET status = ?2, attempt_count = attempt_count + 1,
                     platform_message_id = NULL, last_error = NULL,
                     last_fence_epoch = ?3, updated_at_ms = ?4
                 WHERE id = ?1",
                params![segment_id, target.as_str(), fence_epoch, now_ms],
            )?;
        }
        SegmentTransition::Confirm {
            platform_message_id,
        } => {
            transaction.execute(
                "UPDATE outbound_segments
                 SET status = ?2, platform_message_id = ?3, last_error = NULL,
                     last_fence_epoch = ?4, updated_at_ms = ?5
                 WHERE id = ?1",
                params![
                    segment_id,
                    target.as_str(),
                    platform_message_id,
                    fence_epoch,
                    now_ms
                ],
            )?;
        }
        SegmentTransition::Reject { error: reason }
        | SegmentTransition::MarkRetryable { reason }
        | SegmentTransition::MarkUncertain { reason } => {
            transaction.execute(
                "UPDATE outbound_segments
                 SET status = ?2, last_error = ?3,
                     last_fence_epoch = ?4, updated_at_ms = ?5
                 WHERE id = ?1",
                params![segment_id, target.as_str(), reason, fence_epoch, now_ms],
            )?;
        }
    }
    Ok(())
}

fn derive_batch_status(
    transaction: &Transaction<'_>,
    batch_id: &str,
) -> Result<BatchStatus, StoreError> {
    let mut statement = transaction
        .prepare("SELECT status FROM outbound_segments WHERE batch_id = ?1 ORDER BY ordinal")?;
    let rows = statement.query_map(params![batch_id], |row| row.get::<_, String>(0))?;
    let mut statuses = Vec::new();
    for row in rows {
        statuses.push(SegmentStatus::parse(&row?)?);
    }
    if statuses.is_empty() {
        return Err(StoreError::InvalidInput(
            "outbound batch must contain at least one segment",
        ));
    }

    let all_confirmed = statuses
        .iter()
        .all(|status| *status == SegmentStatus::Confirmed);
    let all_rejected = statuses
        .iter()
        .all(|status| *status == SegmentStatus::Rejected);
    let status = if statuses.contains(&SegmentStatus::Uncertain) {
        BatchStatus::Uncertain
    } else if statuses.contains(&SegmentStatus::Sending) {
        BatchStatus::Sending
    } else if statuses.contains(&SegmentStatus::Retryable) {
        BatchStatus::Retryable
    } else if statuses.contains(&SegmentStatus::Prepared) {
        BatchStatus::Prepared
    } else if all_confirmed {
        BatchStatus::Confirmed
    } else if all_rejected {
        BatchStatus::Rejected
    } else {
        BatchStatus::Partial
    };
    Ok(status)
}

const fn valid_segment_transition(current: SegmentStatus, target: SegmentStatus) -> bool {
    matches!(
        (current, target),
        (
            SegmentStatus::Prepared | SegmentStatus::Retryable,
            SegmentStatus::Sending
        ) | (
            SegmentStatus::Sending,
            SegmentStatus::Confirmed
                | SegmentStatus::Retryable
                | SegmentStatus::Rejected
                | SegmentStatus::Uncertain
        ) | (
            SegmentStatus::Uncertain,
            SegmentStatus::Confirmed | SegmentStatus::Rejected
        )
    )
}

fn validate_transition_details(transition: &SegmentTransition) -> Result<(), StoreError> {
    match transition {
        SegmentTransition::StartAttempt => Ok(()),
        SegmentTransition::Confirm {
            platform_message_id,
        } => validate_non_empty(platform_message_id, "platform_message_id must not be empty"),
        SegmentTransition::MarkRetryable { reason } => {
            validate_non_empty(reason, "retryable reason must not be empty")
        }
        SegmentTransition::Reject { error } => {
            validate_non_empty(error, "rejection error must not be empty")
        }
        SegmentTransition::MarkUncertain { reason } => {
            validate_non_empty(reason, "uncertain reason must not be empty")
        }
    }
}

fn validate_repeated_transition(
    segment_id: &str,
    transition: &SegmentTransition,
    stored_platform_message_id: Option<&str>,
    stored_error: Option<&str>,
) -> Result<(), StoreError> {
    let matches = match transition {
        SegmentTransition::StartAttempt => true,
        SegmentTransition::Confirm {
            platform_message_id,
        } => stored_platform_message_id == Some(platform_message_id.as_str()),
        SegmentTransition::Reject { error } => stored_error == Some(error.as_str()),
        SegmentTransition::MarkRetryable { reason }
        | SegmentTransition::MarkUncertain { reason } => stored_error == Some(reason.as_str()),
    };
    if matches {
        Ok(())
    } else {
        Err(StoreError::IdempotencyConflict {
            entity: "outbound transition",
            key: segment_id.to_owned(),
        })
    }
}

fn validate_non_empty(value: &str, message: &'static str) -> Result<(), StoreError> {
    if value.trim().is_empty() {
        Err(StoreError::InvalidInput(message))
    } else {
        Ok(())
    }
}

fn validate_lease_token(lease_token: &LeaseToken) -> Result<(), StoreError> {
    validate_non_empty(&lease_token.account_id, "account_id must not be empty")?;
    validate_non_empty(
        &lease_token.owner_instance_id,
        "owner_instance_id must not be empty",
    )?;
    validate_non_empty(
        &lease_token.owner_boot_id,
        "owner_boot_id must not be empty",
    )?;
    if lease_token.fence_epoch <= 0 {
        return Err(StoreError::InvalidInput(
            "fence_epoch must be greater than zero",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use super::*;

    const ACCOUNT: &str = "account-1";
    const INSTANCE: &str = "installation-1";
    const BOOT: &str = "boot-1";
    const EPOCH: i64 = 41;

    fn store() -> (tempfile::TempDir, CoreStore) {
        let directory = tempfile::tempdir().expect("temporary directory");
        let store = CoreStore::open(directory.path()).expect("open core store");
        (directory, store)
    }

    fn lease(store: &CoreStore) -> AccountLease {
        store
            .install_verified_account_lease_at(ACCOUNT, INSTANCE, BOOT, EPOCH, 100, 10_000)
            .expect("install verified account lease")
    }

    fn receipt(event_id: &str, payload: &str) -> InboundReceiptDraft {
        InboundReceiptDraft {
            event_id: event_id.to_owned(),
            payload: payload.as_bytes().to_vec(),
            payload_hash: format!("digest:{payload}"),
        }
    }

    #[test]
    fn open_configures_full_durability_and_validates_schema_v1() {
        let (_directory, store) = store();
        assert_eq!(store.schema_version().unwrap(), CORE_SCHEMA_VERSION);
        assert!(store.database_path().ends_with(DATABASE_FILE_NAME));

        let connection = store.lock_connection().unwrap();
        let journal_mode: String = connection
            .query_row("PRAGMA journal_mode", [], |row| row.get(0))
            .unwrap();
        let foreign_keys: i64 = connection
            .query_row("PRAGMA foreign_keys", [], |row| row.get(0))
            .unwrap();
        let synchronous: i64 = connection
            .query_row("PRAGMA synchronous", [], |row| row.get(0))
            .unwrap();
        let busy_timeout: i64 = connection
            .query_row("PRAGMA busy_timeout", [], |row| row.get(0))
            .unwrap();
        let pending_index: i64 = connection
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master
                 WHERE type = 'index' AND name = 'inbound_receipts_pending_idx'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(journal_mode, "wal");
        assert_eq!(foreign_keys, 1);
        assert_eq!(synchronous, 2);
        assert_eq!(busy_timeout, 5_000);
        assert_eq!(pending_index, 1);

        let foreign_key_failure = connection.execute(
            "INSERT INTO outbound_segments
             (id, client_message_id, batch_id, ordinal, kind, payload, status,
              attempt_count, last_fence_epoch, created_at_ms, updated_at_ms)
             VALUES ('orphan', 'client-orphan', 'missing', 0, 'text', 'x',
                     'prepared', 0, 1, 0, 0)",
            [],
        );
        assert!(foreign_key_failure.is_err());
    }

    #[test]
    fn initialized_marker_matches_database_identity_and_recovers_crash_window() {
        let directory = tempfile::tempdir().unwrap();
        let store = CoreStore::open(directory.path()).unwrap();
        let marker_path = directory.path().join(INITIALIZED_MARKER_FILE_NAME);
        let database_id = read_database_id(&store.lock_connection().unwrap()).unwrap();
        assert_eq!(
            read_initialized_marker(&marker_path).unwrap(),
            Some(database_id)
        );
        drop(store);

        fs::remove_file(&marker_path).unwrap();
        let reopened = CoreStore::open(directory.path()).unwrap();
        assert_eq!(
            read_initialized_marker(&marker_path).unwrap(),
            Some(database_id)
        );
        assert_eq!(
            read_database_id(&reopened.lock_connection().unwrap()).unwrap(),
            database_id
        );
    }

    #[test]
    fn unmarked_empty_database_is_initialized_but_nonempty_invalid_database_is_rejected() {
        let empty_directory = tempfile::tempdir().unwrap();
        let empty_database_path = empty_directory.path().join(DATABASE_FILE_NAME);
        let empty_connection = Connection::open(&empty_database_path).unwrap();
        assert!(database_schema_is_empty(&empty_connection).unwrap());
        drop(empty_connection);

        let initialized = CoreStore::open(empty_directory.path()).unwrap();
        assert_eq!(initialized.schema_version().unwrap(), CORE_SCHEMA_VERSION);
        assert!(empty_directory
            .path()
            .join(INITIALIZED_MARKER_FILE_NAME)
            .exists());

        let invalid_directory = tempfile::tempdir().unwrap();
        let invalid_database_path = invalid_directory.path().join(DATABASE_FILE_NAME);
        let invalid_connection = Connection::open(&invalid_database_path).unwrap();
        invalid_connection
            .execute("CREATE TABLE interrupted_write (id INTEGER)", [])
            .unwrap();
        drop(invalid_connection);

        assert!(matches!(
            CoreStore::open(invalid_directory.path()),
            Err(StoreError::SchemaInvariant(_))
        ));
        assert!(!invalid_directory
            .path()
            .join(INITIALIZED_MARKER_FILE_NAME)
            .exists());
        let invalid_connection = Connection::open(&invalid_database_path).unwrap();
        assert!(table_exists(&invalid_connection, "interrupted_write").unwrap());
        assert!(!table_exists(&invalid_connection, "meta").unwrap());
    }

    #[test]
    fn initialized_marker_prevents_deleted_or_empty_database_recreation() {
        let deleted_directory = tempfile::tempdir().unwrap();
        let deleted_store = CoreStore::open(deleted_directory.path()).unwrap();
        let deleted_database_path = deleted_store.database_path().to_owned();
        let deleted_marker_path = deleted_directory.path().join(INITIALIZED_MARKER_FILE_NAME);
        drop(deleted_store);
        remove_sqlite_files(&deleted_database_path);

        assert!(deleted_marker_path.exists());
        assert!(matches!(
            CoreStore::open(deleted_directory.path()),
            Err(StoreError::DatabaseMissingAfterInitialization {
                database_path,
                marker_path,
            }) if database_path == deleted_database_path && marker_path == deleted_marker_path
        ));
        assert!(!deleted_database_path.exists());

        let empty_directory = tempfile::tempdir().unwrap();
        let empty_store = CoreStore::open(empty_directory.path()).unwrap();
        let empty_database_path = empty_store.database_path().to_owned();
        drop(empty_store);
        remove_sqlite_files(&empty_database_path);
        fs::File::create(&empty_database_path).unwrap();

        assert!(matches!(
            CoreStore::open(empty_directory.path()),
            Err(StoreError::SchemaInvariant(_))
        ));
        let replacement = Connection::open(&empty_database_path).unwrap();
        assert!(!table_exists(&replacement, "meta").unwrap());
    }

    #[test]
    fn initialized_marker_rejects_a_different_valid_database() {
        let directory = tempfile::tempdir().unwrap();
        let store = CoreStore::open(directory.path()).unwrap();
        let database_path = store.database_path().to_owned();
        let original_database_id = read_database_id(&store.lock_connection().unwrap()).unwrap();
        drop(store);
        remove_sqlite_files(&database_path);

        let mut replacement = Connection::open(&database_path).unwrap();
        replacement
            .pragma_update(None, "journal_mode", "WAL")
            .unwrap();
        let replacement_database_id = initialize_new_database(&mut replacement).unwrap();
        assert_ne!(replacement_database_id, original_database_id);
        drop(replacement);

        assert!(matches!(
            CoreStore::open(directory.path()),
            Err(StoreError::DatabaseIdentityMismatch {
                marker_database_id,
                database_database_id,
            }) if marker_database_id == original_database_id
                && database_database_id == replacement_database_id
        ));
    }

    #[test]
    fn reopen_rejects_user_version_or_core_table_drift() {
        let user_version_dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(user_version_dir.path()).unwrap();
        let database_path = store.database_path().to_owned();
        drop(store);
        let connection = Connection::open(&database_path).unwrap();
        connection.pragma_update(None, "user_version", 0).unwrap();
        drop(connection);
        assert!(matches!(
            CoreStore::open(user_version_dir.path()),
            Err(StoreError::SchemaInvariant(_))
        ));

        let missing_table_dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(missing_table_dir.path()).unwrap();
        let database_path = store.database_path().to_owned();
        drop(store);
        let connection = Connection::open(&database_path).unwrap();
        connection
            .execute("DROP TABLE inbound_checkpoints", [])
            .unwrap();
        drop(connection);
        assert!(matches!(
            CoreStore::open(missing_table_dir.path()),
            Err(StoreError::SchemaInvariant(_))
        ));
    }

    fn remove_sqlite_files(database_path: &Path) {
        for path in [
            database_path.to_owned(),
            PathBuf::from(format!("{}-wal", database_path.display())),
            PathBuf::from(format!("{}-shm", database_path.display())),
        ] {
            if path.exists() {
                fs::remove_file(path).unwrap();
            }
        }
    }

    #[test]
    fn verified_lease_uses_remote_monotonic_epoch_without_local_generation() {
        let (_directory, store) = store();
        let first = lease(&store);
        assert_eq!(first.fence_epoch, EPOCH);

        let lower = store.install_verified_account_lease_at(
            ACCOUNT,
            "installation-old",
            "boot-old",
            EPOCH - 1,
            110,
            20_000,
        );
        assert!(matches!(
            lower,
            Err(StoreError::StaleFence {
                current_epoch: EPOCH,
                provided_epoch,
                ..
            }) if provided_epoch == EPOCH - 1
        ));

        let extended = store
            .install_verified_account_lease_at(ACCOUNT, INSTANCE, BOOT, EPOCH, 120, 20_000)
            .unwrap();
        assert_eq!(extended.fence_epoch, EPOCH);
        assert_eq!(extended.lease_until_ms, 20_000);

        let same_epoch_other_boot = store
            .install_verified_account_lease_at(ACCOUNT, INSTANCE, "boot-2", EPOCH, 130, 30_000);
        assert!(matches!(
            same_epoch_other_boot,
            Err(StoreError::LeaseOwnerMismatch { .. })
        ));

        let transferred = store
            .install_verified_account_lease_at(
                ACCOUNT,
                "installation-2",
                "boot-2",
                900,
                130,
                30_000,
            )
            .unwrap();
        assert_eq!(transferred.fence_epoch, 900);
        assert_eq!(transferred.owner_boot_id, "boot-2");
    }

    #[test]
    fn release_is_irreversible_for_epoch_and_clock_regression_is_rejected() {
        let (_directory, store) = store();
        let first = lease(&store);
        let token = first.token();

        assert!(matches!(
            store.pending_inbound_receipts_at(&token, 99, 1),
            Err(StoreError::ClockRegression { .. })
        ));
        store.release_account_lease_at(&token, 200).unwrap();
        assert!(matches!(
            store.pending_inbound_receipts_at(&token, 201, 1),
            Err(StoreError::LeaseReleased { .. })
        ));
        assert!(matches!(
            store.install_verified_account_lease_at(ACCOUNT, INSTANCE, BOOT, EPOCH, 201, 20_000),
            Err(StoreError::LeaseReleased { .. })
        ));

        let next = store
            .install_verified_account_lease_at(ACCOUNT, INSTANCE, "boot-2", EPOCH + 1, 202, 20_000)
            .unwrap();
        assert_eq!(next.status, LeaseStatus::Active);
        assert_eq!(next.fence_epoch, EPOCH + 1);
    }

    #[test]
    fn inbound_page_is_atomic_idempotent_and_cursor_is_monotonic() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        let page = [receipt("event-1", "one"), receipt("event-2", "two")];

        let first = store
            .record_inbound_page_at(&token, "frontier", 7, 20, &page, 200)
            .unwrap();
        let duplicate = store
            .record_inbound_page_at(&token, "frontier", 7, 20, &page, 201)
            .unwrap();
        let older = store
            .record_inbound_page_at(
                &token,
                "frontier",
                7,
                10,
                &[receipt("event-old", "old")],
                202,
            )
            .unwrap();

        assert_eq!(first.inserted_count, 2);
        assert_eq!(duplicate.inserted_count, 0);
        assert_eq!(older.inserted_count, 1);
        assert_eq!(
            older.checkpoint,
            InboundCheckpoint {
                stream_generation: 7,
                checkpoint: 20,
            }
        );
        let pending = store.pending_inbound_receipts_at(&token, 203, 10).unwrap();
        assert_eq!(pending.len(), 3);
        assert!(pending
            .iter()
            .all(|item| { item.status == InboundStatus::Pending && item.payload.is_some() }));
    }

    #[test]
    fn inbound_page_failure_rolls_back_every_receipt_and_cursor() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        {
            let connection = store.lock_connection().unwrap();
            connection
                .execute_batch(
                    "CREATE TRIGGER reject_second_receipt
                     BEFORE INSERT ON inbound_receipts
                     WHEN NEW.event_id = 'event-2'
                     BEGIN
                       SELECT RAISE(ABORT, 'second receipt rejected');
                     END;",
                )
                .unwrap();
        }

        let result = store.record_inbound_page_at(
            &token,
            "frontier",
            1,
            20,
            &[receipt("event-1", "one"), receipt("event-2", "two")],
            200,
        );
        assert!(matches!(result, Err(StoreError::Sqlite(_))));
        assert_eq!(store.inbound_checkpoint(ACCOUNT, "frontier").unwrap(), None);
        let connection = store.lock_connection().unwrap();
        let count: i64 = connection
            .query_row("SELECT COUNT(*) FROM inbound_receipts", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 0);
    }

    #[test]
    fn committed_pending_payload_survives_crash_window_then_is_cleared() {
        let directory = tempfile::tempdir().unwrap();
        let first_store = CoreStore::open(directory.path()).unwrap();
        let token = lease(&first_store).token();
        first_store
            .record_inbound_page_at(
                &token,
                "frontier",
                3,
                88,
                &[receipt("event-1", r#"{"text":"hello"}"#)],
                200,
            )
            .unwrap();
        drop(first_store);

        let reopened = CoreStore::open(directory.path()).unwrap();
        let recovered = reopened
            .pending_inbound_receipts_at(&token, 201, 10)
            .unwrap();
        assert_eq!(recovered.len(), 1);
        assert_eq!(
            recovered[0].payload.as_deref(),
            Some(r#"{"text":"hello"}"#.as_bytes())
        );
        assert_eq!(
            reopened.inbound_checkpoint(ACCOUNT, "frontier").unwrap(),
            Some(InboundCheckpoint {
                stream_generation: 3,
                checkpoint: 88,
            })
        );

        let processed = reopened
            .mark_inbound_processed_at(&token, "frontier", 3, "event-1", 202)
            .unwrap();
        assert!(processed.applied);
        assert_eq!(processed.receipt.status, InboundStatus::Processed);
        assert_eq!(processed.receipt.payload, None);
        let repeated = reopened
            .mark_inbound_processed_at(&token, "frontier", 3, "event-1", 203)
            .unwrap();
        assert!(!repeated.applied);
        assert_eq!(repeated.receipt.payload, None);
        assert!(reopened
            .pending_inbound_receipts_at(&token, 204, 10)
            .unwrap()
            .is_empty());

        let duplicate = reopened
            .record_inbound_page_at(
                &token,
                "frontier",
                3,
                88,
                &[receipt("event-1", r#"{"text":"hello"}"#)],
                205,
            )
            .unwrap();
        assert_eq!(duplicate.inserted_count, 0);
        assert!(reopened
            .pending_inbound_receipts_at(&token, 206, 10)
            .unwrap()
            .is_empty());
    }

    #[test]
    fn credential_generation_can_reset_cursor_and_old_generation_is_rejected() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        store
            .record_inbound_page_at(
                &token,
                "frontier",
                5,
                1_000,
                &[receipt("same-event", "old credential")],
                200,
            )
            .unwrap();
        let reset = store
            .record_inbound_page_at(
                &token,
                "frontier",
                6,
                3,
                &[receipt("same-event", "new credential")],
                201,
            )
            .unwrap();
        assert_eq!(reset.checkpoint.stream_generation, 6);
        assert_eq!(reset.checkpoint.checkpoint, 3);

        let stale = store.record_inbound_page_at(
            &token,
            "frontier",
            5,
            2_000,
            &[receipt("late-old", "late")],
            202,
        );
        assert!(matches!(
            stale,
            Err(StoreError::StaleStreamGeneration {
                current_generation: 6,
                provided_generation: 5,
                ..
            })
        ));
        let pending = store.pending_inbound_receipts_at(&token, 202, 10).unwrap();
        assert_eq!(pending.len(), 2);
        assert_eq!(
            pending
                .iter()
                .map(|item| item.stream_generation)
                .collect::<HashSet<_>>(),
            HashSet::from([5, 6])
        );
    }

    #[test]
    fn inbound_duplicate_payload_conflict_does_not_advance_cursor() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        store
            .record_inbound_page_at(
                &token,
                "frontier",
                1,
                20,
                &[receipt("event-1", "original")],
                200,
            )
            .unwrap();
        let conflict = store.record_inbound_page_at(
            &token,
            "frontier",
            1,
            30,
            &[receipt("event-1", "changed")],
            201,
        );
        assert!(matches!(
            conflict,
            Err(StoreError::IdempotencyConflict {
                entity: "inbound receipt",
                ..
            })
        ));
        assert_eq!(
            store.inbound_checkpoint(ACCOUNT, "frontier").unwrap(),
            Some(InboundCheckpoint {
                stream_generation: 1,
                checkpoint: 20,
            })
        );
    }

    #[test]
    fn outbound_preparation_returns_stable_independent_ids_across_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let first_store = CoreStore::open(directory.path()).unwrap();
        let token = lease(&first_store).token();
        let plan = vec![
            OutboundSegmentDraft::text("hello"),
            OutboundSegmentDraft::text("world"),
        ];
        let first = first_store
            .prepare_outbound_batch_at(&token, "trigger-1", "response-1", &plan, 200)
            .unwrap();
        let repeated = first_store
            .prepare_outbound_batch_at(&token, "trigger-1", "response-1", &plan, 201)
            .unwrap();
        assert_eq!(first, repeated);
        assert!(first
            .segments
            .iter()
            .all(|segment| segment.id != segment.client_message_id));
        let client_ids: HashSet<_> = first
            .segments
            .iter()
            .map(|segment| &segment.client_message_id)
            .collect();
        assert_eq!(client_ids.len(), first.segments.len());
        drop(first_store);

        let reopened = CoreStore::open(directory.path()).unwrap();
        let durable = reopened
            .prepare_outbound_batch_at(&token, "trigger-1", "response-1", &plan, 202)
            .unwrap();
        assert_eq!(first.id, durable.id);
        assert_eq!(first.segments, durable.segments);
    }

    #[test]
    fn trigger_claim_rejects_a_different_response_or_segment_plan() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        store
            .prepare_outbound_batch_at(
                &token,
                "trigger-1",
                "response-1",
                &[OutboundSegmentDraft::text("hello")],
                200,
            )
            .unwrap();

        let response_conflict = store.prepare_outbound_batch_at(
            &token,
            "trigger-1",
            "response-2",
            &[OutboundSegmentDraft::text("hello")],
            201,
        );
        assert!(matches!(
            response_conflict,
            Err(StoreError::IdempotencyConflict {
                entity: "outbound batch",
                ..
            })
        ));
        let segment_conflict = store.prepare_outbound_batch_at(
            &token,
            "trigger-1",
            "response-1",
            &[OutboundSegmentDraft::text("changed")],
            201,
        );
        assert!(matches!(
            segment_conflict,
            Err(StoreError::IdempotencyConflict {
                entity: "outbound batch",
                ..
            })
        ));
    }

    #[test]
    fn transition_outcome_gates_side_effects_and_retryable_is_explicit() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        let prepared = store
            .prepare_outbound_batch_at(
                &token,
                "trigger-1",
                "response-1",
                &[OutboundSegmentDraft::text("hello")],
                200,
            )
            .unwrap();
        let segment_id = &prepared.segments[0].id;

        let first_start = store
            .transition_segment_at(&token, segment_id, SegmentTransition::StartAttempt, 201)
            .unwrap();
        let duplicate_start = store
            .transition_segment_at(&token, segment_id, SegmentTransition::StartAttempt, 202)
            .unwrap();
        assert!(first_start.applied);
        assert!(!duplicate_start.applied);
        assert_eq!(duplicate_start.batch.segments[0].attempt_count, 1);

        let retryable = store
            .transition_segment_at(
                &token,
                segment_id,
                SegmentTransition::MarkRetryable {
                    reason: "transport proved zero request bytes written".to_owned(),
                },
                203,
            )
            .unwrap();
        assert!(retryable.applied);
        assert_eq!(retryable.batch.status, BatchStatus::Retryable);
        let duplicate_retryable = store
            .transition_segment_at(
                &token,
                segment_id,
                SegmentTransition::MarkRetryable {
                    reason: "transport proved zero request bytes written".to_owned(),
                },
                204,
            )
            .unwrap();
        assert!(!duplicate_retryable.applied);
        assert!(matches!(
            store.transition_segment_at(
                &token,
                segment_id,
                SegmentTransition::MarkRetryable {
                    reason: "different reason".to_owned(),
                },
                205,
            ),
            Err(StoreError::IdempotencyConflict { .. })
        ));

        let retry_start = store
            .transition_segment_at(&token, segment_id, SegmentTransition::StartAttempt, 205)
            .unwrap();
        assert!(retry_start.applied);
        assert_eq!(retry_start.batch.segments[0].attempt_count, 2);
        let confirmed = store
            .transition_segment_at(
                &token,
                segment_id,
                SegmentTransition::Confirm {
                    platform_message_id: "platform-1".to_owned(),
                },
                206,
            )
            .unwrap();
        assert!(confirmed.applied);
        let repeated_confirmation = store
            .transition_segment_at(
                &token,
                segment_id,
                SegmentTransition::Confirm {
                    platform_message_id: "platform-1".to_owned(),
                },
                207,
            )
            .unwrap();
        assert!(!repeated_confirmation.applied);
    }

    #[test]
    fn rejected_is_terminal_and_repeated_reason_must_match() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        let prepared = store
            .prepare_outbound_batch_at(
                &token,
                "trigger-1",
                "response-1",
                &[OutboundSegmentDraft::text("hello")],
                200,
            )
            .unwrap();
        let segment_id = &prepared.segments[0].id;
        assert!(matches!(
            store.transition_segment_at(
                &token,
                segment_id,
                SegmentTransition::Reject {
                    error: "too early".to_owned(),
                },
                201,
            ),
            Err(StoreError::InvalidTransition { .. })
        ));
        store
            .transition_segment_at(&token, segment_id, SegmentTransition::StartAttempt, 201)
            .unwrap();
        let rejected = store
            .transition_segment_at(
                &token,
                segment_id,
                SegmentTransition::Reject {
                    error: "platform rejected".to_owned(),
                },
                202,
            )
            .unwrap();
        assert_eq!(rejected.batch.status, BatchStatus::Rejected);
        let duplicate = store
            .transition_segment_at(
                &token,
                segment_id,
                SegmentTransition::Reject {
                    error: "platform rejected".to_owned(),
                },
                203,
            )
            .unwrap();
        assert!(!duplicate.applied);
        assert!(matches!(
            store.transition_segment_at(
                &token,
                segment_id,
                SegmentTransition::Reject {
                    error: "different reason".to_owned(),
                },
                204,
            ),
            Err(StoreError::IdempotencyConflict { .. })
        ));
        assert!(matches!(
            store.transition_segment_at(&token, segment_id, SegmentTransition::StartAttempt, 204),
            Err(StoreError::InvalidTransition { .. })
        ));
    }

    #[test]
    fn uncertain_has_priority_over_partial_and_reason_is_idempotent() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        let prepared = store
            .prepare_outbound_batch_at(
                &token,
                "trigger-1",
                "response-1",
                &[
                    OutboundSegmentDraft::text("first"),
                    OutboundSegmentDraft::text("second"),
                ],
                200,
            )
            .unwrap();
        let first_id = &prepared.segments[0].id;
        let second_id = &prepared.segments[1].id;
        store
            .transition_segment_at(&token, first_id, SegmentTransition::StartAttempt, 201)
            .unwrap();
        store
            .transition_segment_at(
                &token,
                first_id,
                SegmentTransition::Confirm {
                    platform_message_id: "platform-1".to_owned(),
                },
                202,
            )
            .unwrap();
        store
            .transition_segment_at(&token, second_id, SegmentTransition::StartAttempt, 203)
            .unwrap();
        let uncertain = store
            .transition_segment_at(
                &token,
                second_id,
                SegmentTransition::MarkUncertain {
                    reason: "timeout after write".to_owned(),
                },
                204,
            )
            .unwrap();
        assert_eq!(uncertain.batch.status, BatchStatus::Uncertain);
        let duplicate = store
            .transition_segment_at(
                &token,
                second_id,
                SegmentTransition::MarkUncertain {
                    reason: "timeout after write".to_owned(),
                },
                205,
            )
            .unwrap();
        assert!(!duplicate.applied);
        assert!(matches!(
            store.transition_segment_at(
                &token,
                second_id,
                SegmentTransition::MarkUncertain {
                    reason: "different reason".to_owned(),
                },
                206,
            ),
            Err(StoreError::IdempotencyConflict { .. })
        ));
    }

    #[test]
    fn actionable_segment_status_has_priority_over_terminal_siblings() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        let prepared = store
            .prepare_outbound_batch_at(
                &token,
                "trigger-priority",
                "response-priority",
                &[
                    OutboundSegmentDraft::text("first"),
                    OutboundSegmentDraft::text("second"),
                ],
                200,
            )
            .unwrap();
        let first_id = &prepared.segments[0].id;
        let second_id = &prepared.segments[1].id;

        store
            .transition_segment_at(&token, first_id, SegmentTransition::StartAttempt, 201)
            .unwrap();
        let one_rejected = store
            .transition_segment_at(
                &token,
                first_id,
                SegmentTransition::Reject {
                    error: "platform rejected".to_owned(),
                },
                202,
            )
            .unwrap();
        assert_eq!(one_rejected.batch.status, BatchStatus::Prepared);

        let sending = store
            .transition_segment_at(&token, second_id, SegmentTransition::StartAttempt, 203)
            .unwrap();
        assert_eq!(sending.batch.status, BatchStatus::Sending);
        let retryable = store
            .transition_segment_at(
                &token,
                second_id,
                SegmentTransition::MarkRetryable {
                    reason: "zero request bytes written".to_owned(),
                },
                204,
            )
            .unwrap();
        assert_eq!(retryable.batch.status, BatchStatus::Retryable);
    }

    #[test]
    fn higher_epoch_fences_interrupted_send_to_uncertain_for_recovery() {
        let (_directory, store) = store();
        let old_token = lease(&store).token();
        let prepared = store
            .prepare_outbound_batch_at(
                &old_token,
                "trigger-1",
                "response-1",
                &[OutboundSegmentDraft::text("hello")],
                200,
            )
            .unwrap();
        let segment_id = &prepared.segments[0].id;
        store
            .transition_segment_at(&old_token, segment_id, SegmentTransition::StartAttempt, 201)
            .unwrap();

        let new_lease = store
            .install_verified_account_lease_at(
                ACCOUNT,
                INSTANCE,
                "boot-after-restart",
                EPOCH + 1,
                202,
                20_000,
            )
            .unwrap();
        let new_token = new_lease.token();
        let recovery = store
            .unfinished_outbound_batches_at(&new_token, 203, 10)
            .unwrap();
        assert_eq!(recovery.len(), 1);
        assert_eq!(recovery[0].status, BatchStatus::Uncertain);
        assert_eq!(recovery[0].segments[0].status, SegmentStatus::Uncertain);
        assert_eq!(
            recovery[0].segments[0].last_error.as_deref(),
            Some(LEASE_TRANSFER_UNCERTAIN_REASON)
        );
        assert!(matches!(
            store.transition_segment_at(
                &new_token,
                segment_id,
                SegmentTransition::StartAttempt,
                204,
            ),
            Err(StoreError::InvalidTransition { .. })
        ));
        assert!(matches!(
            store.transition_segment_at(
                &old_token,
                segment_id,
                SegmentTransition::Confirm {
                    platform_message_id: "stale-result".to_owned(),
                },
                204,
            ),
            Err(StoreError::StaleFence { .. })
        ));
        let reconciled = store
            .transition_segment_at(
                &new_token,
                segment_id,
                SegmentTransition::Confirm {
                    platform_message_id: "confirmed-by-query".to_owned(),
                },
                204,
            )
            .unwrap();
        assert!(reconciled.applied);
        assert_eq!(reconciled.batch.status, BatchStatus::Confirmed);
    }

    #[test]
    fn unfinished_query_excludes_fully_terminal_batches() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        let pending = store
            .prepare_outbound_batch_at(
                &token,
                "trigger-pending",
                "response-pending",
                &[OutboundSegmentDraft::text("pending")],
                200,
            )
            .unwrap();
        let terminal = store
            .prepare_outbound_batch_at(
                &token,
                "trigger-terminal",
                "response-terminal",
                &[OutboundSegmentDraft::text("terminal")],
                201,
            )
            .unwrap();
        let terminal_id = &terminal.segments[0].id;
        store
            .transition_segment_at(&token, terminal_id, SegmentTransition::StartAttempt, 202)
            .unwrap();
        store
            .transition_segment_at(
                &token,
                terminal_id,
                SegmentTransition::Confirm {
                    platform_message_id: "platform-terminal".to_owned(),
                },
                203,
            )
            .unwrap();

        let unfinished = store
            .unfinished_outbound_batches_at(&token, 204, 10)
            .unwrap();
        assert_eq!(unfinished.len(), 1);
        assert_eq!(unfinished[0].id, pending.id);
    }
}
