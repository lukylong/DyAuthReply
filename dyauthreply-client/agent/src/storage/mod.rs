//! Bounded rolling projection storage for chat, audit, and debug records.
//!
//! The correctness database remains authoritative.  This module owns only
//! disposable projection bodies and deliberately has no network or credential
//! dependencies.

mod segment;

pub mod retention;

use std::{fmt, str::FromStr};

use serde::{Deserialize, Serialize};
use thiserror::Error;

pub(crate) use segment::validate_manifest_identity;
pub use segment::{
    AppendOutcome, CompressionCodec, RecoveryReport, SegmentStore, SkipReason, ZstdCodec,
};

/// Maximum payload accepted by any single segment frame (16 MiB).
pub const MAX_RECORD_BYTES_HARD: u64 = 16 * 1024 * 1024;
/// Maximum configured target size for one uncompressed segment (256 MiB).
pub const MAX_TARGET_SEGMENT_BYTES_HARD: u64 = 256 * 1024 * 1024;
/// Maximum aggregate budget accepted for one family (16 TiB).
pub const MAX_FAMILY_BYTES_HARD: u64 = 16 * 1024 * 1024 * 1024 * 1024;

pub(crate) const SEGMENT_HEADER_BYTES: u64 = 36;
pub(crate) const SEGMENT_HEADER_LEN: usize = 36;
// v2 frame: payload length + one's-complement length + payload digest +
// trailing commit marker.  The redundant length prevents a corrupted committed
// last frame from being mistaken for a torn append and silently truncated.
pub(crate) const FRAME_OVERHEAD_BYTES: u64 = 48;

/// Independent rolling-file families.  The ordering is stable but is not the
/// disk-pressure reclamation order (see [`retention`]).
#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SegmentFamily {
    Chat,
    Audit,
    Debug,
}

impl SegmentFamily {
    /// Returns the durable lowercase representation.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Chat => "chat",
            Self::Audit => "audit",
            Self::Debug => "debug",
        }
    }

    /// Returns every family exactly once.
    #[must_use]
    pub const fn all() -> [Self; 3] {
        [Self::Chat, Self::Audit, Self::Debug]
    }

    pub(crate) const fn header_tag(self) -> u8 {
        match self {
            Self::Chat => 1,
            Self::Audit => 2,
            Self::Debug => 3,
        }
    }

    pub(crate) fn from_header_tag(tag: u8) -> Result<Self, StorageError> {
        match tag {
            1 => Ok(Self::Chat),
            2 => Ok(Self::Audit),
            3 => Ok(Self::Debug),
            _ => Err(StorageError::Integrity(format!(
                "unknown segment family tag {tag}"
            ))),
        }
    }
}

impl fmt::Display for SegmentFamily {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for SegmentFamily {
    type Err = StorageError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "chat" => Ok(Self::Chat),
            "audit" => Ok(Self::Audit),
            "debug" => Ok(Self::Debug),
            _ => Err(StorageError::Integrity(format!(
                "unknown segment family {value:?}"
            ))),
        }
    }
}

/// Validated storage policy for one projection family.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SegmentPolicy {
    pub retention_age_ms: u64,
    pub max_total_bytes: u64,
    pub target_segment_bytes: u64,
    pub max_record_bytes: u64,
    pub minimum_segments: usize,
    pub persist: bool,
    pub compress: bool,
}

impl SegmentPolicy {
    fn validate(&self, family: SegmentFamily) -> Result<(), StorageError> {
        if self.retention_age_ms == 0 {
            return Err(StorageError::InvalidPolicy {
                family,
                reason: "retention_age_ms must be greater than zero".to_owned(),
            });
        }
        if self.max_record_bytes == 0 || self.max_record_bytes > MAX_RECORD_BYTES_HARD {
            return Err(StorageError::InvalidPolicy {
                family,
                reason: format!("max_record_bytes must be in 1..={MAX_RECORD_BYTES_HARD}"),
            });
        }
        let minimum_target = SEGMENT_HEADER_BYTES
            .checked_add(FRAME_OVERHEAD_BYTES)
            .and_then(|value| value.checked_add(self.max_record_bytes))
            .ok_or_else(|| StorageError::InvalidPolicy {
                family,
                reason: "target size arithmetic overflow".to_owned(),
            })?;
        if self.target_segment_bytes < minimum_target
            || self.target_segment_bytes > MAX_TARGET_SEGMENT_BYTES_HARD
        {
            return Err(StorageError::InvalidPolicy {
                family,
                reason: format!(
                    "target_segment_bytes must be in {minimum_target}..={MAX_TARGET_SEGMENT_BYTES_HARD}"
                ),
            });
        }
        if self.max_total_bytes < self.target_segment_bytes
            || self.max_total_bytes > MAX_FAMILY_BYTES_HARD
        {
            return Err(StorageError::InvalidPolicy {
                family,
                reason: format!(
                    "max_total_bytes must be in {}..={MAX_FAMILY_BYTES_HARD}",
                    self.target_segment_bytes
                ),
            });
        }
        if self.minimum_segments > 10_000 {
            return Err(StorageError::InvalidPolicy {
                family,
                reason: "minimum_segments must not exceed 10000".to_owned(),
            });
        }
        let minimum_retained_bytes = self
            .target_segment_bytes
            .checked_mul(u64::try_from(self.minimum_segments).map_err(|_| {
                StorageError::InvalidPolicy {
                    family,
                    reason: "minimum_segments cannot fit the byte budget".to_owned(),
                }
            })?)
            .ok_or_else(|| StorageError::InvalidPolicy {
                family,
                reason: "minimum retained byte budget overflows".to_owned(),
            })?;
        if minimum_retained_bytes > self.max_total_bytes {
            return Err(StorageError::InvalidPolicy {
                family,
                reason: format!(
                    "minimum_segments * target_segment_bytes ({minimum_retained_bytes}) exceeds max_total_bytes ({})",
                    self.max_total_bytes
                ),
            });
        }
        Ok(())
    }
}

/// Complete validated policy set.  Construction guarantees that all three
/// families have independently valid bounds.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SegmentPolicies {
    chat: SegmentPolicy,
    audit: SegmentPolicy,
    debug: SegmentPolicy,
}

impl SegmentPolicies {
    /// Validates and constructs a complete policy set.
    ///
    /// # Errors
    ///
    /// Returns [`StorageError::InvalidPolicy`] for a zero/oversized bound or a
    /// target incapable of containing the configured maximum record.
    pub fn new(
        chat: SegmentPolicy,
        audit: SegmentPolicy,
        debug: SegmentPolicy,
    ) -> Result<Self, StorageError> {
        chat.validate(SegmentFamily::Chat)?;
        audit.validate(SegmentFamily::Audit)?;
        debug.validate(SegmentFamily::Debug)?;
        Ok(Self { chat, audit, debug })
    }

    /// Returns the policy for `family`.
    #[must_use]
    pub const fn get(&self, family: SegmentFamily) -> &SegmentPolicy {
        match family {
            SegmentFamily::Chat => &self.chat,
            SegmentFamily::Audit => &self.audit,
            SegmentFamily::Debug => &self.debug,
        }
    }
}

/// Durable metadata for one sealed segment.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SegmentManifest {
    pub segment_id: String,
    pub family: SegmentFamily,
    pub relative_path: String,
    pub record_count: u64,
    pub uncompressed_bytes: u64,
    pub stored_bytes: u64,
    pub content_sha256: String,
    pub file_sha256: String,
    pub created_at_ms: i64,
    pub sealed_at_ms: i64,
    pub compression: Option<String>,
}

/// Durable cleanup-journal item returned by the catalog.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct PendingSegmentDeletion {
    pub segment_id: String,
}

/// Error boundary used by a durable manifest catalog implementation.
#[derive(Clone, Debug, Eq, Error, PartialEq)]
#[error("{message}")]
pub struct CatalogError {
    message: String,
}

impl CatalogError {
    /// Creates a catalog error without leaking implementation-specific types
    /// through the storage boundary.
    #[must_use]
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

/// Transactional manifest and deletion-journal boundary implemented by the
/// correctness store.
pub trait SegmentCatalog: Send + Sync {
    /// Returns every sealed segment manifest.
    ///
    /// # Errors
    ///
    /// Returns an error when durable catalog state cannot be read or decoded.
    fn list_manifests(&self) -> Result<Vec<SegmentManifest>, CatalogError>;

    /// Commits an adopted or newly sealed manifest.  Equal duplicate IDs must
    /// be idempotent; a different value for an existing ID must fail.
    ///
    /// # Errors
    ///
    /// Returns an error when the transaction fails or an ID conflicts.
    fn commit_manifest(&self, manifest: &SegmentManifest) -> Result<(), CatalogError>;

    /// Durably records deletion intent without removing the manifest.
    ///
    /// # Errors
    ///
    /// Returns an error when the manifest is missing or intent cannot be saved.
    fn begin_delete(&self, segment_id: &str) -> Result<(), CatalogError>;

    /// Returns every incomplete deletion intent.
    ///
    /// # Errors
    ///
    /// Returns an error when the cleanup journal cannot be read or decoded.
    fn list_pending_deletions(&self) -> Result<Vec<PendingSegmentDeletion>, CatalogError>;

    /// Atomically removes the manifest and its deletion intent.  Repeating a
    /// completed deletion must be harmless.
    ///
    /// # Errors
    ///
    /// Returns an error when the completion transaction cannot be committed.
    fn finish_delete(&self, segment_id: &str) -> Result<(), CatalogError>;
}

/// Rolling-storage failure.  Integrity failures are fail-closed and require a
/// restart/recovery rather than continuing with an uncertain writer.
#[derive(Debug, Error)]
pub enum StorageError {
    #[error("storage I/O failed: {0}")]
    Io(#[from] std::io::Error),
    #[error("segment catalog failed: {0}")]
    Catalog(#[from] CatalogError),
    #[error("invalid {family} policy: {reason}")]
    InvalidPolicy {
        family: SegmentFamily,
        reason: String,
    },
    #[error("{family} record is {actual} bytes; configured maximum is {maximum}")]
    RecordTooLarge {
        family: SegmentFamily,
        actual: u64,
        maximum: u64,
    },
    #[error("compression is enabled for {family}, but no codec was supplied")]
    CompressionUnavailable { family: SegmentFamily },
    #[error("segment codec failed: {0}")]
    Codec(String),
    #[error("segment integrity failure: {0}")]
    Integrity(String),
    #[error(
        "segment store is poisoned after an uncertain write; reopen it before mutating storage"
    )]
    Poisoned,
    #[error("storage clock moved backwards from {previous_ms} to {now_ms}")]
    ClockRegression { previous_ms: i64, now_ms: i64 },
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> SegmentPolicy {
        SegmentPolicy {
            retention_age_ms: 86_400_000,
            max_total_bytes: 1_048_576,
            target_segment_bytes: 4096,
            max_record_bytes: 1024,
            minimum_segments: 1,
            persist: true,
            compress: false,
        }
    }

    #[test]
    fn policy_set_requires_a_bounded_record_and_segment() {
        let valid = policy();
        assert!(SegmentPolicies::new(valid.clone(), valid.clone(), valid.clone()).is_ok());

        let mut oversized_record = valid.clone();
        oversized_record.max_record_bytes = MAX_RECORD_BYTES_HARD + 1;
        assert!(matches!(
            SegmentPolicies::new(oversized_record, valid.clone(), valid.clone()),
            Err(StorageError::InvalidPolicy {
                family: SegmentFamily::Chat,
                ..
            })
        ));

        let valid = policy();
        let mut impossible_minimum = valid.clone();
        impossible_minimum.minimum_segments = 300;
        assert!(matches!(
            SegmentPolicies::new(impossible_minimum, valid.clone(), valid.clone()),
            Err(StorageError::InvalidPolicy {
                family: SegmentFamily::Chat,
                ..
            })
        ));

        let mut undersized_target = valid.clone();
        undersized_target.target_segment_bytes =
            SEGMENT_HEADER_BYTES + FRAME_OVERHEAD_BYTES + valid.max_record_bytes - 1;
        assert!(matches!(
            SegmentPolicies::new(valid.clone(), undersized_target, valid),
            Err(StorageError::InvalidPolicy {
                family: SegmentFamily::Audit,
                ..
            })
        ));
    }

    #[test]
    fn family_wire_values_are_stable_and_round_trip() {
        for family in SegmentFamily::all() {
            assert_eq!(family.as_str().parse::<SegmentFamily>().unwrap(), family);
            assert_eq!(
                SegmentFamily::from_header_tag(family.header_tag()).unwrap(),
                family
            );
        }
    }
}
