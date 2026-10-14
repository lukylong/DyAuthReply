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
#[test]
fn group_chat_messages_never_enter_the_direct_message_workbench() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut group = event("7681885293746357900", "peer");
    group.message_type = 2;
    group.conversation_id = "7485123456789012345".into();
    group.text = Some("group text".into());
    group.content_json = r#"{"text":"group text","aweType":700}"#.into();
    db.project("a", "self", &[group]).unwrap();
    assert_eq!(db.conversations("a", 1, 50, "").unwrap()["total"], 0);
}
#[test]
fn rich_direct_messages_are_labelled_and_control_packets_hidden() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut share = event("7681885293746357901", "peer");
    share.text = None;
    share.content_json = r#"{"itemId":"7","content_title":"秋天","aweType":800}"#.into();
    let mut control = event("7681885293746357902", "peer");
    control.sender_uid = String::new();
    control.text = None;
    control.content_json = r#"{"command_type":1,"conversation_id":"c"}"#.into();
    db.project("a", "self", &[share, control]).unwrap();
    let conversations = db.conversations("a", 1, 50, "").unwrap();
    let id = conversations["items"][0]["id"].as_str().unwrap();
    let messages = db.messages("a", id).unwrap();
    let messages = messages.as_array().unwrap();
    assert_eq!(messages.len(), 1);
    assert_eq!(messages[0]["content"], "[分享视频] 秋天");
    assert_eq!(messages[0]["content_type"], "card");
}
#[test]
fn reopening_removes_previously_projected_group_conversations_only() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut direct = event("7681885293746357903", "peer");
    direct.conversation_id = "0:1:42:43".into();
    db.project("a", "self", &[direct]).unwrap();
    {
        let conn = db.lock().unwrap();
        conn.execute("INSERT INTO conversations(id,account_id,platform_id,short_id,peer,data) VALUES('g','a','7485123456789012345','1','p','{}')", []).unwrap();
        conn.execute("INSERT INTO messages(id,account_id,conversation_id,server_id,direction,kind,content,at_ms) VALUES('m','a','g','99','in','unknown','[消息类型 2]',1)", []).unwrap();
    }
    drop(db);
    let reopened = Workbench::open(root.path()).unwrap();
    let conversations = reopened.conversations("a", 1, 50, "").unwrap();
    assert_eq!(conversations["total"], 1);
    let bytes: i64 = reopened
        .lock()
        .unwrap()
        .query_row(
            "SELECT CAST(value AS INTEGER) FROM meta WHERE key='message_bytes'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    let actual: i64 = reopened
        .lock()
        .unwrap()
        .query_row(
            "SELECT coalesce(sum(length(CAST(content AS BLOB))),0) FROM messages",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(bytes, actual);
}
#[test]
fn conversation_started_by_us_learns_its_peer_from_a_later_reply() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut ours = event("7681885293746357910", "self");
    ours.conversation_id = "0:1:1:2".into();
    db.project("a", "self", &[ours]).unwrap();
    let conversations = db.conversations("a", 1, 50, "").unwrap();
    assert_eq!(conversations["items"][0]["peer_sec_uid"], "");
    assert_eq!(conversations["items"][0]["peer_nickname"], "");
    let reply = || {
        let mut reply = event("7681885293746357911", "peer-sec");
        reply.conversation_id = "0:1:1:2".into();
        reply
    };
    db.project("a", "self", &[reply()]).unwrap();
    let id = conversations["items"][0]["id"].as_str().unwrap();
    assert_eq!(db.peer_scope("a", id).unwrap(), "peer-sec");
    // Re-projecting an already stored reply (history pages) also repairs a peerless row.
    db.lock()
        .unwrap()
        .execute("UPDATE conversations SET peer=''", [])
        .unwrap();
    db.project("a", "self", &[reply()]).unwrap();
    assert_eq!(db.peer_scope("a", id).unwrap(), "peer-sec");
}
#[test]
fn placeholder_names_are_reported_missing_and_queued_for_backfill_once() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut inbound = event("7681885293746357920", "peer-sec");
    inbound.conversation_id = "0:1:1:3".into();
    db.project("a", "self", &[inbound]).unwrap();
    // Rows written by earlier versions carried the sec_uid or a fixed text as the name.
    db.lock()
        .unwrap()
        .execute(
            "UPDATE conversations SET data=json_set(data,'$.peer_nickname','peer-sec')",
            [],
        )
        .unwrap();
    let listed = db.conversations("a", 1, 50, "").unwrap();
    assert_eq!(listed["items"][0]["peer_nickname"], "");
    let now = now_ms();
    let due = db.peers_missing_profile("a", 5, now).unwrap();
    assert_eq!(due.len(), 1);
    assert!(db
        .peers_missing_profile("a", 5, now + 60_000)
        .unwrap()
        .is_empty());
    assert_eq!(
        db.peers_missing_profile("a", 5, now + PEER_PROFILE_RETRY_MS + 1)
            .unwrap()
            .len(),
        1
    );
    let id = listed["items"][0]["id"].as_str().unwrap();
    db.set_peer_profile(
        "a",
        id,
        &crate::protocol::VerifiedSelf {
            user_id: "3".into(),
            sec_uid: "peer-sec".into(),
            nickname: "真名".into(),
            avatar: "https://example.com/a.jpg".into(),
            unique_id: "u".into(),
            follower_count: 0,
            following_count: 0,
            aweme_count: 0,
            total_favorited: 0,
        },
    )
    .unwrap();
    assert!(db
        .peers_missing_profile("a", 5, now + 2 * PEER_PROFILE_RETRY_MS)
        .unwrap()
        .is_empty());
}
#[test]
fn peerless_direct_chats_are_resolved_once_and_never_overwritten() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut ours = event("7681885293746357930", "self");
    ours.conversation_id = "0:1:1:2".into();
    db.project("a", "self", &[ours]).unwrap();
    let now = now_ms();
    let due = db.peerless_conversations("a", 5, now).unwrap();
    assert_eq!(due.len(), 1);
    assert_eq!(due[0].1, "0:1:1:2");
    assert_eq!(due[0].2, 7_681_885_293_746_357_819);
    assert!(db
        .peerless_conversations("a", 5, now + 1)
        .unwrap()
        .is_empty());
    assert!(db
        .set_conversation_peer("a", &due[0].0, "peer-sec")
        .unwrap());
    assert_eq!(db.peer_scope("a", &due[0].0).unwrap(), "peer-sec");
    assert!(!db.set_conversation_peer("a", &due[0].0, "other").unwrap());
    assert_eq!(db.peer_scope("a", &due[0].0).unwrap(), "peer-sec");
    assert!(db
        .set_conversation_peer("a", &due[0].0, "fallback_x")
        .is_err());
}
#[test]
fn rich_media_is_shown_with_its_preview_and_counted_against_the_byte_budget() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut image = event("7681885293746357940", "peer");
    image.text = None;
    image.content_json = r#"{"resource_url":{"skey":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","origin_url_list":["https://p3-im.douyinpic.com/o"]},"cover_width":1,"cover_height":2,"inline_pic":"UklGRg=="}"#.into();
    db.project("a", "self", &[image]).unwrap();
    let conversations = db.conversations("a", 1, 50, "").unwrap();
    let id = conversations["items"][0]["id"].as_str().unwrap();
    let messages = db.messages("a", id).unwrap();
    let message = &messages[0];
    assert_eq!(message["content_type"], "image");
    assert_eq!(message["media"]["encrypted"], true);
    assert_eq!(message["media"]["inline_pic"], "UklGRg==");
    assert!(message["media"].get("skey").is_none());
    let source = db
        .media_source("a", id, message["id"].as_str().unwrap())
        .unwrap()
        .unwrap();
    assert_eq!(source.url, "https://p3-im.douyinpic.com/o");
    assert!(db
        .media_source("b", id, message["id"].as_str().unwrap())
        .is_err());
    let (counted, actual): (i64, i64) = {
        let conn = db.lock().unwrap();
        (
            conn.query_row("SELECT CAST(value AS INTEGER) FROM meta WHERE key='message_bytes'", [], |r| r.get(0)).unwrap(),
            conn.query_row("SELECT sum(length(CAST(content AS BLOB))+length(CAST(coalesce(media,'') AS BLOB))) FROM messages", [], |r| r.get(0)).unwrap(),
        )
    };
    assert_eq!(counted, actual);
    drop(db);
    // Reopening is idempotent for the added columns and triggers.
    Workbench::open(root.path()).unwrap();
}
#[test]
fn reprojection_refreshes_labels_and_media_but_never_rewrites_text() {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    let mut text = event("7681885293746357950", "peer");
    text.conversation_id = "0:1:1:2".into();
    db.project("a", "self", &[text]).unwrap();
    let image_json = r#"{"resource_url":{"skey":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef","large_url_list":["https://p5-ex.douyinpic.com/l"]},"cover_width":1,"cover_height":2}"#;
    let push_json = "";
    // Rows an older version stored without media / with a placeholder label.
    {
        let conn = db.lock().unwrap();
        let conversation: String = conn
            .query_row("SELECT id FROM conversations", [], |r| r.get(0))
            .unwrap();
        for (server, content) in [
            ("7681885293746357951", "[暂不支持的消息类型]"),
            ("7681885293746357952", "[暂不支持的消息类型]"),
        ] {
            conn.execute(
                "INSERT INTO messages(id,account_id,conversation_id,server_id,direction,kind,content,at_ms) VALUES(?1,'a',?2,?3,'in','other',?4,?5)",
                rusqlite::params![uuid::Uuid::new_v4().to_string(), conversation, server, content, now_ms()],
            )
            .unwrap();
        }
    }
    let rich = |server: &str, json: &str| {
        let mut e = event(server, "peer");
        e.conversation_id = "0:1:1:2".into();
        e.text = None;
        e.content_json = json.into();
        e
    };
    db.project(
        "a",
        "self",
        &[
            rich("7681885293746357951", image_json),
            rich("7681885293746357952", push_json),
        ],
    )
    .unwrap();
    let id = db.conversations("a", 1, 10, "").unwrap()["items"][0]["id"]
        .as_str()
        .unwrap()
        .to_owned();
    let messages = db.messages("a", &id).unwrap();
    let messages = messages.as_array().unwrap();
    assert_eq!(messages.len(), 2, "the content-less placeholder is removed");
    let image = messages
        .iter()
        .find(|m| m["content_type"] == "image")
        .unwrap();
    assert_eq!(image["media"]["url"], "https://p5-ex.douyinpic.com/l");
    assert!(db
        .media_source("a", &id, image["id"].as_str().unwrap())
        .unwrap()
        .is_some());
    // A changed body for a stored text message is still a conflict.
    let mut altered = event("7681885293746357950", "peer");
    altered.conversation_id = "0:1:1:2".into();
    altered.text = Some("different".into());
    altered.content_json = r#"{"text":"different"}"#.into();
    assert!(db.project("a", "self", &[altered]).is_err());
}
