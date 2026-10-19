//! Display metadata only: never derive or replace licensing identity from a hostname.
use serde_json::{json, Value};

fn normalize(raw: &str) -> Option<String> {
    let name: String = raw.chars().filter(|c| !c.is_control()).take(128).collect();
    let name = name.trim();
    if name.is_empty()
        || matches!(
            name.to_ascii_lowercase().as_str(),
            "本地设备" | "localhost" | "localhost.localdomain" | "unknown"
        )
    {
        return None;
    }
    Some(name.into())
}

pub(super) fn refresh(state: &mut Value, hostname: Option<&str>, os: &str) -> bool {
    let current = state["device_name"].as_str().unwrap_or_default();
    let source = state["device_name_source"].as_str().unwrap_or_default();
    let automatic = matches!(source, "system" | "fallback");
    if !automatic && normalize(current).is_some() {
        return false; // Preserve an existing custom label.
    }
    let (name, source) = if let Some(name) = hostname.and_then(normalize) {
        (name, "system")
    } else if automatic && normalize(current).is_some() {
        return false; // A temporary OS lookup failure must not erase a known name.
    } else {
        let platform = match os {
            "windows" => "Windows",
            "macos" => "macOS",
            "linux" => "Linux",
            _ => "设备",
        };
        let suffix: String = state["device_fingerprint"]
            .as_str()
            .unwrap_or_default()
            .chars()
            .filter(char::is_ascii_alphanumeric)
            .take(8)
            .collect();
        (
            format!(
                "{platform}-{}",
                if suffix.is_empty() {
                    "unknown"
                } else {
                    &suffix
                }
            ),
            "fallback",
        )
    };
    if state["device_name"] == name && state["device_name_source"] == source {
        return false;
    }
    state["device_name"] = json!(name);
    state["device_name_source"] = json!(source);
    true
}

pub(super) fn machine_meta(state: &Value) -> Value {
    json!({"runtime":"rust", "device_name":state["device_name"],
        "device_name_source":state["device_name_source"].as_str().unwrap_or("custom")})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn device_name_upgrade_changes_only_display_metadata() {
        let mut value = json!({"device_name":"本地设备","device_fingerprint":"stable-identity",
            "activation_id":"same-activation","lease_token":"same-signed-token","refresh_token":"same-refresh"});
        assert!(refresh(&mut value, Some("DESKTOP-DEMO"), "windows"));
        assert_eq!(value["device_name"], "DESKTOP-DEMO");
        assert_eq!(value["device_fingerprint"], "stable-identity");
        assert_eq!(value["activation_id"], "same-activation");
        assert_eq!(value["lease_token"], "same-signed-token");
        assert_eq!(value["refresh_token"], "same-refresh");
        assert_eq!(machine_meta(&value)["device_name"], "DESKTOP-DEMO");
        assert!(!refresh(&mut value, Some("DESKTOP-DEMO"), "windows"));
    }
    #[test]
    fn device_name_fallback_is_distinct_and_recovers_when_hostname_returns() {
        let mut a = json!({"device_fingerprint":"11112222aaaa"});
        let mut b = json!({"device_fingerprint":"33334444bbbb"});
        assert!(refresh(&mut a, None, "windows"));
        assert!(refresh(&mut b, Some("localhost"), "windows"));
        assert_eq!(a["device_name"], "Windows-11112222");
        assert_ne!(a["device_name"], b["device_name"]);
        assert!(refresh(&mut a, Some("电脑A"), "windows"));
        assert!(!refresh(&mut a, None, "windows"));
        assert_eq!(a["device_name"], "电脑A");
    }
    #[test]
    fn device_name_custom_labels_are_preserved_and_system_renames_follow() {
        let mut value = json!({"device_name":"客服电脑A"});
        assert!(!refresh(&mut value, Some("DESKTOP-B"), "windows"));
        value["device_name_source"] = json!("system");
        assert!(refresh(&mut value, Some("DESKTOP-B"), "windows"));
        assert_eq!(value["device_name"], "DESKTOP-B");
    }
    #[test]
    fn device_name_unicode_and_control_characters_are_bounded() {
        let mut value = json!({"device_fingerprint":"stable"});
        assert!(refresh(
            &mut value,
            Some(&format!("电脑\n{}", "机".repeat(200))),
            "windows"
        ));
        let name = value["device_name"].as_str().unwrap();
        assert_eq!(name.chars().count(), 128);
        assert!(!name.contains('\n'));
    }
}
