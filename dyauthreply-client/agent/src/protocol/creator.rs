//! Creator-center envelope used only for scoped history reads.
use super::{
    im::ProtocolError,
    wire::{append_bytes_field, append_string_field, append_varint_field},
};
pub(super) fn envelope(
    command: u64,
    sequence: u64,
    body: &[u8],
    headers: &[(&str, &str)],
) -> Result<Vec<u8>, ProtocolError> {
    let mut out = Vec::new();
    append_varint_field(&mut out, 1, command)?;
    append_varint_field(&mut out, 2, sequence)?;
    append_string_field(&mut out, 3, "0.2.0")?;
    append_string_field(&mut out, 4, "")?;
    append_varint_field(&mut out, 5, 3)?;
    append_varint_field(&mut out, 6, 0)?;
    append_string_field(&mut out, 7, "0f64c47:feat/pc-im")?;
    append_bytes_field(&mut out, 8, body)?;
    append_string_field(&mut out, 9, "")?;
    append_string_field(&mut out, 11, "douyin_creator")?;
    for (key, value) in headers {
        super::im::append_map_entry(&mut out, 15, key, value)?;
    }
    append_varint_field(&mut out, 18, 1)?;
    append_string_field(&mut out, 21, "douyin_creator")?;
    append_string_field(&mut out, 22, "web_sdk")?;
    Ok(out)
}
