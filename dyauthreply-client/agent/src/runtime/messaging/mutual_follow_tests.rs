use super::*;
use crate::{
    protocol::inbox::{InboxMessage, InboxPage},
    runtime::inbound,
    store::SegmentTransition,
    workbench::Workbench,
};

const NOTICE: &str = "我们已互相关注，可以开始聊天了";
struct Fixture {
    dir: tempfile::TempDir,
    store: CoreStore,
    workbench: Workbench,
    lease: LeaseToken,
    policy: AutomationPolicy,
    engine: RuleEngine,
    now: i64,
    cutoff_us: u64,
}
impl Fixture {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(dir.path()).unwrap();
        let workbench = Workbench::open(dir.path()).unwrap();
        workbench.ensure_account("account", "self").unwrap();
        let now = crate::workbench::now_ms();
        let lease = store
            .install_verified_account_lease("account", "i", "b", 1, now + 60000)
            .unwrap()
            .token();
        store
            .restore_send_observation(&lease, "self", &"a".repeat(64))
            .unwrap();
        store
            .record_inbound_page(&lease, inbound::STREAM, 1, 1, &[])
            .unwrap();
        let mut policy = super::tests::policy();
        policy.silent_start = None;
        policy.silent_end = None;
        policy.min_interval_seconds = 0;
        policy.max_interval_seconds = 0;
        let engine = RuleEngine::from_json(br#"{"version":1,"revision":1,"timezone":"Asia/Shanghai","rules":[{"id":"r","match_type":"default","reply_text":"welcome","cooldown_seconds":3600}]}"#).unwrap();
        Self {
            dir,
            store,
            workbench,
            lease,
            policy,
            engine,
            now,
            cutoff_us: u64::try_from(now - 2000).unwrap() * 1000,
        }
    }
    fn event(&self, id: &str, sender: &str, text: &str) -> InboundEvent {
        InboundEvent {
            version: 1,
            server_message_id: id.into(),
            conversation_id: "0:1:100:200".into(),
            conversation_short_id: "123".into(),
            sender_uid: if sender == "self" { "100" } else { "200" }.into(),
            sender_sec_uid: sender.into(),
            client_message_id: format!("00000000-0000-0000-0000-{id:0>12}"),
            message_type: 1,
            create_time_us: u64::try_from(self.now - 1000).unwrap() * 1000,
            content_json: serde_json::json!({"text":text}).to_string(),
            text: Some(text.into()),
        }
    }
    fn seed_peer(&self) {
        self.workbench
            .project("account", "self", &[self.event("10", "peer", "old")])
            .unwrap();
    }
    fn receive(&self, event: &InboundEvent, ws: bool) -> super::super::ReceiveProcessing {
        let message = InboxMessage {
            conversation_id: event.conversation_id.clone(),
            conversation_short_id: event.conversation_short_id.parse().unwrap(),
            server_message_id: event.server_message_id.parse().unwrap(),
            sender_uid: event.sender_uid.parse().unwrap(),
            sender_sec_uid: event.sender_sec_uid.clone(),
            client_message_id: event.client_message_id.clone(),
            message_type: event.message_type,
            create_time_us: event.create_time_us,
            content_json: event.content_json.clone(),
            text: event.text.clone(),
        };
        if ws {
            inbound::commit_frontier(&self.store, &self.lease, 1, vec![message]).unwrap();
        } else {
            let previous = self
                .store
                .inbound_checkpoint("account", inbound::STREAM)
                .unwrap()
                .unwrap();
            inbound::commit_page(
                &self.store,
                &self.lease,
                1,
                Some(&previous),
                InboxPage {
                    status_code: 0,
                    status_message: String::new(),
                    wrapper_present: true,
                    next_cursor: u64::try_from(previous.checkpoint + 1).unwrap(),
                    messages: vec![message],
                },
            )
            .unwrap();
        }
        super::super::filter_pending(
            &self.store,
            &self.lease,
            "self",
            self.cutoff(),
            Some(&self.workbench),
        )
        .unwrap()
    }
    fn cutoff(&self) -> u64 {
        self.cutoff_us
    }
    fn run(&self) -> Vec<String> {
        PlanPass {
            workbench: Some(&self.workbench),
            store: &self.store,
            lease: &self.lease,
            policy: &self.policy,
            engine: &self.engine,
            own: "self",
            cutoff: self.cutoff(),
            now: self.now,
        }
        .run()
        .unwrap()
    }
    fn pending(&self) -> usize {
        self.store
            .pending_inbound_receipts(&self.lease, 128)
            .unwrap()
            .len()
    }
    fn count(&self, table: &str) -> i64 {
        rusqlite::Connection::open(self.store.database_path())
            .unwrap()
            .query_row(&format!("SELECT count(*) FROM {table}"), [], |r| r.get(0))
            .unwrap()
    }
    fn confirm(&self, id: &str) {
        let batch = self.store.outbound_batch(id).unwrap();
        self.store
            .transition_segment(
                &self.lease,
                &batch.segments[0].id,
                SegmentTransition::StartAttempt,
            )
            .unwrap();
        self.store
            .transition_segment(
                &self.lease,
                &batch.segments[0].id,
                SegmentTransition::Confirm {
                    platform_message_id: "fixture-delivery".into(),
                },
            )
            .unwrap();
    }
}

#[test]
fn mutual_follow_both_transports_resolve_peer_and_one_durable_batch_after_restart() {
    for first in ["self", "peer"] {
        let mut f = Fixture::new();
        f.seed_peer();
        let first_event = f.event("101", first, NOTICE);
        assert_eq!(f.receive(&first_event, true).candidates, 1);
        let ids = f.run();
        assert_eq!(ids.len(), 1);
        let db = rusqlite::Connection::open(f.store.database_path()).unwrap();
        let raw: String = db
            .query_row("SELECT policy_json FROM auto_reply_batches", [], |r| {
                r.get(0)
            })
            .unwrap();
        let guard: GuardPolicy = serde_json::from_str(&raw).unwrap();
        assert_eq!(guard.peer_id, "peer");
        assert_eq!(guard.cooldown_ms, 0);
        assert!(!guard.daily_peer_limit);
        // Same server ID over HTTP must not queue again; paired other-direction
        // ID after reopening must not queue again even while the first is pending.
        f.receive(&first_event, false);
        let replay = f.run();
        assert!(replay.is_empty() || replay == ids);
        assert_eq!(f.count("outbound_batches"), 1);
        f.store = CoreStore::open(f.dir.path()).unwrap();
        let other = f.event("102", if first == "self" { "peer" } else { "self" }, NOTICE);
        f.receive(&other, false);
        assert!(f.run().is_empty());
        assert_eq!(f.count("outbound_batches"), 1);
        assert_eq!(f.pending(), 0);
    }
}

#[test]
fn mutual_follow_missing_peer_waits_then_recovers_without_rewriting_sender() {
    let f = Fixture::new();
    let event = f.event("101", "self", NOTICE);
    f.receive(&event, true);
    assert!(f.run().is_empty());
    assert_eq!(f.pending(), 1);
    f.seed_peer();
    assert_eq!(f.run().len(), 1);
    let db = rusqlite::Connection::open(f.dir.path().join("workbench.sqlite3")).unwrap();
    let direction: String = db
        .query_row(
            "SELECT direction FROM messages WHERE server_id='101'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(direction, "out");
}

#[test]
fn mutual_follow_blacklists_rules_history_and_account_quota_still_apply() {
    for mode in [
        "peer",
        "content",
        "unknown_nickname",
        "no_rule",
        "history",
        "expired",
        "quota",
    ] {
        let mut f = Fixture::new();
        f.seed_peer();
        match mode {
            "peer" => f.policy.blocked_peers.push("peer".into()),
            "content" => f.policy.blocked_content_keywords.push("互相关注".into()),
            "unknown_nickname" => f.policy.blocked_nickname_keywords.push("blocked".into()),
            "no_rule" => {
                f.engine = RuleEngine::from_json(
                    br#"{"version":1,"revision":1,"timezone":"Asia/Shanghai","rules":[]}"#,
                )
                .unwrap();
            }
            "quota" => f.policy.daily_quota = 0,
            _ => {}
        }
        let mut event = f.event("101", "self", NOTICE);
        if mode == "history" {
            event.create_time_us = f.cutoff();
        }
        if mode == "expired" {
            event.create_time_us -= 301_000_000;
            f.cutoff_us = event.create_time_us - 1;
        }
        f.receive(&event, true);
        assert!(f.run().is_empty(), "{mode}");
        assert_eq!(f.count("outbound_batches"), 0, "{mode}");
        assert_eq!(f.count("mutual_follow_claims"), 0, "{mode}");
        assert_eq!(
            f.pending(),
            usize::from(mode == "unknown_nickname"),
            "{mode}"
        );
    }
}

#[test]
fn mutual_follow_bypasses_prior_peer_limit_and_rule_cooldown_not_account_quota() {
    for quota in [1, 10] {
        let mut f = Fixture::new();
        f.seed_peer();
        f.policy.daily_peer_limit = true;
        f.policy.daily_quota = quota;
        f.receive(&f.event("100", "peer", "hello"), true);
        let ids = f.run();
        assert_eq!(ids.len(), 1);
        f.confirm(&ids[0]);
        f.receive(&f.event("101", "self", NOTICE), true);
        assert_eq!(f.run().len(), usize::from(quota > 1));
    }
}

#[test]
fn mutual_follow_claim_failure_rolls_back_receipt_outbox_and_guards() {
    let f = Fixture::new();
    f.seed_peer();
    f.receive(&f.event("101", "self", NOTICE), true);
    let db = rusqlite::Connection::open(f.store.database_path()).unwrap();
    db.execute_batch("CREATE TRIGGER fail_follow BEFORE INSERT ON mutual_follow_claims BEGIN SELECT RAISE(ABORT,'fixture'); END;").unwrap();
    let pass = PlanPass {
        workbench: Some(&f.workbench),
        store: &f.store,
        lease: &f.lease,
        policy: &f.policy,
        engine: &f.engine,
        own: "self",
        cutoff: f.cutoff(),
        now: f.now,
    };
    assert!(pass.run().is_err());
    assert_eq!(f.pending(), 1);
    assert_eq!(f.count("outbound_batches"), 0);
    assert_eq!(f.count("auto_reply_batches"), 0);
    assert_eq!(f.count("reply_guard_scopes"), 0);
    db.execute_batch("DROP TRIGGER fail_follow").unwrap();
    assert_eq!(f.run().len(), 1);
}

#[test]
fn mutual_follow_text_sent_by_us_is_still_an_echo_at_receive_and_plan() {
    for use_receive_filter in [true, false] {
        let f = Fixture::new();
        f.seed_peer();
        let sent = f
            .store
            .prepare_outbound_batch(
                &f.lease,
                "manual",
                "route",
                &[OutboundSegmentDraft::text(NOTICE)],
            )
            .unwrap();
        let mut e = f.event("101", "self", NOTICE);
        e.client_message_id
            .clone_from(&sent.segments[0].client_message_id);
        if use_receive_filter {
            assert_eq!(f.receive(&e, true).ignored, 1);
        } else {
            let payload = serde_json::to_vec(&e).unwrap();
            f.store
                .record_inbound_page(
                    &f.lease,
                    "ws",
                    1,
                    1,
                    &[crate::store::InboundReceiptDraft {
                        event_id: e.server_message_id,
                        payload_hash: format!("{:x}", Sha256::digest(&payload)),
                        payload,
                    }],
                )
                .unwrap();
        }
        assert!(f.run().is_empty());
        assert_eq!(f.count("outbound_batches"), 1);
        assert_eq!(f.count("mutual_follow_claims"), 0);
        assert_eq!(f.pending(), 0);
        assert!(!f
            .store
            .has_outbound_client_message("different-account", &e.client_message_id)
            .unwrap());
    }
}

#[test]
fn mutual_follow_pairing_window_does_not_suppress_later_events_or_other_conversations() {
    let f = Fixture::new();
    f.seed_peer();
    let first = f.event("101", "self", NOTICE);
    f.receive(&first, true);
    let ids = f.run();
    f.confirm(&ids[0]);
    let mut twin = f.event("102", "peer", NOTICE);
    twin.create_time_us += 25_000;
    f.receive(&twin, false);
    assert!(f.run().is_empty());
    let mut later = f.event("103", "self", NOTICE);
    later.create_time_us += 6_000_000;
    f.receive(&later, true);
    let ids = f.run();
    assert_eq!(ids.len(), 1);
    f.confirm(&ids[0]);
    let mut other = f.event("104", "peer2", NOTICE);
    other.conversation_id = "0:1:100:300".into();
    other.conversation_short_id = "124".into();
    f.receive(&other, false);
    assert_eq!(f.run().len(), 1);
    assert_eq!(f.count("outbound_batches"), 3);
}

#[test]
fn diagnostic_mutual_follow_queue_and_send_share_trace_after_restart() {
    use tracing_subscriber::layer::SubscriberExt;
    let mut f = Fixture::new();
    f.seed_peer();
    f.receive(&f.event("111", "peer", NOTICE), true);
    let diagnostics = std::sync::Arc::new(crate::diagnostics::Shared::new());
    diagnostics.set_enabled(true).unwrap();
    let subscriber = tracing_subscriber::registry().with(
        crate::diagnostics::DiagnosticsLayer::new(diagnostics.clone()),
    );
    tracing::subscriber::with_default(subscriber, || {
        let ids = f.run();
        assert_eq!(ids.len(), 1);
        f.store = CoreStore::open(f.dir.path()).unwrap();
        let batch = f.store.outbound_batch(&ids[0]).unwrap();
        let work = WorkEnvelope::new(
            AccountId::new("account").unwrap(),
            ids[0].clone(),
            WorkKind::AutomaticReply,
            crate::runtime::model::WorkFence {
                actor_generation: 1,
                credential_generation: 1,
                lease_epoch: 1,
            },
            0,
        );
        super::super::diagnostic_events::log_send_outcome(
            &work,
            &batch,
            &batch.segments[0],
            &crate::protocol::live_sender::SendOutcome {
                classification: crate::protocol::DeliveryClass::Delivered,
                platform_message_id: Some("synthetic-platform-id".into()),
                response_codes: Some("http=200,outer=Some(0)".into()),
            },
            25,
        );
    });
    let events = diagnostics.take();
    let queued = events
        .iter()
        .find(|e| e.message == "auto-reply queued")
        .unwrap();
    let sent = events
        .iter()
        .find(|e| e.message == "native send completed")
        .unwrap();
    assert_eq!(queued.fields["trigger_type"], "mutual_follow");
    assert_eq!(queued.fields["trace_id"], sent.fields["trace_id"]);
    assert_eq!(sent.fields["message_source"], "automatic");
    assert_eq!(sent.fields["segment_index"], 1);
    assert_eq!(sent.fields["attempt"], 1);
    assert_eq!(sent.fields["elapsed_ms"], 25);
    let output = serde_json::to_string(&events).unwrap();
    assert!(
        !output.contains(NOTICE)
            && !output.contains("welcome")
            && !output.contains("synthetic-platform-id")
    );
}
