//! Native `get_by_user` wire encoding/decoding with explicit cursor semantics.
use super::{
    im::{ProtocolError, BUILD_ID, SDK_VERSION},
    wire::{self, WireError, WireField, WireValue},
};

pub struct InboxMessage {
    pub conversation_id: String,
    pub conversation_short_id: u64,
    pub server_message_id: u64,
    pub sender_uid: u64,
    pub sender_sec_uid: String,
    pub create_time_us: u64,
    pub message_type: u64,
    pub client_message_id: String,
    pub content_json: String,
    pub text: Option<String>,
}
pub struct InboxPage {
    pub status_code: i64,
    pub status_message: String,
    pub messages: Vec<InboxMessage>,
    pub next_cursor: u64,
    pub wrapper_present: bool,
}

/// Exact receive envelope used by the current Python adapter.
/// # Errors
/// Rejects limits outside 1..=200 and malformed wire values.
pub fn encode_get_by_user(cursor: u64, limit: u16) -> Result<Vec<u8>, WireError> {
    let mut payload = Vec::new();
    if !(1..=200).contains(&limit) {
        return Err(WireError::InvalidFieldNumber(0));
    }
    wire::append_varint_field(&mut payload, 1, cursor)?;
    wire::append_varint_field(&mut payload, 2, u64::from(limit))?;
    let mut inner = Vec::new();
    wire::append_bytes_field(&mut inner, 200, &payload)?;
    let mut out = Vec::new();
    wire::append_varint_field(&mut out, 1, 200)?;
    wire::append_varint_field(&mut out, 2, 10004)?;
    wire::append_string_field(&mut out, 3, SDK_VERSION)?;
    wire::append_string_field(&mut out, 4, "")?;
    wire::append_varint_field(&mut out, 5, 3)?;
    wire::append_varint_field(&mut out, 6, 1)?;
    wire::append_string_field(&mut out, 7, BUILD_ID)?;
    wire::append_bytes_field(&mut out, 8, &inner)?;
    wire::append_string_field(&mut out, 9, "")?;
    wire::append_string_field(&mut out, 11, "douyin_creator")?;
    for (key, value) in [
        ("aid_new", ""),
        ("app_name", "douyin_creator"),
        ("is-retry", "0"),
    ] {
        let mut entry = Vec::new();
        wire::append_string_field(&mut entry, 1, key)?;
        wire::append_string_field(&mut entry, 2, value)?;
        wire::append_bytes_field(&mut out, 15, &entry)?;
    }
    wire::append_varint_field(&mut out, 18, 1)?;
    wire::append_string_field(&mut out, 21, "douyin_creator")?;
    wire::append_string_field(&mut out, 22, "web_sdk")?;
    Ok(out)
}

fn bytes<'a>(fields: &[WireField<'a>], number: u32) -> Result<Option<&'a [u8]>, WireError> {
    fields
        .iter()
        .find(|f| f.number == number)
        .copied()
        .map(wire::expect_bytes)
        .transpose()
}
fn num(fields: &[WireField<'_>], number: u32) -> Result<u64, WireError> {
    fields
        .iter()
        .find(|f| f.number == number)
        .copied()
        .map(wire::expect_varint)
        .transpose()
        .map(Option::unwrap_or_default)
}
fn text(fields: &[WireField<'_>], number: u32) -> Result<String, ProtocolError> {
    let Some(value) = bytes(fields, number)? else {
        return Ok(String::new());
    };
    std::str::from_utf8(value)
        .map(str::to_owned)
        .map_err(|source| ProtocolError::InvalidUtf8 {
            field: number,
            source,
        })
}

pub(super) fn decode_message(raw: &[u8]) -> Result<Option<InboxMessage>, ProtocolError> {
    let fields = wire::decode_message(raw)?;
    let conversation_id = text(&fields, 1)?;
    let server_message_id = num(&fields, 3)?;
    if conversation_id.is_empty() || server_message_id == 0 {
        return Ok(None);
    }
    let content_json = text(&fields, 8)?;
    let content: Option<serde_json::Value> = serde_json::from_str(&content_json).ok();
    let message_text = content.and_then(|v| {
        v.get("text")
            .and_then(serde_json::Value::as_str)
            .map(str::to_owned)
    });
    let mut create_time_us = num(&fields, 4)?;
    let mut client_message_id = String::new();
    for field in fields.iter().filter(|f| f.number == 9) {
        let ext = wire::decode_message(wire::expect_bytes(*field)?)?;
        match text(&ext, 1)?.as_str() {
            "s:client_message_id" => client_message_id = text(&ext, 2)?,
            "s:server_message_create_time" => {
                if let Some(t) = text(&ext, 2)?
                    .parse::<u64>()
                    .ok()
                    .and_then(|t| t.checked_mul(1000))
                {
                    create_time_us = t;
                }
            }
            _ => {}
        }
    }
    Ok(Some(InboxMessage {
        conversation_id,
        server_message_id,
        conversation_short_id: num(&fields, 5)?,
        message_type: num(&fields, 2)?,
        sender_uid: num(&fields, 7)?,
        sender_sec_uid: text(&fields, 14)?,
        create_time_us,
        client_message_id,
        content_json,
        text: message_text,
    }))
}

/// Decode errors do not advance a cursor. Wrapper cursor wins over message time.
/// # Errors
/// Rejects malformed/oversized envelopes and malformed recognized message fields.
pub fn decode_get_by_user(raw: &[u8]) -> Result<InboxPage, ProtocolError> {
    let fields = wire::decode_message(raw)?;
    let status_code = if fields.iter().any(|f| f.number == 3) {
        i64::from_le_bytes(num(&fields, 3)?.to_le_bytes())
    } else {
        -1
    };
    let mut page = InboxPage {
        status_code,
        status_message: text(&fields, 4)?,
        messages: Vec::new(),
        next_cursor: 0,
        wrapper_present: false,
    };
    if status_code != 0 {
        return Ok(page);
    }
    let Some(inner) = bytes(&fields, 6)? else {
        return Ok(page);
    };
    let inner = wire::decode_message(inner)?;
    let Some(wrapper) = bytes(&inner, 200)? else {
        return Ok(page);
    };
    page.wrapper_present = true;
    let fields = wire::decode_message(wrapper)?;
    for field in fields.iter().filter(|f| f.number == 1) {
        if let Some(message) = decode_message(wire::expect_bytes(*field)?)? {
            page.messages.push(message);
        }
    }
    let cursor = num(&fields, 2)?;
    page.next_cursor = if cursor > 0 { cursor } else { num(&fields, 5)? };
    if page.next_cursor == 0 {
        page.next_cursor = page
            .messages
            .iter()
            .map(|m| m.create_time_us)
            .max()
            .unwrap_or_default();
    }
    Ok(page)
}

pub struct ConversationContext {
    pub status_code: i64,
    pub conversation_id: String,
    pub short_id: u64,
    pub ticket: String,
    /// Members as `(uid, sec_uid)`; for a direct chat, ourselves and the peer.
    pub participants: Vec<(u64, String)>,
}

const MAX_PARTICIPANTS: usize = 64;
/// # Errors
/// Rejects empty/invalid routing identifiers and wire encoding errors.
pub fn encode_conversation_info(
    id: &str,
    short_id: u64,
    sequence: u64,
    ua: &str,
) -> Result<Vec<u8>, super::im::ProtocolError> {
    if id.is_empty() || id.len() > 1024 || short_id == 0 || sequence == 0 {
        return Err(super::im::ProtocolError::InvalidRequest(
            "invalid conversation context",
        ));
    }
    let mut data = Vec::new();
    wire::append_string_field(&mut data, 1, id)?;
    wire::append_varint_field(&mut data, 2, short_id)?;
    wire::append_varint_field(&mut data, 3, 1)?;
    let mut wrapper = Vec::new();
    wire::append_bytes_field(&mut wrapper, 1, &data)?;
    let mut inner = Vec::new();
    wire::append_bytes_field(&mut inner, 610, &wrapper)?;
    super::im::encode_normal_envelope(610, sequence, &inner, super::im::common_headers(ua))
}
/// # Errors
/// Rejects malformed response wrappers. Ticket contents stay out of diagnostics.
pub fn decode_conversation_info(raw: &[u8]) -> Result<ConversationContext, ProtocolError> {
    let envelope = wire::decode_message(raw)?;
    let status_code = if envelope.iter().any(|f| f.number == 3) {
        i64::from_le_bytes(num(&envelope, 3)?.to_le_bytes())
    } else {
        -1
    };
    let mut result = ConversationContext {
        status_code,
        conversation_id: String::new(),
        short_id: 0,
        ticket: String::new(),
        participants: Vec::new(),
    };
    if status_code != 0 {
        return Ok(result);
    }
    let Some(inner) = bytes(&envelope, 6)? else {
        return Ok(result);
    };
    let inner = wire::decode_message(inner)?;
    let Some(wrapper) = bytes(&inner, 610)? else {
        return Ok(result);
    };
    let wrapper = wire::decode_message(wrapper)?;
    let Some(info) = bytes(&wrapper, 1)? else {
        return Ok(result);
    };
    let fields = wire::decode_message(info)?;
    result.conversation_id = text(&fields, 1)?;
    result.short_id = num(&fields, 2)?;
    result.ticket = text(&fields, 4)?;
    // Members are advisory (peer naming only). A shape we do not understand must never
    // fail the context, because sending depends on its ticket.
    result.participants = participants(&fields).unwrap_or_default();
    Ok(result)
}

/// `info.6` holds the member list; each repeated `1` is one member with uid `1`
/// and `sec_uid` `5`.
fn participants(info: &[WireField<'_>]) -> Option<Vec<(u64, String)>> {
    let mut members = Vec::new();
    for list in info.iter().filter(|field| field.number == 6) {
        let WireValue::LengthDelimited(raw) = list.value else {
            return None;
        };
        for entry in wire::decode_message(raw).ok()? {
            let (1, WireValue::LengthDelimited(raw)) = (entry.number, entry.value) else {
                continue;
            };
            let member = wire::decode_message(raw).ok()?;
            let (uid, sec_uid) = (num(&member, 1).ok()?, text(&member, 5).ok()?);
            if uid > 0 && !sec_uid.is_empty() && members.len() < MAX_PARTICIPANTS {
                members.push((uid, sec_uid));
            }
        }
    }
    Some(members)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn conversation_info_exposes_both_direct_chat_members() {
        let member = |uid: u64, sec: &str| {
            let mut m = Vec::new();
            wire::append_varint_field(&mut m, 1, uid).unwrap();
            wire::append_string_field(&mut m, 5, sec).unwrap();
            m
        };
        let mut list = Vec::new();
        wire::append_bytes_field(&mut list, 1, &member(1, "self-sec")).unwrap();
        wire::append_bytes_field(&mut list, 1, &member(2, "peer-sec")).unwrap();
        let mut info = Vec::new();
        wire::append_string_field(&mut info, 1, "0:1:1:2").unwrap();
        wire::append_varint_field(&mut info, 2, 9).unwrap();
        wire::append_string_field(&mut info, 4, "ticket").unwrap();
        wire::append_bytes_field(&mut info, 6, &list).unwrap();
        let mut wrapper = Vec::new();
        wire::append_bytes_field(&mut wrapper, 1, &info).unwrap();
        let mut inner = Vec::new();
        wire::append_bytes_field(&mut inner, 610, &wrapper).unwrap();
        let mut envelope = Vec::new();
        wire::append_varint_field(&mut envelope, 3, 0).unwrap();
        wire::append_bytes_field(&mut envelope, 6, &inner).unwrap();
        let context = decode_conversation_info(&envelope).unwrap();
        assert_eq!(context.ticket, "ticket");
        assert_eq!(
            context.participants,
            vec![(1, "self-sec".to_owned()), (2, "peer-sec".to_owned())]
        );
        // An unexpected member shape never fails the context that sending relies on.
        let mut odd = Vec::new();
        wire::append_string_field(&mut odd, 1, "0:1:1:2").unwrap();
        wire::append_varint_field(&mut odd, 2, 9).unwrap();
        wire::append_string_field(&mut odd, 4, "ticket").unwrap();
        wire::append_bytes_field(&mut odd, 6, &member(1, "flat-member")).unwrap();
        let mut wrapper = Vec::new();
        wire::append_bytes_field(&mut wrapper, 1, &odd).unwrap();
        let mut inner = Vec::new();
        wire::append_bytes_field(&mut inner, 610, &wrapper).unwrap();
        let mut envelope = Vec::new();
        wire::append_varint_field(&mut envelope, 3, 0).unwrap();
        wire::append_bytes_field(&mut envelope, 6, &inner).unwrap();
        let context = decode_conversation_info(&envelope).unwrap();
        assert_eq!(context.ticket, "ticket");
        assert!(context.participants.is_empty());
    }
    #[test]
    fn invalid_utf8_message_is_not_silently_accepted() {
        let mut body = Vec::new();
        wire::append_string_field(&mut body, 1, "conversation").unwrap();
        wire::append_varint_field(&mut body, 3, 1).unwrap();
        wire::append_bytes_field(&mut body, 8, &[255]).unwrap();
        assert!(matches!(
            decode_message(&body),
            Err(ProtocolError::InvalidUtf8 { field: 8, .. })
        ));
    }

    #[test]
    fn conversation_context_request_matches_reference_pb2() {
        use std::fmt::Write;
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/account_session.json"))
                .unwrap();
        let out =
            encode_conversation_info("synthetic-conversation", 123_456, 10001, "Synthetic UA")
                .unwrap();
        let hex = out.iter().fold(String::new(), |mut s, b| {
            write!(s, "{b:02x}").unwrap();
            s
        });
        assert_eq!(hex, fixture["conversation_body_hex"]);
    }

    #[test]
    fn receive_request_matches_python_reference_bytes() {
        use std::fmt::Write;
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/account_session.json"))
                .unwrap();
        let out = encode_get_by_user(123_456_789, 2).unwrap();
        let hex = out.iter().fold(String::new(), |mut s, b| {
            write!(s, "{b:02x}").unwrap();
            s
        });
        assert_eq!(hex, fixture["inbox_body_hex"]);
    }
    #[test]
    fn wrapper_cursor_has_priority_and_message_fields_survive() {
        let mut message = Vec::new();
        wire::append_string_field(&mut message, 1, "conversation").unwrap();
        wire::append_varint_field(&mut message, 3, 99).unwrap();
        wire::append_varint_field(&mut message, 4, 99999).unwrap();
        wire::append_varint_field(&mut message, 5, 12).unwrap();
        wire::append_varint_field(&mut message, 7, 44).unwrap();
        wire::append_string_field(&mut message, 8, r#"{"text":"hello"}"#).unwrap();
        let mut wrapper = Vec::new();
        wire::append_bytes_field(&mut wrapper, 1, &message).unwrap();
        wire::append_varint_field(&mut wrapper, 2, 123).unwrap();
        let mut inner = Vec::new();
        wire::append_bytes_field(&mut inner, 200, &wrapper).unwrap();
        let mut envelope = Vec::new();
        wire::append_varint_field(&mut envelope, 3, 0).unwrap();
        wire::append_bytes_field(&mut envelope, 6, &inner).unwrap();
        let page = decode_get_by_user(&envelope).unwrap();
        assert_eq!(page.next_cursor, 123);
        assert_eq!(page.messages.len(), 1);
        assert_eq!(page.messages[0].text.as_deref(), Some("hello"));
        assert_eq!(page.messages[0].sender_uid, 44);
    }
}

/// Bounded current-conversation read, matching the creator UI's command 301.
/// # Errors
/// Rejects invalid route identifiers before encoding.
pub fn encode_conversation_history(id: &str, short: u64) -> Result<Vec<u8>, ProtocolError> {
    if id.is_empty()
        || id.len() > 256
        || id.chars().any(char::is_control)
        || short == 0
        || short > i64::MAX as u64
    {
        return Err(ProtocolError::InvalidRequest("conversation history scope"));
    }
    let mut payload = Vec::new();
    wire::append_string_field(&mut payload, 1, id)?;
    for (field, value) in [(2, 1), (3, short), (4, 1), (5, 0), (6, 20)] {
        wire::append_varint_field(&mut payload, field, value)?;
    }
    let mut body = Vec::new();
    wire::append_bytes_field(&mut body, 301, &payload)?;
    super::creator::envelope(
        301,
        10004,
        &body,
        &[
            ("aid_new", ""),
            ("app_name", "douyin_creator"),
            ("is-retry", "0"),
        ],
    )
}

/// Decode only the requested direct conversation; no cursor/automation changes.
/// # Errors
/// Rejects malformed envelopes, excess rows and foreign conversation messages.
pub fn decode_conversation_history(
    raw: &[u8],
    id: &str,
    short: u64,
) -> Result<InboxPage, ProtocolError> {
    let outer = wire::decode_message(raw)?;
    let status_code = if outer.iter().any(|f| f.number == 3) {
        i64::from_le_bytes(num(&outer, 3)?.to_le_bytes())
    } else {
        -1
    };
    let mut page = InboxPage {
        status_code,
        status_message: text(&outer, 4)?,
        messages: Vec::new(),
        next_cursor: 0,
        wrapper_present: false,
    };
    let Some(body) = bytes(&outer, 6)? else {
        return Ok(page);
    };
    let body = wire::decode_message(body)?;
    let Some(history) = bytes(&body, 301)? else {
        return Ok(page);
    };
    let history = wire::decode_message(history)?;
    page.wrapper_present = true;
    for row in history.iter().filter(|f| f.number == 1) {
        if page.messages.len() >= 200 {
            return Err(ProtocolError::InvalidRequest("conversation history count"));
        }
        if let Some(message) = decode_message(wire::expect_bytes(*row)?)? {
            if message.conversation_id != id || message.conversation_short_id != short {
                return Err(ProtocolError::InvalidRequest(
                    "conversation history scope mismatch",
                ));
            }
            page.messages.push(message);
        }
    }
    Ok(page)
}

#[cfg(test)]
mod history_tests {
    use super::*;
    fn response(id: &str, short: u64, rows: usize) -> Vec<u8> {
        let mut message = Vec::new();
        wire::append_string_field(&mut message, 1, id).unwrap();
        wire::append_varint_field(&mut message, 3, 100).unwrap();
        wire::append_varint_field(&mut message, 5, short).unwrap();
        wire::append_string_field(&mut message, 8, r#"{"text":"synthetic"}"#).unwrap();
        let mut history = Vec::new();
        for _ in 0..rows {
            wire::append_bytes_field(&mut history, 1, &message).unwrap();
        }
        let mut body = Vec::new();
        wire::append_bytes_field(&mut body, 301, &history).unwrap();
        let mut outer = Vec::new();
        wire::append_varint_field(&mut outer, 3, 0).unwrap();
        wire::append_bytes_field(&mut outer, 6, &body).unwrap();
        outer
    }
    #[test]
    fn history_is_scoped_bounded_and_never_treated_as_an_inbox_cursor() {
        let page =
            decode_conversation_history(&response("0:1:10:20", 30, 1), "0:1:10:20", 30).unwrap();
        assert!(page.wrapper_present);
        assert_eq!(page.messages.len(), 1);
        assert_eq!(page.next_cursor, 0);
        assert_eq!(page.messages[0].text.as_deref(), Some("synthetic"));
        assert!(decode_conversation_history(&response("foreign", 30, 1), "0:1:10:20", 30).is_err());
        assert!(
            decode_conversation_history(&response("0:1:10:20", 31, 1), "0:1:10:20", 30).is_err()
        );
        assert!(
            decode_conversation_history(&response("0:1:10:20", 30, 201), "0:1:10:20", 30).is_err()
        );
        assert!(
            !decode_conversation_history(&[24, 0], "0:1:10:20", 30)
                .unwrap()
                .wrapper_present
        );
        assert!(encode_conversation_history("", 30).is_err());
        let encoded = encode_conversation_history("0:1:10:20", 30).unwrap();
        let fields = wire::decode_message(&encoded).unwrap();
        assert_eq!(num(&fields, 1).unwrap(), 301);
    }
}
