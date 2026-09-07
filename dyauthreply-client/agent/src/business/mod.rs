//! Durable native business configuration. One compiled generation couples rules and policies.
use crate::runtime::{
    messaging::AutomationPolicy,
    rules::{Rule, RuleEngine, RuleSnapshot},
};
use anyhow::{Context, Result};
use fs2::FileExt;
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, HashSet},
    fs::{File, OpenOptions},
    path::Path,
    sync::{Arc, Mutex, RwLock},
};
pub mod api;
mod import;
pub use import::import_legacy;
#[cfg(test)]
mod tests;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Document {
    pub version: u32,
    pub revision: u64,
    pub timezone: String,
    pub rules: Vec<Value>,
    pub templates: Vec<Value>,
    pub cards: Vec<Value>,
    pub policies: Vec<AutomationPolicy>,
}
pub struct Snapshot {
    pub document: Document,
    pub engine: Arc<RuleEngine>,
    pub policies: BTreeMap<String, AutomationPolicy>,
}
pub struct BusinessStore {
    _lock: File,
    db: Mutex<Connection>,
    current: RwLock<Arc<Snapshot>>,
}
impl Document {
    #[must_use]
    pub fn seed(rules: Vec<Value>, policies: Vec<AutomationPolicy>) -> Self {
        Self {
            version: 1,
            revision: 1,
            timezone: "Asia/Shanghai".into(),
            rules,
            templates: vec![],
            cards: vec![],
            policies,
        }
    }
    /// # Errors
    /// Rejects invalid references, account bindings and oversized rule generations.
    pub fn compile(self) -> Result<Snapshot> {
        anyhow::ensure!(
            self.version == 1
                && self.revision > 0
                && serde_json::to_vec(&self)?.len() <= 2 * 1024 * 1024
                && self.policies.len() <= 3000
                && self.templates.len() <= 512
                && self.cards.len() <= 512,
            "业务配置超过容量或版本不匹配"
        );
        let mut policies = BTreeMap::new();
        for policy in &self.policies {
            policy.validate()?;
            anyhow::ensure!(
                policies
                    .insert(policy.account_id.clone(), policy.clone())
                    .is_none(),
                "账号策略重复"
            );
        }
        validate_catalog(&self.templates, "name")?;
        validate_catalog(&self.cards, "title")?;
        for card in &self.cards {
            validate_card(card)?;
        }
        let mut rules = Vec::new();
        let mut assigned = HashSet::new();
        for raw in &self.rules {
            let mut rule = decode_rule(raw)?;
            for id in &rule.account_ids {
                anyhow::ensure!(policies.contains_key(id), "规则关联账号不存在");
                anyhow::ensure!(assigned.insert(id.clone()), "账号已被其他规则绑定");
            }
            if let Some(id) = raw["template_id"].as_str().filter(|s| !s.is_empty()) {
                let template = self
                    .templates
                    .iter()
                    .find(|t| t["id"] == id)
                    .context("引用模板不存在")?;
                rule.template = Some(serde_json::from_value(
                    json!({"content":template["content"].as_str().unwrap_or(""),"links":template.get("links").cloned().unwrap_or_else(||json!([])),"send_mode":template.get("send_mode").cloned().unwrap_or(Value::Null)}),
                )?);
            }
            let card_ids = raw.get("card_ids").cloned().unwrap_or_else(|| json!([]));
            let card_ids: Vec<String> = serde_json::from_value(card_ids)?;
            anyhow::ensure!(card_ids.len() <= 16, "引用卡片过多");
            for id in card_ids {
                let card = self
                    .cards
                    .iter()
                    .find(|c| c["id"] == id)
                    .context("引用卡片不存在")?;
                if card["status"] == false
                    || card["sync_state"]
                        .as_str()
                        .is_some_and(|state| state != "synced")
                {
                    continue;
                }
                let url = card["landing_url"]
                    .as_str()
                    .filter(|s| s.starts_with("http://") || s.starts_with("https://"))
                    .context("卡片落地地址尚未配置")?;
                rule.card_urls.push(url.into());
            }
            rules.push(rule);
        }
        let engine = Arc::new(RuleEngine::from_json(&serde_json::to_vec(
            &RuleSnapshot {
                version: 1,
                revision: self.revision,
                timezone: self.timezone.clone(),
                rules,
            },
        )?)?);
        Ok(Snapshot {
            document: self,
            engine,
            policies,
        })
    }
}
fn validate_catalog(rows: &[Value], label: &str) -> Result<()> {
    let mut ids = HashSet::new();
    for row in rows {
        let id = row["id"].as_str().context("配置缺少ID")?;
        anyhow::ensure!(
            !id.is_empty()
                && id.len() <= 128
                && ids.insert(id)
                && row[label]
                    .as_str()
                    .is_some_and(|s| !s.trim().is_empty() && s.len() <= 600)
                && row.to_string().len() <= 65536,
            "模板或卡片字段无效"
        );
    }
    Ok(())
}
fn decode_rule(raw: &Value) -> Result<Rule> {
    let mut value = raw.clone();
    let map = value.as_object_mut().context("规则必须为对象")?;
    if let Some(name) = map.get("name") {
        anyhow::ensure!(
            name.as_str()
                .is_some_and(|s| !s.trim().is_empty() && s.len() <= 600),
            "规则名称不能为空或过长"
        );
    }
    for key in [
        "name",
        "template_id",
        "card_ids",
        "remark",
        "account_id",
        "account_nickname",
        "account_nicknames",
        "match_type_display",
    ] {
        map.remove(key);
    }
    for key in ["time_window_start", "time_window_end", "regex_pattern"] {
        if map.get(key).is_some_and(|v| v.as_str() == Some("")) {
            map.insert(key.into(), Value::Null);
        }
    }
    Ok(serde_json::from_value(value)?)
}
impl BusinessStore {
    /// # Errors
    /// Rejects concurrent writers, corrupt documents or invalid persisted generations.
    pub fn open(root: &Path, seed: Document) -> Result<Self> {
        std::fs::create_dir_all(root)?;
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let lock = options.open(root.join("business.lock"))?;
        lock.try_lock_exclusive()
            .context("业务配置已被其他进程占用")?;
        let path = root.join("business.sqlite3");
        let mut db = Connection::open(&path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        }
        db.busy_timeout(std::time::Duration::from_secs(2))?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA journal_size_limit=4194304; PRAGMA max_page_count=4096; CREATE TABLE IF NOT EXISTS configuration(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,payload TEXT NOT NULL);")?;
        let tx = db.transaction()?;
        let raw: Option<(u64, String)> = tx
            .query_row(
                "SELECT revision,payload FROM configuration WHERE singleton=1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?;
        let document = if let Some((revision, raw)) = raw {
            anyhow::ensure!(raw.len() <= 2 * 1024 * 1024, "持久化配置超限");
            let doc: Document = serde_json::from_str(&raw)?;
            anyhow::ensure!(doc.revision == revision, "配置版本记录不一致");
            doc
        } else {
            seed
        };
        let snapshot = document.compile()?;
        tx.execute(
            "INSERT OR IGNORE INTO configuration(singleton,revision,payload) VALUES(1,?1,?2)",
            params![
                snapshot.document.revision,
                serde_json::to_string(&snapshot.document)?
            ],
        )?;
        tx.commit()?;
        Ok(Self {
            _lock: lock,
            db: Mutex::new(db),
            current: RwLock::new(Arc::new(snapshot)),
        })
    }
    /// # Errors
    /// Does not hide poisoned/corrupt state as empty configuration.
    pub fn snapshot(&self) -> Result<Arc<Snapshot>> {
        Ok(self
            .current
            .read()
            .map_err(|_| anyhow::anyhow!("配置状态不可用"))?
            .clone())
    }
    /// Validates/compiles before a single durable commit and publication. An error leaves both old.
    /// # Errors
    /// Reports validation/conflict or persistence errors without partial runtime publication.
    pub fn change(&self, edit: impl FnOnce(&mut Document) -> Result<Value>) -> Result<Value> {
        let mut db = self
            .db
            .lock()
            .map_err(|_| anyhow::anyhow!("配置存储不可用"))?;
        let mut current = self
            .current
            .write()
            .map_err(|_| anyhow::anyhow!("配置状态不可用"))?;
        let mut document = current.document.clone();
        let response = edit(&mut document)?;
        document.revision = document
            .revision
            .checked_add(1)
            .context("配置版本已达上限")?;
        let compiled = Arc::new(document.compile()?);
        let tx = db.transaction()?;
        anyhow::ensure!(
            tx.execute(
                "UPDATE configuration SET revision=?1,payload=?2 WHERE singleton=1 AND revision=?3",
                params![
                    compiled.document.revision,
                    serde_json::to_string(&compiled.document)?,
                    current.document.revision
                ]
            )? == 1,
            "配置版本已变更，请刷新后重试"
        );
        tx.commit()?;
        *current = compiled;
        Ok(response)
    }
}

#[derive(Clone)]
pub enum Edit {
    Rule {
        id: Option<String>,
        input: Value,
    },
    DeleteRule(String),
    CloneRule(String),
    Template {
        id: Option<String>,
        input: Value,
    },
    DeleteTemplate(String),
    Account {
        id: String,
        input: Value,
    },
    EmergencyStop,
    Card {
        id: Option<String>,
        input: Value,
    },
    CardSync {
        id: String,
        sync_state: String,
        landing_url: Option<String>,
        cover_url: Option<String>,
    },
    BeginDeleteCard(String),
    FinishDeleteCard(String),
}
impl Edit {
    /// # Errors
    /// Rejects malformed updates and reference/account conflicts.
    pub fn apply(self, doc: &mut Document) -> Result<Value> {
        match self {
            Self::Rule { id, input } => edit_rule(doc, id.as_deref(), input),
            Self::DeleteRule(id) => {
                let n = doc.rules.len();
                doc.rules.retain(|r| r["id"] != id);
                anyhow::ensure!(doc.rules.len() < n, "规则不存在");
                Ok(json!({"success":true}))
            }
            Self::CloneRule(id) => {
                let mut rule = doc
                    .rules
                    .iter()
                    .find(|r| r["id"] == id)
                    .context("规则不存在")?
                    .clone();
                rule["id"] = json!(uuid::Uuid::new_v4().to_string());
                rule["name"] = json!(format!(
                    "{}（副本）",
                    rule["name"].as_str().unwrap_or("规则")
                ));
                rule["account_ids"] = json!([]);
                rule["status"] = json!(false);
                rule["created_at_ms"] = json!(crate::workbench::now_ms());
                doc.rules.push(rule.clone());
                Ok(rule)
            }
            Self::Template { id, input } => edit_template(doc, id, input),
            Self::DeleteTemplate(id) => {
                anyhow::ensure!(
                    !doc.rules.iter().any(|r| r["template_id"] == id),
                    "模板正被规则使用"
                );
                let n = doc.templates.len();
                doc.templates.retain(|r| r["id"] != id);
                anyhow::ensure!(n > doc.templates.len(), "模板不存在");
                Ok(json!({"success":true}))
            }
            Self::Account { id, input } => edit_account(doc, &id, &input),
            Self::EmergencyStop => {
                let mut disabled = 0usize;
                for policy in &mut doc.policies {
                    if policy.enabled {
                        disabled += 1;
                        policy.enabled = false;
                    }
                }
                Ok(json!({"disabled_policies":disabled}))
            }
            Self::Card { id, input } => edit_card(doc, id.as_deref(), input),
            Self::CardSync {
                id,
                sync_state,
                landing_url,
                cover_url,
            } => card_sync(doc, &id, &sync_state, landing_url, cover_url),
            Self::BeginDeleteCard(id) => begin_delete_card(doc, &id),
            Self::FinishDeleteCard(id) => {
                let before = doc.cards.len();
                doc.cards.retain(|card| card["id"] != id);
                anyhow::ensure!(doc.cards.len() < before, "卡片不存在");
                Ok(json!({"success":true}))
            }
        }
    }
}
fn allowed(input: &Value, keys: &[&str]) -> Result<()> {
    anyhow::ensure!(
        input
            .as_object()
            .is_some_and(|m| m.keys().all(|k| keys.contains(&k.as_str()))),
        "配置包含未知字段"
    );
    Ok(())
}
fn merge(target: &mut Value, patch: Value) {
    if let (Some(t), Value::Object(p)) = (target.as_object_mut(), patch) {
        t.extend(p);
    }
}
fn edit_rule(doc: &mut Document, id: Option<&str>, mut input: Value) -> Result<Value> {
    allowed(
        &input,
        &[
            "name",
            "account_id",
            "account_ids",
            "force_move",
            "match_type",
            "keywords",
            "regex_pattern",
            "reply_text",
            "links",
            "send_mode",
            "template_id",
            "card_ids",
            "priority",
            "status",
            "cooldown_seconds",
            "channel",
            "weekday_mask",
            "time_window_start",
            "time_window_end",
            "remark",
        ],
    )?;
    let force = input["force_move"].as_bool().unwrap_or(false);
    let map = input.as_object_mut().context("规则格式错误")?;
    map.remove("force_move");
    if !map.contains_key("account_ids") {
        if let Some(id) = map.get("account_id") {
            let ids = id.as_str().map_or_else(|| json!([]), |id| json!([id]));
            map.insert("account_ids".into(), ids);
        }
    }
    map.remove("account_id");
    let mut rule = if let Some(id) = id {
        doc.rules
            .iter()
            .find(|r| r["id"] == id)
            .context("规则不存在")?
            .clone()
    } else {
        json!({"id":uuid::Uuid::new_v4().to_string(),"name":"","account_ids":[],"status":true,"match_type":"contains","keywords":[],"reply_text":"","priority":0,"cooldown_seconds":300,"weekday_mask":"1111111","channel":"dm","created_at_ms":crate::workbench::now_ms()})
    };
    merge(&mut rule, input);
    let parsed = decode_rule(&rule)?;
    let mut conflicts = Vec::new();
    for other in &doc.rules {
        if other["id"] == rule["id"] {
            continue;
        }
        for aid in &parsed.account_ids {
            if other["account_ids"]
                .as_array()
                .is_some_and(|ids| ids.contains(&json!(aid)))
            {
                conflicts.push(json!({"account_id":aid,"account_nickname":aid,"rule_id":other["id"],"rule_name":other["name"]}));
            }
        }
    }
    anyhow::ensure!(
        force || conflicts.is_empty(),
        "{}",
        json!({"code":"account_conflict","conflicts":conflicts})
    );
    if force {
        for other in &mut doc.rules {
            if other["id"] == rule["id"] {
                continue;
            }
            if let Some(ids) = other["account_ids"].as_array_mut() {
                let had = !ids.is_empty();
                ids.retain(|v| !parsed.account_ids.iter().any(|id| v == id));
                // Losing the last bound account must never turn a rule into a global sender.
                if had && ids.is_empty() {
                    other["status"] = json!(false);
                }
            }
        }
    }
    if let Some(index) = doc.rules.iter().position(|r| r["id"] == rule["id"]) {
        doc.rules[index] = rule.clone();
    } else {
        doc.rules.push(rule.clone());
    }
    Ok(rule)
}
fn edit_template(doc: &mut Document, id: Option<String>, input: Value) -> Result<Value> {
    allowed(
        &input,
        &["name", "content", "status", "remark", "links", "send_mode"],
    )?;
    let mut row = if let Some(id) = id {
        doc.templates
            .iter()
            .find(|r| r["id"] == id)
            .context("模板不存在")?
            .clone()
    } else {
        json!({"id":uuid::Uuid::new_v4().to_string(),"name":"","content":"","status":true})
    };
    merge(&mut row, input);
    anyhow::ensure!(
        row["content"].as_str().is_some_and(|s| s.len() <= 16384),
        "模板内容无效或过长"
    );
    if let Some(i) = doc.templates.iter().position(|r| r["id"] == row["id"]) {
        doc.templates[i] = row.clone();
    } else {
        doc.templates.push(row.clone());
    }
    Ok(row)
}
fn edit_account(doc: &mut Document, id: &str, input: &Value) -> Result<Value> {
    allowed(
        input,
        &[
            "auto_reply_enabled",
            "daily_reply_quota",
            "min_interval_seconds",
            "max_interval_seconds",
            "silent_start",
            "silent_end",
            "daily_peer_limit",
        ],
    )?;
    let policy = doc
        .policies
        .iter_mut()
        .find(|p| p.account_id == id)
        .context("账号策略不存在")?;
    let mut raw = serde_json::to_value(&*policy)?;
    for (key, value) in input.as_object().context("账号配置格式错误")? {
        raw[match key.as_str() {
            "auto_reply_enabled" => "enabled",
            "daily_reply_quota" => "daily_quota",
            _ => key,
        }] = value.clone();
    }
    let mut updated: AutomationPolicy = serde_json::from_value(raw)?;
    if updated.enabled && !policy.enabled {
        updated.enabled_since_us = u64::try_from(crate::workbench::now_ms())? * 1000;
    }
    updated.validate()?;
    *policy = updated;
    Ok(
        json!({"id":id,"auto_reply_enabled":policy.enabled,"daily_reply_quota":policy.daily_quota,"min_interval_seconds":policy.min_interval_seconds,"max_interval_seconds":policy.max_interval_seconds}),
    )
}

fn edit_card(doc: &mut Document, id: Option<&str>, input: Value) -> Result<Value> {
    allowed(
        &input,
        &[
            "title",
            "description",
            "cover_file_id",
            "target_url",
            "remark",
            "status",
        ],
    )?;
    let mut card = if let Some(id) = id {
        doc.cards
            .iter()
            .find(|card| card["id"] == id)
            .context("卡片不存在")?
            .clone()
    } else {
        json!({
            "id":uuid::Uuid::new_v4().to_string(),
            "title":"",
            "description":"",
            "cover_file_id":null,
            "cover_url":null,
            "target_url":"",
            "remark":null,
            "status":true,
            "landing_url":null,
        })
    };
    merge(&mut card, input);
    card["sync_state"] = json!("pending");
    validate_card(&card)?;
    if let Some(index) = doc.cards.iter().position(|row| row["id"] == card["id"]) {
        doc.cards[index] = card.clone();
    } else {
        doc.cards.push(card.clone());
    }
    Ok(card)
}

fn card_sync(
    doc: &mut Document,
    id: &str,
    state: &str,
    landing_url: Option<String>,
    cover_url: Option<String>,
) -> Result<Value> {
    anyhow::ensure!(
        matches!(state, "synced" | "failed" | "delete_failed"),
        "卡片同步状态无效"
    );
    let card = doc
        .cards
        .iter_mut()
        .find(|card| card["id"] == id)
        .context("卡片不存在")?;
    card["sync_state"] = json!(state);
    if let Some(url) = landing_url {
        card["landing_url"] = json!(url);
    }
    if let Some(url) = cover_url {
        card["cover_url"] = json!(url);
    }
    validate_card(card)?;
    Ok(card.clone())
}

fn begin_delete_card(doc: &mut Document, id: &str) -> Result<Value> {
    anyhow::ensure!(
        !doc.rules.iter().any(|rule| {
            rule["card_ids"]
                .as_array()
                .is_some_and(|ids| ids.contains(&json!(id)))
        }),
        "卡片正被规则使用"
    );
    let card = doc
        .cards
        .iter_mut()
        .find(|card| card["id"] == id)
        .context("卡片不存在")?;
    card["status"] = json!(false);
    card["sync_state"] = json!("delete_pending");
    Ok(card.clone())
}

fn validate_card(card: &Value) -> Result<()> {
    let title = card["title"].as_str().context("卡片标题格式错误")?;
    let target = card["target_url"]
        .as_str()
        .context("卡片目标链接格式错误")?;
    let uri: wreq::Uri = target.parse().context("卡片目标链接格式错误")?;
    anyhow::ensure!(
        !title.trim().is_empty()
            && title.len() <= 600
            && target.len() <= 4096
            && matches!(uri.scheme_str(), Some("http" | "https"))
            && uri.host().is_some()
            && card["description"]
                .as_str()
                .is_none_or(|value| value.len() <= 4096)
            && card["remark"]
                .as_str()
                .is_none_or(|value| value.len() <= 4096)
            && card["cover_file_id"]
                .as_str()
                .is_none_or(|value| value.len() <= 128)
            && card["status"].as_bool().is_some()
            && card["sync_state"].as_str().is_none_or(|state| matches!(
                state,
                "synced" | "pending" | "failed" | "delete_pending" | "delete_failed"
            )),
        "卡片字段无效"
    );
    for key in ["landing_url", "cover_url"] {
        if let Some(url) = card[key].as_str().filter(|value| !value.is_empty()) {
            let uri: wreq::Uri = url.parse().context("卡片公网链接格式错误")?;
            anyhow::ensure!(
                matches!(uri.scheme_str(), Some("http" | "https")) && uri.host().is_some(),
                "卡片公网链接格式错误"
            );
        }
    }
    Ok(())
}
