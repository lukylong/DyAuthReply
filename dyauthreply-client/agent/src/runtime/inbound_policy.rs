//! Eligibility is separate from receipt persistence: pagination cursors are NOT
//! message ages. Unknown identities/timestamps/content remain deferred, not replied to.
use super::inbound::InboundEvent;
use serde::Serialize;

const MAX_REPLY_AGE_US: u64 = 5 * 60 * 1_000_000;
const MAX_FUTURE_SKEW_US: u64 = 30 * 1_000_000;
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Eligibility {
    Candidate,
    IgnoreSelf,
    IgnoreHistory,
    IgnoreExpired,
    IgnoreSystem,
    DeferIdentity,
    DeferTimestamp,
    DeferContent,
}
impl Eligibility {
    #[must_use]
    pub const fn is_terminal_ignore(self) -> bool {
        matches!(
            self,
            Self::IgnoreSelf | Self::IgnoreHistory | Self::IgnoreExpired | Self::IgnoreSystem
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
    if event.version != 1
        || own_sec_uid.is_empty()
        || event.sender_sec_uid.is_empty()
        || event
            .sender_uid
            .parse::<u64>()
            .ok()
            .is_none_or(|id| id == 0)
    {
        return Eligibility::DeferIdentity;
    }
    if event.sender_sec_uid == own_sec_uid {
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
    let Some(content) = content else {
        return Eligibility::DeferContent;
    };
    let text = content.get("text").and_then(serde_json::Value::as_str);
    // Rich-media/default trigger parity is a separate rule-engine gate. Preserve
    // those receipts pending rather than silently treating media as no-match.
    if event.message_type != 1
        || text.is_none_or(|s| s.trim().is_empty() || s.len() > 4096)
        || text != event.text.as_deref()
    {
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
            Eligibility::DeferContent
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
