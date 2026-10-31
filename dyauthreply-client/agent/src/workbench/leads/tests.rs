use super::*;
use crate::runtime::inbound::InboundEvent;
fn setup() -> (tempfile::TempDir, Workbench) {
    let root = tempfile::tempdir().unwrap();
    let db = Workbench::open(root.path()).unwrap();
    db.ensure_account("a", "self").unwrap();
    db.ensure_account("b", "other-self").unwrap();
    (root, db)
}
fn event(id: &str, peer: &str, text: &str, at: i64) -> InboundEvent {
    InboundEvent {
        version: 1,
        server_message_id: id.into(),
        conversation_id: format!("0:1:1:{peer}"),
        conversation_short_id: "12345".into(),
        sender_uid: "42".into(),
        sender_sec_uid: peer.into(),
        client_message_id: String::new(),
        message_type: 1,
        create_time_us: u64::try_from(at).unwrap() * 1000,
        content_json: json!({"text":text}).to_string(),
        text: Some(text.into()),
    }
}
fn rows(db: &Workbench) -> Value {
    db.list_leads(&Filter::default(), false).unwrap()
}
#[test]
fn extracts_only_bounded_mobile_candidates() {
    for text in [
        "电话13800138000",
        "+86 13800138000",
        "8613800138000",
        "１３８００１３８０００",
        "138 0013 8000",
        "138-0013-8000",
    ] {
        assert_eq!(
            phones(text),
            BTreeSet::from(["13800138000".to_string()]),
            "{text}"
        );
    }
    for text in [
        "订单9138001380007",
        "138001380001",
        "电话138，订单0013，数量8000",
        "138\n0013\n8000",
        "12345678901",
        "138****8000",
        "+13800138000",
    ] {
        assert!(phones(text).is_empty(), "{text}");
    }
    assert_eq!(phones("13800138000、13900139000 13800138000").len(), 2);
}
#[test]
fn durable_live_jobs_are_deduplicated_independent_of_reply_and_restart() {
    let (root, db) = setup();
    let now = now_ms();
    let e = event("m1", "peer", "电话13800138000，备用13900139000", now);
    for _ in 0..3 {
        db.project("a", "self", std::slice::from_ref(&e)).unwrap();
    }
    assert_eq!(db.lead_status().unwrap()["pending"], 1);
    drop(db);
    let db = Workbench::open(root.path()).unwrap();
    assert_eq!(db.process_lead_jobs().unwrap(), 2);
    assert_eq!(rows(&db)["total"], 2);
    db.scan_lead_history("").unwrap();
    db.process_lead_jobs().unwrap();
    assert_eq!(rows(&db)["items"][0]["count"], 1);
    db.project("a", "self", &[event("m2", "peer", "13800138000", now + 1)])
        .unwrap();
    db.process_lead_jobs().unwrap();
    assert_eq!(rows(&db)["items"][0]["count"], 2);
}
#[test]
fn live_boundary_toggle_history_and_message_time_are_distinct() {
    let (_root, db) = setup();
    let now = now_ms();
    db.project(
        "a",
        "self",
        &[event("old", "peer", "13800138000", now - 60_000)],
    )
    .unwrap();
    assert_eq!(db.process_lead_jobs().unwrap(), 0);
    db.set_lead_enabled(false).unwrap();
    db.project("a", "self", &[event("off", "peer", "13800138000", now)])
        .unwrap();
    assert_eq!(db.process_lead_jobs().unwrap(), 0);
    db.scan_lead_history("a").unwrap();
    assert_eq!(db.process_lead_jobs().unwrap(), 2);
    let row = &rows(&db)["items"][0];
    assert_eq!(row["first_at"], json!(iso(now - 60_000)));
    assert_eq!(row["last_at"], json!(iso(now)));
    db.set_lead_enabled(true).unwrap();
    db.project(
        "a",
        "self",
        &[event("new", "peer", "13900139000", now_ms())],
    )
    .unwrap();
    assert_eq!(db.process_lead_jobs().unwrap(), 1);
}
#[test]
fn filters_out_self_group_system_and_rich_content() {
    let (_root, db) = setup();
    let now = now_ms();
    let mut group = event("group", "peer", "13800138000", now);
    group.message_type = 2;
    let mut system = event("system", "peer", "13800138000", now);
    system.content_json = json!({"command_type":1,"text":"13800138000"}).to_string();
    let mut card = event("card", "peer", "13800138000", now);
    card.text = None;
    card.content_json =
        json!({"itemId":"1","content_title":"13800138000","aweType":800}).to_string();
    db.project(
        "a",
        "self",
        &[
            event("self", "self", "13800138000", now),
            group,
            system,
            card,
        ],
    )
    .unwrap();
    assert_eq!(db.process_lead_jobs().unwrap(), 0);
    assert_eq!(rows(&db)["total"], 0);
}
#[test]
fn customer_and_account_scopes_never_merge_and_sources_survive_chat_pruning() {
    let (_root, db) = setup();
    let now = now_ms();
    db.project(
        "a",
        "self",
        &[
            event("m1", "peer", "13800138000", now),
            event("m2", "peer2", "13800138000", now),
        ],
    )
    .unwrap();
    db.project(
        "b",
        "other-self",
        &[event("m1", "peer", "13800138000", now)],
    )
    .unwrap();
    db.process_lead_jobs().unwrap();
    assert_eq!(rows(&db)["total"], 3);
    db.lock()
        .unwrap()
        .execute("UPDATE messages SET at_ms=1", [])
        .unwrap();
    db.maintain().unwrap();
    assert_eq!(rows(&db)["total"], 3);
    let a = db
        .list_leads(
            &Filter {
                account: "a".into(),
                ..Filter::default()
            },
            false,
        )
        .unwrap();
    assert_eq!(a["total"], 2);
    let id = a["items"][0]["id"].as_str().unwrap();
    assert_eq!(
        db.lead_sources(id, 1).unwrap()["items"][0]["content"],
        "13800138000"
    );
    let conv = a["items"][0]["conversation_id"].as_str().unwrap();
    assert!(db.lead_conversation("b", conv).is_err());
}
#[test]
fn missing_profiles_are_backfilled_without_changing_customer_identity() {
    let (_root, db) = setup();
    db.project("a", "self", &[event("m1", "peer", "13800138000", now_ms())])
        .unwrap();
    db.process_lead_jobs().unwrap();
    let first = rows(&db);
    let row = &first["items"][0];
    assert_eq!(row["nickname"], "");
    let conv = row["conversation_id"].as_str().unwrap();
    let profile = crate::protocol::VerifiedSelf {
        user_id: "42".into(),
        sec_uid: "peer".into(),
        nickname: "客户甲".into(),
        avatar: String::new(),
        unique_id: "customer.a".into(),
        follower_count: 0,
        following_count: 0,
        aweme_count: 0,
        total_favorited: 0,
    };
    db.set_peer_profile("a", conv, &profile).unwrap();
    db.process_lead_jobs().unwrap();
    let updated = rows(&db);
    assert_eq!(updated["items"][0]["id"], row["id"]);
    assert_eq!(updated["items"][0]["nickname"], "客户甲");
    assert_eq!(updated["items"][0]["douyin_id"], "customer.a");
    assert_eq!(
        db.list_leads(
            &Filter {
                keyword: "customer.a".into(),
                ..Filter::default()
            },
            false
        )
        .unwrap()["total"],
        1
    );
}
#[test]
fn failed_extraction_rolls_back_and_retries_without_losing_jobs() {
    let (_root, db) = setup();
    db.project("a", "self", &[event("m1", "peer", "13800138000", now_ms())])
        .unwrap();
    db.open_lead_store().unwrap().execute_batch("CREATE TRIGGER fail_leads BEFORE INSERT ON lead_sources BEGIN SELECT RAISE(ABORT,'fixture failure'); END;").unwrap();
    assert!(db.process_lead_jobs().is_err());
    assert_eq!(db.lead_status().unwrap()["pending"], 1);
    // Incoming projection still works while extraction is failing.
    db.project("a", "self", &[event("m2", "peer", "13900139000", now_ms())])
        .unwrap();
    db.open_lead_store()
        .unwrap()
        .execute_batch("DROP TRIGGER fail_leads")
        .unwrap();
    assert_eq!(db.process_lead_jobs().unwrap(), 2);
}
#[test]
fn deletion_does_not_resurrect_on_rescan_but_future_messages_can_create_leads() {
    let (_root, db) = setup();
    db.project("a", "self", &[event("m1", "peer", "13800138000", now_ms())])
        .unwrap();
    db.process_lead_jobs().unwrap();
    let id = rows(&db)["items"][0]["id"].as_str().unwrap().to_owned();
    db.delete_lead(&id).unwrap();
    db.scan_lead_history("").unwrap();
    db.process_lead_jobs().unwrap();
    assert_eq!(rows(&db)["total"], 0);
    db.project("a", "self", &[event("m2", "peer", "13800138000", now_ms())])
        .unwrap();
    db.process_lead_jobs().unwrap();
    assert_eq!(rows(&db)["items"][0]["count"], 1);
}
#[test]
fn export_escapes_formulas_and_date_filters_use_last_message_time() {
    let (_root, db) = setup();
    let now = now_ms();
    db.project("a", "self", &[event("m1", "peer", "13800138000", now)])
        .unwrap();
    db.process_lead_jobs().unwrap();
    let query = Filter {
        from_ms: now + 1,
        ..Filter::default()
    };
    assert_eq!(db.list_leads(&query, false).unwrap()["total"], 0);
    let csv = db.list_leads(&Filter::default(), true).unwrap();
    assert!(csv["csv"].as_str().unwrap().contains("13800138000"));
    assert_eq!(csv_cell("=1+1"), "\"'=1+1\"");
    assert_eq!(csv_cell("a\"b"), "\"a\"\"b\"");
    assert_eq!(csv_cell("\t=1"), "\"'\t=1\"");
}

#[test]
fn commit_before_ack_crash_replays_without_duplicate_count() {
    let (_root, db) = setup();
    db.project("a", "self", &[event("m1", "peer", "13800138000", now_ms())])
        .unwrap();
    db.lock().unwrap().execute_batch("CREATE TRIGGER fail_ack BEFORE DELETE ON lead_jobs BEGIN SELECT RAISE(ABORT,'fixture crash before ack'); END;").unwrap();
    assert!(db.process_lead_jobs().is_err());
    assert_eq!(rows(&db)["items"][0]["count"], 1);
    assert_eq!(db.lead_status().unwrap()["pending"], 1);
    db.lock()
        .unwrap()
        .execute_batch("DROP TRIGGER fail_ack")
        .unwrap();
    assert_eq!(db.process_lead_jobs().unwrap(), 0);
    assert_eq!(db.lead_status().unwrap()["pending"], 0);
    assert_eq!(rows(&db)["items"][0]["count"], 1);
}
#[test]
fn lead_store_failure_does_not_block_projection_and_survives_chat_db_rebuild() {
    let (root, db) = setup();
    db.project("a", "self", &[event("m1", "peer", "13800138000", now_ms())])
        .unwrap();
    db.process_lead_jobs().unwrap();
    let leads = db.open_lead_store().unwrap();
    leads.pragma_update(None, "user_version", 999).unwrap();
    db.project("a", "self", &[event("m2", "peer", "13900139000", now_ms())])
        .unwrap();
    assert!(db.process_lead_jobs().is_err());
    leads.pragma_update(None, "user_version", 1).unwrap();
    drop(leads);
    db.process_lead_jobs().unwrap();
    drop(db);
    std::fs::remove_file(root.path().join("workbench.sqlite3")).unwrap();
    let db = Workbench::open(root.path()).unwrap();
    assert_eq!(rows(&db)["total"], 2);
}
#[test]
fn bounded_worker_pagination_and_empty_handle_backfill() {
    let (_root, db) = setup();
    let now = now_ms();
    let events: Vec<_> = (0..205)
        .map(|i| event(&format!("m{i}"), &format!("peer{i}"), "13800138000", now))
        .collect();
    db.project("a", "self", &events).unwrap();
    assert_eq!(db.process_lead_jobs().unwrap(), 200);
    assert_eq!(db.lead_status().unwrap()["pending"], 5);
    assert_eq!(db.process_lead_jobs().unwrap(), 5);
    let first = rows(&db);
    let second = db
        .list_leads(
            &Filter {
                page: 2,
                ..Filter::default()
            },
            false,
        )
        .unwrap();
    assert_eq!(first["items"].as_array().unwrap().len(), 50);
    assert_eq!(first["total"], 205);
    assert_ne!(first["items"][0]["id"], second["items"][0]["id"]);
    db.lock()
        .unwrap()
        .execute(
            "UPDATE conversations SET data=json_set(data,'$.peer_nickname','已知昵称')",
            [],
        )
        .unwrap();
    assert!(!db
        .peers_missing_profile("a", 2, now + super::super::PEER_PROFILE_RETRY_MS + 1)
        .unwrap()
        .is_empty());
}

#[test]
fn remarks_keep_original_text_trim_edges_and_bind_each_phone() {
    let cases = [
        ("龙。13800138000", "龙"),
        ("得到13800138000", "得到"),
        ("  张三：+86 138-0013-8000  ", "张三"),
        ("客户１：１３８００１３８０００", "客户１"),
        ("张三\n13800138000", "张三"),
        ("13800138000", ""),
        ("，。13800138000", ""),
        ("123 13800138000", ""),
    ];
    for (text, expected) in cases {
        assert_eq!(phone_remarks(text)["13800138000"], expected, "{text}");
    }
    let rows = phone_remarks("张三13800138000，李四：13900139000");
    assert_eq!(rows["13800138000"], "张三");
    assert_eq!(rows["13900139000"], "李四");
    assert_eq!(
        phone_remarks("张三13800138000，13800138000")["13800138000"],
        "张三"
    );
}

#[test]
fn recent_nonempty_remark_survives_bare_repeat_and_is_searchable_exportable() {
    let (_root, db) = setup();
    let at = now_ms();
    db.project(
        "a",
        "self",
        &[
            event("named", "peer", "龙。13800138000", at),
            event("bare", "peer", "13800138000", at + 1),
        ],
    )
    .unwrap();
    db.process_lead_jobs().unwrap();
    let result = rows(&db);
    let lead = &result["items"][0];
    assert_eq!(lead["remark"], "龙");
    assert_eq!(lead["count"], 2);
    let id = lead["id"].as_str().unwrap();
    let sources = db.lead_sources(id, 1).unwrap();
    assert_eq!(sources["items"][0]["remark"], "");
    assert_eq!(sources["items"][1]["remark"], "龙");
    assert_eq!(
        db.list_leads(
            &Filter {
                keyword: "龙".into(),
                ..Filter::default()
            },
            false
        )
        .unwrap()["total"],
        1
    );
    let csv = db.list_leads(&Filter::default(), true).unwrap();
    assert!(csv["csv"].as_str().unwrap().contains("手机号,备注,"));
    assert!(csv["csv"].as_str().unwrap().contains("\"龙\""));
    db.project(
        "a",
        "self",
        &[event("rename", "peer", "新备注：13800138000", at + 2)],
    )
    .unwrap();
    db.process_lead_jobs().unwrap();
    assert_eq!(rows(&db)["items"][0]["remark"], "新备注");
}

#[test]
fn upgrade_backfills_old_sources_without_recounting_or_changing_source_schema() {
    let (root, db) = setup();
    db.project(
        "a",
        "self",
        &[event("named", "peer", "龙。13800138000", now_ms())],
    )
    .unwrap();
    db.process_lead_jobs().unwrap();
    let before = rows(&db);
    let store = db.open_lead_store().unwrap();
    store
        .execute_batch("DROP TRIGGER lead_remark_delete; DROP TABLE lead_source_remarks;")
        .unwrap();
    drop(store);
    drop(db);
    let db = Workbench::open(root.path()).unwrap();
    let after = rows(&db);
    assert_eq!(after, before);
    let store = db.open_lead_store().unwrap();
    let columns: i64 = store
        .query_row(
            "SELECT count(*) FROM pragma_table_info('lead_sources')",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(columns, 14);
}

#[test]
fn old_app_insert_backfills_on_reopen_and_delete_removes_remark() {
    let (_root, db) = setup();
    db.project(
        "a",
        "self",
        &[event("named", "peer", "张三13800138000", now_ms())],
    )
    .unwrap();
    db.process_lead_jobs().unwrap();
    let store = db.open_lead_store().unwrap();
    // Previous binary uses the unchanged positional 14-column insert.
    store.execute("INSERT INTO lead_sources SELECT 'legacy-insert',lead_id,account,account_name,conversation,platform,'legacy-message',peer,nickname,unique_id,phone,'旧包备注13800138000',at_ms+1,extracted_ms FROM lead_sources LIMIT 1",[]).unwrap();
    drop(store);
    let result = rows(&db);
    assert_eq!(result["items"][0]["remark"], "旧包备注");
    let id = result["items"][0]["id"].as_str().unwrap();
    db.delete_lead(id).unwrap();
    let store = db.open_lead_store().unwrap();
    assert_eq!(
        store
            .query_row("SELECT count(*) FROM lead_source_remarks", [], |r| r
                .get::<_, i64>(0))
            .unwrap(),
        0
    );
}
