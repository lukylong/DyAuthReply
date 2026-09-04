use std::{
    collections::{BTreeMap, HashMap, HashSet},
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Component, Path, PathBuf},
    sync::Arc,
};

use sha2::{Digest, Sha256};
use uuid::Uuid;

#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

use super::{
    retention::CleanupPlan, PendingSegmentDeletion, SegmentCatalog, SegmentFamily, SegmentManifest,
    SegmentPolicies, SegmentPolicy, StorageError, FRAME_OVERHEAD_BYTES, MAX_RECORD_BYTES_HARD,
    SEGMENT_HEADER_BYTES, SEGMENT_HEADER_LEN,
};

const SEGMENT_MAGIC: &[u8; 8] = b"DYSEG001";
const SEGMENT_FORMAT_VERSION: u16 = 2;
const FRAME_COMMIT_MARKER: &[u8; 8] = b"DYFRM002";
const MILLIS_PER_DAY: i64 = 86_400_000;
const IO_BUFFER_BYTES: usize = 64 * 1024;

/// Streaming codec boundary for optional segment compression.  A codec must be
/// deterministic and must not buffer the complete segment in memory.
pub trait CompressionCodec: Send + Sync {
    /// Durable codec identifier stored in the manifest. Schema v2 supports the
    /// canonical `zstd` identifier only.
    fn name(&self) -> &'static str;

    /// Streams an uncompressed segment into its encoded representation.
    ///
    /// # Errors
    ///
    /// Returns a non-secret diagnostic when streaming or encoding fails.
    fn encode(&self, input: &mut dyn Read, output: &mut dyn Write) -> Result<(), String>;

    /// Streams an encoded segment back to its uncompressed representation.
    ///
    /// # Errors
    ///
    /// Returns a non-secret diagnostic when streaming or decoding fails.
    fn decode(&self, input: &mut dyn Read, output: &mut dyn Write) -> Result<(), String>;
}

/// Built-in streaming Zstandard codec used by production startup. Keeping the
/// decoder available even when new compression is disabled allows existing
/// `.segment.zst` files to remain readable after a policy change.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ZstdCodec {
    level: i32,
}

impl ZstdCodec {
    /// Creates a codec with a caller-selected Zstandard level.
    #[must_use]
    pub const fn new(level: i32) -> Self {
        Self { level }
    }
}

impl Default for ZstdCodec {
    fn default() -> Self {
        Self::new(3)
    }
}

impl CompressionCodec for ZstdCodec {
    fn name(&self) -> &'static str {
        "zstd"
    }

    fn encode(&self, input: &mut dyn Read, output: &mut dyn Write) -> Result<(), String> {
        zstd::stream::copy_encode(input, output, self.level).map_err(|error| error.to_string())
    }

    fn decode(&self, input: &mut dyn Read, output: &mut dyn Write) -> Result<(), String> {
        zstd::stream::copy_decode(input, output).map_err(|error| error.to_string())
    }
}

struct LimitedWriter<'a> {
    output: &'a mut dyn Write,
    remaining: u64,
}

impl Write for LimitedWriter<'_> {
    fn write(&mut self, buffer: &[u8]) -> std::io::Result<usize> {
        let requested = u64::try_from(buffer.len()).map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "decoded buffer length cannot fit storage limit",
            )
        })?;
        if requested > self.remaining {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "decoded segment exceeds configured uncompressed size limit",
            ));
        }
        let written = self.output.write(buffer)?;
        let written = u64::try_from(written).map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "decoded write length cannot fit storage limit",
            )
        })?;
        self.remaining = self.remaining.saturating_sub(written);
        usize::try_from(written).map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "decoded write length cannot fit platform size",
            )
        })
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.output.flush()
    }
}

type RecordVisitor<'a> = dyn FnMut(&[u8]) -> Result<(), StorageError> + 'a;

/// Why an append intentionally wrote no projection body.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SkipReason {
    PersistenceDisabled,
    CriticalDiskPressure,
}

/// Result of one bounded append.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum AppendOutcome {
    Stored {
        segment_id: String,
        record_index: u64,
        rotated_segment_id: Option<String>,
    },
    Skipped(SkipReason),
}

/// Observable startup repairs.  Every counter is bounded by files already on
/// disk; no record bodies are retained in the report.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct RecoveryReport {
    pub adopted_segments: usize,
    pub truncated_active_tails: usize,
    pub discarded_partial_active_headers: usize,
    pub completed_deletions: usize,
    pub removed_staging_files: usize,
    pub removed_duplicate_active_files: usize,
}

#[derive(Debug)]
struct ActiveWriter {
    segment_id: String,
    family: SegmentFamily,
    created_at_ms: i64,
    path: PathBuf,
    file: File,
    bytes: u64,
    record_count: u64,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct NameParts {
    segment_id: String,
    family: SegmentFamily,
    created_at_ms: i64,
    sealed_at_ms: Option<i64>,
    compressed: bool,
}

#[derive(Clone, Debug)]
struct RawScan {
    segment_id: String,
    family: SegmentFamily,
    created_at_ms: i64,
    record_count: u64,
    bytes: u64,
    sha256: String,
    truncated_tail: bool,
}

#[derive(Clone, Debug)]
struct InspectedSegment {
    manifest: SegmentManifest,
}

/// Crash-safe rolling projection store.  The supplied root must be a dedicated
/// segment directory; unexpected entries are reported as integrity failures.
pub struct SegmentStore {
    root: PathBuf,
    policies: SegmentPolicies,
    catalog: Arc<dyn SegmentCatalog>,
    codec: Option<Arc<dyn CompressionCodec>>,
    writers: BTreeMap<SegmentFamily, ActiveWriter>,
    recovery_report: RecoveryReport,
    poisoned: bool,
}

impl SegmentStore {
    /// Opens and recovers an uncompressed segment store.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid policy/paths, catalog failures, corruption,
    /// manifest drift, or compressed data without a codec.
    pub fn open(
        root: impl Into<PathBuf>,
        policies: SegmentPolicies,
        catalog: Arc<dyn SegmentCatalog>,
    ) -> Result<Self, StorageError> {
        Self::open_internal(root.into(), policies, catalog, None)
    }

    /// Opens and recovers a store with an injected streaming compression codec.
    ///
    /// # Errors
    ///
    /// Returns an error for invalid policy/codec metadata, catalog failures,
    /// corruption, or manifest/file drift.
    pub fn open_with_codec(
        root: impl Into<PathBuf>,
        policies: SegmentPolicies,
        catalog: Arc<dyn SegmentCatalog>,
        codec: Arc<dyn CompressionCodec>,
    ) -> Result<Self, StorageError> {
        Self::open_internal(root.into(), policies, catalog, Some(codec))
    }

    fn open_internal(
        root: PathBuf,
        policies: SegmentPolicies,
        catalog: Arc<dyn SegmentCatalog>,
        codec: Option<Arc<dyn CompressionCodec>>,
    ) -> Result<Self, StorageError> {
        if root.as_os_str().is_empty() {
            return Err(StorageError::Integrity(
                "segment root must not be empty".to_owned(),
            ));
        }
        if let Some(codec) = codec.as_ref() {
            validate_codec_name(codec.name())?;
        }
        for family in SegmentFamily::all() {
            if policies.get(family).persist && policies.get(family).compress && codec.is_none() {
                return Err(StorageError::CompressionUnavailable { family });
            }
        }

        prepare_private_directory(&root)?;

        let mut store = Self {
            root,
            policies,
            catalog,
            codec,
            writers: BTreeMap::new(),
            recovery_report: RecoveryReport::default(),
            poisoned: false,
        };
        store.recover()?;
        Ok(store)
    }

    /// Returns the dedicated segment root.
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Returns startup repair/adoption counters.
    #[must_use]
    pub const fn recovery_report(&self) -> &RecoveryReport {
        &self.recovery_report
    }

    /// Returns the bounded number of active writers (never greater than three).
    #[must_use]
    pub fn active_writer_count(&self) -> usize {
        self.writers.len()
    }

    /// Appends one arbitrary byte record and synchronizes it before returning.
    /// `allow_disposable_writes=false` implements the critical-pressure gate.
    ///
    /// # Errors
    ///
    /// Returns an error for an oversized record, clock regression, I/O failure,
    /// codec failure, or catalog failure while rotating.
    pub fn append(
        &mut self,
        family: SegmentFamily,
        record: &[u8],
        now_ms: i64,
        allow_disposable_writes: bool,
    ) -> Result<AppendOutcome, StorageError> {
        if self.poisoned {
            return Err(StorageError::Poisoned);
        }
        let policy = self.policies.get(family);
        if !policy.persist {
            return Ok(AppendOutcome::Skipped(SkipReason::PersistenceDisabled));
        }
        if !allow_disposable_writes {
            return Ok(AppendOutcome::Skipped(SkipReason::CriticalDiskPressure));
        }
        if now_ms < 0 {
            return Err(StorageError::Integrity(
                "append time must not be negative".to_owned(),
            ));
        }
        let record_bytes = u64::try_from(record.len())
            .map_err(|_| StorageError::Integrity("record length cannot fit u64".to_owned()))?;
        if record_bytes > policy.max_record_bytes {
            return Err(StorageError::RecordTooLarge {
                family,
                actual: record_bytes,
                maximum: policy.max_record_bytes,
            });
        }

        let frame_bytes = FRAME_OVERHEAD_BYTES
            .checked_add(record_bytes)
            .ok_or_else(|| StorageError::Integrity("frame length overflow".to_owned()))?;
        let should_rotate = if let Some(writer) = self.writers.get(&family) {
            if now_ms < writer.created_at_ms {
                return Err(StorageError::ClockRegression {
                    previous_ms: writer.created_at_ms,
                    now_ms,
                });
            }
            let date_changed = logical_day(writer.created_at_ms) != logical_day(now_ms);
            let next_bytes = writer
                .bytes
                .checked_add(frame_bytes)
                .ok_or_else(|| StorageError::Integrity("segment size overflow".to_owned()))?;
            writer.record_count > 0 && (date_changed || next_bytes > policy.target_segment_bytes)
        } else {
            false
        };
        let rotated_segment_id = if should_rotate {
            self.seal_family(family, now_ms)?
                .map(|manifest| manifest.segment_id)
        } else {
            None
        };

        if !self.writers.contains_key(&family) {
            let writer = match create_active_writer(&self.root, family, now_ms) {
                Ok(writer) => writer,
                Err(error) => {
                    self.poisoned = true;
                    return Err(error);
                }
            };
            self.writers.insert(family, writer);
        }
        let writer = self.writers.get_mut(&family).ok_or_else(|| {
            StorageError::Integrity(format!("missing {family} writer after creation"))
        })?;
        let record_index = writer.record_count;
        let next_bytes = writer
            .bytes
            .checked_add(frame_bytes)
            .ok_or_else(|| StorageError::Integrity("writer size overflow".to_owned()))?;
        let next_record_count = writer
            .record_count
            .checked_add(1)
            .ok_or_else(|| StorageError::Integrity("record count overflow".to_owned()))?;
        if let Err(error) = append_frame(&mut writer.file, record)
            .and_then(|()| writer.file.sync_data().map_err(StorageError::Io))
        {
            self.poisoned = true;
            return Err(error);
        }
        writer.bytes = next_bytes;
        writer.record_count = next_record_count;

        Ok(AppendOutcome::Stored {
            segment_id: writer.segment_id.clone(),
            record_index,
            rotated_segment_id,
        })
    }

    /// Seals the active family, atomically publishes it, then commits its
    /// manifest.  An empty active file is removed instead of manifested.
    ///
    /// # Errors
    ///
    /// Returns an error for clock regression, on-disk writer drift, I/O/codec
    /// failure, or catalog failure.  Callers should fail-stop and reopen after
    /// an error because the crash-safe on-disk state may require adoption.
    pub fn seal_family(
        &mut self,
        family: SegmentFamily,
        sealed_at_ms: i64,
    ) -> Result<Option<SegmentManifest>, StorageError> {
        if self.poisoned {
            return Err(StorageError::Poisoned);
        }
        if let Some(writer) = self.writers.get(&family) {
            if sealed_at_ms < writer.created_at_ms {
                return Err(StorageError::ClockRegression {
                    previous_ms: writer.created_at_ms,
                    now_ms: sealed_at_ms,
                });
            }
        }
        let result = self.seal_family_inner(family, sealed_at_ms);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn seal_family_inner(
        &mut self,
        family: SegmentFamily,
        sealed_at_ms: i64,
    ) -> Result<Option<SegmentManifest>, StorageError> {
        let Some(writer) = self.writers.remove(&family) else {
            return Ok(None);
        };
        writer.file.sync_all()?;
        if writer.record_count == 0 {
            drop(writer.file);
            fs::remove_file(&writer.path)?;
            sync_directory(&self.root)?;
            return Ok(None);
        }

        let expected =
            scan_raw_segment(&writer.path, false, None, family, self.policies.get(family))?;
        validate_writer_scan(&writer, &expected)?;
        let compress = self.policies.get(family).compress;
        let final_name = sealed_file_name(
            family,
            writer.created_at_ms,
            &writer.segment_id,
            sealed_at_ms,
            compress,
        );
        let final_path = self.root.join(&final_name);
        if final_path.try_exists()? {
            return Err(StorageError::Integrity(format!(
                "sealed target already exists: {final_name}"
            )));
        }

        writer.file.sync_all()?;
        drop(writer.file);
        if compress {
            self.encode_and_publish(&writer.path, &final_path)?;
        } else {
            fs::rename(&writer.path, &final_path)?;
            sync_directory(&self.root)?;
        }

        let parts = NameParts {
            segment_id: writer.segment_id,
            family,
            created_at_ms: writer.created_at_ms,
            sealed_at_ms: Some(sealed_at_ms),
            compressed: compress,
        };
        let inspected = self.inspect_sealed(&final_path, &parts)?;
        self.catalog.commit_manifest(&inspected.manifest)?;

        if compress && writer.path.try_exists()? {
            fs::remove_file(&writer.path)?;
            sync_directory(&self.root)?;
        }
        Ok(Some(inspected.manifest))
    }

    /// Verifies a manifest/file pair and streams every record to `visitor`.
    /// At most one configured-hard-cap record is resident at a time.
    ///
    /// # Errors
    ///
    /// Returns an error before visiting records if the stored file metadata or
    /// digest differs from the manifest, or during visitation for corrupt frames
    /// and visitor failures.
    pub fn visit_records(
        &self,
        manifest: &SegmentManifest,
        visitor: &mut dyn FnMut(&[u8]) -> Result<(), StorageError>,
    ) -> Result<(), StorageError> {
        validate_manifest_identity(manifest)?;
        let path = self.root.join(&manifest.relative_path);
        let parts = parse_sealed_name(&manifest.relative_path)?;
        let inspected = self.inspect_sealed(&path, &parts)?;
        compare_manifest(manifest, &inspected.manifest)?;
        self.with_uncompressed_path(&path, parts.compressed, parts.family, |raw_path| {
            let scan = scan_raw_segment(
                raw_path,
                false,
                Some(visitor),
                parts.family,
                self.policies.get(parts.family),
            )?;
            validate_scan_name(&scan, &parts)?;
            Ok(())
        })
    }

    /// Executes a precomputed deletion plan using journal -> unlink/directory
    /// sync -> transactional catalog completion.
    ///
    /// # Errors
    ///
    /// Returns an error for a stale/drifted candidate, an active/non-segment
    /// path, I/O failure, or catalog failure.  Interrupted deletions are resumed
    /// automatically on the next open.
    pub fn apply_cleanup(&mut self, plan: &CleanupPlan) -> Result<usize, StorageError> {
        if self.poisoned {
            return Err(StorageError::Poisoned);
        }
        let current = self
            .catalog
            .list_manifests()?
            .into_iter()
            .map(|manifest| (manifest.segment_id.clone(), manifest))
            .collect::<HashMap<_, _>>();
        let mut seen = HashSet::new();
        let mut planned_by_family = BTreeMap::<SegmentFamily, usize>::new();
        let mut prepared_paths = Vec::with_capacity(plan.deletions.len());
        for candidate in &plan.deletions {
            let manifest = &candidate.manifest;
            if !seen.insert(manifest.segment_id.clone()) {
                return Err(StorageError::Integrity(format!(
                    "cleanup plan repeats segment {}",
                    manifest.segment_id
                )));
            }
            let durable = current.get(&manifest.segment_id).ok_or_else(|| {
                StorageError::Integrity(format!(
                    "cleanup plan references missing manifest {}",
                    manifest.segment_id
                ))
            })?;
            compare_manifest(manifest, durable)?;
            validate_manifest_identity(manifest)?;
            prepared_paths.push(self.validate_cleanup_file(manifest)?);
            *planned_by_family.entry(manifest.family).or_default() += 1;
        }
        for family in SegmentFamily::all() {
            let current_count = current
                .values()
                .filter(|manifest| manifest.family == family)
                .count();
            let planned_count = planned_by_family.get(&family).copied().unwrap_or_default();
            let minimum = self.policies.get(family).minimum_segments;
            let maximum_deletions = current_count.saturating_sub(minimum);
            if planned_count > maximum_deletions {
                return Err(StorageError::Integrity(format!(
                    "cleanup plan would reduce {family} below minimum segment count {minimum}"
                )));
            }
        }

        let mut completed = 0_usize;
        for (candidate, prepared_path) in plan.deletions.iter().zip(prepared_paths) {
            let manifest = &candidate.manifest;
            let durable = current.get(&manifest.segment_id).ok_or_else(|| {
                StorageError::Integrity(format!(
                    "cleanup plan references missing manifest {}",
                    manifest.segment_id
                ))
            })?;
            compare_manifest(manifest, durable)?;
            validate_manifest_identity(manifest)?;
            let path = self.validate_cleanup_file(manifest)?;
            if path != prepared_path {
                return Err(StorageError::Integrity(format!(
                    "cleanup candidate path changed for {}",
                    manifest.segment_id
                )));
            }

            if let Err(error) = self
                .catalog
                .begin_delete(&manifest.segment_id)
                .map_err(StorageError::Catalog)
                .and_then(|()| fs::remove_file(&path).map_err(StorageError::Io))
                .and_then(|()| sync_directory(&self.root))
                .and_then(|()| {
                    self.catalog
                        .finish_delete(&manifest.segment_id)
                        .map_err(StorageError::Catalog)
                })
            {
                self.poisoned = true;
                return Err(error);
            }
            completed = completed.saturating_add(1);
        }
        Ok(completed)
    }

    fn validate_cleanup_file(&self, manifest: &SegmentManifest) -> Result<PathBuf, StorageError> {
        let parts = parse_sealed_name(&manifest.relative_path)?;
        if parts.segment_id != manifest.segment_id {
            return Err(StorageError::Integrity(format!(
                "cleanup filename ID differs from manifest {}",
                manifest.segment_id
            )));
        }
        let path = self.root.join(&manifest.relative_path);
        reject_symlink_or_non_file(&path)?;
        let metadata = fs::metadata(&path)?;
        let digest = sha256_file(&path)?;
        if metadata.len() != manifest.stored_bytes || digest != manifest.file_sha256 {
            return Err(StorageError::Integrity(format!(
                "cleanup candidate {} differs from its manifest",
                manifest.segment_id
            )));
        }
        Ok(path)
    }

    #[allow(clippy::too_many_lines)]
    fn recover(&mut self) -> Result<(), StorageError> {
        self.remove_staging_files()?;
        let mut manifests = catalog_manifest_map(self.catalog.as_ref())?;
        self.recover_pending_deletions(&mut manifests)?;

        let mut active_files = Vec::new();
        let mut sealed_files = Vec::new();
        for entry in fs::read_dir(&self.root)? {
            let entry = entry?;
            let file_type = entry.file_type()?;
            if !file_type.is_file() {
                return Err(StorageError::Integrity(format!(
                    "unexpected non-file entry in segment root: {}",
                    entry.path().display()
                )));
            }
            enforce_private_file_permissions(&entry.path())?;
            let name = entry.file_name().into_string().map_err(|_| {
                StorageError::Integrity("segment filename is not valid UTF-8".to_owned())
            })?;
            if is_staging_name(&name) {
                // A staging file created after the initial sweep is impossible
                // without another writer, so fail closed rather than racing it.
                return Err(StorageError::Integrity(format!(
                    "staging file appeared during recovery: {name}"
                )));
            }
            if name.ends_with(".active") {
                active_files.push((entry.path(), parse_active_name(&name)?));
            } else if name.ends_with(".segment") || name.ends_with(".segment.zst") {
                sealed_files.push((entry.path(), parse_sealed_name(&name)?));
            } else {
                return Err(StorageError::Integrity(format!(
                    "unexpected file in segment root: {name}"
                )));
            }
        }
        active_files.sort_by(|left, right| left.1.family.cmp(&right.1.family));
        sealed_files.sort_by(|left, right| left.1.segment_id.cmp(&right.1.segment_id));

        let mut seen_manifest_ids = HashSet::new();
        let mut sealed_by_id = HashMap::new();
        for (path, parts) in sealed_files {
            if sealed_by_id.contains_key(&parts.segment_id) {
                return Err(StorageError::Integrity(format!(
                    "multiple sealed files use segment ID {}",
                    parts.segment_id
                )));
            }
            let inspected = self.inspect_sealed(&path, &parts)?;
            if let Some(manifest) = manifests.get(&parts.segment_id) {
                compare_manifest(manifest, &inspected.manifest)?;
                seen_manifest_ids.insert(parts.segment_id.clone());
            } else {
                self.catalog.commit_manifest(&inspected.manifest)?;
                manifests.insert(parts.segment_id.clone(), inspected.manifest.clone());
                seen_manifest_ids.insert(parts.segment_id.clone());
                self.recovery_report.adopted_segments =
                    self.recovery_report.adopted_segments.saturating_add(1);
            }
            sealed_by_id.insert(parts.segment_id.clone(), inspected.manifest);
        }
        for segment_id in manifests.keys() {
            if !seen_manifest_ids.contains(segment_id) {
                return Err(StorageError::Integrity(format!(
                    "manifest {segment_id} has no sealed file"
                )));
            }
        }

        let mut seen_active_families = HashSet::new();
        for (path, parts) in active_files {
            if !seen_active_families.insert(parts.family) {
                return Err(StorageError::Integrity(format!(
                    "multiple active {} segments exist",
                    parts.family
                )));
            }
            let metadata = fs::metadata(&path)?;
            if metadata.len() < SEGMENT_HEADER_BYTES {
                fs::remove_file(&path)?;
                sync_directory(&self.root)?;
                self.recovery_report.discarded_partial_active_headers = self
                    .recovery_report
                    .discarded_partial_active_headers
                    .saturating_add(1);
                continue;
            }
            let scan = scan_raw_segment(
                &path,
                true,
                None,
                parts.family,
                self.policies.get(parts.family),
            )?;
            validate_scan_name(&scan, &parts)?;
            if scan.truncated_tail {
                self.recovery_report.truncated_active_tails = self
                    .recovery_report
                    .truncated_active_tails
                    .saturating_add(1);
            }
            if let Some(sealed) = sealed_by_id.get(&parts.segment_id) {
                if scan.sha256 != sealed.content_sha256
                    || scan.bytes != sealed.uncompressed_bytes
                    || scan.record_count != sealed.record_count
                {
                    return Err(StorageError::Integrity(format!(
                        "active/sealed duplicate {} has different content",
                        parts.segment_id
                    )));
                }
                fs::remove_file(&path)?;
                sync_directory(&self.root)?;
                self.recovery_report.removed_duplicate_active_files = self
                    .recovery_report
                    .removed_duplicate_active_files
                    .saturating_add(1);
                continue;
            }

            let file = OpenOptions::new().read(true).append(true).open(&path)?;
            self.writers.insert(
                parts.family,
                ActiveWriter {
                    segment_id: parts.segment_id,
                    family: parts.family,
                    created_at_ms: parts.created_at_ms,
                    path,
                    file,
                    bytes: scan.bytes,
                    record_count: scan.record_count,
                },
            );
        }
        Ok(())
    }

    fn recover_pending_deletions(
        &mut self,
        manifests: &mut HashMap<String, SegmentManifest>,
    ) -> Result<(), StorageError> {
        let pending = self.catalog.list_pending_deletions()?;
        let mut seen = HashSet::new();
        for PendingSegmentDeletion { segment_id } in pending {
            if !seen.insert(segment_id.clone()) {
                return Err(StorageError::Integrity(format!(
                    "duplicate pending deletion {segment_id}"
                )));
            }
            if let Some(manifest) = manifests.remove(&segment_id) {
                validate_relative_path(&manifest.relative_path)?;
                parse_sealed_name(&manifest.relative_path)?;
                let path = self.root.join(&manifest.relative_path);
                if path.try_exists()? {
                    reject_symlink_or_non_file(&path)?;
                    fs::remove_file(&path)?;
                    sync_directory(&self.root)?;
                }
            }
            self.catalog.finish_delete(&segment_id)?;
            self.recovery_report.completed_deletions =
                self.recovery_report.completed_deletions.saturating_add(1);
        }
        Ok(())
    }

    fn remove_staging_files(&mut self) -> Result<(), StorageError> {
        let mut removed = 0_usize;
        for entry in fs::read_dir(&self.root)? {
            let entry = entry?;
            let name = entry.file_name().into_string().map_err(|_| {
                StorageError::Integrity("segment filename is not valid UTF-8".to_owned())
            })?;
            if is_staging_name(&name) {
                if !entry.file_type()?.is_file() {
                    return Err(StorageError::Integrity(format!(
                        "staging entry is not a regular file: {name}"
                    )));
                }
                fs::remove_file(entry.path())?;
                removed = removed.saturating_add(1);
            }
        }
        if removed > 0 {
            sync_directory(&self.root)?;
        }
        self.recovery_report.removed_staging_files = removed;
        Ok(())
    }

    fn encode_and_publish(
        &self,
        active_path: &Path,
        final_path: &Path,
    ) -> Result<(), StorageError> {
        let codec = self
            .codec
            .as_ref()
            .ok_or(StorageError::CompressionUnavailable {
                family: parse_active_name(
                    active_path
                        .file_name()
                        .and_then(|name| name.to_str())
                        .ok_or_else(|| {
                            StorageError::Integrity("active filename is invalid".to_owned())
                        })?,
                )?
                .family,
            })?;
        let final_name = final_path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| StorageError::Integrity("sealed filename is invalid".to_owned()))?;
        let staging_path = self.root.join(format!("{final_name}.encode.tmp"));
        if staging_path.try_exists()? {
            return Err(StorageError::Integrity(format!(
                "compression staging file already exists: {}",
                staging_path.display()
            )));
        }
        let mut input = File::open(active_path)?;
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .private_mode()
            .open(&staging_path)?;
        if let Err(message) = codec.encode(&mut input, &mut output) {
            drop(output);
            let _ = fs::remove_file(&staging_path);
            return Err(StorageError::Codec(message));
        }
        output.flush()?;
        output.sync_all()?;
        drop(output);
        fs::rename(&staging_path, final_path)?;
        sync_directory(&self.root)?;
        Ok(())
    }

    fn inspect_sealed(
        &self,
        path: &Path,
        parts: &NameParts,
    ) -> Result<InspectedSegment, StorageError> {
        reject_symlink_or_non_file(path)?;
        let stored_bytes = fs::metadata(path)?.len();
        let file_sha256 = sha256_file(path)?;
        let scan =
            self.with_uncompressed_path(path, parts.compressed, parts.family, |raw_path| {
                scan_raw_segment(
                    raw_path,
                    false,
                    None,
                    parts.family,
                    self.policies.get(parts.family),
                )
            })?;
        validate_scan_name(&scan, parts)?;
        let relative_path = path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| StorageError::Integrity("sealed filename is invalid".to_owned()))?
            .to_owned();
        let sealed_at_ms = parts.sealed_at_ms.ok_or_else(|| {
            StorageError::Integrity("sealed filename has no sealed timestamp".to_owned())
        })?;
        let compression = if parts.compressed {
            Some(
                self.codec
                    .as_ref()
                    .ok_or(StorageError::CompressionUnavailable {
                        family: parts.family,
                    })?
                    .name()
                    .to_owned(),
            )
        } else {
            None
        };
        Ok(InspectedSegment {
            manifest: SegmentManifest {
                segment_id: parts.segment_id.clone(),
                family: parts.family,
                relative_path,
                record_count: scan.record_count,
                uncompressed_bytes: scan.bytes,
                stored_bytes,
                content_sha256: scan.sha256,
                file_sha256,
                created_at_ms: parts.created_at_ms,
                sealed_at_ms,
                compression,
            },
        })
    }

    fn with_uncompressed_path<T>(
        &self,
        path: &Path,
        compressed: bool,
        family: SegmentFamily,
        operation: impl FnOnce(&Path) -> Result<T, StorageError>,
    ) -> Result<T, StorageError> {
        if !compressed {
            return operation(path);
        }
        let codec = self.codec.as_ref().ok_or_else(|| {
            StorageError::Integrity("compressed segment requires a codec".to_owned())
        })?;
        let file_name = path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| StorageError::Integrity("compressed filename is invalid".to_owned()))?;
        let staging_path = self.root.join(format!("{file_name}.decode.tmp"));
        if staging_path.try_exists()? {
            return Err(StorageError::Integrity(format!(
                "decode staging file already exists: {}",
                staging_path.display()
            )));
        }
        let mut input = File::open(path)?;
        let mut output = OpenOptions::new()
            .create_new(true)
            .write(true)
            .private_mode()
            .open(&staging_path)?;
        let decode_result = {
            let mut limited_output = LimitedWriter {
                output: &mut output,
                remaining: self.policies.get(family).target_segment_bytes,
            };
            codec.decode(&mut input, &mut limited_output)
        };
        if let Err(message) = decode_result {
            drop(output);
            let _ = fs::remove_file(&staging_path);
            return Err(StorageError::Codec(message));
        }
        output.flush()?;
        drop(output);
        let result = operation(&staging_path);
        let remove_result = fs::remove_file(&staging_path);
        match (result, remove_result) {
            (Ok(value), Ok(())) => Ok(value),
            (Err(error), _) => Err(error),
            (Ok(_), Err(error)) => Err(StorageError::Io(error)),
        }
    }
}

fn create_active_writer(
    root: &Path,
    family: SegmentFamily,
    created_at_ms: i64,
) -> Result<ActiveWriter, StorageError> {
    for _ in 0..8 {
        let segment_id = Uuid::new_v4().to_string();
        let name = active_file_name(family, created_at_ms, &segment_id);
        let path = root.join(name);
        let file_result = OpenOptions::new()
            .create_new(true)
            .read(true)
            .write(true)
            .private_mode()
            .open(&path);
        let mut file = match file_result {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(StorageError::Io(error)),
        };
        write_header(&mut file, family, created_at_ms, &segment_id)?;
        file.sync_all()?;
        sync_directory(root)?;
        return Ok(ActiveWriter {
            segment_id,
            family,
            created_at_ms,
            path,
            file,
            bytes: SEGMENT_HEADER_BYTES,
            record_count: 0,
        });
    }
    Err(StorageError::Integrity(
        "could not allocate a unique active segment filename".to_owned(),
    ))
}

fn write_header(
    file: &mut File,
    family: SegmentFamily,
    created_at_ms: i64,
    segment_id: &str,
) -> Result<(), StorageError> {
    let uuid = Uuid::parse_str(segment_id)
        .map_err(|error| StorageError::Integrity(format!("invalid segment UUID: {error}")))?;
    let mut header = [0_u8; SEGMENT_HEADER_LEN];
    header[0..8].copy_from_slice(SEGMENT_MAGIC);
    header[8..10].copy_from_slice(&SEGMENT_FORMAT_VERSION.to_be_bytes());
    header[10] = family.header_tag();
    header[11] = 0;
    header[12..20].copy_from_slice(&created_at_ms.to_be_bytes());
    header[20..36].copy_from_slice(uuid.as_bytes());
    file.write_all(&header)?;
    Ok(())
}

fn append_frame(file: &mut File, record: &[u8]) -> Result<(), StorageError> {
    let length = u32::try_from(record.len())
        .map_err(|_| StorageError::Integrity("record does not fit frame length".to_owned()))?;
    let digest = Sha256::digest(record);
    file.write_all(&length.to_be_bytes())?;
    file.write_all(&(!length).to_be_bytes())?;
    file.write_all(&digest)?;
    file.write_all(record)?;
    file.write_all(FRAME_COMMIT_MARKER)?;
    Ok(())
}

#[allow(clippy::too_many_lines)]
fn scan_raw_segment(
    path: &Path,
    repair_active_tail: bool,
    mut visitor: Option<&mut RecordVisitor<'_>>,
    expected_family: SegmentFamily,
    policy: &SegmentPolicy,
) -> Result<RawScan, StorageError> {
    reject_symlink_or_non_file(path)?;
    let mut file = OpenOptions::new()
        .read(true)
        .write(repair_active_tail)
        .open(path)?;
    let original_bytes = file.metadata()?.len();
    if original_bytes < SEGMENT_HEADER_BYTES {
        return Err(StorageError::Integrity(format!(
            "segment header is truncated: {}",
            path.display()
        )));
    }
    let mut header = [0_u8; SEGMENT_HEADER_LEN];
    file.read_exact(&mut header)?;
    let (family, created_at_ms, segment_id) = parse_header(&header)?;
    if family != expected_family {
        return Err(StorageError::Integrity(format!(
            "segment header family {family} differs from expected {expected_family}"
        )));
    }

    let mut offset = SEGMENT_HEADER_BYTES;
    let mut record_count = 0_u64;
    let mut truncated_tail = false;
    while offset < original_bytes {
        let remaining = original_bytes - offset;
        if remaining < 8 {
            if repair_active_tail {
                file.set_len(offset)?;
                file.sync_all()?;
                truncated_tail = true;
                break;
            }
            return Err(StorageError::Integrity(format!(
                "sealed segment has an incomplete frame length at byte {offset}"
            )));
        }
        let mut length_bytes = [0_u8; 8];
        file.read_exact(&mut length_bytes)?;
        let payload_length = u32::from_be_bytes(
            length_bytes[0..4]
                .try_into()
                .map_err(|_| StorageError::Integrity("invalid frame length bytes".to_owned()))?,
        );
        let complement = u32::from_be_bytes(length_bytes[4..8].try_into().map_err(|_| {
            StorageError::Integrity("invalid frame length complement bytes".to_owned())
        })?);
        if complement != !payload_length {
            return Err(StorageError::Integrity(format!(
                "frame length check mismatch at byte {offset}"
            )));
        }
        let payload_bytes = u64::from(payload_length);
        if payload_bytes > MAX_RECORD_BYTES_HARD || payload_bytes > policy.max_record_bytes {
            return Err(StorageError::Integrity(format!(
                "frame at byte {offset} declares {payload_bytes} bytes above the configured or hard cap"
            )));
        }
        let frame_bytes = FRAME_OVERHEAD_BYTES
            .checked_add(payload_bytes)
            .ok_or_else(|| StorageError::Integrity("frame length overflow".to_owned()))?;
        let frame_end = offset
            .checked_add(frame_bytes)
            .ok_or_else(|| StorageError::Integrity("scan offset overflow".to_owned()))?;
        if frame_end > policy.target_segment_bytes {
            return Err(StorageError::Integrity(format!(
                "segment exceeds configured {} target of {} bytes",
                expected_family, policy.target_segment_bytes
            )));
        }
        if remaining < frame_bytes {
            if repair_active_tail {
                if tail_has_commit_marker(&mut file, original_bytes)? {
                    return Err(StorageError::Integrity(format!(
                        "committed frame at byte {offset} has corrupt length metadata"
                    )));
                }
                file.set_len(offset)?;
                file.sync_all()?;
                truncated_tail = true;
                break;
            }
            return Err(StorageError::Integrity(format!(
                "sealed segment has an incomplete frame at byte {offset}"
            )));
        }
        let mut expected_digest = [0_u8; 32];
        file.read_exact(&mut expected_digest)?;
        let payload_len = usize::try_from(payload_bytes).map_err(|_| {
            StorageError::Integrity("frame length cannot fit memory index".to_owned())
        })?;
        let mut payload = vec![0_u8; payload_len];
        file.read_exact(&mut payload)?;
        let actual_digest = Sha256::digest(&payload);
        if actual_digest.as_slice() != expected_digest {
            return Err(StorageError::Integrity(format!(
                "frame digest mismatch at byte {offset}"
            )));
        }
        let mut commit_marker = [0_u8; 8];
        file.read_exact(&mut commit_marker)?;
        if &commit_marker != FRAME_COMMIT_MARKER {
            return Err(StorageError::Integrity(format!(
                "frame commit marker mismatch at byte {offset}"
            )));
        }
        if let Some(callback) = visitor.as_deref_mut() {
            callback(&payload)?;
        }
        offset = frame_end;
        record_count = record_count
            .checked_add(1)
            .ok_or_else(|| StorageError::Integrity("record count overflow".to_owned()))?;
    }
    drop(file);
    let bytes = fs::metadata(path)?.len();
    Ok(RawScan {
        segment_id,
        family,
        created_at_ms,
        record_count,
        bytes,
        sha256: sha256_file(path)?,
        truncated_tail,
    })
}

fn tail_has_commit_marker(file: &mut File, original_bytes: u64) -> Result<bool, StorageError> {
    if original_bytes < u64::try_from(FRAME_COMMIT_MARKER.len()).unwrap_or(u64::MAX) {
        return Ok(false);
    }
    let marker_len = i64::try_from(FRAME_COMMIT_MARKER.len())
        .map_err(|_| StorageError::Integrity("commit marker length overflow".to_owned()))?;
    file.seek(SeekFrom::End(-marker_len))?;
    let mut tail = [0_u8; 8];
    file.read_exact(&mut tail)?;
    Ok(&tail == FRAME_COMMIT_MARKER)
}

fn parse_header(
    header: &[u8; SEGMENT_HEADER_LEN],
) -> Result<(SegmentFamily, i64, String), StorageError> {
    if &header[0..8] != SEGMENT_MAGIC {
        return Err(StorageError::Integrity(
            "segment magic does not match".to_owned(),
        ));
    }
    let version = u16::from_be_bytes([header[8], header[9]]);
    if version != SEGMENT_FORMAT_VERSION {
        return Err(StorageError::Integrity(format!(
            "unsupported segment format version {version}"
        )));
    }
    if header[11] != 0 {
        return Err(StorageError::Integrity(
            "segment reserved header byte is nonzero".to_owned(),
        ));
    }
    let family = SegmentFamily::from_header_tag(header[10])?;
    let created_at_ms = i64::from_be_bytes(
        header[12..20]
            .try_into()
            .map_err(|_| StorageError::Integrity("invalid creation timestamp".to_owned()))?,
    );
    let uuid_bytes: [u8; 16] = header[20..36]
        .try_into()
        .map_err(|_| StorageError::Integrity("invalid segment UUID bytes".to_owned()))?;
    Ok((
        family,
        created_at_ms,
        Uuid::from_bytes(uuid_bytes).to_string(),
    ))
}

fn validate_writer_scan(writer: &ActiveWriter, scan: &RawScan) -> Result<(), StorageError> {
    if scan.segment_id != writer.segment_id
        || scan.family != writer.family
        || scan.created_at_ms != writer.created_at_ms
        || scan.record_count != writer.record_count
        || scan.bytes != writer.bytes
        || scan.truncated_tail
    {
        return Err(StorageError::Integrity(format!(
            "active {} writer state differs from disk",
            writer.segment_id
        )));
    }
    Ok(())
}

fn validate_scan_name(scan: &RawScan, parts: &NameParts) -> Result<(), StorageError> {
    if scan.segment_id != parts.segment_id
        || scan.family != parts.family
        || scan.created_at_ms != parts.created_at_ms
    {
        return Err(StorageError::Integrity(format!(
            "segment header differs from filename for {}",
            parts.segment_id
        )));
    }
    Ok(())
}

fn compare_manifest(
    expected: &SegmentManifest,
    actual: &SegmentManifest,
) -> Result<(), StorageError> {
    if expected != actual {
        return Err(StorageError::Integrity(format!(
            "manifest/file metadata mismatch for {}",
            expected.segment_id
        )));
    }
    Ok(())
}

fn catalog_manifest_map(
    catalog: &dyn SegmentCatalog,
) -> Result<HashMap<String, SegmentManifest>, StorageError> {
    let mut by_id = HashMap::new();
    let mut paths = HashSet::new();
    for manifest in catalog.list_manifests()? {
        validate_manifest_identity(&manifest)?;
        if !paths.insert(manifest.relative_path.clone()) {
            return Err(StorageError::Integrity(format!(
                "duplicate manifest path {}",
                manifest.relative_path
            )));
        }
        if by_id
            .insert(manifest.segment_id.clone(), manifest)
            .is_some()
        {
            return Err(StorageError::Integrity(
                "duplicate manifest segment ID".to_owned(),
            ));
        }
    }
    Ok(by_id)
}

fn validate_relative_path(relative_path: &str) -> Result<(), StorageError> {
    let path = Path::new(relative_path);
    let mut components = path.components();
    let first = components.next();
    if !matches!(first, Some(Component::Normal(_))) || components.next().is_some() {
        return Err(StorageError::Integrity(format!(
            "segment path is not one safe relative filename: {relative_path:?}"
        )));
    }
    Ok(())
}

fn reject_symlink_or_non_file(path: &Path) -> Result<(), StorageError> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() {
        return Err(StorageError::Integrity(format!(
            "segment is not a regular file: {}",
            path.display()
        )));
    }
    Ok(())
}

fn active_file_name(family: SegmentFamily, created_at_ms: i64, segment_id: &str) -> String {
    format!("{family}_{created_at_ms}_{segment_id}.active")
}

fn sealed_file_name(
    family: SegmentFamily,
    created_at_ms: i64,
    segment_id: &str,
    sealed_at_ms: i64,
    compressed: bool,
) -> String {
    let suffix = if compressed {
        ".segment.zst"
    } else {
        ".segment"
    };
    format!("{family}_{created_at_ms}_{segment_id}_{sealed_at_ms}{suffix}")
}

fn parse_active_name(name: &str) -> Result<NameParts, StorageError> {
    let base = name.strip_suffix(".active").ok_or_else(|| {
        StorageError::Integrity(format!("invalid active segment filename: {name}"))
    })?;
    let fields = base.split('_').collect::<Vec<_>>();
    if fields.len() != 3 {
        return Err(StorageError::Integrity(format!(
            "invalid active segment filename: {name}"
        )));
    }
    let family = fields[0].parse()?;
    let created_at_ms = parse_nonnegative_timestamp(fields[1], name)?;
    let segment_id = canonical_uuid(fields[2], name)?;
    Ok(NameParts {
        segment_id,
        family,
        created_at_ms,
        sealed_at_ms: None,
        compressed: false,
    })
}

fn parse_sealed_name(name: &str) -> Result<NameParts, StorageError> {
    let (base, compressed) = if let Some(base) = name.strip_suffix(".segment.zst") {
        (base, true)
    } else if let Some(base) = name.strip_suffix(".segment") {
        (base, false)
    } else {
        return Err(StorageError::Integrity(format!(
            "invalid sealed segment filename: {name}"
        )));
    };
    let fields = base.split('_').collect::<Vec<_>>();
    if fields.len() != 4 {
        return Err(StorageError::Integrity(format!(
            "invalid sealed segment filename: {name}"
        )));
    }
    let family = fields[0].parse()?;
    let created_at_ms = parse_nonnegative_timestamp(fields[1], name)?;
    let segment_id = canonical_uuid(fields[2], name)?;
    let sealed_at_ms = parse_nonnegative_timestamp(fields[3], name)?;
    if sealed_at_ms < created_at_ms {
        return Err(StorageError::Integrity(format!(
            "sealed timestamp precedes creation in {name}"
        )));
    }
    Ok(NameParts {
        segment_id,
        family,
        created_at_ms,
        sealed_at_ms: Some(sealed_at_ms),
        compressed,
    })
}

fn canonical_uuid(value: &str, filename: &str) -> Result<String, StorageError> {
    let parsed = Uuid::parse_str(value).map_err(|_| {
        StorageError::Integrity(format!("invalid segment UUID in filename {filename}"))
    })?;
    let canonical = parsed.to_string();
    if canonical != value {
        return Err(StorageError::Integrity(format!(
            "non-canonical segment UUID in filename {filename}"
        )));
    }
    Ok(canonical)
}

fn parse_nonnegative_timestamp(value: &str, filename: &str) -> Result<i64, StorageError> {
    let timestamp = value.parse::<i64>().map_err(|_| {
        StorageError::Integrity(format!("invalid timestamp in segment filename {filename}"))
    })?;
    if timestamp < 0 {
        return Err(StorageError::Integrity(format!(
            "negative timestamp in segment filename {filename}"
        )));
    }
    Ok(timestamp)
}

fn validate_codec_name(name: &str) -> Result<(), StorageError> {
    if name != "zstd" {
        return Err(StorageError::Integrity(format!(
            "unsupported compression codec name {name:?}; schema v2 requires \"zstd\""
        )));
    }
    Ok(())
}

/// Validates the identity fields shared by the file layer and durable catalog.
/// Keeping this at one boundary prevents catalog rows that can be committed but
/// can never be reopened by [`SegmentStore`].
pub(crate) fn validate_manifest_identity(manifest: &SegmentManifest) -> Result<(), StorageError> {
    validate_relative_path(&manifest.relative_path)?;
    let parts = parse_sealed_name(&manifest.relative_path)?;
    let expected_compression = parts.compressed.then_some("zstd");
    if parts.segment_id != manifest.segment_id
        || parts.family != manifest.family
        || parts.created_at_ms != manifest.created_at_ms
        || parts.sealed_at_ms != Some(manifest.sealed_at_ms)
        || manifest.compression.as_deref() != expected_compression
    {
        return Err(StorageError::Integrity(format!(
            "manifest filename metadata mismatch for {}",
            manifest.segment_id
        )));
    }
    Ok(())
}

fn is_staging_name(name: &str) -> bool {
    name.ends_with(".encode.tmp") || name.ends_with(".decode.tmp")
}

fn logical_day(timestamp_ms: i64) -> i64 {
    timestamp_ms.div_euclid(MILLIS_PER_DAY)
}

fn sha256_file(path: &Path) -> Result<String, StorageError> {
    let mut file = File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0_u8; IO_BUFFER_BYTES];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hex_digest(&hasher.finalize()))
}

fn hex_digest(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len().saturating_mul(2));
    for &byte in bytes {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), StorageError> {
    File::open(path)?.sync_all()?;
    Ok(())
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), StorageError> {
    Ok(())
}

fn prepare_private_directory(path: &Path) -> Result<(), StorageError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_dir() => {}
        Ok(_) => {
            return Err(StorageError::Integrity(format!(
                "segment root is a symlink or non-directory: {}",
                path.display()
            )))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            fs::create_dir_all(path)?;
            if !fs::symlink_metadata(path)?.file_type().is_dir() {
                return Err(StorageError::Integrity(format!(
                    "segment root is not a directory: {}",
                    path.display()
                )));
            }
        }
        Err(error) => return Err(StorageError::Io(error)),
    }
    enforce_private_directory_permissions(path)
}

#[cfg(unix)]
fn enforce_private_directory_permissions(path: &Path) -> Result<(), StorageError> {
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
    Ok(())
}

#[cfg(not(unix))]
fn enforce_private_directory_permissions(_path: &Path) -> Result<(), StorageError> {
    Ok(())
}

#[cfg(unix)]
fn enforce_private_file_permissions(path: &Path) -> Result<(), StorageError> {
    fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
    Ok(())
}

#[cfg(not(unix))]
fn enforce_private_file_permissions(_path: &Path) -> Result<(), StorageError> {
    Ok(())
}

trait PrivateOpenOptions {
    fn private_mode(&mut self) -> &mut Self;
}

impl PrivateOpenOptions for OpenOptions {
    fn private_mode(&mut self) -> &mut Self {
        #[cfg(unix)]
        self.mode(0o600);
        self
    }
}

#[cfg(test)]
mod tests {
    use std::io::{Seek, SeekFrom};
    use std::sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    };

    use tempfile::TempDir;

    use super::*;
    use crate::storage::{
        retention::{DeletionCandidate, DeletionReason, DiskPressure},
        CatalogError, SegmentPolicy,
    };

    #[derive(Default)]
    struct MemoryCatalog {
        manifests: Mutex<HashMap<String, SegmentManifest>>,
        pending: Mutex<HashSet<String>>,
        fail_commit: AtomicBool,
        fail_finish: AtomicBool,
    }

    impl MemoryCatalog {
        fn manifests(&self) -> Vec<SegmentManifest> {
            self.manifests.lock().unwrap().values().cloned().collect()
        }
    }

    impl SegmentCatalog for MemoryCatalog {
        fn list_manifests(&self) -> Result<Vec<SegmentManifest>, CatalogError> {
            Ok(self.manifests())
        }

        fn commit_manifest(&self, manifest: &SegmentManifest) -> Result<(), CatalogError> {
            if self.fail_commit.load(Ordering::SeqCst) {
                return Err(CatalogError::new("injected commit failure"));
            }
            let mut manifests = self.manifests.lock().unwrap();
            if let Some(existing) = manifests.get(&manifest.segment_id) {
                if existing == manifest {
                    return Ok(());
                }
                return Err(CatalogError::new("manifest conflict"));
            }
            manifests.insert(manifest.segment_id.clone(), manifest.clone());
            Ok(())
        }

        fn begin_delete(&self, segment_id: &str) -> Result<(), CatalogError> {
            if !self.manifests.lock().unwrap().contains_key(segment_id) {
                return Err(CatalogError::new("missing manifest"));
            }
            self.pending.lock().unwrap().insert(segment_id.to_owned());
            Ok(())
        }

        fn list_pending_deletions(&self) -> Result<Vec<PendingSegmentDeletion>, CatalogError> {
            Ok(self
                .pending
                .lock()
                .unwrap()
                .iter()
                .map(|segment_id| PendingSegmentDeletion {
                    segment_id: segment_id.clone(),
                })
                .collect())
        }

        fn finish_delete(&self, segment_id: &str) -> Result<(), CatalogError> {
            if self.fail_finish.load(Ordering::SeqCst) {
                return Err(CatalogError::new("injected finish failure"));
            }
            self.manifests.lock().unwrap().remove(segment_id);
            self.pending.lock().unwrap().remove(segment_id);
            Ok(())
        }
    }

    struct OversizedDecodeCodec;

    impl CompressionCodec for OversizedDecodeCodec {
        fn name(&self) -> &'static str {
            "zstd"
        }

        fn encode(&self, input: &mut dyn Read, output: &mut dyn Write) -> Result<(), String> {
            std::io::copy(input, output)
                .map(|_| ())
                .map_err(|error| error.to_string())
        }

        fn decode(&self, input: &mut dyn Read, output: &mut dyn Write) -> Result<(), String> {
            let mut ignored = Vec::new();
            input
                .read_to_end(&mut ignored)
                .map_err(|error| error.to_string())?;
            output
                .write_all(&[0_u8; 149])
                .map_err(|error| error.to_string())
        }
    }

    struct UnsupportedCodec;

    impl CompressionCodec for UnsupportedCodec {
        fn name(&self) -> &'static str {
            "zstd-v1"
        }

        fn encode(&self, _input: &mut dyn Read, _output: &mut dyn Write) -> Result<(), String> {
            unreachable!("unsupported codec must be rejected before use")
        }

        fn decode(&self, _input: &mut dyn Read, _output: &mut dyn Write) -> Result<(), String> {
            unreachable!("unsupported codec must be rejected before use")
        }
    }

    fn policy(compress: bool) -> SegmentPolicy {
        SegmentPolicy {
            retention_age_ms: 86_400_000,
            max_total_bytes: 1_048_576,
            target_segment_bytes: 148,
            max_record_bytes: 64,
            minimum_segments: 0,
            persist: true,
            compress,
        }
    }

    fn policies(compressed_family: Option<SegmentFamily>) -> SegmentPolicies {
        SegmentPolicies::new(
            policy(compressed_family == Some(SegmentFamily::Chat)),
            policy(compressed_family == Some(SegmentFamily::Audit)),
            policy(compressed_family == Some(SegmentFamily::Debug)),
        )
        .unwrap()
    }

    fn open_store(
        directory: &TempDir,
        catalog: Arc<MemoryCatalog>,
    ) -> Result<SegmentStore, StorageError> {
        SegmentStore::open(directory.path(), policies(None), catalog)
    }

    #[test]
    fn arbitrary_bytes_round_trip_and_families_rotate_independently() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
        let binary = [0_u8, 0xff, b'\n', 0, 0x80];
        store.append(SegmentFamily::Chat, &binary, 1, true).unwrap();
        store
            .append(SegmentFamily::Audit, b"audit", 1, true)
            .unwrap();
        let chat_manifest = store.seal_family(SegmentFamily::Chat, 2).unwrap().unwrap();
        assert_eq!(store.active_writer_count(), 1);

        let mut records = Vec::new();
        store
            .visit_records(&chat_manifest, &mut |record| {
                records.push(record.to_vec());
                Ok(())
            })
            .unwrap();
        assert_eq!(records, vec![binary.to_vec()]);
        assert_eq!(catalog.manifests().len(), 1);
    }

    #[test]
    fn size_and_date_boundaries_rotate_before_the_next_record() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
        let first = vec![1_u8; 64];
        store.append(SegmentFamily::Chat, &first, 1, true).unwrap();
        let outcome = store.append(SegmentFamily::Chat, b"next", 2, true).unwrap();
        assert!(matches!(
            outcome,
            AppendOutcome::Stored {
                rotated_segment_id: Some(_),
                ..
            }
        ));
        let date_outcome = store
            .append(SegmentFamily::Chat, b"tomorrow", MILLIS_PER_DAY + 1, true)
            .unwrap();
        assert!(matches!(
            date_outcome,
            AppendOutcome::Stored {
                rotated_segment_id: Some(_),
                ..
            }
        ));
        assert_eq!(catalog.manifests().len(), 2);
    }

    #[test]
    fn incomplete_active_tail_is_truncated_without_losing_committed_frames() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let active_path = {
            let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
            store
                .append(SegmentFamily::Chat, b"complete", 1, true)
                .unwrap();
            store
                .writers
                .get(&SegmentFamily::Chat)
                .unwrap()
                .path
                .clone()
        };
        let good_len = fs::metadata(&active_path).unwrap().len();
        let mut file = OpenOptions::new().append(true).open(&active_path).unwrap();
        file.write_all(&[0xde, 0xad, 0xbe]).unwrap();
        file.sync_all().unwrap();

        let mut reopened = open_store(&directory, Arc::clone(&catalog)).unwrap();
        assert_eq!(reopened.recovery_report().truncated_active_tails, 1);
        assert_eq!(fs::metadata(&active_path).unwrap().len(), good_len);
        let manifest = reopened
            .seal_family(SegmentFamily::Chat, 2)
            .unwrap()
            .unwrap();
        let mut records = Vec::new();
        reopened
            .visit_records(&manifest, &mut |record| {
                records.push(record.to_vec());
                Ok(())
            })
            .unwrap();
        assert_eq!(records, vec![b"complete".to_vec()]);
    }

    #[test]
    fn complete_active_frame_digest_corruption_is_rejected() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let active_path = {
            let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
            store
                .append(SegmentFamily::Chat, b"complete", 1, true)
                .unwrap();
            store
                .writers
                .get(&SegmentFamily::Chat)
                .unwrap()
                .path
                .clone()
        };
        let mut file = OpenOptions::new().write(true).open(active_path).unwrap();
        file.seek(SeekFrom::Start(SEGMENT_HEADER_BYTES + 8))
            .unwrap();
        file.write_all(&[0xff]).unwrap();
        file.sync_all().unwrap();

        let Err(error) = open_store(&directory, catalog) else {
            panic!("digest corruption must fail");
        };
        assert!(error.to_string().contains("digest mismatch"));
    }

    #[test]
    fn committed_last_frame_length_corruption_is_rejected_not_truncated() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let active_path = {
            let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
            store
                .append(SegmentFamily::Chat, b"complete", 1, true)
                .unwrap();
            store
                .writers
                .get(&SegmentFamily::Chat)
                .unwrap()
                .path
                .clone()
        };
        let committed_bytes = fs::metadata(&active_path).unwrap().len();
        let mut file = OpenOptions::new().write(true).open(&active_path).unwrap();
        file.seek(SeekFrom::Start(SEGMENT_HEADER_BYTES + 3))
            .unwrap();
        file.write_all(&[9]).unwrap();
        file.sync_all().unwrap();
        drop(file);

        let Err(error) = open_store(&directory, catalog) else {
            panic!("committed length corruption must fail closed");
        };
        assert!(error.to_string().contains("length check mismatch"));
        assert_eq!(fs::metadata(active_path).unwrap().len(), committed_bytes);
    }

    #[test]
    fn committed_length_and_complement_corruption_still_is_not_truncated() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let active_path = {
            let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
            store
                .append(SegmentFamily::Chat, b"complete", 1, true)
                .unwrap();
            store
                .writers
                .get(&SegmentFamily::Chat)
                .unwrap()
                .path
                .clone()
        };
        let committed_bytes = fs::metadata(&active_path).unwrap().len();
        let corrupt_length = 9_u32;
        let mut file = OpenOptions::new().write(true).open(&active_path).unwrap();
        file.seek(SeekFrom::Start(SEGMENT_HEADER_BYTES)).unwrap();
        file.write_all(&corrupt_length.to_be_bytes()).unwrap();
        file.write_all(&(!corrupt_length).to_be_bytes()).unwrap();
        file.sync_all().unwrap();
        drop(file);

        let Err(error) = open_store(&directory, catalog) else {
            panic!("paired committed length corruption must fail closed");
        };
        assert!(error.to_string().contains("committed frame"));
        assert_eq!(fs::metadata(active_path).unwrap().len(), committed_bytes);
    }

    #[test]
    fn recovered_active_segment_cannot_exceed_its_family_target() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let active_path = {
            let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
            store
                .append(SegmentFamily::Chat, &[1_u8; 64], 1, true)
                .unwrap();
            store
                .writers
                .get(&SegmentFamily::Chat)
                .unwrap()
                .path
                .clone()
        };
        let mut file = OpenOptions::new().append(true).open(active_path).unwrap();
        append_frame(&mut file, b"extra").unwrap();
        file.sync_all().unwrap();
        drop(file);

        let Err(error) = open_store(&directory, catalog) else {
            panic!("over-target recovered segment must fail closed");
        };
        assert!(error.to_string().contains("exceeds configured chat target"));
    }

    #[test]
    fn renamed_but_unmanifested_segment_is_adopted_once() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
        store.append(SegmentFamily::Chat, b"body", 1, true).unwrap();
        catalog.fail_commit.store(true, Ordering::SeqCst);
        assert!(store.seal_family(SegmentFamily::Chat, 2).is_err());
        catalog.fail_commit.store(false, Ordering::SeqCst);

        let reopened = open_store(&directory, Arc::clone(&catalog)).unwrap();
        assert_eq!(reopened.recovery_report().adopted_segments, 1);
        assert_eq!(catalog.manifests().len(), 1);
        drop(reopened);
        let second = open_store(&directory, Arc::clone(&catalog)).unwrap();
        assert_eq!(second.recovery_report().adopted_segments, 0);
        assert_eq!(catalog.manifests().len(), 1);
    }

    #[test]
    fn manifest_missing_file_and_file_digest_drift_are_explicit() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let manifest = {
            let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
            store
                .append(SegmentFamily::Audit, b"body", 1, true)
                .unwrap();
            store.seal_family(SegmentFamily::Audit, 2).unwrap().unwrap()
        };
        fs::remove_file(directory.path().join(&manifest.relative_path)).unwrap();
        let Err(missing) = open_store(&directory, Arc::clone(&catalog)) else {
            panic!("missing file must fail");
        };
        assert!(missing.to_string().contains("has no sealed file"));

        catalog.manifests.lock().unwrap().clear();
        let mut new_store = open_store(&directory, Arc::clone(&catalog)).unwrap();
        new_store
            .append(SegmentFamily::Debug, b"body", 3, true)
            .unwrap();
        let drift = new_store
            .seal_family(SegmentFamily::Debug, 4)
            .unwrap()
            .unwrap();
        drop(new_store);
        let path = directory.path().join(&drift.relative_path);
        let mut file = OpenOptions::new().append(true).open(path).unwrap();
        file.write_all(&[0]).unwrap();
        file.sync_all().unwrap();
        let Err(error) = open_store(&directory, catalog) else {
            panic!("digest drift must fail");
        };
        assert!(error.to_string().contains("incomplete frame"));
    }

    #[test]
    fn real_zstd_codec_round_trips_reopens_and_records_magic_and_name() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let codec: Arc<dyn CompressionCodec> = Arc::new(ZstdCodec::default());
        let mut store = SegmentStore::open_with_codec(
            directory.path(),
            policies(Some(SegmentFamily::Chat)),
            Arc::clone(&catalog) as Arc<dyn SegmentCatalog>,
            codec,
        )
        .unwrap();
        store
            .append(SegmentFamily::Chat, b"compressed", 1, true)
            .unwrap();
        let manifest = store.seal_family(SegmentFamily::Chat, 2).unwrap().unwrap();
        assert_eq!(manifest.compression.as_deref(), Some("zstd"));
        assert!(manifest.relative_path.ends_with(".segment.zst"));
        let encoded = fs::read(directory.path().join(&manifest.relative_path)).unwrap();
        assert_eq!(&encoded[..4], &[0x28, 0xb5, 0x2f, 0xfd]);
        drop(store);
        let reopened = SegmentStore::open_with_codec(
            directory.path(),
            policies(None),
            Arc::clone(&catalog) as Arc<dyn SegmentCatalog>,
            Arc::new(ZstdCodec::default()),
        )
        .unwrap();
        let mut records = Vec::new();
        reopened
            .visit_records(&manifest, &mut |record| {
                records.push(record.to_vec());
                Ok(())
            })
            .unwrap();
        assert_eq!(records, vec![b"compressed".to_vec()]);
    }

    #[test]
    fn codec_decode_cannot_expand_beyond_family_segment_limit() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let codec: Arc<dyn CompressionCodec> = Arc::new(OversizedDecodeCodec);
        let mut store = SegmentStore::open_with_codec(
            directory.path(),
            policies(Some(SegmentFamily::Chat)),
            catalog,
            codec,
        )
        .unwrap();
        store.append(SegmentFamily::Chat, b"body", 1, true).unwrap();
        let error = store.seal_family(SegmentFamily::Chat, 2).unwrap_err();
        assert!(error.to_string().contains("decoded segment exceeds"));
        assert!(matches!(
            store.append(SegmentFamily::Audit, b"poisoned", 3, true),
            Err(StorageError::Poisoned)
        ));
    }

    #[test]
    fn codec_name_must_match_the_schema_v2_manifest_contract() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let codec: Arc<dyn CompressionCodec> = Arc::new(UnsupportedCodec);
        let result = SegmentStore::open_with_codec(
            directory.path(),
            policies(Some(SegmentFamily::Chat)),
            catalog,
            codec,
        );
        assert!(matches!(result, Err(StorageError::Integrity(_))));
    }

    #[cfg(unix)]
    #[test]
    fn segment_directory_and_files_are_owner_only_and_symlink_roots_are_rejected() {
        use std::os::unix::fs::{symlink, PermissionsExt};

        let parent = tempfile::tempdir().unwrap();
        let root = parent.path().join("segments");
        let catalog = Arc::new(MemoryCatalog::default());
        let mut store = SegmentStore::open(
            &root,
            policies(None),
            Arc::clone(&catalog) as Arc<dyn SegmentCatalog>,
        )
        .unwrap();
        store
            .append(SegmentFamily::Chat, b"private", 1, true)
            .unwrap();
        let active = store
            .writers
            .get(&SegmentFamily::Chat)
            .unwrap()
            .path
            .clone();
        assert_eq!(
            fs::metadata(&root).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(&active).unwrap().permissions().mode() & 0o777,
            0o600
        );
        let manifest = store.seal_family(SegmentFamily::Chat, 2).unwrap().unwrap();
        let sealed = root.join(manifest.relative_path);
        assert_eq!(
            fs::metadata(&sealed).unwrap().permissions().mode() & 0o777,
            0o600
        );
        drop(store);

        fs::set_permissions(&sealed, fs::Permissions::from_mode(0o644)).unwrap();
        let reopened = SegmentStore::open(&root, policies(None), catalog).unwrap();
        assert_eq!(
            fs::metadata(&sealed).unwrap().permissions().mode() & 0o777,
            0o600
        );
        drop(reopened);

        let real_root = parent.path().join("real-segments");
        fs::create_dir(&real_root).unwrap();
        let symlink_root = parent.path().join("linked-segments");
        symlink(&real_root, &symlink_root).unwrap();
        let result = SegmentStore::open(
            &symlink_root,
            policies(None),
            Arc::new(MemoryCatalog::default()),
        );
        assert!(matches!(result, Err(StorageError::Integrity(_))));
    }

    #[test]
    fn critical_admission_and_disabled_persistence_create_no_files() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let mut disabled = policy(false);
        disabled.persist = false;
        let policies = SegmentPolicies::new(disabled, policy(false), policy(false)).unwrap();
        let mut store = SegmentStore::open(
            directory.path(),
            policies,
            Arc::clone(&catalog) as Arc<dyn SegmentCatalog>,
        )
        .unwrap();
        assert_eq!(
            store.append(SegmentFamily::Chat, b"x", 1, true).unwrap(),
            AppendOutcome::Skipped(SkipReason::PersistenceDisabled)
        );
        assert_eq!(
            store.append(SegmentFamily::Audit, b"x", 1, false).unwrap(),
            AppendOutcome::Skipped(SkipReason::CriticalDiskPressure)
        );
        assert_eq!(store.active_writer_count(), 0);
        assert!(fs::read_dir(directory.path()).unwrap().next().is_none());
    }

    #[test]
    fn cleanup_executor_rejects_public_plan_that_crosses_family_minimum_before_unlink() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let mut chat = policy(false);
        chat.minimum_segments = 1;
        let policies = SegmentPolicies::new(chat, policy(false), policy(false)).unwrap();
        let mut store = SegmentStore::open(
            directory.path(),
            policies,
            Arc::clone(&catalog) as Arc<dyn SegmentCatalog>,
        )
        .unwrap();
        store.append(SegmentFamily::Chat, b"keep", 1, true).unwrap();
        let manifest = store.seal_family(SegmentFamily::Chat, 2).unwrap().unwrap();
        let path = directory.path().join(&manifest.relative_path);
        let plan = CleanupPlan {
            pressure: DiskPressure::Critical,
            deletions: vec![DeletionCandidate {
                manifest: manifest.clone(),
                reason: DeletionReason::DiskPressure,
            }],
            projected_available_bytes: manifest.stored_bytes,
            pause_background: true,
            allow_disposable_writes: false,
            deletion_limit_reached: false,
        };

        let error = store.apply_cleanup(&plan).unwrap_err();
        assert!(error.to_string().contains("below minimum segment count"));
        assert!(path.exists(), "preflight failure must not unlink any file");
        assert_eq!(catalog.manifests(), vec![manifest]);
        assert!(catalog.pending.lock().unwrap().is_empty());
    }

    #[test]
    fn interrupted_delete_is_finished_on_reopen() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let manifest = {
            let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
            store
                .append(SegmentFamily::Debug, b"body", 1, true)
                .unwrap();
            store.seal_family(SegmentFamily::Debug, 2).unwrap().unwrap()
        };
        let mut store = open_store(&directory, Arc::clone(&catalog)).unwrap();
        let plan = CleanupPlan {
            pressure: DiskPressure::Normal,
            deletions: vec![DeletionCandidate {
                manifest: manifest.clone(),
                reason: DeletionReason::Expired,
            }],
            projected_available_bytes: 1,
            pause_background: false,
            allow_disposable_writes: true,
            deletion_limit_reached: false,
        };
        catalog.fail_finish.store(true, Ordering::SeqCst);
        assert!(store.apply_cleanup(&plan).is_err());
        assert!(!directory.path().join(&manifest.relative_path).exists());
        assert_eq!(catalog.pending.lock().unwrap().len(), 1);
        catalog.fail_finish.store(false, Ordering::SeqCst);
        assert!(matches!(
            store.append(SegmentFamily::Chat, b"must reopen", 3, true),
            Err(StorageError::Poisoned)
        ));

        let reopened = open_store(&directory, Arc::clone(&catalog)).unwrap();
        assert_eq!(reopened.recovery_report().completed_deletions, 1);
        assert!(catalog.manifests().is_empty());
        assert!(catalog.pending.lock().unwrap().is_empty());
    }

    #[test]
    fn unexpected_files_and_partial_active_headers_are_handled_differently() {
        let directory = tempfile::tempdir().unwrap();
        let catalog = Arc::new(MemoryCatalog::default());
        let partial_name = active_file_name(SegmentFamily::Chat, 1, &Uuid::new_v4().to_string());
        fs::write(directory.path().join(partial_name), b"partial").unwrap();
        let reopened = open_store(&directory, Arc::clone(&catalog)).unwrap();
        assert_eq!(
            reopened.recovery_report().discarded_partial_active_headers,
            1
        );
        drop(reopened);

        fs::write(directory.path().join("surprise.bin"), b"x").unwrap();
        let Err(error) = open_store(&directory, catalog) else {
            panic!("unexpected file must fail");
        };
        assert!(error.to_string().contains("unexpected file"));
    }
}
