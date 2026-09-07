//! A small, bounded protobuf wire codec for the fields used by PC IM send.
//!
//! Only protobuf wire types 0 (varint) and 2 (length-delimited) are accepted.
//! That is intentional: accepting an unsupported type and then partially
//! decoding a response would make delivery classification unsafe.

use thiserror::Error;

pub const MAX_FIELD_NUMBER: u32 = 536_870_911;
pub const DEFAULT_MAX_MESSAGE_BYTES: usize = 1024 * 1024;
pub const DEFAULT_MAX_LENGTH_BYTES: usize = 1024 * 1024;
pub const DEFAULT_MAX_FIELDS: usize = 4096;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct DecodeLimits {
    pub max_message_bytes: usize,
    pub max_length_delimited_bytes: usize,
    pub max_fields: usize,
}

impl Default for DecodeLimits {
    fn default() -> Self {
        Self {
            max_message_bytes: DEFAULT_MAX_MESSAGE_BYTES,
            max_length_delimited_bytes: DEFAULT_MAX_LENGTH_BYTES,
            max_fields: DEFAULT_MAX_FIELDS,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum WireType {
    Varint = 0,
    LengthDelimited = 2,
}

impl TryFrom<u8> for WireType {
    type Error = WireError;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value {
            0 => Ok(Self::Varint),
            2 => Ok(Self::LengthDelimited),
            unsupported => Err(WireError::UnsupportedWireType(unsupported)),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WireValue<'a> {
    Varint(u64),
    LengthDelimited(&'a [u8]),
}

impl WireValue<'_> {
    #[must_use]
    pub const fn wire_type(self) -> WireType {
        match self {
            Self::Varint(_) => WireType::Varint,
            Self::LengthDelimited(_) => WireType::LengthDelimited,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WireField<'a> {
    pub number: u32,
    pub value: WireValue<'a>,
}

#[derive(Clone, Debug, Error, Eq, PartialEq)]
pub enum WireError {
    #[error("protobuf message is empty")]
    EmptyInput,
    #[error("protobuf message is {actual} bytes; limit is {max}")]
    MessageTooLarge { actual: usize, max: usize },
    #[error("protobuf field number {0} is invalid")]
    InvalidFieldNumber(u64),
    #[error("protobuf wire type {0} is unsupported")]
    UnsupportedWireType(u8),
    #[error("protobuf field {field} uses {actual:?}; expected {expected:?}")]
    WrongWireType {
        field: u32,
        expected: WireType,
        actual: WireType,
    },
    #[error("protobuf varint is truncated at byte offset {offset}")]
    TruncatedVarint { offset: usize },
    #[error("protobuf varint exceeds 64 bits at byte offset {offset}")]
    VarintTooLong { offset: usize },
    #[error("protobuf varint is non-canonical at byte offset {offset}")]
    NonCanonicalVarint { offset: usize },
    #[error("protobuf length {length} cannot fit this platform")]
    LengthOverflow { length: u64 },
    #[error("protobuf input length {length} cannot fit an unsigned 64-bit value")]
    InputLengthOverflow { length: usize },
    #[error("protobuf length-delimited field declares {declared} bytes; limit is {max}")]
    LengthDelimitedTooLarge { declared: usize, max: usize },
    #[error(
        "protobuf length-delimited field is truncated: declared {declared} bytes, only {remaining} remain"
    )]
    TruncatedLengthDelimited { declared: usize, remaining: usize },
    #[error("protobuf message exceeds field-count limit {max}")]
    TooManyFields { max: usize },
}

#[must_use]
#[allow(clippy::cast_possible_truncation)]
pub fn encode_varint(mut value: u64) -> Vec<u8> {
    let mut output = Vec::with_capacity(10);
    loop {
        // The mask makes this cast exact.
        let low = (value & 0x7f) as u8;
        value >>= 7;
        if value == 0 {
            output.push(low);
            return output;
        }
        output.push(low | 0x80);
    }
}

/// Appends one protobuf varint field.
///
/// # Errors
///
/// Returns [`WireError::InvalidFieldNumber`] when the field number is outside
/// protobuf's valid range.
pub fn append_varint_field(
    output: &mut Vec<u8>,
    field_number: u32,
    value: u64,
) -> Result<(), WireError> {
    append_key(output, field_number, WireType::Varint)?;
    output.extend_from_slice(&encode_varint(value));
    Ok(())
}

/// Appends one protobuf length-delimited field.
///
/// # Errors
///
/// Returns an error when the field number is invalid or the host input length
/// cannot be represented by protobuf's `u64` length.
pub fn append_bytes_field(
    output: &mut Vec<u8>,
    field_number: u32,
    value: &[u8],
) -> Result<(), WireError> {
    append_key(output, field_number, WireType::LengthDelimited)?;
    let length = u64::try_from(value.len()).map_err(|_| WireError::InputLengthOverflow {
        length: value.len(),
    })?;
    output.extend_from_slice(&encode_varint(length));
    output.extend_from_slice(value);
    Ok(())
}

/// Appends one UTF-8 protobuf length-delimited field.
///
/// # Errors
///
/// Returns an error when the field number is invalid or the encoded string
/// length cannot be represented by protobuf's `u64` length.
pub fn append_string_field(
    output: &mut Vec<u8>,
    field_number: u32,
    value: &str,
) -> Result<(), WireError> {
    append_bytes_field(output, field_number, value.as_bytes())
}

fn append_key(
    output: &mut Vec<u8>,
    field_number: u32,
    wire_type: WireType,
) -> Result<(), WireError> {
    if field_number == 0 || field_number > MAX_FIELD_NUMBER {
        return Err(WireError::InvalidFieldNumber(u64::from(field_number)));
    }
    let key = (u64::from(field_number) << 3) | wire_type as u64;
    output.extend_from_slice(&encode_varint(key));
    Ok(())
}

/// Decodes a non-empty protobuf message with conservative default limits.
///
/// # Errors
///
/// Returns a typed [`WireError`] for empty, malformed, oversized, or
/// unsupported input.
pub fn decode_message(input: &[u8]) -> Result<Vec<WireField<'_>>, WireError> {
    decode_message_with_limits(input, DecodeLimits::default())
}

/// Decodes a protobuf message using caller-supplied size and field limits.
///
/// # Errors
///
/// Returns a typed [`WireError`] for empty, malformed, oversized, or
/// unsupported input.
pub fn decode_message_with_limits(
    input: &[u8],
    limits: DecodeLimits,
) -> Result<Vec<WireField<'_>>, WireError> {
    if input.is_empty() {
        return Err(WireError::EmptyInput);
    }
    if input.len() > limits.max_message_bytes {
        return Err(WireError::MessageTooLarge {
            actual: input.len(),
            max: limits.max_message_bytes,
        });
    }

    let mut offset = 0;
    let mut fields = Vec::new();
    while offset < input.len() {
        if fields.len() >= limits.max_fields {
            return Err(WireError::TooManyFields {
                max: limits.max_fields,
            });
        }
        let (key, next) = decode_varint_at(input, offset)?;
        offset = next;
        let field_number = key >> 3;
        if field_number == 0 || field_number > u64::from(MAX_FIELD_NUMBER) {
            return Err(WireError::InvalidFieldNumber(field_number));
        }
        let wire_type = WireType::try_from(key.to_le_bytes()[0] & 0x07)?;
        let value = match wire_type {
            WireType::Varint => {
                let (value, next) = decode_varint_at(input, offset)?;
                offset = next;
                WireValue::Varint(value)
            }
            WireType::LengthDelimited => {
                let (declared, next) = decode_varint_at(input, offset)?;
                offset = next;
                let length = usize::try_from(declared)
                    .map_err(|_| WireError::LengthOverflow { length: declared })?;
                if length > limits.max_length_delimited_bytes {
                    return Err(WireError::LengthDelimitedTooLarge {
                        declared: length,
                        max: limits.max_length_delimited_bytes,
                    });
                }
                let remaining = input.len() - offset;
                if length > remaining {
                    return Err(WireError::TruncatedLengthDelimited {
                        declared: length,
                        remaining,
                    });
                }
                let end = offset + length;
                let bytes = &input[offset..end];
                offset = end;
                WireValue::LengthDelimited(bytes)
            }
        };
        let number =
            u32::try_from(field_number).map_err(|_| WireError::InvalidFieldNumber(field_number))?;
        fields.push(WireField { number, value });
    }
    Ok(fields)
}

fn decode_varint_at(input: &[u8], start: usize) -> Result<(u64, usize), WireError> {
    let mut value = 0_u64;
    for index in 0..10 {
        let offset = start + index;
        let Some(&byte) = input.get(offset) else {
            return Err(WireError::TruncatedVarint { offset });
        };
        if index == 9 && byte > 1 {
            return Err(WireError::VarintTooLong { offset: start });
        }
        value |= u64::from(byte & 0x7f) << (index * 7);
        if byte & 0x80 == 0 {
            if index > 0 && byte == 0 {
                return Err(WireError::NonCanonicalVarint { offset: start });
            }
            return Ok((value, offset + 1));
        }
    }
    Err(WireError::VarintTooLong { offset: start })
}

/// Extracts a varint from a decoded field.
///
/// # Errors
///
/// Returns [`WireError::WrongWireType`] when the field is length-delimited.
pub fn expect_varint(field: WireField<'_>) -> Result<u64, WireError> {
    match field.value {
        WireValue::Varint(value) => Ok(value),
        WireValue::LengthDelimited(_) => Err(WireError::WrongWireType {
            field: field.number,
            expected: WireType::Varint,
            actual: WireType::LengthDelimited,
        }),
    }
}

/// Extracts bytes from a decoded length-delimited field.
///
/// # Errors
///
/// Returns [`WireError::WrongWireType`] when the field is a varint.
pub fn expect_bytes(field: WireField<'_>) -> Result<&[u8], WireError> {
    match field.value {
        WireValue::LengthDelimited(value) => Ok(value),
        WireValue::Varint(_) => Err(WireError::WrongWireType {
            field: field.number,
            expected: WireType::LengthDelimited,
            actual: WireType::Varint,
        }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn varint_round_trips_boundaries() {
        for value in [0, 1, 127, 128, 16_383, 16_384, u64::MAX] {
            let encoded = encode_varint(value);
            let (decoded, next) = decode_varint_at(&encoded, 0).expect("varint must decode");
            assert_eq!(decoded, value);
            assert_eq!(next, encoded.len());
        }
    }

    #[test]
    fn message_round_trips_supported_wire_types() {
        let mut encoded = Vec::new();
        append_varint_field(&mut encoded, 1, 15_000).expect("valid field");
        append_string_field(&mut encoded, 100, "你好").expect("valid field");

        let fields = decode_message(&encoded).expect("message must decode");
        assert_eq!(fields.len(), 2);
        assert_eq!(expect_varint(fields[0]).expect("varint"), 15_000);
        assert_eq!(expect_bytes(fields[1]).expect("bytes"), "你好".as_bytes());
    }

    #[test]
    fn empty_input_is_rejected() {
        assert_eq!(decode_message(&[]), Err(WireError::EmptyInput));
    }

    #[test]
    fn truncated_tag_varint_is_rejected() {
        assert_eq!(
            decode_message(&[0x80]),
            Err(WireError::TruncatedVarint { offset: 1 })
        );
    }

    #[test]
    fn truncated_value_varint_is_rejected() {
        assert_eq!(
            decode_message(&[0x08, 0x80]),
            Err(WireError::TruncatedVarint { offset: 2 })
        );
    }

    #[test]
    fn overlong_varints_are_rejected() {
        assert_eq!(
            decode_message(&[0x80, 0x00]),
            Err(WireError::NonCanonicalVarint { offset: 0 })
        );
        assert!(matches!(
            decode_message(&[0x80; 11]),
            Err(WireError::VarintTooLong { offset: 0 })
        ));
    }

    #[test]
    fn unsupported_and_zero_field_tags_are_rejected() {
        assert_eq!(
            decode_message(&[0x0d]),
            Err(WireError::UnsupportedWireType(5))
        );
        assert_eq!(
            decode_message(&[0x00, 0x00]),
            Err(WireError::InvalidFieldNumber(0))
        );
    }

    #[test]
    fn truncated_and_oversized_length_fields_are_rejected() {
        assert_eq!(
            decode_message(&[0x0a, 0x03, b'a']),
            Err(WireError::TruncatedLengthDelimited {
                declared: 3,
                remaining: 1,
            })
        );

        let limits = DecodeLimits {
            max_message_bytes: 10,
            max_length_delimited_bytes: 2,
            max_fields: 2,
        };
        assert_eq!(
            decode_message_with_limits(&[0x0a, 0x03, b'a', b'b', b'c'], limits),
            Err(WireError::LengthDelimitedTooLarge {
                declared: 3,
                max: 2,
            })
        );
    }

    #[test]
    fn message_size_and_field_count_limits_are_enforced() {
        let size_limits = DecodeLimits {
            max_message_bytes: 1,
            ..DecodeLimits::default()
        };
        assert_eq!(
            decode_message_with_limits(&[0x08, 0x01], size_limits),
            Err(WireError::MessageTooLarge { actual: 2, max: 1 })
        );

        let field_limits = DecodeLimits {
            max_fields: 1,
            ..DecodeLimits::default()
        };
        assert_eq!(
            decode_message_with_limits(&[0x08, 0x01, 0x10, 0x02], field_limits),
            Err(WireError::TooManyFields { max: 1 })
        );
    }

    #[test]
    fn recognized_fields_reject_wrong_wire_types() {
        let fields = decode_message(&[0x0a, 0x01, b'x']).expect("generic wire is valid");
        assert!(matches!(
            expect_varint(fields[0]),
            Err(WireError::WrongWireType {
                field: 1,
                expected: WireType::Varint,
                actual: WireType::LengthDelimited,
            })
        ));
    }
}
