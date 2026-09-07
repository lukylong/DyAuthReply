//! Immutable bounded native snapshots of the ACTUAL `DouyinRule` client contract.
//! Matching/rendering has no network, database access, or Python subprocess.
use anyhow::{Context, Result};
use chrono::{DateTime, Datelike, NaiveTime, Utc};
use chrono_tz::Tz;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};
mod registry;
mod render;
pub use registry::RuleRegistry;
use sha2::{Digest, Sha256};

#[derive(Clone, Deserialize, Serialize)]
#[serde(untagged)]
pub enum Link {
    Url(String),
    Named {
        url: String,
        #[serde(default)]
        title: String,
    },
}
#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Template {
    #[serde(default)]
    pub content: String,
    #[serde(default)]
    pub links: Vec<Link>,
    #[serde(default)]
    pub send_mode: Option<String>,
}
#[derive(Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MatchType {
    Contains,
    Regex,
    Default,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Rule {
    pub id: String,
    #[serde(default)]
    pub account_ids: Vec<String>,
    #[serde(default = "yes")]
    pub status: bool,
    #[serde(default)]
    pub priority: i32,
    #[serde(default)]
    pub created_at_ms: i64,
    pub match_type: MatchType,
    #[serde(default)]
    pub keywords: Vec<String>,
    #[serde(default)]
    pub regex_pattern: Option<String>,
    #[serde(default)]
    pub reply_text: String,
    #[serde(default)]
    pub links: Vec<Link>,
    #[serde(default)]
    pub card_urls: Vec<String>,
    #[serde(default)]
    pub send_mode: Option<String>,
    #[serde(default)]
    pub template: Option<Template>,
    #[serde(default)]
    pub cooldown_seconds: u32,
    #[serde(default)]
    pub time_window_start: Option<String>,
    #[serde(default)]
    pub time_window_end: Option<String>,
    #[serde(default = "week")]
    pub weekday_mask: String,
    #[serde(default = "dm")]
    pub channel: String,
}
const fn yes() -> bool {
    true
}
fn week() -> String {
    "1111111".into()
}
fn dm() -> String {
    "dm".into()
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RuleSnapshot {
    pub version: u32,
    pub revision: u64,
    pub timezone: String,
    pub rules: Vec<Rule>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MatchInput {
    pub account_id: String,
    pub text: String,
    #[serde(default = "dm")]
    pub channel: String,
    pub at_ms: i64,
    #[serde(default)]
    pub peer_nickname: String,
}
#[derive(Serialize, Debug, PartialEq, Eq)]
pub struct ReplyPlan {
    pub rule_id: String,
    pub revision: u64,
    pub cooldown_seconds: u32,
    pub segments: Vec<String>,
    pub day_start_ms: i64,
}
struct Compiled {
    rule: Rule,
    keywords: Vec<String>,
    regex: Option<fancy_regex::Regex>,
    start: Option<NaiveTime>,
    end: Option<NaiveTime>,
}
pub struct RuleEngine {
    revision: u64,
    digest: String,
    timezone: Tz,
    rules: Vec<Compiled>,
    bound: BTreeMap<String, Vec<usize>>,
    global: Vec<usize>,
}
impl RuleEngine {
    /// Validates/compiles once before publishing a snapshot. An invalid pattern
    /// fails the whole new revision rather than silently disabling that rule.
    /// # Errors
    /// Rejects malformed/oversized snapshots, duplicate IDs and invalid regex/timezone.
    pub fn from_json(raw: &[u8]) -> Result<Self> {
        anyhow::ensure!(raw.len() <= 1024 * 1024, "rule snapshot exceeds1MiB");
        let mut snapshot: RuleSnapshot =
            serde_json::from_slice(raw).context("invalid rule snapshot")?;
        anyhow::ensure!(
            snapshot.version == 1 && snapshot.revision > 0 && snapshot.rules.len() <= 512,
            "unsupported snapshot or rule limit"
        );
        let timezone = snapshot
            .timezone
            .parse::<Tz>()
            .context("invalid rule timezone")?;
        let digest = format!("{:x}", Sha256::digest(serde_json::to_vec(&snapshot)?));
        let mut ids = HashSet::new();
        let mut regex_count = 0;
        for r in &snapshot.rules {
            validate(r)?;
            anyhow::ensure!(ids.insert(r.id.clone()), "duplicate rule ID");
            if r.status && r.match_type == MatchType::Regex {
                regex_count += 1;
            }
        }
        anyhow::ensure!(regex_count <= 64, "too many regular expressions");
        // Stable ordering preserves source order for exact priority/time ties.
        snapshot.rules.sort_by_key(|r| {
            (
                std::cmp::Reverse(r.priority),
                std::cmp::Reverse(r.created_at_ms),
            )
        });
        let rules = snapshot
            .rules
            .into_iter()
            .map(compile)
            .collect::<Result<Vec<_>>>()?;
        let mut bound: BTreeMap<String, Vec<usize>> = BTreeMap::new();
        let mut global = Vec::new();
        for (index, compiled) in rules.iter().enumerate() {
            if compiled.rule.account_ids.is_empty() {
                global.push(index);
            } else {
                for account in &compiled.rule.account_ids {
                    bound.entry(account.clone()).or_default().push(index);
                }
            }
        }
        Ok(Self {
            revision: snapshot.revision,
            digest,
            timezone,
            rules,
            bound,
            global,
        })
    }
    #[must_use]
    pub fn timezone_name(&self) -> &str {
        self.timezone.name()
    }
    #[must_use]
    pub fn rule_enabled_for(&self, account: &str, id: &str) -> bool {
        self.rules.iter().any(|r| {
            r.rule.id == id
                && r.rule.status
                && (r.rule.account_ids.is_empty()
                    || r.rule.account_ids.iter().any(|a| a == account))
        })
    }
    #[must_use]
    pub fn revision(&self) -> u64 {
        self.revision
    }
    #[must_use]
    pub fn rule_count(&self) -> usize {
        self.rules.len()
    }
    /// Returns a durable-ready plan without executing it. A regex evaluation
    /// error defers the decision; it MUST NOT turn into fallback/no-match.
    /// # Errors
    /// Rejects oversized input, regex budget exhaustion, invalid time, or oversized output.
    pub fn evaluate(&self, input: &MatchInput) -> Result<Option<ReplyPlan>> {
        anyhow::ensure!(
            !input.account_id.is_empty()
                && input.account_id.len() <= 128
                && input.text.len() <= 4096
                && input.peer_nickname.len() <= 256
                && matches!(input.channel.as_str(), "dm" | "comment"),
            "invalid matcher input"
        );
        let instant = DateTime::<Utc>::from_timestamp_millis(input.at_ms)
            .context("invalid rule evaluation time")?;
        let local = instant.with_timezone(&self.timezone);
        let low = input.text.to_lowercase();
        let mut fallback = None;
        let mut matched = None;
        let indices = self
            .bound
            .get(&input.account_id)
            .into_iter()
            .flatten()
            .chain(self.global.iter());
        for index in indices {
            let rule = &self.rules[*index];
            if !effective(rule, &input.channel, &local) {
                continue;
            }
            if rule.rule.match_type == MatchType::Default {
                if fallback.is_none() {
                    fallback = Some(rule);
                }
            } else if match_text(rule, &input.text, &low)? {
                matched = Some(rule);
                break;
            }
        }
        let Some(compiled) = matched.or(fallback) else {
            return Ok(None);
        };
        let segments = render::segments(&compiled.rule, &input.peer_nickname, &local)?;
        let day_start = local
            .date_naive()
            .and_hms_opt(0, 0, 0)
            .context("invalid day start")?
            .and_local_timezone(self.timezone)
            .earliest()
            .context("local day start does not exist")?;
        Ok(Some(ReplyPlan {
            rule_id: compiled.rule.id.clone(),
            revision: self.revision,
            cooldown_seconds: compiled.rule.cooldown_seconds,
            segments,
            day_start_ms: day_start.timestamp_millis(),
        }))
    }
}
fn validate(rule: &Rule) -> Result<()> {
    anyhow::ensure!(
        !rule.id.is_empty()
            && rule.id.len() <= 128
            && rule.account_ids.len() <= 300
            && rule
                .account_ids
                .iter()
                .all(|a| !a.is_empty() && a.len() <= 128),
        "invalid rule identity/binding"
    );
    anyhow::ensure!(
        rule.account_ids.iter().collect::<HashSet<_>>().len() == rule.account_ids.len(),
        "duplicate account binding"
    );
    anyhow::ensure!(
        rule.keywords.len() <= 128
            && rule.keywords.iter().all(|k| k.len() <= 512)
            && rule.regex_pattern.as_ref().is_none_or(|p| p.len() <= 512)
            && rule.reply_text.len() <= 16384
            && rule.card_urls.len() <= 16
            && rule.card_urls.iter().all(|u| u.len() <= 4096)
            && rule.cooldown_seconds <= 31_536_000
            && rule.weekday_mask.len() <= 32
            && matches!(rule.channel.as_str(), "dm" | "comment" | "all"),
        "rule exceeds supported bounds"
    );
    render::validate_links(&rule.links)?;
    render::validate_mode(rule.send_mode.as_deref())?;
    if let Some(t) = &rule.template {
        anyhow::ensure!(t.content.len() <= 16384, "template too long");
        render::validate_links(&t.links)?;
        render::validate_mode(t.send_mode.as_deref())?;
    }
    Ok(())
}
fn compile(rule: Rule) -> Result<Compiled> {
    let regex = if rule.status && rule.match_type == MatchType::Regex {
        match rule.regex_pattern.as_deref().filter(|p| !p.is_empty()) {
            Some(pattern) => Some(
                fancy_regex::RegexBuilder::new(pattern)
                    .case_insensitive(true)
                    .dot_matches_new_line(true)
                    .backtrack_limit(10_000)
                    .delegate_size_limit(128 * 1024)
                    .delegate_dfa_size_limit(256 * 1024)
                    .build()
                    .with_context(|| format!("invalid or unsupported regex in rule {}", rule.id))?,
            ),
            None => None,
        }
    } else {
        None
    };
    let parse = |text: &Option<String>| -> Result<Option<NaiveTime>> {
        text.as_deref()
            .filter(|v| !v.is_empty())
            .map(|v| {
                NaiveTime::parse_from_str(v, "%H:%M:%S%.f")
                    .or_else(|_| NaiveTime::parse_from_str(v, "%H:%M"))
                    .context("invalid time window")
            })
            .transpose()
    };
    let start = parse(&rule.time_window_start)?;
    let end = parse(&rule.time_window_end)?;
    let keywords = rule
        .keywords
        .iter()
        .filter(|k| !k.is_empty())
        .map(|k| k.to_lowercase())
        .collect();
    Ok(Compiled {
        rule,
        keywords,
        regex,
        start,
        end,
    })
}
fn effective(rule: &Compiled, channel: &str, at: &DateTime<Tz>) -> bool {
    let r = &rule.rule;
    if !r.status || (r.channel != "all" && r.channel != channel) {
        return false;
    }
    if r.weekday_mask.chars().count() == 7
        && r.weekday_mask
            .chars()
            .nth(at.weekday().num_days_from_monday() as usize)
            != Some('1')
    {
        return false;
    }
    match (rule.start, rule.end) {
        (Some(start), Some(end)) if start <= end => start <= at.time() && at.time() <= end,
        (Some(start), Some(end)) => at.time() >= start || at.time() <= end,
        _ => true,
    }
}
fn match_text(rule: &Compiled, text: &str, low: &str) -> Result<bool> {
    match rule.rule.match_type {
        MatchType::Contains => Ok(rule.keywords.iter().any(|k| low.contains(k))),
        MatchType::Regex => rule.regex.as_ref().map_or(Ok(false), |r| {
            r.is_match(text)
                .context("regex evaluation budget/error; keep receipt pending")
        }),
        MatchType::Default => Ok(true),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn actual_python_client_matcher_and_renderer_parity() {
        let corpus: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/rule_engine.json")).unwrap();
        for case in corpus["cases"].as_array().unwrap() {
            let engine =
                RuleEngine::from_json(&serde_json::to_vec(&case["snapshot"]).unwrap()).unwrap();
            let input = serde_json::from_value(case["input"].clone()).unwrap();
            let actual = serde_json::to_value(engine.evaluate(&input).unwrap()).unwrap();
            assert_eq!(actual, case["expected"], "case {}", case["name"]);
        }
    }
    #[test]
    fn invalid_new_revision_is_rejected_instead_of_dropping_regex_rule() {
        let corpus: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/rule_engine.json")).unwrap();
        let mut snapshot = corpus["cases"][0]["snapshot"].clone();
        snapshot["rules"][0]["match_type"] = serde_json::json!("regex");
        snapshot["rules"][0]["regex_pattern"] = serde_json::json!("(");
        assert!(RuleEngine::from_json(&serde_json::to_vec(&snapshot).unwrap()).is_err());
        snapshot["rules"][0]["regex_pattern"] = serde_json::json!("x");
        snapshot["timezone"] = serde_json::json!("not/a/zone");
        assert!(RuleEngine::from_json(&serde_json::to_vec(&snapshot).unwrap()).is_err());
    }
    #[test]
    fn bounds_reject_overlarge_snapshot_input_and_rendered_output() {
        assert!(RuleEngine::from_json(&vec![b' '; 1024 * 1024 + 1]).is_err());
        let corpus: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/rule_engine.json")).unwrap();
        let engine =
            RuleEngine::from_json(&serde_json::to_vec(&corpus["cases"][0]["snapshot"]).unwrap())
                .unwrap();
        let mut input: MatchInput =
            serde_json::from_value(corpus["cases"][0]["input"].clone()).unwrap();
        input.text = "x".repeat(4097);
        assert!(engine.evaluate(&input).is_err());
        let mut snapshot = corpus["cases"][0]["snapshot"].clone();
        snapshot["rules"][0]["reply_text"] = serde_json::json!("{{nickname}}".repeat(1000));
        let engine = RuleEngine::from_json(&serde_json::to_vec(&snapshot).unwrap()).unwrap();
        input.text = "hello".into();
        input.peer_nickname = "x".repeat(256);
        assert!(engine.evaluate(&input).is_err());
    }
    #[test]
    fn registry_is_whole_revision_atomic_and_conflicts_retain_previous() {
        use std::sync::Arc;
        let corpus: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/rule_engine.json")).unwrap();
        let mut snapshot = corpus["cases"][0]["snapshot"].clone();
        let compile = |v: &serde_json::Value| {
            Arc::new(RuleEngine::from_json(&serde_json::to_vec(v).unwrap()).unwrap())
        };
        let registry = RuleRegistry::default();
        registry.publish(compile(&snapshot)).unwrap();
        let pinned = registry.snapshot().unwrap().unwrap();
        registry.publish(compile(&snapshot)).unwrap();
        snapshot["rules"][0]["reply_text"] = serde_json::json!("changed");
        assert!(registry.publish(compile(&snapshot)).is_err());
        assert_eq!(registry.snapshot().unwrap().unwrap().digest, pinned.digest);
        snapshot["revision"] = serde_json::json!(8);
        registry.publish(compile(&snapshot)).unwrap();
        assert_eq!(registry.snapshot().unwrap().unwrap().revision(), 8);
        assert_eq!(pinned.revision(), 7);
        snapshot["revision"] = serde_json::json!(6);
        assert!(registry.publish(compile(&snapshot)).is_err());
    }
    #[test]
    fn regex_budget_failure_does_not_select_default_reply() {
        let raw = serde_json::json!({"version":1,"revision":1,"timezone":"Asia/Shanghai","rules":[
            {"id":"regex","match_type":"regex","regex_pattern":"x","reply_text":"match"},
            {"id":"fallback","match_type":"default","reply_text":"fallback"}]});
        let mut engine = RuleEngine::from_json(&serde_json::to_vec(&raw).unwrap()).unwrap();
        engine.rules[0].regex = Some(
            fancy_regex::RegexBuilder::new(r"(?i)(a|b|ab)*(?>c)")
                .seek(false)
                .backtrack_limit(1)
                .build()
                .unwrap(),
        );
        let input = MatchInput {
            account_id: "a".into(),
            text: "ab".repeat(30),
            channel: "dm".into(),
            at_ms: 1_788_600_000_000,
            peer_nickname: String::new(),
        };
        assert!(engine.evaluate(&input).is_err());
    }
    #[test]
    fn indexed_bindings_route_three_hundred_accounts_independently() {
        let rules: Vec<_> = (0..300)
            .map(|i| {
                serde_json::json!({"id":format!("rule-{i}"),"account_ids":[format!("account-{i}")],
            "match_type":"contains","keywords":["hello"],"reply_text":format!("reply-{i}")})
            })
            .collect();
        let raw =
            serde_json::json!({"version":1,"revision":1,"timezone":"Asia/Shanghai","rules":rules});
        let engine = RuleEngine::from_json(&serde_json::to_vec(&raw).unwrap()).unwrap();
        for i in 0..300 {
            let input = MatchInput {
                account_id: format!("account-{i}"),
                text: "hello".into(),
                channel: "dm".into(),
                at_ms: 1_788_600_000_000,
                peer_nickname: String::new(),
            };
            let plan = engine.evaluate(&input).unwrap().unwrap();
            assert_eq!(plan.rule_id, format!("rule-{i}"));
            assert_eq!(plan.segments, vec![format!("reply-{i}")]);
        }
    }
    #[test]
    fn regex_count_is_bounded_independently_of_overall_rule_count() {
        let rules:Vec<_>=(0..65).map(|i|serde_json::json!({"id":format!("regex-{i}"),"match_type":"regex","regex_pattern":"x","reply_text":"x"})).collect();
        let raw =
            serde_json::json!({"version":1,"revision":1,"timezone":"Asia/Shanghai","rules":rules});
        assert!(RuleEngine::from_json(&serde_json::to_vec(&raw).unwrap()).is_err());
    }
}
