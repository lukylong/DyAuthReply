//! Deterministic PC IM send request encoding and response semantics.

use std::collections::BTreeMap;
use std::str;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;

use super::wire::{
    append_bytes_field, append_string_field, append_varint_field, decode_message, expect_bytes,
    expect_varint, WireError, WireField,
};

pub const SEND_MESSAGE_COMMAND: u64 = 100;
pub const SDK_VERSION: &str = "0.1.8";
pub const BUILD_ID: &str = "0d50935:feat/pc-im-groupB";

const DEFAULT_SESSION_AID: &str = "6383";
const DEVICE_PLATFORM: &str = "douyin_pc";
const VERSION_CODE: &str = "360000";
const BIZ: &str = "douyin_web";
const ACCESS: &str = "web_sdk";
const MAX_BUSINESS_JSON_DEPTH: usize = 64;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct ExtensionInput {
    pub key: String,
    pub value: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct SendRequestInput {
    pub conversation_id: String,
    pub conversation_short_id: u64,
    pub ticket: String,
    pub text: String,
    pub user_agent: String,
    pub client_msg_id: String,
    pub sequence_id: u64,
    pub stime: String,
    pub message_type: u64,
    pub identity_security_token: String,
    pub identity_security_device_id: String,
    #[serde(default)]
    pub mentioned_users: Vec<u64>,
    #[serde(default)]
    pub ext: Vec<ExtensionInput>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
#[allow(clippy::struct_excessive_bools)]
pub struct SendMessageResponse {
    pub status_code: i64,
    pub status_msg: String,
    pub server_msg_id: u64,
    pub client_msg_id: String,
    pub biz_status_code: i64,
    pub biz_status_text: String,
    pub biz_raw_check_code: i64,
    pub outer_status_present: bool,
    pub has_response_body: bool,
    pub has_inner_response: bool,
    pub business_payload_present: bool,
    pub business_payload_valid: bool,
}

#[derive(Debug, Error)]
pub enum ProtocolError {
    #[error(transparent)]
    Wire(#[from] WireError),
    #[error("invalid send request: {0}")]
    InvalidRequest(&'static str),
    #[error("protobuf integer in field {field} is outside signed 64-bit range: {value}")]
    IntegerOutOfRange { field: u32, value: u64 },
    #[error("protobuf field {field} is not valid UTF-8")]
    InvalidUtf8 {
        field: u32,
        #[source]
        source: str::Utf8Error,
    },
    #[error("cannot serialize message content JSON: {0}")]
    ContentJson(#[from] serde_json::Error),
}

#[derive(Serialize)]
struct TextContent<'a> {
    #[serde(rename = "aweType")]
    awe_type: u16,
    #[serde(rename = "type")]
    kind: u8,
    #[serde(rename = "richTextInfos")]
    rich_text_infos: [Value; 0],
    text: &'a str,
}

/// Encodes the deterministic subset frozen by the shared PC IM send corpus.
///
/// # Errors
///
/// Returns [`ProtocolError`] when a required deterministic input is absent,
/// outside its protobuf integer range, or cannot be encoded.
pub fn encode_send_message_request(input: &SendRequestInput) -> Result<Vec<u8>, ProtocolError> {
    validate_send_input(input)?;

    let content = serde_json::to_string(&TextContent {
        awe_type: 700,
        kind: 0,
        rich_text_infos: [],
        text: &input.text,
    })?;

    let mut send_body = Vec::new();
    append_string_field(&mut send_body, 1, &input.conversation_id)?;
    append_varint_field(&mut send_body, 2, 1)?;
    if input.conversation_short_id != 0 {
        append_varint_field(&mut send_body, 3, input.conversation_short_id)?;
    }
    append_string_field(&mut send_body, 4, &content)?;

    append_extension(&mut send_body, "s:mentioned_users", "")?;
    append_extension(&mut send_body, "s:client_message_id", &input.client_msg_id)?;
    for extension in &input.ext {
        if !matches!(
            extension.key.as_str(),
            "s:mentioned_users" | "s:client_message_id" | "s:stime"
        ) {
            append_extension(&mut send_body, &extension.key, &extension.value)?;
        }
    }
    append_extension(&mut send_body, "s:stime", &input.stime)?;

    if input.message_type != 0 {
        append_varint_field(&mut send_body, 6, input.message_type)?;
    }
    if !input.ticket.is_empty() {
        append_string_field(&mut send_body, 7, &input.ticket)?;
    }
    append_string_field(&mut send_body, 8, &input.client_msg_id)?;
    if !input.mentioned_users.is_empty() {
        let mut packed_mentions = Vec::new();
        for &user_id in &input.mentioned_users {
            packed_mentions.extend_from_slice(&super::wire::encode_varint(user_id));
        }
        append_bytes_field(&mut send_body, 9, &packed_mentions)?;
    }

    let mut request_body = Vec::new();
    append_bytes_field(&mut request_body, 100, &send_body)?;

    let mut request = Vec::new();
    append_varint_field(&mut request, 1, SEND_MESSAGE_COMMAND)?;
    append_varint_field(&mut request, 2, input.sequence_id)?;
    append_string_field(&mut request, 3, SDK_VERSION)?;
    append_varint_field(&mut request, 5, 3)?;
    append_string_field(&mut request, 7, BUILD_ID)?;
    append_bytes_field(&mut request, 8, &request_body)?;
    append_string_field(&mut request, 9, "0")?;
    append_string_field(&mut request, 11, DEVICE_PLATFORM)?;
    append_string_field(&mut request, 14, VERSION_CODE)?;

    for (key, value) in request_headers(input) {
        append_map_entry(&mut request, 15, &key, &value)?;
    }

    append_varint_field(&mut request, 18, 4)?;
    append_string_field(&mut request, 21, BIZ)?;
    append_string_field(&mut request, 22, ACCESS)?;
    Ok(request)
}

fn validate_send_input(input: &SendRequestInput) -> Result<(), ProtocolError> {
    if input.conversation_id.is_empty() {
        return Err(ProtocolError::InvalidRequest("conversation_id is empty"));
    }
    if input.text.is_empty() {
        return Err(ProtocolError::InvalidRequest("text is empty"));
    }
    if input.client_msg_id.is_empty() {
        return Err(ProtocolError::InvalidRequest("client_msg_id is empty"));
    }
    if input.sequence_id == 0 || input.sequence_id > i64::MAX as u64 {
        return Err(ProtocolError::InvalidRequest(
            "sequence_id must be within positive int64 range",
        ));
    }
    if input.conversation_short_id > i64::MAX as u64 {
        return Err(ProtocolError::InvalidRequest(
            "conversation_short_id exceeds int64 range",
        ));
    }
    if input.message_type > i32::MAX as u64 {
        return Err(ProtocolError::InvalidRequest(
            "message_type exceeds int32 range",
        ));
    }
    if input.stime.is_empty() {
        return Err(ProtocolError::InvalidRequest("stime is empty"));
    }
    if input.mentioned_users.iter().any(|&id| id > i64::MAX as u64) {
        return Err(ProtocolError::InvalidRequest(
            "mentioned user exceeds int64 range",
        ));
    }
    Ok(())
}

fn append_extension(output: &mut Vec<u8>, key: &str, value: &str) -> Result<(), WireError> {
    let mut extension = Vec::new();
    append_string_field(&mut extension, 1, key)?;
    // ExtValue is a proto3 message, so an empty value is omitted by protobuf.
    if !value.is_empty() {
        append_string_field(&mut extension, 2, value)?;
    }
    append_bytes_field(output, 5, &extension)
}

fn append_map_entry(
    output: &mut Vec<u8>,
    field_number: u32,
    key: &str,
    value: &str,
) -> Result<(), WireError> {
    let mut entry = Vec::new();
    append_string_field(&mut entry, 1, key)?;
    // Protobuf map entries serialize their value field even when it is empty.
    append_string_field(&mut entry, 2, value)?;
    append_bytes_field(output, field_number, &entry)
}

fn request_headers(input: &SendRequestInput) -> BTreeMap<String, String> {
    let browser_version = input.user_agent.replacen("Mozilla/", "", 1);
    let mut headers = BTreeMap::from([
        ("app_name".to_owned(), DEVICE_PLATFORM.to_owned()),
        ("browser_language".to_owned(), "zh-CN".to_owned()),
        ("browser_name".to_owned(), "Mozilla".to_owned()),
        ("browser_online".to_owned(), "true".to_owned()),
        ("browser_platform".to_owned(), "Win32".to_owned()),
        ("browser_version".to_owned(), browser_version),
        ("cookie_enabled".to_owned(), "true".to_owned()),
        ("deviceId".to_owned(), "0".to_owned()),
        ("is-retry".to_owned(), "0".to_owned()),
        ("priority_region".to_owned(), "cn".to_owned()),
        (
            "referer".to_owned(),
            "https://www.douyin.com/jingxuan".to_owned(),
        ),
        ("screen_height".to_owned(), "1440".to_owned()),
        ("screen_width".to_owned(), "2560".to_owned()),
        ("session_aid".to_owned(), DEFAULT_SESSION_AID.to_owned()),
        ("session_did".to_owned(), "0".to_owned()),
        ("timezone_name".to_owned(), "Asia/Shanghai".to_owned()),
        ("user_agent".to_owned(), input.user_agent.clone()),
    ]);
    headers.insert("identity_security_aid".to_owned(), String::new());
    if !input.identity_security_device_id.is_empty() {
        headers.insert(
            "identity_security_device_id".to_owned(),
            input.identity_security_device_id.clone(),
        );
    }
    if !input.identity_security_token.is_empty() {
        // The source value is a scalar, so serde_json cannot reorder anything.
        let token = serde_json::json!({ "token": input.identity_security_token }).to_string();
        headers.insert("identity_security_token".to_owned(), token);
    }
    headers
}

/// Decodes the outer and inner semantics of a PC IM send response.
///
/// # Errors
///
/// Returns [`ProtocolError`] when protobuf input is empty, malformed,
/// oversized, uses the wrong wire type, or contains invalid UTF-8.
pub fn decode_send_message_response(input: &[u8]) -> Result<SendMessageResponse, ProtocolError> {
    let envelope = decode_message(input)?;
    let status_field = first_field(&envelope, 3);
    let status_code = match status_field {
        Some(field) => to_i64(3, expect_varint(field)?)?,
        None => -1,
    };
    let status_msg = optional_string(&envelope, 4)?.unwrap_or_default();

    let response_body = optional_bytes(&envelope, 6)?;
    let has_response_body = response_body.is_some();
    let mut response = SendMessageResponse {
        status_code,
        status_msg,
        server_msg_id: 0,
        client_msg_id: String::new(),
        biz_status_code: 0,
        biz_status_text: String::new(),
        biz_raw_check_code: 0,
        outer_status_present: status_field.is_some(),
        has_response_body,
        has_inner_response: false,
        business_payload_present: false,
        business_payload_valid: true,
    };

    let Some(response_body) = response_body.filter(|body| !body.is_empty()) else {
        return Ok(response);
    };
    let body_fields = decode_message(response_body)?;
    let Some(inner_field) = first_field(&body_fields, 100) else {
        return Ok(response);
    };
    let inner = expect_bytes(inner_field)?;
    response.has_inner_response = true;
    let inner_fields = if inner.is_empty() {
        Vec::new()
    } else {
        decode_message(inner)?
    };

    if let Some(field) = first_field(&inner_fields, 1) {
        response.server_msg_id = expect_varint(field)?;
    }
    response.client_msg_id = optional_string(&inner_fields, 4)?.unwrap_or_default();
    if let Some(business_field) = first_field(&inner_fields, 6) {
        response.business_payload_present = true;
        let raw = expect_bytes(business_field)?;
        match serde_json::from_slice::<Value>(raw) {
            Ok(Value::Object(object)) if json_object_within_depth(&object) => {
                let (status_code, status_code_valid) = json_i64(&object, "status_code");
                let (raw_check_code, raw_check_valid) = json_i64(&object, "raw_check_code");
                let (status_text, status_text_valid) = business_status_text(&object);
                response.biz_status_code = status_code;
                response.biz_raw_check_code = raw_check_code;
                response.biz_status_text = status_text;
                response.business_payload_valid =
                    status_code_valid && raw_check_valid && status_text_valid;
            }
            Ok(_) | Err(_) => response.business_payload_valid = false,
        }
    }
    Ok(response)
}

fn first_field<'a>(fields: &'a [WireField<'a>], number: u32) -> Option<WireField<'a>> {
    fields.iter().copied().find(|field| field.number == number)
}

fn optional_bytes<'a>(
    fields: &'a [WireField<'a>],
    number: u32,
) -> Result<Option<&'a [u8]>, WireError> {
    first_field(fields, number).map(expect_bytes).transpose()
}

fn optional_string(fields: &[WireField<'_>], number: u32) -> Result<Option<String>, ProtocolError> {
    let Some(raw) = optional_bytes(fields, number)? else {
        return Ok(None);
    };
    let value = str::from_utf8(raw).map_err(|source| ProtocolError::InvalidUtf8 {
        field: number,
        source,
    })?;
    Ok(Some(value.to_owned()))
}

fn to_i64(field: u32, value: u64) -> Result<i64, ProtocolError> {
    i64::try_from(value).map_err(|_| ProtocolError::IntegerOutOfRange { field, value })
}

fn json_i64(object: &serde_json::Map<String, Value>, key: &str) -> (i64, bool) {
    match object.get(key) {
        None | Some(Value::Null) => (0, true),
        Some(Value::Number(value)) => value.as_i64().map_or((0, false), |value| (value, true)),
        Some(_) => (0, false),
    }
}

fn business_status_text(object: &serde_json::Map<String, Value>) -> (String, bool) {
    match object.get("status_msg") {
        None | Some(Value::Null) => (String::new(), true),
        Some(Value::String(message)) => (message.clone(), true),
        Some(Value::Object(status)) => match status.get("msg_content") {
            None | Some(Value::Null) => (String::new(), true),
            Some(Value::Object(content)) => match content.get("tips") {
                None | Some(Value::Null) => (String::new(), true),
                Some(Value::String(tips)) => (tips.clone(), true),
                Some(_) => (String::new(), false),
            },
            Some(_) => (String::new(), false),
        },
        Some(_) => (String::new(), false),
    }
}

fn json_object_within_depth(object: &serde_json::Map<String, Value>) -> bool {
    let mut stack: Vec<_> = object.values().map(|value| (value, 1_usize)).collect();
    while let Some((item, depth)) = stack.pop() {
        if depth > MAX_BUSINESS_JSON_DEPTH {
            return false;
        }
        match item {
            Value::Array(items) => {
                stack.extend(items.iter().map(|child| (child, depth + 1)));
            }
            Value::Object(items) => {
                stack.extend(items.values().map(|child| (child, depth + 1)));
            }
            Value::Number(number) if !json_number_is_supported(number) => return false,
            Value::Null | Value::Bool(_) | Value::Number(_) | Value::String(_) => {}
        }
    }
    true
}

fn json_number_is_supported(number: &serde_json::Number) -> bool {
    let encoded = number.to_string();
    if encoded
        .bytes()
        .any(|byte| matches!(byte, b'.' | b'e' | b'E'))
    {
        return number.as_f64().is_some_and(f64::is_finite);
    }
    if encoded.starts_with('-') {
        encoded.parse::<i64>().is_ok()
    } else {
        encoded.parse::<u64>().is_ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::wire::WireError;

    fn minimal_input() -> SendRequestInput {
        SendRequestInput {
            conversation_id: "0:1:1:2".to_owned(),
            conversation_short_id: 9,
            ticket: String::new(),
            text: "hello".to_owned(),
            user_agent: "Mozilla/test".to_owned(),
            client_msg_id: "client".to_owned(),
            sequence_id: 10_001,
            stime: "1700000000000.00001".to_owned(),
            message_type: 7,
            identity_security_token: String::new(),
            identity_security_device_id: String::new(),
            mentioned_users: Vec::new(),
            ext: Vec::new(),
        }
    }

    #[test]
    fn deterministic_encoder_rejects_dynamic_or_invalid_identifiers() {
        let mut input = minimal_input();
        input.sequence_id = 0;
        assert!(matches!(
            encode_send_message_request(&input),
            Err(ProtocolError::InvalidRequest(_))
        ));
        input.sequence_id = 10_001;
        input.client_msg_id.clear();
        assert!(matches!(
            encode_send_message_request(&input),
            Err(ProtocolError::InvalidRequest(_))
        ));
    }

    #[test]
    fn decoder_rejects_empty_truncated_and_wrong_wire_inputs() {
        assert!(matches!(
            decode_send_message_response(&[]),
            Err(ProtocolError::Wire(WireError::EmptyInput))
        ));
        assert!(matches!(
            decode_send_message_response(&[0x32, 0x03, 0x01]),
            Err(ProtocolError::Wire(
                WireError::TruncatedLengthDelimited { .. }
            ))
        ));
        assert!(matches!(
            decode_send_message_response(&[0x1a, 0x01, b'x']),
            Err(ProtocolError::Wire(WireError::WrongWireType {
                field: 3,
                ..
            }))
        ));
    }

    #[test]
    fn encoder_is_stable_for_the_same_input() {
        let input = minimal_input();
        let first = encode_send_message_request(&input).expect("input must encode");
        let second = encode_send_message_request(&input).expect("input must encode");
        assert_eq!(first, second);
    }
}
