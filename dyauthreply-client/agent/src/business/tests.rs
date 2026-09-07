use super::*;
fn policy() -> AutomationPolicy {
    serde_json::from_value(json!({"account_id":"a","enabled":false,"daily_quota":10,"min_interval_seconds":0,"max_interval_seconds":0})).unwrap()
}
fn fixture() -> (tempfile::TempDir, BusinessStore) {
    let d = tempfile::tempdir().unwrap();
    let store = BusinessStore::open(d.path(), Document::seed(vec![], vec![policy()])).unwrap();
    (d, store)
}
fn create(store: &BusinessStore, text: &str) -> Value {
    store.change(|d|Edit::Rule{id:None,input:json!({"name":"测试规则","match_type":"contains","keywords":["hello"],"reply_text":text,"account_ids":["a"]})}.apply(d)).unwrap()
}
#[test]
fn durable_compiled_configuration_updates_as_one_generation() {
    let (root, store) = fixture();
    let rule = create(&store, "world");
    let first = store.snapshot().unwrap();
    assert_eq!(first.document.revision, 2);
    assert!(store
        .change(|d| Edit::Rule {
            id: Some(rule["id"].as_str().unwrap().into()),
            input: json!({"match_type":"regex","regex_pattern":"["})
        }
        .apply(d))
        .is_err());
    assert_eq!(store.snapshot().unwrap().document.revision, 2);
    drop(store);
    let reopened = BusinessStore::open(root.path(), Document::seed(vec![], vec![])).unwrap();
    assert_eq!(
        reopened.snapshot().unwrap().document.rules[0]["reply_text"],
        "world"
    );
}
#[test]
fn moving_last_account_disables_old_rule_instead_of_making_it_global() {
    let (_root, store) = fixture();
    let old = create(&store, "old");
    let input = json!({"name":"new","match_type":"default","reply_text":"new","account_ids":["a"]});
    assert!(store
        .change(|d| Edit::Rule {
            id: None,
            input: input.clone()
        }
        .apply(d))
        .unwrap_err()
        .to_string()
        .contains("account_conflict"));
    let mut force = input;
    force["force_move"] = json!(true);
    store
        .change(|d| {
            Edit::Rule {
                id: None,
                input: force,
            }
            .apply(d)
        })
        .unwrap();
    let snapshot = store.snapshot().unwrap();
    let original = snapshot
        .document
        .rules
        .iter()
        .find(|r| r["id"] == old["id"])
        .unwrap();
    assert_eq!(original["status"], false);
    assert_eq!(original["account_ids"], json!([]));
}
#[test]
fn activation_boundary_is_persisted_and_not_reset_by_unrelated_edit() {
    let (root, store) = fixture();
    create(&store, "reply");
    store
        .change(|d| {
            Edit::Account {
                id: "a".into(),
                input: json!({"auto_reply_enabled":true}),
            }
            .apply(d)
        })
        .unwrap();
    let boundary = store.snapshot().unwrap().policies["a"].enabled_since_us;
    assert!(boundary > 0);
    store
        .change(|d| {
            Edit::Account {
                id: "a".into(),
                input: json!({"daily_reply_quota":20}),
            }
            .apply(d)
        })
        .unwrap();
    assert_eq!(
        store.snapshot().unwrap().policies["a"].enabled_since_us,
        boundary
    );
    drop(store);
    let reopened = BusinessStore::open(root.path(), Document::seed(vec![], vec![])).unwrap();
    assert_eq!(
        reopened.snapshot().unwrap().policies["a"].enabled_since_us,
        boundary
    );
}

#[test]
fn emergency_stop_disables_every_policy_in_one_durable_generation() {
    let (root, store) = fixture();
    store
        .change(|document| {
            Edit::Account {
                id: "a".into(),
                input: json!({"auto_reply_enabled":true}),
            }
            .apply(document)
        })
        .unwrap();
    let response = store
        .change(|document| Edit::EmergencyStop.apply(document))
        .unwrap();
    assert_eq!(response["disabled_policies"], 1);
    assert!(store
        .snapshot()
        .unwrap()
        .policies
        .values()
        .all(|policy| !policy.enabled));
    drop(store);
    let reopened = BusinessStore::open(root.path(), Document::seed(vec![], vec![])).unwrap();
    assert!(reopened
        .snapshot()
        .unwrap()
        .policies
        .values()
        .all(|policy| !policy.enabled));
}

#[test]
fn card_projection_states_and_reference_safe_delete_are_durable() {
    let (root, store) = fixture();
    let card = store
        .change(|document| {
            Edit::Card {
                id: None,
                input: json!({
                    "title":"测试卡片",
                    "description":"描述",
                    "target_url":"https://example.com/target",
                    "status":true,
                }),
            }
            .apply(document)
        })
        .unwrap();
    let id = card["id"].as_str().unwrap().to_string();
    assert_eq!(card["sync_state"], "pending");
    let synced = store
        .change(|document| {
            Edit::CardSync {
                id: id.clone(),
                sync_state: "synced".into(),
                landing_url: Some(format!("https://cards.example.com/c/{id}")),
                cover_url: None,
            }
            .apply(document)
        })
        .unwrap();
    assert_eq!(synced["sync_state"], "synced");
    let rule = store
        .change(|document| {
            Edit::Rule {
                id: None,
                input: json!({
                    "name":"卡片规则",
                    "match_type":"contains",
                    "keywords":["card"],
                    "reply_text":"reply",
                    "account_ids":["a"],
                    "card_ids":[id.clone()],
                }),
            }
            .apply(document)
        })
        .unwrap();
    assert!(store
        .change(|document| Edit::BeginDeleteCard(id.clone()).apply(document))
        .is_err());
    store
        .change(|document| Edit::DeleteRule(rule["id"].as_str().unwrap().into()).apply(document))
        .unwrap();
    let pending = store
        .change(|document| Edit::BeginDeleteCard(id.clone()).apply(document))
        .unwrap();
    assert_eq!(pending["status"], false);
    assert_eq!(pending["sync_state"], "delete_pending");
    store
        .change(|document| Edit::FinishDeleteCard(id).apply(document))
        .unwrap();
    assert!(store.snapshot().unwrap().document.cards.is_empty());
    drop(store);
    assert!(
        BusinessStore::open(root.path(), Document::seed(vec![], vec![]))
            .unwrap()
            .snapshot()
            .unwrap()
            .document
            .cards
            .is_empty()
    );
}

#[test]
fn invalid_card_target_never_changes_the_published_generation() {
    let (_root, store) = fixture();
    let revision = store.snapshot().unwrap().document.revision;
    assert!(store
        .change(|document| {
            Edit::Card {
                id: None,
                input: json!({"title":"bad","target_url":"javascript:alert(1)"}),
            }
            .apply(document)
        })
        .is_err());
    assert_eq!(store.snapshot().unwrap().document.revision, revision);
    assert!(store.snapshot().unwrap().document.cards.is_empty());
}
#[test]
fn template_references_are_validated_and_clones_never_start_sending() {
    let (root, store) = fixture();
    let t = store
        .change(|d| {
            Edit::Template {
                id: None,
                input: json!({"name":"模板","content":"内容"}),
            }
            .apply(d)
        })
        .unwrap();
    let rule = store
        .change(|d| {
            Edit::Rule {
                id: None,
                input: json!({"name":"rule","match_type":"default","template_id":t["id"]}),
            }
            .apply(d)
        })
        .unwrap();
    assert!(store
        .change(|d| Edit::DeleteTemplate(t["id"].as_str().unwrap().into()).apply(d))
        .is_err());
    let clone = store
        .change(|d| Edit::CloneRule(rule["id"].as_str().unwrap().into()).apply(d))
        .unwrap();
    assert_eq!(clone["status"], false);
    assert!(BusinessStore::open(root.path(), Document::seed(vec![], vec![])).is_err());
}

#[test]
fn failed_reference_updates_keep_previous_snapshot_and_old_readers_immutable() {
    let (_root, store) = fixture();
    let rule = create(&store, "before");
    let old = store.snapshot().unwrap();
    store
        .change(|d| {
            Edit::Rule {
                id: Some(rule["id"].as_str().unwrap().into()),
                input: json!({"reply_text":"after","cooldown_seconds":0}),
            }
            .apply(d)
        })
        .unwrap();
    assert_eq!(old.document.rules[0]["reply_text"], "before");
    assert_eq!(
        store.snapshot().unwrap().document.rules[0]["cooldown_seconds"],
        0
    );
    let revision = store.snapshot().unwrap().document.revision;
    assert!(store
        .change(|d| Edit::Rule {
            id: Some(rule["id"].as_str().unwrap().into()),
            input: json!({"template_id":"missing"})
        }
        .apply(d))
        .is_err());
    assert_eq!(store.snapshot().unwrap().document.revision, revision);
}

#[test]
fn corrupt_persistent_revision_is_rejected_not_replaced_with_empty_seed() {
    let (root, store) = fixture();
    create(&store, "existing");
    drop(store);
    let db = Connection::open(root.path().join("business.sqlite3")).unwrap();
    db.execute("UPDATE configuration SET revision=999", [])
        .unwrap();
    drop(db);
    assert!(BusinessStore::open(root.path(), Document::seed(vec![], vec![])).is_err());
}

#[test]
fn concurrent_edits_serialize_without_losing_either_patch() {
    let (_root, store) = fixture();
    let store = Arc::new(store);
    let rule = create(&store, "existing");
    let id = rule["id"].as_str().unwrap().to_owned();
    let threads: Vec<_> = [json!({"priority":7}), json!({"cooldown_seconds":8})]
        .into_iter()
        .map(|input| {
            let store = store.clone();
            let id = id.clone();
            std::thread::spawn(move || {
                store
                    .change(|d| {
                        Edit::Rule {
                            id: Some(id),
                            input,
                        }
                        .apply(d)
                    })
                    .unwrap()
            })
        })
        .collect();
    for thread in threads {
        thread.join().unwrap();
    }
    let snapshot = store.snapshot().unwrap();
    assert_eq!(snapshot.document.rules[0]["priority"], 7);
    assert_eq!(snapshot.document.rules[0]["cooldown_seconds"], 8);
    assert_eq!(snapshot.document.revision, 4);
}

#[test]
fn failed_initial_import_can_retry_without_publishing_an_empty_configuration() {
    let root = tempfile::tempdir().unwrap();
    let bad = Document::seed(
        vec![json!({"id":"bad","name":"bad","match_type":"regex","regex_pattern":"["})],
        vec![policy()],
    );
    assert!(BusinessStore::open(root.path(), bad).is_err());
    let good = Document::seed(
        vec![json!({"id":"good","name":"good","match_type":"default","reply_text":"preserved"})],
        vec![policy()],
    );
    let store = BusinessStore::open(root.path(), good).unwrap();
    assert_eq!(store.snapshot().unwrap().engine.rule_count(), 1);
    assert_eq!(
        store.snapshot().unwrap().document.rules[0]["reply_text"],
        "preserved"
    );
}
