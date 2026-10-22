use super::*;
use serde_json::{json, Value};

fn samples() -> Vec<(&'static str, Value, &'static str)> {
    vec![
        (
            "sticker",
            json!({"display_name":"[比心]","sticker_type":1,"url":{"url_list":["https://p3.douyinpic.com/fixture.webp"]},"width":120,"height":120}),
            "other",
        ),
        (
            "emoji_without_name",
            json!({"url":{"url_list":["https://p3.douyinpic.com/fixture.webp"]}}),
            "other",
        ),
        (
            "image",
            json!({"resource_url":{"large_url_list":["https://p3.douyinpic.com/fixture.webp"],"skey":"11111111111111111111111111111111"},"cover_width":640,"cover_height":480,"inline_pic":"fixture-preview"}),
            "image",
        ),
        (
            "voice",
            json!({"voice_wave":[1,2],"resource_url":{"url_list":["https://v3.douyinvod.com/fixture.mp3"]},"duration":3000,"ai_audio_text":"fixture transcript"}),
            "other",
        ),
        (
            "video",
            json!({"video":{"vid":"fixture-video"},"poster":{"origin_url_list":["https://p3.douyinpic.com/poster.webp"]},"duration":5}),
            "video",
        ),
        (
            "shared_video",
            json!({"itemId":"123","content_title":"测试标题"}),
            "card",
        ),
        ("live_room", json!({"room_id":"123"}), "card"),
        ("display_name", json!({"display_name":"比心"}), "text"),
        ("unicode_emoji", json!({"text":"😀"}), "text"),
    ]
}
fn media_event(f: &Fixture, id: &str, payload: &Value) -> InboundEvent {
    let mut event = f.event(id, "peer", "");
    event.content_json = payload.to_string();
    event.text = payload
        .get("text")
        .and_then(Value::as_str)
        .map(str::to_owned);
    event
}
fn chat_rows(f: &Fixture) -> Value {
    let conversations = f.workbench.conversations("account", 1, 50, "").unwrap();
    let id = conversations["items"][0]["id"].as_str().unwrap();
    f.workbench.messages("account", id).unwrap()
}
fn stored_display(f: &Fixture) -> (String, String, Option<String>, Option<String>) {
    rusqlite::Connection::open(f.dir.path().join("workbench.sqlite3"))
        .unwrap()
        .query_row(
            "SELECT kind,content,media,media_source FROM messages WHERE server_id='101'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .unwrap()
}

#[test]
fn media_fallback_ws_http_reply_without_changing_chat_or_payload() {
    for ws in [true, false] {
        for (name, payload, kind) in samples() {
            let mut f = Fixture::new();
            let e = media_event(&f, "101", &payload);
            f.workbench
                .project("account", "self", &[media_event(&f, "101", &payload)])
                .unwrap();
            let before = chat_rows(&f);
            assert_eq!(before[0]["content_type"], kind, "{name}");
            let stored_before = stored_display(&f);
            assert_eq!(f.receive(&e, ws).candidates, 1, "{name} ws={ws}");
            let pending = f.store.pending_inbound_receipts(&f.lease, 128).unwrap();
            assert_eq!(
                pending[0].payload.as_ref().unwrap(),
                &serde_json::to_vec(&e).unwrap(),
                "wire payload was rewritten: {name}"
            );
            let ids = f.run();
            assert_eq!(ids.len(), 1, "{name} ws={ws}");
            assert_eq!(
                f.store.outbound_batch(&ids[0]).unwrap().segments[0].payload,
                "welcome"
            );
            f.confirm(&ids[0]);
            f.store = CoreStore::open(f.dir.path()).unwrap();
            f.receive(&e, !ws);
            f.run();
            assert_eq!(f.count("outbound_batches"), 1, "duplicate {name}");
            assert_eq!(chat_rows(&f), before, "chat JSON changed: {name}");
            assert_eq!(
                stored_display(&f),
                stored_before,
                "media/crypto/source changed: {name}"
            );
        }
    }
}

#[test]
fn media_fallback_placeholder_matching_keeps_keyword_priority() {
    for mode in ["keyword", "regex", "default"] {
        let mut f = Fixture::new();
        let first = match mode {
            "keyword" => {
                json!({"id":"specific","match_type":"contains","keywords":["[图片]"],"reply_text":"specific reply"})
            }
            "regex" => {
                json!({"id":"specific","match_type":"regex","regex_pattern":"^\\[图片\\]$","reply_text":"specific reply"})
            }
            _ => {
                json!({"id":"specific","match_type":"contains","keywords":["no-match"],"reply_text":"specific reply"})
            }
        };
        f.engine = RuleEngine::from_json(&serde_json::to_vec(&json!({"version":1,"revision":1,"timezone":"Asia/Shanghai","rules":[first,{"id":"fallback","match_type":"default","reply_text":"fallback reply"}]})).unwrap()).unwrap();
        f.receive(&media_event(&f, "101", &samples()[2].1), true);
        let ids = f.run();
        assert_eq!(ids.len(), 1, "{mode}");
        let batch = f.store.outbound_batch(&ids[0]).unwrap();
        assert_eq!(
            batch.segments[0].payload,
            if mode == "default" {
                "fallback reply"
            } else {
                "specific reply"
            }
        );
    }
}

#[test]
fn media_fallback_keeps_blacklist_quota_silence_and_no_rule_checks() {
    for mode in ["peer", "content", "quota", "silent", "no_rule"] {
        let mut f = Fixture::new();
        match mode {
            "peer" => f.policy.blocked_peers.push("peer".into()),
            "content" => f.policy.blocked_content_keywords.push("图片".into()),
            "quota" => f.policy.daily_quota = 0,
            "silent" => {
                f.policy.silent_start = Some("00:00:00".into());
                f.policy.silent_end = Some("23:59:59".into());
            }
            "no_rule" => {
                f.engine = RuleEngine::from_json(
                    br#"{"version":1,"revision":1,"timezone":"Asia/Shanghai","rules":[]}"#,
                )
                .unwrap();
            }
            _ => unreachable!(),
        }
        assert_eq!(
            f.receive(&media_event(&f, "101", &samples()[2].1), true)
                .candidates,
            1
        );
        assert!(f.run().is_empty(), "{mode}");
        assert_eq!(f.count("outbound_batches"), 0);
    }
}

#[test]
fn media_fallback_is_not_a_follow_cooldown_or_daily_peer_exemption() {
    for cooldown in [true, false] {
        let mut f = Fixture::new();
        f.policy.daily_peer_limit = !cooldown;
        if !cooldown {
            f.engine=RuleEngine::from_json(br#"{"version":1,"revision":1,"timezone":"Asia/Shanghai","rules":[{"id":"r","match_type":"default","reply_text":"welcome","cooldown_seconds":0}]}"#).unwrap();
        }
        f.receive(&media_event(&f, "100", &samples()[0].1), true);
        let ids = f.run();
        assert_eq!(ids.len(), 1);
        f.confirm(&ids[0]);
        f.receive(&media_event(&f, "101", &samples()[2].1), true);
        assert!(f.run().is_empty());
        assert_eq!(f.count("outbound_batches"), 1);
        assert_eq!(f.count("mutual_follow_claims"), 0);
    }
}

#[test]
fn media_fallback_control_unknown_malformed_and_text_mismatch_stay_out() {
    for payload in [
        json!({"command_type":1,"url":{}}),
        json!({"read_index":7,"display_name":"比心"}),
        json!({}),
        json!({"aweType":5}),
        json!({"resource_url":["malformed"]}),
    ] {
        let f = Fixture::new();
        assert_eq!(
            f.receive(&media_event(&f, "101", &payload), true)
                .candidates,
            0
        );
        assert!(f.run().is_empty());
    }
    let f = Fixture::new();
    let mut e = media_event(&f, "101", &json!({"text":"original","url":{}}));
    e.text = Some("different".into());
    assert_eq!(f.receive(&e, true).candidates, 0);
    assert!(f.run().is_empty());
}

#[test]
fn media_fallback_self_history_expired_identity_and_missing_time_stay_out() {
    for mode in [
        "self",
        "history",
        "expired",
        "identity",
        "timestamp",
        "group",
    ] {
        let mut f = Fixture::new();
        let mut e = media_event(&f, "101", &samples()[0].1);
        match mode {
            "self" => {
                e.sender_sec_uid = "self".into();
                e.sender_uid = "100".into();
            }
            "history" => e.create_time_us = f.cutoff(),
            "expired" => {
                e.create_time_us -= 301_000_000;
                f.cutoff_us = e.create_time_us - 1;
            }
            "identity" => e.sender_sec_uid.clear(),
            "timestamp" => e.create_time_us = 0,
            "group" => e.message_type = 2,
            _ => unreachable!(),
        }
        assert_eq!(f.receive(&e, true).candidates, 0, "{mode}");
        assert!(f.run().is_empty(), "{mode}");
    }
}
