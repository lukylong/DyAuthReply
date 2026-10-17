//! In-process signing: native `RustCrypto` ticket guard and the vendored A-Bogus
//! program executed by embedded `QuickJS`. No Python/Node child process.
use std::{fmt, sync::Arc};

use base64::{engine::general_purpose::STANDARD, Engine};
use hkdf::Hkdf;
use hmac::{Hmac, Mac};
use p256::{
    ecdh::diffie_hellman,
    ecdsa::{signature::Signer, Signature, SigningKey},
    elliptic_curve::sec1::ToEncodedPoint,
    pkcs8::DecodePrivateKey,
    PublicKey, SecretKey,
};
use rquickjs::{Context, Runtime};
use serde::Serialize;
use sha2::Sha256;
use thiserror::Error;
use tokio::sync::Semaphore;
use x509_cert::{der::DecodePem, Certificate};

use super::http_plan::{SignerOutputs, TicketGuardSigningInput, UnsignedRequestPlan};

const AB_SOURCE: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../backend-django/core/douyin/runtime/transport/sign/js/dy_ab.js"
));
const MAX_INPUT: usize = 16_384;
const AB_CURRENT_SOURCE: &str = include_str!("ab_current.js");

#[derive(Debug, Error)]
pub enum SigningError {
    #[error("invalid P-256 private key")]
    InvalidPrivateKey,
    #[error("invalid P-256 server certificate")]
    InvalidServerKey,
    #[error("signing input exceeds bound")]
    InputLimit,
    #[error("invalid ticket guard HMAC key")]
    InvalidHmacKey,
    #[error("A-Bogus engine failed")]
    Engine,
    #[error("signer lanes full")]
    Busy,
    #[error("signer worker terminated")]
    Worker,
}

// Browser exports may keep all base64 DER on one line. Decode PEM framing
// separately instead of enforcing a 64-column encoder style. The original
// credential text and request-plan digest stay untouched.
fn pem_der(value: &str, label: &str) -> Result<Vec<u8>, SigningError> {
    let begin = format!("-----BEGIN {label}-----");
    let end = format!("-----END {label}-----");
    let body = value
        .strip_prefix(&begin)
        .and_then(|s| s.strip_suffix(&end))
        .ok_or(SigningError::InvalidPrivateKey)?;
    let body: String = body.chars().filter(|c| !c.is_ascii_whitespace()).collect();
    STANDARD
        .decode(body)
        .map_err(|_| SigningError::InvalidPrivateKey)
}

fn private_key(value: &str) -> Result<SecretKey, SigningError> {
    if value.len() > MAX_INPUT {
        return Err(SigningError::InputLimit);
    }
    let value = value.trim();
    if value.starts_with("-----BEGIN PRIVATE KEY-----") {
        return SecretKey::from_pkcs8_der(&pem_der(value, "PRIVATE KEY")?)
            .map_err(|_| SigningError::InvalidPrivateKey);
    }
    if value.starts_with("-----BEGIN EC PRIVATE KEY-----") {
        return SecretKey::from_sec1_der(&pem_der(value, "EC PRIVATE KEY")?)
            .map_err(|_| SigningError::InvalidPrivateKey);
    }
    if value.is_empty() || value.len() > 64 || !value.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err(SigningError::InvalidPrivateKey);
    }
    let padded = format!("{value:0>64}");
    let mut bytes = [0_u8; 32];
    for (i, pair) in padded.as_bytes().as_chunks::<2>().0.iter().enumerate() {
        let pair = std::str::from_utf8(pair).map_err(|_| SigningError::InvalidPrivateKey)?;
        bytes[i] = u8::from_str_radix(pair, 16).map_err(|_| SigningError::InvalidPrivateKey)?;
    }
    SecretKey::from_slice(&bytes).map_err(|_| SigningError::InvalidPrivateKey)
}

/// P-256 ECDH followed by HKDF-SHA256, empty salt/info, 32 output bytes.
/// # Errors
/// Rejects invalid/oversized keys and non-P256 certificates.
pub fn derive_ecdh_key(private: &str, server: &str) -> Result<[u8; 32], SigningError> {
    if server.len() > MAX_INPUT {
        return Err(SigningError::InputLimit);
    }
    let private = private_key(private)?;
    let point = if let Some(raw) = server.trim().strip_prefix("pub.") {
        STANDARD
            .decode(raw)
            .map_err(|_| SigningError::InvalidServerKey)?
    } else {
        let cert =
            Certificate::from_pem(server.trim()).map_err(|_| SigningError::InvalidServerKey)?;
        cert.tbs_certificate
            .subject_public_key_info
            .subject_public_key
            .as_bytes()
            .ok_or(SigningError::InvalidServerKey)?
            .to_vec()
    };
    let public = PublicKey::from_sec1_bytes(&point).map_err(|_| SigningError::InvalidServerKey)?;
    let shared = diffie_hellman(private.to_nonzero_scalar(), public.as_affine());
    let mut key = [0_u8; 32];
    Hkdf::<Sha256>::new(None, shared.raw_secret_bytes())
        .expand(b"", &mut key)
        .map_err(|_| SigningError::InvalidHmacKey)?;
    Ok(key)
}

/// # Errors
/// Rejects invalid P-256 key material.
pub fn ree_public_key(private: &str) -> Result<String, SigningError> {
    Ok(STANDARD.encode(
        private_key(private)?
            .public_key()
            .to_encoded_point(false)
            .as_bytes(),
    ))
}

#[derive(Serialize)]
struct ClientData<'a> {
    ts_sign: &'a str,
    req_content: &'a str,
    req_sign: String,
    timestamp: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    t_trust: Option<u8>,
}

/// Builds the real DER-ECDSA or HMAC ticket-guard header, not a canned output.
/// # Errors
/// Rejects malformed/bounded signer input without revealing its contents.
pub fn ticket_client_data(input: &TicketGuardSigningInput) -> Result<String, SigningError> {
    if input.sign_payload.len() > MAX_INPUT || input.ts_sign.len() > MAX_INPUT {
        return Err(SigningError::InputLimit);
    }
    let signature = if let Some(key) = &input.ecdh_key {
        if key.len() != 32 {
            return Err(SigningError::InvalidHmacKey);
        }
        let mut mac =
            Hmac::<Sha256>::new_from_slice(key).map_err(|_| SigningError::InvalidHmacKey)?;
        mac.update(input.sign_payload.as_bytes());
        STANDARD.encode(mac.finalize().into_bytes())
    } else {
        let key = SigningKey::from(private_key(&input.private_key)?);
        let signature: Signature = key.sign(input.sign_payload.as_bytes());
        STANDARD.encode(signature.to_der().as_bytes())
    };
    let data = ClientData {
        ts_sign: &input.ts_sign,
        req_content: input.req_content,
        req_sign: signature,
        timestamp: input.timestamp,
        t_trust: input.t_trust,
    };
    Ok(STANDARD.encode(serde_json::to_vec(&data).map_err(|_| SigningError::InputLimit)?))
}

fn abogus_with_prelude(query: &str, body: &str, prelude: &str) -> Result<String, SigningError> {
    abogus_with_user_agent_and_prelude(query, body, None, prelude)
}

fn abogus_with_user_agent_and_prelude(
    query: &str,
    body: &str,
    user_agent: Option<&str>,
    prelude: &str,
) -> Result<String, SigningError> {
    if query.len() > MAX_INPUT || body.len() > MAX_INPUT {
        return Err(SigningError::InputLimit);
    }
    if user_agent
        .is_some_and(|ua| ua.is_empty() || ua.len() > 2048 || ua.chars().any(char::is_control))
    {
        return Err(SigningError::InputLimit);
    }
    let runtime = Runtime::new().map_err(|_| SigningError::Engine)?;
    runtime.set_memory_limit(32 * 1024 * 1024);
    runtime.set_max_stack_size(1024 * 1024);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    runtime.set_interrupt_handler(Some(Box::new(move || {
        std::time::Instant::now() >= deadline
    })));
    let context = Context::full(&runtime).map_err(|_| SigningError::Engine)?;
    let result = context.with(|ctx| {
        ctx.eval::<(), _>("function require(){return {};}")
            .map_err(|_| SigningError::Engine)?;
        ctx.eval::<(), _>(prelude)
            .map_err(|_| SigningError::Engine)?;
        let mut options = rquickjs::context::EvalOptions::default();
        options.strict = false; // The frozen source relies on sloppy-mode globals.
        ctx.eval_with_options::<(), _>(AB_SOURCE, options)
            .map_err(|_| SigningError::Engine)?;
        let function: rquickjs::Function = ctx
            .globals()
            .get("get_ab")
            .map_err(|_| SigningError::Engine)?;
        function
            .call::<_, String>((query, body, user_agent.unwrap_or("")))
            .map_err(|_| SigningError::Engine)
    })?;
    if result.is_empty() || result.len() > 8192 {
        return Err(SigningError::Engine);
    }

    Ok(result)
}

fn captured_abogus(
    query: &str,
    body: &str,
    ua: &str,
    profile: &str,
    prelude: &str,
) -> Result<String, SigningError> {
    if query.len() > MAX_INPUT
        || body.len() > MAX_INPUT
        || ua.is_empty()
        || ua.len() > 2048
        || profile.len() > 4096
    {
        return Err(SigningError::InputLimit);
    }
    let runtime = Runtime::new().map_err(|_| SigningError::Engine)?;
    runtime.set_memory_limit(32 * 1024 * 1024);
    runtime.set_max_stack_size(1024 * 1024);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    runtime.set_interrupt_handler(Some(Box::new(move || {
        std::time::Instant::now() >= deadline
    })));
    let context = Context::full(&runtime).map_err(|_| SigningError::Engine)?;
    context.with(|ctx| {
        ctx.eval::<(), _>(prelude)
            .map_err(|_| SigningError::Engine)?;
        ctx.eval::<(), _>(AB_CURRENT_SOURCE)
            .map_err(|_| SigningError::Engine)?;
        let f: rquickjs::Function = ctx
            .globals()
            .get("get_ab_current")
            .map_err(|_| SigningError::Engine)?;
        let result = f
            .call::<_, String>((query, body, ua, profile))
            .map_err(|_| SigningError::Engine)?;
        if result.is_empty() || result.len() > 8192 {
            return Err(SigningError::InputLimit);
        }
        Ok(result)
    })
}

/// No subprocess, external JS runtime, credential store, or network operation.
/// # Errors
/// Returns a bounded error on input limits or JS evaluation failure.
pub fn abogus(query: &str, body: &str) -> Result<String, SigningError> {
    abogus_with_prelude(query, body, "")
}

/// Sign using the same imported User-Agent as the actual HTTP request.
/// # Errors
/// Rejects empty, oversized or control-containing UA values without logging them.
pub fn abogus_for_user_agent(
    query: &str,
    body: &str,
    user_agent: &str,
) -> Result<String, SigningError> {
    abogus_with_user_agent_and_prelude(query, body, Some(user_agent), "")
}

#[derive(Clone)]
pub struct NativeSigner {
    lanes: Arc<Semaphore>,
}
impl fmt::Debug for NativeSigner {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("NativeSigner(<bounded>)")
    }
}
impl NativeSigner {
    /// # Errors
    /// Rejects zero or excessive parallelism.
    pub fn new(concurrency: usize) -> Result<Self, SigningError> {
        if !(1..=16).contains(&concurrency) {
            return Err(SigningError::InputLimit);
        }
        Ok(Self {
            lanes: Arc::new(Semaphore::new(concurrency)),
        })
    }
    /// General query signing for authenticated read requests.
    /// # Errors
    /// Enforces the same global CPU admission and execution bounds as sends.
    pub async fn sign_query(
        &self,
        query: String,
        body: String,
        user_agent: String,
    ) -> Result<String, SigningError> {
        let permit = self
            .lanes
            .clone()
            .try_acquire_owned()
            .map_err(|_| SigningError::Busy)?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            abogus_for_user_agent(&query, &body, &user_agent)
        })
        .await
        .map_err(|_| SigningError::Worker)?
    }

    /// Current host-aware SDK with a real captured geometry profile.
    /// # Errors
    /// Preserves bounded signer admission and rejects incomplete captured fields.
    pub async fn sign_captured_query(
        &self,
        query: String,
        body: String,
        ua: String,
        profile: String,
    ) -> Result<String, SigningError> {
        if profile.len() > 4096 {
            return Err(SigningError::InputLimit);
        }
        let mut profile: serde_json::Value =
            serde_json::from_str(&profile).map_err(|_| SigningError::InputLimit)?;
        profile
            .as_object_mut()
            .ok_or(SigningError::InputLimit)?
            .insert("signing_context".into(), serde_json::json!("current_web"));
        let profile = profile.to_string();
        let permit = self
            .lanes
            .clone()
            .try_acquire_owned()
            .map_err(|_| SigningError::Busy)?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            captured_abogus(&query, &body, &ua, &profile, "")
        })
        .await
        .map_err(|_| SigningError::Worker)?
    }

    /// # Errors
    /// Busy is explicit; cancelled callers do not release CPU capacity early.
    pub async fn sign(&self, plan: UnsignedRequestPlan) -> Result<SignerOutputs, SigningError> {
        let permit = self
            .lanes
            .clone()
            .try_acquire_owned()
            .map_err(|_| SigningError::Busy)?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            let input = plan.signer_requests();
            Ok(SignerOutputs {
                plan_digest: plan.plan_digest(),
                a_bogus: if let Some(profile) = &input.a_bogus.browser_profile {
                    captured_abogus(
                        &input.a_bogus.query,
                        input.a_bogus.body,
                        plan.user_agent(),
                        profile,
                        "",
                    )?
                } else {
                    abogus_for_user_agent(
                        &input.a_bogus.query,
                        input.a_bogus.body,
                        plan.user_agent(),
                    )?
                },
                client_data: ticket_client_data(&input.ticket_guard)?,
                ree_public_key: ree_public_key(&input.ticket_guard.private_key)?,
            })
        })
        .await
        .map_err(|_| SigningError::Worker)?
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fmt::Write;
    #[test]
    fn current_sdk_matches_independent_python_vectors_with_captured_geometry() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/captured_abogus.json"))
                .unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let prelude = format!(
                "Date.now=()=>{};Math.random=()=>{};",
                case["now"], case["random"]
            );
            let value = captured_abogus(
                case["query"].as_str().unwrap(),
                case["body"].as_str().unwrap(),
                case["ua"].as_str().unwrap(),
                &case["profile"].to_string(),
                &prelude,
            )
            .unwrap();
            assert_eq!(value, case["expected"].as_str().unwrap());
            assert_eq!(value.len(), 192);
            assert!(captured_abogus("x", "", "Chrome/155", "{}", &prelude).is_err());
        }
    }
    #[test]
    fn account_user_agent_changes_abogus_with_frozen_clock_and_random() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/native_signing.json")).unwrap();
        let prelude = fixture["ab_prelude"].as_str().unwrap();
        let mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36";
        let win = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36";
        let sign = |ua| {
            abogus_with_user_agent_and_prelude("msToken=synthetic-only", "", Some(ua), prelude)
                .unwrap()
        };
        assert_ne!(sign(mac), sign(win));
        assert_eq!(sign(mac), sign(mac));
        assert_ne!(
            sign(mac),
            abogus_with_prelude("msToken=synthetic-only", "", prelude).unwrap()
        );
        for invalid in ["", "bad\nua"] {
            assert!(matches!(
                abogus_for_user_agent("synthetic", "", invalid),
                Err(SigningError::InputLimit)
            ));
        }
    }

    #[test]
    fn browser_single_line_pkcs8_body_is_accepted_without_changing_plan_material() {
        let f: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/native_signing.json")).unwrap();
        let original = f["private_key"].as_str().unwrap();
        let body: String = original
            .lines()
            .filter(|line| !line.starts_with("-----"))
            .collect();
        let compact = format!("-----BEGIN PRIVATE KEY-----\n{body}\n-----END PRIVATE KEY-----");
        assert_eq!(
            ree_public_key(&compact).unwrap(),
            ree_public_key(original).unwrap()
        );
        assert_eq!(
            derive_ecdh_key(&compact, f["peer_public"].as_str().unwrap()).unwrap(),
            derive_ecdh_key(original, f["peer_public"].as_str().unwrap()).unwrap()
        );
    }

    #[test]
    fn javascript_execution_deadline_interrupts_a_loop() {
        let start = std::time::Instant::now();
        assert!(matches!(
            abogus_with_prelude("", "", "while(true){}"),
            Err(SigningError::Engine)
        ));
        assert!(start.elapsed() < std::time::Duration::from_secs(4));
    }

    #[test]
    fn native_crypto_and_abogus_match_python_node_reference() {
        use p256::ecdsa::{signature::Verifier, VerifyingKey};
        let f: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/native_signing.json")).unwrap();
        let value = |key: &str| f[key].as_str().unwrap();
        assert_eq!(
            ree_public_key(value("private_key")).unwrap(),
            value("ree_public")
        );
        let shared = derive_ecdh_key(value("private_key"), value("peer_public")).unwrap();
        let hex = shared.iter().fold(String::new(), |mut output, byte| {
            write!(&mut output, "{byte:02x}").unwrap();
            output
        });
        assert_eq!(hex, value("ecdh_hex"));
        let mut mac = Hmac::<Sha256>::new_from_slice(&shared).unwrap();
        mac.update(value("payload").as_bytes());
        assert_eq!(STANDARD.encode(mac.finalize().into_bytes()), value("hmac"));
        let signature = Signature::from_der(&STANDARD.decode(value("ecdsa")).unwrap()).unwrap();
        let public = VerifyingKey::from(private_key(value("private_key")).unwrap().public_key());
        public
            .verify(value("payload").as_bytes(), &signature)
            .unwrap();
        assert_eq!(
            abogus_with_prelude(value("ab_query"), value("ab_body"), value("ab_prelude")).unwrap(),
            value("ab_expected")
        );
    }

    #[test]
    fn real_abogus_executes_without_node_or_python() {
        let signed = abogus("msToken=synthetic-only", "").expect("embedded A-Bogus executes");
        assert!(signed.len() > 64);
    }
    #[test]
    fn rejects_private_material_without_echoing_it() {
        let secret = "private-secret-invalid";
        let err = ree_public_key(secret).unwrap_err();
        assert!(!format!("{err:?} {err}").contains(secret));
    }
    #[test]
    fn ecdh_is_symmetric() {
        let first = format!("pub.{}", ree_public_key("1").unwrap());
        let second = format!("pub.{}", ree_public_key("2").unwrap());
        assert_eq!(
            derive_ecdh_key("1", &second).unwrap(),
            derive_ecdh_key("2", &first).unwrap()
        );
    }
}
