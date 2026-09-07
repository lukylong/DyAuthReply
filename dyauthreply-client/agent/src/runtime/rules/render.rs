use super::{Link, Rule};
use anyhow::Result;
use chrono::{DateTime, Timelike};
use chrono_tz::Tz;
use std::{collections::BTreeMap, sync::OnceLock};
static VARIABLES: OnceLock<regex::Regex> = OnceLock::new();
pub(super) fn validate_links(links: &[Link]) -> Result<()> {
    anyhow::ensure!(
        links.len() <= 16
            && links.iter().all(|l| match l {
                Link::Url(u) => u.len() <= 4096,
                Link::Named { url, title } => url.len() <= 4096 && title.len() <= 512,
            }),
        "links exceed bounds"
    );
    Ok(())
}
pub(super) fn validate_mode(mode: Option<&str>) -> Result<()> {
    anyhow::ensure!(
        matches!(
            mode.unwrap_or("").trim(),
            "" | "multi_message" | "merged" | "card_fallback"
        ),
        "invalid send mode"
    );
    Ok(())
}
fn greeting(hour: u32) -> &'static str {
    match hour {
        5..=10 => "早上好",
        11..=13 => "中午好",
        14..=17 => "下午好",
        18..=22 => "晚上好",
        _ => "夜深了",
    }
}
fn normalize(links: &[Link]) -> Vec<(&str, &str)> {
    links
        .iter()
        .filter_map(|link| {
            let (url, title) = match link {
                Link::Url(url) => (url.as_str(), ""),
                Link::Named { url, title } => (url.as_str(), title.as_str()),
            };
            (!url.trim().is_empty()).then_some((url.trim(), title.trim()))
        })
        .collect()
}
pub(super) fn segments(rule: &Rule, nickname: &str, at: &DateTime<Tz>) -> Result<Vec<String>> {
    let template = rule.template.as_ref();
    // Executable legacy code gives nonblank rule text/nonempty rule links priority.
    let base = template.map_or(rule.reply_text.as_str(), |t| {
        if rule.reply_text.trim().is_empty() {
            t.content.as_str()
        } else {
            rule.reply_text.trim()
        }
    });
    let links = normalize(if rule.links.is_empty() {
        template.map_or(&rule.links, |t| &t.links)
    } else {
        &rule.links
    });
    let mode = rule
        .send_mode
        .as_deref()
        .filter(|m| !m.trim().is_empty())
        .or_else(|| template.and_then(|t| t.send_mode.as_deref()))
        .unwrap_or("multi_message")
        .trim();
    let cards: Vec<&str> = rule
        .card_urls
        .iter()
        .map(String::as_str)
        .filter(|u| !u.trim().is_empty())
        .collect();
    let mut context: BTreeMap<String, String> = BTreeMap::from([
        ("nickname".into(), nickname.into()),
        ("peer_nickname".into(), nickname.into()),
        ("time_greeting".into(), greeting(at.hour()).into()),
    ]);
    for (index, (url, title)) in links.iter().enumerate() {
        context.insert(format!("link_{}", index + 1), (*url).into());
        context.insert(format!("link_{}_title", index + 1), (*title).into());
    }
    let pattern = VARIABLES.get_or_init(|| {
        regex::Regex::new(r"\{\{\s*(\w+)\s*\}\}").expect("constant variable pattern")
    });
    let text = pattern
        .replace_all(base, |caps: &regex::Captures<'_>| {
            context
                .get(&caps[1])
                .cloned()
                .unwrap_or_else(|| caps[0].to_owned())
        })
        .into_owned();
    let mut result = Vec::new();
    if mode == "merged" && cards.is_empty() && links.is_empty() {
        if !text.trim().is_empty() {
            result.push(text);
        }
    } else {
        if !text.trim().is_empty() {
            result.push(text.trim().to_owned());
        }
        result.extend(cards.into_iter().map(|u| u.trim().to_owned()));
        result.extend(links.into_iter().map(|(u, _)| u.to_owned()));
    }
    anyhow::ensure!(
        result.len() <= 32
            && result.iter().all(|s| s.len() <= 16384)
            && result.iter().map(String::len).sum::<usize>() <= 65536,
        "rendered reply exceeds bounds"
    );
    Ok(result)
}
