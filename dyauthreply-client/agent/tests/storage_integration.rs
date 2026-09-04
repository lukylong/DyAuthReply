use std::{
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};

use dy_agent::{
    storage::{
        retention::{plan_cleanup, DiskPressure, DiskSnapshot, WatermarkPolicy},
        AppendOutcome, SegmentCatalog, SegmentFamily, SegmentPolicies, SegmentPolicy, SegmentStore,
        SkipReason,
    },
    store::{CoreStore, InboundReceiptDraft},
};

fn policies() -> SegmentPolicies {
    let policy = SegmentPolicy {
        retention_age_ms: 1,
        max_total_bytes: 1024,
        target_segment_bytes: 256,
        max_record_bytes: 128,
        minimum_segments: 0,
        persist: true,
        compress: false,
    };
    SegmentPolicies::new(policy.clone(), policy.clone(), policy).expect("valid test policy")
}

#[test]
fn critical_projection_cleanup_keeps_core_correctness_operational() {
    let directory = tempfile::tempdir().expect("temporary Agent directory");
    let core = Arc::new(CoreStore::open(directory.path()).expect("open core store"));
    let catalog: Arc<dyn SegmentCatalog> = core.clone();
    let policies = policies();
    let mut segments =
        SegmentStore::open(directory.path().join("segments"), policies.clone(), catalog)
            .expect("open rolling segments");

    let body = [0_u8, 0xff, b'\n', 0x80];
    segments
        .append(SegmentFamily::Chat, &body, 1, true)
        .expect("append chat body");
    let manifest = segments
        .seal_family(SegmentFamily::Chat, 2)
        .expect("seal chat body")
        .expect("non-empty segment");
    assert_eq!(
        core.segment_manifest(&manifest.segment_id).unwrap(),
        Some(manifest.clone())
    );

    let mut round_trip = Vec::new();
    segments
        .visit_records(&manifest, &mut |record| {
            round_trip.push(record.to_vec());
            Ok(())
        })
        .expect("read sealed body");
    assert_eq!(round_trip, vec![body.to_vec()]);

    let plan = plan_cleanup(
        &[manifest],
        &policies,
        DiskSnapshot {
            total_bytes: 1000,
            available_bytes: 10,
        },
        WatermarkPolicy {
            low_recovery_basis_points: 7000,
            high_basis_points: 8000,
            critical_basis_points: 9000,
            max_deletions_per_run: 8,
        },
        10,
    )
    .expect("plan critical cleanup");
    assert_eq!(plan.pressure, DiskPressure::Critical);
    assert!(!plan.allow_disposable_writes);
    assert_eq!(segments.apply_cleanup(&plan).unwrap(), 1);
    assert!(core.segment_manifests(None, None).unwrap().is_empty());
    assert_eq!(
        segments
            .append(SegmentFamily::Debug, b"discardable", 11, false)
            .unwrap(),
        AppendOutcome::Skipped(SkipReason::CriticalDiskPressure)
    );

    let now_ms = i64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time after epoch")
            .as_millis(),
    )
    .expect("test time fits i64");
    let lease = core
        .install_verified_account_lease(
            "account-storage-test",
            "instance-storage-test",
            "boot-storage-test",
            1,
            now_ms + 60_000,
        )
        .expect("install already-verified test lease");
    let receipt = InboundReceiptDraft {
        event_id: "event-storage-test".to_owned(),
        payload: b"correctness payload".to_vec(),
        payload_hash: "synthetic-digest".to_owned(),
    };
    let result = core
        .record_inbound_page(&lease.token(), "test", 1, 1, &[receipt])
        .expect("critical pressure must not block correctness transaction");
    assert_eq!(result.inserted_count, 1);
    assert!(core.database_integrity().unwrap().is_valid());
}
