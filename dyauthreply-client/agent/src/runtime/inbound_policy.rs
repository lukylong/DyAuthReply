//! Eligibility is separate from receipt persistence: pagination cursors are NOT
//! message ages. Unknown identities/timestamps/content remain deferred, not replied to.
use super::inbound::InboundEvent;
use serde::Serialize;

const MAX_REPLY_AGE_US: u64 = 5 * 60 * 1_000_000;
const MAX_FUTURE_SKEW_US: u64 = 30 * 1_000_000;

/// Platform-generated mutual-follow notices are ordinary direct text packets,
/// sometimes attributed to ourselves. Do not exempt arbitrary text mentioning
/// following or control packets. Notices also have client IDs; the caller checks
/// the durable outbox to distinguish our actual sends from platform notices.
pub(crate) fn is_mutual_follow_notice(event: &InboundEvent) -> bool {
    if event.message_type != 1 {
        return false;
    }
    let Ok(serde_json::Value::Object(content)) = serde_json::from_str(&event.content_json) else {
        return false;
    };
    if content.contains_key("read_index") || content.contains_key("command_type") {
        return false;
    }
    let Some(text) = content.get("text").and_then(serde_json::Value::as_str) else {
        return false;
    };
    if event.text.as_deref() != Some(text) {
        return false;
    }
    // Legacy Python accepted wording/punctuation variants. Keep a bounded notice
    // grammar rather than turning every self-message containing “互相关注” into a trigger.
    if text.len() > 256 {
        return false;
    }
    let normalized: String = text
        .chars()
        .filter(|c| {
            !c.is_whitespace() && !matches!(c, '，' | ',' | '。' | '.' | '！' | '!' | '～' | '~')
        })
        .collect();
    [
        "我们已互相关注",
        "我们已经互相关注",
        "你们已互相关注",
        "你们已经互相关注",
    ]
    .iter()
    .filter_map(|prefix| normalized.strip_prefix(prefix))
    .any(|tail| {
        matches!(
            tail.strip_prefix('了').unwrap_or(tail),
            "可以开始聊天了" | "可以开始聊天啦"
        )
    })
}

/// Only live WS notices may borrow their durable arrival time. Never rewrite the
/// stored payload/hash, manufacture HTTP-history freshness, or extend the window
/// on restart/replay. This derived timestamp is not platform delivery evidence.
pub(crate) fn with_live_notice_time(
    mut event: InboundEvent,
    stream: &str,
    received_at_ms: i64,
) -> InboundEvent {
    if event.create_time_us == 0 && stream == "douyin-im-ws" && is_mutual_follow_notice(&event) {
        event.create_time_us = u64::try_from(received_at_ms)
            .ok()
            .and_then(|ms| ms.checked_mul(1000))
            .unwrap_or(0);
    }
    event
}
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Eligibility {
    Candidate,
    IgnoreSelf,
    IgnoreHistory,
    IgnoreExpired,
    IgnoreSystem,
    IgnoreNonDirect,
    IgnoreEmpty,
    DeferIdentity,
    DeferTimestamp,
    DeferContent,
}
impl Eligibility {
    #[must_use]
    pub const fn is_terminal_ignore(self) -> bool {
        matches!(
            self,
            Self::IgnoreSelf
                | Self::IgnoreHistory
                | Self::IgnoreExpired
                | Self::IgnoreSystem
                | Self::IgnoreNonDirect
                | Self::IgnoreEmpty
        )
    }
    /// Packets that are never conversation messages: consumed without a reply-log audit.
    #[must_use]
    pub const fn is_silent(self) -> bool {
        matches!(
            self,
            Self::IgnoreSystem | Self::IgnoreNonDirect | Self::IgnoreEmpty
        )
    }
}
/// Evaluates authenticated account-bound received content. Inbound f2 subtype1
/// is NOT outgoing send type7. Reference system keys take precedence over text.
#[must_use]
pub fn eligibility(
    event: &InboundEvent,
    own_sec_uid: &str,
    live_start_us: u64,
    now_us: u64,
) -> Eligibility {
    let content = serde_json::from_str::<serde_json::Value>(&event.content_json).ok();
    let content = content.as_ref().and_then(serde_json::Value::as_object);
    // Control packets (conversation updates, recalls, read receipts) carry no
    // sender; judging identity first left them deferred forever in the spool.
    if content.is_some_and(|c| c.contains_key("read_index") || c.contains_key("command_type")) {
        return Eligibility::IgnoreSystem;
    }
    // Only single chats (type 1, `0:1:uid:uid`) are auto-reply targets; groups arrive as type 2.
    if event.message_type != 1 {
        return Eligibility::IgnoreNonDirect;
    }
    if event.content_json.trim().is_empty() && event.text.is_none() {
        return Eligibility::IgnoreEmpty;
    }
    let verified_self_notice =
        event.sender_sec_uid == own_sec_uid && is_mutual_follow_notice(event);
    if event.version != 1
        || own_sec_uid.is_empty()
        || event.sender_sec_uid.is_empty()
        || event
            .sender_uid
            .parse::<u64>()
            .ok()
            .is_none_or(|id| id == 0 && !verified_self_notice)
    {
        return Eligibility::DeferIdentity;
    }
    if event.sender_sec_uid == own_sec_uid && !is_mutual_follow_notice(event) {
        return Eligibility::IgnoreSelf;
    }
    if live_start_us == 0
        || now_us < live_start_us
        || event.create_time_us == 0
        || event.create_time_us > now_us.saturating_add(MAX_FUTURE_SKEW_US)
    {
        return Eligibility::DeferTimestamp;
    }
    if event.create_time_us <= live_start_us {
        return Eligibility::IgnoreHistory;
    }
    if now_us.saturating_sub(event.create_time_us) > MAX_REPLY_AGE_US {
        return Eligibility::IgnoreExpired;
    }
    // Recognized media has a bounded matching label independent of its chat
    // rendering. Use the same view here and in planning; do not rewrite events.
    if crate::workbench::reply_match_text(&event.content_json, event.text.as_deref()).is_none() {
        return Eligibility::DeferContent;
    }
    Eligibility::Candidate
}

/// Receipt-level decision: a deferral can never outlive the reply window measured
/// from durable arrival, so unresolvable rows cannot fill the bounded pending spool.
#[must_use]
pub fn receipt_eligibility(
    event: &InboundEvent,
    received_at_ms: i64,
    own_sec_uid: &str,
    live_start_us: u64,
    now_us: u64,
) -> Eligibility {
    let decision = eligibility(event, own_sec_uid, live_start_us, now_us);
    let received_us = u64::try_from(received_at_ms)
        .unwrap_or(0)
        .saturating_mul(1000);
    let deferred = !decision.is_terminal_ignore() && decision != Eligibility::Candidate;
    if deferred && now_us.saturating_sub(received_us) > MAX_REPLY_AGE_US {
        return Eligibility::IgnoreExpired;
    }
    decision
}

#[cfg(test)]
mod tests {
    use super::*;
    fn event() -> InboundEvent {
        InboundEvent {
            version: 1,
            server_message_id: "123".into(),
            conversation_id: "conversation".into(),
            conversation_short_id: "456".into(),
            sender_uid: "42".into(),
            sender_sec_uid: "peer".into(),
            client_message_id: String::new(),
            message_type: 1,
            create_time_us: 1_700_000_000_000_001,
            content_json: r#"{"text":"hello"}"#.into(),
            text: Some("hello".into()),
        }
    }
    const START: u64 = 1_700_000_000_000_000;
    #[test]
    fn inbound_subtype_is_not_outbound_type_and_system_keys_win_over_text() {
        let mut e = event();
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::Candidate
        );
        e.message_type = 7;
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::IgnoreNonDirect
        );
        e.message_type = 1;
        e.content_json = r#"{"text":"hello","command_type":1}"#.into();
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::IgnoreSystem
        );
    }
    #[test]
    fn later_pages_cannot_reanimate_history_and_boundary_is_exclusive() {
        let mut e = event();
        e.create_time_us = START;
        assert_eq!(
            eligibility(&e, "self", START, START + 10),
            Eligibility::IgnoreHistory
        );
        e.create_time_us = START - 3_600_000_000;
        assert_eq!(
            eligibility(&e, "self", START, START + 10),
            Eligibility::IgnoreHistory
        );
        e.create_time_us = START + 1;
        assert_eq!(
            eligibility(&e, "self", START, START + MAX_REPLY_AGE_US + 2),
            Eligibility::IgnoreExpired
        );
    }
    #[test]
    fn self_echo_unknown_identity_clock_jump_and_missing_time_never_reply() {
        let mut e = event();
        assert_eq!(
            eligibility(&e, "peer", START, START + 1),
            Eligibility::IgnoreSelf
        );
        assert_eq!(
            eligibility(&e, "", START, START + 1),
            Eligibility::DeferIdentity
        );
        e.sender_sec_uid.clear();
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::DeferIdentity
        );
        e = event();
        e.create_time_us = 0;
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::DeferTimestamp
        );
        e = event();
        assert_eq!(
            eligibility(&e, "self", START, START - 1),
            Eligibility::DeferTimestamp
        );
        e.create_time_us = START + MAX_FUTURE_SKEW_US + 2;
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::DeferTimestamp
        );
    }
    #[test]
    fn senderless_system_packets_are_terminal_not_identity_deferred() {
        let mut e = event();
        e.sender_uid.clear();
        e.sender_sec_uid.clear();
        e.text = None;
        e.content_json =
            r#"{"command_type":1,"conversation_id":"c","need_pull_conv_info":true}"#.into();
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::IgnoreSystem
        );
        e.content_json = r#"{"read_index":9}"#.into();
        assert_eq!(
            eligibility(&e, "", START, START + 1),
            Eligibility::IgnoreSystem
        );
    }
    #[test]
    fn group_and_empty_packets_are_silent_terminal_ignores() {
        let mut e = event();
        e.message_type = 2;
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::IgnoreNonDirect
        );
        e = event();
        e.text = None;
        e.content_json = String::new();
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::IgnoreEmpty
        );
        e.content_json = "  ".into();
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::IgnoreEmpty
        );
        for silent in [
            Eligibility::IgnoreSystem,
            Eligibility::IgnoreNonDirect,
            Eligibility::IgnoreEmpty,
        ] {
            assert!(silent.is_terminal_ignore() && silent.is_silent());
        }
        assert!(!Eligibility::IgnoreExpired.is_silent());
        assert!(!Eligibility::DeferContent.is_silent());
    }
    #[test]
    fn deferred_receipts_expire_once_older_than_the_reply_window() {
        let mut e = event();
        e.sender_uid.clear();
        let arrived_ms = i64::try_from(START / 1000).unwrap();
        let fresh = START + 1;
        let stale = START + MAX_REPLY_AGE_US + 1_000;
        assert_eq!(
            receipt_eligibility(&e, arrived_ms, "self", START, fresh),
            Eligibility::DeferIdentity
        );
        assert_eq!(
            receipt_eligibility(&e, arrived_ms, "self", START, stale),
            Eligibility::IgnoreExpired
        );
        e = event();
        e.create_time_us = 0;
        assert_eq!(
            receipt_eligibility(&e, arrived_ms, "self", START, stale),
            Eligibility::IgnoreExpired
        );
        // A deliverable candidate is never rewritten by receipt age alone.
        e = event();
        assert_eq!(
            receipt_eligibility(&e, arrived_ms, "self", START, fresh),
            Eligibility::Candidate
        );
    }
    #[test]
    fn ambiguous_media_and_content_mismatch_are_deferred_not_discarded() {
        let mut e = event();
        e.content_json = r#"{"resource_url":["fixture"]}"#.into();
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::DeferContent
        );
        e = event();
        e.text = Some("different".into());
        assert_eq!(
            eligibility(&e, "self", START, START + 1),
            Eligibility::DeferContent
        );
    }
}

#[cfg(test)]
mod mutual_follow_regression {
    use super::*;
    const START: u64 = 1_700_000_000_000_000;
    fn notice(sender: &str) -> InboundEvent {
        let text = "我们已互相关注，可以开始聊天了";
        InboundEvent {
            version: 1,
            server_message_id: "9001".into(),
            conversation_id: "0:1:100:200".into(),
            conversation_short_id: "9002".into(),
            sender_uid: if sender == "self" { "100" } else { "200" }.into(),
            sender_sec_uid: sender.into(),
            client_message_id: String::new(),
            message_type: 1,
            create_time_us: START + 1,
            content_json: serde_json::json!({"text": text}).to_string(),
            text: Some(text.into()),
        }
    }
    #[test]
    fn fresh_peer_notice_is_candidate_but_history_and_expired_are_not() {
        let mut e = notice("peer");
        assert_eq!(
            eligibility(&e, "self", START, START + 2),
            Eligibility::Candidate
        );
        e.create_time_us = START;
        assert_eq!(
            eligibility(&e, "self", START, START + 2),
            Eligibility::IgnoreHistory
        );
        e.create_time_us = START + 1;
        assert_eq!(
            eligibility(&e, "self", START, START + 300_000_002),
            Eligibility::IgnoreExpired
        );
    }
    #[test]
    fn legacy_outbound_follow_notice_should_reach_rule_matching() {
        let e = notice("self");
        assert_eq!(eligibility(&e, "self", START, START + 2), Eligibility::Candidate,
            "legacy mutual-follow fallback missing: own-side follow notice is discarded before rule matching");
    }
}

#[cfg(test)]
mod mutual_follow_boundaries {
    use super::*;
    #[test]
    fn mutual_follow_exception_is_not_a_generic_self_echo_bypass() {
        let start = 1_700_000_000_000_000;
        let text = "我们已互相关注，可以开始聊天了";
        let mut e = InboundEvent {
            version: 1,
            server_message_id: "1".into(),
            conversation_id: "0:1:100:200".into(),
            conversation_short_id: "123".into(),
            sender_uid: "100".into(),
            sender_sec_uid: "self".into(),
            client_message_id: String::new(),
            message_type: 1,
            create_time_us: start + 1,
            content_json: serde_json::json!({"text":text}).to_string(),
            text: Some(text.into()),
        };
        assert_eq!(
            eligibility(&e, "self", start, start + 2),
            Eligibility::Candidate
        );
        e.client_message_id = "our-send-id".into();
        assert_eq!(
            eligibility(&e, "self", start, start + 2),
            Eligibility::Candidate
        );
        e.client_message_id.clear();
        e.content_json = serde_json::json!({"text":text,"command_type":1}).to_string();
        assert_eq!(
            eligibility(&e, "self", start, start + 2),
            Eligibility::IgnoreSystem
        );
        for body in [
            "请互相关注",
            "hello",
            "我们已互相关注，可以开始聊天了 http://example.test",
        ] {
            e.text = Some(body.into());
            e.content_json = serde_json::json!({"text":body}).to_string();
            assert_eq!(
                eligibility(&e, "self", start, start + 2),
                Eligibility::IgnoreSelf
            );
        }
        e.text = Some(text.into());
        e.content_json = serde_json::json!({"text":text}).to_string();
        e.create_time_us = start;
        assert_eq!(
            eligibility(&e, "self", start, start + 2),
            Eligibility::IgnoreHistory
        );
        e.create_time_us = start + 1;
        assert_eq!(
            eligibility(&e, "self", start, start + MAX_REPLY_AGE_US + 2),
            Eligibility::IgnoreExpired
        );
        e.sender_sec_uid.clear();
        assert_eq!(
            eligibility(&e, "self", start, start + 2),
            Eligibility::DeferIdentity
        );
    }
}
