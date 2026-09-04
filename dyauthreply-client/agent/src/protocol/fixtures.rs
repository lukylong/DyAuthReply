//! Typed loader and fail-closed verifier for the shared protocol corpus.

use std::fmt::Write as _;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

use super::classify::{classify_delivery, DeliveryClass};
use super::im::{
    decode_send_message_response, encode_send_message_request, ProtocolError, SendMessageResponse,
    SendRequestInput,
};

const EMBEDDED_CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../protocol-fixtures/douyin_pc_im_v1.json"
));
const EXPECTED_SCHEMA_VERSION: u32 = 1;
const EXPECTED_CORPUS_ID: &str = "douyin-pc-im-send-v1";
const EXPECTED_PROTOCOL: &str = "douyin_pc_im_send";
const EXPECTED_REFERENCE_REVISION: &str = "9afaf79580b1ee84e8954ff906ff26869d5b7f1f";

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct ParityReport {
    pub schema_version: u32,
    pub corpus_id: String,
    pub reference_revision: String,
    pub request_cases: usize,
    pub response_cases: usize,
    pub corpus_sha256: String,
    pub verified: bool,
}

#[derive(Debug, Error)]
pub enum FixtureError {
    #[error("embedded protocol corpus JSON is invalid: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("embedded protocol corpus metadata is invalid: {0}")]
    InvalidMetadata(String),
    #[error("protocol corpus case {case:?} contains invalid hex: {detail}")]
    InvalidHex { case: String, detail: String },
    #[error("protocol corpus request case {case:?} cannot be encoded: {source}")]
    RequestEncoding {
        case: String,
        #[source]
        source: ProtocolError,
    },
    #[error("protocol corpus response case {case:?} cannot be decoded: {source}")]
    ResponseDecoding {
        case: String,
        #[source]
        source: ProtocolError,
    },
    #[error(
        "protocol corpus case {case:?} mismatch for {field}: expected {expected}, got {actual}"
    )]
    Mismatch {
        case: String,
        field: &'static str,
        expected: String,
        actual: String,
    },
}

#[derive(Debug, Deserialize)]
struct ProtocolCorpus {
    schema_version: u32,
    corpus_id: String,
    protocol: String,
    contains_secrets: bool,
    reference: Reference,
    scope: Scope,
    request_cases: Vec<RequestCase>,
    response_cases: Vec<ResponseCase>,
}

#[derive(Debug, Deserialize)]
struct Reference {
    repository: String,
    revision: String,
}

#[derive(Debug, Deserialize)]
#[allow(clippy::struct_excessive_bools)]
struct Scope {
    send_request: bool,
    send_response: bool,
    signer: bool,
    http_transport: bool,
    inbox: bool,
    websocket: bool,
}

#[derive(Debug, Deserialize)]
struct RequestCase {
    id: String,
    input: SendRequestInput,
    expected: RequestExpected,
}

#[derive(Debug, Deserialize)]
#[allow(clippy::struct_field_names)]
struct RequestExpected {
    body_hex: String,
    body_sha256: String,
    body_length: usize,
}

#[derive(Debug, Deserialize)]
struct ResponseCase {
    id: String,
    http_status: Option<u16>,
    expected_client_msg_id: String,
    body_hex: String,
    expected: ResponseExpected,
}

#[derive(Debug, Deserialize)]
#[allow(clippy::struct_excessive_bools)]
struct ResponseExpected {
    status_code: i64,
    status_msg: String,
    server_msg_id: u64,
    client_msg_id: String,
    biz_status_code: i64,
    biz_status_text: String,
    biz_raw_check_code: i64,
    outer_status_present: bool,
    has_response_body: bool,
    has_inner_response: bool,
    business_payload_present: bool,
    business_payload_valid: bool,
    classification: DeliveryClass,
}

/// Verifies every request byte, response semantic, and classification in the
/// embedded shared corpus without accessing credentials or the network.
///
/// # Errors
///
/// Returns [`FixtureError`] when metadata, hex, deterministic encoding,
/// decoding, hashing, or a frozen expected value differs.
pub fn verify_embedded_corpus() -> Result<ParityReport, FixtureError> {
    verify_corpus_source(EMBEDDED_CORPUS)
}

fn verify_corpus_source(source: &str) -> Result<ParityReport, FixtureError> {
    let corpus: ProtocolCorpus = serde_json::from_str(source)?;
    validate_metadata(&corpus)?;

    for case in &corpus.request_cases {
        verify_request_case(case)?;
    }
    for case in &corpus.response_cases {
        verify_response_case(case)?;
    }

    Ok(ParityReport {
        schema_version: corpus.schema_version,
        corpus_id: corpus.corpus_id,
        reference_revision: corpus.reference.revision,
        request_cases: corpus.request_cases.len(),
        response_cases: corpus.response_cases.len(),
        corpus_sha256: sha256_hex(source.as_bytes()),
        verified: true,
    })
}

fn validate_metadata(corpus: &ProtocolCorpus) -> Result<(), FixtureError> {
    ensure_metadata(
        corpus.schema_version == EXPECTED_SCHEMA_VERSION,
        format!(
            "schema_version must be {EXPECTED_SCHEMA_VERSION}, got {}",
            corpus.schema_version
        ),
    )?;
    ensure_metadata(
        corpus.corpus_id == EXPECTED_CORPUS_ID,
        format!(
            "corpus_id must be {EXPECTED_CORPUS_ID:?}, got {:?}",
            corpus.corpus_id
        ),
    )?;
    ensure_metadata(
        corpus.protocol == EXPECTED_PROTOCOL,
        format!(
            "protocol must be {EXPECTED_PROTOCOL:?}, got {:?}",
            corpus.protocol
        ),
    )?;
    ensure_metadata(
        !corpus.contains_secrets,
        "contains_secrets must be false".to_owned(),
    )?;
    ensure_metadata(
        corpus.reference.repository == "https://github.com/lukylong/DouYin_Spider",
        "reference repository is not the frozen upstream".to_owned(),
    )?;
    ensure_metadata(
        corpus.reference.revision == EXPECTED_REFERENCE_REVISION,
        format!(
            "reference revision must be {EXPECTED_REFERENCE_REVISION}, got {}",
            corpus.reference.revision
        ),
    )?;
    ensure_metadata(
        corpus.scope.send_request && corpus.scope.send_response,
        "send request and response scope must both be enabled".to_owned(),
    )?;
    ensure_metadata(
        !corpus.scope.signer
            && !corpus.scope.http_transport
            && !corpus.scope.inbox
            && !corpus.scope.websocket,
        "signer, HTTP transport, inbox, and WebSocket must remain outside this corpus".to_owned(),
    )?;
    ensure_metadata(
        !corpus.request_cases.is_empty(),
        "request_cases must not be empty".to_owned(),
    )?;
    ensure_metadata(
        !corpus.response_cases.is_empty(),
        "response_cases must not be empty".to_owned(),
    )
}

fn ensure_metadata(condition: bool, message: String) -> Result<(), FixtureError> {
    if condition {
        Ok(())
    } else {
        Err(FixtureError::InvalidMetadata(message))
    }
}

fn verify_request_case(case: &RequestCase) -> Result<(), FixtureError> {
    let expected_bytes = decode_hex(&case.id, &case.expected.body_hex)?;
    let actual = encode_send_message_request(&case.input).map_err(|source| {
        FixtureError::RequestEncoding {
            case: case.id.clone(),
            source,
        }
    })?;

    compare(
        &case.id,
        "body_length",
        &case.expected.body_length,
        &actual.len(),
    )?;
    compare(
        &case.id,
        "body_hex",
        &case.expected.body_hex,
        &hex_encode(&actual),
    )?;
    compare(
        &case.id,
        "decoded_body_hex",
        &case.expected.body_hex,
        &hex_encode(&expected_bytes),
    )?;
    compare(
        &case.id,
        "body_sha256",
        &case.expected.body_sha256,
        &sha256_hex(&actual),
    )
}

fn verify_response_case(case: &ResponseCase) -> Result<(), FixtureError> {
    let body = decode_hex(&case.id, &case.body_hex)?;
    let actual =
        decode_send_message_response(&body).map_err(|source| FixtureError::ResponseDecoding {
            case: case.id.clone(),
            source,
        })?;
    let expected = case.expected.to_response();
    compare(&case.id, "decoded response", &expected, &actual)?;

    let classification = classify_delivery(case.http_status, &actual, &case.expected_client_msg_id);
    compare(
        &case.id,
        "classification",
        &case.expected.classification,
        &classification,
    )
}

impl ResponseExpected {
    fn to_response(&self) -> SendMessageResponse {
        SendMessageResponse {
            status_code: self.status_code,
            status_msg: self.status_msg.clone(),
            server_msg_id: self.server_msg_id,
            client_msg_id: self.client_msg_id.clone(),
            biz_status_code: self.biz_status_code,
            biz_status_text: self.biz_status_text.clone(),
            biz_raw_check_code: self.biz_raw_check_code,
            outer_status_present: self.outer_status_present,
            has_response_body: self.has_response_body,
            has_inner_response: self.has_inner_response,
            business_payload_present: self.business_payload_present,
            business_payload_valid: self.business_payload_valid,
        }
    }
}

fn compare<T>(case: &str, field: &'static str, expected: &T, actual: &T) -> Result<(), FixtureError>
where
    T: std::fmt::Debug + PartialEq,
{
    if expected == actual {
        Ok(())
    } else {
        Err(FixtureError::Mismatch {
            case: case.to_owned(),
            field,
            expected: format!("{expected:?}"),
            actual: format!("{actual:?}"),
        })
    }
}

fn decode_hex(case: &str, value: &str) -> Result<Vec<u8>, FixtureError> {
    if !value.len().is_multiple_of(2) {
        return Err(FixtureError::InvalidHex {
            case: case.to_owned(),
            detail: "hex string has odd length".to_owned(),
        });
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .enumerate()
        .map(|(index, pair)| {
            let high = hex_nibble(pair[0]);
            let low = hex_nibble(pair[1]);
            match (high, low) {
                (Some(high), Some(low)) => Ok((high << 4) | low),
                _ => Err(FixtureError::InvalidHex {
                    case: case.to_owned(),
                    detail: format!("non-hex byte at character {}", index * 2),
                }),
            }
        })
        .collect()
}

const fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn hex_encode(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(&mut output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

fn sha256_hex(bytes: &[u8]) -> String {
    hex_encode(&Sha256::digest(bytes))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_corpus_verifies_every_frozen_case() {
        let report = verify_embedded_corpus().expect("embedded corpus must verify");
        assert_eq!(report.schema_version, 1);
        assert_eq!(report.corpus_id, EXPECTED_CORPUS_ID);
        assert_eq!(report.reference_revision, EXPECTED_REFERENCE_REVISION);
        assert_eq!(report.request_cases, 2);
        assert_eq!(report.response_cases, 31);
        assert_eq!(report.corpus_sha256.len(), 64);
        assert!(report.verified);
    }

    #[test]
    fn corpus_verification_fails_closed_on_byte_drift() {
        let tampered = EMBEDDED_CORPUS.replacen(
            "1fcb8e0391d50963dc1a6873a08f5b4891ce3207b5484b2c5017af76216c02c2",
            "0fcb8e0391d50963dc1a6873a08f5b4891ce3207b5484b2c5017af76216c02c2",
            1,
        );
        assert!(matches!(
            verify_corpus_source(&tampered),
            Err(FixtureError::Mismatch {
                field: "body_sha256",
                ..
            })
        ));
    }

    #[test]
    fn corpus_verification_rejects_secret_or_live_transport_scope() {
        let secret = EMBEDDED_CORPUS.replacen(
            "\"contains_secrets\": false",
            "\"contains_secrets\": true",
            1,
        );
        assert!(matches!(
            verify_corpus_source(&secret),
            Err(FixtureError::InvalidMetadata(_))
        ));

        let live =
            EMBEDDED_CORPUS.replacen("\"http_transport\": false", "\"http_transport\": true", 1);
        assert!(matches!(
            verify_corpus_source(&live),
            Err(FixtureError::InvalidMetadata(_))
        ));
    }

    #[test]
    fn hex_decoder_rejects_odd_and_invalid_input() {
        assert!(matches!(
            decode_hex("case", "0"),
            Err(FixtureError::InvalidHex { .. })
        ));
        assert!(matches!(
            decode_hex("case", "gg"),
            Err(FixtureError::InvalidHex { .. })
        ));
    }
}
