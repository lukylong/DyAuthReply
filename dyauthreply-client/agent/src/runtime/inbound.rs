//! HTTP reconciliation -> bounded durable inbound spool. Initial pages discard
//! only messages at/before the durable live-start watermark. Consumers must explicitly
//! acknowledge durable events before their payloads are reclaimed.
use crate::{
    protocol::inbox::{InboxMessage, InboxPage},
    store::{
        CoreStore, InboundCheckpoint, InboundLimits, InboundPageDraft, InboundReceiptDraft,
        LeaseToken,
    },
};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const STREAM: &str = "douyin-im-http";
pub const MAX_PENDING_RECORDS: u32 = 128;
pub const MAX_PENDING_BYTES: u64 = 1024 * 1024;
const MAX_PAGE_MESSAGES: usize = 512;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InboundEvent {
    pub version: u8,
    pub server_message_id: String,
    pub conversation_id: String,
    pub conversation_short_id: String,
    pub sender_uid: String,
    pub sender_sec_uid: String,
    pub client_message_id: String,
    pub message_type: u64,
    pub create_time_us: u64,
    pub content_json: String,
    pub text: Option<String>,
}
impl From<&InboxMessage> for InboundEvent {
    fn from(message: &InboxMessage) -> Self {
        Self {
            version: 1,
            server_message_id: message.server_message_id.to_string(),
            conversation_id: message.conversation_id.clone(),
            conversation_short_id: message.conversation_short_id.to_string(),
            sender_uid: message.sender_uid.to_string(),
            sender_sec_uid: message.sender_sec_uid.clone(),
            client_message_id: message.client_message_id.clone(),
            message_type: message.message_type,
            create_time_us: message.create_time_us,
            content_json: message.content_json.clone(),
            text: message.text.clone(),
        }
    }
}

/// Only a consistent ordinary own-message can prove delivery. This does not
/// establish current sendability; old historical receipts are intentionally valid.
pub(super) fn sent_evidence(
    event: &InboundEvent,
    own: &str,
) -> Option<crate::store::SentReceiptEvidence> {
    if event.version != 1
        || event.sender_sec_uid != own
        || own.is_empty()
        || event.sender_uid.parse::<u64>().ok()? == 0
        || event.message_type != 1
        || event.client_message_id.is_empty()
        || event.create_time_us == 0
    {
        return None;
    }
    let content: serde_json::Value = serde_json::from_str(&event.content_json).ok()?;
    if content.get("read_index").is_some() || content.get("command_type").is_some() {
        return None;
    }
    let text = content.get("text")?.as_str()?;
    if event.text.as_deref() != Some(text) {
        return None;
    }
    Some(crate::store::SentReceiptEvidence {
        client_message_id: event.client_message_id.clone(),
        platform_message_id: event.server_message_id.clone(),
        conversation_id: event.conversation_id.clone(),
        conversation_short_id: event.conversation_short_id.parse().ok()?,
        sender_sec_uid: event.sender_sec_uid.clone(),
        text: text.into(),
        sent_at_ms: i64::try_from(event.create_time_us / 1000).ok()?,
    })
}

pub(super) fn reconcile_page_receipts(
    store: &CoreStore,
    lease: &LeaseToken,
    own: &str,
    page: &InboxPage,
) -> Result<crate::store::ReceiptReconciliation> {
    anyhow::ensure!(
        page.status_code == 0 && page.wrapper_present && page.messages.len() <= MAX_PAGE_MESSAGES,
        "invalid receipt evidence page"
    );
    let evidence: Vec<_> = page
        .messages
        .iter()
        .filter(|m| m.sender_sec_uid == own)
        .filter_map(|m| sent_evidence(&InboundEvent::from(m), own))
        .collect();
    if evidence.is_empty() {
        return Ok(crate::store::ReceiptReconciliation::default());
    }
    Ok(store.reconcile_sent_receipts(lease, own, &evidence)?)
}

#[derive(Debug, Serialize)]
pub struct ReconcileResult {
    pub baseline: bool,
    pub inserted: usize,
    pub cursor: i64,
}
/// # Errors
/// Rejects stale generations and invalid persisted cursors.
pub fn cursor_for(checkpoint: Option<&InboundCheckpoint>, generation: i64) -> Result<u64> {
    if let Some(checkpoint) = checkpoint {
        anyhow::ensure!(
            generation >= checkpoint.stream_generation,
            "stale credential generation"
        );
        if generation == checkpoint.stream_generation {
            return u64::try_from(checkpoint.checkpoint).context("invalid durable cursor");
        }
    }
    Ok(0)
}
/// Commits one successfully decoded response. This function never sends a reply.
/// # Errors
/// Rejects malformed/regressing cursors, oversized pages, stale fences, conflicting
/// duplicate events and exhausted spool capacity without cursor advancement.
pub fn commit_page(
    store: &CoreStore,
    lease: &LeaseToken,
    generation: i64,
    previous: Option<&InboundCheckpoint>,
    page: InboxPage,
) -> Result<ReconcileResult> {
    let old = cursor_for(previous, generation)?;
    anyhow::ensure!(
        page.status_code == 0 && page.wrapper_present,
        "inbox response is not a valid success"
    );
    anyhow::ensure!(page.next_cursor >= old, "inbox cursor regressed");
    let baseline = previous.is_none_or(|p| p.stream_generation != generation);
    anyhow::ensure!(
        !baseline || page.messages.is_empty() || page.next_cursor > 0,
        "history baseline has no usable cursor"
    );
    anyhow::ensure!(
        page.messages.len() <= MAX_PAGE_MESSAGES,
        "inbox page has too many messages"
    );
    let next = i64::try_from(page.next_cursor)?;
    let messages = if baseline {
        // Runtime establishes this before identity and inbox I/O. Reuse it on
        // retries/restarts so messages arriving during the first request survive.
        let cutoff = u64::try_from(store.ensure_inbound_live_start(lease, generation)?)?;
        page.messages
            .into_iter()
            .filter(|message| message.create_time_us == 0 || message.create_time_us > cutoff)
            .collect()
    } else {
        page.messages
    };
    let receipts = normalize_page(messages)?;
    let result = store.record_bounded_inbound_page(
        lease,
        InboundPageDraft {
            stream: STREAM,
            stream_generation: generation,
            checkpoint: next,
            receipts: &receipts,
        },
        InboundLimits {
            pending_records: MAX_PENDING_RECORDS,
            pending_bytes: MAX_PENDING_BYTES,
        },
    )?;
    Ok(ReconcileResult {
        baseline,
        inserted: result.inserted_count,
        cursor: result.checkpoint.checkpoint,
    })
}
/// Persists pushed messages with a local monotonic checkpoint, not opaque logId order.
/// # Errors
/// Rejects stale fences/generations and spool overflow; callers must not ACK on failure.
pub fn commit_frontier(
    store: &CoreStore,
    lease: &LeaseToken,
    generation: i64,
    messages: Vec<InboxMessage>,
) -> Result<()> {
    const STREAM: &str = "douyin-im-ws";
    let receipts = normalize_page(messages)?;
    let old = store.inbound_checkpoint(&lease.account_id, STREAM)?;
    let checkpoint = cursor_for(old.as_ref(), generation)?
        .checked_add(1)
        .context("WS checkpoint exhausted")?;
    store.record_bounded_inbound_page(
        lease,
        InboundPageDraft {
            stream: STREAM,
            stream_generation: generation,
            checkpoint: i64::try_from(checkpoint)?,
            receipts: &receipts,
        },
        InboundLimits {
            pending_records: MAX_PENDING_RECORDS,
            pending_bytes: MAX_PENDING_BYTES,
        },
    )?;
    Ok(())
}

fn normalize_page(messages: Vec<InboxMessage>) -> Result<Vec<InboundReceiptDraft>> {
    anyhow::ensure!(
        messages.len() <= MAX_PAGE_MESSAGES,
        "inbox page has too many messages"
    );
    let mut receipts = Vec::with_capacity(messages.len());
    let mut total = 0usize;
    for message in messages {
        anyhow::ensure!(
            message.server_message_id > 0 && !message.conversation_id.is_empty(),
            "invalid message identity"
        );
        let event = InboundEvent::from(&message);
        let payload = serde_json::to_vec(&event)?;
        total = total
            .checked_add(payload.len())
            .context("page size overflow")?;
        anyhow::ensure!(
            total <= usize::try_from(MAX_PENDING_BYTES)?,
            "inbox page exceeds spool budget"
        );
        receipts.push(InboundReceiptDraft {
            event_id: event.server_message_id,
            payload_hash: format!("{:x}", Sha256::digest(&payload)),
            payload,
        });
    }
    Ok(receipts)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> (tempfile::TempDir, CoreStore, LeaseToken) {
        let dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(dir.path()).unwrap();
        let until = i64::try_from(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis(),
        )
        .unwrap()
            + 60_000;
        let token = store
            .install_verified_account_lease("account-a", "installation", "boot", 1, until)
            .unwrap()
            .token();
        (dir, store, token)
    }
    fn message(id: u64) -> InboxMessage {
        InboxMessage {
            conversation_id: "conversation".into(),
            conversation_short_id: 7_681_885_293_746_357_819,
            server_message_id: id,
            sender_uid: 42,
            sender_sec_uid: "peer".into(),
            create_time_us: 100,
            message_type: 7,
            client_message_id: format!("client-{id}"),
            content_json: "{\"text\":\"fixture\"}".into(),
            text: Some("fixture".into()),
        }
    }
    fn page(cursor: u64, ids: &[u64]) -> InboxPage {
        InboxPage {
            status_code: 0,
            status_message: String::new(),
            messages: ids.iter().map(|id| message(*id)).collect(),
            next_cursor: cursor,
            wrapper_present: true,
        }
    }
    fn commit(store: &CoreStore, token: &LeaseToken, cursor: u64, ids: &[u64]) -> ReconcileResult {
        let old = store.inbound_checkpoint(&token.account_id, STREAM).unwrap();
        commit_page(store, token, 1, old.as_ref(), page(cursor, ids)).unwrap()
    }
    #[test]
    fn websocket_receipts_are_bounded_and_do_not_advance_http_cursor() {
        let (_dir, store, token) = fixture();
        commit_frontier(&store, &token, 1, (1..=128).map(message).collect()).unwrap();
        assert!(store
            .inbound_checkpoint(&token.account_id, STREAM)
            .unwrap()
            .is_none());
        assert!(commit_frontier(&store, &token, 1, vec![message(129)]).is_err());
        assert_eq!(
            store
                .inbound_checkpoint(&token.account_id, "douyin-im-ws")
                .unwrap()
                .unwrap()
                .checkpoint,
            1
        );
    }
    #[test]
    fn websocket_and_http_share_terminal_decision_without_duplicate_reply() {
        let (_dir, store, token) = fixture();
        commit(&store, &token, 1, &[]);
        commit(&store, &token, 2, &[7]);
        let receipt = store
            .pending_inbound_receipts(&token, 128)
            .unwrap()
            .remove(0);
        let decision = store
            .consume_inbound(
                &token,
                crate::store::InboundReceiptKey {
                    stream: &receipt.stream,
                    generation: receipt.stream_generation,
                    event_id: &receipt.event_id,
                    payload_hash: &receipt.payload_hash,
                },
                None,
            )
            .unwrap();
        assert!(decision.applied);
        commit_frontier(&store, &token, 1, vec![message(7)]).unwrap();
        let receipt = store
            .pending_inbound_receipts(&token, 128)
            .unwrap()
            .remove(0);
        let replay = store
            .consume_inbound(
                &token,
                crate::store::InboundReceiptKey {
                    stream: &receipt.stream,
                    generation: receipt.stream_generation,
                    event_id: &receipt.event_id,
                    payload_hash: &receipt.payload_hash,
                },
                None,
            )
            .unwrap();
        assert!(!replay.applied);
        assert!(store
            .pending_inbound_receipts(&token, 128)
            .unwrap()
            .is_empty());
    }

    #[test]
    fn own_history_settles_before_baseline_discards_it_and_invalid_pages_do_not() {
        let (_dir, store, token) = fixture();
        store
            .restore_send_observation(&token, "self", &"a".repeat(64))
            .unwrap();
        let batch = store
            .prepare_outbound_batch(
                &token,
                "trigger",
                r#"{"version":1,"conversation_id":"conversation","short_id":7681885293746357819}"#,
                &[crate::store::OutboundSegmentDraft::text("fixture")],
            )
            .unwrap();
        store
            .transition_segment(
                &token,
                &batch.segments[0].id,
                crate::store::SegmentTransition::StartAttempt,
            )
            .unwrap();
        store
            .transition_segment(
                &token,
                &batch.segments[0].id,
                crate::store::SegmentTransition::MarkUncertain {
                    reason: "lost ack".into(),
                },
            )
            .unwrap();
        let cutoff = u64::try_from(store.ensure_inbound_live_start(&token, 1).unwrap()).unwrap();
        let mut response = page(100, &[99]);
        response.messages[0].sender_sec_uid = "self".into();
        response.messages[0].message_type = 1;
        response.messages[0].client_message_id = batch.segments[0].client_message_id.clone();
        response.messages[0].create_time_us = cutoff;
        response.status_code = -1;
        assert!(reconcile_page_receipts(&store, &token, "self", &response).is_err());
        assert_eq!(
            store.outbound_batch(&batch.id).unwrap().segments[0].status,
            crate::store::SegmentStatus::Uncertain
        );
        response.status_code = 0;
        assert_eq!(
            reconcile_page_receipts(&store, &token, "self", &response)
                .unwrap()
                .confirmed,
            1
        );
        assert_eq!(
            commit_page(&store, &token, 1, None, response)
                .unwrap()
                .inserted,
            0
        );
        let current = store.outbound_batch(&batch.id).unwrap();
        assert_eq!(current.status, crate::store::BatchStatus::Confirmed);
        assert_eq!(current.segments[0].attempt_count, 1);
    }

    #[test]
    fn system_peer_and_inconsistent_content_never_form_delivery_evidence() {
        let mut event = InboundEvent::from(&message(99));
        event.sender_sec_uid = "self".into();
        event.message_type = 1;
        event.create_time_us = 1_700_000_000_000_000;
        assert!(sent_evidence(&event, "self").is_some());
        assert!(sent_evidence(&event, "other").is_none());
        event.content_json = r#"{"text":"fixture","command_type":1}"#.into();
        assert!(sent_evidence(&event, "self").is_none());
        event.content_json = r#"{"text":"different"}"#.into();
        assert!(sent_evidence(&event, "self").is_none());
        event.content_json = r#"{"text":"fixture"}"#.into();
        event.sender_uid = "0".into();
        assert!(sent_evidence(&event, "self").is_none());
    }

    #[test]
    fn first_response_preserves_messages_arriving_during_initial_sync() {
        let (dir, store, token) = fixture();
        // Production establishes this before identity/network I/O, not after the response.
        let cutoff = u64::try_from(store.ensure_inbound_live_start(&token, 1).unwrap()).unwrap();
        let mut response = page(100, &[1, 2, 3, 4]);
        response.messages[0].create_time_us = cutoff - 1;
        response.messages[1].create_time_us = cutoff;
        response.messages[2].create_time_us = cutoff + 1;
        // Unknown time remains durable/deferred instead of being silently lost.
        response.messages[3].create_time_us = 0;
        let outcome = commit_page(&store, &token, 1, None, response).unwrap();
        assert!(outcome.baseline);
        assert_eq!(outcome.inserted, 2);
        let pending = store.pending_inbound_receipts(&token, 128).unwrap();
        assert_eq!(
            pending
                .iter()
                .map(|r| r.event_id.as_str())
                .collect::<Vec<_>>(),
            vec!["3", "4"]
        );
        drop(store);
        let reopened = CoreStore::open(dir.path()).unwrap();
        assert_eq!(
            reopened.ensure_inbound_live_start(&token, 1).unwrap(),
            i64::try_from(cutoff).unwrap()
        );
        assert_eq!(
            reopened
                .pending_inbound_receipts(&token, 128)
                .unwrap()
                .len(),
            2
        );
    }

    #[test]
    fn failed_initial_page_keeps_watermark_and_retries_without_advancing_cursor() {
        let (_dir, store, token) = fixture();
        let cutoff = u64::try_from(store.ensure_inbound_live_start(&token, 1).unwrap()).unwrap();
        let ids: Vec<u64> = (1..=129).collect();
        let mut response = page(100, &ids);
        for m in &mut response.messages {
            m.create_time_us = cutoff + 1;
        }
        assert!(commit_page(&store, &token, 1, None, response).is_err());
        assert!(store
            .inbound_checkpoint(&token.account_id, STREAM)
            .unwrap()
            .is_none());
        assert!(store
            .pending_inbound_receipts(&token, 128)
            .unwrap()
            .is_empty());
        assert_eq!(
            store.ensure_inbound_live_start(&token, 1).unwrap(),
            i64::try_from(cutoff).unwrap()
        );
        let mut retry = page(100, &[1]);
        retry.messages[0].create_time_us = cutoff + 1;
        assert_eq!(
            commit_page(&store, &token, 1, None, retry)
                .unwrap()
                .inserted,
            1
        );
    }

    #[test]
    fn initial_page_count_is_bounded_even_when_all_messages_are_history() {
        let (_dir, store, token) = fixture();
        let ids: Vec<u64> = (1..=513).collect();
        assert!(commit_page(&store, &token, 1, None, page(100, &ids)).is_err());
        assert!(store
            .inbound_checkpoint(&token.account_id, STREAM)
            .unwrap()
            .is_none());
    }

    #[test]
    fn new_generation_uses_its_own_watermark_and_preserves_new_initial_messages() {
        let (_dir, store, token) = fixture();
        commit(&store, &token, 100, &[]);
        let cutoff = u64::try_from(store.ensure_inbound_live_start(&token, 2).unwrap()).unwrap();
        let previous = store.inbound_checkpoint(&token.account_id, STREAM).unwrap();
        let mut response = page(200, &[2, 3]);
        response.messages[0].create_time_us = cutoff;
        response.messages[1].create_time_us = cutoff + 1;
        let outcome = commit_page(&store, &token, 2, previous.as_ref(), response).unwrap();
        assert!(outcome.baseline);
        assert_eq!(outcome.inserted, 1);
        let row = store
            .pending_inbound_receipts(&token, 128)
            .unwrap()
            .remove(0);
        assert_eq!(row.event_id, "3");
        assert_eq!(row.stream_generation, 2);
        assert!(store.ensure_inbound_live_start(&token, 1).is_err());
    }

    #[test]
    fn history_baseline_never_becomes_reply_work_and_duplicates_are_idempotent() {
        let (_dir, store, token) = fixture();
        assert!(commit(&store, &token, 100, &[1, 2]).baseline);
        assert!(store
            .pending_inbound_receipts(&token, 128)
            .unwrap()
            .is_empty());
        assert_eq!(commit(&store, &token, 101, &[3]).inserted, 1);
        assert_eq!(commit(&store, &token, 101, &[3]).inserted, 0);
        let events = store.pending_inbound_receipts(&token, 128).unwrap();
        assert_eq!(events.len(), 1);
        let event: InboundEvent =
            serde_json::from_slice(events[0].payload.as_ref().unwrap()).unwrap();
        assert_eq!(event.server_message_id, "3");
        assert_eq!(event.conversation_short_id, "7681885293746357819");
    }
    #[test]
    fn empty_zero_cursor_baseline_does_not_drop_the_first_new_message() {
        let (_dir, store, token) = fixture();
        assert!(commit(&store, &token, 0, &[]).baseline);
        let delta = commit(&store, &token, 1, &[1]);
        assert!(!delta.baseline);
        assert_eq!(delta.inserted, 1);
    }
    #[test]
    fn full_spool_rolls_back_every_insert_and_cursor() {
        let (_dir, store, token) = fixture();
        commit(&store, &token, 100, &[]);
        let ids: Vec<u64> = (1..=128).collect();
        commit(&store, &token, 200, &ids);
        let old = store.inbound_checkpoint(&token.account_id, STREAM).unwrap();
        assert!(commit_page(&store, &token, 1, old.as_ref(), page(201, &[129])).is_err());
        assert_eq!(
            store
                .inbound_checkpoint(&token.account_id, STREAM)
                .unwrap()
                .unwrap()
                .checkpoint,
            200
        );
        assert_eq!(
            store.pending_inbound_receipts(&token, 200).unwrap().len(),
            128
        );
        assert_eq!(commit(&store, &token, 200, &ids).inserted, 0);
    }
    #[test]
    fn malformed_regressing_and_stale_results_never_advance_cursor() {
        let (_dir, store, token) = fixture();
        commit(&store, &token, 100, &[]);
        let old = store.inbound_checkpoint(&token.account_id, STREAM).unwrap();
        let mut missing = page(101, &[1]);
        missing.wrapper_present = false;
        for bad in [missing, page(99, &[1]), page(101, &[0])] {
            assert!(commit_page(&store, &token, 1, old.as_ref(), bad).is_err());
        }
        let mut stale = token.clone();
        stale.fence_epoch = 0;
        assert!(commit_page(&store, &stale, 1, old.as_ref(), page(101, &[1])).is_err());
        assert_eq!(
            store
                .inbound_checkpoint(&token.account_id, STREAM)
                .unwrap()
                .unwrap()
                .checkpoint,
            100
        );
        assert!(store
            .pending_inbound_receipts(&token, 128)
            .unwrap()
            .is_empty());
    }
    #[test]
    fn account_scopes_and_credential_generations_remain_independent() {
        let (_dir, store, token) = fixture();
        commit(&store, &token, 100, &[]);
        commit(&store, &token, 101, &[1]);
        let until = store
            .install_verified_account_lease("account-b", "installation", "boot", 1, i64::MAX)
            .unwrap()
            .token();
        assert!(commit(&store, &until, 0, &[]).baseline);
        assert_eq!(commit(&store, &until, 1, &[1]).inserted, 1);
        let old = store.inbound_checkpoint(&token.account_id, STREAM).unwrap();
        assert!(
            commit_page(&store, &token, 2, old.as_ref(), page(200, &[2]))
                .unwrap()
                .baseline
        );
        let current = store.inbound_checkpoint(&token.account_id, STREAM).unwrap();
        assert!(cursor_for(current.as_ref(), 1).is_err());
        assert_eq!(
            store.pending_inbound_receipts(&token, 128).unwrap().len(),
            1
        );
    }
    #[test]
    fn byte_budget_rejects_oversized_page_without_cursor_loss() {
        let (_dir, store, token) = fixture();
        commit(&store, &token, 100, &[]);
        let old = store.inbound_checkpoint(&token.account_id, STREAM).unwrap();
        let mut large = page(101, &[1]);
        large.messages[0].content_json = "x".repeat(1024 * 1024);
        assert!(commit_page(&store, &token, 1, old.as_ref(), large).is_err());
        assert_eq!(
            store
                .inbound_checkpoint(&token.account_id, STREAM)
                .unwrap()
                .unwrap()
                .checkpoint,
            100
        );
    }
    #[test]
    fn acknowledged_payload_releases_capacity_without_replaying_the_receipt() {
        let (_dir, store, token) = fixture();
        commit(&store, &token, 100, &[]);
        let ids: Vec<u64> = (1..=128).collect();
        commit(&store, &token, 200, &ids);
        let ack = store
            .mark_inbound_processed(&token, STREAM, 1, "1")
            .unwrap();
        assert!(ack.applied);
        assert!(ack.receipt.payload.is_none());
        assert_eq!(commit(&store, &token, 201, &[1, 129]).inserted, 1);
        let rows = store.pending_inbound_receipts(&token, 200).unwrap();
        assert_eq!(rows.len(), 128);
        assert!(!rows.iter().any(|r| r.event_id == "1"));
        assert!(
            !store
                .mark_inbound_processed(&token, STREAM, 1, "1")
                .unwrap()
                .applied
        );
    }
}
