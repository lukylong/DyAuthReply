//! Native `get_by_user` wire encoding/decoding with explicit cursor semantics.
use super::{
    im::{ProtocolError, BUILD_ID, SDK_VERSION},
    wire::{self, WireError, WireField},
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
}
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
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
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
