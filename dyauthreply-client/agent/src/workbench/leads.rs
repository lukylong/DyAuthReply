//! Durable customer leads in a separate SQLite file; retryable jobs are atomically
//! enqueued alongside chat projection. Commit leads BEFORE acknowledging jobs:
//! a crash between the two database commits replays safely via `lead_seen`.
use super::{iso, now_ms, valid_peer, Workbench};
use anyhow::{ensure, Result};
use rusqlite::{params, Connection};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
#[cfg(test)]
use std::collections::BTreeSet;
use std::{collections::BTreeMap, sync::OnceLock};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS lead_config(id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL,since_ms INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS lead_jobs(id INTEGER PRIMARY KEY,account TEXT NOT NULL,conversation TEXT NOT NULL,platform TEXT NOT NULL,server TEXT NOT NULL,peer TEXT NOT NULL,content TEXT NOT NULL,at_ms INTEGER NOT NULL,UNIQUE(account,platform,server));
CREATE TABLE IF NOT EXISTS lead_profile_jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,account TEXT NOT NULL,peer TEXT NOT NULL,nickname TEXT NOT NULL,unique_id TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS lead_enqueue AFTER INSERT ON messages
WHEN new.direction='in' AND new.kind='text' AND new.source='native'
 AND new.at_ms >= (SELECT since_ms FROM lead_config WHERE id=1 AND enabled=1)
BEGIN
 INSERT OR IGNORE INTO lead_jobs(account,conversation,platform,server,peer,content,at_ms)
 SELECT new.account_id,new.conversation_id,c.platform_id,new.server_id,c.peer,new.content,new.at_ms
 FROM conversations c JOIN accounts a ON a.id=c.account_id
 WHERE c.id=new.conversation_id AND c.account_id=new.account_id AND c.peer<>'' AND c.peer<>a.sec_uid;
END;
CREATE TRIGGER IF NOT EXISTS lead_profile_update AFTER UPDATE OF data ON conversations
WHEN coalesce(json_extract(old.data,'$.peer_nickname'),'')<>coalesce(json_extract(new.data,'$.peer_nickname'),'')
 OR coalesce(json_extract(old.data,'$.peer_unique_id'),'')<>coalesce(json_extract(new.data,'$.peer_unique_id'),'')
BEGIN
 INSERT INTO lead_profile_jobs(account,peer,nickname,unique_id) VALUES(new.account_id,new.peer,coalesce(json_extract(new.data,'$.peer_nickname'),''),coalesce(json_extract(new.data,'$.peer_unique_id'),''));
END;
CREATE TRIGGER IF NOT EXISTS lead_account_update AFTER UPDATE OF data ON accounts
WHEN coalesce(json_extract(old.data,'$.nickname'),'')<>coalesce(json_extract(new.data,'$.nickname'),'')
BEGIN
 INSERT INTO lead_profile_jobs(account,peer,nickname,unique_id) VALUES(new.id,'',coalesce(json_extract(new.data,'$.nickname'),''),'');
END;";
const STORE_SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS lead_seen(digest TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS lead_sources(id TEXT PRIMARY KEY,lead_id TEXT NOT NULL,account TEXT NOT NULL,account_name TEXT NOT NULL,conversation TEXT NOT NULL,platform TEXT NOT NULL,server TEXT NOT NULL,peer TEXT NOT NULL,nickname TEXT NOT NULL,unique_id TEXT NOT NULL,phone TEXT NOT NULL,content TEXT NOT NULL,at_ms INTEGER NOT NULL,extracted_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS lead_group ON lead_sources(lead_id,at_ms DESC,id);
CREATE INDEX IF NOT EXISTS lead_account_time ON lead_sources(account,at_ms DESC);
";

pub(super) fn initialize(db: &Connection) -> Result<()> {
    db.execute_batch(SCHEMA)?;
    db.execute(
        "INSERT OR IGNORE INTO lead_config VALUES(1,1,?1)",
        [now_ms()],
    )?;
    Ok(())
}

/// Conservative mainland mobile candidates: contiguous or 3-4-4 groups only.
/// Normalize characters, never concatenate arbitrary fragments or messages.
fn phone_remarks(text: &str) -> BTreeMap<String, String> {
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    let pattern = RE.get_or_init(|| {
        regex::Regex::new(
            r"(?:\+?86[ \t-]?)?(?:1[3-9][0-9][ \t-][0-9]{4}[ \t-][0-9]{4}|1[3-9][0-9]{9})",
        )
        .expect("static phone pattern")
    });
    let mut normalized = String::with_capacity(text.len());
    // Match normalized digits, but take remark text from the unchanged source.
    let mut original_offsets = Vec::with_capacity(text.len() + 1);
    for (offset, c) in text.char_indices() {
        let mapped = match c {
            '０'..='９' => {
                char::from_u32(u32::from(c) - u32::from('０') + u32::from('0')).unwrap_or(c)
            }
            '＋' => '+',
            '－' => '-',
            '\u{a0}' | '\u{3000}' => ' ',
            _ => c,
        };
        normalized.push(mapped);
        original_offsets.extend(std::iter::repeat_n(offset, mapped.len_utf8()));
    }
    original_offsets.push(text.len());
    let mut result = BTreeMap::new();
    let mut previous_end = 0;
    for matched in pattern.find_iter(&normalized) {
        let before = normalized[..matched.start()].chars().next_back();
        let after = normalized[matched.end()..].chars().next();
        if before.is_some_and(|c| c.is_ascii_digit() || c == '+')
            || after.is_some_and(|c| c.is_ascii_digit())
        {
            continue;
        }
        let digits: String = matched
            .as_str()
            .chars()
            .filter(char::is_ascii_digit)
            .collect();
        let phone = digits[digits.len() - 11..].to_owned();
        let prefix = &text[previous_end..original_offsets[matched.start()]];
        previous_end = original_offsets[matched.end()];
        let remark = prefix.trim_matches(|c: char| {
            c.is_whitespace() || ",，.。:：;；、-—_()（）[]【】<>《》\"'“”‘’!！?？".contains(c)
        });
        let remark = if remark.chars().any(char::is_alphabetic) {
            remark
        } else {
            ""
        };
        let entry = result.entry(phone).or_insert_with(String::new);
        if !remark.is_empty() {
            remark.clone_into(entry);
        }
    }
    result
}
#[cfg(test)]
fn phones(text: &str) -> BTreeSet<String> {
    phone_remarks(text).into_keys().collect()
}

// Additive side table keeps the original 14-column source INSERT compatible
// with the previous app. A source deletion also removes its remark on downgrade.
fn ensure_remarks(db: &mut Connection) -> Result<()> {
    let tx = db.transaction()?;
    tx.execute_batch("CREATE TABLE IF NOT EXISTS lead_source_remarks(source_id TEXT PRIMARY KEY,remark TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS lead_remark_delete AFTER DELETE ON lead_sources BEGIN DELETE FROM lead_source_remarks WHERE source_id=old.id; END;")?;
    let mut query = tx.prepare("SELECT s.id,s.phone,s.content FROM lead_sources s LEFT JOIN lead_source_remarks r ON r.source_id=s.id WHERE r.source_id IS NULL")?;
    let rows = query.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
        ))
    })?;
    for row in rows {
        let (id, phone, content) = row?;
        let remark = phone_remarks(&content).remove(&phone).unwrap_or_default();
        tx.execute(
            "INSERT OR IGNORE INTO lead_source_remarks VALUES(?1,?2)",
            params![id, remark],
        )?;
    }
    drop(query);
    tx.commit()?;
    Ok(())
}

fn key(parts: &[&str]) -> String {
    let mut hash = Sha256::new();
    for part in parts {
        hash.update((part.len() as u64).to_le_bytes());
        hash.update(part.as_bytes());
    }
    format!("{:x}", hash.finalize())
}

#[derive(Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Filter {
    pub account: String,
    pub keyword: String,
    pub from_ms: i64,
    pub to_ms: i64,
    pub page: u32,
}
impl Filter {
    fn validate(&self) -> Result<()> {
        ensure!(
            self.account.len() <= 256
                && self.keyword.len() <= 256
                && self.page <= 100_000
                && self.from_ms >= 0
                && self.to_ms >= 0
                && (self.to_ms == 0 || self.to_ms >= self.from_ms),
            "invalid lead filters"
        );
        Ok(())
    }
}
const GROUPED: &str = "SELECT lead_id AS id,account,max(account_name) AS account_name,peer,
 (SELECT s.nickname FROM lead_sources s WHERE s.lead_id=lead_sources.lead_id ORDER BY s.at_ms DESC,s.id LIMIT 1) AS nickname,
 (SELECT s.unique_id FROM lead_sources s WHERE s.lead_id=lead_sources.lead_id ORDER BY s.at_ms DESC,s.id LIMIT 1) AS unique_id,
 phone,min(at_ms) AS first_ms,max(at_ms) AS last_ms,count(*) AS count,
 (SELECT s.conversation FROM lead_sources s WHERE s.lead_id=lead_sources.lead_id ORDER BY s.at_ms DESC,s.id LIMIT 1) AS conversation,
 coalesce((SELECT r.remark FROM lead_sources s JOIN lead_source_remarks r ON r.source_id=s.id WHERE s.lead_id=lead_sources.lead_id AND r.remark<>'' ORDER BY s.at_ms DESC,s.id LIMIT 1),'') AS remark
 FROM lead_sources GROUP BY lead_id";
const FILTERED: &str = "WHERE (?1='' OR account=?1) AND (?2='' OR instr(phone,?2)>0 OR instr(nickname,?2)>0 OR instr(unique_id,?2)>0 OR instr(remark,?2)>0)
 AND last_ms>=?3 AND (?4=0 OR last_ms<?4)";

impl Workbench {
    fn open_lead_store(&self) -> Result<Connection> {
        let mut db = Connection::open(&self.lead_path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&self.lead_path, std::fs::Permissions::from_mode(0o600))?;
        }
        db.busy_timeout(std::time::Duration::from_secs(2))?;
        let version: i64 = db.pragma_query_value(None, "user_version", |r| r.get(0))?;
        ensure!(version <= 1, "unsupported customer lead schema");
        db.pragma_update(None, "journal_mode", "WAL")?;
        db.pragma_update(None, "secure_delete", true)?;
        db.execute_batch(STORE_SCHEMA)?;
        db.pragma_update(None, "user_version", 1)?;
        ensure_remarks(&mut db)?;
        Ok(db)
    }

    /// # Errors
    /// Reports invalid parameters or local storage failures; callers may retry.
    pub fn lead_status(&self) -> Result<Value> {
        let db = self.lock()?;
        let (enabled, since): (bool, i64) = db.query_row(
            "SELECT enabled,since_ms FROM lead_config WHERE id=1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        let pending: i64 = db.query_row("SELECT count(*) FROM lead_jobs", [], |r| r.get(0))?;
        let mut q = db.prepare(
            "SELECT id,coalesce(json_extract(data,'$.nickname'),'') FROM accounts ORDER BY id",
        )?;
        let mut accounts = q
            .query_map([], |r| {
                Ok(json!({"id":r.get::<_,String>(0)?,"nickname":r.get::<_,String>(1)?}))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        drop(q);
        drop(db);
        // Retained leads remain filterable after a chat-cache rebuild/account removal.
        if let Ok(store) = self.open_lead_store() {
            let mut q = store
                .prepare("SELECT account,max(account_name) FROM lead_sources GROUP BY account")?;
            let retained = q
                .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
                .collect::<rusqlite::Result<Vec<_>>>()?;
            for (id, name) in retained {
                if !accounts.iter().any(|a| a["id"] == id) {
                    accounts.push(json!({"id":id,"nickname":name}));
                }
            }
        }
        Ok(json!({"enabled":enabled,"since_ms":since,"pending":pending,"accounts":accounts}))
    }
    /// # Errors
    /// Reports invalid parameters or local storage failures; callers may retry.
    pub fn set_lead_enabled(&self, enabled: bool) -> Result<Value> {
        self.lock()?.execute("UPDATE lead_config SET since_ms=CASE WHEN enabled=0 AND ?1=1 THEN ?2 ELSE since_ms END,enabled=?1 WHERE id=1", params![enabled,now_ms()])?;
        self.lead_status()
    }
    /// Bounded retry worker. Sources and idempotency commit atomically, before
    /// acknowledging jobs in the chat database. A crash in between replays safely.
    /// The lead store's capacity or extraction errors never propagate to replies.
    /// # Errors
    /// Reports invalid parameters or local storage failures; callers may retry.
    pub fn process_lead_jobs(&self) -> Result<usize> {
        let db = self.lock()?;
        let mut q = db.prepare("SELECT j.id,j.account,j.conversation,j.platform,j.server,j.peer,j.content,j.at_ms,
            coalesce(json_extract(c.data,'$.peer_nickname'),''),coalesce(json_extract(c.data,'$.peer_unique_id'),''),coalesce(json_extract(a.data,'$.nickname'),'')
            FROM lead_jobs j LEFT JOIN conversations c ON c.id=j.conversation AND c.account_id=j.account AND c.peer=j.peer
            LEFT JOIN accounts a ON a.id=j.account ORDER BY j.id LIMIT 200")?;
        let jobs = q
            .query_map([], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                    r.get::<_, String>(5)?,
                    r.get::<_, String>(6)?,
                    r.get::<_, i64>(7)?,
                    r.get::<_, String>(8)?,
                    r.get::<_, String>(9)?,
                    r.get::<_, String>(10)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        drop(q);
        let mut q = db.prepare("SELECT id,account,peer,nickname,unique_id FROM lead_profile_jobs ORDER BY id LIMIT 200")?;
        let profiles = q
            .query_map([], |r| {
                Ok((
                    r.get::<_, i64>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                ))
            })?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        drop(q);
        drop(db);
        if jobs.is_empty() && profiles.is_empty() {
            return Ok(0);
        }
        let ids: Vec<_> = jobs.iter().map(|job| job.0).collect();
        let mut store = self.open_lead_store()?;
        let tx = store.transaction()?;
        let mut added = 0;
        for (
            _id,
            account,
            conversation,
            platform,
            server,
            peer,
            content,
            at,
            nickname,
            unique,
            account_name,
        ) in jobs
        {
            if valid_peer(&peer) && !server.is_empty() && content.len() <= 16 * 1024 {
                let nickname = if nickname == peer || nickname == "未知联系人" {
                    ""
                } else {
                    &nickname
                };
                for (phone, remark) in phone_remarks(&content) {
                    let digest = key(&[&account, &platform, &server, &phone]);
                    if tx.execute("INSERT OR IGNORE INTO lead_seen VALUES(?1)", [&digest])? == 1 {
                        tx.execute("INSERT INTO lead_sources VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14)",params![digest,key(&[&account,&peer,&phone]),account,account_name,conversation,platform,server,peer,nickname,unique,phone,content,at,now_ms()])?;
                        tx.execute(
                            "INSERT INTO lead_source_remarks VALUES(?1,?2)",
                            params![digest, remark],
                        )?;
                        added += 1;
                    }
                }
            }
        }
        for (_, account, peer, nickname, unique) in &profiles {
            if peer.is_empty() {
                tx.execute(
                    "UPDATE lead_sources SET account_name=?1 WHERE account=?2",
                    params![nickname, account],
                )?;
            } else {
                tx.execute("UPDATE lead_sources SET nickname=CASE WHEN ?1 NOT IN ('',peer,'未知联系人') THEN ?1 ELSE nickname END,unique_id=CASE WHEN ?2<>'' THEN ?2 ELSE unique_id END WHERE account=?3 AND peer=?4",params![nickname,unique,account,peer])?;
            }
        }
        tx.commit()?;
        drop(store);
        let mut db = self.lock()?;
        let tx = db.transaction()?;
        for id in ids {
            tx.execute("DELETE FROM lead_jobs WHERE id=?1", [id])?;
        }
        for (id, _, _, _, _) in profiles {
            tx.execute("DELETE FROM lead_profile_jobs WHERE id=?1", [id])?;
        }
        tx.commit()?;
        Ok(added)
    }
    /// Explicit local-only historical import. Does not fetch remote history or reply.
    /// # Errors
    /// Reports invalid parameters or local storage failures; callers may retry.
    pub fn scan_lead_history(&self, account: &str) -> Result<Value> {
        ensure!(account.len() <= 256, "invalid account");
        let queued = self.lock()?.execute("INSERT OR IGNORE INTO lead_jobs(account,conversation,platform,server,peer,content,at_ms)
            SELECT m.account_id,m.conversation_id,c.platform_id,m.server_id,c.peer,m.content,m.at_ms
            FROM messages m JOIN conversations c ON c.id=m.conversation_id JOIN accounts a ON a.id=m.account_id
            WHERE m.direction='in' AND m.kind='text' AND c.peer<>'' AND c.peer<>a.sec_uid AND (?1='' OR m.account_id=?1)", [account])?;
        Ok(json!({"queued":queued}))
    }
    /// # Errors
    /// Reports invalid parameters or local storage failures; callers may retry.
    pub fn list_leads(&self, filter: &Filter, export: bool) -> Result<Value> {
        filter.validate()?;
        let mut connection = self.open_lead_store()?;
        let db = connection.transaction()?;
        let sql = format!("WITH grouped AS ({GROUPED}) SELECT count(*) FROM grouped {FILTERED}");
        let total: i64 = db.query_row(
            &sql,
            params![filter.account, filter.keyword, filter.from_ms, filter.to_ms],
            |r| r.get(0),
        )?;
        ensure!(!export || total <= 10_000, "export exceeds 10000 rows");
        let size = if export { 10_000 } else { 50 };
        let offset = if export { 0 } else { filter.page.max(1) - 1 } * 50;
        let sql = format!("WITH grouped AS ({GROUPED}) SELECT * FROM grouped {FILTERED} ORDER BY last_ms DESC,id LIMIT ?5 OFFSET ?6");
        let mut q = db.prepare(&sql)?;
        let items = q.query_map(params![filter.account,filter.keyword,filter.from_ms,filter.to_ms,size,offset], |r| Ok(json!({
            "id":r.get::<_,String>(0)?,"account_id":r.get::<_,String>(1)?,"account_name":r.get::<_,String>(2)?,
            "peer_id":r.get::<_,String>(3)?,"nickname":r.get::<_,String>(4)?,"douyin_id":r.get::<_,String>(5)?,
            "phone":r.get::<_,String>(6)?,"first_at":iso(r.get(7)?),"last_at":iso(r.get(8)?),"count":r.get::<_,i64>(9)?,"conversation_id":r.get::<_,String>(10)?,"remark":r.get::<_,String>(11)?
        })))?.collect::<rusqlite::Result<Vec<_>>>()?;
        if export {
            let mut csv = String::from("\u{feff}手机号,备注,客户昵称,客户抖音号,接收账号,首次留资时间,最近留资时间,留资次数\r\n");
            for item in items {
                let cells = [
                    "phone",
                    "remark",
                    "nickname",
                    "douyin_id",
                    "account_name",
                    "first_at",
                    "last_at",
                    "count",
                ]
                .map(|k| {
                    let v = item[k]
                        .as_str()
                        .map_or_else(|| item[k].to_string(), str::to_owned);
                    csv_cell(&v)
                });
                csv.push_str(&cells.join(","));
                csv.push_str("\r\n");
            }
            Ok(json!({"csv":csv,"total":total}))
        } else {
            Ok(json!({"items":items,"total":total,"page":filter.page.max(1),"page_size":50}))
        }
    }
    /// # Errors
    /// Reports invalid parameters or local storage failures; callers may retry.
    pub fn lead_sources(&self, id: &str, page: u32) -> Result<Value> {
        ensure!(
            id.len() == 64 && page <= 100_000,
            "invalid lead source query"
        );
        let db = self.open_lead_store()?;
        let total: i64 = db.query_row(
            "SELECT count(*) FROM lead_sources WHERE lead_id=?1",
            [id],
            |r| r.get(0),
        )?;
        let mut q = db.prepare("SELECT s.content,s.at_ms,s.extracted_ms,s.server,coalesce(r.remark,'') FROM lead_sources s LEFT JOIN lead_source_remarks r ON r.source_id=s.id WHERE s.lead_id=?1 ORDER BY s.at_ms DESC,s.id LIMIT 50 OFFSET ?2")?;
        let items = q.query_map(params![id,(page.max(1)-1)*50], |r| Ok(json!({"content":r.get::<_,String>(0)?,"message_at":iso(r.get(1)?),"extracted_at":iso(r.get(2)?),"message_id":r.get::<_,String>(3)?,"remark":r.get::<_,String>(4)?})))?.collect::<rusqlite::Result<Vec<_>>>()?;
        Ok(json!({"items":items,"total":total}))
    }
    /// # Errors
    /// Reports invalid parameters or local storage failures; callers may retry.
    pub fn delete_lead(&self, id: &str) -> Result<Value> {
        ensure!(id.len() == 64, "invalid lead identity");
        // Keep only irreversible message/phone identity digests, not deleted text.
        let deleted = self
            .open_lead_store()?
            .execute("DELETE FROM lead_sources WHERE lead_id=?1", [id])?;
        Ok(json!({"deleted":deleted}))
    }
    /// Exact account-scoped local route, including conversations outside page one.
    /// # Errors
    /// Reports invalid parameters or local storage failures; callers may retry.
    pub fn lead_conversation(&self, account: &str, conversation: &str) -> Result<Value> {
        let db = self.lock()?;
        let (raw, peer, at): (String, String, i64) = db.query_row(
            "SELECT data,peer,updated FROM conversations WHERE account_id=?1 AND id=?2",
            params![account, conversation],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        let mut data: Value = serde_json::from_str(&raw)?;
        data["id"] = json!(conversation);
        data["peer_sec_uid"] = json!(peer);
        data["last_message_at"] = json!(iso(at));
        Ok(data)
    }
}
fn csv_cell(value: &str) -> String {
    let prefix = if value.trim_start().starts_with(['=', '+', '-', '@'])
        || value.starts_with(['\t', '\r', '\n'])
    {
        "'"
    } else {
        ""
    };
    format!("\"{prefix}{}\"", value.replace('"', "\"\""))
}
#[cfg(test)]
mod tests;
