use super::*;
const NOW: i64 = 1_700_000_000_000;
fn fixture() -> (tempfile::TempDir, CoreStore, LeaseToken) {
    let dir = tempfile::tempdir().unwrap();
    let store = CoreStore::open(dir.path()).unwrap();
    let token = store
        .install_verified_account_lease_at("account", "instance", "boot", 1, NOW, NOW + 300_000_000)
        .unwrap()
        .token();
    (dir, store, token)
}
fn policy() -> GuardPolicy {
    GuardPolicy {
        rule_id: "rule".into(),
        conversation_id: "conversation".into(),
        peer_id: "peer".into(),
        timezone: "Asia/Shanghai".into(),
        daily_quota: 10,
        cooldown_ms: 0,
        minimum_interval_ms: 0,
        daily_peer_limit: false,
    }
}
fn receipt(store: &CoreStore, token: &LeaseToken, id: &str, time: i64) {
    store
        .record_inbound_page_at(
            token,
            "http",
            1,
            time,
            &[InboundReceiptDraft {
                event_id: id.into(),
                payload: b"body".to_vec(),
                payload_hash: "hash".into(),
            }],
            time,
        )
        .unwrap();
}
fn claim(
    store: &CoreStore,
    token: &LeaseToken,
    id: &str,
    policy: &GuardPolicy,
    time: i64,
    count: usize,
) -> Result<OutboundBatch, StoreError> {
    let segments: Vec<_> = (0..count)
        .map(|i| OutboundSegmentDraft::text(format!("reply{i}")))
        .collect();
    store
        .consume_inbound_with_guards(
            token,
            InboundReceiptKey {
                stream: "http",
                generation: 1,
                event_id: id,
                payload_hash: "hash",
            },
            Some(InboundReplyPlan {
                response_id: "route",
                segments: &segments,
                pending_batch_limit: 32,
            }),
            Some(policy),
            OperationTime::Fixed(time),
            None,
        )
        .map(|r| r.batch.unwrap())
}
fn confirm(store: &CoreStore, token: &LeaseToken, batch: &OutboundBatch, index: usize, time: i64) {
    store
        .transition_segment_at(
            token,
            &batch.segments[index].id,
            SegmentTransition::StartAttempt,
            time,
        )
        .unwrap();
    store
        .transition_segment_at(
            token,
            &batch.segments[index].id,
            SegmentTransition::Confirm {
                platform_message_id: format!("server{index}"),
            },
            time + 1,
        )
        .unwrap();
}
fn state(store: &CoreStore) -> (u32, bool) {
    store.lock_connection().unwrap().query_row("SELECT used_count,reserved_batch_id IS NOT NULL FROM reply_guard_scopes WHERE kind='account'",[],|r|Ok((r.get(0)?,r.get(1)?))).unwrap()
}
#[test]
fn competing_claim_is_rolled_back_and_rejection_releases_without_quota_charge() {
    let (_dir, store, token) = fixture();
    let policy = policy();
    receipt(&store, &token, "1", NOW);
    receipt(&store, &token, "2", NOW);
    let first = claim(&store, &token, "1", &policy, NOW, 1).unwrap();
    assert!(matches!(
        claim(&store, &token, "2", &policy, NOW, 1),
        Err(StoreError::ReplyGuardBlocked {
            reason: "pending_reply",
            ..
        })
    ));
    assert!(store
        .outbound_batch_for_trigger("account", "auto:2")
        .unwrap()
        .is_none());
    assert_eq!(state(&store), (0, true));
    store
        .transition_segment_at(
            &token,
            &first.segments[0].id,
            SegmentTransition::CancelPrepared {
                reason: "test cancellation".into(),
            },
            NOW + 1,
        )
        .unwrap();
    assert_eq!(state(&store), (0, false));
    assert!(claim(&store, &token, "2", &policy, NOW + 2, 1).is_ok());
}
#[test]
fn daily_account_quota_charges_once_and_new_day_resets_count() {
    let (_dir, store, token) = fixture();
    let mut policy = policy();
    policy.daily_quota = 1;
    receipt(&store, &token, "1", NOW);
    let batch = claim(&store, &token, "1", &policy, NOW, 1).unwrap();
    confirm(&store, &token, &batch, 0, NOW + 1);
    assert_eq!(state(&store), (1, false));
    store
        .transition_segment_at(
            &token,
            &batch.segments[0].id,
            SegmentTransition::Confirm {
                platform_message_id: "server0".into(),
            },
            NOW + 3,
        )
        .unwrap();
    assert_eq!(state(&store), (1, false));
    receipt(&store, &token, "2", NOW + 4);
    assert!(matches!(
        claim(&store, &token, "2", &policy, NOW + 4, 1),
        Err(StoreError::ReplyGuardBlocked {
            reason: "account_daily_quota",
            ..
        })
    ));
    let tomorrow = NOW + 86_400_000;
    let next = claim(&store, &token, "2", &policy, tomorrow, 1).unwrap();
    confirm(&store, &token, &next, 0, tomorrow + 1);
    assert_eq!(state(&store), (1, false));
}
#[test]
fn cooldown_and_account_interval_have_distinct_retry_times() {
    let (_dir, store, token) = fixture();
    let mut policy = policy();
    policy.cooldown_ms = 1000;
    receipt(&store, &token, "1", NOW);
    let first = claim(&store, &token, "1", &policy, NOW, 1).unwrap();
    confirm(&store, &token, &first, 0, NOW + 1);
    receipt(&store, &token, "2", NOW + 3);
    assert!(
        matches!(claim(&store,&token,"2",&policy,NOW+3,1),Err(StoreError::ReplyGuardBlocked{reason:"rule_cooldown",retry_at_ms:Some(t)}) if t==NOW+1002)
    );
    policy.rule_id = "different-rule".into();
    policy.minimum_interval_ms = 2000;
    assert!(
        matches!(claim(&store,&token,"2",&policy,NOW+4,1),Err(StoreError::ReplyGuardBlocked{reason:"account_interval",retry_at_ms:Some(t)}) if t==NOW+2002)
    );
    assert!(claim(&store, &token, "2", &policy, NOW + 2002, 1).is_ok());
}
#[test]
fn peer_history_is_retained_even_while_optional_peer_limiter_is_off() {
    let (_dir, store, token) = fixture();
    let mut policy = policy();
    receipt(&store, &token, "1", NOW);
    let first = claim(&store, &token, "1", &policy, NOW, 1).unwrap();
    confirm(&store, &token, &first, 0, NOW + 1);
    receipt(&store, &token, "2", NOW + 3);
    policy.daily_peer_limit = true;
    assert!(matches!(
        claim(&store, &token, "2", &policy, NOW + 3, 1),
        Err(StoreError::ReplyGuardBlocked {
            reason: "peer_daily_quota",
            ..
        })
    ));
    policy.peer_id = "other-peer".into();
    assert!(claim(&store, &token, "2", &policy, NOW + 4, 1).is_ok());
}
#[test]
fn uncertain_send_keeps_reservation_across_restart_and_fence_transfer() {
    let (dir, store, token) = fixture();
    let policy = policy();
    receipt(&store, &token, "1", NOW);
    let batch = claim(&store, &token, "1", &policy, NOW, 1).unwrap();
    store
        .transition_segment_at(
            &token,
            &batch.segments[0].id,
            SegmentTransition::StartAttempt,
            NOW + 1,
        )
        .unwrap();
    drop(store);
    let store = CoreStore::open(dir.path()).unwrap();
    let next = store
        .install_verified_account_lease_at(
            "account",
            "instance",
            "boot2",
            2,
            NOW + 2,
            NOW + 300_000_000,
        )
        .unwrap()
        .token();
    assert_eq!(
        store.outbound_batch(&batch.id).unwrap().segments[0].status,
        SegmentStatus::Uncertain
    );
    receipt(&store, &next, "2", NOW + 3);
    assert!(matches!(
        claim(&store, &next, "2", &policy, NOW + 3, 1),
        Err(StoreError::ReplyGuardBlocked {
            reason: "pending_reply",
            ..
        })
    ));
    store
        .transition_segment_at(
            &next,
            &batch.segments[0].id,
            SegmentTransition::Confirm {
                platform_message_id: "verified".into(),
            },
            NOW + 4,
        )
        .unwrap();
    assert_eq!(state(&store), (1, false));
}
#[test]
fn partial_delivered_batch_is_charged_once_and_releases_after_all_segments_settle() {
    let (_dir, store, token) = fixture();
    let policy = policy();
    receipt(&store, &token, "1", NOW);
    let batch = claim(&store, &token, "1", &policy, NOW, 2).unwrap();
    confirm(&store, &token, &batch, 0, NOW + 1);
    assert_eq!(state(&store), (0, true));
    store
        .transition_segment_at(
            &token,
            &batch.segments[1].id,
            SegmentTransition::CancelPrepared {
                reason: "second segment rejected".into(),
            },
            NOW + 3,
        )
        .unwrap();
    assert_eq!(state(&store), (1, false));
    assert_eq!(
        store.outbound_batch(&batch.id).unwrap().status,
        BatchStatus::Partial
    );
}
#[test]
fn timezone_change_is_explicitly_blocked_instead_of_resetting_daily_counters() {
    let (_dir, store, token) = fixture();
    let mut policy = policy();
    receipt(&store, &token, "1", NOW);
    let batch = claim(&store, &token, "1", &policy, NOW, 1).unwrap();
    confirm(&store, &token, &batch, 0, NOW + 1);
    policy.timezone = "UTC".into();
    receipt(&store, &token, "2", NOW + 3);
    assert!(matches!(
        claim(&store, &token, "2", &policy, NOW + 3, 1),
        Err(StoreError::ReplyGuardBlocked {
            reason: "timezone_change_requires_migration",
            ..
        })
    ));
}
#[test]
fn quota_reservation_is_atomic_across_independent_sqlite_connections() {
    use std::sync::{Arc, Barrier};
    let (dir, store, token) = fixture();
    receipt(&store, &token, "1", NOW);
    receipt(&store, &token, "2", NOW);
    let other = CoreStore::open(dir.path()).unwrap();
    let barrier = Arc::new(Barrier::new(2));
    let joins: Vec<_> = [(store, "1"), (other, "2")]
        .into_iter()
        .map(|(store, id)| {
            let token = token.clone();
            let barrier = barrier.clone();
            std::thread::spawn(move || {
                barrier.wait();
                claim(&store, &token, id, &policy(), NOW, 1).map(|b| b.id)
            })
        })
        .collect();
    let results: Vec<_> = joins.into_iter().map(|j| j.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|r| r.is_ok()).count(), 1);
    assert!(results.iter().any(|r| matches!(
        r,
        Err(StoreError::ReplyGuardBlocked {
            reason: "pending_reply",
            ..
        })
    )));
}

fn fixture_v2(path: &std::path::Path) -> (Connection, Uuid) {
    let connection = Connection::open(path.join(DATABASE_FILE_NAME)).unwrap();
    connection
        .pragma_update(None, "journal_mode", "WAL")
        .unwrap();
    connection
        .pragma_update(None, "wal_autocheckpoint", 0)
        .unwrap();
    connection
        .execute_batch("CREATE TABLE meta(key TEXT PRIMARY KEY NOT NULL,value TEXT NOT NULL);")
        .unwrap();
    connection.execute_batch(SCHEMA_V1_SQL).unwrap();
    connection.execute_batch(SCHEMA_V2_SQL).unwrap();
    let id = Uuid::new_v4();
    connection
        .execute("INSERT INTO meta VALUES('schema_version','2')", [])
        .unwrap();
    connection
        .execute(
            "INSERT INTO meta VALUES(?1,?2)",
            params![DATABASE_ID_META_KEY, id.to_string()],
        )
        .unwrap();
    connection
        .pragma_update(None, "user_version", SCHEMA_V2)
        .unwrap();
    connection.execute("INSERT INTO inbound_receipts(account_id,stream,stream_generation,event_id,page_checkpoint,payload,payload_hash,status,fence_epoch,received_at_ms)
        VALUES('account','http',1,'1',10,X'010203','hash','pending',1,10)",[]).unwrap();
    (connection, id)
}
#[test]
fn v2_wal_migration_keeps_rows_and_verified_rollback_identity() {
    let dir = tempfile::tempdir().unwrap();
    let (mut source, id) = fixture_v2(dir.path());
    migrate_v2_to_v3(&mut source, dir.path()).unwrap();
    assert_eq!(read_schema_version(&source).unwrap(), SCHEMA_V3);
    let backup = load_migration_backup(&source, SCHEMA_V2, SCHEMA_V3)
        .unwrap()
        .unwrap();
    let restored = Connection::open(dir.path().join(&backup.relative_path)).unwrap();
    assert_eq!(read_schema_version(&restored).unwrap(), SCHEMA_V2);
    assert_eq!(read_database_id(&restored).unwrap(), id);
    assert!(!table_exists(&restored, "auto_reply_batches").unwrap());
    for c in [&source, &restored] {
        let payload: Vec<u8> = c
            .query_row(
                "SELECT payload FROM inbound_receipts WHERE event_id='1'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(payload, vec![1, 2, 3]);
        assert!(inspect_database_integrity(c).unwrap().is_valid());
    }
    drop(restored);
    drop(source);
    let store = CoreStore::open(dir.path()).unwrap();
    assert_eq!(store.guard_migration_backup().unwrap().unwrap(), backup);
}
#[test]
fn failed_v3_migration_rolls_back_tables_and_retry_refreshes_backup() {
    let dir = tempfile::tempdir().unwrap();
    let (mut source, id) = fixture_v2(dir.path());
    source.execute_batch("CREATE TEMP TRIGGER fail_version BEFORE UPDATE OF value ON meta WHEN OLD.key='schema_version' BEGIN SELECT RAISE(ABORT,'fixture migration failure'); END;").unwrap();
    assert!(migrate_v2_to_v3(&mut source, dir.path()).is_err());
    assert_eq!(read_schema_version(&source).unwrap(), SCHEMA_V2);
    assert!(!table_exists(&source, "auto_reply_batches").unwrap());
    let backup_path = dir
        .path()
        .join(format!("backups/core-v2-to-v3-{id}.sqlite3"));
    assert!(backup_path.exists());
    source.execute_batch("DROP TRIGGER fail_version; UPDATE inbound_receipts SET payload=X'040506' WHERE event_id='1';").unwrap();
    migrate_v2_to_v3(&mut source, dir.path()).unwrap();
    let backup = Connection::open(backup_path).unwrap();
    let payload: Vec<u8> = backup
        .query_row(
            "SELECT payload FROM inbound_receipts WHERE event_id='1'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(payload, vec![4, 5, 6]);
}
#[test]
fn guard_schema_drift_is_rejected_on_reopen() {
    let (dir, store, _token) = fixture();
    store
        .lock_connection()
        .unwrap()
        .execute_batch("DROP INDEX reply_guard_scopes_reserved_idx")
        .unwrap();
    drop(store);
    assert!(matches!(
        CoreStore::open(dir.path()),
        Err(StoreError::SchemaInvariant(_))
    ));
}
#[test]
fn final_guard_settlement_failure_rolls_back_segment_confirmation_and_quota() {
    let (_dir, store, token) = fixture();
    let policy = policy();
    receipt(&store, &token, "1", NOW);
    let batch = claim(&store, &token, "1", &policy, NOW, 1).unwrap();
    store
        .transition_segment_at(
            &token,
            &batch.segments[0].id,
            SegmentTransition::StartAttempt,
            NOW + 1,
        )
        .unwrap();
    store.lock_connection().unwrap().execute_batch("CREATE TEMP TRIGGER fail_settle BEFORE UPDATE OF settled ON auto_reply_batches BEGIN SELECT RAISE(ABORT,'fixture settlement failure'); END;").unwrap();
    assert!(store
        .transition_segment_at(
            &token,
            &batch.segments[0].id,
            SegmentTransition::Confirm {
                platform_message_id: "verified".into()
            },
            NOW + 2
        )
        .is_err());
    assert_eq!(state(&store), (0, true));
    assert_eq!(
        store.outbound_batch(&batch.id).unwrap().segments[0].status,
        SegmentStatus::Sending
    );
    store
        .lock_connection()
        .unwrap()
        .execute_batch("DROP TRIGGER fail_settle")
        .unwrap();
    store
        .transition_segment_at(
            &token,
            &batch.segments[0].id,
            SegmentTransition::Confirm {
                platform_message_id: "verified".into(),
            },
            NOW + 3,
        )
        .unwrap();
    assert_eq!(state(&store), (1, false));
}

#[test]
fn terminal_partial_batch_does_not_exhaust_pending_batch_capacity() {
    let (_dir, store, token) = fixture();
    let policy = policy();
    receipt(&store, &token, "1", NOW);
    let batch = claim(&store, &token, "1", &policy, NOW, 2).unwrap();
    confirm(&store, &token, &batch, 0, NOW + 1);
    store
        .transition_segment_at(
            &token,
            &batch.segments[1].id,
            SegmentTransition::CancelPrepared {
                reason: "terminal".into(),
            },
            NOW + 3,
        )
        .unwrap();
    receipt(&store, &token, "2", NOW + 4);
    let segments = [OutboundSegmentDraft::text("next")];
    let result = store.consume_inbound_with_guards(
        &token,
        InboundReceiptKey {
            stream: "http",
            generation: 1,
            event_id: "2",
            payload_hash: "hash",
        },
        Some(InboundReplyPlan {
            response_id: "route",
            segments: &segments,
            pending_batch_limit: 1,
        }),
        Some(&policy),
        OperationTime::Fixed(NOW + 4),
        None,
    );
    assert!(result.is_ok());
}
#[test]
fn guard_capacity_and_zero_quota_block_without_partial_claim() {
    let (_dir, store, token) = fixture();
    let mut policy = policy();
    receipt(&store, &token, "1", NOW);
    policy.daily_quota = 0;
    assert!(matches!(
        claim(&store, &token, "1", &policy, NOW, 1),
        Err(StoreError::ReplyGuardBlocked {
            reason: "account_daily_quota",
            ..
        })
    ));
    policy.daily_quota = 10;
    {
        let mut c = store.lock_connection().unwrap();
        let t = c.transaction().unwrap();
        for i in 0..10_000 {
            t.execute("INSERT INTO reply_guard_scopes(account_id,kind,scope_key,timezone,updated_at_ms) VALUES('account','peer',?1,'Asia/Shanghai',?2)",params![format!("fixture-{i}"),NOW]).unwrap();
        }
        t.commit().unwrap();
    }
    assert!(matches!(
        claim(&store, &token, "1", &policy, NOW, 1),
        Err(StoreError::ReplyGuardBlocked {
            reason: "guard_storage_capacity",
            ..
        })
    ));
    assert!(store
        .outbound_batch_for_trigger("account", "auto:1")
        .unwrap()
        .is_none());
}

#[test]
fn automatic_start_requires_live_reservations_before_attempt_increment() {
    let (_dir, store, token) = fixture();
    let segments = [OutboundSegmentDraft::text("unguarded")];
    let unguarded = store
        .prepare_outbound_batch_at(&token, "manual", "route", &segments, NOW)
        .unwrap();
    assert!(store
        .transition_segment_at(
            &token,
            &unguarded.segments[0].id,
            SegmentTransition::StartAutomaticAttempt,
            NOW
        )
        .is_err());
    assert_eq!(
        store.outbound_batch(&unguarded.id).unwrap().segments[0].attempt_count,
        0
    );
    receipt(&store, &token, "1", NOW);
    let guarded = claim(&store, &token, "1", &policy(), NOW, 1).unwrap();
    store
        .transition_segment_at(
            &token,
            &guarded.segments[0].id,
            SegmentTransition::StartAutomaticAttempt,
            NOW + 1,
        )
        .unwrap();
    assert_eq!(
        store.outbound_batch(&guarded.id).unwrap().segments[0].attempt_count,
        1
    );
}
