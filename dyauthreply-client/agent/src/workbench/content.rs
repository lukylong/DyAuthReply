//! Readable projection of IM `content_json`, mirroring the reference parser's
//! precedence: a label for every message plus display media for rich kinds.
use super::media::{self, MediaSource};
use serde_json::{Map, Value};

const MAX_TITLE_CHARS: usize = 60;

/// Single chats use message type 1 (`0:1:uid:uid`); group chats arrive as type 2.
#[must_use]
pub fn is_direct_message(message_type: u64) -> bool {
    message_type == 1
}

#[derive(Debug, PartialEq)]
pub struct Described {
    /// Frontend `content_type`: text, image, video, card or other.
    pub kind: &'static str,
    pub content: String,
    /// Frontend `media` (kind/url/cover/size/encrypted/...), for rich kinds only.
    pub media: Option<Value>,
    /// Encrypted CDN source for the image proxy; stays local.
    pub source: Option<MediaSource>,
}

/// Returns `None` for control packets and content-less pushes, which are not
/// visible conversation messages.
#[must_use]
pub fn describe(content_json: &str, decoded_text: Option<&str>) -> Option<Described> {
    let parsed = serde_json::from_str::<Value>(content_json).ok();
    let Some(obj) = parsed.as_ref().and_then(Value::as_object) else {
        let text = decoded_text.filter(|t| !t.trim().is_empty())?;
        return Some(labelled("text", text.to_owned()));
    };
    if obj.contains_key("read_index") || obj.contains_key("command_type") {
        return None;
    }
    // The wire decoder's text is authoritative (it may come from ext fields).
    if let Some(text) = decoded_text
        .or_else(|| obj.get("text").and_then(Value::as_str))
        .filter(|t| !t.trim().is_empty())
    {
        return Some(labelled("text", text.to_owned()));
    }
    if ["itemId", "item_id"].iter().any(|k| present(obj.get(*k))) {
        let title = ["content_title", "description", "content_name"]
            .iter()
            .find_map(|k| obj.get(*k).and_then(Value::as_str).map(str::trim))
            .filter(|t| !t.is_empty());
        return Some(with_media(
            labelled("card", with_title("[分享视频]", title)),
            obj,
            "share_video",
        ));
    }
    if obj
        .get("video")
        .and_then(Value::as_object)
        .is_some_and(|v| present(v.get("vid")))
    {
        return Some(with_media(labelled("video", "[视频]".into()), obj, "video"));
    }
    let resource = obj.get("resource_url").and_then(Value::as_object);
    if resource.is_some() && (obj.contains_key("cover_width") || obj.contains_key("cover_height")) {
        return Some(with_media(labelled("image", "[图片]".into()), obj, "image"));
    }
    if obj.contains_key("voice_wave") || (resource.is_some() && obj.contains_key("duration")) {
        return Some(with_media(labelled("other", "[语音]".into()), obj, "voice"));
    }
    let display = obj
        .get("display_name")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|d| !d.is_empty());
    if is_emoji(obj) && (display.is_some() || obj.get("url").is_some_and(Value::is_object)) {
        return Some(with_media(
            labelled(
                "other",
                display.map_or_else(|| "[表情]".into(), str::to_owned),
            ),
            obj,
            "emoji",
        ));
    }
    if let Some(display) = display {
        return Some(labelled("text", display.to_owned()));
    }
    if obj.contains_key("room_id") {
        return Some(labelled("card", "[直播间分享]".into()));
    }
    let lower: Vec<String> = obj.keys().map(|k| k.to_ascii_lowercase()).collect();
    if lower
        .iter()
        .any(|k| k.contains("image") || k.contains("picture"))
    {
        return Some(labelled("image", "[图片]".into()));
    }
    if lower.iter().any(|k| k.contains("video")) {
        return Some(labelled("video", "[视频]".into()));
    }
    Some(fallback(decoded_text))
}

fn fallback(decoded_text: Option<&str>) -> Described {
    match decoded_text.filter(|t| !t.trim().is_empty()) {
        Some(text) => labelled("text", text.to_owned()),
        None => labelled("other", "[暂不支持的消息类型]".into()),
    }
}

fn labelled(kind: &'static str, content: String) -> Described {
    Described {
        kind,
        content,
        media: None,
        source: None,
    }
}

fn with_media(mut described: Described, obj: &Map<String, Value>, kind: &str) -> Described {
    (described.media, described.source) = media::extract(obj, kind);
    described
}

fn present(value: Option<&Value>) -> bool {
    match value {
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Number(_)) => true,
        _ => false,
    }
}

fn is_emoji(obj: &Map<String, Value>) -> bool {
    obj.get("url").is_some_and(Value::is_object)
        || obj.keys().any(|k| {
            let k = k.to_ascii_lowercase();
            k.contains("emoji") || k.contains("sticker") || k.contains("emoticon")
        })
}

fn with_title(label: &str, title: Option<&str>) -> String {
    match title {
        Some(title) => {
            let short: String = title.chars().take(MAX_TITLE_CHARS).collect();
            let ellipsis = if title.chars().count() > MAX_TITLE_CHARS {
                "…"
            } else {
                ""
            };
            format!("{label} {short}{ellipsis}")
        }
        None => label.to_owned(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(json: &str) -> Option<(&'static str, String)> {
        describe(json, None).map(|x| (x.kind, x.content))
    }
    fn dt(json: &str, text: Option<&str>) -> Option<(&'static str, String)> {
        describe(json, text).map(|x| (x.kind, x.content))
    }

    #[test]
    fn only_type_one_is_a_direct_message() {
        assert!(is_direct_message(1));
        assert!(!is_direct_message(2));
        assert!(!is_direct_message(0));
    }

    #[test]
    fn control_packets_are_not_visible_messages() {
        assert_eq!(d(r#"{"command_type":1,"conversation_id":"c"}"#), None);
        assert_eq!(d(r#"{"read_index":3}"#), None);
        assert_eq!(d(r#"{"text":"hi","command_type":1}"#), None);
    }

    #[test]
    fn text_wins_over_media_keys() {
        assert_eq!(
            d(r#"{"text":"你好","itemId":"1"}"#),
            Some(("text", "你好".into()))
        );
    }

    #[test]
    fn shared_works_carry_a_bounded_title() {
        assert_eq!(
            d(r#"{"itemId":"7","content_title":"秋天","aweType":800}"#),
            Some(("card", "[分享视频] 秋天".into()))
        );
        assert_eq!(d(r#"{"item_id":7}"#), Some(("card", "[分享视频]".into())));
        let long = "长".repeat(80);
        let got = d(&format!(r#"{{"itemId":"7","content_title":"{long}"}}"#)).unwrap();
        assert_eq!(
            got.1.chars().count(),
            "[分享视频] ".chars().count() + 60 + 1
        );
    }

    #[test]
    fn reference_media_shapes_get_placeholders() {
        assert_eq!(
            d(r#"{"video":{"vid":"v1"},"poster":{}}"#).unwrap(),
            ("video", "[视频]".into())
        );
        assert_eq!(
            d(r#"{"resource_url":{"skey":"k"},"cover_width":1,"cover_height":2}"#).unwrap(),
            ("image", "[图片]".into())
        );
        assert_eq!(
            d(r#"{"voice_wave":[1,2]}"#).unwrap(),
            ("other", "[语音]".into())
        );
        assert_eq!(
            d(r#"{"resource_url":{"url_list":["u"]},"duration":3}"#).unwrap(),
            ("other", "[语音]".into())
        );
        assert_eq!(
            d(r#"{"display_name":"[比心]","sticker_type":1}"#).unwrap(),
            ("other", "[比心]".into())
        );
        assert_eq!(
            d(r#"{"url":{"url_list":["u"]}}"#).unwrap(),
            ("other", "[表情]".into())
        );
        assert_eq!(
            d(r#"{"display_name":"比心"}"#).unwrap(),
            ("text", "比心".into())
        );
        assert_eq!(
            d(r#"{"room_id":"9","aweType":1}"#).unwrap(),
            ("card", "[直播间分享]".into())
        );
        assert_eq!(
            d(r#"{"big_picture":{}}"#).unwrap(),
            ("image", "[图片]".into())
        );
    }

    #[test]
    fn decoded_wire_text_is_authoritative() {
        assert_eq!(
            dt(r#"{"text":"json"}"#, Some("wire")).unwrap(),
            ("text", "wire".into())
        );
        assert_eq!(dt(r#"{"command_type":1}"#, Some("wire")), None);
    }

    #[test]
    fn unknown_or_malformed_content_never_loses_a_decoded_text() {
        assert_eq!(
            d(r#"{"aweType":5}"#).unwrap(),
            ("other", "[暂不支持的消息类型]".into())
        );
        assert_eq!(dt("not json", Some("hi")).unwrap(), ("text", "hi".into()));
        // Content-less pushes (e.g. the platform's follow-up after a link) are not messages.
        assert_eq!(dt("[]", None), None);
        assert_eq!(dt("", None), None);
        assert_eq!(dt("  ", None), None);
    }
}
