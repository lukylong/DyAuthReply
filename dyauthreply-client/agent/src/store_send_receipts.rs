//! Indexed, fenced settlement from authenticated own-inbox delivery evidence.
//! Reading a past delivery never refreshes account sendability or repeats a send.
//! A newly settled receipt from the current fence may persist fresh sendability once.
use super::{
    apply_segment_transition, derive_batch_status, guards, params, require_fence,
    validate_lease_token, CoreStore, LeaseToken, OperationTime, OptionalExtension, SegmentStatus,
    SegmentTransition, StoreError, TransactionBehavior,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone)]
pub struct SentReceiptEvidence {
    pub client_message_id: String,
    pub platform_message_id: String,
    pub conversation_id: String,
    pub conversation_short_id: u64,
    pub sender_sec_uid: String,
    pub text: String,
    pub sent_at_ms: i64,
}
#[derive(Default, Debug, Clone, Copy, Serialize, Eq, PartialEq)]
pub struct ReceiptReconciliation {
    pub confirmed: usize,
    pub duplicates: usize,
    pub conflicts: usize,
}
#[derive(Deserialize)]
struct Route {
    version: u8,
    conversation_id: String,
    short_id: u64,
}
impl CoreStore {
    /// Call only with messages from this verified account's authenticated inbox.
    /// Indexed client ID + account + own sender + route + exact text must match.
    /// Settlement is atomic for the whole bounded evidence page; no attempt is added.
    /// # Errors
    /// Rejects stale ownership, unverified scope, excessive input or storage failure.
    pub fn reconcile_sent_receipts(
        &self,
        lease: &LeaseToken,
        verified_sec_uid: &str,
        evidence: &[SentReceiptEvidence],
    ) -> Result<ReceiptReconciliation, StoreError> {
        self.reconcile_sent_receipts_at(lease, verified_sec_uid, evidence, OperationTime::System)
    }

    fn reconcile_sent_receipts_at(
        &self,
        lease: &LeaseToken,
        own: &str,
        evidence: &[SentReceiptEvidence],
        time: OperationTime,
    ) -> Result<ReceiptReconciliation, StoreError> {
        validate_lease_token(lease)?;
        if own.is_empty() || own.len() > 256 || evidence.len() > 512 {
            return Err(StoreError::InvalidInput("invalid receipt evidence bounds"));
        }
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let now = time.resolve()?;
        require_fence(&tx, lease, now)?;
        let (credential_digest, observed_at_ms) = verified_binding(&tx, lease, own)?;
        let mut result = ReceiptReconciliation::default();
        let mut batches = BTreeSet::new();
        let mut fresh_current_fence_confirmation = false;
        for item in evidence {
            if !valid_evidence(item, own, now) {
                continue;
            }
            let row = tx.query_row(
                "SELECT s.id,s.batch_id,s.status,s.attempt_count,s.platform_message_id,s.payload,
                        s.kind,b.response_id,b.created_at_ms,s.last_fence_epoch
                 FROM outbound_segments s JOIN outbound_batches b ON b.id=s.batch_id
                 WHERE s.client_message_id=?1 AND b.account_id=?2",
                params![item.client_message_id,lease.account_id],
                |r| Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?,r.get::<_,String>(2)?,
                    r.get::<_,u32>(3)?,r.get::<_,Option<String>>(4)?,r.get::<_,String>(5)?,
                    r.get::<_,String>(6)?,r.get::<_,String>(7)?,r.get::<_,i64>(8)?,
                    r.get::<_,i64>(9)?)),
            ).optional()?;
            let Some((
                id,
                batch,
                status,
                attempts,
                server,
                text,
                kind,
                route,
                created,
                attempt_fence,
            )) = row
            else {
                continue;
            };
            let Ok(route) = serde_json::from_str::<Route>(&route) else {
                continue;
            };
            if !matches!(route.version, 1 | 2)
                || route.conversation_id != item.conversation_id
                || route.short_id != item.conversation_short_id
                || route.short_id == 0
                || kind != "text"
                || text != item.text
                || attempts == 0
                || item.sent_at_ms < created.saturating_sub(30_000)
            {
                continue;
            }
            let status = SegmentStatus::parse(&status)?;
            if status == SegmentStatus::Confirmed {
                if server.as_deref() == Some(item.platform_message_id.as_str()) {
                    result.duplicates += 1;
                } else {
                    result.conflicts += 1;
                }
                continue;
            }
            if !matches!(status, SegmentStatus::Sending | SegmentStatus::Uncertain) {
                continue;
            }
            if server
                .as_ref()
                .is_some_and(|v| v != &item.platform_message_id)
            {
                result.conflicts += 1;
                continue;
            }
            // Confirmed segment timestamp describes delivery, not rediscovery.
            // Keep the batch's update timestamp as local reconciliation time.
            apply_segment_transition(
                &tx,
                &id,
                lease.fence_epoch,
                SegmentTransition::Confirm {
                    platform_message_id: item.platform_message_id.clone(),
                },
                item.sent_at_ms.min(now),
            )?;
            batches.insert(batch);
            result.confirmed += 1;
            // Promote only a newly applied, recent receipt for an attempt made
            // under this exact ownership fence. A replay, older observation, or
            // post-rotation reconciliation still confirms delivery but cannot
            // overwrite newer risk/auth evidence or bless a new credential.
            if is_fresh_current_fence(item, attempt_fence, lease, observed_at_ms, now) {
                fresh_current_fence_confirmation = true;
            }
        }
        settle_reconciled_batches(&tx, lease, batches, now)?;
        if fresh_current_fence_confirmation {
            record_fresh_sendability(&tx, lease, own, &credential_digest, now)?;
        }
        tx.commit()?;
        Ok(result)
    }
}

fn verified_binding(
    tx: &super::Transaction<'_>,
    lease: &LeaseToken,
    own: &str,
) -> Result<(String, i64), StoreError> {
    let state = tx
        .query_row(
            "SELECT canonical_sec_uid,credential_digest,observed_at_ms
             FROM account_protocol_state WHERE account_id=?1",
            [&lease.account_id],
            |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                ))
            },
        )
        .optional()?;
    let Some((canonical, digest, observed_at_ms)) = state else {
        return Err(StoreError::InvalidInput("receipt scope must be verified"));
    };
    if canonical != own {
        return Err(StoreError::InvalidInput("receipt scope must be verified"));
    }
    Ok((digest, observed_at_ms))
}

fn valid_evidence(item: &SentReceiptEvidence, own: &str, now: i64) -> bool {
    item.sender_sec_uid == own
        && !item.client_message_id.is_empty()
        && item.client_message_id.len() <= 128
        && item.conversation_id.len() <= 512
        && item.text.len() <= 4096
        && item.sent_at_ms > 0
        && item.sent_at_ms <= now.saturating_add(30_000)
        && item
            .platform_message_id
            .parse::<u64>()
            .is_ok_and(|id| id != 0)
}

fn is_fresh_current_fence(
    item: &SentReceiptEvidence,
    attempt_fence: i64,
    lease: &LeaseToken,
    observed_at_ms: i64,
    now: i64,
) -> bool {
    attempt_fence == lease.fence_epoch
        && item.sent_at_ms >= observed_at_ms
        && now.saturating_sub(item.sent_at_ms) <= 300_000
}

fn record_fresh_sendability(
    tx: &super::Transaction<'_>,
    lease: &LeaseToken,
    own: &str,
    credential_digest: &str,
    now: i64,
) -> Result<(), StoreError> {
    super::protocol_state::record(
        tx,
        lease,
        super::SendObservation {
            canonical_sec_uid: own,
            credential_digest,
            capability: crate::state::SendCapability::Sendable,
        },
        now,
    )
}

fn settle_reconciled_batches(
    tx: &super::Transaction<'_>,
    lease: &LeaseToken,
    batches: BTreeSet<String>,
    now: i64,
) -> Result<(), StoreError> {
    for batch in batches {
        let status = derive_batch_status(tx, &batch)?;
        tx.execute("UPDATE outbound_batches SET status=?2,last_fence_epoch=?3,updated_at_ms=?4 WHERE id=?1",
                params![batch,status.as_str(),lease.fence_epoch,now])?;
        guards::settle(tx, &batch, now)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::SendCapability;
    use crate::store::{OutboundBatch, OutboundSegmentDraft, SendObservation};
    const NOW: i64 = 1_700_000_000_000;
    const ROUTE: &str = r#"{"version":1,"conversation_id":"conversation","short_id":123}"#;
    fn fixture() -> (tempfile::TempDir, CoreStore, LeaseToken) {
        let dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(dir.path()).unwrap();
        let lease = store
            .install_verified_account_lease_at(
                "account",
                "instance",
                "boot",
                1,
                NOW,
                NOW + 300_000_000,
            )
            .unwrap()
            .token();
        super::super::protocol_state::restore(
            &store.lock_connection().unwrap(),
            &lease,
            "self",
            &"a".repeat(64),
            NOW,
        )
        .unwrap();
        (dir, store, lease)
    }
    fn prepared(store: &CoreStore, lease: &LeaseToken, id: &str, count: usize) -> OutboundBatch {
        store
            .prepare_outbound_batch_at(
                lease,
                id,
                ROUTE,
                &(0..count)
                    .map(|_| OutboundSegmentDraft::text("hello"))
                    .collect::<Vec<_>>(),
                NOW,
            )
            .unwrap()
    }
    fn sent(
        store: &CoreStore,
        lease: &LeaseToken,
        batch: &OutboundBatch,
        ordinal: usize,
    ) -> SentReceiptEvidence {
        store
            .transition_segment_at(
                lease,
                &batch.segments[ordinal].id,
                SegmentTransition::StartAttempt,
                NOW + 1,
            )
            .unwrap();
        SentReceiptEvidence {
            client_message_id: batch.segments[ordinal].client_message_id.clone(),
            platform_message_id: (123_456 + ordinal).to_string(),
            conversation_id: "conversation".into(),
            conversation_short_id: 123,
            sender_sec_uid: "self".into(),
            text: "hello".into(),
            sent_at_ms: NOW + 2,
        }
    }
    fn reconcile(
        store: &CoreStore,
        lease: &LeaseToken,
        evidence: &[SentReceiptEvidence],
    ) -> ReceiptReconciliation {
        store
            .reconcile_sent_receipts_at(lease, "self", evidence, OperationTime::Fixed(NOW + 100))
            .unwrap()
    }
    #[test]
    fn newer_risk_observation_wins_over_an_older_uncertain_receipt() {
        let (_dir, store, lease) = fixture();
        let batch = prepared(&store, &lease, "trigger", 1);
        let evidence = sent(&store, &lease, &batch, 0);
        store
            .transition_segment_at(
                &lease,
                &batch.segments[0].id,
                SegmentTransition::MarkUncertain {
                    reason: "ack lost".into(),
                },
                NOW + 3,
            )
            .unwrap();
        super::super::protocol_state::record(
            &store.lock_connection().unwrap(),
            &lease,
            SendObservation {
                canonical_sec_uid: "self",
                credential_digest: &"a".repeat(64),
                capability: SendCapability::RiskControlled,
            },
            NOW + 4,
        )
        .unwrap();
        assert_eq!(
            reconcile(&store, &lease, std::slice::from_ref(&evidence)).confirmed,
            1
        );
        assert_eq!(
            reconcile(&store, &lease, std::slice::from_ref(&evidence)).duplicates,
            1
        );
        let current = store.outbound_batch(&batch.id).unwrap();
        assert_eq!(current.status, super::super::BatchStatus::Confirmed);
        assert_eq!(current.segments[0].attempt_count, 1);
        let c = store.lock_connection().unwrap();
        assert_eq!(
            c.query_row(
                "SELECT capability,observed_at_ms FROM account_protocol_state",
                [],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
            )
            .unwrap(),
            ("risk_controlled".into(), NOW + 4)
        );
        assert_eq!(
            c.query_row("SELECT updated_at_ms FROM outbound_segments", [], |r| r
                .get::<_, i64>(0))
                .unwrap(),
            NOW + 2
        );
    }
    #[test]
    fn fresh_current_fence_receipt_persists_sendability_without_replay_refresh() {
        let (_dir, store, lease) = fixture();
        let batch = prepared(&store, &lease, "fresh-current-fence", 1);
        let evidence = sent(&store, &lease, &batch, 0);
        store
            .transition_segment_at(
                &lease,
                &batch.segments[0].id,
                SegmentTransition::MarkUncertain {
                    reason: "ack lost".into(),
                },
                NOW + 3,
            )
            .unwrap();

        assert_eq!(
            reconcile(&store, &lease, std::slice::from_ref(&evidence)).confirmed,
            1
        );
        let observed = || {
            store
                .lock_connection()
                .unwrap()
                .query_row(
                    "SELECT capability,observed_at_ms FROM account_protocol_state",
                    [],
                    |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)),
                )
                .unwrap()
        };
        assert_eq!(observed(), ("sendable".into(), NOW + 100));
        assert_eq!(
            store
                .reconcile_sent_receipts_at(
                    &lease,
                    "self",
                    std::slice::from_ref(&evidence),
                    OperationTime::Fixed(NOW + 200),
                )
                .unwrap()
                .duplicates,
            1
        );
        assert_eq!(observed(), ("sendable".into(), NOW + 100));
    }
    #[test]
    fn receipt_from_an_older_fence_confirms_delivery_without_blessing_new_credentials() {
        let (_dir, store, first) = fixture();
        let batch = prepared(&store, &first, "old-fence", 1);
        let evidence = sent(&store, &first, &batch, 0);
        store
            .transition_segment_at(
                &first,
                &batch.segments[0].id,
                SegmentTransition::MarkUncertain {
                    reason: "restart before receipt".into(),
                },
                NOW + 3,
            )
            .unwrap();
        store.release_account_lease_at(&first, NOW + 4).unwrap();
        let second = store
            .install_verified_account_lease_at(
                "account",
                "instance",
                "new-boot",
                2,
                NOW + 5,
                NOW + 300_000_000,
            )
            .unwrap()
            .token();
        assert_eq!(
            super::super::protocol_state::restore(
                &store.lock_connection().unwrap(),
                &second,
                "self",
                &"b".repeat(64),
                NOW + 6,
            )
            .unwrap(),
            SendCapability::Unknown
        );

        assert_eq!(
            store
                .reconcile_sent_receipts_at(
                    &second,
                    "self",
                    std::slice::from_ref(&evidence),
                    OperationTime::Fixed(NOW + 100),
                )
                .unwrap()
                .confirmed,
            1
        );
        assert_eq!(
            store
                .lock_connection()
                .unwrap()
                .query_row("SELECT capability FROM account_protocol_state", [], |r| {
                    r.get::<_, String>(0)
                })
                .unwrap(),
            "unknown"
        );
    }
    #[test]
    fn wrong_sender_account_route_text_and_unsent_segments_never_confirm() {
        let (_dir, store, lease) = fixture();
        let batch = prepared(&store, &lease, "trigger", 2);
        let evidence = sent(&store, &lease, &batch, 0);
        let mut bad = Vec::new();
        let mut v = evidence.clone();
        v.sender_sec_uid = "peer".into();
        bad.push(v);
        let mut v = evidence.clone();
        v.conversation_id = "other".into();
        bad.push(v);
        let mut v = evidence.clone();
        v.conversation_short_id = 999;
        bad.push(v);
        let mut v = evidence.clone();
        v.text = "different".into();
        bad.push(v);
        let mut v = evidence.clone();
        v.platform_message_id = "0".into();
        bad.push(v);
        let mut v = evidence.clone();
        v.sent_at_ms = 0;
        bad.push(v);
        let mut v = evidence.clone();
        v.sent_at_ms = NOW - 30_001;
        bad.push(v);
        let mut v = evidence.clone();
        v.sent_at_ms = NOW + 100_000;
        bad.push(v);
        let mut v = evidence.clone();
        v.client_message_id = batch.segments[1].client_message_id.clone();
        bad.push(v);
        assert_eq!(reconcile(&store, &lease, &bad).confirmed, 0);
        let other = store
            .install_verified_account_lease_at(
                "other",
                "instance",
                "boot",
                1,
                NOW,
                NOW + 300_000_000,
            )
            .unwrap()
            .token();
        super::super::protocol_state::restore(
            &store.lock_connection().unwrap(),
            &other,
            "self",
            &"b".repeat(64),
            NOW,
        )
        .unwrap();
        assert_eq!(
            reconcile(&store, &other, std::slice::from_ref(&evidence)).confirmed,
            0
        );
        assert!(store
            .reconcile_sent_receipts_at(
                &lease,
                "wrong",
                &[evidence],
                OperationTime::Fixed(NOW + 100)
            )
            .is_err());
        let current = store.outbound_batch(&batch.id).unwrap();
        assert_eq!(current.segments[0].status, SegmentStatus::Sending);
        assert_eq!(current.segments[1].status, SegmentStatus::Prepared);
    }
    #[test]
    fn fence_transfer_then_restart_reconciles_and_conflicting_server_id_is_visible() {
        let (dir, store, lease) = fixture();
        let batch = prepared(&store, &lease, "trigger", 1);
        let mut evidence = sent(&store, &lease, &batch, 0);
        let new = store
            .install_verified_account_lease_at(
                "account",
                "instance",
                "new-boot",
                2,
                NOW + 3,
                NOW + 300_000_000,
            )
            .unwrap()
            .token();
        assert!(store
            .reconcile_sent_receipts_at(
                &lease,
                "self",
                std::slice::from_ref(&evidence),
                OperationTime::Fixed(NOW + 100)
            )
            .is_err());
        drop(store);
        let store = CoreStore::open(dir.path()).unwrap();
        assert_eq!(
            reconcile(&store, &new, std::slice::from_ref(&evidence)).confirmed,
            1
        );
        evidence.platform_message_id = "789012".into();
        assert_eq!(reconcile(&store, &new, &[evidence]).conflicts, 1);
        assert_eq!(
            store.outbound_batch(&batch.id).unwrap().segments[0]
                .platform_message_id
                .as_deref(),
            Some("123456")
        );
    }
    #[test]
    fn failed_page_settlement_rolls_back_all_confirmations() {
        let (_dir, store, lease) = fixture();
        let batch = prepared(&store, &lease, "trigger", 2);
        let a = sent(&store, &lease, &batch, 0);
        let b = sent(&store, &lease, &batch, 1);
        store.lock_connection().unwrap().execute_batch("CREATE TRIGGER injected_failure BEFORE UPDATE ON outbound_batches BEGIN SELECT RAISE(ABORT,'injected'); END;").unwrap();
        assert!(store
            .reconcile_sent_receipts_at(
                &lease,
                "self",
                &[a.clone(), b.clone()],
                OperationTime::Fixed(NOW + 100)
            )
            .is_err());
        assert!(store
            .outbound_batch(&batch.id)
            .unwrap()
            .segments
            .iter()
            .all(|s| s.status == SegmentStatus::Sending && s.attempt_count == 1));
        store
            .lock_connection()
            .unwrap()
            .execute_batch("DROP TRIGGER injected_failure")
            .unwrap();
        assert_eq!(reconcile(&store, &lease, &[a, b]).confirmed, 2);
    }
    #[test]
    fn auto_multi_segment_receipts_release_one_guard_charge_on_delivery_day() {
        let (_dir, store, lease) = fixture();
        let batch = prepared(&store, &lease, "trigger", 2);
        let policy = crate::store::GuardPolicy {
            rule_id: "r".into(),
            conversation_id: "conversation".into(),
            peer_id: "peer".into(),
            timezone: "Asia/Shanghai".into(),
            daily_quota: 10,
            cooldown_ms: 1000,
            minimum_interval_ms: 1000,
            daily_peer_limit: true,
        };
        guards::reserve(&store.lock_connection().unwrap(), &batch, &policy, NOW).unwrap();
        let a = sent(&store, &lease, &batch, 0);
        let b = sent(&store, &lease, &batch, 1);
        assert_eq!(reconcile(&store, &lease, &[a]).confirmed, 1);
        assert_eq!(
            store
                .lock_connection()
                .unwrap()
                .query_row("SELECT settled FROM auto_reply_batches", [], |r| r
                    .get::<_, i64>(0))
                .unwrap(),
            0
        );
        let result = store
            .reconcile_sent_receipts_at(
                &lease,
                "self",
                &[b],
                OperationTime::Fixed(NOW + 86_400_000),
            )
            .unwrap();
        assert_eq!(result.confirmed, 1);
        let c = store.lock_connection().unwrap();
        assert_eq!(
            c.query_row("SELECT settled_at_ms FROM auto_reply_batches", [], |r| r
                .get::<_, i64>(0))
                .unwrap(),
            NOW + 86_400_000
        );
        assert_eq!(c.query_row("SELECT used_count,reserved_batch_id,last_delivery_at_ms FROM reply_guard_scopes WHERE kind='account'",[],|r|Ok((r.get::<_,i64>(0)?,r.get::<_,Option<String>>(1)?,r.get::<_,i64>(2)?))).unwrap(),(1,None,NOW+2));
    }
    #[test]
    fn cancelling_unsent_tail_does_not_move_old_delivery_into_today() {
        let (_dir, store, lease) = fixture();
        let batch = prepared(&store, &lease, "trigger", 2);
        let policy = crate::store::GuardPolicy {
            rule_id: "r".into(),
            conversation_id: "conversation".into(),
            peer_id: "peer".into(),
            timezone: "Asia/Shanghai".into(),
            daily_quota: 10,
            cooldown_ms: 1000,
            minimum_interval_ms: 1000,
            daily_peer_limit: true,
        };
        guards::reserve(&store.lock_connection().unwrap(), &batch, &policy, NOW).unwrap();
        let evidence = sent(&store, &lease, &batch, 0);
        assert_eq!(reconcile(&store, &lease, &[evidence]).confirmed, 1);
        store
            .transition_segment_at(
                &lease,
                &batch.segments[1].id,
                SegmentTransition::CancelPrepared {
                    reason: "expired unsent tail".into(),
                },
                NOW + 86_400_000,
            )
            .unwrap();
        let c = store.lock_connection().unwrap();
        let (delivery,used,reserved)=c.query_row("SELECT last_delivery_at_ms,used_count,reserved_batch_id FROM reply_guard_scopes WHERE kind='account'",[],|r|Ok((r.get::<_,i64>(0)?,r.get::<_,i64>(1)?,r.get::<_,Option<String>>(2)?))).unwrap();
        assert_eq!((delivery, used, reserved), (NOW + 2, 1, None));
    }
}
