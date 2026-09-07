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
    let Ok(serde_json::Value::Object(content)) = serde_json::from_str(&event.content_json) else {
        return Eligibility::DeferContent;
    };
    if content.contains_key("read_index") || content.contains_key("command_type") {
        return Eligibility::IgnoreSystem;
    }
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
