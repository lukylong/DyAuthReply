//! Read-only authenticated identity binding; never use a peer profile as evidence
//! of the currently logged-in account. Query UID and same-object hydration pair must agree.
use super::{checked_json, AccountRequestError, NativeAccountSession};
use crate::protocol::{
    http_plan::{percent_encode_rfc3986, OrderedHeader},
    live_http::SessionEndpoint,
};
use rand::Rng;
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    sync::OnceLock,
    time::{Duration, Instant},
};
#[derive(Clone, Serialize)]
pub struct VerifiedSelf {
    pub user_id: String,
    pub sec_uid: String,
    pub nickname: String,
    pub avatar: String,
    pub unique_id: String,
    pub follower_count: u64,
    pub following_count: u64,
    pub aweme_count: u64,
    pub total_favorited: u64,
}

#[derive(Clone, Serialize)]
pub struct WorkItem {
    pub aweme_id: String,
    pub desc: String,
    pub cover: String,
    pub work_type: &'static str,
    pub like_count: u64,
    pub comment_count: u64,
    pub collect_count: u64,
    pub share_count: u64,
    pub create_time: u64,
    pub share_url: String,
}

#[derive(Clone, Serialize)]
pub struct WorksPage {
    pub items: Vec<WorkItem>,
    pub max_cursor: String,
    pub has_more: bool,
}
impl NativeAccountSession {
    /// Returns the current verified profile. Force bypasses only the five-minute
    /// in-memory profile cache; authenticated identity checks still apply.
    /// # Errors
    /// Rejects transport, authentication, scope and malformed profile responses.
    pub async fn profile_stats(
        &mut self,
        force: bool,
    ) -> Result<VerifiedSelf, AccountRequestError> {
        if force {
            self.self_profile = None;
        }
        self.verify_self().await
    }

    /// Reads one bounded public works page for this verified account without persistence.
    /// # Errors
    /// Rejects invalid cursors/counts, transport/signing failures and malformed pages.
    pub async fn works(
        &mut self,
        cursor: &str,
        count: u8,
    ) -> Result<WorksPage, AccountRequestError> {
        const STEP: &str = "self_works";
        if cursor.is_empty()
            || cursor.len() > 32
            || !cursor.bytes().all(|byte| byte.is_ascii_digit())
            || !(1..=30).contains(&count)
        {
            return Err(AccountRequestError::Decode { step: STEP });
        }
        let profile = self.verify_self().await?;
        let fp = Fingerprint::new(&self.credentials.user_agent);
        let mut params = vec![
            ("device_platform", "webapp".into()),
            ("aid", "6383".into()),
            ("channel", "channel_pc_web".into()),
            ("sec_user_id", profile.sec_uid.clone()),
            ("max_cursor", cursor.into()),
            ("locate_query", "false".into()),
            ("show_live_replay_strategy", "1".into()),
            (
                "need_time_list",
                if cursor == "0" { "1" } else { "0" }.into(),
            ),
            ("time_list_query", "0".into()),
            ("whale_cut_token", String::new()),
            ("cut_version", "1".into()),
            ("count", count.to_string()),
            ("publish_video_strategy_type", "2".into()),
            ("from_user_page", "0".into()),
        ];
        params.extend(
            fp.params()
                .into_iter()
                .skip(4)
                .map(|(key, value)| match key {
                    "version_code" => (key, "290100".into()),
                    "version_name" => (key, "29.1.0".into()),
                    _ => (key, value),
                }),
        );
        params.push((
            "webid",
            (0..19)
                .map(|_| char::from(b'0' + rand::thread_rng().gen_range(0..10)))
                .collect(),
        ));
        let uifid = self.credentials.cookie("www.douyin.com", "UIFID");
        if !uifid.is_empty() {
            params.push(("uifid", uifid));
        }
        params.push(("msToken", self.credentials.query_ms_token.clone()));
        let query = params
            .into_iter()
            .map(|(key, value)| format!("{key}={}", percent_encode_rfc3986(&value)))
            .collect::<Vec<_>>()
            .join("&");
        let query = self.signed_query(query, STEP).await?;
        let verify = percent_encode_rfc3986(self.credentials.web_fingerprint());
        let query = format!("{query}&verifyFp={verify}&fp={verify}");
        let mut headers = self.headers("www.douyin.com");
        headers.extend([
            OrderedHeader::new(
                "referer",
                format!(
                    "https://www.douyin.com/user/{}",
                    percent_encode_rfc3986(&profile.sec_uid)
                ),
            ),
            OrderedHeader::new("accept", "application/json, text/plain, */*"),
            OrderedHeader::new("sec-ch-ua", fp.client_hint()),
            OrderedHeader::new("sec-ch-ua-mobile", "?0"),
            OrderedHeader::new("sec-ch-ua-platform", fp.header_platform),
            OrderedHeader::new("accept-language", "zh-CN,zh;q=0.9"),
            OrderedHeader::new("priority", "u=1, i"),
            OrderedHeader::new("sec-fetch-dest", "empty"),
            OrderedHeader::new("sec-fetch-mode", "cors"),
            OrderedHeader::new("sec-fetch-site", "same-origin"),
        ]);
        let response = self
            .request(SessionEndpoint::Works, &query, &headers, b"", STEP)
            .await?;
        let payload = checked_json(&response, STEP)?;
        parse_works(&payload)
    }

    /// Reads a public peer profile with this account's authenticated session.
    /// It never changes the session's verified self binding.
    /// # Errors
    /// Rejects invalid scopes, transport/signing failures and a mismatched response.
    pub async fn user_profile(&self, sec_uid: &str) -> Result<VerifiedSelf, AccountRequestError> {
        const STEP: &str = "peer_profile";
        if sec_uid.is_empty()
            || sec_uid.len() > 256
            || sec_uid.chars().any(char::is_control)
            || sec_uid.starts_with("fallback_")
        {
            return Err(AccountRequestError::Decode { step: STEP });
        }
        self.profile_for_sec_uid(sec_uid, STEP)
            .await?
            .ok_or(AccountRequestError::Decode { step: STEP })
    }

    /// # Errors
    /// Rejects authentication/transport/parse failures and an account-cookie mismatch.
    pub async fn verify_self(&mut self) -> Result<VerifiedSelf, AccountRequestError> {
        const STEP: &str = "self_query";
        if let Some((profile, at, binding)) = &self.self_profile {
            if at.elapsed() < Duration::from_secs(300)
                && profile.sec_uid == self.credentials.expected_sec_uid
                && *binding == self.credentials.binding_digest()
            {
                return Ok(profile.clone());
            }
        }
        let fingerprint = Fingerprint::new(&self.credentials.user_agent);
        let mut params = fingerprint.params();
        let webid = (0..19)
            .map(|_| char::from(b'0' + rand::thread_rng().gen_range(0..10)))
            .collect::<String>();
        params.push(("webid", webid));
        let uifid = self.credentials.cookie("www.douyin.com", "UIFID");
        if !uifid.is_empty() {
            params.push(("uifid", uifid.clone()));
        }
        let fp = self.credentials.web_fingerprint().to_owned();
        params.extend([
            ("verifyFp", fp.clone()),
            ("fp", fp),
            ("msToken", self.credentials.query_ms_token.clone()),
        ]);
        let query = params
            .into_iter()
            .map(|(k, v)| format!("{k}={}", percent_encode_rfc3986(&v)))
            .collect::<Vec<_>>()
            .join("&");
        let query = self.signed_query(query, STEP).await?;
        let mut headers = self.headers("www.douyin.com");
        headers.extend([
            OrderedHeader::new("referer", "https://www.douyin.com/"),
            OrderedHeader::new("accept", "application/json, text/plain, */*"),
            OrderedHeader::new("sec-ch-ua", fingerprint.client_hint()),
            OrderedHeader::new("sec-ch-ua-mobile", "?0"),
            OrderedHeader::new("sec-ch-ua-platform", fingerprint.header_platform),
            OrderedHeader::new("accept-language", "zh-CN,zh;q=0.9"),
            OrderedHeader::new("priority", "u=1, i"),
            OrderedHeader::new("sec-fetch-dest", "empty"),
            OrderedHeader::new("sec-fetch-mode", "cors"),
            OrderedHeader::new("sec-fetch-site", "same-origin"),
        ]);
        if !uifid.is_empty() {
            headers.push(OrderedHeader::new("uifid", uifid));
        }
        let response = self
            .request(SessionEndpoint::QueryUser, &query, &headers, b"", STEP)
            .await?;
        let payload = checked_json(&response, STEP)?;
        if payload
            .get("status_code")
            .and_then(Value::as_i64)
            .is_some_and(|code| code != 0)
        {
            return Err(AccountRequestError::Business {
                step: STEP,
                code: payload["status_code"].as_i64(),
            });
        }
        let uid = payload
            .get("user_uid")
            .or_else(|| payload.get("uid"))
            .and_then(identifier)
            .filter(|s| s.parse::<u64>().is_ok_and(|n| n > 0))
            .ok_or(AccountRequestError::Decode { step: STEP })?;
        if let Some(profile) = if self.credentials.expected_sec_uid.is_empty() {
            None
        } else {
            self.bound_profile(&uid).await?
        } {
            self.self_profile = Some((
                profile.clone(),
                Instant::now(),
                self.credentials.binding_digest(),
            ));
            return Ok(profile);
        }
        let mut headers = self.headers("www.douyin.com");
        headers.push(OrderedHeader::new(
            "accept",
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        ));
        let html = self
            .request(
                SessionEndpoint::SelfPage,
                "from_tab_name=main",
                &headers,
                b"",
                "self_page",
            )
            .await?;
        if !(200..300).contains(&html.status) {
            return Err(AccountRequestError::Http {
                step: "self_page",
                status: html.status,
            });
        }
        let html = std::str::from_utf8(&html.body)
            .map_err(|_| AccountRequestError::Decode { step: "self_page" })?;
        let profile = parse_hydration(html, &uid).ok_or(AccountRequestError::Decode {
            step: "self_hydration",
        })?;
        if !self.credentials.expected_sec_uid.is_empty()
            && profile.sec_uid != self.credentials.expected_sec_uid
        {
            return Err(AccountRequestError::AccountMismatch);
        }
        if self.credentials.expected_sec_uid.is_empty() {
            self.credentials
                .expected_sec_uid
                .clone_from(&profile.sec_uid);
        }
        // Hydration contains user-controlled strings. It is only a hint: the
        // authenticated UID must still match a fresh structured profile response.
        let profile = self
            .bound_profile(&uid)
            .await?
            .ok_or(AccountRequestError::Decode {
                step: "self_profile_unconfirmed",
            })?;
        self.self_profile = Some((
            profile.clone(),
            Instant::now(),
            self.credentials.binding_digest(),
        ));
        Ok(profile)
    }
    async fn bound_profile(&self, uid: &str) -> Result<Option<VerifiedSelf>, AccountRequestError> {
        let expected = &self.credentials.expected_sec_uid;
        if expected.is_empty() {
            return Err(AccountRequestError::AccountMismatch);
        }
        let profile = self
            .profile_for_sec_uid(expected, "self_bound_profile")
            .await?;
        if profile
            .as_ref()
            .is_some_and(|profile| profile.user_id != uid)
        {
            return Err(AccountRequestError::AccountMismatch);
        }
        Ok(profile)
    }

    async fn profile_for_sec_uid(
        &self,
        expected: &str,
        step: &'static str,
    ) -> Result<Option<VerifiedSelf>, AccountRequestError> {
        let fp = Fingerprint::new(&self.credentials.user_agent);
        let mut params = vec![
            ("device_platform", "webapp".to_owned()),
            ("aid", "6383".into()),
            ("channel", "channel_pc_web".into()),
            ("publish_video_strategy_type", "2".into()),
            ("source", "channel_pc_web".into()),
            ("sec_user_id", expected.to_owned()),
            ("personal_center_strategy", "1".into()),
            ("profile_other_record_enable", "1".into()),
            ("land_to", "1".into()),
        ];
        params.extend(fp.params().into_iter().skip(4));
        params.push((
            "webid",
            (0..19)
                .map(|_| char::from(b'0' + rand::thread_rng().gen_range(0..10)))
                .collect(),
        ));
        let uifid = self.credentials.cookie("www.douyin.com", "UIFID");
        if !uifid.is_empty() {
            params.push(("uifid", uifid.clone()));
        }
        params.push(("msToken", self.credentials.query_ms_token.clone()));
        let query = params
            .into_iter()
            .map(|(k, v)| format!("{k}={}", percent_encode_rfc3986(&v)))
            .collect::<Vec<_>>()
            .join("&");
        let query = self.signed_query(query, step).await?;
        let verify = percent_encode_rfc3986(self.credentials.web_fingerprint());
        let query = format!("{query}&verifyFp={verify}&fp={verify}");
        let mut headers = self.headers("www.douyin.com");
        headers.extend([
            OrderedHeader::new(
                "referer",
                format!(
                    "https://www.douyin.com/user/{}",
                    percent_encode_rfc3986(expected)
                ),
            ),
            OrderedHeader::new("accept", "application/json, text/plain, */*"),
            OrderedHeader::new("sec-ch-ua", fp.client_hint()),
            OrderedHeader::new("sec-ch-ua-mobile", "?0"),
            OrderedHeader::new("sec-ch-ua-platform", fp.header_platform),
            OrderedHeader::new("accept-language", "zh-CN,zh;q=0.9"),
            OrderedHeader::new("priority", "u=1, i"),
            OrderedHeader::new("sec-fetch-dest", "empty"),
            OrderedHeader::new("sec-fetch-mode", "cors"),
            OrderedHeader::new("sec-fetch-site", "same-origin"),
        ]);
        if !uifid.is_empty() {
            headers.push(OrderedHeader::new("uifid", uifid));
        }
        let response = self
            .request(SessionEndpoint::ProfileOther, &query, &headers, b"", step)
            .await?;
        let payload = checked_json(&response, step)?;
        if payload.get("status_code").and_then(Value::as_i64) != Some(0) {
            return Ok(None);
        }
        validate_profile(expected, &payload)
    }
}
fn validate_profile(
    expected: &str,
    payload: &Value,
) -> Result<Option<VerifiedSelf>, AccountRequestError> {
    let user = &payload["user"];
    let Some(actual) = user.get("uid").and_then(identifier) else {
        return Ok(None);
    };
    let Some(sec) = user
        .get("sec_uid")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
    else {
        return Ok(None);
    };
    if sec != expected {
        return Err(AccountRequestError::AccountMismatch);
    }
    Ok(Some(VerifiedSelf {
        user_id: actual,
        sec_uid: sec.into(),
        nickname: user["nickname"]
            .as_str()
            .unwrap_or("")
            .chars()
            .take(128)
            .collect(),
        avatar: first_image(user, &["avatar_thumb", "avatar_larger", "avatar_medium"]),
        unique_id: bounded_text(user, "unique_id", 128),
        follower_count: unsigned(user.get("follower_count")),
        following_count: unsigned(user.get("following_count")),
        aweme_count: unsigned(user.get("aweme_count")),
        total_favorited: unsigned(user.get("total_favorited")),
    }))
}

#[cfg(test)]
fn validate_bound_profile(
    uid: &str,
    expected: &str,
    payload: &Value,
) -> Result<Option<VerifiedSelf>, AccountRequestError> {
    let profile = validate_profile(expected, payload)?;
    if profile
        .as_ref()
        .is_some_and(|profile| profile.user_id != uid)
    {
        return Err(AccountRequestError::AccountMismatch);
    }
    Ok(profile)
}

fn bounded_text(value: &Value, key: &str, limit: usize) -> String {
    value[key]
        .as_str()
        .filter(|text| text.len() <= limit && !text.chars().any(char::is_control))
        .unwrap_or("")
        .to_owned()
}

fn first_image(value: &Value, keys: &[&str]) -> String {
    keys.iter()
        .find_map(|key| {
            value[*key]["url_list"]
                .as_array()?
                .iter()
                .find_map(Value::as_str)
                .filter(|url| valid_media_url(url))
                .map(str::to_owned)
        })
        .unwrap_or_default()
}

fn first_url_list(value: &Value) -> String {
    value["url_list"]
        .as_array()
        .and_then(|urls| urls.iter().find_map(Value::as_str))
        .filter(|url| valid_media_url(url))
        .unwrap_or_default()
        .to_owned()
}

fn valid_media_url(value: &str) -> bool {
    value.len() <= 4096
        && !value.chars().any(char::is_control)
        && value.parse::<wreq::Uri>().is_ok_and(|uri| {
            matches!(uri.scheme_str(), Some("http" | "https")) && uri.host().is_some()
        })
}

fn unsigned(value: Option<&Value>) -> u64 {
    match value {
        Some(Value::Number(number)) => number.as_u64().unwrap_or(0),
        Some(Value::String(text)) => text.parse().unwrap_or(0),
        _ => 0,
    }
}

fn parse_works(payload: &Value) -> Result<WorksPage, AccountRequestError> {
    const STEP: &str = "self_works";
    if payload.get("status_code").and_then(Value::as_i64) != Some(0)
        && payload.get("aweme_list").is_none()
    {
        return Err(AccountRequestError::Business {
            step: STEP,
            code: payload.get("status_code").and_then(Value::as_i64),
        });
    }
    let rows = payload["aweme_list"]
        .as_array()
        .ok_or(AccountRequestError::Decode { step: STEP })?;
    if rows.len() > 30 {
        return Err(AccountRequestError::Decode { step: STEP });
    }
    let mut items = Vec::with_capacity(rows.len());
    for row in rows {
        let aweme_id = row["aweme_id"]
            .as_str()
            .filter(|value| {
                !value.is_empty()
                    && value.len() <= 32
                    && value.bytes().all(|byte| byte.is_ascii_digit())
            })
            .ok_or(AccountRequestError::Decode { step: STEP })?;
        let description = row["desc"]
            .as_str()
            .filter(|value| value.len() <= 4096)
            .unwrap_or("")
            .to_owned();
        let cover = first_image(&row["video"], &["cover", "origin_cover", "dynamic_cover"]);
        let cover = if cover.is_empty() {
            row["images"]
                .as_array()
                .and_then(|images| images.first())
                .map_or_else(String::new, first_url_list)
        } else {
            cover
        };
        let statistics = &row["statistics"];
        items.push(WorkItem {
            aweme_id: aweme_id.into(),
            desc: description,
            cover,
            work_type: if row["aweme_type"].as_i64() == Some(68) {
                "image"
            } else {
                "video"
            },
            like_count: unsigned(statistics.get("digg_count")),
            comment_count: unsigned(statistics.get("comment_count")),
            collect_count: unsigned(statistics.get("collect_count")),
            share_count: unsigned(statistics.get("share_count")),
            create_time: unsigned(row.get("create_time")),
            share_url: format!("https://www.douyin.com/video/{aweme_id}"),
        });
    }
    let max_cursor = identifier(&payload["max_cursor"])
        .filter(|value| value.len() <= 32 && value.bytes().all(|byte| byte.is_ascii_digit()))
        .unwrap_or_else(|| "0".into());
    Ok(WorksPage {
        items,
        max_cursor,
        has_more: payload["has_more"]
            .as_bool()
            .or_else(|| payload["has_more"].as_i64().map(|value| value != 0))
            .unwrap_or(false),
    })
}
fn identifier(value: &Value) -> Option<String> {
    match value {
        Value::String(s) => Some(s.clone()),
        Value::Number(n) => Some(n.to_string()),
        _ => None,
    }
}
fn visit(
    value: &Value,
    uid: &str,
    found: &mut BTreeMap<String, String>,
    budget: &mut usize,
    depth: u8,
) {
    if *budget == 0 || depth > 12 {
        return;
    }
    *budget -= 1;
    match value {
        Value::Object(object) => {
            let id = ["uid", "userId", "user_id"]
                .iter()
                .find_map(|k| object.get(*k).and_then(identifier));
            if id.as_deref() == Some(uid) {
                if let Some(sec) = ["secUid", "sec_uid"]
                    .iter()
                    .find_map(|k| object.get(*k).and_then(Value::as_str))
                    .filter(|s| !s.is_empty() && s.len() <= 256)
                {
                    found.insert(
                        sec.into(),
                        object
                            .get("nickname")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .chars()
                            .take(128)
                            .collect(),
                    );
                }
            }
            for child in object.values() {
                visit(child, uid, found, budget, depth + 1);
            }
        }
        Value::Array(values) => {
            for child in values {
                visit(child, uid, found, budget, depth + 1);
            }
        }
        _ => {}
    }
}
fn unquote(text: &str) -> Option<String> {
    let raw = text.as_bytes();
    let mut output = Vec::with_capacity(raw.len());
    let mut i = 0;
    while i < raw.len() {
        if raw[i] == b'%' {
            let byte =
                u8::from_str_radix(std::str::from_utf8(raw.get(i + 1..i + 3)?).ok()?, 16).ok()?;
            output.push(byte);
            i += 3;
        } else {
            output.push(raw[i]);
            i += 1;
        }
    }
    String::from_utf8(output).ok()
}
fn parse_hydration(html: &str, uid: &str) -> Option<VerifiedSelf> {
    static SCRIPTS: OnceLock<regex::Regex> = OnceLock::new();
    static PUSH: OnceLock<regex::Regex> = OnceLock::new();
    static RECORD: OnceLock<regex::Regex> = OnceLock::new();
    let scripts = SCRIPTS.get_or_init(|| {
        regex::Regex::new(r"(?is)<script\b([^>]*)>(.*?)</script>").expect("script selector")
    });
    let push = PUSH.get_or_init(|| {
        regex::Regex::new(r"(?:self\.__next_f|\(self\.__next_f=self\.__next_f\|\|\[\]\))\.push\(")
            .expect("flight push selector")
    });
    let record = RECORD.get_or_init(|| {
        regex::Regex::new(r#"(?m)^[0-9a-zA-Z]+:([\[{"])"#).expect("flight JSON record")
    });
    let mut found = BTreeMap::new();
    let mut budget = 50_000;
    let mut flight = String::new();
    for capture in scripts.captures_iter(html) {
        if capture[1].contains("id=\"RENDER_DATA\"") || capture[1].contains("id='RENDER_DATA'") {
            let text = unquote(&capture[2])?;
            if let Ok(value) = serde_json::from_str::<Value>(&text) {
                visit(&value, uid, &mut found, &mut budget, 0);
            }
        } else {
            for call in push.find_iter(&capture[2]) {
                let mut stream = serde_json::Deserializer::from_str(&capture[2][call.end()..])
                    .into_iter::<Value>();
                if let Some(Ok(Value::Array(values))) = stream.next() {
                    if values.first() == Some(&Value::from(1)) {
                        if let Some(text) = values.get(1).and_then(Value::as_str) {
                            flight.push_str(text);
                        }
                    }
                }
            }
        }
    }
    for item in record.captures_iter(&flight) {
        let start = item.get(1)?.start();
        let mut stream = serde_json::Deserializer::from_str(&flight[start..]).into_iter::<Value>();
        if let Some(Ok(value)) = stream.next() {
            visit(&value, uid, &mut found, &mut budget, 0);
        }
    }
    if found.len() != 1 {
        return None;
    }
    let (sec_uid, nickname) = found.into_iter().next()?;
    Some(VerifiedSelf {
        user_id: uid.into(),
        sec_uid,
        nickname,
        avatar: String::new(),
        unique_id: String::new(),
        follower_count: 0,
        following_count: 0,
        aweme_count: 0,
        total_favorited: 0,
    })
}

struct Fingerprint {
    version: String,
    platform: &'static str,
    header_platform: &'static str,
    os: &'static str,
    divert: &'static str,
}
impl Fingerprint {
    fn new(ua: &str) -> Self {
        let version = ua
            .split("Chrome/")
            .nth(1)
            .or_else(|| ua.split("Chromium/").nth(1))
            .and_then(|v| v.split_whitespace().next())
            .unwrap_or("124.0.0.0")
            .to_owned();
        let (platform, header_platform, os, divert) = if ua.contains("Macintosh") {
            ("MacIntel", "\"macOS\"", "Mac OS", "Mac")
        } else if ua.contains("Linux") && !ua.contains("Android") {
            ("Linux x86_64", "\"Linux\"", "Linux", "Linux")
        } else {
            ("Win32", "\"Windows\"", "Windows", "Windows")
        };
        Self {
            version,
            platform,
            header_platform,
            os,
            divert,
        }
    }
    fn client_hint(&self) -> String {
        let major = self.version.split('.').next().unwrap_or("124");
        format!(
            "\"Not=A?Brand\";v=\"99\", \"Google Chrome\";v=\"{major}\", \"Chromium\";v=\"{major}\""
        )
    }
    fn params(&self) -> Vec<(&'static str, String)> {
        [
            ("device_platform", "webapp"),
            ("aid", "6383"),
            ("channel", "channel_pc_web"),
            ("publish_video_strategy_type", "2"),
            ("update_version_code", "170400"),
            ("pc_client_type", "1"),
            ("pc_libra_divert", self.divert),
            ("support_h265", "1"),
            ("support_dash", "1"),
            ("cpu_core_num", "8"),
            ("version_code", "170400"),
            ("version_name", "17.4.0"),
            ("cookie_enabled", "true"),
            ("screen_width", "1920"),
            ("screen_height", "1080"),
            ("browser_language", "zh-CN"),
            ("browser_platform", self.platform),
            ("browser_name", "Chrome"),
            ("browser_version", self.version.as_str()),
            ("browser_online", "true"),
            ("engine_name", "Blink"),
            ("engine_version", self.version.as_str()),
            ("os_name", self.os),
            ("os_version", "10"),
            ("device_memory", "8"),
            ("platform", "PC"),
            ("downlink", "10"),
            ("effective_type", "4g"),
            ("round_trip_time", "0"),
        ]
        .into_iter()
        .map(|(k, v)| (k, v.into()))
        .collect()
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn uid_and_sec_uid_must_belong_to_same_authenticated_user_object() {
        let html = r#"<script>self.__next_f.push([1,"1:{\"user\":{\"uid\":\"123\",\"secUid\":\"self\",\"nickname\":\"name\"},\"other\":{\"uid\":\"456\",\"secUid\":\"peer\"}}"])</script>"#;
        assert_eq!(parse_hydration(html, "123").unwrap().sec_uid, "self");
        assert!(parse_hydration(html, "789").is_none());
        let split = r#"<script>{"uid":"123","other":{"uid":"456","secUid":"peer"}}</script>"#;
        assert!(parse_hydration(split, "123").is_none());
    }
    #[test]
    fn conflicting_self_objects_and_challenge_pages_fail_closed() {
        assert!(parse_hydration(
            r#"<script>[{"uid":"1","secUid":"a"},{"uid":"1","secUid":"b"}]</script>"#,
            "1"
        )
        .is_none());
        assert!(parse_hydration("<html>verification required</html>", "1").is_none());
    }
    #[test]
    fn percent_encoded_render_data_is_parsed_without_executing_scripts() {
        let html="<script id=\"RENDER_DATA\">%7B%22uid%22%3A%221%22%2C%22sec_uid%22%3A%22self%22%7D</script>";
        assert_eq!(parse_hydration(html, "1").unwrap().sec_uid, "self");
    }
    #[test]
    fn public_profile_only_binds_when_its_uid_matches_authenticated_query() {
        let payload = serde_json::json!({"status_code":0,"user":{"uid":"123","sec_uid":"expected","nickname":"name"}});
        assert!(validate_bound_profile("123", "expected", &payload)
            .unwrap()
            .is_some());
        assert!(matches!(
            validate_bound_profile("456", "expected", &payload),
            Err(AccountRequestError::AccountMismatch)
        ));
        assert!(matches!(
            validate_bound_profile("123", "other", &payload),
            Err(AccountRequestError::AccountMismatch)
        ));
    }
    #[test]
    fn profile_json_hidden_in_nickname_is_not_identity_evidence() {
        let fake =
            serde_json::json!({"uid":"123","nickname":"{\"uid\":\"123\",\"secUid\":\"forged\"}"});
        let chunk = format!("1:{fake}");
        let html = format!(
            "<script>self.__next_f.push({});</script>",
            serde_json::json!([1, chunk])
        );
        assert!(parse_hydration(&html, "123").is_none());
    }
    #[test]
    fn verified_profile_carries_bounded_public_stats_and_rejects_scope_mix() {
        let payload = serde_json::json!({"status_code":0,"user":{
            "uid":"123","sec_uid":"expected","nickname":"账号","unique_id":"short",
            "avatar_thumb":{"url_list":["https://example.com/avatar.jpg"]},
            "follower_count":"12","following_count":3,"aweme_count":4,"total_favorited":"56"
        }});
        let profile = validate_profile("expected", &payload).unwrap().unwrap();
        assert_eq!(profile.user_id, "123");
        assert_eq!(profile.avatar, "https://example.com/avatar.jpg");
        assert_eq!(profile.follower_count, 12);
        assert_eq!(profile.following_count, 3);
        assert_eq!(profile.aweme_count, 4);
        assert_eq!(profile.total_favorited, 56);
        assert!(matches!(
            validate_profile("another", &payload),
            Err(AccountRequestError::AccountMismatch)
        ));
    }
    #[test]
    fn works_page_handles_video_and_image_covers_without_persisting_raw_rows() {
        let payload = serde_json::json!({
            "status_code":0,"max_cursor":99,"has_more":1,
            "aweme_list":[
                {"aweme_id":"100","desc":"video","aweme_type":0,
                 "video":{"cover":{"url_list":["https://example.com/video.jpg"]}},
                 "statistics":{"digg_count":1,"comment_count":"2","collect_count":3,"share_count":4},
                 "create_time":5},
                {"aweme_id":"101","desc":"image","aweme_type":68,
                 "images":[{"url_list":["https://example.com/image.jpg"]}],
                 "statistics":{},"create_time":"6"}
            ]
        });
        let page = parse_works(&payload).unwrap();
        assert_eq!(page.items.len(), 2);
        assert_eq!(page.items[0].cover, "https://example.com/video.jpg");
        assert_eq!(page.items[0].comment_count, 2);
        assert_eq!(page.items[1].cover, "https://example.com/image.jpg");
        assert_eq!(page.items[1].work_type, "image");
        assert_eq!(page.max_cursor, "99");
        assert!(page.has_more);
        let mut excessive = payload;
        excessive["aweme_list"] = Value::Array(vec![Value::Null; 31]);
        assert!(parse_works(&excessive).is_err());
    }
}
