//! Account-isolated credential imports. Secret-bearing containers deliberately
//! have no Debug or Serialize implementation.
use crate::runtime::model::AccountId;
use base64::{
    engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
    Engine,
};
use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use thiserror::Error;

pub const MAX_CREDENTIAL_BYTES: usize = 1024 * 1024;

#[derive(Debug, Error)]
pub enum CredentialError {
    #[error("credential input is invalid or exceeds its bound")]
    Invalid,
    #[error("credential is missing required field: {0}")]
    Missing(&'static str),
    #[error("票据签名与 Cookie 的当前登录会话不匹配，请在同一账号页面刷新后重新复制完整凭证")]
    SessionMismatch,
    #[error("采集不完整：{0}；请在同一浏览器账号初始化主站后重新抓取")]
    CaptureIncomplete(&'static str),
}

#[derive(Clone, Deserialize)]
pub struct CredentialImport {
    pub account_id: String,
    pub user_agent: String,
    #[serde(default)]
    pub expected_sec_uid: String,
    pub storage_state: Value,
}

pub struct AccountCredentials {
    pub captured_web: bool,
    pub browser_profile: Value,
    pub account_id: AccountId,
    pub expected_sec_uid: String,
    pub user_agent: String,
    pub private_key: String,
    pub ticket: String,
    pub ts_sign: String,
    pub client_cert: String,
    pub dtrait_blob: String,
    pub dtrait_header: String,
    pub dtrait_path: String,
    pub query_ms_token: String,
    web_fingerprint: String,
    cookies: BTreeMap<String, String>,
    headers: BTreeMap<String, String>,
}

fn stable_web_bytes(label: &[u8], account: &str, session: &str, key: &str, len: usize) -> Vec<u8> {
    let mut output = Vec::with_capacity(len);
    let mut counter = 0_u32;
    while output.len() < len {
        let mut digest = Sha256::new();
        digest.update(b"dyauthreply-web-fallback-v1\0");
        digest.update(label);
        digest.update(b"\0");
        digest.update(account.as_bytes());
        digest.update(b"\0");
        digest.update(session.as_bytes());
        digest.update(b"\0");
        digest.update(key.as_bytes());
        digest.update(counter.to_be_bytes());
        output.extend_from_slice(&digest.finalize());
        counter = counter.saturating_add(1);
    }
    output.truncate(len);
    output
}

fn stable_ms_token(account: &str, session: &str, key: &str) -> String {
    URL_SAFE_NO_PAD
        .encode(stable_web_bytes(b"ms-token", account, session, key, 95))
        .chars()
        .take(126)
        .collect()
}

fn stable_verify_fp(account: &str, session: &str, key: &str) -> String {
    const ALPHABET: &[u8] = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    let bytes = stable_web_bytes(b"verify-fp", account, session, key, 44);
    let time = bytes[..8].iter().fold(0_u64, |value, byte| {
        value.wrapping_mul(256).wrapping_add(u64::from(*byte))
    });
    let mut stamp = String::new();
    let mut value = time;
    while value > 0 {
        stamp.push(char::from(ALPHABET[(value % 36) as usize]));
        value /= 36;
    }
    let stamp: String = stamp.chars().rev().collect();
    let mut body = String::with_capacity(36);
    for index in 0..36 {
        body.push(match index {
            8 | 13 | 18 | 23 => '_',
            14 => '4',
            19 => char::from(ALPHABET[usize::from((bytes[index] & 3) | 8)]),
            _ => char::from(ALPHABET[usize::from(bytes[index]) % ALPHABET.len()]),
        });
    }
    format!("verify_{stamp}_{body}")
}

fn header_cookie(raw: &str, name: &str) -> Option<String> {
    raw.split(';')
        .filter_map(|part| part.trim().split_once('='))
        .rfind(|(cookie, value)| *cookie == name && !value.is_empty())
        .map(|(_, value)| value.to_owned())
}

pub(crate) fn valid_query_token(value: &str) -> bool {
    (32..=8192).contains(&value.len())
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-+/=".contains(&b))
}

fn import_cookie_entries(raw: &str, scoped_capture: bool) -> Vec<Value> {
    let pairs = raw.split(';').filter_map(|p| p.trim().split_once('='));
    // Browser Cookie headers legally contain host/path variants of a name.
    // Keep the exact scoped wire header separately; only the compatibility map
    // is unique. V2 raw session and ticket checks run before this conversion.
    let pairs: Vec<_> = if scoped_capture {
        pairs.collect::<BTreeMap<_, _>>().into_iter().collect()
    } else {
        pairs.collect()
    };
    pairs
        .into_iter()
        .map(|(name, value)| serde_json::json!({"name":name,"value":value}))
        .collect()
}

fn resolve_captured_token(state: &Value, fallback: String) -> Result<String, CredentialError> {
    if let Some(captured) = state["_query_ms_token"]["value"]
        .as_str()
        .filter(|s| !s.is_empty())
    {
        if !valid_query_token(captured) {
            return Err(CredentialError::Invalid);
        }
        Ok(captured.to_owned())
    } else {
        Ok(fallback)
    }
}

fn merge_capture_metadata(state: &mut Value, value: &Value) -> Result<(), CredentialError> {
    if let Some(token) = value
        .get("query_ms_token")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
    {
        if !valid_query_token(token) {
            return Err(CredentialError::CaptureIncomplete("SDK Token 格式无效"));
        }
        state["_query_ms_token"] = serde_json::json!({"value":token,
                "captured_at_ms":value["query_ms_token_captured_at_ms"],"source":"browser_network"});
    }
    if value["capture_version"] == 2 {
        state["_capture_version"] = serde_json::json!(2);
        state["_browser_profile"] = value["browser_profile"].clone();
    }
    Ok(())
}

fn validate_browser_capture(value: &Value) -> Result<(), CredentialError> {
    if value["capture_version"] != 2 {
        return Ok(());
    }
    let headers = &value["cookie_headers"];
    let www = headers["www.douyin.com"].as_str().unwrap_or("");
    if value["cookie"].as_str() != Some(www) {
        return Err(CredentialError::CaptureIncomplete("主站Cookie快照不一致"));
    }
    let session = header_cookie(www, "sessionid")
        .or_else(|| header_cookie(www, "sessionid_ss"))
        .ok_or(CredentialError::CaptureIncomplete("主站登录会话"))?;
    for host in ["creator.douyin.com", "imapi.douyin.com"] {
        let raw = headers[host].as_str().unwrap_or("");
        let other = header_cookie(raw, "sessionid").or_else(|| header_cookie(raw, "sessionid_ss"));
        if other.as_deref() != Some(session.as_str()) {
            return Err(CredentialError::SessionMismatch);
        }
    }
    for host in ["www.douyin.com", "creator.douyin.com", "imapi.douyin.com"] {
        let raw = headers[host].as_str().unwrap_or("");
        for (name, val) in raw.split(';').filter_map(|p| p.trim().split_once('=')) {
            if matches!(name, "sessionid" | "sessionid_ss" | "sid_tt") && val != session {
                return Err(CredentialError::SessionMismatch);
            }
        }
    }
    if header_cookie(www, "UIFID").is_none() {
        return Err(CredentialError::CaptureIncomplete("主站 UIFID"));
    }
    if header_cookie(www, "s_v_web_id").is_none() {
        return Err(CredentialError::CaptureIncomplete("主站 s_v_web_id"));
    }
    if !valid_query_token(value["query_ms_token"].as_str().unwrap_or("")) {
        return Err(CredentialError::CaptureIncomplete("浏览器 SDK Token"));
    }
    let keys = decode_object(value.get("keys").unwrap_or(&Value::Null))?;
    let private = keys["ec_privateKey"]
        .as_str()
        .or_else(|| keys["private_key"].as_str())
        .unwrap_or("");
    let raw = value["ticket_guard_server_data"]
        .as_str()
        .filter(|s| !s.is_empty())
        .or_else(|| value["web_protect"].as_str())
        .unwrap_or("");
    let ticket = decode_ticket(raw)?;
    let cert = ticket["client_cert"]
        .as_str()
        .or_else(|| ticket["sdk_cert"].as_str())
        .unwrap_or("");
    let public = super::native_signer::ree_public_key(private)
        .map_err(|_| CredentialError::CaptureIncomplete("主站签名私钥"))?;
    if cert.strip_prefix("pub.") != Some(public.as_str()) {
        return Err(CredentialError::CaptureIncomplete("票据和私钥不配套"));
    }
    Ok(())
}

fn web_identity(
    cookies: &BTreeMap<String, String>,
    headers: &BTreeMap<String, String>,
    account: &str,
    key: &str,
) -> (String, String) {
    let session = cookies
        .get("sessionid")
        .or_else(|| cookies.get("sessionid_ss"))
        .map_or("", String::as_str);
    let token = cookies
        .get("msToken")
        .or_else(|| cookies.get("msToken_ss"))
        .cloned()
        .unwrap_or_else(|| stable_ms_token(account, session, key));
    // The signed www request must use the fingerprint from the Cookie header
    // it actually sends, not a stale creator-domain cookie in the flattened map.
    let fingerprint = headers
        .get("www.douyin.com")
        .and_then(|raw| header_cookie(raw, "s_v_web_id"))
        .or_else(|| cookies.get("s_v_web_id").cloned())
        .or_else(|| {
            ["creator.douyin.com", "imapi.douyin.com"]
                .into_iter()
                .find_map(|host| {
                    headers
                        .get(host)
                        .and_then(|raw| header_cookie(raw, "s_v_web_id"))
                })
        })
        .unwrap_or_else(|| stable_verify_fp(account, session, key));
    (token, fingerprint)
}

fn bind_fingerprint_cookie(
    cookies: &mut BTreeMap<String, String>,
    headers: &mut BTreeMap<String, String>,
    fingerprint: &str,
) {
    cookies
        .entry("s_v_web_id".to_owned())
        .or_insert_with(|| fingerprint.to_owned());
    for host in ["www.douyin.com", "imapi.douyin.com"] {
        if let Some(raw) = headers.get_mut(host) {
            if header_cookie(raw, "s_v_web_id").is_none() {
                raw.push_str("; s_v_web_id=");
                raw.push_str(fingerprint);
            }
        }
    }
}

fn string(value: &Value, field: &str) -> String {
    value
        .get(field)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}
fn text_ok(text: &str, max: usize) -> bool {
    text.len() <= max && !text.chars().any(char::is_control)
}

fn validate_ticket_session(result: &AccountCredentials) -> Result<(), CredentialError> {
    // Stored snapshots were accepted by older clients without the modern cookie
    // check. Keep them readable so a single retired/bad account cannot prevent
    // startup or deletion. New imports and send plans use the strict check below.
    let legacy = result.cookie("www.douyin.com", "bd_ticket_guard_ts_sign_id");
    if !legacy.is_empty()
        && !result.ts_sign.is_empty()
        && !result.ts_sign.starts_with(&legacy)
        && !ticket_cookie_matches(&result.cookie_header("www.douyin.com"), &result.ts_sign)
    {
        return Err(CredentialError::SessionMismatch);
    }
    Ok(())
}

/// Current SDK cookies carry the complete signed value. A legacy short ID may
/// survive a later login, so compare the full value first, without rewriting
/// the Cookie header or accepting conflicting modern values.
pub(super) fn ticket_cookie_matches(raw_cookie: &str, ts_sign: &str) -> bool {
    if ts_sign.is_empty() {
        return true; // Cookie-only receive imports do not contain a ticket yet.
    }
    let mut saw_current = false;
    for (_, raw) in raw_cookie
        .split(';')
        .filter_map(|p| p.trim().split_once('='))
        .filter(|(name, _)| *name == "bd_ticket_guard_tssign")
    {
        saw_current = true;
        let Ok(decoded) = percent_decode(raw) else {
            return false;
        };
        let current = if decoded.starts_with("ts.") {
            Some(decoded.clone())
        } else {
            serde_json::from_str::<Value>(&decoded).ok().and_then(|v| {
                v.as_str()
                    .or_else(|| v.get("value").and_then(Value::as_str))
                    .map(str::to_owned)
            })
        };
        if current.as_deref() != Some(ts_sign) {
            return false;
        }
    }
    if saw_current {
        return true;
    }
    header_cookie(raw_cookie, "bd_ticket_guard_ts_sign_id")
        .is_none_or(|id| ts_sign.starts_with(&id))
}

fn validate_incoming_ticket(state: &Value) -> Result<(), CredentialError> {
    // Tightening validation applies to newly supplied material, not old rows.
    let raw_cookie = state["_cookie_headers"]["www.douyin.com"]
        .as_str()
        .filter(|value| !value.is_empty())
        .map_or_else(
            || {
                state["cookies"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(|c| {
                        Some(format!("{}={}", c["name"].as_str()?, c["value"].as_str()?))
                    })
                    .collect::<Vec<_>>()
                    .join("; ")
            },
            str::to_owned,
        );
    if !ticket_cookie_matches(
        &raw_cookie,
        state["_bd_ticket"]["ts_sign"].as_str().unwrap_or(""),
    ) {
        return Err(CredentialError::SessionMismatch);
    }
    Ok(())
}

fn merge_dtrait(state: &mut Value, value: &Value) {
    let mut dtrait = state["_dtrait"].clone();
    if !dtrait.is_object() {
        dtrait = serde_json::json!({});
    }
    for (key, field) in [
        ("dtrait_blob", "blob"),
        ("session_dtrait", "header"),
        ("session_dtrait_path", "path"),
    ] {
        if let Some(v) = value
            .get(key)
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
        {
            dtrait[field] = serde_json::json!(v);
        }
    }
    state["_dtrait"] = dtrait;
}

fn decode_object(value: &Value) -> Result<Value, CredentialError> {
    let mut value = value.clone();
    for _ in 0..3 {
        if value.is_null() || value.as_str() == Some("") {
            return Ok(serde_json::json!({}));
        }
        if let Some(text) = value.as_str() {
            value = serde_json::from_str(text).map_err(|_| CredentialError::Invalid)?;
            continue;
        }
        if let Some(data) = value.get("data").filter(|v| v.is_object() || v.is_string()) {
            value = data.clone();
            continue;
        }
        return if value.is_object() {
            Ok(value)
        } else {
            Err(CredentialError::Invalid)
        };
    }
    if value.is_object() {
        Ok(value)
    } else {
        Err(CredentialError::Invalid)
    }
}
fn decode_ticket(raw: &str) -> Result<Value, CredentialError> {
    let raw = percent_decode(raw)?;
    if let Ok(value) = serde_json::from_str::<Value>(&raw) {
        return decode_object(&value);
    }
    let bytes = URL_SAFE_NO_PAD
        .decode(raw.trim_end_matches('='))
        .or_else(|_| STANDARD.decode(&raw))
        .map_err(|_| CredentialError::Invalid)?;
    decode_object(&serde_json::from_slice::<Value>(&bytes).map_err(|_| CredentialError::Invalid)?)
}
fn percent_decode(raw: &str) -> Result<String, CredentialError> {
    let mut bytes = Vec::with_capacity(raw.len());
    let data = raw.as_bytes();
    let mut i = 0;
    while i < data.len() {
        if data[i] == b'%' {
            if i + 2 >= data.len() {
                return Err(CredentialError::Invalid);
            }
            let hex =
                std::str::from_utf8(&data[i + 1..i + 3]).map_err(|_| CredentialError::Invalid)?;
            bytes.push(u8::from_str_radix(hex, 16).map_err(|_| CredentialError::Invalid)?);
            i += 3;
        } else {
            bytes.push(data[i]);
            i += 1;
        }
    }
    String::from_utf8(bytes).map_err(|_| CredentialError::Invalid)
}

impl AccountCredentials {
    #[must_use]
    pub fn binding_digest(&self) -> String {
        use sha2::{Digest, Sha256};
        format!(
            "{:x}",
            Sha256::digest(
                format!(
                    "{}\0{}\0{}\0{}\0{}\0{}",
                    if self.captured_web {
                        "native-binding-captured-web-v1"
                    } else {
                        "native-binding-v2"
                    },
                    self.account_id,
                    self.expected_sec_uid,
                    self.cookie_header("www.douyin.com"),
                    self.private_key,
                    self.user_agent,
                )
                .as_bytes()
            )
        )
    }

    /// Canonical one-time legacy migration format; runtime never calls Python.
    /// # Errors
    /// Rejects malformed, oversized or cross-session data without echoing it.
    pub fn import_json(bytes: &[u8]) -> Result<Self, CredentialError> {
        if bytes.len() > MAX_CREDENTIAL_BYTES {
            return Err(CredentialError::Invalid);
        }
        let input: CredentialImport =
            serde_json::from_slice(bytes).map_err(|_| CredentialError::Invalid)?;
        Self::from_state(input)
    }

    /// Native support for the existing browser extension import string.
    /// # Errors
    /// Rejects unrecognized package shapes or invalid account material.
    pub fn import_extension(account_id: String, package: &str) -> Result<Self, CredentialError> {
        Self::from_state(Self::extension_input(account_id, package)?)
    }
    /// # Errors
    /// Decodes the extension format for encrypted persistence; never use this as a public export API.
    pub fn extension_input(
        account_id: String,
        package: &str,
    ) -> Result<CredentialImport, CredentialError> {
        Self::extension_input_with_base(account_id, package, None)
    }
    /// # Errors
    /// Partial same-session SDK updates retain existing material; a new session clears it first.
    pub fn extension_input_with_base(
        account_id: String,
        package: &str,
        base: Option<&CredentialImport>,
    ) -> Result<CredentialImport, CredentialError> {
        if package.len() > MAX_CREDENTIAL_BYTES {
            return Err(CredentialError::Invalid);
        }
        let raw = package
            .strip_prefix("DYCRED1.")
            .ok_or(CredentialError::Invalid)?;
        let bytes = URL_SAFE_NO_PAD
            .decode(raw.trim_end_matches('='))
            .map_err(|_| CredentialError::Invalid)?;
        let value: Value = serde_json::from_slice(&bytes).map_err(|_| CredentialError::Invalid)?;
        Self::input_from_fields(account_id, &value, base)
    }
    /// # Errors
    /// Merges partial legacy fields without carrying session-bound signing data across cookie changes.
    pub fn input_from_fields(
        account_id: String,
        value: &Value,
        base: Option<&CredentialImport>,
    ) -> Result<CredentialImport, CredentialError> {
        validate_browser_capture(value)?;
        let mut state = base.map_or_else(|| serde_json::json!({}), |v| v.storage_state.clone());
        let cookie = string(value, "cookie");
        let cookie_changed = !cookie.is_empty()
            && base.is_some_and(|old| {
                Self::from_state(old.clone()).is_ok_and(|c| {
                    let previous = c.cookie("www.douyin.com", "sessionid");
                    cookie
                        .split(';')
                        .filter_map(|p| p.trim().split_once('='))
                        .find(|(k, _)| *k == "sessionid")
                        .is_some_and(|(_, v)| v != previous)
                })
            });
        if cookie_changed {
            state["_bd_ticket"] = serde_json::json!({});
            state["_dtrait"] = serde_json::json!({});
            state["_cookie_headers"] = serde_json::json!({});
        }
        let keys = decode_object(value.get("keys").unwrap_or(&Value::Null))?;
        let server_raw = string(value, "ticket_guard_server_data");
        let server_raw = if server_raw.is_empty() {
            string(value, "web_protect")
        } else {
            server_raw
        };
        let mut ticket = state["_bd_ticket"].clone();
        if !ticket.is_object() {
            ticket = serde_json::json!({});
        }
        if !server_raw.is_empty() {
            let parsed = decode_ticket(&server_raw)?;
            if let Some(map) = parsed.as_object() {
                ticket
                    .as_object_mut()
                    .ok_or(CredentialError::Invalid)?
                    .extend(map.clone());
            }
        }
        if !ticket.is_object() {
            ticket = serde_json::json!({});
        }
        if ticket["ticket"].as_str().is_none_or(str::is_empty) {
            ticket["ticket"] = ticket["token"].clone();
        }
        if ticket["client_cert"].as_str().is_none_or(str::is_empty) {
            ticket["client_cert"] = ticket["sdk_cert"].clone();
        }
        if let Some(key) = keys
            .get("ec_privateKey")
            .or_else(|| keys.get("private_key"))
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
        {
            ticket["private_key"] = serde_json::json!(key);
        }
        state["_bd_ticket"] = ticket;
        let cookie = string(value, "cookie");
        if !cookie.is_empty() {
            state["_query_ms_token"] = Value::Null;
            let cookies = import_cookie_entries(&cookie, value["capture_version"] == 2);
            state["cookies"] = serde_json::json!(cookies);
            // A new full cookie header replaces old host snapshots unless explicitly refreshed too.
            state["_cookie_headers"] = value
                .get("cookie_headers")
                .cloned()
                .unwrap_or_else(|| serde_json::json!({}));
        }
        merge_capture_metadata(&mut state, value)?;
        merge_dtrait(&mut state, value);
        validate_incoming_ticket(&state)?;
        let ua = value
            .get("ua")
            .or_else(|| value.get("user_agent"))
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
            .map(str::to_owned)
            .or_else(|| base.map(|v| v.user_agent.clone()))
            .unwrap_or_default();
        let scope = value
            .get("sec_uid")
            .and_then(Value::as_str)
            .filter(|v| !v.is_empty())
            .map(str::to_owned)
            .or_else(|| base.map(|v| v.expected_sec_uid.clone()))
            .unwrap_or_default();
        Ok(CredentialImport {
            account_id,
            user_agent: ua,
            expected_sec_uid: scope,
            storage_state: state,
        })
    }

    pub(crate) fn from_state(input: CredentialImport) -> Result<Self, CredentialError> {
        let account_id = AccountId::new(input.account_id).map_err(|_| CredentialError::Invalid)?;
        if input.user_agent.is_empty() || !text_ok(&input.user_agent, 2048) {
            return Err(CredentialError::Missing("user_agent"));
        }
        if !text_ok(&input.expected_sec_uid, 256) {
            return Err(CredentialError::Invalid);
        }
        let state = input.storage_state;
        let mut cookies = BTreeMap::new();
        let entries = state["cookies"]
            .as_array()
            .ok_or(CredentialError::Missing("cookies"))?;
        if entries.len() > 512 {
            return Err(CredentialError::Invalid);
        }
        for item in entries {
            let name = string(item, "name");
            let value = string(item, "value");
            if name.is_empty() || !text_ok(&name, 256) || !text_ok(&value, 32768) {
                return Err(CredentialError::Invalid);
            }
            if cookies.insert(name, value).is_some() {
                return Err(CredentialError::Invalid);
            }
        }
        let mut headers = BTreeMap::new();
        if let Some(values) = state["_cookie_headers"].as_object() {
            for host in ["www.douyin.com", "imapi.douyin.com", "creator.douyin.com"] {
                if let Some(value) = values
                    .get(host)
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                {
                    if !text_ok(value, 65536) {
                        return Err(CredentialError::Invalid);
                    }
                    headers.insert(host.to_owned(), value.to_owned());
                }
            }
        }
        let (ticket, dtrait) = (&state["_bd_ticket"], &state["_dtrait"]);
        let private_key = string(ticket, "private_key");
        let (mut token, web_fingerprint) =
            web_identity(&cookies, &headers, account_id.as_str(), &private_key);
        token = resolve_captured_token(&state, token)?;
        // verifyFp/fp and the Cookie must describe the same browser identity.
        // Chromium keeps this host-scoped and quick-auth can observe it first
        // on creator.douyin.com, so bind that captured value into the www/imapi
        // request snapshots instead of signing a query with a cookie-absent ID.
        if state["_capture_version"] != 2 {
            bind_fingerprint_cookie(&mut cookies, &mut headers, &web_fingerprint);
        }
        let result = Self {
            captured_web: state["_capture_version"] == 2,
            browser_profile: state["_browser_profile"].clone(),
            account_id,
            expected_sec_uid: input.expected_sec_uid,
            user_agent: input.user_agent,
            cookies,
            headers,
            query_ms_token: token,
            web_fingerprint,
            private_key,
            ticket: string(ticket, "ticket"),
            ts_sign: string(ticket, "ts_sign"),
            client_cert: string(ticket, "client_cert"),
            dtrait_blob: string(dtrait, "blob"),
            dtrait_header: string(dtrait, "header"),
            dtrait_path: string(dtrait, "path"),
        };
        if result.cookie("www.douyin.com", "sessionid").is_empty() {
            return Err(CredentialError::Missing("sessionid"));
        }
        for (name, value) in [
            ("ticket", &result.ticket),
            ("ts_sign", &result.ts_sign),
            ("client_cert", &result.client_cert),
        ] {
            if !text_ok(value, 16384) {
                let _ = name;
                return Err(CredentialError::Invalid);
            }
        }
        if result.private_key.len() > 16384
            || result
                .private_key
                .chars()
                .any(|c| c.is_control() && c != '\n' && c != '\r')
        {
            return Err(CredentialError::Invalid);
        }
        if !text_ok(&result.dtrait_blob, 65536)
            || !text_ok(&result.dtrait_header, 131_072)
            || !text_ok(&result.dtrait_path, 512)
        {
            return Err(CredentialError::Invalid);
        }
        validate_ticket_session(&result)?;
        Ok(result)
    }

    #[must_use]
    pub fn cookie_header(&self, host: &str) -> String {
        self.headers.get(host).cloned().unwrap_or_else(|| {
            self.cookies
                .iter()
                .map(|(k, v)| format!("{k}={v}"))
                .collect::<Vec<_>>()
                .join("; ")
        })
    }
    #[must_use]
    pub fn cookie(&self, host: &str, name: &str) -> String {
        self.cookie_header(host)
            .split(';')
            .filter_map(|part| part.trim().split_once('='))
            .rfind(|(key, _)| *key == name)
            .map(|(_, v)| v.to_owned())
            .unwrap_or_default()
    }
    #[must_use]
    pub fn web_fingerprint(&self) -> &str {
        &self.web_fingerprint
    }
    #[must_use]
    pub fn has_signing_material(&self) -> bool {
        !self.private_key.is_empty() && !self.ticket.is_empty() && !self.ts_sign.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_extension_import_keeps_session_material() {
        let server = STANDARD
            .encode(br#"{"ticket":"ticket","ts_sign":"ts.1.test","client_cert":"pub.test"}"#);
        let package = serde_json::json!({"cookie":"sessionid=synthetic; bd_ticket_guard_ts_sign_id=ts.1",
            "cookie_headers":{"www.douyin.com":"sessionid=synthetic; bd_ticket_guard_ts_sign_id=ts.1"},
            "keys":"{\"ec_privateKey\":\"1\"}","ticket_guard_server_data":server,"ua":"Chrome/131.0",
            "session_dtrait":"captured","session_dtrait_path":"/passport/safe/get_identity_security_token/"});
        let encoded = format!(
            "DYCRED1.{}",
            URL_SAFE_NO_PAD.encode(serde_json::to_vec(&package).unwrap())
        );
        let account = AccountCredentials::import_extension("a".to_owned(), &encoded).unwrap();
        assert!(account.has_signing_material());
        assert_eq!(account.private_key, "1");
        assert_eq!(account.dtrait_header, "captured");
    }

    #[test]
    fn scoped_headers_and_query_token_stay_independent() {
        let bytes=br#"{"account_id":"a","user_agent":"Chrome/131.0","storage_state":{"cookies":[{"name":"sessionid","value":"base"}],"_cookie_headers":{"www.douyin.com":"sessionid=www; exact=1", "imapi.douyin.com":"sessionid=im"},"_bd_ticket":{}}}"#;
        let c = AccountCredentials::import_json(bytes).unwrap();
        assert_eq!(c.cookie("www.douyin.com", "sessionid"), "www");
        assert_eq!(c.cookie("imapi.douyin.com", "sessionid"), "im");
        assert_eq!(c.query_ms_token.len(), 126);
        assert!(c
            .cookie_header("www.douyin.com")
            .starts_with("sessionid=www; exact=1; s_v_web_id=verify_"));
        assert!(c.web_fingerprint().starts_with("verify_"));
        assert!(!c.has_signing_material());
    }
    #[test]
    fn missing_web_identity_fallbacks_are_stable_and_bind_query_to_cookie() {
        let bytes=br#"{"account_id":"a","user_agent":"Chrome/151.0","storage_state":{"cookies":[{"name":"sessionid","value":"stable"}],"_cookie_headers":{"www.douyin.com":"sessionid=stable; exact=1"},"_bd_ticket":{"private_key":"1","ticket":"ticket","ts_sign":"ts.2.test","client_cert":"pub.test"}}}"#;
        let first = AccountCredentials::import_json(bytes).unwrap();
        let second = AccountCredentials::import_json(bytes).unwrap();
        assert_eq!(first.query_ms_token, second.query_ms_token);
        assert_eq!(first.query_ms_token.len(), 126);
        assert_eq!(first.web_fingerprint(), second.web_fingerprint());
        assert!(first.web_fingerprint().starts_with("verify_"));
        assert_eq!(
            first.cookie("www.douyin.com", "s_v_web_id"),
            first.web_fingerprint()
        );
        assert!(first
            .cookie_header("www.douyin.com")
            .starts_with("sessionid=stable; exact=1; s_v_web_id=verify_"));
    }

    #[test]
    fn creator_captured_fingerprint_is_reused_for_www_and_imapi() {
        let bytes=br#"{"account_id":"a","user_agent":"Chrome/152.0","storage_state":{"cookies":[{"name":"sessionid","value":"stable"}],"_cookie_headers":{"www.douyin.com":"sessionid=stable","imapi.douyin.com":"sessionid=stable","creator.douyin.com":"sessionid=stable; s_v_web_id=verify_creator_capture"},"_bd_ticket":{}}}"#;
        let credentials = AccountCredentials::import_json(bytes).unwrap();
        assert_eq!(credentials.web_fingerprint(), "verify_creator_capture");
        assert_eq!(
            credentials.cookie("www.douyin.com", "s_v_web_id"),
            "verify_creator_capture"
        );
        assert_eq!(
            credentials.cookie("imapi.douyin.com", "s_v_web_id"),
            "verify_creator_capture"
        );
    }

    #[test]
    fn send_observation_binding_includes_imported_browser_identity() {
        let first = AccountCredentials::import_json(
            br#"{"account_id":"a","expected_sec_uid":"self","user_agent":"Chrome/151.0","storage_state":{"cookies":[{"name":"sessionid","value":"stable"}],"_bd_ticket":{}}}"#,
        )
        .unwrap();
        let second = AccountCredentials::import_json(
            br#"{"account_id":"a","expected_sec_uid":"self","user_agent":"Chrome/152.0","storage_state":{"cookies":[{"name":"sessionid","value":"stable"}],"_bd_ticket":{}}}"#,
        )
        .unwrap();
        assert_ne!(first.binding_digest(), second.binding_digest());
        assert_eq!(first.binding_digest(), first.binding_digest());
    }
    #[test]
    fn cookie_only_bundle_and_partial_update_do_not_carry_old_session_signing_material() {
        let id = "a".to_string();
        let original = serde_json::json!({"cookie":"sessionid=old","ua":"Chrome/151.0","sec_uid":"self","web_protect":"{\"ticket\":\"old-ticket\",\"ts_sign\":\"old-sign\"}","keys":"{\"ec_privateKey\":\"old-key\"}"});
        let base = AccountCredentials::input_from_fields(id.clone(), &original, None).unwrap();
        let same = AccountCredentials::input_from_fields(
            id.clone(),
            &serde_json::json!({"keys":"{\"private_key\":\"new-key\"}"}),
            Some(&base),
        )
        .unwrap();
        assert_eq!(same.storage_state["_bd_ticket"]["ticket"], "old-ticket");
        let changed = AccountCredentials::input_from_fields(
            id,
            &serde_json::json!({"cookie":"sessionid=new"}),
            Some(&base),
        )
        .unwrap();
        assert!(changed.storage_state["_bd_ticket"]["ticket"].is_null());
        let package = format!(
            "DYCRED1.{}",
            URL_SAFE_NO_PAD
                .encode(br#"{"cookie":"sessionid=test","ua":"Chrome/151.0","sec_uid":"self"}"#)
        );
        let credentials = AccountCredentials::import_extension("a".into(), &package).unwrap();
        assert!(!credentials.has_signing_material());
    }
}

#[cfg(test)]
mod ticket_rotation_regression {
    use super::*;
    fn input(short_id: &str, current: Option<&str>) -> Vec<u8> {
        let mut cookies = vec![
            serde_json::json!({"name":"sessionid","value":"synthetic-session"}),
            serde_json::json!({"name":"bd_ticket_guard_ts_sign_id","value":short_id}),
        ];
        if let Some(current) = current {
            let encoded = crate::protocol::http_plan::percent_encode_rfc3986(
                &serde_json::json!({"value":current,"timestamp":1_700_000_000_000_u64}).to_string(),
            );
            cookies.push(serde_json::json!({"name":"bd_ticket_guard_tssign","value":encoded}));
        }
        serde_json::to_vec(&serde_json::json!({"account_id":"a","user_agent":"Chrome/155.0","storage_state":{"cookies":cookies,"_bd_ticket":{"ticket":"synthetic-ticket","ts_sign":"ts.2.current+signature","private_key":"synthetic-key"}}})).unwrap()
    }
    #[test]
    fn import_delete_current_full_signature_supersedes_stale_short_id() {
        assert!(AccountCredentials::import_json(&input(
            "ts.2.old-id",
            Some("ts.2.current+signature")
        ))
        .is_ok());
    }
    #[test]
    fn import_delete_new_conflicting_full_signature_is_rejected_but_old_rows_remain_readable() {
        let stored = input("ts.2", Some("ts.2.other-signature"));
        assert!(AccountCredentials::import_json(&stored).is_ok());
        let value = serde_json::json!({"cookie":"sessionid=synthetic-session; bd_ticket_guard_ts_sign_id=ts.2; bd_ticket_guard_tssign=ts.2.other-signature","ua":"Chrome/155.0","web_protect":"{\"ticket\":\"synthetic-ticket\",\"ts_sign\":\"ts.2.current+signature\"}"});
        assert!(matches!(
            AccountCredentials::input_from_fields("a".into(), &value, None),
            Err(CredentialError::SessionMismatch)
        ));
    }

    #[test]
    fn import_delete_retired_snapshot_without_legacy_id_remains_readable() {
        assert!(AccountCredentials::import_json(&input("", Some("ts.2.stale-snapshot"))).is_ok());
    }

    #[test]
    fn import_delete_malformed_or_conflicting_current_cookies_fail_closed() {
        for raw in [
            "%ZZ",
            "null",
            "%7B%22value%22%3A3%7D",
            "ts.2.current+signature; bd_ticket_guard_tssign=ts.2.other",
        ] {
            assert!(!ticket_cookie_matches(
                &format!("bd_ticket_guard_tssign={raw}"),
                "ts.2.current+signature"
            ));
        }
        assert!(!ticket_cookie_matches(
            "bd_ticket_guard_ts_sign_id=ts.2.old; bd_ticket_guard_ts_sign_id=",
            "ts.2.current"
        ));
    }

    #[test]
    fn import_delete_scoped_current_cookie_handles_duplicate_legacy_ids() {
        let mut document: Value = serde_json::from_slice(&input("ts.2.old", None)).unwrap();
        document["storage_state"]["_cookie_headers"] = serde_json::json!({"www.douyin.com":"sessionid=synthetic-session; bd_ticket_guard_ts_sign_id=ts.2.stale-host; bd_ticket_guard_ts_sign_id=ts.2.stale-parent; bd_ticket_guard_tssign=ts.2.current+signature"});
        assert!(AccountCredentials::import_json(&serde_json::to_vec(&document).unwrap()).is_ok());
    }

    #[test]
    fn import_delete_www_fingerprint_wins_over_stale_creator_snapshot() {
        let raw = serde_json::json!({"account_id":"a","user_agent":"Chrome/155.0","storage_state":{"cookies":[{"name":"sessionid","value":"synthetic"},{"name":"s_v_web_id","value":"verify_creator_old"}],"_cookie_headers":{"www.douyin.com":"sessionid=synthetic; s_v_web_id=verify_www_current","imapi.douyin.com":"sessionid=synthetic"}}});
        let credentials =
            AccountCredentials::import_json(&serde_json::to_vec(&raw).unwrap()).unwrap();
        assert_eq!(credentials.web_fingerprint(), "verify_www_current");
        assert_eq!(
            credentials.cookie_header("www.douyin.com"),
            "sessionid=synthetic; s_v_web_id=verify_www_current"
        );
        assert_eq!(
            credentials.cookie("imapi.douyin.com", "s_v_web_id"),
            "verify_www_current"
        );
    }

    #[test]
    fn import_delete_legacy_mismatch_still_rejected() {
        assert!(matches!(
            AccountCredentials::import_json(&input("ts.2.old-id", None)),
            Err(CredentialError::SessionMismatch)
        ));
        assert!(AccountCredentials::import_json(&input("ts.2", None)).is_ok());
    }
}

#[cfg(test)]
mod capture_v2_tests {
    use super::*;
    fn complete() -> Value {
        let fixture: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/native_signing.json")).unwrap();
        let raw = "sessionid=synthetic; sessionid_ss=synthetic; sid_tt=synthetic; UIFID=real-browser-field; s_v_web_id=verify_browser; bd_ticket_guard_tssign=ts.2.synthetic";
        serde_json::json!({"capture_version":2,"cookie":raw,"cookie_headers":{"www.douyin.com":raw,"creator.douyin.com":raw,"imapi.douyin.com":raw},"ua":"Chrome/152.0","query_ms_token":"S".repeat(164),"keys":{"ec_privateKey":fixture["private_key"]},"ticket_guard_server_data":serde_json::json!({"ticket":"synthetic","ts_sign":"ts.2.synthetic","client_cert":format!("pub.{}",fixture["ree_public"].as_str().unwrap())}).to_string()})
    }
    #[test]
    fn creator_channel_is_bound_to_capture_and_does_not_restore_www_send_state() {
        let document =
            AccountCredentials::input_from_fields("a".into(), &complete(), None).unwrap();
        let creator = AccountCredentials::from_state(document.clone()).unwrap();
        assert!(creator.captured_web);
        let mut legacy = document;
        legacy.storage_state["_capture_version"] = Value::Null;
        let web = AccountCredentials::from_state(legacy).unwrap();
        assert!(!web.captured_web);
        assert_ne!(creator.binding_digest(), web.binding_digest());
    }
    #[test]
    fn capture_v2_complete_passes_and_conflicts_reject() {
        assert!(AccountCredentials::input_from_fields("a".into(), &complete(), None).is_ok());
        for (field, value) in [
            ("query_ms_token", Value::String("bad\nvalue".into())),
            ("keys", serde_json::json!({"ec_privateKey":"invalid"})),
        ] {
            let mut raw = complete();
            raw[field] = value;
            assert!(AccountCredentials::input_from_fields("a".into(), &raw, None).is_err());
        }
        for host in ["www.douyin.com", "creator.douyin.com", "imapi.douyin.com"] {
            let mut raw = complete();
            let h = raw["cookie_headers"][host].as_str().unwrap().to_owned();
            raw["cookie_headers"][host] = Value::String(format!("{h}; sid_tt=other"));
            assert!(AccountCredentials::input_from_fields("a".into(), &raw, None).is_err());
        }
        let mut raw = complete();
        raw["ticket_guard_server_data"] = Value::String(
            "{\"ticket\":\"synthetic\",\"ts_sign\":\"ts.2.other\",\"client_cert\":\"pub.other\"}"
                .into(),
        );
        assert!(AccountCredentials::input_from_fields("a".into(), &raw, None).is_err());
    }
    #[test]
    fn full_cookie_replacement_clears_previous_sdk_token() {
        let previous =
            AccountCredentials::input_from_fields("a".into(), &complete(), None).unwrap();
        let next = serde_json::json!({"cookie":"sessionid=new-session","ua":"Chrome/152.0","web_protect":"{}","keys":"{}"});
        let updated =
            AccountCredentials::input_from_fields("a".into(), &next, Some(&previous)).unwrap();
        assert!(updated.storage_state["_query_ms_token"].is_null());
    }
    #[test]
    fn capture_v2_scoped_duplicate_names_preserve_wire_header_and_import() {
        let mut raw = complete();
        let cookie = format!("{}; bd_ticket_guard_ts_sign_id=ts.2.old-host; bd_ticket_guard_ts_sign_id=ts.2.old-parent",raw["cookie"].as_str().unwrap());
        raw["cookie"] = serde_json::json!(cookie);
        raw["cookie_headers"]["www.douyin.com"] = serde_json::json!(cookie);
        let state = AccountCredentials::input_from_fields("a".into(), &raw, None).unwrap();
        let credentials = AccountCredentials::from_state(state).unwrap();
        assert_eq!(credentials.cookie_header("www.douyin.com"), cookie);
    }
    #[test]
    fn captured_query_token_survives_import_without_entering_cookie_header() {
        let value = serde_json::json!({"cookie":"sessionid=synthetic","cookie_headers":{"www.douyin.com":"sessionid=synthetic"},"ua":"Chrome/152.0","query_ms_token":"S".repeat(164)});
        let state = AccountCredentials::input_from_fields("a".into(), &value, None).unwrap();
        let parsed = AccountCredentials::from_state(state).unwrap();
        assert_eq!(parsed.query_ms_token, "S".repeat(164));
        assert!(!parsed.cookie_header("www.douyin.com").contains("msToken="));
    }
    #[test]
    fn capture_v2_missing_main_site_material_is_rejected_before_synthetic_fallback() {
        let value = serde_json::json!({"cookie":"sessionid=synthetic","ua":"Chrome/152.0","capture_version":2});
        assert!(AccountCredentials::input_from_fields("a".into(), &value, None).is_err());
    }
}
