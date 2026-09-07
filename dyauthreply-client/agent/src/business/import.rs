//! Native legacy configuration import; source DB is opened read-only and never executed.
use super::{BusinessStore, Document};
use anyhow::{Context, Result};
use rusqlite::{types::ValueRef, Connection, OpenFlags};
use serde_json::{json, Value};
use std::path::Path;

fn rows(
    db: &Connection,
    sql: &str,
    json_fields: &[&str],
    bool_fields: &[&str],
) -> Result<Vec<Value>> {
    let mut query = db.prepare(sql)?;
    let columns: Vec<String> = query.column_names().iter().map(|s| (*s).into()).collect();
    let mut iter = query.query([])?;
    let mut output = Vec::new();
    while let Some(row) = iter.next()? {
        anyhow::ensure!(output.len() < 3000, "导入行数超限");
        let mut record = serde_json::Map::new();
        for (index, name) in columns.iter().enumerate() {
            let value = match row.get_ref(index)? {
                ValueRef::Null => Value::Null,
                ValueRef::Integer(v) => {
                    if bool_fields.contains(&name.as_str()) {
                        json!(v != 0)
                    } else {
                        json!(v)
                    }
                }
                ValueRef::Text(raw) => {
                    anyhow::ensure!(raw.len() <= 65536, "导入字段过长");
                    let text = std::str::from_utf8(raw)?;
                    if json_fields.contains(&name.as_str()) {
                        serde_json::from_str(text)?
                    } else {
                        json!(text)
                    }
                }
                _ => anyhow::bail!("配置字段类型不匹配"),
            };
            record.insert(name.clone(), value);
        }
        output.push(Value::Object(record));
    }
    Ok(output)
}
/// # Errors
/// Keeps source immutable; existing native configuration wins on repeat import.
pub fn import_legacy(source: &Path, root: &Path, card_base: &str) -> Result<Value> {
    let mut db = Connection::open_with_flags(source, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
    let tx = db.transaction()?;
    let accounts=rows(&tx,"SELECT id,group_id,daily_reply_quota,min_interval_seconds,max_interval_seconds,silent_start,silent_end FROM core_douyin_account WHERE is_deleted=0 AND deleted_at IS NULL",&[],&[])?;
    let mut rules=rows(&tx,"SELECT id,name,account_id,account_ids,match_type,keywords,regex_pattern,reply_text,links,send_mode,priority,status,cooldown_seconds,channel,time_window_start,time_window_end,weekday_mask,template_id,card_ids,remark,sys_create_datetime FROM core_douyin_rule WHERE is_deleted=0 ORDER BY priority DESC,sys_create_datetime DESC",&["account_ids","keywords","links","card_ids"],&["status"])?;
    for rule in &mut rules {
        let time = rule["sys_create_datetime"]
            .as_str()
            .context("规则时间缺失")?;
        let at = chrono::NaiveDateTime::parse_from_str(time, "%Y-%m-%d %H:%M:%S%.f")?
            .and_local_timezone(chrono_tz::Asia::Shanghai)
            .single()
            .context("规则时间错误")?
            .timestamp_millis();
        rule.as_object_mut()
            .context("规则格式错误")?
            .remove("sys_create_datetime");
        rule["created_at_ms"] = json!(at);
        if rule["account_ids"].as_array().is_none_or(Vec::is_empty) {
            if let Some(id) = rule["account_id"].as_str() {
                rule["account_ids"] = json!([id]);
            }
        }
        rule.as_object_mut()
            .context("规则格式错误")?
            .remove("account_id");
    }
    let templates=rows(&tx,"SELECT id,name,content,links,send_mode,status,remark FROM core_douyin_template WHERE is_deleted=0",&["links"],&["status"])?;
    let mut cards=rows(&tx,"SELECT id,title,description,cover_file_id,target_url,remark,status FROM core_douyin_card WHERE is_deleted=0",&[],&["status"])?;
    for card in &mut cards {
        if !card_base.is_empty() {
            let base: axum::http::Uri = card_base.parse()?;
            anyhow::ensure!(
                matches!(base.scheme_str(), Some("http" | "https"))
                    && base.authority().is_some_and(|a| !a.as_str().contains('@'))
                    && base.query().is_none(),
                "卡片公网基址无效"
            );
            card["landing_url"] = json!(format!(
                "{}/c/{}",
                card_base.trim_end_matches('/'),
                card["id"].as_str().context("卡片ID缺失")?
            ));
            if let Some(id) = card["cover_file_id"].as_str() {
                card["cover_url"] = json!(format!(
                    "{}/api/core/file_manager/proxy/{id}",
                    card_base.trim_end_matches('/')
                ));
            }
        }
    }
    let blacklist=rows(&tx,"SELECT blacklist_type,value,scope,account_id,group_id FROM core_douyin_blacklist WHERE is_deleted=0 AND status=1",&[],&[])?;
    let mut policies = Vec::new();
    for account in accounts {
        let id = account["id"].as_str().context("账号ID缺失")?;
        uuid::Uuid::parse_str(id)?;
        let mut policy: crate::runtime::messaging::AutomationPolicy = serde_json::from_value(
            json!({"account_id":id,"enabled":false,"daily_quota":account["daily_reply_quota"],"min_interval_seconds":account["min_interval_seconds"],"max_interval_seconds":account["max_interval_seconds"],"silent_start":account["silent_start"],"silent_end":account["silent_end"]}),
        )?;
        for entry in &blacklist {
            let applies = entry["scope"] == "global"
                || (entry["scope"] == "account" && entry["account_id"] == id)
                || (entry["scope"] == "group"
                    && !account["group_id"].is_null()
                    && account["group_id"] == entry["group_id"]);
            if applies {
                let value = entry["value"]
                    .as_str()
                    .context("黑名单字段错误")?
                    .to_owned();
                match entry["blacklist_type"].as_str() {
                    Some("user") => policy.blocked_peers.push(value),
                    Some("content_keyword") => policy.blocked_content_keywords.push(value),
                    Some("nickname_keyword") => policy.blocked_nickname_keywords.push(value),
                    _ => anyhow::bail!("黑名单类型不受支持"),
                }
            }
        }
        policies.push(policy);
    }
    let doc = Document {
        version: 1,
        revision: 1,
        timezone: "Asia/Shanghai".into(),
        rules,
        templates,
        cards,
        policies,
    };
    // Explicit activation through the native UI starts a fresh receive boundary; importing never sends.
    let store = BusinessStore::open(root, doc)?;
    report(&store)
}
fn report(store: &BusinessStore) -> Result<Value> {
    let snapshot = store.snapshot()?;
    Ok(
        json!({"revision":snapshot.document.revision,"rules":snapshot.document.rules.len(),"templates":snapshot.document.templates.len(),"cards":snapshot.document.cards.len(),"accounts":snapshot.policies.len(),"enabled_accounts":snapshot.policies.values().filter(|p|p.enabled).count()}),
    )
}
