//! Fail-closed verifier for the independent HTTP `RequestPlan` corpus.

use std::{collections::BTreeMap, fmt::Write as _};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

use super::http_plan::{
    finalize_send_request, parse_plan_digest, prepare_send_request, FingerprintInput,
    OrderedHeader, RequestPlanError, SendHttpPlanInput, SignerOutputs, TicketGuardCredential,
    UnsignedRequestPlan, COOKIE_HOST, MAX_BODY_BYTES, MAX_COOKIE_HEADER_BYTES, MAX_FINAL_URL_BYTES,
    MAX_HEADER_BYTES, MAX_HEADER_COUNT, MAX_TICKET_FIELD_BYTES, MAX_TIMEOUT_MS,
    MAX_USER_AGENT_BYTES, SEND_ENDPOINT, SEND_METHOD, SEND_PATH, SIGNING_HOST,
};

const EMBEDDED_HTTP_PLAN_CORPUS: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../protocol-fixtures/douyin_pc_im_http_plan_v1.json"
));
const EXPECTED_SCHEMA_VERSION: u32 = 1;
const EXPECTED_CORPUS_ID: &str = "douyin-pc-im-http-plan-v1";
const EXPECTED_PROTOCOL: &str = "douyin_pc_im_http_request_plan";
const EXPECTED_REFERENCE_REPOSITORY: &str = "https://github.com/lukylong/DouYin_Spider";
const EXPECTED_REFERENCE_REVISION: &str = "9afaf79580b1ee84e8954ff906ff26869d5b7f1f";
const EXPECTED_WIRE_CORPUS_PATH: &str = "douyin_pc_im_v1.json";
const EXPECTED_WIRE_CORPUS_SHA256: &str =
    "043e92fc54582c16b9baab50f6c106776489f443ccb71f2862647b17200fa234";
const EXPECTED_HTTP_PLAN_CORPUS_SHA256: &str =
    "88f00c3c7014fae64edae44065916d70eff2ed8b82054602f73e2a14c7862d47";
const EXPECTED_DIGEST_PREFIX_HEX: &str = "44595f485454505f504c414e5f563100";

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RequestPlanParityReport {
    pub schema_version: u32,
    pub corpus_id: String,
    pub reference_revision: String,
    pub happy_cases: usize,
    pub rejection_cases: usize,
    pub corpus_sha256: String,
    pub verified: bool,
}

#[derive(Debug, Error)]
pub enum HttpFixtureError {
    #[error("embedded HTTP RequestPlan corpus JSON is invalid: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("embedded HTTP RequestPlan corpus metadata is invalid: {0}")]
    InvalidMetadata(String),
    #[error("HTTP RequestPlan case {case:?} has an invalid body reference")]
    InvalidBodyReference { case: String },
    #[error("HTTP RequestPlan case {case:?} failed with {code}: {source}")]
    RequestPlan {
        case: String,
        code: &'static str,
        #[source]
        source: RequestPlanError,
    },
    #[error(
        "HTTP RequestPlan case {case:?} mismatch for {field}: expected {expected}, got {actual}"
    )]
    Mismatch {
        case: String,
        field: &'static str,
        expected: String,
        actual: String,
    },
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct HttpPlanCorpus {
    schema_version: u32,
    corpus_id: String,
    protocol: String,
    contains_secrets: bool,
    reference: Reference,
    scope: Scope,
    wire_corpus: WireCorpusReference,
    constants: Constants,
    limits: Limits,
    happy_cases: Vec<HappyCase>,
    rejection_cases: Vec<RejectionCase>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Reference {
    repository: String,
    revision: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(clippy::struct_excessive_bools)]
struct Scope {
    request_plan: bool,
    signer_inputs: bool,
    signer_algorithms: bool,
    http_network: bool,
    credential_loader: bool,
    inbox: bool,
    websocket: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct WireCorpusReference {
    path: String,
    sha256: String,
    request_cases: usize,
    response_cases: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Constants {
    method: String,
    endpoint: String,
    path: String,
    signing_host: String,
    cookie_host: String,
    query_order: Vec<String>,
    ticket_header_order: Vec<String>,
    plan_digest_algorithm: String,
    plan_digest_prefix_hex: String,
    plan_digest_field_order: Vec<String>,
    plan_digest_frame: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Limits {
    max_body_bytes: usize,
    max_final_url_bytes: usize,
    max_cookie_bytes: usize,
    max_headers: usize,
    max_header_bytes: usize,
    max_user_agent_bytes: usize,
    max_field_bytes: usize,
    min_timeout_ms: u64,
    max_timeout_ms: u64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct HappyCase {
    id: String,
    input: Input,
    signer_outputs: SignerOutputDto,
    expected: Expected,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RejectionCase {
    id: String,
    stage: String,
    input: Input,
    signer_outputs: Option<SignerOutputDto>,
    expected_error: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Input {
    method: String,
    url: String,
    raw_cookie_header: String,
    query_ms_token: String,
    user_agent: String,
    caller_headers: Vec<[String; 2]>,
    body: BodyReference,
    timeout_ms: u64,
    fingerprint: Fingerprint,
    ticket_guard: TicketGuard,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct BodyReference {
    wire_request_case: Option<String>,
    hex: Option<String>,
    repeat_byte_hex: Option<String>,
    count: Option<usize>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Fingerprint {
    verify_fp: String,
    fp: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TicketGuard {
    private_key: String,
    ticket: String,
    ts_sign: String,
    timestamp: u64,
    ecdh_key_hex: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SignerOutputDto {
    plan_digest: String,
    a_bogus: String,
    client_data: String,
    ree_public_key: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Expected {
    cookie_lookup: ExpectedCookieLookup,
    a_bogus_query: String,
    a_bogus_body: String,
    ticket_guard_input: ExpectedTicketGuard,
    unsigned_headers: Vec<[String; 2]>,
    body_hex: String,
    body_length: usize,
    body_sha256: String,
    plan_digest: String,
    final_url: String,
    final_headers: Vec<[String; 2]>,
    timeout_s: f64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExpectedCookieLookup {
    #[serde(rename = "msToken")]
    ms_token: String,
    bd_ticket_guard_ts_sign_id: String,
    #[serde(rename = "_bd_ticket_crypt_cookie")]
    bd_ticket_crypt_cookie: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExpectedTicketGuard {
    path: String,
    ticket: String,
    ts_sign: String,
    private_key: String,
    timestamp: u64,
    ecdh_present: bool,
    t_trust: Option<u8>,
}

#[derive(Debug)]
enum CaseFailure {
    InvalidBodyReference,
    InvalidEcdhKey,
    Plan(RequestPlanError),
}

impl CaseFailure {
    const fn code(&self) -> &'static str {
        match self {
            Self::InvalidBodyReference => "invalid_body_reference",
            Self::InvalidEcdhKey => "invalid_ecdh_key",
            Self::Plan(error) => error.code(),
        }
    }
}

/// Verifies the embedded HTTP-plan corpus against already-verified wire bodies.
///
/// # Errors
///
/// Returns [`HttpFixtureError`] when metadata, body linkage, request planning,
/// signer binding, or any frozen output differs.
pub fn verify_embedded_http_plan_corpus(
    wire_bodies: &BTreeMap<String, Vec<u8>>,
    wire_corpus_sha256: &str,
    wire_request_cases: usize,
    wire_response_cases: usize,
) -> Result<RequestPlanParityReport, HttpFixtureError> {
    verify_http_plan_corpus_source(
        EMBEDDED_HTTP_PLAN_CORPUS,
        wire_bodies,
        wire_corpus_sha256,
        wire_request_cases,
        wire_response_cases,
    )
}

fn verify_http_plan_corpus_source(
    source: &str,
    wire_bodies: &BTreeMap<String, Vec<u8>>,
    wire_corpus_sha256: &str,
    wire_request_cases: usize,
    wire_response_cases: usize,
) -> Result<RequestPlanParityReport, HttpFixtureError> {
    let corpus: HttpPlanCorpus = serde_json::from_str(source)?;
    metadata(
        sha256_hex(source.as_bytes()) == EXPECTED_HTTP_PLAN_CORPUS_SHA256,
        "corpus_sha256",
    )?;
    validate_metadata(
        &corpus,
        wire_corpus_sha256,
        wire_request_cases,
        wire_response_cases,
    )?;
    for case in &corpus.happy_cases {
        verify_happy_case(case, wire_bodies)?;
    }
    for case in &corpus.rejection_cases {
        verify_rejection_case(case, wire_bodies)?;
    }
    Ok(RequestPlanParityReport {
        schema_version: corpus.schema_version,
        corpus_id: corpus.corpus_id,
        reference_revision: corpus.reference.revision,
        happy_cases: corpus.happy_cases.len(),
        rejection_cases: corpus.rejection_cases.len(),
        corpus_sha256: sha256_hex(source.as_bytes()),
        verified: true,
    })
}

fn validate_metadata(
    corpus: &HttpPlanCorpus,
    wire_corpus_sha256: &str,
    wire_request_cases: usize,
    wire_response_cases: usize,
) -> Result<(), HttpFixtureError> {
    metadata(
        corpus.schema_version == EXPECTED_SCHEMA_VERSION,
        "schema_version",
    )?;
    metadata(corpus.corpus_id == EXPECTED_CORPUS_ID, "corpus_id")?;
    metadata(corpus.protocol == EXPECTED_PROTOCOL, "protocol")?;
    metadata(!corpus.contains_secrets, "contains_secrets")?;
    metadata(
        corpus.reference.repository == EXPECTED_REFERENCE_REPOSITORY,
        "reference.repository",
    )?;
    metadata(
        corpus.reference.revision == EXPECTED_REFERENCE_REVISION,
        "reference.revision",
    )?;
    metadata(
        corpus.scope.request_plan
            && corpus.scope.signer_inputs
            && !corpus.scope.signer_algorithms
            && !corpus.scope.http_network
            && !corpus.scope.credential_loader
            && !corpus.scope.inbox
            && !corpus.scope.websocket,
        "scope",
    )?;
    metadata(
        corpus.wire_corpus.path == EXPECTED_WIRE_CORPUS_PATH,
        "wire_corpus.path",
    )?;
    metadata(
        corpus.wire_corpus.sha256 == EXPECTED_WIRE_CORPUS_SHA256
            && corpus.wire_corpus.sha256 == wire_corpus_sha256,
        "wire_corpus.sha256",
    )?;
    metadata(
        corpus.wire_corpus.request_cases == 2
            && corpus.wire_corpus.request_cases == wire_request_cases,
        "wire_corpus.request_cases",
    )?;
    metadata(
        corpus.wire_corpus.response_cases == 31
            && corpus.wire_corpus.response_cases == wire_response_cases,
        "wire_corpus.response_cases",
    )?;
    validate_constants(&corpus.constants)?;
    validate_limits(&corpus.limits)?;
    metadata(!corpus.happy_cases.is_empty(), "happy_cases")?;
    metadata(!corpus.rejection_cases.is_empty(), "rejection_cases")
}

fn validate_constants(constants: &Constants) -> Result<(), HttpFixtureError> {
    metadata(constants.method == SEND_METHOD, "constants.method")?;
    metadata(constants.endpoint == SEND_ENDPOINT, "constants.endpoint")?;
    metadata(constants.path == SEND_PATH, "constants.path")?;
    metadata(
        constants.signing_host == SIGNING_HOST,
        "constants.signing_host",
    )?;
    metadata(
        constants.cookie_host == COOKIE_HOST,
        "constants.cookie_host",
    )?;
    metadata(
        constants.query_order == ["msToken", "a_bogus", "verifyFp", "fp"],
        "constants.query_order",
    )?;
    metadata(
        constants.ticket_header_order
            == [
                "bd-ticket-guard-client-data",
                "bd-ticket-guard-ree-public-key",
                "bd-ticket-guard-version",
                "bd-ticket-guard-web-version",
                "bd-ticket-guard-web-sign-type",
            ],
        "constants.ticket_header_order",
    )?;
    metadata(
        constants.plan_digest_algorithm == "sha256-u64be-length-framed-v1",
        "constants.plan_digest_algorithm",
    )?;
    metadata(
        constants.plan_digest_prefix_hex == EXPECTED_DIGEST_PREFIX_HEX,
        "constants.plan_digest_prefix_hex",
    )?;
    metadata(
        constants.plan_digest_field_order
            == [
                "method",
                "endpoint",
                "path",
                "signing_host",
                "cookie_host",
                "raw_cookie_header",
                "query_ms_token",
                "verify_fp",
                "fp",
                "user_agent",
                "header_count",
                "headers[name,value]*",
                "body_bytes",
                "timeout_ms",
                "ticket",
                "ts_sign",
                "private_key",
                "timestamp",
                "ecdh_present",
                "ecdh_key_bytes",
                "t_trust",
            ],
        "constants.plan_digest_field_order",
    )?;
    metadata(
        constants
            .plan_digest_frame
            .contains("uint64 big-endian byte length"),
        "constants.plan_digest_frame",
    )
}

fn validate_limits(limits: &Limits) -> Result<(), HttpFixtureError> {
    metadata(
        limits.max_body_bytes == MAX_BODY_BYTES,
        "limits.max_body_bytes",
    )?;
    metadata(
        limits.max_final_url_bytes == MAX_FINAL_URL_BYTES,
        "limits.max_final_url_bytes",
    )?;
    metadata(
        limits.max_cookie_bytes == MAX_COOKIE_HEADER_BYTES,
        "limits.max_cookie_bytes",
    )?;
    metadata(limits.max_headers == MAX_HEADER_COUNT, "limits.max_headers")?;
    metadata(
        limits.max_header_bytes == MAX_HEADER_BYTES,
        "limits.max_header_bytes",
    )?;
    metadata(
        limits.max_user_agent_bytes == MAX_USER_AGENT_BYTES,
        "limits.max_user_agent_bytes",
    )?;
    metadata(
        limits.max_field_bytes == MAX_TICKET_FIELD_BYTES,
        "limits.max_field_bytes",
    )?;
    metadata(limits.min_timeout_ms == 1, "limits.min_timeout_ms")?;
    metadata(
        limits.max_timeout_ms == MAX_TIMEOUT_MS,
        "limits.max_timeout_ms",
    )
}

fn metadata(condition: bool, field: &'static str) -> Result<(), HttpFixtureError> {
    if condition {
        Ok(())
    } else {
        Err(HttpFixtureError::InvalidMetadata(format!(
            "{field} does not match the frozen contract"
        )))
    }
}

fn verify_happy_case(
    case: &HappyCase,
    wire_bodies: &BTreeMap<String, Vec<u8>>,
) -> Result<(), HttpFixtureError> {
    let unsigned = prepare_from_dto(&case.input, wire_bodies)
        .map_err(|failure| map_case_failure(&case.id, failure))?;
    compare_unsigned(&case.id, &unsigned, &case.expected)?;
    compare(
        &case.id,
        "signer_outputs.plan_digest",
        &case.expected.plan_digest,
        &case.signer_outputs.plan_digest,
    )?;
    let outputs = signer_outputs_from_dto(&case.signer_outputs)
        .map_err(|failure| map_case_failure(&case.id, failure))?;
    let plan = finalize_send_request(unsigned, outputs).map_err(|source| {
        HttpFixtureError::RequestPlan {
            case: case.id.clone(),
            code: source.code(),
            source,
        }
    })?;
    compare(
        &case.id,
        "final_url",
        case.expected.final_url.as_str(),
        plan.final_url(),
    )?;
    compare(
        &case.id,
        "final_headers",
        &case.expected.final_headers,
        &header_pairs(plan.headers()),
    )?;
    compare(
        &case.id,
        "final_body",
        &case.expected.body_hex,
        &hex_encode(plan.body()),
    )?;
    compare(
        &case.id,
        "final_body_length",
        &case.expected.body_length,
        &plan.body_length(),
    )?;
    compare(
        &case.id,
        "final_body_sha256",
        &case.expected.body_sha256,
        &plan.body_sha256_hex(),
    )?;
    compare(
        &case.id,
        "final_plan_digest",
        &case.expected.plan_digest,
        &plan.plan_digest_hex(),
    )?;
    if (plan.timeout_seconds() - case.expected.timeout_s).abs() > f64::EPSILON {
        return Err(HttpFixtureError::Mismatch {
            case: case.id.clone(),
            field: "timeout_s",
            expected: format!("{:?}", case.expected.timeout_s),
            actual: format!("{:?}", plan.timeout_seconds()),
        });
    }
    Ok(())
}

fn compare_unsigned(
    case: &str,
    unsigned: &UnsignedRequestPlan,
    expected: &Expected,
) -> Result<(), HttpFixtureError> {
    compare_cookie_and_signer_inputs(case, unsigned, expected)?;
    compare_unsigned_request(case, unsigned, expected)
}

fn compare_cookie_and_signer_inputs(
    case: &str,
    unsigned: &UnsignedRequestPlan,
    expected: &Expected,
) -> Result<(), HttpFixtureError> {
    let cookie = unsigned.cookie_lookup();
    compare(
        case,
        "cookie.msToken",
        &expected.cookie_lookup.ms_token,
        &cookie.ms_token,
    )?;
    compare(
        case,
        "cookie.bd_ticket_guard_ts_sign_id",
        &expected.cookie_lookup.bd_ticket_guard_ts_sign_id,
        &cookie.bd_ticket_guard_ts_sign_id,
    )?;
    compare(
        case,
        "cookie._bd_ticket_crypt_cookie",
        &expected.cookie_lookup.bd_ticket_crypt_cookie,
        &cookie.bd_ticket_crypt_cookie,
    )?;
    compare(
        case,
        "a_bogus_query",
        &expected.a_bogus_query,
        &unsigned.signer_requests().a_bogus.query,
    )?;
    compare(
        case,
        "a_bogus_body",
        expected.a_bogus_body.as_str(),
        unsigned.signer_requests().a_bogus.body,
    )?;
    let guard = &unsigned.signer_requests().ticket_guard;
    compare(
        case,
        "guard.path",
        expected.ticket_guard_input.path.as_str(),
        guard.path,
    )?;
    compare(
        case,
        "guard.ticket",
        &expected.ticket_guard_input.ticket,
        &guard.ticket,
    )?;
    compare(
        case,
        "guard.ts_sign",
        &expected.ticket_guard_input.ts_sign,
        &guard.ts_sign,
    )?;
    compare(
        case,
        "guard.private_key",
        &expected.ticket_guard_input.private_key,
        &guard.private_key,
    )?;
    compare(
        case,
        "guard.timestamp",
        &expected.ticket_guard_input.timestamp,
        &guard.timestamp,
    )?;
    compare(
        case,
        "guard.ecdh_present",
        &expected.ticket_guard_input.ecdh_present,
        &guard.ecdh_key.is_some(),
    )?;
    compare(
        case,
        "guard.t_trust",
        &expected.ticket_guard_input.t_trust,
        &guard.t_trust,
    )
}

fn compare_unsigned_request(
    case: &str,
    unsigned: &UnsignedRequestPlan,
    expected: &Expected,
) -> Result<(), HttpFixtureError> {
    compare(
        case,
        "unsigned_headers",
        &expected.unsigned_headers,
        &header_pairs(unsigned.unsigned_headers()),
    )?;
    compare(
        case,
        "body_hex",
        &expected.body_hex,
        &hex_encode(unsigned.body()),
    )?;
    compare(
        case,
        "body_length",
        &expected.body_length,
        &unsigned.body_length(),
    )?;
    compare(
        case,
        "body_sha256",
        &expected.body_sha256,
        &unsigned.body_sha256_hex(),
    )?;
    compare(
        case,
        "plan_digest",
        &expected.plan_digest,
        &unsigned.plan_digest_hex(),
    )
}

fn verify_rejection_case(
    case: &RejectionCase,
    wire_bodies: &BTreeMap<String, Vec<u8>>,
) -> Result<(), HttpFixtureError> {
    let failure = match case.stage.as_str() {
        "prepare" => match prepare_from_dto(&case.input, wire_bodies) {
            Ok(_) => {
                return Err(HttpFixtureError::Mismatch {
                    case: case.id.clone(),
                    field: "expected_error",
                    expected: case.expected_error.clone(),
                    actual: "success".to_owned(),
                });
            }
            Err(failure) => failure,
        },
        "finalize" => {
            let unsigned = prepare_from_dto(&case.input, wire_bodies)
                .map_err(|failure| map_case_failure(&case.id, failure))?;
            let dto = case.signer_outputs.as_ref().ok_or_else(|| {
                HttpFixtureError::InvalidMetadata(format!(
                    "finalize case {:?} has no signer_outputs",
                    case.id
                ))
            })?;
            let outputs = signer_outputs_from_dto(dto)
                .map_err(|failure| map_case_failure(&case.id, failure))?;
            match finalize_send_request(unsigned, outputs) {
                Ok(_) => {
                    return Err(HttpFixtureError::Mismatch {
                        case: case.id.clone(),
                        field: "expected_error",
                        expected: case.expected_error.clone(),
                        actual: "success".to_owned(),
                    });
                }
                Err(error) => CaseFailure::Plan(error),
            }
        }
        _ => {
            return Err(HttpFixtureError::InvalidMetadata(format!(
                "case {:?} has unsupported stage {:?}",
                case.id, case.stage
            )));
        }
    };
    compare(
        &case.id,
        "expected_error",
        case.expected_error.as_str(),
        failure.code(),
    )
}

fn prepare_from_dto(
    input: &Input,
    wire_bodies: &BTreeMap<String, Vec<u8>>,
) -> Result<UnsignedRequestPlan, CaseFailure> {
    let body = resolve_body(&input.body, wire_bodies)?;
    let ecdh_key = decode_optional_ecdh_key(input.ticket_guard.ecdh_key_hex.as_deref())?;
    let headers = input
        .caller_headers
        .iter()
        .map(|pair| OrderedHeader::new(pair[0].clone(), pair[1].clone()))
        .collect::<Vec<_>>();
    prepare_send_request(&SendHttpPlanInput {
        method: &input.method,
        url: &input.url,
        raw_cookie_header: &input.raw_cookie_header,
        query_ms_token: &input.query_ms_token,
        user_agent: &input.user_agent,
        caller_headers: &headers,
        body: &body,
        timeout_ms: input.timeout_ms,
        fingerprint: FingerprintInput {
            verify_fp: &input.fingerprint.verify_fp,
            fp: &input.fingerprint.fp,
        },
        ticket_guard: TicketGuardCredential {
            private_key: &input.ticket_guard.private_key,
            ticket: &input.ticket_guard.ticket,
            ts_sign: &input.ticket_guard.ts_sign,
            timestamp: input.ticket_guard.timestamp,
            ecdh_key: ecdh_key.as_deref(),
        },
    })
    .map_err(CaseFailure::Plan)
}

fn resolve_body(
    reference: &BodyReference,
    wire_bodies: &BTreeMap<String, Vec<u8>>,
) -> Result<Vec<u8>, CaseFailure> {
    let variants = usize::from(reference.wire_request_case.is_some())
        + usize::from(reference.hex.is_some())
        + usize::from(reference.repeat_byte_hex.is_some() || reference.count.is_some());
    if variants != 1 {
        return Err(CaseFailure::InvalidBodyReference);
    }
    if let Some(id) = &reference.wire_request_case {
        return wire_bodies
            .get(id)
            .cloned()
            .ok_or(CaseFailure::InvalidBodyReference);
    }
    if let Some(value) = &reference.hex {
        return decode_hex(value).ok_or(CaseFailure::InvalidBodyReference);
    }
    let byte_hex = reference
        .repeat_byte_hex
        .as_deref()
        .ok_or(CaseFailure::InvalidBodyReference)?;
    let count = reference.count.ok_or(CaseFailure::InvalidBodyReference)?;
    if count > MAX_BODY_BYTES {
        return Err(CaseFailure::Plan(RequestPlanError::BodyTooLarge {
            actual: count,
            maximum: MAX_BODY_BYTES,
        }));
    }
    let decoded = decode_hex(byte_hex).ok_or(CaseFailure::InvalidBodyReference)?;
    if decoded.len() != 1 {
        return Err(CaseFailure::InvalidBodyReference);
    }
    Ok(vec![decoded[0]; count])
}

fn decode_optional_ecdh_key(value: Option<&str>) -> Result<Option<Vec<u8>>, CaseFailure> {
    match value {
        None | Some("") => Ok(None),
        Some(value) => decode_hex(value)
            .map(Some)
            .ok_or(CaseFailure::InvalidEcdhKey),
    }
}

fn signer_outputs_from_dto(dto: &SignerOutputDto) -> Result<SignerOutputs, CaseFailure> {
    let plan_digest = parse_plan_digest(&dto.plan_digest)
        .ok_or(CaseFailure::Plan(RequestPlanError::PlanDigestMismatch))?;
    Ok(SignerOutputs {
        plan_digest,
        a_bogus: dto.a_bogus.clone(),
        client_data: dto.client_data.clone(),
        ree_public_key: dto.ree_public_key.clone(),
    })
}

fn map_case_failure(case: &str, failure: CaseFailure) -> HttpFixtureError {
    match failure {
        CaseFailure::InvalidBodyReference => HttpFixtureError::InvalidBodyReference {
            case: case.to_owned(),
        },
        CaseFailure::InvalidEcdhKey => HttpFixtureError::InvalidMetadata(format!(
            "HTTP RequestPlan case {case:?} has invalid ECDH key hex"
        )),
        CaseFailure::Plan(source) => HttpFixtureError::RequestPlan {
            case: case.to_owned(),
            code: source.code(),
            source,
        },
    }
}

fn compare<T>(
    case: &str,
    field: &'static str,
    expected: &T,
    actual: &T,
) -> Result<(), HttpFixtureError>
where
    T: std::fmt::Debug + PartialEq + ?Sized,
{
    if expected == actual {
        Ok(())
    } else {
        Err(HttpFixtureError::Mismatch {
            case: case.to_owned(),
            field,
            expected: format!("{expected:?}"),
            actual: format!("{actual:?}"),
        })
    }
}

fn header_pairs(headers: &[OrderedHeader]) -> Vec<[String; 2]> {
    headers
        .iter()
        .map(|header| [header.name.clone(), header.value.clone()])
        .collect()
}

fn decode_hex(value: &str) -> Option<Vec<u8>> {
    if !value.len().is_multiple_of(2) {
        return None;
    }
    value
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| Some((hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?))
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

    fn wire_bodies() -> BTreeMap<String, Vec<u8>> {
        let source = include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../protocol-fixtures/douyin_pc_im_v1.json"
        ));
        let value: serde_json::Value = serde_json::from_str(source).expect("wire JSON must parse");
        value["request_cases"]
            .as_array()
            .expect("wire requests must be an array")
            .iter()
            .map(|case| {
                let id = case["id"]
                    .as_str()
                    .expect("wire id must be text")
                    .to_owned();
                let hex = case["expected"]["body_hex"]
                    .as_str()
                    .expect("wire body must be hex");
                (id, decode_hex(hex).expect("wire body hex must decode"))
            })
            .collect()
    }

    #[test]
    fn embedded_http_plan_corpus_verifies_all_cases() {
        let report =
            verify_embedded_http_plan_corpus(&wire_bodies(), EXPECTED_WIRE_CORPUS_SHA256, 2, 31)
                .expect("HTTP RequestPlan corpus must verify");
        assert_eq!(report.schema_version, 1);
        assert_eq!(report.corpus_id, EXPECTED_CORPUS_ID);
        assert_eq!(report.happy_cases, 2);
        assert_eq!(report.rejection_cases, 30);
        assert_eq!(report.corpus_sha256, EXPECTED_HTTP_PLAN_CORPUS_SHA256);
        assert!(report.verified);
    }

    #[test]
    fn metadata_rejects_wire_digest_drift() {
        assert!(matches!(
            verify_http_plan_corpus_source(
                EMBEDDED_HTTP_PLAN_CORPUS,
                &wire_bodies(),
                "0000000000000000000000000000000000000000000000000000000000000000",
                2,
                31,
            ),
            Err(HttpFixtureError::InvalidMetadata(_))
        ));
    }

    #[test]
    fn corpus_rejects_changed_expected_final_url() {
        let tampered =
            EMBEDDED_HTTP_PLAN_CORPUS.replacen("a_bogus=ABOGUS_A-._~", "a_bogus=WRONG_A-._~", 1);
        assert!(matches!(
            verify_http_plan_corpus_source(
                &tampered,
                &wire_bodies(),
                EXPECTED_WIRE_CORPUS_SHA256,
                2,
                31,
            ),
            Err(HttpFixtureError::InvalidMetadata(_)
                | HttpFixtureError::Mismatch { .. }
                | HttpFixtureError::RequestPlan { .. })
        ));
    }

    #[test]
    fn ecdh_hex_boundary_normalizes_empty_and_rejects_malformed_values() {
        assert_eq!(
            decode_optional_ecdh_key(None).expect("absent key must remain absent"),
            None
        );
        assert_eq!(
            decode_optional_ecdh_key(Some("")).expect("empty key must normalize"),
            None
        );
        assert_eq!(
            decode_optional_ecdh_key(Some("00fF")).expect("valid hex must decode"),
            Some(vec![0, 255])
        );
        assert!(matches!(
            decode_optional_ecdh_key(Some("0")),
            Err(CaseFailure::InvalidEcdhKey)
        ));
        assert!(matches!(
            decode_optional_ecdh_key(Some("gg")),
            Err(CaseFailure::InvalidEcdhKey)
        ));
    }
}
