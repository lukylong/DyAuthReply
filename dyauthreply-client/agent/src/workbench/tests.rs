use super::*;
fn event(server: &str, own: &str) -> InboundEvent {
    InboundEvent {
        version: 1,
        server_message_id: server.into(),
        conversation_id: "platform-route".into(),
        conversation_short_id: "7681885293746357819".into(),
        sender_uid: "42".into(),
        sender_sec_uid: own.into(),
        client_message_id: "client-exact".into(),
        message_type: 1,
        create_time_us: u64::try_from(now_ms()).unwrap() * 1000,
        content_json: r#"{"text":"hello"}"#.into(),
        text: Some("hello".into()),
    }
}
#[test]
fn exact_server_identity_deduplicates_ws_http_and_outbox_not_text() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    db.ensure_account("b", "other").unwrap();
    for _ in 0..3 {
        db.project("a", "self", &[event("7681885293746357820", "self")])
            .unwrap();
    }
    db.project("a", "self", &[event("7681885293746357821", "self")])
        .unwrap();
    let conversations = db.conversations("a", 1, 50, "").unwrap();
    let id = conversations["items"][0]["id"].as_str().unwrap();
    assert_eq!(db.messages("a", id).unwrap().as_array().unwrap().len(), 2);
    assert!(db.messages("b", id).is_err());
    assert_eq!(db.route("a", id).unwrap().1, "7681885293746357819");
    let mut bad = event("7681885293746357820", "self");
    bad.text = Some("different".into());
    assert!(db.project("a", "self", &[bad]).is_err());
    drop(db);
    let reopened = Workbench::open(root.path()).unwrap();
    assert_eq!(reopened.conversations("a", 1, 50, "").unwrap()["total"], 1);
}
#[test]
fn retention_discards_old_bodies_without_correctness_store_changes() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut old = event("old", "self");
    old.create_time_us = u64::try_from(now_ms() - RETENTION_MS - 1).unwrap() * 1000;
    db.project("a", "self", &[old]).unwrap();
    assert_eq!(db.conversations("a", 1, 50, "").unwrap()["total"], 0);
    db.project("a", "self", &[event("new", "peer")]).unwrap();
    db.lock()
        .unwrap()
        .execute("UPDATE messages SET at_ms=1", [])
        .unwrap();
    db.maintain().unwrap();
    assert_eq!(
        db.lock()
            .unwrap()
            .query_row("SELECT count(*) FROM messages", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        0
    );
}
#[test]
fn projection_failure_preserves_transaction_and_notification_order() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut notices = db.changed.subscribe();
    let mut bad = event("bad", "peer");
    bad.content_json = "x".repeat(65537);
    assert!(db
        .project("a", "self", &[event("new", "peer"), bad])
        .is_err());
    assert_eq!(db.conversations("a", 1, 50, "").unwrap()["total"], 0);
    assert!(notices.try_recv().is_err());
    db.project("a", "self", &[event("new", "peer")]).unwrap();
    assert_eq!(notices.try_recv().unwrap()["type"], "new_message");
    assert!(db.conversations("a", 0, 500, "").is_err());
    assert!(db.ensure_account("a", "another").is_err());
}

#[test]
fn legacy_import_is_atomic_idempotent_and_native_echo_repairs_old_direction() {
    let source = tempfile::tempdir().unwrap();
    let native = tempfile::tempdir().unwrap();
    let path = source.path().join("legacy.sqlite3");
    let db = Connection::open(&path).unwrap();
    db.execute_batch("CREATE TABLE core_douyin_account(id TEXT,sec_uid TEXT,nickname TEXT,avatar TEXT,unique_id TEXT,daily_reply_quota INTEGER,is_deleted INTEGER,deleted_at TEXT);
CREATE TABLE core_douyin_conversation(id TEXT,account_id TEXT,platform_conversation_id TEXT,platform_conversation_short_id INTEGER,peer_sec_uid TEXT,peer_nickname TEXT,peer_avatar TEXT,peer_unique_id TEXT,last_message_at TEXT,last_message_preview TEXT,is_deleted INTEGER);
CREATE TABLE core_douyin_message(id TEXT,external_msg_id TEXT,direction TEXT,content_type TEXT,content TEXT,received_at TEXT,conversation_id TEXT,is_deleted INTEGER);").unwrap();
    let account = uuid::Uuid::new_v4().to_string();
    let time = iso(now_ms()).unwrap();
    db.execute(
        "INSERT INTO core_douyin_account VALUES(?1,'self','测试账号',NULL,NULL,100,0,NULL)",
        [&account],
    )
    .unwrap();
    db.execute("INSERT INTO core_douyin_conversation VALUES('conv',?1,'platform-route',7681885293746357819,'peer','联系人',NULL,NULL,?2,'hello',0)",params![account,time]).unwrap();
    db.execute("INSERT INTO core_douyin_message VALUES('legacy-message','srv_7681885293746357820','in','text','hello',?1,'conv',0)",[&time]).unwrap();
    drop(db);
    let before = std::fs::read(&path).unwrap();
    let workbench = Workbench::open(native.path()).unwrap();
    let report = workbench.import_legacy(&path).unwrap();
    assert_eq!(report["accounts"], 1);
    assert_eq!(report["retained_messages"], 1);
    assert_eq!(workbench.import_legacy(&path).unwrap(), report);
    assert_eq!(std::fs::read(&path).unwrap(), before);
    workbench
        .project(&account, "self", &[event("7681885293746357820", "self")])
        .unwrap();
    let messages = workbench.messages(&account, "conv").unwrap();
    assert_eq!(messages.as_array().unwrap().len(), 1);
    assert_eq!(messages[0]["direction"], "out");
    assert_eq!(messages[0]["client_message_id"], "client-exact");
    assert_eq!(
        workbench.route(&account, "conv").unwrap().1,
        "7681885293746357819"
    );
    let reject = tempfile::tempdir().unwrap();
    let other = Workbench::open(reject.path()).unwrap();
    other.ensure_account("another", "another").unwrap();
    assert!(other.import_legacy(&path).is_err());
}

#[test]
fn rolling_row_limit_is_account_fair_and_body_capacity_is_bounded() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    db.ensure_account("b", "peer").unwrap();
    db.project("a", "self", &[event("one", "self")]).unwrap();
    db.project("b", "peer", &[event("one", "self")]).unwrap();
    let conn = &mut *db.lock().unwrap();
    let tx = conn.transaction().unwrap();
    let conversation_id: String = tx
        .query_row(
            "SELECT id FROM conversations WHERE account_id='a'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    tx.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<=10000) INSERT INTO messages(id,account_id,conversation_id,server_id,direction,kind,content,at_ms) SELECT 'limit-'||x,'a',?1,'server-'||x,'in','text','hello',?2+x FROM n",params![conversation_id,now_ms()]).unwrap();
    prune(&tx, "a", now_ms()).unwrap();
    tx.commit().unwrap();
    assert_eq!(
        conn.query_row(
            "SELECT count(*) FROM messages WHERE account_id='a'",
            [],
            |r| r.get::<_, i64>(0)
        )
        .unwrap(),
        PER_ACCOUNT_MESSAGES
    );
    assert_eq!(
        conn.query_row(
            "SELECT count(*) FROM messages WHERE account_id='b'",
            [],
            |r| r.get::<_, i64>(0)
        )
        .unwrap(),
        1
    );
}

#[test]
fn byte_budget_counts_utf8_and_reclaims_oldest_projection_only() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    for i in 0..3 {
        let mut e = event(&i.to_string(), "peer");
        e.text = Some("中文测试".repeat(20));
        db.project("a", "self", &[e]).unwrap();
    }
    let mut c = db.lock().unwrap();
    let tx = c.transaction().unwrap();
    let before: i64 = tx
        .query_row(
            "SELECT CAST(value AS INTEGER) FROM meta WHERE key='message_bytes'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(before, 720);
    prune_bytes(&tx, 100).unwrap();
    tx.commit().unwrap();
    let after: i64 = c
        .query_row(
            "SELECT CAST(value AS INTEGER) FROM meta WHERE key='message_bytes'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert!(after <= 100);
    assert_eq!(
        c.query_row("SELECT count(*) FROM conversations", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        1
    );
}

#[test]
fn verified_profile_cache_and_peer_refresh_are_account_scoped() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let self_profile = crate::protocol::VerifiedSelf {
        user_id: "1".into(),
        sec_uid: "self".into(),
        nickname: "账号".into(),
        avatar: "https://example.com/self.jpg".into(),
        unique_id: "self-id".into(),
        follower_count: 10,
        following_count: 20,
        aweme_count: 30,
        total_favorited: 40,
    };
    let at = now_ms();
    db.set_account_profile_details("a", &self_profile, at)
        .unwrap();
    let (cached, cached_at) = db.profile_snapshot("a").unwrap().unwrap();
    assert_eq!(cached_at, at);
    assert_eq!(cached["nickname"], "账号");
    assert_eq!(cached["follower_count"], 10);
    assert_eq!(cached["cached"], true);

    db.project("a", "self", &[event("peer-message", "peer")])
        .unwrap();
    let conversation = db.conversations("a", 1, 50, "").unwrap()["items"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    assert_eq!(db.peer_scope("a", &conversation).unwrap(), "peer");
    let peer_profile = crate::protocol::VerifiedSelf {
        user_id: "2".into(),
        sec_uid: "peer".into(),
        nickname: "联系人".into(),
        avatar: "https://example.com/peer.jpg".into(),
        unique_id: "peer-id".into(),
        follower_count: 0,
        following_count: 0,
        aweme_count: 0,
        total_favorited: 0,
    };
    db.set_peer_profile("a", &conversation, &peer_profile)
        .unwrap();
    let row = db.conversations("a", 1, 50, "").unwrap();
    assert_eq!(row["items"][0]["peer_nickname"], "联系人");
    assert_eq!(
        row["items"][0]["peer_avatar"],
        "https://example.com/peer.jpg"
    );
    let mut mixed = peer_profile;
    mixed.sec_uid = "another".into();
    assert!(db.set_peer_profile("a", &conversation, &mixed).is_err());
    assert!(db.peer_scope("missing", &conversation).is_err());
}
