//! Pure retention and disk-pressure planning.

use std::collections::{BTreeMap, HashSet};

use super::{SegmentFamily, SegmentManifest, SegmentPolicies, StorageError};

const BASIS_POINTS: u64 = 10_000;

/// Immutable filesystem-capacity snapshot supplied by the platform layer.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DiskSnapshot {
    pub total_bytes: u64,
    pub available_bytes: u64,
}

/// Validated watermarks and per-pass deletion bound.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WatermarkPolicy {
    pub low_recovery_basis_points: u16,
    pub high_basis_points: u16,
    pub critical_basis_points: u16,
    pub max_deletions_per_run: usize,
}

impl WatermarkPolicy {
    /// Validates `low < high < critical <= 100%` and a nonzero pass bound.
    ///
    /// # Errors
    ///
    /// Returns an integrity/configuration error for an impossible watermark
    /// ordering or zero deletion bound.
    pub fn validate(self) -> Result<Self, StorageError> {
        if self.low_recovery_basis_points >= self.high_basis_points
            || self.high_basis_points >= self.critical_basis_points
            || u64::from(self.critical_basis_points) > BASIS_POINTS
            || self.max_deletions_per_run == 0
        {
            return Err(StorageError::Integrity(
                "watermarks require low < high < critical <= 10000 and a nonzero deletion bound"
                    .to_owned(),
            ));
        }
        Ok(self)
    }
}

/// Current pressure class.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DiskPressure {
    Normal,
    High,
    Critical,
}

/// Why a sealed segment was selected.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeletionReason {
    Expired,
    FamilyByteCap,
    DiskPressure,
}

/// One durable deletion candidate.  Only sealed manifests can enter a plan.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct DeletionCandidate {
    pub manifest: SegmentManifest,
    pub reason: DeletionReason,
}

/// Side effects and admission controls derived from one cleanup snapshot.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CleanupPlan {
    pub pressure: DiskPressure,
    pub deletions: Vec<DeletionCandidate>,
    pub projected_available_bytes: u64,
    pub pause_background: bool,
    pub allow_disposable_writes: bool,
    pub deletion_limit_reached: bool,
}

/// Produces a bounded cleanup plan without touching disk or catalog state.
///
/// Normal cleanup removes expired data before family-byte-cap excess.  Under
/// pressure, additional oldest segments are selected in the explicit order
/// debug, chat, audit until the low recovery watermark is reached or family
/// minimums/pass bounds prevent further reclamation.
///
/// # Errors
///
/// Returns an error for an invalid disk snapshot, watermark policy, duplicate
/// manifest ID, negative time, or byte arithmetic overflow.
#[allow(clippy::too_many_lines)]
pub fn plan_cleanup(
    manifests: &[SegmentManifest],
    policies: &SegmentPolicies,
    disk: DiskSnapshot,
    watermarks: WatermarkPolicy,
    now_ms: i64,
) -> Result<CleanupPlan, StorageError> {
    plan_cleanup_with_previous(manifests, policies, disk, watermarks, now_ms, None)
}

/// Produces a cleanup plan while retaining high/critical pressure until the
/// low recovery watermark is reached. The previous value must come from the
/// durable cleanup state, not from an in-memory timer.
///
/// # Errors
///
/// Returns the same validation and arithmetic errors as [`plan_cleanup`].
#[allow(clippy::too_many_lines)]
pub fn plan_cleanup_with_previous(
    manifests: &[SegmentManifest],
    policies: &SegmentPolicies,
    disk: DiskSnapshot,
    watermarks: WatermarkPolicy,
    now_ms: i64,
    previous_pressure: Option<DiskPressure>,
) -> Result<CleanupPlan, StorageError> {
    let watermarks = watermarks.validate()?;
    if disk.total_bytes == 0 || disk.available_bytes > disk.total_bytes {
        return Err(StorageError::Integrity(
            "disk snapshot requires 0 <= available_bytes <= total_bytes and nonzero total"
                .to_owned(),
        ));
    }
    if now_ms < 0 {
        return Err(StorageError::Integrity(
            "cleanup time must not be negative".to_owned(),
        ));
    }

    let used_bytes = disk.total_bytes - disk.available_bytes;
    let used_basis_points = ratio_basis_points(used_bytes, disk.total_bytes)?;
    let pressure = classify_pressure(used_basis_points, watermarks, previous_pressure);

    let mut by_family = grouped_sorted_manifests(manifests)?;
    let mut selected = HashSet::new();
    let mut deletions = Vec::new();
    let mut reclaimed = 0_u64;
    let limit = watermarks.max_deletions_per_run;

    let recovery_available = recovery_available_bytes(disk.total_bytes, watermarks)?;
    let mut deletion_limit_reached = false;
    if pressure == DiskPressure::Normal {
        // Expiration is the first global priority in normal operation. Keep
        // one family's byte-cap excess from consuming the bounded pass before
        // another family's already-expired data can be reclaimed.
        'expired_families: for family in [
            SegmentFamily::Chat,
            SegmentFamily::Audit,
            SegmentFamily::Debug,
        ] {
            let family_manifests = by_family.entry(family).or_default();
            let policy = policies.get(family);
            let mut remaining_count = family_manifests.len();
            for manifest in family_manifests.iter() {
                if remaining_count <= policy.minimum_segments {
                    break;
                }
                let age_ms = now_ms.saturating_sub(manifest.sealed_at_ms);
                let expired = u64::try_from(age_ms)
                    .map(|age| age >= policy.retention_age_ms)
                    .unwrap_or(false);
                if expired {
                    if deletions.len() == limit {
                        deletion_limit_reached = true;
                        break 'expired_families;
                    }
                    push_candidate(
                        manifest,
                        DeletionReason::Expired,
                        &mut selected,
                        &mut deletions,
                        &mut reclaimed,
                    )?;
                    remaining_count -= 1;
                }
            }
        }

        if !deletion_limit_reached {
            'cap_families: for family in [
                SegmentFamily::Chat,
                SegmentFamily::Audit,
                SegmentFamily::Debug,
            ] {
                let family_manifests = by_family.entry(family).or_default();
                let policy = policies.get(family);
                let mut remaining_count = family_manifests.len();
                let mut remaining_bytes = checked_total_bytes(family_manifests)?;
                for manifest in family_manifests.iter() {
                    if selected.contains(&manifest.segment_id) {
                        remaining_count -= 1;
                        remaining_bytes = remaining_bytes
                            .checked_sub(manifest.stored_bytes)
                            .ok_or_else(|| {
                                StorageError::Integrity("family byte total underflow".to_owned())
                            })?;
                    }
                }
                if remaining_bytes <= policy.max_total_bytes {
                    continue;
                }
                for manifest in family_manifests.iter() {
                    if remaining_count <= policy.minimum_segments
                        || remaining_bytes <= policy.max_total_bytes
                    {
                        break;
                    }
                    if selected.contains(&manifest.segment_id) {
                        continue;
                    }
                    if deletions.len() == limit {
                        deletion_limit_reached = true;
                        break 'cap_families;
                    }
                    push_candidate(
                        manifest,
                        DeletionReason::FamilyByteCap,
                        &mut selected,
                        &mut deletions,
                        &mut reclaimed,
                    )?;
                    remaining_count -= 1;
                    remaining_bytes = remaining_bytes
                        .checked_sub(manifest.stored_bytes)
                        .ok_or_else(|| {
                            StorageError::Integrity("family byte total underflow".to_owned())
                        })?;
                }
            }
        }
    } else {
        // Under pressure the family priority is strict, including expiration
        // and family-cap work, so debug is always reclaimed before chat/audit.
        'families: for family in [
            SegmentFamily::Debug,
            SegmentFamily::Chat,
            SegmentFamily::Audit,
        ] {
            let family_manifests = by_family.entry(family).or_default();
            let policy = policies.get(family);
            let mut remaining_count = family_manifests.len();
            let mut remaining_bytes = checked_total_bytes(family_manifests)?;

            for manifest in family_manifests.iter() {
                if remaining_count <= policy.minimum_segments {
                    break;
                }
                let age_ms = now_ms.saturating_sub(manifest.sealed_at_ms);
                let expired = u64::try_from(age_ms)
                    .map(|age| age >= policy.retention_age_ms)
                    .unwrap_or(false);
                if expired {
                    if deletions.len() == limit {
                        deletion_limit_reached = true;
                        break 'families;
                    }
                    push_candidate(
                        manifest,
                        DeletionReason::Expired,
                        &mut selected,
                        &mut deletions,
                        &mut reclaimed,
                    )?;
                    remaining_count -= 1;
                    remaining_bytes = remaining_bytes
                        .checked_sub(manifest.stored_bytes)
                        .ok_or_else(|| {
                            StorageError::Integrity("family byte total underflow".to_owned())
                        })?;
                }
            }

            if remaining_bytes > policy.max_total_bytes {
                for manifest in family_manifests.iter() {
                    if remaining_count <= policy.minimum_segments
                        || remaining_bytes <= policy.max_total_bytes
                    {
                        break;
                    }
                    if selected.contains(&manifest.segment_id) {
                        continue;
                    }
                    if deletions.len() == limit {
                        deletion_limit_reached = true;
                        break 'families;
                    }
                    push_candidate(
                        manifest,
                        DeletionReason::FamilyByteCap,
                        &mut selected,
                        &mut deletions,
                        &mut reclaimed,
                    )?;
                    remaining_count -= 1;
                    remaining_bytes = remaining_bytes
                        .checked_sub(manifest.stored_bytes)
                        .ok_or_else(|| {
                            StorageError::Integrity("family byte total underflow".to_owned())
                        })?;
                }
            }

            if disk.available_bytes.saturating_add(reclaimed) < recovery_available {
                for manifest in family_manifests.iter() {
                    if remaining_count <= policy.minimum_segments
                        || disk.available_bytes.saturating_add(reclaimed) >= recovery_available
                    {
                        break;
                    }
                    if selected.contains(&manifest.segment_id) {
                        continue;
                    }
                    if deletions.len() == limit {
                        deletion_limit_reached = true;
                        break 'families;
                    }
                    push_candidate(
                        manifest,
                        DeletionReason::DiskPressure,
                        &mut selected,
                        &mut deletions,
                        &mut reclaimed,
                    )?;
                    remaining_count -= 1;
                }
            }
        }
    }

    let projected_available_bytes = disk
        .available_bytes
        .saturating_add(reclaimed)
        .min(disk.total_bytes);
    Ok(CleanupPlan {
        pressure,
        deletions,
        projected_available_bytes,
        pause_background: pressure != DiskPressure::Normal,
        allow_disposable_writes: pressure != DiskPressure::Critical,
        deletion_limit_reached,
    })
}

fn classify_pressure(
    used_basis_points: u16,
    watermarks: WatermarkPolicy,
    previous_pressure: Option<DiskPressure>,
) -> DiskPressure {
    let threshold_pressure = if used_basis_points >= watermarks.critical_basis_points {
        DiskPressure::Critical
    } else if used_basis_points >= watermarks.high_basis_points {
        DiskPressure::High
    } else {
        DiskPressure::Normal
    };
    if used_basis_points <= watermarks.low_recovery_basis_points {
        return threshold_pressure;
    }
    match (previous_pressure, threshold_pressure) {
        (Some(DiskPressure::Critical), _) => DiskPressure::Critical,
        (Some(DiskPressure::High), DiskPressure::Normal | DiskPressure::High) => DiskPressure::High,
        (_, pressure) => pressure,
    }
}

fn ratio_basis_points(numerator: u64, denominator: u64) -> Result<u16, StorageError> {
    let value = u128::from(numerator)
        .checked_mul(u128::from(BASIS_POINTS))
        .and_then(|scaled| scaled.checked_div(u128::from(denominator)))
        .ok_or_else(|| StorageError::Integrity("disk ratio arithmetic failed".to_owned()))?;
    u16::try_from(value)
        .map_err(|_| StorageError::Integrity("disk ratio does not fit basis points".to_owned()))
}

fn recovery_available_bytes(
    total_bytes: u64,
    watermarks: WatermarkPolicy,
) -> Result<u64, StorageError> {
    let available_basis_points = BASIS_POINTS - u64::from(watermarks.low_recovery_basis_points);
    let numerator = u128::from(total_bytes)
        .checked_mul(u128::from(available_basis_points))
        .ok_or_else(|| StorageError::Integrity("recovery target overflow".to_owned()))?;
    let rounded = numerator
        .checked_add(u128::from(BASIS_POINTS - 1))
        .and_then(|value| value.checked_div(u128::from(BASIS_POINTS)))
        .ok_or_else(|| StorageError::Integrity("recovery target arithmetic failed".to_owned()))?;
    u64::try_from(rounded)
        .map_err(|_| StorageError::Integrity("recovery target is too large".to_owned()))
}

fn grouped_sorted_manifests(
    manifests: &[SegmentManifest],
) -> Result<BTreeMap<SegmentFamily, Vec<SegmentManifest>>, StorageError> {
    let mut seen = HashSet::new();
    let mut grouped = BTreeMap::new();
    for manifest in manifests {
        if !seen.insert(manifest.segment_id.clone()) {
            return Err(StorageError::Integrity(format!(
                "duplicate manifest ID {}",
                manifest.segment_id
            )));
        }
        grouped
            .entry(manifest.family)
            .or_insert_with(Vec::new)
            .push(manifest.clone());
    }
    for family_manifests in grouped.values_mut() {
        family_manifests.sort_by(|left, right| {
            left.sealed_at_ms
                .cmp(&right.sealed_at_ms)
                .then_with(|| left.segment_id.cmp(&right.segment_id))
        });
    }
    Ok(grouped)
}

fn checked_total_bytes(manifests: &[SegmentManifest]) -> Result<u64, StorageError> {
    manifests.iter().try_fold(0_u64, |total, manifest| {
        total
            .checked_add(manifest.stored_bytes)
            .ok_or_else(|| StorageError::Integrity("family byte total overflow".to_owned()))
    })
}

fn push_candidate(
    manifest: &SegmentManifest,
    reason: DeletionReason,
    selected: &mut HashSet<String>,
    deletions: &mut Vec<DeletionCandidate>,
    reclaimed: &mut u64,
) -> Result<(), StorageError> {
    if !selected.insert(manifest.segment_id.clone()) {
        return Ok(());
    }
    *reclaimed = reclaimed
        .checked_add(manifest.stored_bytes)
        .ok_or_else(|| StorageError::Integrity("reclaimed byte total overflow".to_owned()))?;
    deletions.push(DeletionCandidate {
        manifest: manifest.clone(),
        reason,
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::storage::SegmentPolicy;

    fn family_policy(max_total_bytes: u64, minimum_segments: usize) -> SegmentPolicy {
        SegmentPolicy {
            retention_age_ms: 1_000,
            max_total_bytes,
            target_segment_bytes: 148,
            max_record_bytes: 64,
            minimum_segments,
            persist: true,
            compress: false,
        }
    }

    fn policies(max_total_bytes: u64, minimum_segments: usize) -> SegmentPolicies {
        let policy = family_policy(max_total_bytes, minimum_segments);
        SegmentPolicies::new(policy.clone(), policy.clone(), policy).unwrap()
    }

    fn manifest(id: &str, family: SegmentFamily, bytes: u64, sealed_at_ms: i64) -> SegmentManifest {
        SegmentManifest {
            segment_id: id.to_owned(),
            family,
            relative_path: format!("{id}.segment"),
            record_count: 1,
            uncompressed_bytes: bytes,
            stored_bytes: bytes,
            content_sha256: "content".to_owned(),
            file_sha256: "file".to_owned(),
            created_at_ms: sealed_at_ms - 1,
            sealed_at_ms,
            compression: None,
        }
    }

    fn watermarks(limit: usize) -> WatermarkPolicy {
        WatermarkPolicy {
            low_recovery_basis_points: 7_000,
            high_basis_points: 8_000,
            critical_basis_points: 9_000,
            max_deletions_per_run: limit,
        }
    }

    #[test]
    fn normal_cleanup_expires_then_caps_without_crossing_minimum() {
        let manifests = vec![
            manifest("chat-old", SegmentFamily::Chat, 100, 0),
            manifest("chat-mid", SegmentFamily::Chat, 100, 9_500),
            manifest("chat-new", SegmentFamily::Chat, 100, 9_600),
        ];
        let plan = plan_cleanup(
            &manifests,
            &policies(150, 1),
            DiskSnapshot {
                total_bytes: 1_000,
                available_bytes: 500,
            },
            watermarks(10),
            10_000,
        )
        .unwrap();

        assert_eq!(plan.pressure, DiskPressure::Normal);
        assert_eq!(plan.deletions.len(), 2);
        assert_eq!(plan.deletions[0].manifest.segment_id, "chat-old");
        assert_eq!(plan.deletions[0].reason, DeletionReason::Expired);
        assert_eq!(plan.deletions[1].reason, DeletionReason::FamilyByteCap);
        assert!(!plan.pause_background);
        assert!(plan.allow_disposable_writes);
    }

    #[test]
    fn normal_cleanup_prioritizes_expiration_globally_before_family_caps() {
        let manifests = vec![
            manifest("chat-cap-old", SegmentFamily::Chat, 100, 9_500),
            manifest("chat-cap-new", SegmentFamily::Chat, 100, 9_600),
            manifest("audit-expired", SegmentFamily::Audit, 100, 0),
        ];
        let plan = plan_cleanup(
            &manifests,
            &policies(150, 0),
            DiskSnapshot {
                total_bytes: 1_000,
                available_bytes: 500,
            },
            watermarks(1),
            10_000,
        )
        .unwrap();

        assert_eq!(plan.pressure, DiskPressure::Normal);
        assert_eq!(plan.deletions.len(), 1);
        assert_eq!(plan.deletions[0].manifest.segment_id, "audit-expired");
        assert_eq!(plan.deletions[0].reason, DeletionReason::Expired);
        assert!(plan.deletion_limit_reached);
    }

    #[test]
    fn high_pressure_reclaims_debug_then_chat_then_audit() {
        let manifests = vec![
            manifest("audit-1", SegmentFamily::Audit, 100, 9_500),
            manifest("audit-2", SegmentFamily::Audit, 100, 9_600),
            manifest("chat-1", SegmentFamily::Chat, 100, 9_500),
            manifest("chat-2", SegmentFamily::Chat, 100, 9_600),
            manifest("debug-1", SegmentFamily::Debug, 100, 9_500),
            manifest("debug-2", SegmentFamily::Debug, 100, 9_600),
        ];
        let plan = plan_cleanup(
            &manifests,
            &policies(10_000, 0),
            DiskSnapshot {
                total_bytes: 1_000,
                available_bytes: 150,
            },
            watermarks(10),
            10_000,
        )
        .unwrap();

        assert_eq!(plan.pressure, DiskPressure::High);
        assert_eq!(
            plan.deletions
                .iter()
                .map(|candidate| candidate.manifest.family)
                .collect::<Vec<_>>(),
            vec![SegmentFamily::Debug, SegmentFamily::Debug]
        );
        assert!(plan.pause_background);
        assert!(plan.allow_disposable_writes);
        assert_eq!(plan.projected_available_bytes, 350);
    }

    #[test]
    fn high_pressure_prioritizes_expired_debug_before_expired_chat() {
        let manifests = vec![
            manifest("chat-expired", SegmentFamily::Chat, 100, 0),
            manifest("debug-expired", SegmentFamily::Debug, 100, 0),
        ];
        let plan = plan_cleanup(
            &manifests,
            &policies(10_000, 0),
            DiskSnapshot {
                total_bytes: 1_000,
                available_bytes: 150,
            },
            watermarks(1),
            10_000,
        )
        .unwrap();

        assert_eq!(plan.pressure, DiskPressure::High);
        assert_eq!(plan.deletions.len(), 1);
        assert_eq!(plan.deletions[0].manifest.segment_id, "debug-expired");
        assert_eq!(plan.deletions[0].reason, DeletionReason::Expired);
        assert!(plan.deletion_limit_reached);
    }

    #[test]
    fn pressure_hysteresis_holds_high_and_critical_until_low_recovery() {
        let cases = [
            (
                Some(DiskPressure::High),
                210,
                DiskPressure::High,
                "high above low",
            ),
            (
                Some(DiskPressure::High),
                300,
                DiskPressure::Normal,
                "high at low",
            ),
            (
                Some(DiskPressure::Critical),
                150,
                DiskPressure::Critical,
                "critical at high-only threshold",
            ),
            (
                Some(DiskPressure::Critical),
                300,
                DiskPressure::Normal,
                "critical at low",
            ),
        ];

        for (previous, available_bytes, expected, label) in cases {
            let plan = plan_cleanup_with_previous(
                &[],
                &policies(10_000, 0),
                DiskSnapshot {
                    total_bytes: 1_000,
                    available_bytes,
                },
                watermarks(10),
                10_000,
                previous,
            )
            .unwrap();

            assert_eq!(plan.pressure, expected, "{label}");
        }
    }

    #[test]
    fn deletion_limit_flag_requires_an_additional_eligible_candidate() {
        let one_candidate = vec![manifest("chat-only", SegmentFamily::Chat, 100, 0)];
        let exact_plan = plan_cleanup(
            &one_candidate,
            &policies(10_000, 0),
            DiskSnapshot {
                total_bytes: 1_000,
                available_bytes: 500,
            },
            watermarks(1),
            10_000,
        )
        .unwrap();

        assert_eq!(exact_plan.deletions.len(), 1);
        assert!(!exact_plan.deletion_limit_reached);

        let two_candidates = vec![
            manifest("chat-first", SegmentFamily::Chat, 100, 0),
            manifest("chat-second", SegmentFamily::Chat, 100, 1),
        ];
        let limited_plan = plan_cleanup(
            &two_candidates,
            &policies(10_000, 0),
            DiskSnapshot {
                total_bytes: 1_000,
                available_bytes: 500,
            },
            watermarks(1),
            10_000,
        )
        .unwrap();

        assert_eq!(limited_plan.deletions.len(), 1);
        assert!(limited_plan.deletion_limit_reached);
    }

    #[test]
    fn critical_pressure_blocks_disposable_writes_and_bounds_deletes() {
        let manifests = vec![
            manifest("debug-1", SegmentFamily::Debug, 100, 9_500),
            manifest("debug-2", SegmentFamily::Debug, 100, 9_600),
            manifest("debug-3", SegmentFamily::Debug, 100, 9_700),
        ];
        let plan = plan_cleanup(
            &manifests,
            &policies(10_000, 0),
            DiskSnapshot {
                total_bytes: 1_000,
                available_bytes: 50,
            },
            watermarks(1),
            10_000,
        )
        .unwrap();

        assert_eq!(plan.pressure, DiskPressure::Critical);
        assert_eq!(plan.deletions.len(), 1);
        assert!(plan.deletion_limit_reached);
        assert!(!plan.allow_disposable_writes);
    }

    #[test]
    fn minimum_segment_count_can_prevent_pressure_reclamation() {
        let manifests = vec![manifest("debug-1", SegmentFamily::Debug, 100, 9_500)];
        let plan = plan_cleanup(
            &manifests,
            &policies(10_000, 1),
            DiskSnapshot {
                total_bytes: 1_000,
                available_bytes: 100,
            },
            watermarks(10),
            10_000,
        )
        .unwrap();

        assert!(plan.deletions.is_empty());
        assert_eq!(plan.projected_available_bytes, 100);
    }
}
