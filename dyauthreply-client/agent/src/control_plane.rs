//! Authenticated hosted ownership. Grants become installable only after strict
//! Ed25519 verification and complete request/account/time binding checks.
use crate::store::{AccountLease, CoreStore};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use ed25519_dalek::{pkcs8::DecodePublicKey, Signature, VerifyingKey};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use thiserror::Error;
use uuid::Uuid;

const MAX_TOKEN_BYTES: usize = 512 * 1024;
const CLOCK_MARGIN_MS: u64 = 5000;
const ISSUER: &str = "dyauthreply-account-lease";
const AUDIENCE: &str = "dy-agent";

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum LeaseAction {
    Acquire,
    Renew,
    Release,
}
#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AccountLeaseOperation {
    pub platform: String,
    pub platform_account_id: String,
    pub local_account_id: String,
    pub action: LeaseAction,
    pub expected_epoch: u64,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct LeaseSyncRequest {
    pub activation_id: Uuid,
    pub activation_token: String,
    pub instance_id: Uuid,
    pub boot_id: Uuid,
    pub request_id: Uuid,
    pub sequence: u64,
    pub accounts: Vec<AccountLeaseOperation>,
}
impl LeaseSyncRequest {
    /// # Errors
    /// Rejects unbounded, duplicated, or contradictory account operations.
    pub fn validate(&self) -> Result<(), ControlError> {
        if self.activation_id.is_nil()
            || self.instance_id.is_nil()
            || self.boot_id.is_nil()
            || self.request_id.is_nil()
            || self.activation_token.is_empty()
            || self.activation_token.len() > 512
            || self.accounts.is_empty()
            || self.accounts.len() > 300
            || self.sequence == 0
            || self.sequence > i64::MAX as u64
        {
            return Err(ControlError::Request);
        }
        let mut local = BTreeSet::new();
        let mut platform = BTreeSet::new();
        for operation in &self.accounts {
            if operation.platform != "douyin"
                || !valid_id(&operation.local_account_id)
                || !valid_id(&operation.platform_account_id)
                || !local.insert(&operation.local_account_id)
                || !platform.insert(&operation.platform_account_id)
                || operation.expected_epoch > i64::MAX as u64
                || (operation.action == LeaseAction::Acquire) != (operation.expected_epoch == 0)
            {
                return Err(ControlError::Request);
            }
        }
        Ok(())
    }
}
fn valid_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 256
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"_.:-".contains(&c))
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, Eq, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum GrantStatus {
    Owned,
    Released,
    Busy,
    Stale,
    Quota,
}
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AccountGrant {
    pub platform: String,
    pub platform_account_id: String,
    pub local_account_id: String,
    pub action: LeaseAction,
    pub status: GrantStatus,
    pub fence_epoch: u64,
    pub lease_until_ms: u64,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct BatchClaims {
    iss: String,
    aud: String,
    sub: Uuid,
    ver: u16,
    instance_id: Uuid,
    boot_id: Uuid,
    request_id: Uuid,
    sequence: u64,
    iat: u64,
    exp: u64,
    server_time_ms: u64,
    results: Vec<AccountGrant>,
    allow_manual: bool,
    allow_auto: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct JwtHeader {
    alg: String,
    typ: String,
}

#[derive(Debug, Error)]
pub enum ControlError {
    #[error("invalid control request")]
    Request,
    #[error("invalid trusted lease verification key")]
    TrustKey,
    #[error("invalid signed grant encoding")]
    Encoding,
    #[error("lease signature verification failed")]
    Signature,
    #[error("lease response does not match requested owner/accounts")]
    Binding,
    #[error("lease expired or clock/TTL bounds failed")]
    Time,
    #[error("hosted control transport failed")]
    Transport,
    #[error("hosted control returned HTTP {0}")]
    Http(u16),
    #[error("verified lease installation failed")]
    Store,
}

pub struct GrantVerifier {
    key: VerifyingKey,
}
pub struct VerifiedGrantBatch {
    claims: BatchClaims,
    verified_at: Instant,
    verified_wall_ms: u64,
    deadline: Instant,
}
impl GrantVerifier {
    /// The public key must be pinned/configured independently of the grant response.
    /// # Errors
    /// Rejects malformed or weak Ed25519 keys.
    pub fn from_pem(public_key: &str) -> Result<Self, ControlError> {
        if public_key.len() > 8192 {
            return Err(ControlError::TrustKey);
        }
        let key =
            VerifyingKey::from_public_key_pem(public_key).map_err(|_| ControlError::TrustKey)?;
        if key.is_weak() {
            return Err(ControlError::TrustKey);
        }
        Ok(Self { key })
    }
    /// # Errors
    /// Verifies signature, issuer/purpose, nonce/owner/accounts, and current expiry.
    pub fn verify(
        &self,
        token: &str,
        request: &LeaseSyncRequest,
    ) -> Result<VerifiedGrantBatch, ControlError> {
        self.verify_at(token, request, unix_ms()?)
    }
    fn verify_at(
        &self,
        token: &str,
        request: &LeaseSyncRequest,
        now_ms: u64,
    ) -> Result<VerifiedGrantBatch, ControlError> {
        request.validate()?;
        if token.len() > MAX_TOKEN_BYTES {
            return Err(ControlError::Encoding);
        }
        let parts: Vec<_> = token.split('.').collect();
        if parts.len() != 3 || parts[0].len() > 2048 {
            return Err(ControlError::Encoding);
        }
        let header: JwtHeader = serde_json::from_slice(
            &URL_SAFE_NO_PAD
                .decode(parts[0])
                .map_err(|_| ControlError::Encoding)?,
        )
        .map_err(|_| ControlError::Encoding)?;
        if header.alg != "EdDSA" || header.typ != "JWT" {
            return Err(ControlError::Signature);
        }
        let signature = Signature::from_slice(
            &URL_SAFE_NO_PAD
                .decode(parts[2])
                .map_err(|_| ControlError::Encoding)?,
        )
        .map_err(|_| ControlError::Signature)?;
        self.key
            .verify_strict(format!("{}.{}", parts[0], parts[1]).as_bytes(), &signature)
            .map_err(|_| ControlError::Signature)?;
        let claims: BatchClaims = serde_json::from_slice(
            &URL_SAFE_NO_PAD
                .decode(parts[1])
                .map_err(|_| ControlError::Encoding)?,
        )
        .map_err(|_| ControlError::Encoding)?;
        validate_claims(&claims, request, now_ms)?;
        let verified_at = Instant::now();
        let until = claims
            .results
            .iter()
            .filter(|r| r.status == GrantStatus::Owned)
            .map(|r| r.lease_until_ms)
            .min()
            .unwrap_or(claims.exp.saturating_mul(1000));
        let remaining = until
            .saturating_sub(now_ms)
            .saturating_sub(CLOCK_MARGIN_MS)
            .min(60_000);
        Ok(VerifiedGrantBatch {
            claims,
            verified_at,
            verified_wall_ms: now_ms,
            deadline: verified_at + Duration::from_millis(remaining),
        })
    }
}

fn validate_claims(
    claims: &BatchClaims,
    request: &LeaseSyncRequest,
    now_ms: u64,
) -> Result<(), ControlError> {
    if claims.iss != ISSUER
        || claims.aud != AUDIENCE
        || claims.ver != 1
        || claims.sub != request.activation_id
        || claims.instance_id != request.instance_id
        || claims.boot_id != request.boot_id
        || claims.request_id != request.request_id
        || claims.sequence != request.sequence
        || claims.results.len() != request.accounts.len()
    {
        return Err(ControlError::Binding);
    }
    let issued = claims.iat.checked_mul(1000).ok_or(ControlError::Time)?;
    let expires = claims.exp.checked_mul(1000).ok_or(ControlError::Time)?;
    if expires <= now_ms
        || expires <= issued
        || expires - issued > 61_000
        || issued > now_ms.saturating_add(CLOCK_MARGIN_MS)
        || claims.server_time_ms < issued
        || claims.server_time_ms >= issued.saturating_add(1000)
    {
        return Err(ControlError::Time);
    }
    let expected: BTreeMap<_, _> = request
        .accounts
        .iter()
        .map(|account| (&account.local_account_id, account))
        .collect();
    let mut seen = BTreeSet::new();
    for grant in &claims.results {
        let input = expected
            .get(&grant.local_account_id)
            .ok_or(ControlError::Binding)?;
        if !seen.insert(&grant.local_account_id)
            || grant.platform != input.platform
            || grant.platform_account_id != input.platform_account_id
            || grant.action != input.action
            || grant.fence_epoch > i64::MAX as u64
        {
            return Err(ControlError::Binding);
        }
        match grant.status {
            GrantStatus::Owned => {
                if grant.action == LeaseAction::Release
                    || grant.fence_epoch == 0
                    || (grant.action == LeaseAction::Renew
                        && grant.fence_epoch != input.expected_epoch)
                {
                    return Err(ControlError::Binding);
                }
                if grant.lease_until_ms > expires
                    || grant.lease_until_ms <= now_ms.saturating_add(CLOCK_MARGIN_MS)
                    || grant.lease_until_ms <= claims.server_time_ms
                    || grant.lease_until_ms - claims.server_time_ms > 60_000
                {
                    return Err(ControlError::Time);
                }
            }
            GrantStatus::Released => {
                if grant.action != LeaseAction::Release
                    || grant.fence_epoch != input.expected_epoch
                    || grant.lease_until_ms != 0
                {
                    return Err(ControlError::Binding);
                }
            }
            GrantStatus::Busy | GrantStatus::Quota | GrantStatus::Stale => {
                if grant.fence_epoch != 0 || grant.lease_until_ms != 0 {
                    return Err(ControlError::Binding);
                }
            }
        }
    }
    Ok(())
}

impl VerifiedGrantBatch {
    #[must_use]
    pub fn results(&self) -> &[AccountGrant] {
        &self.claims.results
    }
    #[must_use]
    pub const fn allows_manual(&self) -> bool {
        self.claims.allow_manual
    }
    #[must_use]
    pub const fn allows_auto(&self) -> bool {
        self.claims.allow_auto
    }
    #[must_use]
    pub const fn deadline(&self) -> Instant {
        self.deadline
    }
    /// Only signed Owned results are installable. Leave a conservative 5-second
    /// margin so a slightly slow client clock cannot outlive server ownership.
    /// # Errors
    /// The caller receives individual install results; one failure is not a batch success.
    pub fn install_owned(&self, store: &CoreStore) -> Vec<Result<AccountLease, ControlError>> {
        self.claims
            .results
            .iter()
            .filter(|grant| grant.status == GrantStatus::Owned)
            .map(|grant| {
                let elapsed = u64::try_from(self.verified_at.elapsed().as_millis())
                    .map_err(|_| ControlError::Time)?;
                let wall = unix_ms()?;
                if Instant::now() >= self.deadline
                    || wall.abs_diff(self.verified_wall_ms.saturating_add(elapsed))
                        > CLOCK_MARGIN_MS
                {
                    return Err(ControlError::Time);
                }
                let epoch = i64::try_from(grant.fence_epoch).map_err(|_| ControlError::Binding)?;
                let until = i64::try_from(grant.lease_until_ms.saturating_sub(CLOCK_MARGIN_MS))
                    .map_err(|_| ControlError::Time)?;
                store
                    .install_verified_account_lease(
                        &grant.local_account_id,
                        &self.claims.instance_id.to_string(),
                        &self.claims.boot_id.to_string(),
                        epoch,
                        until,
                    )
                    .map_err(|_| ControlError::Store)
            })
            .collect()
    }
}
fn unix_ms() -> Result<u64, ControlError> {
    u64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| ControlError::Time)?
            .as_millis(),
    )
    .map_err(|_| ControlError::Time)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SignedBatchResponse {
    token: String,
}

pub struct HostedControlClient {
    client: wreq::Client,
    endpoint: String,
    verifier: GrantVerifier,
}
impl HostedControlClient {
    /// HTTPS for remote hosts; explicit numeric loopback supports local Django tests.
    /// # Errors
    /// Rejects user-info/query/fragment URLs and untrusted HTTP origins.
    pub fn new(server: &str, public_key: &str) -> Result<Self, ControlError> {
        let base = server.trim_end_matches('/');
        let uri: wreq::Uri = base.parse().map_err(|_| ControlError::Request)?;
        let host = uri.host().ok_or(ControlError::Request)?;
        let local = host
            .trim_matches(['[', ']'])
            .parse::<std::net::IpAddr>()
            .is_ok_and(|ip| ip.is_loopback());
        if (uri.scheme_str() != Some("https") && !(local && uri.scheme_str() == Some("http")))
            || uri.query().is_some()
            || base.contains('@')
            || base.contains('#')
            || !matches!(uri.path(), "" | "/")
        {
            return Err(ControlError::Request);
        }
        let client = wreq::Client::builder()
            .redirect(wreq::redirect::Policy::none())
            .retry(wreq::retry::Policy::never())
            .no_proxy()
            .timeout(Duration::from_secs(15))
            .build()
            .map_err(|_| ControlError::Transport)?;
        Ok(Self {
            client,
            endpoint: format!("{base}/api/client-auth/agent/leases/sync"),
            verifier: GrantVerifier::from_pem(public_key)?,
        })
    }
    /// # Errors
    /// HTTP/auth failures never become grants; retries must reuse the same persisted request.
    pub async fn sync(
        &self,
        request: &LeaseSyncRequest,
    ) -> Result<VerifiedGrantBatch, ControlError> {
        request.validate()?;
        let body = serde_json::to_vec(request).map_err(|_| ControlError::Request)?;
        let response = self
            .client
            .post(&self.endpoint)
            .header("content-type", "application/json")
            .body(body)
            .send()
            .await
            .map_err(|_| ControlError::Transport)?;
        if !response.status().is_success() {
            return Err(ControlError::Http(response.status().as_u16()));
        }
        let mut stream = response.bytes_stream();
        let mut bytes = Vec::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk.map_err(|_| ControlError::Transport)?;
            if bytes.len().saturating_add(chunk.len()) > MAX_TOKEN_BYTES {
                return Err(ControlError::Encoding);
            }
            bytes.extend_from_slice(&chunk);
        }
        let data: SignedBatchResponse =
            serde_json::from_slice(&bytes).map_err(|_| ControlError::Encoding)?;
        self.verifier.verify(&data.token, request)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};
    fn fixture() -> serde_json::Value {
        serde_json::from_str(include_str!("../tests/fixtures/account_lease.json")).unwrap()
    }
    fn request() -> LeaseSyncRequest {
        serde_json::from_value(fixture()["request"].clone()).unwrap()
    }
    fn verifier() -> GrantVerifier {
        GrantVerifier::from_pem(fixture()["public_key"].as_str().unwrap()).unwrap()
    }
    fn sign(claims: &serde_json::Value) -> String {
        let header = URL_SAFE_NO_PAD.encode(br#"{"alg":"EdDSA","typ":"JWT"}"#);
        let payload = URL_SAFE_NO_PAD.encode(serde_json::to_vec(claims).unwrap());
        let message = format!("{header}.{payload}");
        let signature = SigningKey::from_bytes(&[7; 32]).sign(message.as_bytes());
        format!("{message}.{}", URL_SAFE_NO_PAD.encode(signature.to_bytes()))
    }
    #[test]
    fn python_signed_grant_is_verified_and_request_bound() {
        let f = fixture();
        let result = verifier()
            .verify_at(f["token"].as_str().unwrap(), &request(), 1_700_000_000_000)
            .unwrap();
        assert_eq!(result.results()[0].fence_epoch, 41);
        assert!(result.allows_manual());
        assert!(result.allows_auto());
        assert!(result.deadline() > Instant::now());
    }
    #[test]
    fn cross_boot_cross_account_and_nonce_replay_are_rejected() {
        let f = fixture();
        let token = f["token"].as_str().unwrap();
        let verify = verifier();
        let mut req = request();
        req.boot_id = Uuid::new_v4();
        assert!(matches!(
            verify.verify_at(token, &req, 1_700_000_000_000),
            Err(ControlError::Binding)
        ));
        let mut req = request();
        req.accounts[0].platform_account_id = "different-account".to_owned();
        assert!(matches!(
            verify.verify_at(token, &req, 1_700_000_000_000),
            Err(ControlError::Binding)
        ));
        let mut req = request();
        req.sequence = 2;
        assert!(matches!(
            verify.verify_at(token, &req, 1_700_000_000_000),
            Err(ControlError::Binding)
        ));
        let mut req = request();
        req.request_id = Uuid::new_v4();
        assert!(matches!(
            verify.verify_at(token, &req, 1_700_000_000_000),
            Err(ControlError::Binding)
        ));
    }
    #[test]
    fn expired_future_and_excessive_ttl_signed_claims_fail_closed() {
        let f = fixture();
        let verify = verifier();
        let req = request();
        assert!(matches!(
            verify.verify_at(f["token"].as_str().unwrap(), &req, 1_700_000_045_000),
            Err(ControlError::Time)
        ));
        assert!(matches!(
            verify.verify_at(f["token"].as_str().unwrap(), &req, 1_699_999_900_000),
            Err(ControlError::Time)
        ));
        let mut claims = f["claims"].clone();
        claims["exp"] = serde_json::json!(1_700_999_999);
        assert!(matches!(
            verify.verify_at(&sign(&claims), &req, 1_700_000_000_000),
            Err(ControlError::Time)
        ));
    }
    #[test]
    fn altered_signature_and_invented_owned_result_are_rejected() {
        let f = fixture();
        let mut token = f["token"].as_str().unwrap().to_owned();
        token.pop();
        token.push('X');
        assert!(verifier()
            .verify_at(&token, &request(), 1_700_000_000_000)
            .is_err());
        let mut claims = f["claims"].clone();
        claims["results"][0]["fence_epoch"] = serde_json::json!(0);
        assert!(matches!(
            verifier().verify_at(&sign(&claims), &request(), 1_700_000_000_000),
            Err(ControlError::Binding)
        ));
    }
    #[test]
    fn duplicate_local_accounts_and_nil_owners_are_invalid_requests() {
        let mut req = request();
        req.accounts.push(req.accounts[0].clone());
        assert!(req.validate().is_err());
        let mut req = request();
        req.instance_id = Uuid::nil();
        assert!(req.validate().is_err());
    }
    #[test]
    fn already_verified_expired_grant_cannot_be_installed_later() {
        let f = fixture();
        let mut batch = verifier()
            .verify_at(f["token"].as_str().unwrap(), &request(), 1_700_000_000_000)
            .unwrap();
        batch.deadline = Instant::now();
        let dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(dir.path()).unwrap();
        assert!(batch.install_owned(&store).iter().all(Result::is_err));
    }
}
