use super::*;
use std::{
    sync::{Arc, Barrier},
    time::{SystemTime, UNIX_EPOCH},
};
fn now() -> i64 {
    i64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis(),
    )
    .unwrap()
}
fn fixture() -> (tempfile::TempDir, CoreStore, LeaseToken) {
    let dir = tempfile::tempdir().unwrap();
    let store = CoreStore::open(dir.path()).unwrap();
    let token = store
        .install_verified_account_lease("a", "instance", "boot", 1, now() + 60_000)
        .unwrap()
        .token();
    (dir, store, token)
}
fn receipt(
    store: &CoreStore,
    lease: &LeaseToken,
    stream: &str,
    generation: i64,
    id: &str,
) -> InboundReceipt {
    store
        .record_inbound_page(
            lease,
            stream,
            generation,
            100,
            &[InboundReceiptDraft {
                event_id: id.into(),
                payload: b"fixture".to_vec(),
                payload_hash: "fixture-hash".into(),
            }],
        )
        .unwrap();
    store
        .pending_inbound_receipts(lease, 128)
        .unwrap()
        .into_iter()
        .find(|r| r.stream == stream && r.event_id == id)
        .unwrap()
}
fn key(receipt: &InboundReceipt) -> InboundReceiptKey<'_> {
    InboundReceiptKey {
        stream: &receipt.stream,
        generation: receipt.stream_generation,
        event_id: &receipt.event_id,
        payload_hash: &receipt.payload_hash,
    }
}
fn plan(segments: &[OutboundSegmentDraft]) -> InboundReplyPlan<'_> {
    InboundReplyPlan {
        response_id: "route-v1",
        segments,
        pending_batch_limit: 32,
    }
}
#[test]
fn live_start_survives_restart_and_only_new_generation_changes_it() {
    let (dir, store, token) = fixture();
    let first = store.ensure_inbound_live_start(&token, 1).unwrap();
    drop(store);
    std::thread::sleep(std::time::Duration::from_millis(2));
    let store = CoreStore::open(dir.path()).unwrap();
    assert_eq!(store.ensure_inbound_live_start(&token, 1).unwrap(), first);
    let second = store.ensure_inbound_live_start(&token, 2).unwrap();
    assert!(second > first);
    assert!(store.ensure_inbound_live_start(&token, 1).is_err());
}
#[test]
fn reply_claim_and_receipt_ack_are_atomic_and_replay_ignores_new_rule_plan() {
    let (dir, store, token) = fixture();
    let event = receipt(&store, &token, "http", 1, "123");
    let segments = [OutboundSegmentDraft::text("reply")];
    let first = store
        .consume_inbound(&token, key(&event), Some(plan(&segments)))
        .unwrap();
    assert!(first.applied);
    let batch = first.batch.unwrap();
    assert_eq!(batch.segments[0].attempt_count, 0);
    assert!(store
        .pending_inbound_receipts(&token, 128)
        .unwrap()
        .is_empty());
    drop(store);
    let store = CoreStore::open(dir.path()).unwrap();
    let different = [OutboundSegmentDraft::text("changed rule")];
    let second = store
        .consume_inbound(&token, key(&event), Some(plan(&different)))
        .unwrap();
    assert!(!second.applied);
    assert_eq!(second.batch.unwrap().id, batch.id);
    let ws = receipt(&store, &token, "ws", 2, "123");
    let duplicate = store
        .consume_inbound(&token, key(&ws), Some(plan(&different)))
        .unwrap();
    assert!(!duplicate.applied);
    assert_eq!(duplicate.batch.unwrap().id, batch.id);
}
#[test]
fn explicit_no_reply_decision_wins_across_transports() {
    let (_dir, store, token) = fixture();
    let http = receipt(&store, &token, "http", 1, "123");
    assert!(
        store
            .consume_inbound(&token, key(&http), None)
            .unwrap()
            .applied
    );
    let ws = receipt(&store, &token, "ws", 1, "123");
    let segments = [OutboundSegmentDraft::text("reply")];
    let result = store
        .consume_inbound(&token, key(&ws), Some(plan(&segments)))
        .unwrap();
    assert!(!result.applied);
    assert!(result.batch.is_none());
    assert!(store
        .outbound_batch_for_trigger("a", "auto:123")
        .unwrap()
        .is_none());
}
#[test]
fn outbox_backpressure_leaves_receipt_and_decision_retryable() {
    let (_dir, store, token) = fixture();
    let segments = [OutboundSegmentDraft::text("reply")];
    let occupied = store
        .prepare_outbound_batch(&token, "manual:occupied", "route", &segments)
        .unwrap();
    let event = receipt(&store, &token, "http", 1, "123");
    let limited = InboundReplyPlan {
        pending_batch_limit: 1,
        ..plan(&segments)
    };
    assert!(store
        .consume_inbound(&token, key(&event), Some(limited))
        .is_err());
    assert_eq!(
        store.pending_inbound_receipts(&token, 128).unwrap().len(),
        1
    );
    store
        .transition_segment(
            &token,
            &occupied.segments[0].id,
            SegmentTransition::CancelPrepared {
                reason: "fixture".into(),
            },
        )
        .unwrap();
    assert!(
        store
            .consume_inbound(&token, key(&event), Some(limited))
            .unwrap()
            .applied
    );
}
#[test]
fn injected_sql_failure_rolls_back_claim_segments_and_ack() {
    let (_dir, store, token) = fixture();
    let event = receipt(&store, &token, "http", 1, "123");
    let segments = [OutboundSegmentDraft::text("reply")];
    store.lock_connection().unwrap().execute_batch("CREATE TEMP TRIGGER fail_reply BEFORE INSERT ON outbound_segments BEGIN SELECT RAISE(ABORT,'fixture fault'); END;").unwrap();
    assert!(store
        .consume_inbound(&token, key(&event), Some(plan(&segments)))
        .is_err());
    assert_eq!(
        store.pending_inbound_receipts(&token, 128).unwrap().len(),
        1
    );
    assert!(store
        .outbound_batch_for_trigger("a", "auto:123")
        .unwrap()
        .is_none());
    store
        .lock_connection()
        .unwrap()
        .execute_batch("DROP TRIGGER fail_reply")
        .unwrap();
    assert!(
        store
            .consume_inbound(&token, key(&event), Some(plan(&segments)))
            .unwrap()
            .applied
    );
}
#[test]
fn concurrent_connections_make_one_transport_independent_claim() {
    let (dir, store, token) = fixture();
    let http = receipt(&store, &token, "http", 1, "123");
    let ws = receipt(&store, &token, "ws", 1, "123");
    let other = CoreStore::open(dir.path()).unwrap();
    let barrier = Arc::new(Barrier::new(2));
    let handles: Vec<_> = [(store, http), (other, ws)]
        .into_iter()
        .map(|(store, event)| {
            let token = token.clone();
            let barrier = barrier.clone();
            std::thread::spawn(move || {
                let segments = [OutboundSegmentDraft::text("reply")];
                barrier.wait();
                let result = store
                    .consume_inbound(&token, key(&event), Some(plan(&segments)))
                    .unwrap();
                (result.applied, result.batch.unwrap().id)
            })
        })
        .collect();
    let results: Vec<_> = handles.into_iter().map(|h| h.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|(applied, _)| *applied).count(), 1);
    assert_eq!(results[0].1, results[1].1);
}
#[test]
fn hash_and_ownership_mismatch_never_acknowledge_the_event() {
    let (_dir, store, token) = fixture();
    let event = receipt(&store, &token, "http", 1, "123");
    let mut wrong = key(&event);
    wrong.payload_hash = "wrong";
    assert!(store.consume_inbound(&token, wrong, None).is_err());
    let mut stale = token.clone();
    stale.fence_epoch = 2;
    assert!(store.consume_inbound(&stale, key(&event), None).is_err());
    assert_eq!(
        store.pending_inbound_receipts(&token, 128).unwrap().len(),
        1
    );
}

#[test]
fn failure_at_final_ack_rolls_back_already_created_outbox_and_decision() {
    let (_dir, store, token) = fixture();
    let event = receipt(&store, &token, "http", 1, "123");
    let segments = [OutboundSegmentDraft::text("reply")];
    store.lock_connection().unwrap().execute_batch("CREATE TEMP TRIGGER fail_ack BEFORE UPDATE OF status ON inbound_receipts WHEN OLD.stream='http' AND NEW.status='processed' BEGIN SELECT RAISE(ABORT,'fixture final ack fault'); END;").unwrap();
    assert!(store
        .consume_inbound(&token, key(&event), Some(plan(&segments)))
        .is_err());
    assert!(store
        .outbound_batch_for_trigger("a", "auto:123")
        .unwrap()
        .is_none());
    let markers: i64 = store
        .lock_connection()
        .unwrap()
        .query_row(
            "SELECT count(*) FROM inbound_receipts WHERE stream='douyin-im-decision'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(markers, 0);
    assert_eq!(
        store.pending_inbound_receipts(&token, 128).unwrap().len(),
        1
    );
    store
        .lock_connection()
        .unwrap()
        .execute_batch("DROP TRIGGER fail_ack")
        .unwrap();
    assert!(
        store
            .consume_inbound(&token, key(&event), Some(plan(&segments)))
            .unwrap()
            .applied
    );
}
#[test]
fn same_server_id_on_different_accounts_does_not_cross_claim_scope() {
    let (_dir, store, a) = fixture();
    let b = store
        .install_verified_account_lease("b", "instance", "boot", 1, now() + 60_000)
        .unwrap()
        .token();
    let segments = [OutboundSegmentDraft::text("reply")];
    let mut ids = Vec::new();
    for token in [&a, &b] {
        let event = receipt(&store, token, "http", 1, "123");
        let result = store
            .consume_inbound(token, key(&event), Some(plan(&segments)))
            .unwrap();
        assert!(result.applied);
        ids.push(result.batch.unwrap().id);
    }
    assert_ne!(ids[0], ids[1]);
}
#[test]
fn noncanonical_event_id_cannot_create_an_alternate_claim_key() {
    let (_dir, store, token) = fixture();
    let event = receipt(&store, &token, "http", 1, "00123");
    assert!(store.consume_inbound(&token, key(&event), None).is_err());
    assert_eq!(
        store.pending_inbound_receipts(&token, 128).unwrap().len(),
        1
    );
}
