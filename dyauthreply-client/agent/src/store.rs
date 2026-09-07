#[path = "store_audit.rs"]
mod audit_feed;
pub use audit_feed::{AuditChanges, DecisionAudit};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Component, Path, PathBuf},
    sync::{Mutex, MutexGuard},
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use rusqlite::{
    backup::Backup, params, Connection, OpenFlags, OptionalExtension, Transaction,
    TransactionBehavior,
};
use thiserror::Error;
use uuid::Uuid;

#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

use crate::{
    storage::{
        retention::DiskPressure, validate_manifest_identity, CatalogError, PendingSegmentDeletion,
        SegmentCatalog, SegmentFamily, SegmentManifest,
    },
    CORE_SCHEMA_VERSION,
};

const DATABASE_FILE_NAME: &str = "core.sqlite3";
const INITIALIZED_MARKER_FILE_NAME: &str = "core.initialized";
const DATABASE_ID_META_KEY: &str = "database_id";
const BACKUP_DIRECTORY_NAME: &str = "backups";
const SCHEMA_V1: u32 = 1;
const SCHEMA_V2: u32 = 2;
const SCHEMA_V3: u32 = 3;
const SCHEMA_V4: u32 = 4;
#[path = "store_protocol_state.rs"]
mod protocol_state;
pub use protocol_state::SendObservation;
#[path = "store_send_receipts.rs"]
mod send_receipts;
pub use send_receipts::{ReceiptReconciliation, SentReceiptEvidence};
#[path = "store_guards.rs"]
mod guards;
pub use guards::GuardPolicy;
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
const SCHEMA_V2_SQL: &str = "CREATE TABLE segment_manifests (
             segment_id TEXT PRIMARY KEY NOT NULL,
             family TEXT NOT NULL CHECK (family IN ('chat', 'audit', 'debug')),
             relative_path TEXT NOT NULL UNIQUE,
             record_count INTEGER NOT NULL CHECK (record_count > 0),
             uncompressed_bytes INTEGER NOT NULL CHECK (uncompressed_bytes > 0),
             stored_bytes INTEGER NOT NULL CHECK (stored_bytes > 0),
             content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
             file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
             created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
             sealed_at_ms INTEGER NOT NULL CHECK (sealed_at_ms >= created_at_ms),
             compression TEXT CHECK (compression IS NULL OR compression = 'zstd')
         );

         CREATE TABLE storage_cleanup_journal (
             segment_id TEXT PRIMARY KEY NOT NULL
                 REFERENCES segment_manifests(segment_id) ON DELETE CASCADE,
             intent_created_at_ms INTEGER NOT NULL CHECK (intent_created_at_ms >= 0)
         );

         CREATE TABLE storage_cleanup_state (
             singleton INTEGER PRIMARY KEY NOT NULL CHECK (singleton = 1),
             last_cleanup_at_ms INTEGER,
             last_pressure TEXT CHECK (
                 last_pressure IS NULL OR last_pressure IN ('normal', 'high', 'critical')
             ),
             CHECK (
                 (last_cleanup_at_ms IS NULL AND last_pressure IS NULL)
                 OR (last_cleanup_at_ms >= 0 AND last_pressure IS NOT NULL)
             )
         );

         CREATE TABLE schema_migrations (
             from_version INTEGER NOT NULL CHECK (from_version >= 0),
             to_version INTEGER NOT NULL CHECK (to_version > from_version),
             database_id TEXT NOT NULL,
             backup_relative_path TEXT NOT NULL,
             backup_bytes INTEGER NOT NULL CHECK (backup_bytes > 0),
             backup_created_at_ms INTEGER NOT NULL CHECK (backup_created_at_ms >= 0),
             applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= backup_created_at_ms),
             backup_quick_check TEXT NOT NULL CHECK (backup_quick_check = 'ok'),
             backup_foreign_key_violations INTEGER NOT NULL
                 CHECK (backup_foreign_key_violations = 0),
             PRIMARY KEY (from_version, to_version)
         );

         CREATE INDEX segment_manifests_family_sealed_idx
             ON segment_manifests(family, sealed_at_ms, segment_id);
         CREATE INDEX storage_cleanup_journal_intent_idx
             ON storage_cleanup_journal(intent_created_at_ms, segment_id);

         INSERT INTO storage_cleanup_state(singleton) VALUES (1);";
const REQUIRED_TABLES_V1: &[(&str, &[&str])] = &[
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
            "updated_at_ms",
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
            "fence_epoch",
            "received_at_ms",
            "processed_at_ms",
            "processed_fence_epoch",
        ],
    ),
    (
        "inbound_checkpoints",
        &[
            "account_id",
            "stream",
            "stream_generation",
            "checkpoint",
            "fence_epoch",
            "updated_at_ms",
        ],
    ),
    (
        "outbound_batches",
        &[
            "id",
            "account_id",
            "trigger_id",
            "response_id",
            "status",
            "created_fence_epoch",
            "last_fence_epoch",
            "created_at_ms",
            "updated_at_ms",
        ],
    ),
    (
        "outbound_segments",
        &[
            "id",
            "client_message_id",
            "batch_id",
            "ordinal",
            "kind",
            "payload",
            "status",
            "attempt_count",
            "platform_message_id",
            "last_error",
            "last_fence_epoch",
            "created_at_ms",
            "updated_at_ms",
        ],
    ),
];
const REQUIRED_TABLES_V2: &[(&str, &[&str])] = &[
    (
        "segment_manifests",
        &[
            "segment_id",
            "family",
            "relative_path",
            "record_count",
            "uncompressed_bytes",
            "stored_bytes",
            "content_sha256",
            "file_sha256",
            "created_at_ms",
            "sealed_at_ms",
            "compression",
        ],
    ),
    (
        "storage_cleanup_journal",
        &["segment_id", "intent_created_at_ms"],
    ),
    (
        "storage_cleanup_state",
        &["singleton", "last_cleanup_at_ms", "last_pressure"],
    ),
    (
        "schema_migrations",
        &[
            "from_version",
            "to_version",
            "database_id",
            "backup_relative_path",
            "backup_bytes",
            "backup_created_at_ms",
            "applied_at_ms",
            "backup_quick_check",
            "backup_foreign_key_violations",
        ],
    ),
];

#[derive(Debug, Error)]
pub enum StoreError {
    #[error("automatic reply guard blocked: {reason}")]
    ReplyGuardBlocked {
        reason: &'static str,
        retry_at_ms: Option<i64>,
    },
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
    #[error("pre-migration backup verification failed: {0}")]
    BackupVerification(String),
    #[error("unsafe storage path {path:?}: {reason}")]
    UnsafeStoragePath { path: PathBuf, reason: String },
    #[error("invalid segment manifest: {0}")]
    InvalidSegmentManifest(String),
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

/// Result of `SQLite`'s built-in integrity checks for a durable database.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DatabaseIntegrity {
    pub quick_check: String,
    pub foreign_key_violations: u64,
}

impl DatabaseIntegrity {
    #[must_use]
    pub fn is_valid(&self) -> bool {
        self.quick_check == "ok" && self.foreign_key_violations == 0
    }
}

/// Durable audit row describing the verified snapshot taken before schema v1
/// was upgraded to schema v2.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PreMigrationBackup {
    pub from_version: u32,
    pub to_version: u32,
    pub database_id: Uuid,
    pub relative_path: PathBuf,
    pub bytes: u64,
    pub created_at_ms: i64,
    pub applied_at_ms: i64,
    pub integrity: DatabaseIntegrity,
}

/// Last completed storage-maintenance pass, persisted for health reporting.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StorageCleanupState {
    pub last_cleanup_at_ms: i64,
    pub last_pressure: DiskPressure,
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

#[derive(Clone, Copy)]
pub struct InboundPageDraft<'a> {
    pub stream: &'a str,
    pub stream_generation: i64,
    pub checkpoint: i64,
    pub receipts: &'a [InboundReceiptDraft],
}

#[derive(Clone, Copy)]
pub struct InboundLimits {
    pub pending_records: u32,
    pub pending_bytes: u64,
}

/// The expected immutable receipt version used when applying a rule decision.
#[derive(Clone, Copy)]
pub struct InboundReceiptKey<'a> {
    pub stream: &'a str,
    pub generation: i64,
    pub event_id: &'a str,
    pub payload_hash: &'a str,
}
#[derive(Clone, Copy)]
pub struct InboundReplyPlan<'a> {
    pub response_id: &'a str,
    pub segments: &'a [OutboundSegmentDraft],
    pub pending_batch_limit: u32,
}
pub struct InboundConsumeOutcome {
    pub applied: bool,
    pub batch: Option<OutboundBatch>,
}
const LIVE_BASELINE_STREAM: &str = "douyin-im-live-start";
const DECISION_STREAM: &str = "douyin-im-decision";

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
    StartAutomaticAttempt,
    CancelPrepared { reason: String },
    Confirm { platform_message_id: String },
    MarkRetryable { reason: String },
    Reject { error: String },
    MarkUncertain { reason: String },
}

impl SegmentTransition {
    const fn target_status(&self) -> SegmentStatus {
        match self {
            Self::StartAttempt | Self::StartAutomaticAttempt => SegmentStatus::Sending,
            Self::Confirm { .. } => SegmentStatus::Confirmed,
            Self::MarkRetryable { .. } => SegmentStatus::Retryable,
            Self::Reject { .. } | Self::CancelPrepared { .. } => SegmentStatus::Rejected,
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
        prepare_private_directory(data_dir)?;
        let database_path = data_dir.join(DATABASE_FILE_NAME);
        let marker_path = data_dir.join(INITIALIZED_MARKER_FILE_NAME);
        let marker_database_id = read_initialized_marker(&marker_path)?;
        let database_exists = optional_regular_file_exists(&database_path)?;

        if marker_database_id.is_some() && !database_exists {
            return Err(StoreError::DatabaseMissingAfterInitialization {
                database_path,
                marker_path,
            });
        }

        let mut connection = Connection::open(&database_path)?;
        enforce_private_file_permissions(&database_path)?;

        connection.busy_timeout(BUSY_TIMEOUT)?;
        connection.pragma_update(None, "foreign_keys", true)?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;

        let may_initialize = !database_exists
            || (marker_database_id.is_none() && database_schema_is_empty(&connection)?);
        let database_id = if may_initialize {
            initialize_new_database(&mut connection)?
        } else {
            if let Some(expected_database_id) = marker_database_id {
                if table_exists(&connection, "meta")? {
                    let database_database_id = read_database_id(&connection)?;
                    if expected_database_id != database_database_id {
                        return Err(StoreError::DatabaseIdentityMismatch {
                            marker_database_id: expected_database_id,
                            database_database_id,
                        });
                    }
                }
            }
            validate_or_migrate_existing_database(&mut connection, data_dir)?
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

    /// Runs `SQLite`'s quick and foreign-key checks against the live correctness
    /// database without changing its contents.
    ///
    /// # Errors
    ///
    /// Returns an error if the connection cannot be locked or either check
    /// cannot be completed.
    pub fn database_integrity(&self) -> Result<DatabaseIntegrity, StoreError> {
        let connection = self.lock_connection()?;
        inspect_database_integrity(&connection)
    }

    /// Returns the durable metadata for the v1-to-v2 pre-migration snapshot.
    /// New databases that were created directly at schema v2 return `None`.
    ///
    /// # Errors
    ///
    /// Returns an error if the connection is unavailable or the persisted
    /// migration metadata is malformed.
    pub fn pre_migration_backup(&self) -> Result<Option<PreMigrationBackup>, StoreError> {
        let connection = self.lock_connection()?;
        load_pre_migration_backup(&connection)
    }

    /// # Errors
    /// Returns unavailable/corrupt migration metadata.
    pub fn guard_migration_backup(&self) -> Result<Option<PreMigrationBackup>, StoreError> {
        let connection = self.lock_connection()?;
        load_migration_backup(&connection, SCHEMA_V2, SCHEMA_V3)
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

    /// Delivery times from durable confirmations, never the time a UI polls the command.
    /// # Errors
    /// Returns database read errors.
    pub fn outbound_delivery_times(
        &self,
        batch_id: &str,
    ) -> Result<std::collections::BTreeMap<String, i64>, StoreError> {
        let db = self.lock_connection()?;
        let mut query=db.prepare("SELECT id,updated_at_ms FROM outbound_segments WHERE batch_id=?1 AND status='confirmed'")?;
        let rows = query.query_map([batch_id], |r| Ok((r.get(0)?, r.get(1)?)))?;
        Ok(rows.collect::<Result<_, _>>()?)
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

    /// Idempotently retires exactly this local owner/epoch, including after its
    /// wall-clock deadline. This only removes rights; it never revokes a newer
    /// owner and a retired epoch cannot be installed again.
    /// # Errors
    /// Rejects invalid token identities or database failures.
    pub fn invalidate_account_lease(&self, token: &LeaseToken) -> Result<bool, StoreError> {
        validate_lease_token(token)?;
        let connection = self.lock_connection()?;
        let changed = connection.execute(
            "UPDATE account_leases SET status = 'released'
             WHERE account_id = ?1 AND owner_instance_id = ?2 AND owner_boot_id = ?3
               AND fence_epoch = ?4 AND status = 'active'",
            params![
                token.account_id,
                token.owner_instance_id,
                token.owner_boot_id,
                token.fence_epoch
            ],
        )?;
        Ok(changed == 1)
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
        self.record_inbound_page_limited(
            lease_token,
            InboundPageDraft {
                stream,
                stream_generation,
                checkpoint,
                receipts,
            },
            operation_time,
            None,
        )
    }

    /// Atomically enforces the account spool budget before committing its cursor.
    /// # Errors
    /// Returns normal receipt/fence errors or backlog-capacity exhaustion. An
    /// over-budget page rolls back all receipts and leaves the cursor unchanged.
    pub fn record_bounded_inbound_page(
        &self,
        lease: &LeaseToken,
        page: InboundPageDraft<'_>,
        limits: InboundLimits,
    ) -> Result<InboundPageResult, StoreError> {
        if limits.pending_records == 0 || limits.pending_bytes == 0 {
            return Err(StoreError::InvalidInput("inbound limits must be positive"));
        }
        self.record_inbound_page_limited(lease, page, OperationTime::System, Some(limits))
    }

    fn record_inbound_page_limited(
        &self,
        lease_token: &LeaseToken,
        draft: InboundPageDraft<'_>,
        operation_time: OperationTime,
        limits: Option<InboundLimits>,
    ) -> Result<InboundPageResult, StoreError> {
        let InboundPageDraft {
            stream,
            stream_generation,
            checkpoint,
            receipts,
        } = draft;
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
        if let Some(limits) = limits {
            let (count, bytes): (u64, u64) = transaction.query_row(
                "SELECT count(*), coalesce(sum(length(payload)), 0) FROM inbound_receipts WHERE account_id = ?1 AND status = 'pending'",
                params![lease_token.account_id], |row| Ok((row.get(0)?, row.get(1)?)))?;
            if count > u64::from(limits.pending_records) || bytes > limits.pending_bytes {
                return Err(StoreError::InvalidInput("inbound pending spool is full"));
            }
        }
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

    /// Establishes one durable live-start wall-clock watermark per credential
    /// generation. It is independent of platform pagination cursors and survives restart.
    /// # Errors
    /// Rejects invalid/stale generations, ownership and database failures.
    pub fn ensure_inbound_live_start(
        &self,
        lease: &LeaseToken,
        generation: i64,
    ) -> Result<i64, StoreError> {
        if generation <= 0 {
            return Err(StoreError::InvalidInput("invalid inbound generation"));
        }
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now = OperationTime::System.resolve()?;
        require_fence(&transaction, lease, now)?;
        let old = select_inbound_checkpoint(&transaction, &lease.account_id, LIVE_BASELINE_STREAM)?;
        if let Some(old) = old {
            if generation < old.stream_generation {
                return Err(StoreError::InvalidInput("stale inbound generation"));
            }
            if generation == old.stream_generation {
                transaction.commit()?;
                return Ok(old.checkpoint);
            }
        }
        let cutoff = now
            .checked_mul(1000)
            .ok_or(StoreError::InvalidInput("timestamp overflow"))?;
        transaction.execute("INSERT INTO inbound_checkpoints(account_id,stream,stream_generation,checkpoint,fence_epoch,updated_at_ms)
            VALUES(?1,?2,?3,?4,?5,?6) ON CONFLICT(account_id,stream) DO UPDATE SET
            stream_generation=excluded.stream_generation,checkpoint=excluded.checkpoint,
            fence_epoch=excluded.fence_epoch,updated_at_ms=excluded.updated_at_ms",
            params![lease.account_id,LIVE_BASELINE_STREAM,generation,cutoff,lease.fence_epoch,now])?;
        transaction.commit()?;
        Ok(cutoff)
    }

    /// Atomically records the transport-independent decision, creates its reply
    /// claim/outbox (when requested), and clears the consumed receipt payload.
    /// The same server ID is decided once across HTTP/WS and credential generations.
    /// `None` means an explicit terminal no-reply decision, not a deferred message.
    /// # Errors
    /// Rejects stale ownership/hash, invalid plans and outbox backpressure. Every
    /// failure rolls back the decision marker AND receipt acknowledgement.
    pub fn consume_inbound(
        &self,
        lease: &LeaseToken,
        key: InboundReceiptKey<'_>,
        reply: Option<InboundReplyPlan<'_>>,
    ) -> Result<InboundConsumeOutcome, StoreError> {
        self.consume_inbound_with_guards(lease, key, reply, None, OperationTime::System, None)
    }
    /// # Errors
    /// Journals terminal no-reply audit in the same transaction as its canonical decision.
    pub fn consume_inbound_audited(
        &self,
        lease: &LeaseToken,
        key: InboundReceiptKey<'_>,
        audit: DecisionAudit<'_>,
    ) -> Result<InboundConsumeOutcome, StoreError> {
        self.consume_inbound_with_guards(lease, key, None, None, OperationTime::System, Some(audit))
    }
    /// Atomically applies a reply decision and reserves durable quota/cooldown scopes.
    /// # Errors
    /// A blocked guard or any SQL error leaves receipt and outbox unchanged.
    pub fn consume_inbound_guarded(
        &self,
        lease: &LeaseToken,
        key: InboundReceiptKey<'_>,
        reply: InboundReplyPlan<'_>,
        policy: &GuardPolicy,
    ) -> Result<InboundConsumeOutcome, StoreError> {
        self.consume_inbound_with_guards(
            lease,
            key,
            Some(reply),
            Some(policy),
            OperationTime::System,
            None,
        )
    }
    fn consume_inbound_with_guards(
        &self,
        lease: &LeaseToken,
        key: InboundReceiptKey<'_>,
        reply: Option<InboundReplyPlan<'_>>,
        policy: Option<&GuardPolicy>,
        operation_time: OperationTime,
        audit: Option<DecisionAudit<'_>>,
    ) -> Result<InboundConsumeOutcome, StoreError> {
        validate_lease_token(lease)?;
        if key.stream == DECISION_STREAM
            || key
                .event_id
                .parse::<u64>()
                .ok()
                .is_none_or(|id| id == 0 || id.to_string() != key.event_id)
        {
            return Err(StoreError::InvalidInput("invalid canonical inbound ID"));
        }
        if let Some(plan) = reply {
            if plan.response_id.is_empty()
                || plan.segments.is_empty()
                || plan.segments.len() > 32
                || plan.pending_batch_limit == 0
                || plan
                    .segments
                    .iter()
                    .any(|s| s.kind.is_empty() || s.payload.is_empty() || s.payload.len() > 16384)
            {
                return Err(StoreError::InvalidInput("invalid inbound reply plan"));
            }
        }
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now = operation_time.resolve()?;
        require_fence(&transaction, lease, now)?;
        let receipt = load_inbound_receipt(
            &transaction,
            &lease.account_id,
            key.stream,
            key.generation,
            key.event_id,
        )?;
        if receipt.payload_hash != key.payload_hash {
            return Err(StoreError::IdempotencyConflict {
                entity: "inbound decision",
                key: key.event_id.to_owned(),
            });
        }
        let trigger = format!("auto:{}", key.event_id);
        let existing = transaction
            .query_row(
                "SELECT id FROM outbound_batches WHERE account_id=?1 AND trigger_id=?2",
                params![lease.account_id, trigger],
                |r| r.get::<_, String>(0),
            )
            .optional()?;
        let mut batch = existing
            .map(|id| load_batch(&transaction, &id))
            .transpose()?;
        let decided=transaction.query_row("SELECT 1 FROM inbound_receipts WHERE account_id=?1 AND stream=?2 AND stream_generation=1 AND event_id=?3",
            params![lease.account_id,DECISION_STREAM,key.event_id],|_|Ok(())).optional()?.is_some();
        let applied = !decided && receipt.status == InboundStatus::Pending;
        if applied {
            if let Some(audit) = audit {
                audit_feed::decision(&transaction, lease, receipt.payload.as_deref(), audit, now)?;
            }
            if let Some(plan) = reply {
                if batch.is_none() {
                    let count:u32=transaction.query_row("SELECT count(*) FROM outbound_batches WHERE account_id=?1 AND status NOT IN ('confirmed','rejected','partial')",
                        [&lease.account_id],|r|r.get(0))?;
                    if count >= plan.pending_batch_limit {
                        return Err(StoreError::InvalidInput("outbound pending spool is full"));
                    }
                    batch = Some(prepare_batch_in_transaction(
                        &transaction,
                        lease,
                        &trigger,
                        plan.response_id,
                        plan.segments,
                        now,
                    )?);
                }
            }
            if let (Some(policy), Some(batch)) = (policy, batch.as_ref()) {
                guards::reserve(&transaction, batch, policy, now)?;
            }
            transaction.execute("INSERT INTO inbound_receipts(account_id,stream,stream_generation,event_id,page_checkpoint,payload,payload_hash,status,
                fence_epoch,received_at_ms,processed_at_ms,processed_fence_epoch) VALUES(?1,?2,1,?3,0,NULL,?4,'processed',?5,?6,?6,?5)",
                params![lease.account_id,DECISION_STREAM,key.event_id,key.payload_hash,lease.fence_epoch,now])?;
        }
        transaction.execute("UPDATE inbound_receipts SET status='processed',payload=NULL,processed_at_ms=?5,processed_fence_epoch=?6
            WHERE account_id=?1 AND stream=?2 AND stream_generation=?3 AND event_id=?4 AND status='pending'",
            params![lease.account_id,key.stream,key.generation,key.event_id,now,lease.fence_epoch])?;
        transaction.commit()?;
        Ok(InboundConsumeOutcome { applied, batch })
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
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now_ms = operation_time.resolve()?;
        require_fence(&transaction, lease_token, now_ms)?;

        let batch = prepare_batch_in_transaction(
            &transaction,
            lease_token,
            trigger_id,
            response_id,
            segments,
            now_ms,
        )?;
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

    /// # Errors
    /// Account removal must not strand an unresolved send; history remains independent.
    pub fn has_unfinished_account(&self, id: &str) -> Result<bool, StoreError> {
        Ok(self.lock_connection()?.query_row("SELECT EXISTS(SELECT 1 FROM outbound_batches WHERE account_id=?1 AND status NOT IN ('confirmed','rejected','partial'))",[id],|r|r.get(0))?)
    }
    /// Reads the existing durable idempotency claim without creating work.
    /// # Errors
    /// Returns validation, database, or corrupt-record errors.
    pub fn outbound_batch_for_trigger(
        &self,
        account_id: &str,
        trigger_id: &str,
    ) -> Result<Option<OutboundBatch>, StoreError> {
        validate_non_empty(account_id, "account_id must not be empty")?;
        validate_non_empty(trigger_id, "trigger_id must not be empty")?;
        let connection = self.lock_connection()?;
        let id: Option<String> = connection
            .query_row(
                "SELECT id FROM outbound_batches WHERE account_id = ?1 AND trigger_id = ?2",
                params![account_id, trigger_id],
                |row| row.get(0),
            )
            .optional()?;
        id.map(|id| load_batch(&connection, &id)).transpose()
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
            None,
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
            None,
        )
    }

    /// Persists verified send evidence in the same transaction as the result.
    /// # Errors
    /// A stale binding/fence or storage failure leaves the entire result uncommitted.
    pub fn transition_segment_observed(
        &self,
        lease: &LeaseToken,
        id: &str,
        transition: SegmentTransition,
        observation: SendObservation<'_>,
    ) -> Result<TransitionOutcome, StoreError> {
        self.transition_segment_with_time(
            lease,
            id,
            transition,
            OperationTime::System,
            Some(observation),
        )
    }
    /// Call only after native authenticated self identity has verified the canonical scope.
    /// # Errors
    /// Rejects stale ownership, invalid binding or storage capacity/failure.
    pub fn restore_send_observation(
        &self,
        lease: &LeaseToken,
        canonical: &str,
        digest: &str,
    ) -> Result<crate::state::SendCapability, StoreError> {
        let mut c = self.lock_connection()?;
        let t = c.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now = OperationTime::System.resolve()?;
        require_fence(&t, lease, now)?;
        let result = protocol_state::restore(&t, lease, canonical, digest, now)?;
        t.commit()?;
        Ok(result)
    }

    fn transition_segment_with_time(
        &self,
        lease_token: &LeaseToken,
        segment_id: &str,
        transition: SegmentTransition,
        operation_time: OperationTime,
        observation: Option<SendObservation<'_>>,
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

        if matches!(transition, SegmentTransition::StartAutomaticAttempt) {
            guards::require_reservation(&transaction, &batch_id, account_id)?;
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

        if !valid_segment_transition(current, target, &transition) {
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
        guards::settle(&transaction, &batch_id, now_ms)?;
        if let Some(observation) = observation {
            protocol_state::record(&transaction, lease_token, observation, now_ms)?;
        }
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

    /// Returns the last completed cleanup pass, or `None` before the first pass.
    ///
    /// # Errors
    ///
    /// Returns an error if the persisted pressure value is corrupt or the
    /// database query fails.
    pub fn storage_cleanup_state(&self) -> Result<Option<StorageCleanupState>, StoreError> {
        let connection = self.lock_connection()?;
        let raw = connection.query_row(
            "SELECT last_cleanup_at_ms, last_pressure
             FROM storage_cleanup_state WHERE singleton = 1",
            [],
            |row| {
                Ok((
                    row.get::<_, Option<i64>>(0)?,
                    row.get::<_, Option<String>>(1)?,
                ))
            },
        )?;
        match raw {
            (None, None) => Ok(None),
            (Some(last_cleanup_at_ms), Some(raw_pressure)) if last_cleanup_at_ms >= 0 => {
                let last_pressure = decode_disk_pressure(&raw_pressure)?;
                Ok(Some(StorageCleanupState {
                    last_cleanup_at_ms,
                    last_pressure,
                }))
            }
            value => Err(StoreError::CorruptData {
                field: "storage_cleanup_state",
                value: format!("{value:?}"),
            }),
        }
    }

    /// Persists a completed cleanup pass for subsequent health reporting.
    /// The scheduler supplies the same observation timestamp used to calculate
    /// its cleanup plan.
    ///
    /// # Errors
    ///
    /// Returns an error for a negative timestamp or a failed transaction.
    pub fn record_storage_cleanup_state(
        &self,
        at_ms: i64,
        pressure: DiskPressure,
    ) -> Result<(), StoreError> {
        if at_ms < 0 {
            return Err(StoreError::InvalidInput(
                "cleanup timestamp must not be negative",
            ));
        }
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute(
            "UPDATE storage_cleanup_state
             SET last_cleanup_at_ms = ?1, last_pressure = ?2
             WHERE singleton = 1",
            params![at_ms, disk_pressure_as_str(pressure)],
        )?;
        if transaction.changes() != 1 {
            return Err(StoreError::SchemaInvariant(
                "storage cleanup singleton disappeared".to_owned(),
            ));
        }
        transaction.commit()?;
        Ok(())
    }

    /// Returns one sealed-segment manifest by its stable ID.
    ///
    /// # Errors
    ///
    /// Returns an error for an invalid ID, corrupt persisted metadata, or a
    /// database failure.
    pub fn segment_manifest(
        &self,
        segment_id: &str,
    ) -> Result<Option<SegmentManifest>, StoreError> {
        validate_segment_id(segment_id)?;
        let connection = self.lock_connection()?;
        load_segment_manifest(&connection, segment_id)
    }

    /// Lists manifests in deterministic seal-time order, optionally limited to
    /// a family and/or segments sealed at or before a timestamp.
    ///
    /// # Errors
    ///
    /// Returns an error for a negative timestamp, corrupt persisted metadata,
    /// or a database failure.
    pub fn segment_manifests(
        &self,
        family: Option<SegmentFamily>,
        sealed_before_or_at_ms: Option<i64>,
    ) -> Result<Vec<SegmentManifest>, StoreError> {
        if sealed_before_or_at_ms.is_some_and(|timestamp| timestamp < 0) {
            return Err(StoreError::InvalidInput(
                "sealed timestamp filter must not be negative",
            ));
        }
        let connection = self.lock_connection()?;
        let mut statement = connection.prepare(
            "SELECT segment_id, family, relative_path, record_count,
                    uncompressed_bytes, stored_bytes, content_sha256,
                    file_sha256, created_at_ms, sealed_at_ms, compression
             FROM segment_manifests
             ORDER BY sealed_at_ms, segment_id",
        )?;
        let rows = statement.query_map([], read_segment_manifest_row)?;
        let mut manifests = Vec::new();
        for row in rows {
            let manifest = decode_segment_manifest(row?)?;
            if family.is_some_and(|wanted| manifest.family != wanted)
                || sealed_before_or_at_ms.is_some_and(|cutoff| manifest.sealed_at_ms > cutoff)
            {
                continue;
            }
            manifests.push(manifest);
        }
        Ok(manifests)
    }

    /// Commits a sealed segment to the durable catalog. Repeating the exact
    /// same manifest is idempotent; reusing its ID or path for different
    /// metadata fails closed.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid metadata, an idempotency conflict, or a
    /// database failure.
    pub fn commit_segment_manifest(&self, manifest: &SegmentManifest) -> Result<(), StoreError> {
        validate_segment_manifest(manifest)?;
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(existing) = load_segment_manifest(&transaction, &manifest.segment_id)? {
            if existing == *manifest {
                transaction.commit()?;
                return Ok(());
            }
            return Err(StoreError::IdempotencyConflict {
                entity: "segment manifest",
                key: manifest.segment_id.clone(),
            });
        }
        let path_owner = transaction
            .query_row(
                "SELECT segment_id FROM segment_manifests WHERE relative_path = ?1",
                params![manifest.relative_path],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        if let Some(path_owner) = path_owner {
            return Err(StoreError::IdempotencyConflict {
                entity: "segment manifest path",
                key: format!("{} owned by {path_owner}", manifest.relative_path),
            });
        }
        transaction.execute(
            "INSERT INTO segment_manifests
             (segment_id, family, relative_path, record_count,
              uncompressed_bytes, stored_bytes, content_sha256, file_sha256,
              created_at_ms, sealed_at_ms, compression)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![
                manifest.segment_id,
                manifest.family.as_str(),
                manifest.relative_path,
                u64_to_sqlite(
                    manifest.record_count,
                    "segment record_count exceeds SQLite integer range"
                )?,
                u64_to_sqlite(
                    manifest.uncompressed_bytes,
                    "segment uncompressed_bytes exceeds SQLite integer range"
                )?,
                u64_to_sqlite(
                    manifest.stored_bytes,
                    "segment stored_bytes exceeds SQLite integer range"
                )?,
                manifest.content_sha256,
                manifest.file_sha256,
                manifest.created_at_ms,
                manifest.sealed_at_ms,
                manifest.compression,
            ],
        )?;
        transaction.commit()?;
        Ok(())
    }

    /// Durably records intent to delete a sealed segment. The manifest remains
    /// readable until [`Self::complete_segment_deletion`] commits completion.
    ///
    /// # Errors
    ///
    /// Returns an error when the ID is invalid, the manifest is missing, the
    /// system clock is unavailable, or the transaction fails.
    pub fn mark_segment_deleting(&self, segment_id: &str) -> Result<(), StoreError> {
        self.mark_segment_deleting_with_time(segment_id, OperationTime::System)
    }

    #[cfg(test)]
    fn mark_segment_deleting_at(&self, segment_id: &str, now_ms: i64) -> Result<(), StoreError> {
        self.mark_segment_deleting_with_time(segment_id, OperationTime::Fixed(now_ms))
    }

    fn mark_segment_deleting_with_time(
        &self,
        segment_id: &str,
        operation_time: OperationTime,
    ) -> Result<(), StoreError> {
        validate_segment_id(segment_id)?;
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let exists = transaction
            .query_row(
                "SELECT 1 FROM segment_manifests WHERE segment_id = ?1",
                params![segment_id],
                |_| Ok(()),
            )
            .optional()?
            .is_some();
        if !exists {
            return Err(StoreError::NotFound {
                entity: "segment manifest",
                id: segment_id.to_owned(),
            });
        }
        let now_ms = operation_time.resolve()?;
        if now_ms < 0 {
            return Err(StoreError::InvalidInput(
                "cleanup intent timestamp must not be negative",
            ));
        }
        transaction.execute(
            "INSERT INTO storage_cleanup_journal(segment_id, intent_created_at_ms)
             VALUES (?1, ?2)
             ON CONFLICT(segment_id) DO NOTHING",
            params![segment_id, now_ms],
        )?;
        transaction.commit()?;
        Ok(())
    }

    /// Lists deletion intents that must be replayed after a crash.
    ///
    /// # Errors
    ///
    /// Returns an error if persisted IDs are invalid or the query fails.
    pub fn pending_segment_deletions(&self) -> Result<Vec<PendingSegmentDeletion>, StoreError> {
        let connection = self.lock_connection()?;
        let mut statement = connection.prepare(
            "SELECT segment_id FROM storage_cleanup_journal
             ORDER BY intent_created_at_ms, segment_id",
        )?;
        let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
        let mut pending = Vec::new();
        for row in rows {
            let segment_id = row?;
            validate_segment_id(&segment_id).map_err(|_| StoreError::CorruptData {
                field: "storage_cleanup_journal.segment_id",
                value: segment_id.clone(),
            })?;
            pending.push(PendingSegmentDeletion { segment_id });
        }
        Ok(pending)
    }

    /// Atomically removes a manifest and its durable deletion intent after the
    /// caller has unlinked and synchronized the sealed file. Repeating an
    /// already completed deletion is harmless; skipping the intent is rejected.
    ///
    /// # Errors
    ///
    /// Returns an error for an invalid ID, a manifest without a prior intent,
    /// or a database failure.
    pub fn complete_segment_deletion(&self, segment_id: &str) -> Result<(), StoreError> {
        validate_segment_id(segment_id)?;
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let manifest_exists = transaction
            .query_row(
                "SELECT 1 FROM segment_manifests WHERE segment_id = ?1",
                params![segment_id],
                |_| Ok(()),
            )
            .optional()?
            .is_some();
        let intent_exists = transaction
            .query_row(
                "SELECT 1 FROM storage_cleanup_journal WHERE segment_id = ?1",
                params![segment_id],
                |_| Ok(()),
            )
            .optional()?
            .is_some();
        if !manifest_exists && !intent_exists {
            transaction.commit()?;
            return Ok(());
        }
        if manifest_exists && !intent_exists {
            return Err(StoreError::InvalidTransition {
                entity: "segment manifest",
                from: "sealed".to_owned(),
                to: "deleted without cleanup intent".to_owned(),
            });
        }
        if !manifest_exists {
            return Err(StoreError::SchemaInvariant(format!(
                "cleanup intent for {segment_id:?} has no manifest"
            )));
        }
        transaction.execute(
            "DELETE FROM storage_cleanup_journal WHERE segment_id = ?1",
            params![segment_id],
        )?;
        transaction.execute(
            "DELETE FROM segment_manifests WHERE segment_id = ?1",
            params![segment_id],
        )?;
        transaction.commit()?;
        Ok(())
    }

    fn lock_connection(&self) -> Result<MutexGuard<'_, Connection>, StoreError> {
        self.connection.lock().map_err(|_| StoreError::LockPoisoned)
    }
}

impl SegmentCatalog for CoreStore {
    fn list_manifests(&self) -> Result<Vec<SegmentManifest>, CatalogError> {
        self.segment_manifests(None, None)
            .map_err(|error| CatalogError::new(error.to_string()))
    }

    fn commit_manifest(&self, manifest: &SegmentManifest) -> Result<(), CatalogError> {
        self.commit_segment_manifest(manifest)
            .map_err(|error| CatalogError::new(error.to_string()))
    }

    fn begin_delete(&self, segment_id: &str) -> Result<(), CatalogError> {
        self.mark_segment_deleting(segment_id)
            .map_err(|error| CatalogError::new(error.to_string()))
    }

    fn list_pending_deletions(&self) -> Result<Vec<PendingSegmentDeletion>, CatalogError> {
        self.pending_segment_deletions()
            .map_err(|error| CatalogError::new(error.to_string()))
    }

    fn finish_delete(&self, segment_id: &str) -> Result<(), CatalogError> {
        self.complete_segment_deletion(segment_id)
            .map_err(|error| CatalogError::new(error.to_string()))
    }
}

fn initialize_new_database(connection: &mut Connection) -> Result<Uuid, StoreError> {
    if CORE_SCHEMA_VERSION != SCHEMA_V4 {
        return Err(StoreError::SchemaInvariant(format!(
            "compiled schema version is {CORE_SCHEMA_VERSION}, expected {SCHEMA_V4}"
        )));
    }
    let database_id = Uuid::new_v4();
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    transaction.execute_batch(
        "CREATE TABLE meta (
             key TEXT PRIMARY KEY NOT NULL,
             value TEXT NOT NULL
         );",
    )?;
    transaction.execute_batch(SCHEMA_V1_SQL)?;
    transaction.execute_batch(SCHEMA_V2_SQL)?;
    guards::create_schema(&transaction)?;
    protocol_state::create_schema(&transaction)?;
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

fn validate_or_migrate_existing_database(
    connection: &mut Connection,
    data_dir: &Path,
) -> Result<Uuid, StoreError> {
    if CORE_SCHEMA_VERSION != SCHEMA_V4 {
        return Err(StoreError::SchemaInvariant(format!(
            "compiled schema version is {CORE_SCHEMA_VERSION}, expected {SCHEMA_V4}"
        )));
    }
    if !table_exists(connection, "meta")? {
        return Err(StoreError::SchemaInvariant(
            "required table \"meta\" is missing".to_owned(),
        ));
    }

    match read_schema_version(connection)? {
        SCHEMA_V1 => {
            migrate_v1_to_v2(connection, data_dir)?;
            migrate_v2_to_v3(connection, data_dir)?;
            migrate_v3_to_v4(connection, data_dir)
        }
        SCHEMA_V2 => {
            migrate_v2_to_v3(connection, data_dir)?;
            migrate_v3_to_v4(connection, data_dir)
        }
        SCHEMA_V3 => migrate_v3_to_v4(connection, data_dir),
        SCHEMA_V4 => validate_existing_database(connection),
        found => Err(StoreError::UnsupportedSchema {
            found,
            supported: CORE_SCHEMA_VERSION,
        }),
    }
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
    validate_schema_v1(connection, SCHEMA_V4)?;
    validate_schema_v2(connection)?;
    guards::validate_schema(connection)?;
    protocol_state::validate_schema(connection)?;
    let integrity = inspect_database_integrity(connection)?;
    if !integrity.is_valid() {
        return Err(StoreError::SchemaInvariant(format!(
            "database integrity failed: quick_check={:?}, foreign_key_violations={}",
            integrity.quick_check, integrity.foreign_key_violations
        )));
    }
    read_database_id(connection)
}

fn validate_schema_v1(connection: &Connection, expected_version: u32) -> Result<(), StoreError> {
    let user_version: u32 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    if user_version != expected_version {
        return Err(StoreError::SchemaInvariant(format!(
            "PRAGMA user_version is {user_version}, expected {expected_version}"
        )));
    }

    validate_required_tables(connection, REQUIRED_TABLES_V1)?;
    for index in [
        "outbound_batches_account_status_idx",
        "outbound_segments_batch_status_idx",
        "inbound_receipts_pending_idx",
    ] {
        validate_required_index(connection, index)?;
    }
    Ok(())
}

fn validate_schema_v2(connection: &Connection) -> Result<(), StoreError> {
    validate_required_tables(connection, REQUIRED_TABLES_V2)?;
    for index in [
        "segment_manifests_family_sealed_idx",
        "storage_cleanup_journal_intent_idx",
    ] {
        validate_required_index(connection, index)?;
    }
    let cleanup_state_count: u32 = connection.query_row(
        "SELECT COUNT(*) FROM storage_cleanup_state WHERE singleton = 1",
        [],
        |row| row.get(0),
    )?;
    if cleanup_state_count != 1 {
        return Err(StoreError::SchemaInvariant(
            "storage_cleanup_state singleton row is missing".to_owned(),
        ));
    }
    Ok(())
}

fn validate_required_tables(
    connection: &Connection,
    required_tables: &[(&str, &[&str])],
) -> Result<(), StoreError> {
    for (table, required_columns) in required_tables {
        if !table_exists(connection, table)? {
            return Err(StoreError::SchemaInvariant(format!(
                "required table {table:?} is missing"
            )));
        }

        let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
        let rows = statement.query_map([], |row| row.get::<_, String>(1))?;
        let columns = rows.collect::<Result<Vec<_>, _>>()?;
        let expected = required_columns
            .iter()
            .map(|column| (*column).to_owned())
            .collect::<Vec<_>>();
        if columns != expected {
            return Err(StoreError::SchemaInvariant(format!(
                "required columns for {table:?} drifted: found {columns:?}, expected {expected:?}"
            )));
        }
    }
    Ok(())
}

fn validate_required_index(connection: &Connection, index: &str) -> Result<(), StoreError> {
    let exists = connection
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?1",
            params![index],
            |_| Ok(()),
        )
        .optional()?
        .is_some();
    if !exists {
        return Err(StoreError::SchemaInvariant(format!(
            "required index {index} is missing"
        )));
    }
    Ok(())
}

fn migrate_v1_to_v2(connection: &mut Connection, data_dir: &Path) -> Result<Uuid, StoreError> {
    validate_schema_v1(connection, SCHEMA_V1)?;
    let database_id = read_database_id(connection)?;
    let integrity = inspect_database_integrity(connection)?;
    if !integrity.is_valid() {
        return Err(StoreError::SchemaInvariant(format!(
            "schema v1 integrity failed before migration: quick_check={:?}, foreign_key_violations={}",
            integrity.quick_check, integrity.foreign_key_violations
        )));
    }
    let backup = create_fresh_v1_backup(connection, data_dir, database_id)?;
    let applied_at_ms = current_time_ms()?.max(backup.created_at_ms);

    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    transaction.execute_batch(SCHEMA_V2_SQL)?;
    transaction.execute(
        "INSERT INTO schema_migrations
         (from_version, to_version, database_id, backup_relative_path,
          backup_bytes, backup_created_at_ms, applied_at_ms,
          backup_quick_check, backup_foreign_key_violations)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        params![
            SCHEMA_V1,
            SCHEMA_V2,
            database_id.to_string(),
            path_to_portable_string(&backup.relative_path)?,
            u64_to_sqlite(backup.bytes, "backup bytes exceed SQLite integer range")?,
            backup.created_at_ms,
            applied_at_ms,
            backup.integrity.quick_check,
            u64_to_sqlite(
                backup.integrity.foreign_key_violations,
                "foreign key violation count exceeds SQLite integer range"
            )?,
        ],
    )?;
    transaction.execute(
        "UPDATE meta SET value = ?1 WHERE key = 'schema_version'",
        params![SCHEMA_V2.to_string()],
    )?;
    if transaction.changes() != 1 {
        return Err(StoreError::SchemaInvariant(
            "schema version row disappeared during migration".to_owned(),
        ));
    }
    transaction.pragma_update(None, "user_version", SCHEMA_V2)?;
    transaction.commit()?;

    validate_schema_v1(connection, SCHEMA_V2)?;
    validate_schema_v2(connection)?;
    let durable_database_id = read_database_id(connection)?;
    if durable_database_id != database_id {
        return Err(StoreError::SchemaInvariant(
            "database identity changed during schema v1-to-v2 migration".to_owned(),
        ));
    }
    Ok(database_id)
}

fn migrate_v2_to_v3(connection: &mut Connection, data_dir: &Path) -> Result<Uuid, StoreError> {
    validate_schema_v1(connection, SCHEMA_V2)?;
    validate_schema_v2(connection)?;
    let id = read_database_id(connection)?;
    let backup = create_fresh_backup(connection, data_dir, id, SCHEMA_V2, SCHEMA_V3)?;
    let applied = current_time_ms()?.max(backup.created_at_ms);
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    guards::create_schema(&transaction)?;
    transaction.execute("INSERT INTO schema_migrations(from_version,to_version,database_id,backup_relative_path,backup_bytes,
        backup_created_at_ms,applied_at_ms,backup_quick_check,backup_foreign_key_violations) VALUES(?1,?2,?3,?4,?5,?6,?7,'ok',0)",
        params![SCHEMA_V2,SCHEMA_V3,id.to_string(),path_to_portable_string(&backup.relative_path)?,
            u64_to_sqlite(backup.bytes,"backup too large")?,backup.created_at_ms,applied])?;
    transaction.execute(
        "UPDATE meta SET value=?1 WHERE key='schema_version'",
        [SCHEMA_V3.to_string()],
    )?;
    if transaction.changes() != 1 {
        return Err(StoreError::SchemaInvariant("missing schema version".into()));
    }
    transaction.pragma_update(None, "user_version", SCHEMA_V3)?;
    transaction.commit()?;
    validate_schema_v1(connection, SCHEMA_V3)?;
    guards::validate_schema(connection)?;
    let actual = read_database_id(connection)?;
    if actual != id {
        return Err(StoreError::SchemaInvariant(
            "migration identity changed".into(),
        ));
    }
    Ok(id)
}

fn migrate_v3_to_v4(connection: &mut Connection, data_dir: &Path) -> Result<Uuid, StoreError> {
    validate_schema_v1(connection, SCHEMA_V3)?;
    validate_schema_v2(connection)?;
    guards::validate_schema(connection)?;
    let id = read_database_id(connection)?;
    let backup = create_fresh_backup(connection, data_dir, id, SCHEMA_V3, SCHEMA_V4)?;
    let applied = current_time_ms()?.max(backup.created_at_ms);
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    protocol_state::create_schema(&transaction)?;
    transaction.execute("INSERT INTO schema_migrations(from_version,to_version,database_id,backup_relative_path,backup_bytes,
        backup_created_at_ms,applied_at_ms,backup_quick_check,backup_foreign_key_violations) VALUES(?1,?2,?3,?4,?5,?6,?7,'ok',0)",
        params![SCHEMA_V3,SCHEMA_V4,id.to_string(),path_to_portable_string(&backup.relative_path)?,
            u64_to_sqlite(backup.bytes,"backup too large")?,backup.created_at_ms,applied])?;
    transaction.execute(
        "UPDATE meta SET value=?1 WHERE key='schema_version'",
        [SCHEMA_V4.to_string()],
    )?;
    if transaction.changes() != 1 {
        return Err(StoreError::SchemaInvariant("missing schema version".into()));
    }
    transaction.pragma_update(None, "user_version", SCHEMA_V4)?;
    transaction.commit()?;
    let actual = validate_existing_database(connection)?;
    if actual != id {
        return Err(StoreError::SchemaInvariant(
            "migration identity changed".into(),
        ));
    }
    Ok(id)
}

fn create_fresh_v1_backup(
    source: &Connection,
    data_dir: &Path,
    database_id: Uuid,
) -> Result<PreMigrationBackup, StoreError> {
    create_fresh_backup(source, data_dir, database_id, SCHEMA_V1, SCHEMA_V2)
}
fn create_fresh_backup(
    source: &Connection,
    data_dir: &Path,
    database_id: Uuid,
    from: u32,
    to: u32,
) -> Result<PreMigrationBackup, StoreError> {
    let backup_directory = data_dir.join(BACKUP_DIRECTORY_NAME);
    prepare_private_directory(&backup_directory)?;
    // Persist creation of the backup directory before relying on a file inside
    // it as the migration rollback boundary.
    sync_directory(data_dir)?;
    let backup_file_name = format!("core-v{from}-to-v{to}-{database_id}.sqlite3");
    let relative_path = PathBuf::from(BACKUP_DIRECTORY_NAME).join(&backup_file_name);
    let final_path = data_dir.join(&relative_path);
    let temporary_path = backup_directory.join(format!(".{backup_file_name}.tmp"));
    let previous_path = backup_directory.join(format!(".{backup_file_name}.previous"));

    #[cfg(not(unix))]
    recover_interrupted_backup_publish(
        &final_path,
        &previous_path,
        &relative_path,
        database_id,
        &backup_directory,
        from,
        to,
    )?;

    if optional_regular_file_exists(&final_path)? {
        // A previous failed migration may have left a valid but now stale
        // snapshot. Verify it before touching it, then keep it in place until
        // a freshly verified replacement is ready. This preserves a usable
        // rollback point if the new Online Backup or its verification fails.
        verify_backup(&final_path, relative_path.clone(), database_id, from, to)?;
    }
    if optional_regular_file_exists(&temporary_path)? {
        fs::remove_file(&temporary_path)?;
        sync_directory(&backup_directory)?;
    }

    let backup_result = (|| -> Result<(), StoreError> {
        let mut destination = Connection::open_with_flags(
            &temporary_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE,
        )?;
        let backup = Backup::new(source, &mut destination)?;
        backup.run_to_completion(128, Duration::from_millis(2), None)?;
        drop(backup);
        destination.close().map_err(|(_, error)| error)?;
        enforce_private_file_permissions(&temporary_path)?;

        OpenOptions::new()
            .read(true)
            .open(&temporary_path)?
            .sync_all()?;
        verify_backup(
            &temporary_path,
            relative_path.clone(),
            database_id,
            from,
            to,
        )?;
        publish_verified_backup(
            &temporary_path,
            &final_path,
            &previous_path,
            &backup_directory,
        )?;
        enforce_private_file_permissions(&final_path)?;
        sync_directory(&backup_directory)?;
        Ok(())
    })();
    if backup_result.is_err() {
        let _ = fs::remove_file(&temporary_path);
    }
    backup_result?;

    verify_backup(&final_path, relative_path, database_id, from, to)
}

#[cfg(not(unix))]
fn recover_interrupted_backup_publish(
    final_path: &Path,
    previous_path: &Path,
    relative_path: &Path,
    database_id: Uuid,
    backup_directory: &Path,
    from: u32,
    to: u32,
) -> Result<(), StoreError> {
    if !optional_regular_file_exists(previous_path)? {
        return Ok(());
    }
    verify_backup(
        previous_path,
        relative_path.to_owned(),
        database_id,
        from,
        to,
    )?;
    if optional_regular_file_exists(final_path)? {
        if verify_backup(final_path, relative_path.to_owned(), database_id, from, to).is_ok() {
            fs::remove_file(previous_path)?;
            sync_directory(backup_directory)?;
            return Ok(());
        }
        fs::remove_file(final_path)?;
    }
    fs::rename(previous_path, final_path)?;
    sync_directory(backup_directory)?;
    verify_backup(final_path, relative_path.to_owned(), database_id, from, to)?;
    Ok(())
}

#[cfg(unix)]
fn publish_verified_backup(
    temporary_path: &Path,
    final_path: &Path,
    _previous_path: &Path,
    _backup_directory: &Path,
) -> Result<(), StoreError> {
    fs::rename(temporary_path, final_path)?;
    Ok(())
}

#[cfg(not(unix))]
fn publish_verified_backup(
    temporary_path: &Path,
    final_path: &Path,
    previous_path: &Path,
    backup_directory: &Path,
) -> Result<(), StoreError> {
    if !optional_regular_file_exists(final_path)? {
        fs::rename(temporary_path, final_path)?;
        return Ok(());
    }
    if optional_regular_file_exists(previous_path)? {
        return Err(StoreError::UnsafeStoragePath {
            path: previous_path.to_owned(),
            reason: "previous backup publish state was not recovered".to_owned(),
        });
    }
    fs::rename(final_path, previous_path)?;
    sync_directory(backup_directory)?;
    if let Err(error) = fs::rename(temporary_path, final_path) {
        fs::rename(previous_path, final_path).map_err(|restore_error| {
            StoreError::BackupVerification(format!(
                "backup publish failed ({error}) and rollback failed ({restore_error})"
            ))
        })?;
        sync_directory(backup_directory)?;
        return Err(StoreError::Io(error));
    }
    sync_directory(backup_directory)?;
    fs::remove_file(previous_path)?;
    sync_directory(backup_directory)?;
    Ok(())
}

#[cfg(test)]
fn verify_v1_backup(
    path: &Path,
    relative: PathBuf,
    id: Uuid,
) -> Result<PreMigrationBackup, StoreError> {
    verify_backup(path, relative, id, SCHEMA_V1, SCHEMA_V2)
}
fn verify_backup(
    backup_path: &Path,
    relative_path: PathBuf,
    expected_database_id: Uuid,
    from: u32,
    to: u32,
) -> Result<PreMigrationBackup, StoreError> {
    require_regular_file(backup_path)?;
    enforce_private_file_permissions(backup_path)?;
    let connection = Connection::open_with_flags(backup_path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let integrity = inspect_database_integrity(&connection)?;
    if !integrity.is_valid() {
        return Err(StoreError::BackupVerification(format!(
            "{} has quick_check={:?} and {} foreign-key violations",
            backup_path.display(),
            integrity.quick_check,
            integrity.foreign_key_violations
        )));
    }
    let schema_version = read_schema_version(&connection)?;
    if schema_version != from {
        return Err(StoreError::BackupVerification(format!(
            "{} has schema version {schema_version}, expected {from}",
            backup_path.display()
        )));
    }
    validate_schema_v1(&connection, from).map_err(|error| {
        StoreError::BackupVerification(format!(
            "{} does not contain the expected schema {from}: {error}",
            backup_path.display()
        ))
    })?;
    if from >= SCHEMA_V2 {
        validate_schema_v2(&connection)?;
    }
    if from >= SCHEMA_V3 {
        guards::validate_schema(&connection)?;
    }
    let database_id = read_database_id(&connection)?;
    if database_id != expected_database_id {
        return Err(StoreError::BackupVerification(format!(
            "{} contains database id {database_id}, expected {expected_database_id}",
            backup_path.display()
        )));
    }
    drop(connection);

    let metadata = fs::metadata(backup_path)?;
    let bytes = metadata.len();
    if bytes == 0 {
        return Err(StoreError::BackupVerification(format!(
            "{} is empty",
            backup_path.display()
        )));
    }
    let created_at_ms = system_time_to_ms(metadata.modified()?)?;
    Ok(PreMigrationBackup {
        from_version: from,
        to_version: to,
        database_id,
        relative_path,
        bytes,
        created_at_ms,
        applied_at_ms: created_at_ms,
        integrity,
    })
}

fn inspect_database_integrity(connection: &Connection) -> Result<DatabaseIntegrity, StoreError> {
    let quick_rows = {
        let mut statement = connection.prepare("PRAGMA quick_check")?;
        let rows = statement.query_map([], |row| row.get::<_, String>(0))?;
        rows.collect::<Result<Vec<_>, _>>()?
    };
    let quick_check = if quick_rows.is_empty() {
        "missing quick_check result".to_owned()
    } else {
        quick_rows.join("; ")
    };

    let mut statement = connection.prepare("PRAGMA foreign_key_check")?;
    let mut rows = statement.query([])?;
    let mut foreign_key_violations = 0_u64;
    while rows.next()?.is_some() {
        foreign_key_violations = foreign_key_violations.checked_add(1).ok_or_else(|| {
            StoreError::SchemaInvariant("foreign-key violation count overflowed".to_owned())
        })?;
    }
    Ok(DatabaseIntegrity {
        quick_check,
        foreign_key_violations,
    })
}

fn load_pre_migration_backup(
    connection: &Connection,
) -> Result<Option<PreMigrationBackup>, StoreError> {
    load_migration_backup(connection, SCHEMA_V1, SCHEMA_V2)
}
fn load_migration_backup(
    connection: &Connection,
    from: u32,
    to: u32,
) -> Result<Option<PreMigrationBackup>, StoreError> {
    let raw = connection
        .query_row(
            "SELECT from_version, to_version, database_id, backup_relative_path,
                    backup_bytes, backup_created_at_ms, applied_at_ms,
                    backup_quick_check, backup_foreign_key_violations
             FROM schema_migrations
             WHERE from_version = ?1 AND to_version = ?2",
            params![from, to],
            |row| {
                Ok((
                    row.get::<_, u32>(0)?,
                    row.get::<_, u32>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                    row.get::<_, i64>(4)?,
                    row.get::<_, i64>(5)?,
                    row.get::<_, i64>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, i64>(8)?,
                ))
            },
        )
        .optional()?;
    let Some(raw) = raw else {
        return Ok(None);
    };
    let database_id = parse_database_id(&raw.2, "schema_migrations.database_id")?;
    let relative_path = validate_relative_path(&raw.3)?.to_owned();
    let bytes = u64::try_from(raw.4).map_err(|_| StoreError::CorruptData {
        field: "schema_migrations.backup_bytes",
        value: raw.4.to_string(),
    })?;
    let foreign_key_violations = u64::try_from(raw.8).map_err(|_| StoreError::CorruptData {
        field: "schema_migrations.backup_foreign_key_violations",
        value: raw.8.to_string(),
    })?;
    Ok(Some(PreMigrationBackup {
        from_version: raw.0,
        to_version: raw.1,
        database_id,
        relative_path,
        bytes,
        created_at_ms: raw.5,
        applied_at_ms: raw.6,
        integrity: DatabaseIntegrity {
            quick_check: raw.7,
            foreign_key_violations,
        },
    }))
}

type RawSegmentManifest = (
    String,
    String,
    String,
    i64,
    i64,
    i64,
    String,
    String,
    i64,
    i64,
    Option<String>,
);

fn read_segment_manifest_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<RawSegmentManifest> {
    Ok((
        row.get(0)?,
        row.get(1)?,
        row.get(2)?,
        row.get(3)?,
        row.get(4)?,
        row.get(5)?,
        row.get(6)?,
        row.get(7)?,
        row.get(8)?,
        row.get(9)?,
        row.get(10)?,
    ))
}

fn load_segment_manifest(
    connection: &Connection,
    segment_id: &str,
) -> Result<Option<SegmentManifest>, StoreError> {
    let raw = connection
        .query_row(
            "SELECT segment_id, family, relative_path, record_count,
                    uncompressed_bytes, stored_bytes, content_sha256,
                    file_sha256, created_at_ms, sealed_at_ms, compression
             FROM segment_manifests WHERE segment_id = ?1",
            params![segment_id],
            read_segment_manifest_row,
        )
        .optional()?;
    raw.map(decode_segment_manifest).transpose()
}

fn decode_segment_manifest(raw: RawSegmentManifest) -> Result<SegmentManifest, StoreError> {
    let family = match raw.1.as_str() {
        "chat" => SegmentFamily::Chat,
        "audit" => SegmentFamily::Audit,
        "debug" => SegmentFamily::Debug,
        _ => {
            return Err(StoreError::CorruptData {
                field: "segment_manifests.family",
                value: raw.1,
            });
        }
    };
    let manifest = SegmentManifest {
        segment_id: raw.0,
        family,
        relative_path: raw.2,
        record_count: decode_non_negative_u64("segment_manifests.record_count", raw.3)?,
        uncompressed_bytes: decode_non_negative_u64("segment_manifests.uncompressed_bytes", raw.4)?,
        stored_bytes: decode_non_negative_u64("segment_manifests.stored_bytes", raw.5)?,
        content_sha256: raw.6,
        file_sha256: raw.7,
        created_at_ms: raw.8,
        sealed_at_ms: raw.9,
        compression: raw.10,
    };
    validate_segment_manifest(&manifest).map_err(|error| StoreError::CorruptData {
        field: "segment_manifests row",
        value: format!("{}: {error}", manifest.segment_id),
    })?;
    Ok(manifest)
}

fn decode_non_negative_u64(field: &'static str, value: i64) -> Result<u64, StoreError> {
    u64::try_from(value).map_err(|_| StoreError::CorruptData {
        field,
        value: value.to_string(),
    })
}

fn validate_segment_manifest(manifest: &SegmentManifest) -> Result<(), StoreError> {
    validate_segment_id(&manifest.segment_id)?;
    validate_manifest_identity(manifest)
        .map_err(|error| StoreError::InvalidSegmentManifest(error.to_string()))?;
    if manifest.record_count == 0 {
        return Err(StoreError::InvalidInput(
            "segment record_count must be greater than zero",
        ));
    }
    if manifest.uncompressed_bytes == 0 || manifest.stored_bytes == 0 {
        return Err(StoreError::InvalidInput(
            "segment byte counts must be greater than zero",
        ));
    }
    let _ = u64_to_sqlite(
        manifest.record_count,
        "segment record_count exceeds SQLite integer range",
    )?;
    let _ = u64_to_sqlite(
        manifest.uncompressed_bytes,
        "segment uncompressed_bytes exceeds SQLite integer range",
    )?;
    let _ = u64_to_sqlite(
        manifest.stored_bytes,
        "segment stored_bytes exceeds SQLite integer range",
    )?;
    validate_sha256(&manifest.content_sha256)?;
    validate_sha256(&manifest.file_sha256)?;
    if manifest.created_at_ms < 0 || manifest.sealed_at_ms < manifest.created_at_ms {
        return Err(StoreError::InvalidInput(
            "segment timestamps must be non-negative and monotonically ordered",
        ));
    }
    match manifest.compression.as_deref() {
        None => {
            if manifest.uncompressed_bytes != manifest.stored_bytes
                || manifest.content_sha256 != manifest.file_sha256
            {
                return Err(StoreError::InvalidInput(
                    "uncompressed segment byte counts and digests must match",
                ));
            }
        }
        Some("zstd") => {}
        Some(_) => {
            return Err(StoreError::InvalidInput(
                "segment compression must be absent or zstd",
            ));
        }
    }
    Ok(())
}

fn validate_segment_id(segment_id: &str) -> Result<(), StoreError> {
    if segment_id.is_empty()
        || segment_id.len() > 128
        || !segment_id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return Err(StoreError::InvalidInput(
            "segment_id must use 1..=128 ASCII letters, digits, hyphens, or underscores",
        ));
    }
    Ok(())
}

fn validate_sha256(digest: &str) -> Result<(), StoreError> {
    if digest.len() != 64
        || !digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(StoreError::InvalidInput(
            "segment SHA-256 must contain exactly 64 lowercase hexadecimal characters",
        ));
    }
    Ok(())
}

const fn disk_pressure_as_str(pressure: DiskPressure) -> &'static str {
    match pressure {
        DiskPressure::Normal => "normal",
        DiskPressure::High => "high",
        DiskPressure::Critical => "critical",
    }
}

fn decode_disk_pressure(raw: &str) -> Result<DiskPressure, StoreError> {
    match raw {
        "normal" => Ok(DiskPressure::Normal),
        "high" => Ok(DiskPressure::High),
        "critical" => Ok(DiskPressure::Critical),
        _ => Err(StoreError::CorruptData {
            field: "storage_cleanup_state.last_pressure",
            value: raw.to_owned(),
        }),
    }
}

fn current_time_ms() -> Result<i64, StoreError> {
    system_time_to_ms(SystemTime::now())
}

fn system_time_to_ms(time: SystemTime) -> Result<i64, StoreError> {
    let elapsed = time
        .duration_since(UNIX_EPOCH)
        .map_err(|_| StoreError::SystemClockBeforeUnixEpoch)?;
    i64::try_from(elapsed.as_millis()).map_err(|_| StoreError::SystemTimeOverflow)
}

fn u64_to_sqlite(value: u64, message: &'static str) -> Result<i64, StoreError> {
    i64::try_from(value).map_err(|_| StoreError::InvalidInput(message))
}

fn path_to_portable_string(path: &Path) -> Result<String, StoreError> {
    path.to_str()
        .map(str::to_owned)
        .ok_or(StoreError::InvalidInput("path must be valid UTF-8"))
}

fn validate_relative_path(raw: &str) -> Result<&Path, StoreError> {
    if raw.is_empty() || raw.contains('\0') || raw.contains('\\') {
        return Err(StoreError::InvalidInput(
            "relative path must be non-empty portable UTF-8",
        ));
    }
    let path = Path::new(raw);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(StoreError::InvalidInput(
            "relative path must not contain roots, prefixes, or traversal components",
        ));
    }
    Ok(path)
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
    if !optional_regular_file_exists(marker_path)? {
        return Ok(None);
    }
    enforce_private_file_permissions(marker_path)?;
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
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        options.mode(0o600);
        let mut marker = options.open(&temporary_path)?;
        writeln!(marker, "{database_id}")?;
        marker.sync_all()?;
        drop(marker);
        fs::rename(&temporary_path, marker_path)?;
        enforce_private_file_permissions(marker_path)?;
        sync_directory(data_dir)?;
        Ok(())
    })();
    if write_result.is_err() {
        let _ = fs::remove_file(&temporary_path);
    }
    write_result
}

fn prepare_private_directory(path: &Path) -> Result<(), StoreError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => {}
        Ok(_) => {
            return Err(StoreError::UnsafeStoragePath {
                path: path.to_owned(),
                reason: "expected a real directory, not a symlink or non-directory".to_owned(),
            })
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir_all(path)?;
            let metadata = fs::symlink_metadata(path)?;
            if !metadata.file_type().is_dir() {
                return Err(StoreError::UnsafeStoragePath {
                    path: path.to_owned(),
                    reason: "created path is not a real directory".to_owned(),
                });
            }
        }
        Err(error) => return Err(StoreError::Io(error)),
    }
    enforce_private_directory_permissions(path)
}

fn optional_regular_file_exists(path: &Path) -> Result<bool, StoreError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_file() => Ok(true),
        Ok(_) => Err(StoreError::UnsafeStoragePath {
            path: path.to_owned(),
            reason: "expected a regular file, not a symlink or special entry".to_owned(),
        }),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(StoreError::Io(error)),
    }
}

fn require_regular_file(path: &Path) -> Result<(), StoreError> {
    if optional_regular_file_exists(path)? {
        Ok(())
    } else {
        Err(StoreError::UnsafeStoragePath {
            path: path.to_owned(),
            reason: "required regular file is missing".to_owned(),
        })
    }
}

#[cfg(unix)]
fn enforce_private_directory_permissions(path: &Path) -> Result<(), StoreError> {
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}

#[cfg(not(unix))]
fn enforce_private_directory_permissions(_path: &Path) -> Result<(), StoreError> {
    Ok(())
}

#[cfg(unix)]
fn enforce_private_file_permissions(path: &Path) -> Result<(), StoreError> {
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    Ok(())
}

#[cfg(not(unix))]
fn enforce_private_file_permissions(_path: &Path) -> Result<(), StoreError> {
    Ok(())
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

fn prepare_batch_in_transaction(
    transaction: &rusqlite::Transaction<'_>,
    lease_token: &LeaseToken,
    trigger_id: &str,
    response_id: &str,
    segments: &[OutboundSegmentDraft],
    now_ms: i64,
) -> Result<OutboundBatch, StoreError> {
    let account_id = lease_token.account_id.as_str();
    let existing_batch_id = transaction
        .query_row(
            "SELECT id FROM outbound_batches
                 WHERE account_id = ?1 AND trigger_id = ?2",
            params![account_id, trigger_id],
            |row| row.get::<_, String>(0),
        )
        .optional()?;

    if let Some(batch_id) = existing_batch_id {
        let batch = load_batch(transaction, &batch_id)?;
        if batch.response_id != response_id || !same_segment_plan(&batch.segments, segments) {
            return Err(StoreError::IdempotencyConflict {
                entity: "outbound batch",
                key: format!("{account_id}/{trigger_id}"),
            });
        }
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

    let batch = load_batch(transaction, &batch_id)?;
    Ok(batch)
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
        SegmentTransition::StartAttempt | SegmentTransition::StartAutomaticAttempt => {
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
        SegmentTransition::CancelPrepared { reason }
        | SegmentTransition::Reject { error: reason }
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

const fn valid_segment_transition(
    current: SegmentStatus,
    target: SegmentStatus,
    transition: &SegmentTransition,
) -> bool {
    if matches!(transition, SegmentTransition::CancelPrepared { .. }) {
        return matches!(current, SegmentStatus::Prepared | SegmentStatus::Retryable);
    }
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
        SegmentTransition::StartAttempt | SegmentTransition::StartAutomaticAttempt => Ok(()),
        SegmentTransition::Confirm {
            platform_message_id,
        } => validate_non_empty(platform_message_id, "platform_message_id must not be empty"),
        SegmentTransition::CancelPrepared { reason }
        | SegmentTransition::MarkRetryable { reason } => {
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
        SegmentTransition::StartAttempt | SegmentTransition::StartAutomaticAttempt => true,
        SegmentTransition::Confirm {
            platform_message_id,
        } => stored_platform_message_id == Some(platform_message_id.as_str()),
        SegmentTransition::Reject { error } => stored_error == Some(error.as_str()),
        SegmentTransition::CancelPrepared { reason }
        | SegmentTransition::MarkRetryable { reason }
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

    fn manifest(segment_id: &str, family: SegmentFamily, sealed_at_ms: i64) -> SegmentManifest {
        let created_at_ms = sealed_at_ms - 10;
        SegmentManifest {
            segment_id: segment_id.to_owned(),
            family,
            relative_path: format!(
                "{}_{created_at_ms}_{segment_id}_{sealed_at_ms}.segment",
                family.as_str()
            ),
            record_count: 2,
            uncompressed_bytes: 512,
            stored_bytes: 512,
            content_sha256: "a".repeat(64),
            file_sha256: "a".repeat(64),
            created_at_ms,
            sealed_at_ms,
            compression: None,
        }
    }

    fn create_schema_v1_fixture(data_dir: &Path) -> Uuid {
        fs::create_dir_all(data_dir).unwrap();
        let database_path = data_dir.join(DATABASE_FILE_NAME);
        let mut connection = Connection::open(&database_path).unwrap();
        connection
            .pragma_update(None, "foreign_keys", true)
            .unwrap();
        connection
            .pragma_update(None, "journal_mode", "WAL")
            .unwrap();
        connection
            .pragma_update(None, "synchronous", "FULL")
            .unwrap();
        let database_id = Uuid::new_v4();
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .unwrap();
        transaction
            .execute_batch(
                "CREATE TABLE meta (
                     key TEXT PRIMARY KEY NOT NULL,
                     value TEXT NOT NULL
                 );",
            )
            .unwrap();
        transaction.execute_batch(SCHEMA_V1_SQL).unwrap();
        transaction
            .execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', '1')",
                [],
            )
            .unwrap();
        transaction
            .execute(
                "INSERT INTO meta(key, value) VALUES (?1, ?2)",
                params![DATABASE_ID_META_KEY, database_id.to_string()],
            )
            .unwrap();
        transaction
            .execute(
                "INSERT INTO account_leases
                 (account_id, owner_instance_id, owner_boot_id, fence_epoch,
                  lease_until_ms, status, last_observed_at_ms, updated_at_ms)
                 VALUES ('fixture-account', 'fixture-instance', 'fixture-boot',
                         7, 9000, 'active', 100, 100)",
                [],
            )
            .unwrap();
        transaction
            .execute(
                "INSERT INTO inbound_receipts
                 (account_id, stream, stream_generation, event_id,
                  page_checkpoint, payload, payload_hash, status, fence_epoch,
                  received_at_ms, processed_at_ms, processed_fence_epoch)
                 VALUES ('fixture-account', 'messages', 3, 'event-1',
                         44, X'010203', 'payload-sha', 'pending', 7, 101, NULL, NULL)",
                [],
            )
            .unwrap();
        transaction
            .execute(
                "INSERT INTO inbound_checkpoints
                 (account_id, stream, stream_generation, checkpoint,
                  fence_epoch, updated_at_ms)
                 VALUES ('fixture-account', 'messages', 3, 44, 7, 101)",
                [],
            )
            .unwrap();
        transaction
            .execute(
                "INSERT INTO outbound_batches
                 (id, account_id, trigger_id, response_id, status,
                  created_fence_epoch, last_fence_epoch, created_at_ms, updated_at_ms)
                 VALUES ('batch-1', 'fixture-account', 'trigger-1', 'response-1',
                         'sending', 7, 7, 102, 103)",
                [],
            )
            .unwrap();
        transaction
            .execute(
                "INSERT INTO outbound_segments
                 (id, client_message_id, batch_id, ordinal, kind, payload, status,
                  attempt_count, platform_message_id, last_error,
                  last_fence_epoch, created_at_ms, updated_at_ms)
                 VALUES ('segment-1', 'client-message-1', 'batch-1', 0, 'text',
                         'fixture reply', 'sending', 1, NULL, NULL, 7, 102, 103)",
                [],
            )
            .unwrap();
        transaction
            .pragma_update(None, "user_version", SCHEMA_V1)
            .unwrap();
        transaction.commit().unwrap();
        drop(connection);
        write_initialized_marker(
            data_dir,
            &data_dir.join(INITIALIZED_MARKER_FILE_NAME),
            database_id,
        )
        .unwrap();
        database_id
    }

    #[test]
    fn open_configures_full_durability_and_validates_schema_v2() {
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
        assert!(table_exists(&connection, "segment_manifests").unwrap());
        assert!(table_exists(&connection, "storage_cleanup_journal").unwrap());
        assert!(table_exists(&connection, "schema_migrations").unwrap());
        assert_eq!(load_pre_migration_backup(&connection).unwrap(), None);
        assert_eq!(
            inspect_database_integrity(&connection).unwrap(),
            DatabaseIntegrity {
                quick_check: "ok".to_owned(),
                foreign_key_violations: 0,
            }
        );

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
    fn schema_v1_fixture_is_backed_up_and_migrated_once_without_changing_identity_or_rows() {
        let directory = tempfile::tempdir().unwrap();
        let database_id = create_schema_v1_fixture(directory.path());

        let store = CoreStore::open(directory.path()).unwrap();
        assert_eq!(store.schema_version().unwrap(), CORE_SCHEMA_VERSION);
        let backup = store
            .pre_migration_backup()
            .unwrap()
            .expect("migration backup metadata");
        assert_eq!(backup.from_version, SCHEMA_V1);
        assert_eq!(backup.to_version, SCHEMA_V2);
        assert_eq!(backup.database_id, database_id);
        assert!(backup.integrity.is_valid());
        assert!(backup.bytes > 0);

        let backup_path = directory.path().join(&backup.relative_path);
        assert!(backup_path.exists());
        let backup_connection =
            Connection::open_with_flags(&backup_path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
        assert_eq!(read_schema_version(&backup_connection).unwrap(), SCHEMA_V1);
        assert_eq!(read_database_id(&backup_connection).unwrap(), database_id);
        validate_schema_v1(&backup_connection, SCHEMA_V1).unwrap();
        assert!(!table_exists(&backup_connection, "segment_manifests").unwrap());
        for table in [
            "account_leases",
            "inbound_receipts",
            "inbound_checkpoints",
            "outbound_batches",
            "outbound_segments",
        ] {
            let count: u32 = backup_connection
                .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                    row.get(0)
                })
                .unwrap();
            assert_eq!(count, 1, "backup row count for {table}");
        }
        drop(backup_connection);

        {
            let connection = store.lock_connection().unwrap();
            assert_eq!(read_database_id(&connection).unwrap(), database_id);
            for table in [
                "account_leases",
                "inbound_receipts",
                "inbound_checkpoints",
                "outbound_batches",
                "outbound_segments",
            ] {
                let count: u32 = connection
                    .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                        row.get(0)
                    })
                    .unwrap();
                assert_eq!(count, 1, "migrated row count for {table}");
            }
        }
        drop(store);

        let reopened = CoreStore::open(directory.path()).unwrap();
        assert_eq!(
            reopened.pre_migration_backup().unwrap(),
            Some(backup.clone())
        );
        let backup_files = fs::read_dir(directory.path().join(BACKUP_DIRECTORY_NAME))
            .unwrap()
            .filter_map(Result::ok)
            .filter(|entry| entry.path().extension().is_some_and(|ext| ext == "sqlite3"))
            .count();
        assert_eq!(backup_files, 3);
        let guard_backup = reopened.guard_migration_backup().unwrap().unwrap();
        assert_eq!(
            (guard_backup.from_version, guard_backup.to_version),
            (SCHEMA_V2, SCHEMA_V3)
        );
        assert_eq!(guard_backup.database_id, database_id);
    }

    #[test]
    fn online_backup_includes_rows_that_exist_only_in_the_live_wal() {
        let directory = tempfile::tempdir().unwrap();
        let database_id = create_schema_v1_fixture(directory.path());
        let database_path = directory.path().join(DATABASE_FILE_NAME);
        let wal_writer = Connection::open(&database_path).unwrap();
        wal_writer
            .pragma_update(None, "journal_mode", "WAL")
            .unwrap();
        wal_writer
            .pragma_update(None, "wal_autocheckpoint", 0)
            .unwrap();
        wal_writer
            .execute(
                "INSERT INTO account_leases
                 (account_id, owner_instance_id, owner_boot_id, fence_epoch,
                  lease_until_ms, status, last_observed_at_ms, updated_at_ms)
                 VALUES ('wal-only', 'fixture-instance', 'fixture-boot',
                         8, 9000, 'active', 110, 110)",
                [],
            )
            .unwrap();
        let wal_path = PathBuf::from(format!("{}-wal", database_path.display()));
        assert!(fs::metadata(&wal_path).unwrap().len() > 0);

        let main_file_only = directory.path().join("main-file-only.sqlite3");
        fs::copy(&database_path, &main_file_only).unwrap();
        let stale =
            Connection::open_with_flags(&main_file_only, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
        let stale_count: u32 = stale
            .query_row(
                "SELECT COUNT(*) FROM account_leases WHERE account_id = 'wal-only'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(stale_count, 0, "fixture row must not be in the main file");
        drop(stale);

        let store = CoreStore::open(directory.path()).unwrap();
        let backup = store.pre_migration_backup().unwrap().unwrap();
        assert_eq!(backup.database_id, database_id);
        let backup_connection = Connection::open_with_flags(
            directory.path().join(backup.relative_path),
            OpenFlags::SQLITE_OPEN_READ_ONLY,
        )
        .unwrap();
        let backup_count: u32 = backup_connection
            .query_row(
                "SELECT COUNT(*) FROM account_leases WHERE account_id = 'wal-only'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(backup_count, 1, "online backup must include live WAL rows");
        drop(backup_connection);
        drop(store);
        drop(wal_writer);
    }

    #[test]
    fn valid_backup_is_refreshed_when_v1_source_changes_after_a_failed_attempt() {
        let directory = tempfile::tempdir().unwrap();
        let database_id = create_schema_v1_fixture(directory.path());
        let database_path = directory.path().join(DATABASE_FILE_NAME);
        let source = Connection::open(&database_path).unwrap();

        let first = create_fresh_v1_backup(&source, directory.path(), database_id).unwrap();
        let first_backup = Connection::open_with_flags(
            directory.path().join(&first.relative_path),
            OpenFlags::SQLITE_OPEN_READ_ONLY,
        )
        .unwrap();
        let first_count: u32 = first_backup
            .query_row(
                "SELECT COUNT(*) FROM account_leases WHERE account_id = 'after-first-backup'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(first_count, 0);
        drop(first_backup);

        source
            .execute(
                "INSERT INTO account_leases
                 (account_id, owner_instance_id, owner_boot_id, fence_epoch,
                  lease_until_ms, status, last_observed_at_ms, updated_at_ms)
                 VALUES ('after-first-backup', 'fixture-instance', 'fixture-boot',
                         9, 9000, 'active', 120, 120)",
                [],
            )
            .unwrap();

        let refreshed = create_fresh_v1_backup(&source, directory.path(), database_id).unwrap();
        assert_eq!(refreshed.relative_path, first.relative_path);
        let refreshed_backup = Connection::open_with_flags(
            directory.path().join(&refreshed.relative_path),
            OpenFlags::SQLITE_OPEN_READ_ONLY,
        )
        .unwrap();
        let refreshed_count: u32 = refreshed_backup
            .query_row(
                "SELECT COUNT(*) FROM account_leases WHERE account_id = 'after-first-backup'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(
            refreshed_count, 1,
            "retry backup must include newer v1 rows"
        );
    }

    #[test]
    fn refresh_failure_keeps_the_previous_verified_v1_backup() {
        let directory = tempfile::tempdir().unwrap();
        let database_id = create_schema_v1_fixture(directory.path());
        let database_path = directory.path().join(DATABASE_FILE_NAME);
        let source = Connection::open(&database_path).unwrap();
        let first = create_fresh_v1_backup(&source, directory.path(), database_id).unwrap();
        let final_path = directory.path().join(&first.relative_path);
        let previous_bytes = fs::read(&final_path).unwrap();

        let backup_file_name = final_path.file_name().unwrap().to_str().unwrap();
        let blocked_temporary_path = directory
            .path()
            .join(BACKUP_DIRECTORY_NAME)
            .join(format!(".{backup_file_name}.tmp"));
        fs::create_dir(&blocked_temporary_path).unwrap();

        assert!(matches!(
            create_fresh_v1_backup(&source, directory.path(), database_id),
            Err(StoreError::UnsafeStoragePath { .. })
        ));
        assert_eq!(
            fs::read(&final_path).unwrap(),
            previous_bytes,
            "a failed refresh must not remove or replace the verified rollback point"
        );
        verify_v1_backup(&final_path, first.relative_path, database_id).unwrap();
    }

    #[test]
    fn failed_schema_migration_keeps_a_verified_v1_backup_and_v1_source() {
        let directory = tempfile::tempdir().unwrap();
        let database_id = create_schema_v1_fixture(directory.path());
        let database_path = directory.path().join(DATABASE_FILE_NAME);
        let connection = Connection::open(&database_path).unwrap();
        connection
            .execute("CREATE TABLE segment_manifests (collision INTEGER)", [])
            .unwrap();
        drop(connection);

        assert!(CoreStore::open(directory.path()).is_err());
        let source =
            Connection::open_with_flags(&database_path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
        assert_eq!(read_schema_version(&source).unwrap(), SCHEMA_V1);
        assert_eq!(read_database_id(&source).unwrap(), database_id);
        assert!(!table_exists(&source, "schema_migrations").unwrap());
        drop(source);

        let backup_name = format!("core-v{SCHEMA_V1}-to-v{SCHEMA_V2}-{database_id}.sqlite3");
        let relative_path = PathBuf::from(BACKUP_DIRECTORY_NAME).join(backup_name);
        let backup = verify_v1_backup(
            &directory.path().join(&relative_path),
            relative_path,
            database_id,
        )
        .unwrap();
        assert!(backup.integrity.is_valid());
        assert_eq!(backup.from_version, SCHEMA_V1);
    }

    #[test]
    fn invalid_existing_migration_backup_fails_closed_and_leaves_source_at_v1() {
        let directory = tempfile::tempdir().unwrap();
        let database_id = create_schema_v1_fixture(directory.path());
        let backup_directory = directory.path().join(BACKUP_DIRECTORY_NAME);
        fs::create_dir_all(&backup_directory).unwrap();
        let backup_path = backup_directory.join(format!(
            "core-v{SCHEMA_V1}-to-v{SCHEMA_V2}-{database_id}.sqlite3"
        ));
        fs::write(&backup_path, b"not a sqlite database").unwrap();

        assert!(CoreStore::open(directory.path()).is_err());
        let source = Connection::open_with_flags(
            directory.path().join(DATABASE_FILE_NAME),
            OpenFlags::SQLITE_OPEN_READ_ONLY,
        )
        .unwrap();
        assert_eq!(read_schema_version(&source).unwrap(), SCHEMA_V1);
        assert_eq!(read_database_id(&source).unwrap(), database_id);
        assert!(!table_exists(&source, "segment_manifests").unwrap());
    }

    #[cfg(unix)]
    #[test]
    fn storage_paths_are_private_and_direct_symlinks_fail_closed() {
        use std::os::unix::fs::{symlink, PermissionsExt};

        let directory = tempfile::tempdir().unwrap();
        let store = CoreStore::open(directory.path()).unwrap();
        for path in [
            store.database_path().to_owned(),
            directory.path().join(INITIALIZED_MARKER_FILE_NAME),
        ] {
            assert_eq!(
                fs::metadata(path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        assert_eq!(
            fs::metadata(directory.path()).unwrap().permissions().mode() & 0o777,
            0o700
        );
        drop(store);

        let migration_dir = tempfile::tempdir().unwrap();
        create_schema_v1_fixture(migration_dir.path());
        let migrated = CoreStore::open(migration_dir.path()).unwrap();
        let backup = migrated.pre_migration_backup().unwrap().unwrap();
        let backup_dir = migration_dir.path().join(BACKUP_DIRECTORY_NAME);
        assert_eq!(
            fs::metadata(&backup_dir).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(migration_dir.path().join(backup.relative_path))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        drop(migrated);

        let symlink_parent = tempfile::tempdir().unwrap();
        let real_dir = symlink_parent.path().join("real");
        fs::create_dir(&real_dir).unwrap();
        let linked_dir = symlink_parent.path().join("linked");
        symlink(&real_dir, &linked_dir).unwrap();
        assert!(matches!(
            CoreStore::open(&linked_dir),
            Err(StoreError::UnsafeStoragePath { .. })
        ));

        let database_link_dir = tempfile::tempdir().unwrap();
        let link_target = database_link_dir.path().join("target.sqlite3");
        fs::write(&link_target, b"not opened").unwrap();
        symlink(
            &link_target,
            database_link_dir.path().join(DATABASE_FILE_NAME),
        )
        .unwrap();
        assert!(matches!(
            CoreStore::open(database_link_dir.path()),
            Err(StoreError::UnsafeStoragePath { .. })
        ));

        let backup_link_dir = tempfile::tempdir().unwrap();
        let database_id = create_schema_v1_fixture(backup_link_dir.path());
        let backup_dir = backup_link_dir.path().join(BACKUP_DIRECTORY_NAME);
        fs::create_dir(&backup_dir).unwrap();
        let target = backup_link_dir.path().join("foreign.sqlite3");
        fs::write(&target, b"not opened").unwrap();
        symlink(
            target,
            backup_dir.join(format!(
                "core-v{SCHEMA_V1}-to-v{SCHEMA_V2}-{database_id}.sqlite3"
            )),
        )
        .unwrap();
        assert!(matches!(
            CoreStore::open(backup_link_dir.path()),
            Err(StoreError::UnsafeStoragePath { .. })
        ));
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

        let future_version_dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(future_version_dir.path()).unwrap();
        let database_path = store.database_path().to_owned();
        drop(store);
        let connection = Connection::open(&database_path).unwrap();
        connection
            .execute(
                "UPDATE meta SET value = '999' WHERE key = 'schema_version'",
                [],
            )
            .unwrap();
        drop(connection);
        assert!(matches!(
            CoreStore::open(future_version_dir.path()),
            Err(StoreError::UnsupportedSchema {
                found: 999,
                supported: CORE_SCHEMA_VERSION,
            })
        ));

        let drifted_v2_dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(drifted_v2_dir.path()).unwrap();
        let database_path = store.database_path().to_owned();
        drop(store);
        let connection = Connection::open(&database_path).unwrap();
        connection
            .execute("ALTER TABLE segment_manifests ADD COLUMN surprise TEXT", [])
            .unwrap();
        drop(connection);
        assert!(matches!(
            CoreStore::open(drifted_v2_dir.path()),
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
    fn segment_manifest_commit_is_strict_idempotent_and_filterable() {
        const CHAT_ID: &str = "00000000-0000-4000-8000-000000000001";
        const DEBUG_ID: &str = "00000000-0000-4000-8000-000000000002";
        const CONFLICT_ID: &str = "00000000-0000-4000-8000-000000000003";
        const INVALID_ID: &str = "00000000-0000-4000-8000-000000000004";
        let (_directory, store) = store();
        assert_eq!(store.storage_cleanup_state().unwrap(), None);
        store
            .record_storage_cleanup_state(150, DiskPressure::High)
            .unwrap();
        assert_eq!(
            store.storage_cleanup_state().unwrap(),
            Some(StorageCleanupState {
                last_cleanup_at_ms: 150,
                last_pressure: DiskPressure::High,
            })
        );
        let chat = manifest(CHAT_ID, SegmentFamily::Chat, 200);
        let debug = manifest(DEBUG_ID, SegmentFamily::Debug, 300);

        store.commit_segment_manifest(&chat).unwrap();
        store.commit_segment_manifest(&chat).unwrap();
        SegmentCatalog::commit_manifest(&store, &debug).unwrap();
        assert_eq!(store.segment_manifest(CHAT_ID).unwrap(), Some(chat.clone()));
        assert_eq!(
            store
                .segment_manifests(Some(SegmentFamily::Chat), Some(250))
                .unwrap(),
            vec![chat.clone()]
        );
        assert_eq!(
            SegmentCatalog::list_manifests(&store).unwrap(),
            vec![chat.clone(), debug.clone()]
        );

        let mut conflicting_id = chat.clone();
        conflicting_id.record_count = 3;
        assert!(matches!(
            store.commit_segment_manifest(&conflicting_id),
            Err(StoreError::IdempotencyConflict {
                entity: "segment manifest",
                ..
            })
        ));
        let mut conflicting_path = chat.clone();
        conflicting_path.segment_id = CONFLICT_ID.to_owned();
        assert!(matches!(
            store.commit_segment_manifest(&conflicting_path),
            Err(StoreError::InvalidSegmentManifest(_))
        ));

        for bad_path in ["/absolute.segment", "../escape.segment", "segments/../x"] {
            let mut invalid = manifest(INVALID_ID, SegmentFamily::Audit, 400);
            invalid.relative_path = bad_path.to_owned();
            assert!(matches!(
                store.commit_segment_manifest(&invalid),
                Err(StoreError::InvalidSegmentManifest(_))
            ));
        }
        let mut invalid_digest = manifest(INVALID_ID, SegmentFamily::Audit, 400);
        invalid_digest.content_sha256 = "A".repeat(64);
        assert!(matches!(
            store.commit_segment_manifest(&invalid_digest),
            Err(StoreError::InvalidInput(_))
        ));
    }

    #[test]
    fn deletion_journal_survives_reopen_and_completion_is_atomic_and_idempotent() {
        const DELETE_ID: &str = "00000000-0000-4000-8000-000000000011";
        const KEEP_ID: &str = "00000000-0000-4000-8000-000000000012";
        let (directory, store) = store();
        let first = manifest(DELETE_ID, SegmentFamily::Debug, 500);
        let without_intent = manifest(KEEP_ID, SegmentFamily::Chat, 510);
        store.commit_segment_manifest(&first).unwrap();
        store.commit_segment_manifest(&without_intent).unwrap();
        store
            .mark_segment_deleting_at(&first.segment_id, 600)
            .unwrap();
        store
            .mark_segment_deleting_at(&first.segment_id, 700)
            .unwrap();
        assert_eq!(
            store.pending_segment_deletions().unwrap(),
            vec![PendingSegmentDeletion {
                segment_id: first.segment_id.clone(),
            }]
        );
        assert_eq!(
            store.segment_manifest(&first.segment_id).unwrap(),
            Some(first.clone())
        );
        drop(store);

        let reopened = CoreStore::open(directory.path()).unwrap();
        assert_eq!(
            SegmentCatalog::list_pending_deletions(&reopened).unwrap(),
            vec![PendingSegmentDeletion {
                segment_id: first.segment_id.clone(),
            }]
        );
        assert!(matches!(
            reopened.complete_segment_deletion(&without_intent.segment_id),
            Err(StoreError::InvalidTransition { .. })
        ));
        SegmentCatalog::finish_delete(&reopened, &first.segment_id).unwrap();
        SegmentCatalog::finish_delete(&reopened, &first.segment_id).unwrap();
        assert_eq!(reopened.segment_manifest(&first.segment_id).unwrap(), None);
        assert!(reopened.pending_segment_deletions().unwrap().is_empty());
        assert_eq!(
            reopened
                .segment_manifest(&without_intent.segment_id)
                .unwrap(),
            Some(without_intent)
        );
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
    fn invalidation_retires_expired_epoch_idempotently_without_touching_new_owner() {
        let (_directory, store) = store();
        let first = lease(&store);
        // Fixture deadline is long in the past relative to the system clock.
        assert!(store.invalidate_account_lease(&first.token()).unwrap());
        assert!(!store.invalidate_account_lease(&first.token()).unwrap());
        assert!(matches!(
            store.install_verified_account_lease_at(ACCOUNT, INSTANCE, BOOT, EPOCH, 200, 20_000),
            Err(StoreError::LeaseReleased { .. })
        ));
        let new = store
            .install_verified_account_lease_at(
                ACCOUNT,
                "new-instance",
                "new-boot",
                EPOCH + 1,
                200,
                20_000,
            )
            .unwrap();
        assert!(!store.invalidate_account_lease(&first.token()).unwrap());
        assert!(store
            .pending_inbound_receipts_at(&new.token(), 201, 1)
            .is_ok());
    }

    #[test]
    fn preparation_rejection_preserves_zero_attempts_and_durable_lookup() {
        let (_directory, store) = store();
        let token = lease(&store).token();
        let batch = store
            .prepare_outbound_batch_at(
                &token,
                "manual-preflight",
                "route",
                &[OutboundSegmentDraft::text("synthetic")],
                200,
            )
            .unwrap();
        let rejected = store
            .transition_segment_at(
                &token,
                &batch.segments[0].id,
                SegmentTransition::CancelPrepared {
                    reason: "preparation failed before network".into(),
                },
                201,
            )
            .unwrap();
        assert_eq!(rejected.batch.segments[0].attempt_count, 0);
        assert_eq!(rejected.batch.segments[0].status, SegmentStatus::Rejected);
        assert_eq!(
            store
                .outbound_batch_for_trigger(ACCOUNT, "manual-preflight")
                .unwrap()
                .unwrap()
                .id,
            batch.id
        );
        assert!(store
            .outbound_batch_for_trigger("other-account", "manual-preflight")
            .unwrap()
            .is_none());
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

#[cfg(test)]
#[path = "store_inbound_claim_tests.rs"]
mod inbound_claim_tests;

#[cfg(test)]
#[path = "store_guard_tests.rs"]
mod guard_tests;
