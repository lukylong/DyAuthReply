//! Native Frontier `PushFrame` codec. Transport ACK is available only after the
//! entire bounded frame is decoded; callers must persist messages before sending it.
use super::{
    inbox::InboxMessage,
    wire::{self, WireField, WireValue},
};
use anyhow::{Context, Result};
use md5::{Digest, Md5};
use std::io::Read;

pub const MAX_FRAME_BYTES: usize = 1024 * 1024;
pub const MAX_MESSAGES: usize = 128;
const MAX_ACK_BYTES: usize = 4096;

pub struct FrontierFrame {
    pub messages: Vec<InboxMessage>,
    pub ack: Option<Vec<u8>>,
    pub log_id: u64,
    pub control_status: Option<i64>,
    pub control_kind: Option<i64>,
}
/// Fixed authority; token is secret and the returned URL must never be logged.
/// # Errors
/// Rejects missing or oversized identifiers/session token.
pub fn connection_url(device_id: &str, session_id: &str) -> Result<String> {
    anyhow::ensure!(
        !device_id.is_empty()
            && device_id.len() <= 128
            && device_id.bytes().all(|b| b.is_ascii_digit()),
        "invalid Frontier device identity"
    );
    anyhow::ensure!(
        !session_id.is_empty() && session_id.len() <= 4096,
        "missing Frontier session token"
    );
    let key = format!(
        "{:x}",
        Md5::digest(format!(
            "9e1bd35ec9db7b8d846de66ed140b1ad9{device_id}f8a69f1719916z"
        ))
    );
    Ok(format!("wss://frontier-im.douyin.com/ws/v2?aid=6383&device_platform=douyin_pc&fpid=9&device_id={device_id}&token={}&access_key={key}",super::http_plan::percent_encode_rfc3986(session_id)))
}
fn field<'a>(fields: &[WireField<'a>], n: u32) -> Result<Option<WireField<'a>>> {
    let mut values = fields.iter().filter(|f| f.number == n);
    let first = values.next().copied();
    anyhow::ensure!(
        values.next().is_none(),
        "duplicate Frontier singleton field"
    );
    Ok(first)
}
fn bytes<'a>(fields: &[WireField<'a>], n: u32) -> Result<&'a [u8]> {
    Ok(field(fields, n)?
        .map(wire::expect_bytes)
        .transpose()?
        .unwrap_or_default())
}
fn number(fields: &[WireField<'_>], n: u32) -> Result<u64> {
    Ok(field(fields, n)?
        .map(wire::expect_varint)
        .transpose()?
        .unwrap_or_default())
}
fn text<'a>(fields: &[WireField<'a>], n: u32) -> Result<&'a str> {
    std::str::from_utf8(bytes(fields, n)?).context("invalid Frontier UTF-8")
}
/// Matches reference ACK bytes. Empty/no-ACK controls do not create ACK frames.
/// # Errors
/// Rejects oversized opaque ACK metadata.
pub fn encode_ack(log_id: u64, token: &str) -> Result<Option<Vec<u8>>> {
    anyhow::ensure!(
        token.len() <= MAX_ACK_BYTES,
        "Frontier ACK metadata too large"
    );
    if log_id == 0 || token.is_empty() {
        return Ok(None);
    }
    let mut data = Vec::new();
    wire::append_varint_field(&mut data, 2, log_id)?;
    wire::append_string_field(&mut data, 7, token)?;
    Ok(Some(data))
}
/// # Errors
/// Malformed/compressed/oversized/unsupported payloads fail as a whole: never ACK
/// a partially parsed page. A valid no-payload control frame carries no messages.
pub fn decode_frame(raw: &[u8]) -> Result<FrontierFrame> {
    anyhow::ensure!(raw.len() <= MAX_FRAME_BYTES, "Frontier frame too large");
    let outer = wire::decode_message(raw)?;
    let log_id = number(&outer, 2)?;
    let payload = bytes(&outer, 8)?;
    if payload.is_empty() {
        return Ok(FrontierFrame {
            messages: vec![],
            ack: None,
            log_id,
            control_status: None,
            control_kind: None,
        });
    }
    let decoded = match text(&outer, 6)? {
        "gzip" => {
            let mut result = Vec::new();
            flate2::read::MultiGzDecoder::new(payload)
                .take((MAX_FRAME_BYTES + 1) as u64)
                .read_to_end(&mut result)
                .context("invalid Frontier gzip")?;
            anyhow::ensure!(
                result.len() <= MAX_FRAME_BYTES,
                "Frontier decompression limit"
            );
            result
        }
        "" | "identity" | "pb" | "protobuf" => payload.to_vec(),
        "utf-8" => {
            let object: serde_json::Value =
                serde_json::from_slice(payload).context("invalid Frontier control JSON")?;
            anyhow::ensure!(
                text(&outer, 7)? == "text/json"
                    && matches!(
                        object.get("msg_type").and_then(serde_json::Value::as_i64),
                        Some(1 | 2)
                    ),
                "unknown Frontier JSON notification keys={:?} msg_type={:?} type={:?}",
                object.as_object().map(|o| o.keys().collect::<Vec<_>>()),
                object.get("msg_type").and_then(serde_json::Value::as_i64),
                text(&outer, 7)?
            );
            let status = object
                .get("status_code")
                .and_then(serde_json::Value::as_i64)
                .context("missing Frontier control status")?;
            // This is a server notification, not an IM delivery or send-business
            // response. Preserve its code but do not infer login/risk/sendability.
            return Ok(FrontierFrame {
                messages: vec![],
                ack: None,
                log_id,
                control_status: Some(status),
                control_kind: object.get("msg_type").and_then(serde_json::Value::as_i64),
            });
        }
        _ => anyhow::bail!("unsupported Frontier encoding"),
    };
    let response = wire::decode_message(&decoded)?;
    let messages = decode_payload(&response)?;
    let need_ack = number(&response, 9)? != 0;
    let mut ack_token = if need_ack { text(&response, 5)? } else { "" };
    if ack_token.is_empty() {
        for entry in &outer {
            if entry.number == 8 {
                continue;
            }
            if let WireValue::LengthDelimited(value) = entry.value {
                if let Ok(value) = std::str::from_utf8(value) {
                    if value.starts_with("msg_") {
                        ack_token = value;
                        break;
                    }
                }
            }
        }
    }
    Ok(FrontierFrame {
        messages,
        ack: encode_ack(log_id, ack_token)?,
        log_id,
        control_status: None,
        control_kind: None,
    })
}
fn decode_payload(response: &[WireField<'_>]) -> Result<Vec<InboxMessage>> {
    // Real PC IM envelope: cmd field1 -> body field6 -> cmd-numbered wrapper.
    // The separate list layout uses repeated bytes field1; do not confuse them.
    let command = response.iter().find_map(|f| {
        if f.number == 1 {
            if let WireValue::Varint(n) = f.value {
                Some(n)
            } else {
                None
            }
        } else {
            None
        }
    });
    let mut messages = Vec::new();
    if let Some(command) = command {
        anyhow::ensure!(
            number(response, 1)? == command && command > 0,
            "invalid Frontier command"
        );
        anyhow::ensure!(number(response, 3)? == 0, "Frontier business failure");
        let body = wire::decode_message(bytes(response, 6)?)?;
        let wrapper = wire::decode_message(bytes(&body, u32::try_from(command)?)?)?;
        for value in wrapper.iter().filter(|f| matches!(f.number, 1 | 5)) {
            push_message(&mut messages, wire::expect_bytes(*value)?)?;
        }
    } else {
        for value in response.iter().filter(|f| f.number == 1) {
            let entry = wire::decode_message(wire::expect_bytes(*value)?)?;
            let payload = bytes(&entry, 2)?;
            if !payload.is_empty() {
                push_message(&mut messages, payload)?;
            }
        }
    }
    Ok(messages)
}
fn push_message(messages: &mut Vec<InboxMessage>, raw: &[u8]) -> Result<()> {
    anyhow::ensure!(messages.len() < MAX_MESSAGES, "too many Frontier messages");
    let message =
        super::inbox::decode_message(raw)?.context("invalid Frontier message identity")?;
    messages.push(message);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn unhex(s: &str) -> Vec<u8> {
        s.as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|x| u8::from_str_radix(std::str::from_utf8(x).unwrap(), 16).unwrap())
            .collect()
    }
    #[test]
    fn reference_real_list_and_gzip_frames_match_exact_ack_bytes() {
        let corpus: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/frontier.json")).unwrap();
        for case in corpus["cases"].as_array().unwrap() {
            let frame = decode_frame(&unhex(case["hex"].as_str().unwrap())).unwrap();
            assert_eq!(frame.messages.len(), 1);
            assert_eq!(frame.messages[0].server_message_id, 123_456);
            assert_eq!(frame.messages[0].client_message_id, "fixture-client");
            assert_eq!(frame.messages[0].sender_sec_uid, "fixture-peer");
            assert_eq!(frame.ack.unwrap(), unhex(case["ack_hex"].as_str().unwrap()));
        }
        let url = connection_url("42", "a+b&c").unwrap();
        assert!(url.ends_with(corpus["access_key"].as_str().unwrap()));
        assert!(url.contains("token=a%2Bb%26c"));
        assert!(connection_url("", "token").is_err());
        assert!(connection_url("42", "").is_err());
    }
    #[test]
    fn malformed_duplicate_fields_and_decompression_bombs_never_return_ack() {
        use std::io::Write;
        assert!(decode_frame(&[]).is_err());
        assert!(decode_frame(&[0x42, 0x7f]).is_err());
        let mut raw = Vec::new();
        wire::append_varint_field(&mut raw, 2, 1).unwrap();
        wire::append_varint_field(&mut raw, 2, 2).unwrap();
        assert!(decode_frame(&raw).is_err());
        let mut zip = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        zip.write_all(&vec![0; MAX_FRAME_BYTES + 1]).unwrap();
        let mut raw = Vec::new();
        wire::append_string_field(&mut raw, 6, "gzip").unwrap();
        wire::append_bytes_field(&mut raw, 8, &zip.finish().unwrap()).unwrap();
        assert!(decode_frame(&raw).is_err());
        assert!(encode_ack(1, &"x".repeat(MAX_ACK_BYTES + 1)).is_err());
        assert!(encode_ack(0, "msg_x").unwrap().is_none());
    }
    #[test]
    fn malformed_later_message_fails_whole_frame_and_no_partial_ack() {
        let corpus: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/frontier.json")).unwrap();
        let mut raw = unhex(corpus["cases"][0]["hex"].as_str().unwrap());
        raw.pop();
        assert!(decode_frame(&raw).is_err());
        let mut response = Vec::new();
        wire::append_bytes_field(&mut response, 1, &[0x12, 0x01, 0xff]).unwrap();
        wire::append_string_field(&mut response, 5, "ack").unwrap();
        wire::append_varint_field(&mut response, 9, 1).unwrap();
        let mut raw = Vec::new();
        wire::append_varint_field(&mut raw, 2, 1).unwrap();
        wire::append_bytes_field(&mut raw, 8, &response).unwrap();
        assert!(decode_frame(&raw).is_err());
    }
    #[test]
    fn json_status_notification_is_not_a_message_or_send_capability() {
        let mut raw = Vec::new();
        wire::append_string_field(&mut raw, 6, "utf-8").unwrap();
        wire::append_string_field(&mut raw, 7, "text/json").unwrap();
        wire::append_bytes_field(
            &mut raw,
            8,
            br#"{"msg_type":1,"status_code":2,"toast_content":"","update_time":0}"#,
        )
        .unwrap();
        let frame = decode_frame(&raw).unwrap();
        assert!(frame.messages.is_empty());
        assert!(frame.ack.is_none());
        assert_eq!(frame.control_status, Some(2));
        assert_eq!(frame.control_kind, Some(1));
        let mut other = Vec::new();
        wire::append_string_field(&mut other, 6, "utf-8").unwrap();
        wire::append_string_field(&mut other, 7, "text/json").unwrap();
        wire::append_bytes_field(
            &mut other,
            8,
            br#"{"msg_type":2,"status_code":0,"toast_content":"","update_time":0}"#,
        )
        .unwrap();
        assert_eq!(decode_frame(&other).unwrap().control_kind, Some(2));
    }
}
