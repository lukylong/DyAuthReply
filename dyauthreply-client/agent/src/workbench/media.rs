//! Chat media: display fields derived from `content_json`, and the encrypted-image
//! proxy. Album photos and video posters are AES-GCM encrypted on the CDN
//! (key = hex `skey`, nonce = first 12 bytes, tag appended), so `<img>` cannot load
//! them directly. Media URLs come from the peer's message, so fetches are limited to
//! HTTPS `ByteDance` CDN hosts to keep a crafted message from reaching local services.
use aes_gcm::{
    aead::{Aead, KeyInit},
    Aes128Gcm, Aes256Gcm, Nonce,
};
use anyhow::{Context, Result};
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::time::Duration;

const MAX_MEDIA_BYTES: usize = 20 * 1024 * 1024;
const MAX_INLINE_PREVIEW: usize = 48 * 1024;
const MAX_FIELD: usize = 4096;
const NONCE_LEN: usize = 12;
const TAG_LEN: usize = 16;
const FETCH_TIMEOUT: Duration = Duration::from_secs(20);
/// Suffixes of the CDNs Douyin serves IM media from.
const ALLOWED_HOST_SUFFIXES: &[&str] = &[
    ".douyinpic.com",
    ".douyinvod.com",
    ".douyincdn.com",
    ".byteimg.com",
    ".bytedimg.com",
    ".zjcdn.com",
    ".pstatp.com",
    ".snssdk.com",
    ".amemv.com",
];

/// Where to fetch an encrypted image and the key to open it. Never sent to the UI.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct MediaSource {
    pub url: String,
    pub skey: String,
}

/// UI media fields for a described message kind (`image`, `video`, `voice`,
/// `emoji`, `share_video`), plus the decryptable source when the CDN copy is encrypted.
#[must_use]
pub fn extract(obj: &Map<String, Value>, kind: &str) -> (Option<Value>, Option<MediaSource>) {
    let resource = obj.get("resource_url").and_then(Value::as_object);
    let poster = obj.get("poster").and_then(Value::as_object);
    let mut media = match kind {
        "image" => json!({
            "kind": "image",
            "url": first_url(resource, &["origin_url_list", "large_url_list", "medium_url_list", "thumb_url_list"]),
            "width": number(obj.get("cover_width")),
            "height": number(obj.get("cover_height")),
            "encrypted": skey(resource).is_some(),
        }),
        "video" => json!({
            "kind": "video",
            "cover_url": first_url(poster, &["origin_url_list", "medium_url_list"]),
            "vid": bounded(obj.get("video").and_then(|v| v.get("vid"))),
            "duration_s": number(obj.get("duration")),
            "width": number(obj.get("width")),
            "height": number(obj.get("height")),
            "encrypted": skey(poster).is_some(),
        }),
        "voice" => json!({
            "kind": "voice",
            "url": first_url(resource, &["url_list"]),
            "duration_ms": number(obj.get("duration")),
            "ai_text": bounded(obj.get("ai_audio_text")),
        }),
        "emoji" => json!({
            "kind": "emoji",
            "url": first_url(obj.get("url").and_then(Value::as_object), &["url_list"]),
            "width": number(obj.get("width")),
            "height": number(obj.get("height")),
        }),
        "share_video" => json!({
            "kind": "share_video",
            "item_id": bounded(obj.get("itemId").or_else(|| obj.get("item_id"))),
            "cover_url": cover_url(obj.get("cover_url")),
        }),
        _ => return (None, None),
    };
    if let Some(preview) = obj
        .get("inline_pic")
        .and_then(Value::as_str)
        .filter(|p| !p.is_empty() && p.len() <= MAX_INLINE_PREVIEW)
    {
        media["inline_pic"] = json!(preview);
    }
    let source = match kind {
        "image" => source(
            resource,
            &[
                "origin_url_list",
                "large_url_list",
                "medium_url_list",
                "thumb_url_list",
            ],
        ),
        "video" => source(poster, &["origin_url_list", "medium_url_list"]),
        _ => None,
    };
    (Some(media), source)
}

fn first_url(container: Option<&Map<String, Value>>, keys: &[&str]) -> String {
    container
        .and_then(|c| {
            keys.iter().find_map(|key| {
                c.get(*key)?
                    .as_array()?
                    .first()?
                    .as_str()
                    .filter(|u| !u.is_empty() && u.len() <= MAX_FIELD)
            })
        })
        .unwrap_or_default()
        .to_owned()
}

fn cover_url(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(url)) if url.len() <= MAX_FIELD => url.clone(),
        Some(Value::Object(map)) => first_url(Some(map), &["url_list"]),
        _ => String::new(),
    }
}

fn skey(container: Option<&Map<String, Value>>) -> Option<&str> {
    container?
        .get("skey")?
        .as_str()
        .filter(|k| matches!(k.len(), 32 | 64) && k.bytes().all(|b| b.is_ascii_hexdigit()))
}

fn source(container: Option<&Map<String, Value>>, keys: &[&str]) -> Option<MediaSource> {
    let skey = skey(container)?.to_owned();
    let url = first_url(container, keys);
    (!url.is_empty()).then_some(MediaSource { url, skey })
}

fn number(value: Option<&Value>) -> Value {
    match value {
        Some(Value::Number(n)) => Value::Number(n.clone()),
        Some(Value::String(s)) => s.parse::<f64>().ok().map_or(Value::Null, |n| json!(n)),
        _ => Value::Null,
    }
}

fn bounded(value: Option<&Value>) -> Value {
    match value {
        Some(Value::String(s)) if s.len() <= MAX_FIELD => json!(s),
        Some(Value::Number(n)) => json!(n.to_string()),
        _ => Value::Null,
    }
}

/// HTTPS on a `ByteDance` media CDN, no credentials, no explicit port.
#[must_use]
pub fn allowed_url(raw: &str) -> bool {
    let Ok(uri) = raw.parse::<wreq::Uri>() else {
        return false;
    };
    let Some(host) = uri.host() else {
        return false;
    };
    uri.scheme_str() == Some("https")
        && uri.port().is_none()
        && !raw.contains('@')
        && ALLOWED_HOST_SUFFIXES
            .iter()
            .any(|suffix| host.len() > suffix.len() && host.ends_with(suffix))
}

/// Opens one AES-GCM encrypted IM media payload (`nonce || ciphertext || tag`).
/// # Errors
/// Rejects malformed keys, truncated payloads and authentication failures.
pub fn decrypt(payload: &[u8], skey_hex: &str) -> Result<Vec<u8>> {
    anyhow::ensure!(payload.len() > NONCE_LEN + TAG_LEN, "加密媒体内容过短");
    let key = hex(skey_hex).context("加密媒体密钥无效")?;
    let (nonce, body) = payload.split_at(NONCE_LEN);
    let nonce = Nonce::from_slice(nonce);
    let plain = match key.len() {
        32 => Aes256Gcm::new_from_slice(&key)?.decrypt(nonce, body),
        16 => Aes128Gcm::new_from_slice(&key)?.decrypt(nonce, body),
        _ => anyhow::bail!("加密媒体密钥长度无效"),
    };
    plain.map_err(|_| anyhow::anyhow!("加密媒体校验失败"))
}

fn hex(value: &str) -> Option<Vec<u8>> {
    value.len().is_multiple_of(2).then_some(())?;
    (0..value.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(value.get(i..i + 2)?, 16).ok())
        .collect()
}

/// Content type from magic bytes. HEIC passes through: `WKWebView` renders it,
/// `WebView2` does not and the UI falls back to the placeholder.
#[must_use]
pub fn sniff(data: &[u8]) -> &'static str {
    if data.starts_with(&[0xff, 0xd8, 0xff]) {
        "image/jpeg"
    } else if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        "image/png"
    } else if data.starts_with(b"GIF87a") || data.starts_with(b"GIF89a") {
        "image/gif"
    } else if data.len() >= 12 && &data[..4] == b"RIFF" && &data[8..12] == b"WEBP" {
        "image/webp"
    } else if data.len() >= 12 && &data[4..8] == b"ftyp" {
        "image/heic"
    } else {
        "application/octet-stream"
    }
}

/// Downloads and decrypts one encrypted image.
/// # Errors
/// Rejects disallowed hosts, redirects, oversized bodies and failed decryption.
pub async fn fetch_decrypted(source: &MediaSource) -> Result<(Vec<u8>, &'static str)> {
    if !allowed_url(&source.url) {
        let host = source
            .url
            .parse::<wreq::Uri>()
            .ok()
            .and_then(|u| u.host().map(str::to_owned))
            .unwrap_or_default();
        tracing::warn!(%host, "encrypted media host not allowed");
        anyhow::bail!("媒体地址不在允许范围内");
    }
    let response = wreq::Client::builder()
        .redirect(wreq::redirect::Policy::none())
        .retry(wreq::retry::Policy::never())
        .timeout(FETCH_TIMEOUT)
        .build()?
        .get(&source.url)
        .send()
        .await?;
    anyhow::ensure!(
        response.status().is_success(),
        "媒体下载失败 HTTP {}",
        response.status()
    );
    let mut body = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk?;
        anyhow::ensure!(body.len() + chunk.len() <= MAX_MEDIA_BYTES, "媒体文件过大");
        body.extend_from_slice(&chunk);
    }
    let plain = decrypt(&body, &source.skey)?;
    let kind = sniff(&plain);
    Ok((plain, kind))
}

#[cfg(test)]
mod tests {
    use super::*;
    use aes_gcm::aead::AeadCore;

    #[test]
    fn decrypts_reference_layout_and_rejects_tampering() {
        let key = [7u8; 32];
        let cipher = Aes256Gcm::new_from_slice(&key).unwrap();
        let nonce = Aes256Gcm::generate_nonce(&mut aes_gcm::aead::OsRng);
        let plain = b"\xff\xd8\xff\xe0 jpeg body".to_vec();
        let mut payload = nonce.to_vec();
        payload.extend(cipher.encrypt(&nonce, plain.as_slice()).unwrap());
        let skey = "07".repeat(32);
        let opened = decrypt(&payload, &skey).unwrap();
        assert_eq!(opened, plain);
        assert_eq!(sniff(&opened), "image/jpeg");
        let last = payload.len() - 1;
        payload[last] ^= 1;
        assert!(decrypt(&payload, &skey).is_err());
        assert!(decrypt(&payload[..20], &skey).is_err());
        assert!(decrypt(&payload, "zz").is_err());
    }

    #[test]
    fn only_https_bytedance_cdn_hosts_are_fetched() {
        assert!(allowed_url(
            "https://p3-im-sign.douyinpic.com/tos-cn-i/abc~tplv.image?x=1"
        ));
        assert!(allowed_url("https://v26.douyinvod.com/video/x"));
        for bad in [
            "http://p3.douyinpic.com/x",
            "https://127.0.0.1/x",
            "https://localhost:18765/api/client/v1/health",
            "https://douyinpic.com.evil.example/x",
            "https://evil.example/?h=.douyinpic.com",
            "https://p3.douyinpic.com:8443/x",
            "https://user@p3.douyinpic.com/x",
            "file:///etc/passwd",
        ] {
            assert!(!allowed_url(bad), "{bad}");
        }
    }

    #[test]
    fn image_and_video_fields_and_sources_follow_the_reference_parser() {
        let image: Value = serde_json::from_str(
            r#"{"resource_url":{"skey":"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "origin_url_list":["https://p3-im.douyinpic.com/o"],"thumb_url_list":["https://p3-im.douyinpic.com/t"]},
                "cover_width":640,"cover_height":"480","inline_pic":"UklGRg=="}"#,
        )
        .unwrap();
        let (media, source) = extract(image.as_object().unwrap(), "image");
        let media = media.unwrap();
        assert_eq!(media["url"], "https://p3-im.douyinpic.com/o");
        assert_eq!(media["width"], 640);
        assert_eq!(media["height"], 480.0);
        assert_eq!(media["encrypted"], true);
        assert_eq!(media["inline_pic"], "UklGRg==");
        assert_eq!(source.unwrap().url, "https://p3-im.douyinpic.com/o");
        let video: Value = serde_json::from_str(
            r#"{"video":{"vid":"v1"},"poster":{"origin_url_list":["https://p.douyinpic.com/c"]},"duration":12}"#,
        )
        .unwrap();
        let (media, source) = extract(video.as_object().unwrap(), "video");
        let media = media.unwrap();
        assert_eq!(media["cover_url"], "https://p.douyinpic.com/c");
        assert_eq!(media["encrypted"], false);
        assert!(source.is_none());
        let share: Value = serde_json::from_str(
            r#"{"itemId":"7","cover_url":{"url_list":["https://p.douyinpic.com/s"]}}"#,
        )
        .unwrap();
        let (media, _) = extract(share.as_object().unwrap(), "share_video");
        assert_eq!(media.unwrap()["cover_url"], "https://p.douyinpic.com/s");
    }
}
