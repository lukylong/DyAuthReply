//! Pure, offline planning for the frozen Douyin PC IM send HTTP request.
//!
//! The module intentionally has no HTTP client, clock, random source, account
//! loader, filesystem access, or signing implementation.  It prepares the exact
//! inputs a signer needs and only finalizes a request when the signer binds its
//! canned outputs to the same versioned plan digest.

use std::{collections::BTreeSet, fmt, fmt::Write as _};

use sha2::{Digest, Sha256};
use thiserror::Error;

pub const SEND_METHOD: &str = "POST";
pub const SEND_ENDPOINT: &str = "https://imapi.douyin.com/v1/message/send";
pub const SEND_PATH: &str = "/v1/message/send";
pub const SIGNING_HOST: &str = "www.douyin.com";
pub const COOKIE_HOST: &str = "www.douyin.com";

pub const MAX_BODY_BYTES: usize = 2_097_152;
pub const MAX_FINAL_URL_BYTES: usize = 16_384;
pub const MAX_COOKIE_HEADER_BYTES: usize = 65_536;
pub const MAX_HEADER_BYTES: usize = 65_536;
pub const MAX_HEADER_COUNT: usize = 64;
pub const MAX_HEADER_NAME_BYTES: usize = 64;
pub const MAX_HEADER_VALUE_BYTES: usize = 8_192;
pub const MAX_USER_AGENT_BYTES: usize = 2_048;
pub const MAX_QUERY_VALUE_BYTES: usize = 8_192;
pub const MAX_A_BOGUS_BYTES: usize = 8_192;
pub const MAX_TICKET_FIELD_BYTES: usize = 8_192;
pub const MAX_CLIENT_DATA_BYTES: usize = 8_192;
pub const MAX_REE_PUBLIC_KEY_BYTES: usize = 8_192;
pub const MAX_TIMEOUT_MS: u64 = 120_000;

const PLAN_DIGEST_DOMAIN: &[u8] = b"DY_HTTP_PLAN_V1\0";
const GUARD_REQ_CONTENT: &str = "ticket,path,timestamp";
const FINAL_GUARD_HEADER_COUNT: usize = 5;

/// One logical HTTP header.  Values are deliberately redacted from `Debug`.
#[derive(Clone, Eq, PartialEq)]
pub struct OrderedHeader {
    pub name: String,
    pub value: String,
}

impl OrderedHeader {
    #[must_use]
    pub fn new(name: impl Into<String>, value: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            value: value.into(),
        }
    }
}

impl fmt::Debug for OrderedHeader {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("OrderedHeader")
            .field("name", &self.name)
            .field("value", &Redacted(self.value.len()))
            .finish()
    }
}

/// Explicit device fingerprint values supplied by the already-loaded account.
#[derive(Clone, Copy)]
pub struct FingerprintInput<'a> {
    pub verify_fp: &'a str,
    pub fp: &'a str,
}

/// Ticket-guard material supplied by the already-loaded account.
#[derive(Clone, Copy)]
pub struct TicketGuardCredential<'a> {
    pub private_key: &'a str,
    pub ticket: &'a str,
    pub ts_sign: &'a str,
    pub timestamp: u64,
    pub ecdh_key: Option<&'a [u8]>,
}

/// Fully explicit input to the pure prepare stage.
#[derive(Clone, Copy)]
pub struct SendHttpPlanInput<'a> {
    pub method: &'a str,
    pub url: &'a str,
    pub raw_cookie_header: &'a str,
    pub query_ms_token: &'a str,
    pub user_agent: &'a str,
    pub caller_headers: &'a [OrderedHeader],
    pub body: &'a [u8],
    pub timeout_ms: u64,
    pub fingerprint: FingerprintInput<'a>,
    pub ticket_guard: TicketGuardCredential<'a>,
}

impl fmt::Debug for SendHttpPlanInput<'_> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SendHttpPlanInput")
            .field("method", &self.method)
            .field("url", &self.url)
            .field("cookie", &Redacted(self.raw_cookie_header.len()))
            .field("query_ms_token", &Redacted(self.query_ms_token.len()))
            .field("user_agent", &Redacted(self.user_agent.len()))
            .field("caller_header_count", &self.caller_headers.len())
            .field("body", &Redacted(self.body.len()))
            .field("timeout_ms", &self.timeout_ms)
            .field("fingerprint", &"<redacted>")
            .field("ticket_guard", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct CookieLookup {
    pub ms_token: String,
    pub bd_ticket_guard_ts_sign_id: String,
    pub bd_ticket_crypt_cookie: String,
}

impl fmt::Debug for CookieLookup {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CookieLookup")
            .field("ms_token", &Redacted(self.ms_token.len()))
            .field(
                "bd_ticket_guard_ts_sign_id",
                &Redacted(self.bd_ticket_guard_ts_sign_id.len()),
            )
            .field(
                "bd_ticket_crypt_cookie",
                &Redacted(self.bd_ticket_crypt_cookie.len()),
            )
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct ABogusSigningInput {
    pub host: &'static str,
    pub query: String,
    pub body: &'static str,
}

impl fmt::Debug for ABogusSigningInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ABogusSigningInput")
            .field("host", &self.host)
            .field("query", &Redacted(self.query.len()))
            .field("body", &Redacted(self.body.len()))
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TicketGuardMode {
    Ecdsa,
    Hmac,
}

#[derive(Clone, Eq, PartialEq)]
pub struct TicketGuardSigningInput {
    pub path: &'static str,
    pub ticket: String,
    pub ts_sign: String,
    pub private_key: String,
    pub timestamp: u64,
    pub ecdh_key: Option<Vec<u8>>,
    pub t_trust: Option<u8>,
    pub req_content: &'static str,
    pub sign_payload: String,
    pub mode: TicketGuardMode,
}

impl fmt::Debug for TicketGuardSigningInput {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("TicketGuardSigningInput")
            .field("path", &self.path)
            .field("ticket", &Redacted(self.ticket.len()))
            .field("ts_sign", &Redacted(self.ts_sign.len()))
            .field("private_key", &Redacted(self.private_key.len()))
            .field("timestamp", &self.timestamp)
            .field(
                "ecdh_key",
                &self.ecdh_key.as_ref().map(|value| Redacted(value.len())),
            )
            .field("t_trust", &self.t_trust)
            .field("req_content", &self.req_content)
            .field("sign_payload", &Redacted(self.sign_payload.len()))
            .field("mode", &self.mode)
            .finish()
    }
}

/// Signer work frozen by the prepare stage.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SignerRequests {
    pub a_bogus: ABogusSigningInput,
    pub ticket_guard: TicketGuardSigningInput,
}

/// Prepared request data.  It contains secrets, so its diagnostics are redacted.
#[derive(Clone, Eq, PartialEq)]
pub struct UnsignedRequestPlan {
    method: &'static str,
    endpoint: &'static str,
    signing_host: &'static str,
    cookie_host: &'static str,
    cookie_lookup: CookieLookup,
    query_ms_token: String,
    unsigned_headers: Vec<OrderedHeader>,
    body: Vec<u8>,
    body_sha256: [u8; 32],
    timeout_ms: u64,
    user_agent_input: String,
    fingerprint_verify_fp: String,
    fingerprint_fp: String,
    signer_requests: SignerRequests,
    plan_digest: [u8; 32],
}

impl fmt::Debug for UnsignedRequestPlan {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("UnsignedRequestPlan")
            .field("method", &self.method)
            .field("endpoint", &self.endpoint)
            .field("signing_host", &self.signing_host)
            .field("cookie_host", &self.cookie_host)
            .field("cookie_lookup", &self.cookie_lookup)
            .field("query_ms_token", &"<redacted>")
            .field("unsigned_header_count", &self.unsigned_headers.len())
            .field("body", &Redacted(self.body.len()))
            .field("body_sha256", &"<redacted>")
            .field("timeout_ms", &self.timeout_ms)
            .field("user_agent_input", &"<redacted>")
            .field("fingerprint_verify_fp", &"<redacted>")
            .field("fingerprint_fp", &"<redacted>")
            .field("signer_requests", &self.signer_requests)
            .field("plan_digest", &"<redacted>")
            .finish()
    }
}

impl UnsignedRequestPlan {
    #[must_use]
    pub const fn method(&self) -> &'static str {
        self.method
    }

    #[must_use]
    pub const fn endpoint(&self) -> &'static str {
        self.endpoint
    }

    #[must_use]
    pub const fn signing_host(&self) -> &'static str {
        self.signing_host
    }

    #[must_use]
    pub const fn cookie_host(&self) -> &'static str {
        self.cookie_host
    }

    #[must_use]
    pub fn cookie_lookup(&self) -> &CookieLookup {
        &self.cookie_lookup
    }

    #[must_use]
    pub fn unsigned_headers(&self) -> &[OrderedHeader] {
        &self.unsigned_headers
    }

    #[must_use]
    pub fn body(&self) -> &[u8] {
        &self.body
    }

    #[must_use]
    pub const fn body_length(&self) -> usize {
        self.body.len()
    }

    #[must_use]
    pub fn body_sha256_hex(&self) -> String {
        hex_encode(&self.body_sha256)
    }

    #[must_use]
    pub const fn timeout_ms(&self) -> u64 {
        self.timeout_ms
    }

    #[must_use]
    pub fn timeout_seconds(&self) -> f64 {
        std::time::Duration::from_millis(self.timeout_ms).as_secs_f64()
    }

    #[must_use]
    pub fn signer_requests(&self) -> &SignerRequests {
        &self.signer_requests
    }

    #[must_use]
    pub const fn plan_digest(&self) -> [u8; 32] {
        self.plan_digest
    }

    #[must_use]
    pub fn plan_digest_hex(&self) -> String {
        hex_encode(&self.plan_digest)
    }
}

/// Canned outputs from a signer.  No signing algorithm is implemented here.
#[derive(Clone, Eq, PartialEq)]
pub struct SignerOutputs {
    pub plan_digest: [u8; 32],
    pub a_bogus: String,
    pub client_data: String,
    pub ree_public_key: String,
}

impl fmt::Debug for SignerOutputs {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("SignerOutputs")
            .field("plan_digest", &"<redacted>")
            .field("a_bogus", &Redacted(self.a_bogus.len()))
            .field("client_data", &Redacted(self.client_data.len()))
            .field("ree_public_key", &Redacted(self.ree_public_key.len()))
            .finish()
    }
}

/// Opaque final HTTP request plan.  This type does not perform the request.
#[derive(Clone, Eq, PartialEq)]
pub struct RequestPlan {
    method: &'static str,
    final_url: String,
    headers: Vec<OrderedHeader>,
    body: Vec<u8>,
    body_sha256: [u8; 32],
    timeout_ms: u64,
    plan_digest: [u8; 32],
}

impl fmt::Debug for RequestPlan {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RequestPlan")
            .field("method", &self.method)
            .field("final_url", &Redacted(self.final_url.len()))
            .field("header_count", &self.headers.len())
            .field("body", &Redacted(self.body.len()))
            .field("body_sha256", &"<redacted>")
            .field("timeout_ms", &self.timeout_ms)
            .field("plan_digest", &"<redacted>")
            .finish()
    }
}

impl RequestPlan {
    #[must_use]
    pub const fn method(&self) -> &'static str {
        self.method
    }

    #[must_use]
    pub fn final_url(&self) -> &str {
        &self.final_url
    }

    #[must_use]
    pub fn headers(&self) -> &[OrderedHeader] {
        &self.headers
    }

    #[must_use]
    pub fn body(&self) -> &[u8] {
        &self.body
    }

    #[must_use]
    pub const fn body_length(&self) -> usize {
        self.body.len()
    }

    #[must_use]
    pub fn body_sha256_hex(&self) -> String {
        hex_encode(&self.body_sha256)
    }

    #[must_use]
    pub const fn timeout_ms(&self) -> u64 {
        self.timeout_ms
    }

    #[must_use]
    pub fn timeout_seconds(&self) -> f64 {
        std::time::Duration::from_millis(self.timeout_ms).as_secs_f64()
    }

    #[must_use]
    pub fn plan_digest_hex(&self) -> String {
        hex_encode(&self.plan_digest)
    }
}

#[derive(Debug, Error, Eq, PartialEq)]
pub enum RequestPlanError {
    #[error("unsupported HTTP method")]
    UnsupportedMethod,
    #[error("unsupported HTTP endpoint")]
    UnsupportedEndpoint,
    #[error("raw Cookie header is missing")]
    MissingCookieHeader,
    #[error("query msToken is missing")]
    MissingMsToken,
    #[error("verifyFp/fp fingerprint is missing")]
    MissingVerifyFp,
    #[error("ticket-guard private key is missing")]
    MissingPrivateKey,
    #[error("ticket-guard ticket is missing")]
    MissingTicket,
    #[error("ticket-guard ts_sign is missing")]
    MissingTsSign,
    #[error("ticket-guard credential and Cookie session differ")]
    TicketSessionMismatch,
    #[error("field {field} contains a forbidden control character")]
    InvalidControlCharacter { field: &'static str },
    #[error("header name is not a valid ASCII token")]
    InvalidHeaderName,
    #[error("caller header is duplicated after case-folding")]
    DuplicateHeader,
    #[error("field {field} is too large: {actual} bytes, maximum {maximum}")]
    FieldTooLarge {
        field: &'static str,
        actual: usize,
        maximum: usize,
    },
    #[error("request body is too large: {actual} bytes, maximum {maximum}")]
    BodyTooLarge { actual: usize, maximum: usize },
    #[error("request contains too many headers: {actual}, maximum {maximum}")]
    TooManyHeaders { actual: usize, maximum: usize },
    #[error("request timeout is outside the supported range")]
    InvalidTimeout,
    #[error("ticket-guard timestamp is outside the supported range")]
    InvalidTimestamp,
    #[error("final request URL is too large: {actual} bytes, maximum {maximum}")]
    UrlTooLarge { actual: usize, maximum: usize },
    #[error("signer outputs were produced for another request plan")]
    PlanDigestMismatch,
    #[error("signer output {field} is empty or invalid")]
    InvalidSignerOutput { field: &'static str },
}

impl RequestPlanError {
    #[must_use]
    pub const fn code(&self) -> &'static str {
        match self {
            Self::UnsupportedMethod => "unsupported_method",
            Self::UnsupportedEndpoint => "unsupported_endpoint",
            Self::MissingCookieHeader => "missing_cookie_header",
            Self::MissingMsToken => "missing_ms_token",
            Self::MissingVerifyFp => "missing_verify_fp",
            Self::MissingPrivateKey => "missing_private_key",
            Self::MissingTicket => "missing_ticket",
            Self::MissingTsSign => "missing_ts_sign",
            Self::TicketSessionMismatch => "ticket_session_mismatch",
            Self::InvalidControlCharacter { .. } => "invalid_control_character",
            Self::InvalidHeaderName => "invalid_header_name",
            Self::DuplicateHeader => "duplicate_header",
            Self::FieldTooLarge { .. } => "field_too_large",
            Self::BodyTooLarge { .. } => "body_too_large",
            Self::TooManyHeaders { .. } => "too_many_headers",
            Self::InvalidTimeout => "invalid_timeout",
            Self::InvalidTimestamp => "invalid_timestamp",
            Self::UrlTooLarge { .. } => "url_too_large",
            Self::PlanDigestMismatch => "plan_digest_mismatch",
            Self::InvalidSignerOutput { .. } => "invalid_signer_output",
        }
    }
}

/// Validates and freezes the exact inputs for one reference PC IM send.
///
/// # Errors
///
/// Returns a typed, stable error when any fixed boundary, credential,
/// fingerprint, body, timeout, or logical header violates the frozen contract.
pub fn prepare_send_request(
    input: &SendHttpPlanInput<'_>,
) -> Result<UnsignedRequestPlan, RequestPlanError> {
    validate_request_boundary(input)?;
    let cookie_lookup = parse_cookie_lookup(input.raw_cookie_header);
    validate_credentials(input, &cookie_lookup)?;
    let unsigned_headers = merge_unsigned_headers(input)?;
    validate_header_collection(&unsigned_headers, FINAL_GUARD_HEADER_COUNT)?;
    let signer_requests = build_signer_requests(input, &cookie_lookup)?;
    let body_sha256: [u8; 32] = Sha256::digest(input.body).into();

    let mut plan = UnsignedRequestPlan {
        method: SEND_METHOD,
        endpoint: SEND_ENDPOINT,
        signing_host: SIGNING_HOST,
        cookie_host: COOKIE_HOST,
        cookie_lookup,
        query_ms_token: input.query_ms_token.to_owned(),
        unsigned_headers,
        body: input.body.to_vec(),
        body_sha256,
        timeout_ms: input.timeout_ms,
        user_agent_input: input.user_agent.to_owned(),
        fingerprint_verify_fp: input.fingerprint.verify_fp.to_owned(),
        fingerprint_fp: input.fingerprint.fp.to_owned(),
        signer_requests,
        plan_digest: [0; 32],
    };
    plan.plan_digest = compute_plan_digest(&plan);
    Ok(plan)
}

fn validate_request_boundary(input: &SendHttpPlanInput<'_>) -> Result<(), RequestPlanError> {
    let maximum_caller_headers = MAX_HEADER_COUNT - FINAL_GUARD_HEADER_COUNT;
    if input.caller_headers.len() > maximum_caller_headers {
        return Err(RequestPlanError::TooManyHeaders {
            actual: input.caller_headers.len(),
            maximum: maximum_caller_headers,
        });
    }
    validate_text_control("method", input.method)?;
    validate_text_control("url", input.url)?;
    if input.method != SEND_METHOD {
        return Err(RequestPlanError::UnsupportedMethod);
    }
    if input.url != SEND_ENDPOINT {
        return Err(RequestPlanError::UnsupportedEndpoint);
    }
    if input.raw_cookie_header.is_empty() {
        return Err(RequestPlanError::MissingCookieHeader);
    }
    validate_bounded_text(
        "raw_cookie_header",
        input.raw_cookie_header,
        MAX_COOKIE_HEADER_BYTES,
    )?;
    validate_required_bounded("user_agent", input.user_agent, MAX_USER_AGENT_BYTES, None)?;
    if input.body.len() > MAX_BODY_BYTES {
        return Err(RequestPlanError::BodyTooLarge {
            actual: input.body.len(),
            maximum: MAX_BODY_BYTES,
        });
    }
    if input.timeout_ms == 0 || input.timeout_ms > MAX_TIMEOUT_MS {
        return Err(RequestPlanError::InvalidTimeout);
    }
    if input.ticket_guard.timestamp == 0 || input.ticket_guard.timestamp > i64::MAX as u64 {
        return Err(RequestPlanError::InvalidTimestamp);
    }
    Ok(())
}

fn validate_credentials(
    input: &SendHttpPlanInput<'_>,
    cookie_lookup: &CookieLookup,
) -> Result<(), RequestPlanError> {
    if input.query_ms_token.is_empty() {
        return Err(RequestPlanError::MissingMsToken);
    }
    validate_bounded_text("msToken", input.query_ms_token, MAX_QUERY_VALUE_BYTES)?;

    if input.fingerprint.verify_fp.is_empty() || input.fingerprint.fp.is_empty() {
        return Err(RequestPlanError::MissingVerifyFp);
    }
    validate_bounded_text(
        "verifyFp",
        input.fingerprint.verify_fp,
        MAX_QUERY_VALUE_BYTES,
    )?;
    validate_bounded_text("fp", input.fingerprint.fp, MAX_QUERY_VALUE_BYTES)?;
    validate_required_bounded(
        "private_key",
        input.ticket_guard.private_key,
        MAX_TICKET_FIELD_BYTES,
        Some(RequestPlanError::MissingPrivateKey),
    )?;
    validate_required_bounded(
        "ticket",
        input.ticket_guard.ticket,
        MAX_TICKET_FIELD_BYTES,
        Some(RequestPlanError::MissingTicket),
    )?;
    validate_required_bounded(
        "ts_sign",
        input.ticket_guard.ts_sign,
        MAX_TICKET_FIELD_BYTES,
        Some(RequestPlanError::MissingTsSign),
    )?;
    if let Some(ecdh_key) = normalized_ecdh_key(input) {
        if ecdh_key.len() > MAX_TICKET_FIELD_BYTES {
            return Err(RequestPlanError::FieldTooLarge {
                field: "ecdh_key",
                actual: ecdh_key.len(),
                maximum: MAX_TICKET_FIELD_BYTES,
            });
        }
    }

    if !cookie_lookup.bd_ticket_guard_ts_sign_id.is_empty()
        && !input
            .ticket_guard
            .ts_sign
            .starts_with(&cookie_lookup.bd_ticket_guard_ts_sign_id)
    {
        return Err(RequestPlanError::TicketSessionMismatch);
    }
    Ok(())
}

fn build_signer_requests(
    input: &SendHttpPlanInput<'_>,
    cookie_lookup: &CookieLookup,
) -> Result<SignerRequests, RequestPlanError> {
    let a_bogus_query = format!("msToken={}", percent_encode_rfc3986(input.query_ms_token));
    let ecdh_key = normalized_ecdh_key(input);
    let guard_mode = if ecdh_key.is_some() {
        TicketGuardMode::Hmac
    } else {
        TicketGuardMode::Ecdsa
    };
    let t_trust = (!cookie_lookup.bd_ticket_crypt_cookie.is_empty()).then_some(1);
    let sign_payload = format!(
        "ticket={}&path={SEND_PATH}&timestamp={}",
        input.ticket_guard.ticket, input.ticket_guard.timestamp
    );
    validate_bounded_text(
        "guard_sign_payload",
        &sign_payload,
        MAX_TICKET_FIELD_BYTES * 2,
    )?;

    Ok(SignerRequests {
        a_bogus: ABogusSigningInput {
            host: SIGNING_HOST,
            query: a_bogus_query,
            body: "",
        },
        ticket_guard: TicketGuardSigningInput {
            path: SEND_PATH,
            ticket: input.ticket_guard.ticket.to_owned(),
            ts_sign: input.ticket_guard.ts_sign.to_owned(),
            private_key: input.ticket_guard.private_key.to_owned(),
            timestamp: input.ticket_guard.timestamp,
            ecdh_key: ecdh_key.map(<[u8]>::to_vec),
            t_trust,
            req_content: GUARD_REQ_CONTENT,
            sign_payload,
            mode: guard_mode,
        },
    })
}

fn normalized_ecdh_key<'a>(input: &'a SendHttpPlanInput<'_>) -> Option<&'a [u8]> {
    input.ticket_guard.ecdh_key.filter(|key| !key.is_empty())
}

/// Finalizes a prepared plan only with outputs bound to its digest.
///
/// # Errors
///
/// Returns [`RequestPlanError::PlanDigestMismatch`] when signer output is
/// reused across plans, or a typed validation error for malformed output.
pub fn finalize_send_request(
    unsigned: UnsignedRequestPlan,
    outputs: SignerOutputs,
) -> Result<RequestPlan, RequestPlanError> {
    if unsigned.plan_digest != outputs.plan_digest {
        return Err(RequestPlanError::PlanDigestMismatch);
    }
    validate_signer_output("a_bogus", &outputs.a_bogus, MAX_A_BOGUS_BYTES)?;
    validate_signer_output("client_data", &outputs.client_data, MAX_CLIENT_DATA_BYTES)?;
    validate_signer_output(
        "ree_public_key",
        &outputs.ree_public_key,
        MAX_REE_PUBLIC_KEY_BYTES,
    )?;

    let final_url = format!(
        "{}?msToken={}&a_bogus={}&verifyFp={}&fp={}",
        unsigned.endpoint,
        percent_encode_rfc3986(&unsigned.query_ms_token),
        percent_encode_rfc3986(&outputs.a_bogus),
        percent_encode_rfc3986(&unsigned.fingerprint_verify_fp),
        percent_encode_rfc3986(&unsigned.fingerprint_fp),
    );
    if final_url.len() > MAX_FINAL_URL_BYTES {
        return Err(RequestPlanError::UrlTooLarge {
            actual: final_url.len(),
            maximum: MAX_FINAL_URL_BYTES,
        });
    }

    let mut headers = unsigned.unsigned_headers;
    headers.push(OrderedHeader::new(
        "bd-ticket-guard-client-data",
        outputs.client_data,
    ));
    headers.push(OrderedHeader::new(
        "bd-ticket-guard-ree-public-key",
        outputs.ree_public_key,
    ));
    headers.push(OrderedHeader::new("bd-ticket-guard-version", "2"));
    headers.push(OrderedHeader::new(
        "bd-ticket-guard-web-version",
        if unsigned
            .signer_requests
            .ticket_guard
            .ts_sign
            .starts_with("ts.1")
        {
            "1"
        } else {
            "2"
        },
    ));
    headers.push(OrderedHeader::new(
        "bd-ticket-guard-web-sign-type",
        match unsigned.signer_requests.ticket_guard.mode {
            TicketGuardMode::Ecdsa => "0",
            TicketGuardMode::Hmac => "1",
        },
    ));
    validate_header_collection(&headers, 0)?;

    Ok(RequestPlan {
        method: unsigned.method,
        final_url,
        headers,
        body: unsigned.body,
        body_sha256: unsigned.body_sha256,
        timeout_ms: unsigned.timeout_ms,
        plan_digest: unsigned.plan_digest,
    })
}

#[must_use]
pub fn percent_encode_rfc3986(value: &str) -> String {
    let mut output = String::with_capacity(value.len());
    for &byte in value.as_bytes() {
        if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
            output.push(char::from(byte));
        } else {
            write!(&mut output, "%{byte:02X}").expect("writing to String cannot fail");
        }
    }
    output
}

#[must_use]
pub fn parse_plan_digest(value: &str) -> Option<[u8; 32]> {
    if value.len() != 64 {
        return None;
    }
    let mut output = [0_u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        let high = hex_nibble(pair[0])?;
        let low = hex_nibble(pair[1])?;
        output[index] = (high << 4) | low;
    }
    Some(output)
}

fn merge_unsigned_headers(
    input: &SendHttpPlanInput<'_>,
) -> Result<Vec<OrderedHeader>, RequestPlanError> {
    let mut headers = vec![
        OrderedHeader::new("user-agent", input.user_agent),
        OrderedHeader::new("cookie", input.raw_cookie_header),
    ];
    let mut caller_names = BTreeSet::new();
    for header in input.caller_headers {
        let name = normalize_header_name(&header.name)?;
        if !caller_names.insert(name.clone()) {
            return Err(RequestPlanError::DuplicateHeader);
        }
        if is_ticket_guard_header(&name) {
            return Err(RequestPlanError::DuplicateHeader);
        }
        validate_bounded_text("header_value", &header.value, MAX_HEADER_VALUE_BYTES)?;
        if name == "cookie" && header.value != input.raw_cookie_header {
            return Err(RequestPlanError::DuplicateHeader);
        }
        if let Some(existing) = headers.iter_mut().find(|existing| existing.name == name) {
            existing.value.clone_from(&header.value);
        } else {
            headers.push(OrderedHeader::new(name, header.value.clone()));
        }
    }
    for (name, value) in browser_sec_ch_headers(input.user_agent) {
        if headers.iter().all(|header| header.name != name) {
            headers.push(OrderedHeader::new(name, value));
        }
    }
    Ok(headers)
}

fn browser_sec_ch_headers(user_agent: &str) -> [(&'static str, String); 3] {
    let major = chromium_major(user_agent).unwrap_or("124");
    let platform = if user_agent.contains("Macintosh") {
        "\"macOS\""
    } else if user_agent.contains("Linux") && !user_agent.contains("Android") {
        "\"Linux\""
    } else {
        "\"Windows\""
    };
    [
        (
            "sec-ch-ua",
            format!(
                "\"Not=A?Brand\";v=\"99\", \"Google Chrome\";v=\"{major}\", \
                 \"Chromium\";v=\"{major}\""
            ),
        ),
        ("sec-ch-ua-mobile", "?0".to_owned()),
        ("sec-ch-ua-platform", platform.to_owned()),
    ]
}

fn chromium_major(user_agent: &str) -> Option<&str> {
    ["Chrome/", "Chromium/"]
        .into_iter()
        .filter_map(|marker| {
            let start = user_agent.find(marker)? + marker.len();
            let rest = &user_agent[start..];
            let length = rest.bytes().take_while(u8::is_ascii_digit).count();
            (length > 0).then_some((start, &rest[..length]))
        })
        .min_by_key(|(start, _major)| *start)
        .map(|(_start, major)| major)
}

fn is_ticket_guard_header(name: &str) -> bool {
    matches!(
        name,
        "bd-ticket-guard-client-data"
            | "bd-ticket-guard-ree-public-key"
            | "bd-ticket-guard-version"
            | "bd-ticket-guard-web-version"
            | "bd-ticket-guard-web-sign-type"
    )
}

fn normalize_header_name(value: &str) -> Result<String, RequestPlanError> {
    if value.is_empty() || value.len() > MAX_HEADER_NAME_BYTES {
        return Err(RequestPlanError::InvalidHeaderName);
    }
    if !value.as_bytes().iter().all(|byte| {
        byte.is_ascii_alphanumeric()
            || matches!(
                byte,
                b'!' | b'#'
                    | b'$'
                    | b'%'
                    | b'&'
                    | b'\''
                    | b'*'
                    | b'+'
                    | b'-'
                    | b'.'
                    | b'^'
                    | b'_'
                    | b'`'
                    | b'|'
                    | b'~'
            )
    }) {
        return Err(RequestPlanError::InvalidHeaderName);
    }
    Ok(value.to_ascii_lowercase())
}

fn validate_header_collection(
    headers: &[OrderedHeader],
    reserved: usize,
) -> Result<(), RequestPlanError> {
    let total_count =
        headers
            .len()
            .checked_add(reserved)
            .ok_or(RequestPlanError::TooManyHeaders {
                actual: usize::MAX,
                maximum: MAX_HEADER_COUNT,
            })?;
    if total_count > MAX_HEADER_COUNT {
        return Err(RequestPlanError::TooManyHeaders {
            actual: total_count,
            maximum: MAX_HEADER_COUNT,
        });
    }
    let mut total_bytes = 0_usize;
    for header in headers {
        total_bytes = total_bytes
            .checked_add(header.name.len())
            .and_then(|value| value.checked_add(header.value.len()))
            .ok_or(RequestPlanError::FieldTooLarge {
                field: "headers",
                actual: usize::MAX,
                maximum: MAX_HEADER_BYTES,
            })?;
        if total_bytes > MAX_HEADER_BYTES {
            return Err(RequestPlanError::FieldTooLarge {
                field: "headers",
                actual: total_bytes,
                maximum: MAX_HEADER_BYTES,
            });
        }
    }
    Ok(())
}

fn validate_required_bounded(
    field: &'static str,
    value: &str,
    maximum: usize,
    missing: Option<RequestPlanError>,
) -> Result<(), RequestPlanError> {
    if value.is_empty() {
        return Err(missing.unwrap_or(RequestPlanError::FieldTooLarge {
            field,
            actual: 0,
            maximum,
        }));
    }
    validate_bounded_text(field, value, maximum)
}

fn validate_bounded_text(
    field: &'static str,
    value: &str,
    maximum: usize,
) -> Result<(), RequestPlanError> {
    validate_text_control(field, value)?;
    if value.len() > maximum {
        return Err(RequestPlanError::FieldTooLarge {
            field,
            actual: value.len(),
            maximum,
        });
    }
    Ok(())
}

fn validate_signer_output(
    field: &'static str,
    value: &str,
    maximum: usize,
) -> Result<(), RequestPlanError> {
    if value.is_empty() {
        return Err(RequestPlanError::InvalidSignerOutput { field });
    }
    validate_bounded_text(field, value, maximum)
}

fn validate_text_control(field: &'static str, value: &str) -> Result<(), RequestPlanError> {
    if value
        .chars()
        .any(|character| character == '\u{7f}' || character.is_control())
    {
        return Err(RequestPlanError::InvalidControlCharacter { field });
    }
    Ok(())
}

fn parse_cookie_lookup(raw: &str) -> CookieLookup {
    let mut result = CookieLookup {
        ms_token: String::new(),
        bd_ticket_guard_ts_sign_id: String::new(),
        bd_ticket_crypt_cookie: String::new(),
    };
    for item in raw.split(';') {
        let Some((name, value)) = item.trim().split_once('=') else {
            continue;
        };
        if name == "msToken" {
            value.clone_into(&mut result.ms_token);
        }
        if name == "bd_ticket_guard_ts_sign_id" {
            value.clone_into(&mut result.bd_ticket_guard_ts_sign_id);
        }
        if name == "_bd_ticket_crypt_cookie" {
            value.clone_into(&mut result.bd_ticket_crypt_cookie);
        }
    }
    result
}

fn compute_plan_digest(plan: &UnsignedRequestPlan) -> [u8; 32] {
    let mut digest = PlanDigestWriter::new();
    digest.put(plan.method.as_bytes());
    digest.put(plan.endpoint.as_bytes());
    digest.put(SEND_PATH.as_bytes());
    digest.put(plan.signing_host.as_bytes());
    digest.put(plan.cookie_host.as_bytes());
    digest.put(cookie_header_value(plan).as_bytes());
    digest.put(plan.query_ms_token.as_bytes());
    digest.put(plan.fingerprint_verify_fp.as_bytes());
    digest.put(plan.fingerprint_fp.as_bytes());
    digest.put(plan.user_agent_input.as_bytes());
    digest.put(plan.unsigned_headers.len().to_string().as_bytes());
    for header in &plan.unsigned_headers {
        digest.put(header.name.as_bytes());
        digest.put(header.value.as_bytes());
    }
    digest.put(&plan.body);
    digest.put(plan.timeout_ms.to_string().as_bytes());
    let guard = &plan.signer_requests.ticket_guard;
    digest.put(guard.ticket.as_bytes());
    digest.put(guard.ts_sign.as_bytes());
    digest.put(guard.private_key.as_bytes());
    digest.put(guard.timestamp.to_string().as_bytes());
    digest.put(if guard.ecdh_key.is_some() { b"1" } else { b"0" });
    digest.put(guard.ecdh_key.as_deref().unwrap_or_default());
    digest.put(guard.t_trust.map_or("", |_| "1").as_bytes());
    digest.finish()
}

fn cookie_header_value(plan: &UnsignedRequestPlan) -> &str {
    plan.unsigned_headers
        .iter()
        .find(|header| header.name == "cookie")
        .map_or("", |header| header.value.as_str())
}

struct PlanDigestWriter(Sha256);

impl PlanDigestWriter {
    fn new() -> Self {
        let mut hasher = Sha256::new();
        hasher.update(PLAN_DIGEST_DOMAIN);
        Self(hasher)
    }

    fn put(&mut self, value: &[u8]) {
        let value_length = u64::try_from(value.len()).expect("plan digest value is bounded");
        self.0.update(value_length.to_be_bytes());
        self.0.update(value);
    }

    fn finish(self) -> [u8; 32] {
        self.0.finalize().into()
    }
}

struct Redacted(usize);

impl fmt::Debug for Redacted {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "<redacted:{} bytes>", self.0)
    }
}

fn hex_encode(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(&mut output, "{byte:02x}").expect("writing to String cannot fail");
    }
    output
}

const fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BODY: &[u8] = b"synthetic protobuf\0body";
    const COOKIE: &str = "msToken=stale-cookie; MSTOKEN=ignored; \
        bd_ticket_guard_ts_sign_id=ts.2.synthetic; _bd_ticket_crypt_cookie=trust";

    fn caller_headers() -> Vec<OrderedHeader> {
        vec![
            OrderedHeader::new("content-type", "application/x-protobuf"),
            OrderedHeader::new("accept", "application/x-protobuf"),
            OrderedHeader::new("user-agent", "Synthetic UA/1.0"),
            OrderedHeader::new("sec-ch-ua", "\"Synthetic\";v=\"1\""),
            OrderedHeader::new("sec-ch-ua-mobile", "?0"),
            OrderedHeader::new("sec-ch-ua-platform", "\"Test\""),
            OrderedHeader::new("accept-language", "zh-CN"),
            OrderedHeader::new("referer", "https://www.douyin.com/"),
            OrderedHeader::new("priority", "u=1, i"),
            OrderedHeader::new("sec-fetch-dest", "empty"),
            OrderedHeader::new("sec-fetch-mode", "cors"),
            OrderedHeader::new("sec-fetch-site", "same-origin"),
        ]
    }

    fn input(headers: &[OrderedHeader]) -> SendHttpPlanInput<'_> {
        SendHttpPlanInput {
            method: SEND_METHOD,
            url: SEND_ENDPOINT,
            raw_cookie_header: COOKIE,
            query_ms_token: "query+ /=% \u{6d4b}\u{8bd5}",
            user_agent: "Synthetic UA/1.0",
            caller_headers: headers,
            body: BODY,
            timeout_ms: 15_000,
            fingerprint: FingerprintInput {
                verify_fp: "verify+ /=% \u{6d4b}\u{8bd5}",
                fp: "fp+ /=% \u{6d4b}\u{8bd5}",
            },
            ticket_guard: TicketGuardCredential {
                private_key: "synthetic-private-key",
                ticket: "synthetic-ticket",
                ts_sign: "ts.2.synthetic.payload",
                timestamp: 1_700_000_000,
                ecdh_key: Some(b"\x00\x11\x22"),
            },
        }
    }

    fn prepare(headers: &[OrderedHeader]) -> UnsignedRequestPlan {
        prepare_send_request(&input(headers)).expect("synthetic plan must prepare")
    }

    fn outputs(plan: &UnsignedRequestPlan) -> SignerOutputs {
        SignerOutputs {
            plan_digest: plan.plan_digest(),
            a_bogus: "AB+ /=% \u{6d4b}\u{8bd5}".to_owned(),
            client_data: "CLIENT_DATA==".to_owned(),
            ree_public_key: "REE+KEY/==".to_owned(),
        }
    }

    #[test]
    fn rfc3986_encoding_is_uppercase_and_exactly_once() {
        assert_eq!(
            percent_encode_rfc3986("a+ /=%\u{6d4b}"),
            "a%2B%20%2F%3D%25%E6%B5%8B"
        );
        assert_eq!(
            percent_encode_rfc3986("already%20encoded"),
            "already%2520encoded"
        );
    }

    #[test]
    fn prepare_preserves_cookie_and_freezes_signer_inputs() {
        let headers = caller_headers();
        let plan = prepare(&headers);
        assert_eq!(plan.signing_host(), SIGNING_HOST);
        assert_eq!(plan.cookie_host(), COOKIE_HOST);
        assert_eq!(plan.cookie_lookup().ms_token, "stale-cookie");
        assert_eq!(
            plan.signer_requests().a_bogus.query,
            "msToken=query%2B%20%2F%3D%25%20%E6%B5%8B%E8%AF%95"
        );
        assert_eq!(plan.signer_requests().a_bogus.body, "");
        assert_eq!(plan.signer_requests().ticket_guard.t_trust, Some(1));
        assert_eq!(
            plan.signer_requests().ticket_guard.mode,
            TicketGuardMode::Hmac
        );
        assert_eq!(plan.unsigned_headers()[1].name, "cookie");
        assert_eq!(plan.unsigned_headers()[1].value, COOKIE);
        assert_eq!(plan.body(), BODY);
    }

    #[test]
    fn cookie_lookup_is_exact_case_and_last_exact_duplicate_wins() {
        let lookup = parse_cookie_lookup(
            "msToken=old; mstoken=wrong-case; msToken=new; \
             bd_ticket_guard_ts_sign_id=ts.1; BD_TICKET_GUARD_TS_SIGN_ID=ignored; \
             bd_ticket_guard_ts_sign_id=ts.2; _bd_ticket_crypt_cookie=old-trust; \
             _BD_TICKET_CRYPT_COOKIE=ignored; _bd_ticket_crypt_cookie=new-trust",
        );
        assert_eq!(lookup.ms_token, "new");
        assert_eq!(lookup.bd_ticket_guard_ts_sign_id, "ts.2");
        assert_eq!(lookup.bd_ticket_crypt_cookie, "new-trust");
    }

    #[test]
    fn finalize_orders_query_and_five_guard_headers() {
        let headers = caller_headers();
        let unsigned = prepare(&headers);
        let plan = finalize_send_request(unsigned.clone(), outputs(&unsigned))
            .expect("bound output must finalize");
        assert_eq!(
            plan.final_url(),
            concat!(
                "https://imapi.douyin.com/v1/message/send?",
                "msToken=query%2B%20%2F%3D%25%20%E6%B5%8B%E8%AF%95&",
                "a_bogus=AB%2B%20%2F%3D%25%20%E6%B5%8B%E8%AF%95&",
                "verifyFp=verify%2B%20%2F%3D%25%20%E6%B5%8B%E8%AF%95&",
                "fp=fp%2B%20%2F%3D%25%20%E6%B5%8B%E8%AF%95"
            )
        );
        let tail = &plan.headers()[plan.headers().len() - 5..];
        assert_eq!(
            tail.iter()
                .map(|header| header.name.as_str())
                .collect::<Vec<_>>(),
            vec![
                "bd-ticket-guard-client-data",
                "bd-ticket-guard-ree-public-key",
                "bd-ticket-guard-version",
                "bd-ticket-guard-web-version",
                "bd-ticket-guard-web-sign-type",
            ]
        );
        assert_eq!(tail[3].value, "2");
        assert_eq!(tail[4].value, "1");
    }

    #[test]
    fn signer_outputs_cannot_be_reused_for_another_plan() {
        let headers = caller_headers();
        let first = prepare(&headers);
        let first_outputs = outputs(&first);
        let mut changed = headers.clone();
        changed.push(OrderedHeader::new("x-synthetic", "different"));
        let second = prepare(&changed);
        assert_eq!(
            finalize_send_request(second, first_outputs),
            Err(RequestPlanError::PlanDigestMismatch)
        );
    }

    #[test]
    fn plan_digest_binds_exact_ecdh_key_bytes_and_normalizes_empty() {
        let headers = caller_headers();
        let mut first_input = input(&headers);
        first_input.ticket_guard.ecdh_key = Some(b"first-derived-key");
        let first = prepare_send_request(&first_input).expect("first HMAC plan must prepare");
        let first_outputs = outputs(&first);

        let mut second_input = input(&headers);
        second_input.ticket_guard.ecdh_key = Some(b"second-derived-key");
        let second = prepare_send_request(&second_input).expect("second HMAC plan must prepare");
        assert_ne!(first.plan_digest(), second.plan_digest());
        assert_eq!(
            finalize_send_request(second, first_outputs),
            Err(RequestPlanError::PlanDigestMismatch)
        );

        let mut empty_input = input(&headers);
        empty_input.ticket_guard.ecdh_key = Some(b"");
        let empty = prepare_send_request(&empty_input).expect("empty key normalizes to absent");
        assert_eq!(empty.signer_requests().ticket_guard.ecdh_key, None);
        assert_eq!(
            empty.signer_requests().ticket_guard.mode,
            TicketGuardMode::Ecdsa
        );
    }

    #[test]
    fn caller_header_count_is_rejected_before_field_processing() {
        let headers = (0..=(MAX_HEADER_COUNT - FINAL_GUARD_HEADER_COUNT))
            .map(|index| OrderedHeader::new(format!("x-{index}"), "secret-value"))
            .collect::<Vec<_>>();
        let mut hostile = input(&headers);
        hostile.method = "GET\r\n";
        let error = prepare_send_request(&hostile).expect_err("header preflight must win");
        assert_eq!(error.code(), "too_many_headers");
        assert!(!format!("{error:?}").contains("secret-value"));
    }

    #[test]
    fn header_duplicates_and_control_characters_are_rejected() {
        let mut duplicate = caller_headers();
        duplicate.push(OrderedHeader::new("Accept", "duplicate"));
        let error =
            prepare_send_request(&input(&duplicate)).expect_err("case-folded duplicate must fail");
        assert_eq!(error.code(), "duplicate_header");

        let mut control = caller_headers();
        control[0].value = "application/x-protobuf\r\nX: injected".to_owned();
        let error = prepare_send_request(&input(&control)).expect_err("header injection must fail");
        assert_eq!(error.code(), "invalid_control_character");
    }

    #[test]
    fn invalid_and_reserved_header_names_are_rejected() {
        let mut invalid = caller_headers();
        invalid.push(OrderedHeader::new("bad header", "value"));
        assert_eq!(
            prepare_send_request(&input(&invalid))
                .expect_err("invalid name must fail")
                .code(),
            "invalid_header_name"
        );

        let mut reserved = caller_headers();
        reserved.push(OrderedHeader::new(
            "BD-Ticket-Guard-Client-Data",
            "caller-controlled",
        ));
        assert_eq!(
            prepare_send_request(&input(&reserved))
                .expect_err("reserved guard header must fail")
                .code(),
            "duplicate_header"
        );
    }

    #[test]
    fn debug_output_redacts_all_sensitive_values() {
        let headers = caller_headers();
        let unsigned = prepare(&headers);
        let debug = format!("{unsigned:?} {:?}", outputs(&unsigned));
        for secret in [
            "query+ /=%",
            "synthetic-private-key",
            "synthetic-ticket",
            "CLIENT_DATA==",
            "REE+KEY/==",
        ] {
            assert!(!debug.contains(secret), "debug leaked {secret}");
        }
        assert!(debug.contains("<redacted"));
    }
}
