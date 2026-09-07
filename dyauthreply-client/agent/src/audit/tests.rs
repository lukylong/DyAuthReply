use super::*;
use crate::store::{CoreStore, OutboundSegmentDraft, SegmentTransition};
fn lease(core: &CoreStore) -> crate::store::LeaseToken {
    core.install_verified_account_lease(
        "a",
        "instance",
        "boot",
        1,
        crate::workbench::now_ms() + 60000,
    )
    .unwrap()
    .token()
}
#[test]
fn outbox_projection_repairs_and_preserves_unknown_without_counting_segments_twice() {
    let root = tempfile::tempdir().unwrap();
    let core = CoreStore::open(root.path()).unwrap();
    core.enable_audit_journal().unwrap();
    let lease = lease(&core);
    let audit = AuditStore::open(root.path()).unwrap();
    let batch = core
        .prepare_outbound_batch(
            &lease,
            "auto:123",
            r#"{"conversation_id":"c","rule_id":"r"}"#,
            &[
                OutboundSegmentDraft::text("first"),
                OutboundSegmentDraft::text("second"),
            ],
        )
        .unwrap();
    audit.sync(&core).unwrap();
    assert_eq!(
        audit.stats(None, "automatic", "today").unwrap()["pending"],
        1
    );
    core.transition_segment(
        &lease,
        &batch.segments[0].id,
        SegmentTransition::StartAttempt,
    )
    .unwrap();
    audit.sync(&core).unwrap();
    assert_eq!(
        audit.stats(None, "automatic", "today").unwrap()["uncertain"],
        1
    );
    core.transition_segment(
        &lease,
        &batch.segments[0].id,
        SegmentTransition::Confirm {
            platform_message_id: "123".into(),
        },
    )
    .unwrap();
    core.transition_segment(
        &lease,
        &batch.segments[1].id,
        SegmentTransition::StartAttempt,
    )
    .unwrap();
    core.transition_segment(
        &lease,
        &batch.segments[1].id,
        SegmentTransition::Reject {
            error: "blocked".into(),
        },
    )
    .unwrap();
    for _ in 0..3 {
        audit.sync(&core).unwrap();
    }
    let stat = audit.stats(None, "automatic", "today").unwrap();
    assert_eq!(stat["total"], 1);
    assert_eq!(stat["partial"], 1);
    assert_eq!(stat["failed"], 0);
    assert_eq!(
        core.outbound_batch(&batch.id).unwrap().segments[0].attempt_count,
        1
    );
}
#[test]
fn retention_never_removes_outbox_or_changes_receipt_evidence() {
    let root = tempfile::tempdir().unwrap();
    let core = CoreStore::open(root.path()).unwrap();
    core.enable_audit_journal().unwrap();
    let lease = lease(&core);
    let audit = AuditStore::open(root.path()).unwrap();
    let b = core
        .prepare_outbound_batch(
            &lease,
            "manual:request",
            r#"{"conversation_id":"c"}"#,
            &[OutboundSegmentDraft::text("message")],
        )
        .unwrap();
    audit.sync(&core).unwrap();
    audit
        .lock()
        .unwrap()
        .execute("UPDATE records SET created=0", [])
        .unwrap();
    audit.maintain().unwrap();
    assert_eq!(audit.list(None, "all", None, 1, 50).unwrap()["total"], 0);
    assert_eq!(
        core.outbound_batch(&b.id).unwrap().segments[0].payload,
        "message"
    );
}
#[test]
fn failed_decision_and_replays_never_publish_false_skips() {
    let root = tempfile::tempdir().unwrap();
    let core = CoreStore::open(root.path()).unwrap();
    core.enable_audit_journal().unwrap();
    let lease = lease(&core);
    let payload=serde_json::to_vec(&json!({"version":1,"server_message_id":"123","conversation_id":"c","conversation_short_id":"456","sender_uid":"1","sender_sec_uid":"peer","client_message_id":"","message_type":1,"create_time_us":crate::workbench::now_ms()*1000,"content_json":"{\"text\":\"hi\"}","text":"hi"})).unwrap();
    core.record_inbound_page(
        &lease,
        "receive",
        1,
        1,
        &[crate::store::InboundReceiptDraft {
            event_id: "123".into(),
            payload,
            payload_hash: "hash".into(),
        }],
    )
    .unwrap();
    let key = |hash| crate::store::InboundReceiptKey {
        stream: "receive",
        generation: 1,
        event_id: "123",
        payload_hash: hash,
    };
    let info = crate::store::DecisionAudit {
        result: "skipped",
        reason: "no match",
        rule_id: None,
    };
    assert!(core
        .consume_inbound_audited(&lease, key("bad"), info)
        .is_err());
    assert!(core.audit_changes(0, 256).unwrap().records.is_empty());
    for _ in 0..3 {
        core.consume_inbound_audited(&lease, key("hash"), info)
            .unwrap();
    }
    let changes = core.audit_changes(0, 256).unwrap();
    assert_eq!(changes.records.len(), 1);
    assert_eq!(changes.records[0].result, "skipped");
}

#[test]
fn bounded_journal_gap_recovers_outbox_and_reports_rotation() {
    let root = tempfile::tempdir().unwrap();
    let core = CoreStore::open(root.path()).unwrap();
    core.enable_audit_journal().unwrap();
    let lease = lease(&core);
    let audit = AuditStore::open(root.path()).unwrap();
    let b = core
        .prepare_outbound_batch(
            &lease,
            "manual:gap",
            r#"{"conversation_id":"c"}"#,
            &[OutboundSegmentDraft::text("unchanged")],
        )
        .unwrap();
    audit.sync(&core).unwrap();
    let mut c = Connection::open(core.database_path()).unwrap();
    let tx = c.transaction().unwrap();
    for _ in 0..4100 {
        tx.execute(
            "UPDATE outbound_batches SET updated_at_ms=updated_at_ms+1 WHERE id=?1",
            [&b.id],
        )
        .unwrap();
    }
    tx.commit().unwrap();
    let count: i64 = c
        .query_row("SELECT count(*) FROM audit_changes", [], |r| r.get(0))
        .unwrap();
    assert_eq!(count, 4096);
    audit.sync(&core).unwrap();
    assert_eq!(audit.stats(None, "manual", "all").unwrap()["total"], 1);
    assert_eq!(
        audit.list(None, "all", None, 1, 50).unwrap()["has_gap"],
        true
    );
    assert_eq!(
        core.outbound_batch(&b.id).unwrap().segments[0].attempt_count,
        0
    );
}
#[test]
fn source_identity_change_discards_only_stale_native_projection() {
    let first = tempfile::tempdir().unwrap();
    let second = tempfile::tempdir().unwrap();
    let auditdir = tempfile::tempdir().unwrap();
    let core = CoreStore::open(first.path()).unwrap();
    core.enable_audit_journal().unwrap();
    let lease = lease(&core);
    let audit = AuditStore::open(auditdir.path()).unwrap();
    core.prepare_outbound_batch(
        &lease,
        "manual:first",
        "{}",
        &[OutboundSegmentDraft::text("first")],
    )
    .unwrap();
    audit.sync(&core).unwrap();
    assert_eq!(audit.stats(None, "all", "all").unwrap()["total"], 1);
    let replacement = CoreStore::open(second.path()).unwrap();
    replacement.enable_audit_journal().unwrap();
    audit.sync(&replacement).unwrap();
    assert_eq!(audit.stats(None, "all", "all").unwrap()["total"], 0);
}
#[test]
fn legacy_import_is_idempotent_and_daily_counts_survive_detail_eviction() {
    let dir = tempfile::tempdir().unwrap();
    let source = dir.path().join("legacy.sqlite3");
    let c = Connection::open(&source).unwrap();
    c.execute_batch("CREATE TABLE core_douyin_reply_log(id TEXT,account_id TEXT,conversation_id TEXT,matched_rule_id TEXT,reply_text TEXT,reply_links TEXT,result TEXT,error_message TEXT,duration_ms INTEGER,sent_at TEXT,sys_create_datetime TEXT,trigger_message_id TEXT,is_deleted INTEGER);
CREATE TABLE core_douyin_conversation(id TEXT,platform_conversation_id TEXT);CREATE TABLE core_douyin_message(id TEXT,content TEXT);").unwrap();
    let time = chrono::DateTime::from_timestamp_millis(crate::workbench::now_ms())
        .unwrap()
        .with_timezone(&chrono_tz::Asia::Shanghai)
        .format("%Y-%m-%d %H:%M:%S")
        .to_string();
    c.execute("INSERT INTO core_douyin_reply_log VALUES('log','a',NULL,NULL,'hello','[]','success',NULL,20,?1,?1,NULL,0)",[&time]).unwrap();
    drop(c);
    let before = std::fs::read(&source).unwrap();
    let audit = AuditStore::open(dir.path()).unwrap();
    let first = audit.import_legacy(&source).unwrap();
    assert_eq!(audit.import_legacy(&source).unwrap(), first);
    assert_eq!(audit.legacy_today().unwrap()["a"], 1);
    audit
        .lock()
        .unwrap()
        .execute("DELETE FROM records", [])
        .unwrap();
    assert_eq!(audit.legacy_today().unwrap()["a"], 1);
    assert_eq!(std::fs::read(source).unwrap(), before);
}
#[test]
fn deferred_observation_cannot_overwrite_confirmed_batch() {
    let root = tempfile::tempdir().unwrap();
    let core = CoreStore::open(root.path()).unwrap();
    core.enable_audit_journal().unwrap();
    let lease = lease(&core);
    let audit = AuditStore::open(root.path()).unwrap();
    let b = core
        .prepare_outbound_batch(
            &lease,
            "auto:999",
            r#"{"conversation_id":"c"}"#,
            &[OutboundSegmentDraft::text("reply")],
        )
        .unwrap();
    core.transition_segment(&lease, &b.segments[0].id, SegmentTransition::StartAttempt)
        .unwrap();
    core.transition_segment(
        &lease,
        &b.segments[0].id,
        SegmentTransition::Confirm {
            platform_message_id: "321".into(),
        },
    )
    .unwrap();
    audit.sync(&core).unwrap();
    let mut db = audit.lock().unwrap();
    let tx = db.transaction().unwrap();
    let raw: String = tx
        .query_row("SELECT record FROM records", [], |r| r.get(0))
        .unwrap();
    let mut observation: Record = serde_json::from_str(&raw).unwrap();
    observation.batch_id = None;
    observation.result = "silent".into();
    assert!(!upsert(&tx, &observation).unwrap());
    tx.commit().unwrap();
    drop(db);
    assert_eq!(audit.stats(None, "automatic", "all").unwrap()["success"], 1);
}

#[test]
fn audit_byte_budget_rotates_details_without_affecting_native_batch() {
    let root = tempfile::tempdir().unwrap();
    let core = CoreStore::open(root.path()).unwrap();
    core.enable_audit_journal().unwrap();
    let lease = lease(&core);
    let audit = AuditStore::open(root.path()).unwrap();
    let b = core
        .prepare_outbound_batch(
            &lease,
            "manual:bytes",
            "{}",
            &[OutboundSegmentDraft::text("original")],
        )
        .unwrap();
    let mut record = core.audit_snapshot_page(0, None, 1).unwrap().pop().unwrap();
    record.reply_text = "x".repeat(12000);
    let mut db = audit.lock().unwrap();
    let tx = db.transaction().unwrap();
    for i in 0..3000 {
        record.id = format!("fixture-{i}");
        upsert(&tx, &record).unwrap();
    }
    tx.commit().unwrap();
    let bytes: i64 = db
        .query_row(
            "SELECT CAST(value AS INTEGER) FROM meta WHERE key='bytes'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!(bytes <= 32 * 1024 * 1024);
    assert!(
        db.query_row("SELECT count(*) FROM records", [], |r| r.get::<_, i64>(0))
            .unwrap()
            < 3000
    );
    drop(db);
    assert_eq!(
        core.outbound_batch(&b.id).unwrap().segments[0].payload,
        "original"
    );
}
