//! Disposable, bounded chat projection. Never used for send decisions or leases.
use crate::runtime::inbound::InboundEvent;
use anyhow::{Context, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
};

pub mod api;
mod import;
#[cfg(test)]
mod tests;

pub struct Workbench {
    db: Mutex<Connection>,
    revision: AtomicU64,
    pub(crate) changed: tokio::sync::broadcast::Sender<Value>,
    pub(crate) sockets: std::sync::Arc<tokio::sync::Semaphore>,
}
const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, sec_uid TEXT NOT NULL UNIQUE, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,account_id TEXT NOT NULL REFERENCES accounts(id),platform_id TEXT NOT NULL,short_id TEXT NOT NULL,peer TEXT NOT NULL,data TEXT NOT NULL,updated INTEGER NOT NULL DEFAULT 0,preview TEXT NOT NULL DEFAULT '',UNIQUE(account_id,platform_id));
CREATE INDEX IF NOT EXISTS conversation_recent ON conversations(account_id,updated DESC,id);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,conversation_id TEXT NOT NULL REFERENCES conversations(id),server_id TEXT NOT NULL,client_id TEXT NOT NULL DEFAULT '',direction TEXT NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,at_ms INTEGER NOT NULL,source TEXT NOT NULL DEFAULT 'native',UNIQUE(account_id,server_id));
CREATE INDEX IF NOT EXISTS message_recent ON messages(conversation_id,at_ms,id);
CREATE INDEX IF NOT EXISTS message_retention ON messages(at_ms,id);
CREATE INDEX IF NOT EXISTS message_account_retention ON messages(account_id,at_ms,id);
INSERT OR IGNORE INTO meta(key,value) SELECT 'message_bytes',CAST(coalesce(sum(length(CAST(content AS BLOB))),0) AS TEXT) FROM messages;
CREATE TRIGGER IF NOT EXISTS message_bytes_insert AFTER INSERT ON messages BEGIN UPDATE meta SET value=CAST(CAST(value AS INTEGER)+length(CAST(new.content AS BLOB)) AS TEXT) WHERE key='message_bytes'; END;
CREATE TRIGGER IF NOT EXISTS message_bytes_delete AFTER DELETE ON messages BEGIN UPDATE meta SET value=CAST(CAST(value AS INTEGER)-length(CAST(old.content AS BLOB)) AS TEXT) WHERE key='message_bytes'; END;
CREATE TRIGGER IF NOT EXISTS message_bytes_update AFTER UPDATE OF content ON messages BEGIN UPDATE meta SET value=CAST(CAST(value AS INTEGER)+length(CAST(new.content AS BLOB))-length(CAST(old.content AS BLOB)) AS TEXT) WHERE key='message_bytes'; END;
";
const RETENTION_MS: i64 = 30 * 86_400_000;
const MAX_MESSAGES: i64 = 100_000;
const PER_ACCOUNT_MESSAGES: i64 = 10_000;
const MAX_CONTENT_BYTES: i64 = 32 * 1024 * 1024;

pub(crate) fn now_ms() -> i64 {
    i64::try_from(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis(),
    )
    .unwrap_or(i64::MAX)
}
fn iso(ms: i64) -> Option<String> {
    chrono::DateTime::from_timestamp_millis(ms).map(|d| d.to_rfc3339())
}
impl Workbench {
    /// # Errors
    /// Rejects inaccessible, foreign-version or corrupt projection databases.
    pub fn open(root: &Path) -> Result<Self> {
        std::fs::create_dir_all(root)?;
        let path = root.join("workbench.sqlite3");
        let db = Connection::open(&path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        }
        db.busy_timeout(std::time::Duration::from_secs(2))?;
        let version: i64 = db.pragma_query_value(None, "user_version", |r| r.get(0))?;
        anyhow::ensure!(version <= 1, "unsupported workbench schema");
        db.pragma_update(None, "auto_vacuum", "INCREMENTAL")?;
        db.pragma_update(None, "foreign_keys", true)?;
        db.pragma_update(None, "journal_mode", "WAL")?;
        db.pragma_update(None, "journal_size_limit", 4 * 1024 * 1024)?;
        db.pragma_update(None, "max_page_count", 32768)?;
        db.execute_batch(SCHEMA)?;
        db.pragma_update(None, "user_version", 1)?;
        Ok(Self {
            db: Mutex::new(db),
            revision: AtomicU64::new(0),
            changed: tokio::sync::broadcast::channel(128).0,
            sockets: std::sync::Arc::new(tokio::sync::Semaphore::new(8)),
        })
    }
    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Connection>> {
        self.db
            .lock()
            .map_err(|_| anyhow::anyhow!("workbench lock poisoned"))
    }
    /// # Errors
    /// Rejects identity changes under an existing account ID.
    pub fn ensure_account(&self, id: &str, sec_uid: &str) -> Result<()> {
        let db = self.lock()?;
        db.execute(
            "INSERT INTO accounts(id,sec_uid,data) VALUES(?1,?2,?3) ON CONFLICT(id) DO NOTHING",
            params![
                id,
                sec_uid,
                json!({"id":id,"sec_uid":sec_uid,"nickname":"抖音账号"}).to_string()
            ],
        )?;
        let actual: String =
            db.query_row("SELECT sec_uid FROM accounts WHERE id=?1", [id], |r| {
                r.get(0)
            })?;
        anyhow::ensure!(actual == sec_uid, "workbench account identity mismatch");
        Ok(())
    }
    /// # Errors
    /// Updates only verified public identity fields; preserves unrelated account metadata.
    pub fn set_account_profile(&self, id: &str, scope: &str, nickname: &str) -> Result<()> {
        self.ensure_account(id, scope)?;
        self.lock()?.execute(
            "UPDATE accounts SET data=json_set(data,'$.nickname',?1) WHERE id=?2",
            params![nickname, id],
        )?;
        Ok(())
    }
    /// Stores one verified public profile snapshot without changing credentials or runtime state.
    /// # Errors
    /// Rejects identity mismatches, invalid fields, counters or storage failures.
    pub fn set_account_profile_details(
        &self,
        id: &str,
        profile: &crate::protocol::VerifiedSelf,
        at_ms: i64,
    ) -> Result<()> {
        anyhow::ensure!(
            at_ms > 0
                && profile.nickname.len() <= 512
                && profile.avatar.len() <= 4096
                && profile.unique_id.len() <= 256
                && !profile.nickname.chars().any(char::is_control)
                && !profile.avatar.chars().any(char::is_control)
                && !profile.unique_id.chars().any(char::is_control),
            "invalid account profile"
        );
        self.ensure_account(id, &profile.sec_uid)?;
        let synced = iso(at_ms).context("invalid profile timestamp")?;
        let changed = self.lock()?.execute(
            "UPDATE accounts SET data=json_set(data,'$.nickname',?1,'$.avatar',?2,'$.unique_id',?3,'$.follower_count',?4,'$.following_count',?5,'$.aweme_count',?6,'$.total_favorited',?7,'$.last_profile_sync_at',?8,'$.last_profile_sync_at_ms',?9) WHERE id=?10 AND sec_uid=?11",
            params![profile.nickname,profile.avatar,profile.unique_id,profile.follower_count,profile.following_count,profile.aweme_count,profile.total_favorited,synced,at_ms,id,profile.sec_uid],
        )?;
        anyhow::ensure!(changed == 1, "account profile identity changed");
        self.revision.fetch_add(1, Ordering::Relaxed);
        let _ = self
            .changed
            .send(json!({"type":"account_state_changed","data":{"account_id":id}}));
        Ok(())
    }
    /// Returns a previously verified profile projection and its sync timestamp.
    /// # Errors
    /// Reports missing/corrupt storage without inventing a successful sync.
    pub fn profile_snapshot(&self, id: &str) -> Result<Option<(Value, i64)>> {
        let data = self.account(id)?;
        let at = data["last_profile_sync_at_ms"].as_i64().unwrap_or(0);
        if at <= 0 {
            return Ok(None);
        }
        let value = json!({
            "ok":true,
            "error":Value::Null,
            "nickname":data["nickname"],
            "avatar":data["avatar"],
            "unique_id":data["unique_id"],
            "follower_count":data["follower_count"].as_u64().unwrap_or(0),
            "following_count":data["following_count"].as_u64().unwrap_or(0),
            "aweme_count":data["aweme_count"].as_u64().unwrap_or(0),
            "total_favorited":data["total_favorited"].as_u64().unwrap_or(0),
            "last_profile_sync_at":data["last_profile_sync_at"],
            "cached":true,
        });
        Ok(Some((value, at)))
    }
    /// # Errors
    /// Reports missing metadata without disclosing credentials.
    pub fn account(&self, id: &str) -> Result<Value> {
        let raw: String =
            self.lock()?
                .query_row("SELECT data FROM accounts WHERE id=?1", [id], |r| r.get(0))?;
        Ok(serde_json::from_str(&raw)?)
    }
    /// # Errors
    /// Returns cached account names in one bounded metadata query.
    pub fn account_names(&self) -> Result<std::collections::BTreeMap<String, String>> {
        let db = self.lock()?;
        let mut query =
            db.prepare("SELECT id,json_extract(data,'$.nickname') FROM accounts LIMIT 3000")?;
        let rows = query.query_map([], |r| {
            Ok((
                r.get(0)?,
                r.get::<_, Option<String>>(1)?.unwrap_or_default(),
            ))
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }
    /// Cached display name for native template/blacklist evaluation; unknown names stay unknown.
    /// # Errors
    /// Reports projection read failures rather than inventing a peer profile.
    pub fn peer_nickname(&self, account: &str, platform: &str) -> Result<Option<String>> {
        let row:Option<(String,String)>=self.lock()?.query_row("SELECT peer,json_extract(data,'$.peer_nickname') FROM conversations WHERE account_id=?1 AND platform_id=?2",params![account,platform],|r|Ok((r.get(0)?,r.get::<_,Option<String>>(1)?.unwrap_or_default()))).optional()?;
        Ok(row.and_then(|(peer, name)| {
            (!name.is_empty() && name != peer && name != "未知联系人").then_some(name)
        }))
    }
    /// Returns the account-bound peer scope for a local conversation ID.
    /// # Errors
    /// Rejects missing, fallback or cross-account conversations.
    pub fn peer_scope(&self, account: &str, conversation: &str) -> Result<String> {
        let peer: String = self.lock()?.query_row(
            "SELECT peer FROM conversations WHERE account_id=?1 AND id=?2",
            params![account, conversation],
            |row| row.get(0),
        )?;
        anyhow::ensure!(
            !peer.is_empty()
                && peer.len() <= 256
                && !peer.starts_with("fallback_")
                && !peer.chars().any(char::is_control),
            "conversation peer scope unavailable"
        );
        Ok(peer)
    }
    /// Updates only verified public peer fields for one account-bound conversation.
    /// # Errors
    /// Rejects response-scope mismatches, empty names or storage failures.
    pub fn set_peer_profile(
        &self,
        account: &str,
        conversation: &str,
        profile: &crate::protocol::VerifiedSelf,
    ) -> Result<()> {
        let peer = self.peer_scope(account, conversation)?;
        anyhow::ensure!(
            profile.sec_uid == peer
                && !profile.nickname.trim().is_empty()
                && profile.nickname.len() <= 512
                && profile.avatar.len() <= 4096
                && profile.unique_id.len() <= 256,
            "peer profile identity mismatch"
        );
        let changed = self.lock()?.execute(
            "UPDATE conversations SET data=json_set(data,'$.peer_nickname',?1,'$.peer_avatar',?2,'$.peer_unique_id',?3) WHERE account_id=?4 AND id=?5 AND peer=?6",
            params![profile.nickname,profile.avatar,profile.unique_id,account,conversation,peer],
        )?;
        anyhow::ensure!(changed == 1, "conversation profile changed");
        self.revision.fetch_add(1, Ordering::Relaxed);
        let _ = self.changed.send(json!({"type":"new_message","data":{"account_id":account,"conversation_ids":[conversation]}}));
        Ok(())
    }
    /// # Errors
    /// Rejects cross-account or missing conversation routes.
    pub fn route(&self, account: &str, id: &str) -> Result<(String, String)> {
        Ok(self.lock()?.query_row(
            "SELECT platform_id,short_id FROM conversations WHERE account_id=?1 AND id=?2",
            params![account, id],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?)
    }
    /// # Errors
    /// Returns storage errors; pagination/search are bounded and parameterized.
    pub fn conversations(
        &self,
        account: &str,
        page: u32,
        size: u32,
        keyword: &str,
    ) -> Result<Value> {
        anyhow::ensure!(
            (1..=10000).contains(&page) && (1..=100).contains(&size) && keyword.len() <= 256,
            "invalid pagination"
        );
        let db = self.lock()?;
        let query="SELECT id,data,updated,preview,peer FROM conversations WHERE account_id=?1 AND (?2='' OR instr(json_extract(data,'$.peer_nickname'),?2)>0 OR instr(peer,?2)>0) ORDER BY updated DESC,id LIMIT ?3 OFFSET ?4";
        let mut statement = db.prepare(query)?;
        let rows =
            statement.query_map(params![account, keyword, size, (page - 1) * size], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, i64>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                ))
            })?;
        let mut items = Vec::new();
        for row in rows {
            let (id, data, at, preview, peer) = row?;
            let mut v: Value = serde_json::from_str(&data)?;
            v["id"] = json!(id);
            v["peer_sec_uid"] = json!(peer);
            v["last_message_at"] = json!(iso(at));
            v["last_message_preview"] = json!(preview);
            items.push(v);
        }
        let total:i64=db.query_row("SELECT count(*) FROM conversations WHERE account_id=?1 AND (?2='' OR instr(json_extract(data,'$.peer_nickname'),?2)>0 OR instr(peer,?2)>0)",params![account,keyword],|r|r.get(0))?;
        Ok(
            json!({"items":items,"total":total,"page":page,"page_size":size,"has_more":i64::from(page)*i64::from(size)<total}),
        )
    }
    /// # Errors
    /// Enforces account scope, returns at most 200 recent messages.
    pub fn messages(&self, account: &str, conversation: &str) -> Result<Value> {
        self.route(account, conversation)?;
        let db = self.lock()?;
        let mut q=db.prepare("SELECT id,direction,kind,content,at_ms,server_id,client_id FROM (SELECT * FROM messages WHERE account_id=?1 AND conversation_id=?2 ORDER BY at_ms DESC,id DESC LIMIT 200) ORDER BY at_ms,id")?;
        let rows=q.query_map(params![account,conversation],|r|Ok(json!({"id":r.get::<_,String>(0)?,"direction":r.get::<_,String>(1)?,"content_type":r.get::<_,String>(2)?,"content":r.get::<_,String>(3)?,"received_at":iso(r.get(4)?),"processed":true,"server_message_id":r.get::<_,String>(5)?,"client_message_id":r.get::<_,String>(6)?})))?;
        Ok(Value::Array(rows.collect::<rusqlite::Result<Vec<_>>>()?))
    }
    /// Called before durable receipts are consumed. Retry is idempotent by exact server ID;
    /// a projection failure leaves receipt payloads available for restart recovery.
    /// # Errors
    /// Rejects malformed/oversized events and conflicting immutable delivery identities.
    pub fn project(&self, account: &str, own: &str, events: &[InboundEvent]) -> Result<()> {
        anyhow::ensure!(events.len() <= 512, "too many projection events");
        let mut db = self.lock()?;
        let tx = db.transaction()?;
        let mut changed = std::collections::BTreeSet::new();
        let now = now_ms();
        for event in events {
            anyhow::ensure!(
                event.version == 1
                    && event.content_json.len() <= 64 * 1024
                    && event.conversation_id.len() <= 256,
                "invalid projection event"
            );
            // Control/system packets are not visible conversation messages.
            let body: Value = serde_json::from_str(&event.content_json).unwrap_or(Value::Null);
            if body.get("read_index").is_some()
                || body.get("command_type").is_some()
                || event.conversation_id.is_empty()
                || event.create_time_us == 0
            {
                continue;
            }
            let at = i64::try_from(event.create_time_us / 1000)?;
            if at < now - RETENTION_MS || at > now + 300_000 {
                continue;
            }
            let direction = if event.sender_sec_uid == own {
                "out"
            } else {
                "in"
            };
            let content = event
                .text
                .clone()
                .unwrap_or_else(|| format!("[消息类型 {}]", event.message_type));
            anyhow::ensure!(
                content.len() <= 16 * 1024 && !event.server_message_id.is_empty(),
                "invalid message projection"
            );
            let old:Option<(String,String,String,String,String)>=tx.query_row("SELECT c.platform_id,m.direction,m.content,m.source,m.conversation_id FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.account_id=?1 AND m.server_id=?2",params![account,event.server_message_id],|r|Ok((r.get(0)?,r.get(1)?,r.get(2)?,r.get(3)?,r.get(4)?))).optional()?;
            if let Some(old) = old {
                if old.3 == "legacy" {
                    anyhow::ensure!(old.0 == event.conversation_id, "legacy route conflict");
                    tx.execute("UPDATE messages SET direction=?1,content=?2,client_id=?3,at_ms=?4,source='native' WHERE account_id=?5 AND server_id=?6",params![direction,content,event.client_message_id,at,account,event.server_message_id])?;
                    changed.insert(old.4);
                } else {
                    anyhow::ensure!(
                        (old.0, old.1, old.2)
                            == (event.conversation_id.clone(), direction.into(), content),
                        "conflicting server message projection"
                    );
                }
                continue;
            }
            let mut conversation: Option<String> = tx
                .query_row(
                    "SELECT id FROM conversations WHERE account_id=?1 AND platform_id=?2",
                    params![account, event.conversation_id],
                    |r| r.get(0),
                )
                .optional()?;
            if conversation.is_none() {
                reserve_conversation(&tx, account)?;
                let id = uuid::Uuid::new_v4().to_string();
                let peer = if direction == "in" {
                    &event.sender_sec_uid
                } else {
                    ""
                };
                tx.execute("INSERT INTO conversations(id,account_id,platform_id,short_id,peer,data) VALUES(?1,?2,?3,?4,?5,?6)",params![id,account,event.conversation_id,event.conversation_short_id,peer,json!({"peer_nickname":if peer.is_empty(){"未知联系人"}else{peer},"unread_count":0}).to_string()])?;
                conversation = Some(id);
            }
            let id = conversation.context("conversation missing")?;
            tx.execute("INSERT INTO messages(id,account_id,conversation_id,server_id,client_id,direction,kind,content,at_ms) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)",params![uuid::Uuid::new_v4().to_string(),account,id,event.server_message_id,event.client_message_id,direction,if event.text.is_some(){"text"}else{"unknown"},content,at])?;
            tx.execute(
                "UPDATE conversations SET updated=?1,preview=?2 WHERE id=?3 AND updated<=?1",
                params![at, content.chars().take(200).collect::<String>(), id],
            )?;
            changed.insert(id);
        }
        if !changed.is_empty() {
            prune(&tx, account, now)?;
        }
        tx.commit()?;
        drop(db);
        if !changed.is_empty() {
            self.revision.fetch_add(1, Ordering::Relaxed);
            let _=self.changed.send(json!({"type":"new_message","data":{"account_id":account,"conversation_ids":changed}}));
        }
        Ok(())
    }
    /// One process-wide maintenance tick; no per-account timers or unbounded WAL.
    /// # Errors
    /// Reports cleanup/checkpoint errors for retry by the central runtime.
    pub fn maintain(&self) -> Result<()> {
        let db = self.lock()?;
        db.execute(
            "DELETE FROM messages WHERE at_ms<?1",
            [now_ms() - RETENTION_MS],
        )?;
        db.execute(
            "UPDATE conversations SET preview='' WHERE updated<?1 AND preview!=''",
            [now_ms() - RETENTION_MS],
        )?;
        db.execute_batch("PRAGMA incremental_vacuum(256); PRAGMA wal_checkpoint(PASSIVE);")?;
        Ok(())
    }
}
fn prune(tx: &rusqlite::Transaction<'_>, account: &str, now: i64) -> Result<()> {
    tx.execute("DELETE FROM messages WHERE at_ms<?1", [now - RETENTION_MS])?;
    tx.execute("DELETE FROM messages WHERE id IN (SELECT id FROM messages WHERE account_id=?1 ORDER BY at_ms DESC,id DESC LIMIT -1 OFFSET ?2)",params![account,PER_ACCOUNT_MESSAGES])?;
    tx.execute("DELETE FROM messages WHERE id IN (SELECT id FROM messages ORDER BY at_ms DESC,id DESC LIMIT -1 OFFSET ?1)",[MAX_MESSAGES])?;
    prune_bytes(tx, MAX_CONTENT_BYTES)?;
    Ok(())
}

fn prune_bytes(tx: &rusqlite::Transaction<'_>, limit: i64) -> Result<()> {
    loop {
        let bytes: i64 = tx.query_row(
            "SELECT CAST(value AS INTEGER) FROM meta WHERE key='message_bytes'",
            [],
            |r| r.get(0),
        )?;
        if bytes <= limit {
            break;
        }
        let removed = tx.execute(
            "DELETE FROM messages WHERE id IN (SELECT id FROM messages ORDER BY at_ms,id LIMIT 64)",
            [],
        )?;
        anyhow::ensure!(removed > 0, "projection byte counter mismatch");
    }
    Ok(())
}
fn reserve_conversation(tx: &rusqlite::Transaction<'_>, account: &str) -> Result<()> {
    let count: i64 = tx.query_row(
        "SELECT count(*) FROM conversations WHERE account_id=?1",
        [account],
        |r| r.get(0),
    )?;
    let total: i64 = tx.query_row("SELECT count(*) FROM conversations", [], |r| r.get(0))?;
    if count >= 2000 || total >= 10000 {
        let oldest:String=tx.query_row("SELECT id FROM conversations WHERE (?1=0 OR account_id=?2) ORDER BY updated,id LIMIT 1",params![i32::from(count>=2000),account],|r|r.get(0))?;
        tx.execute("DELETE FROM messages WHERE conversation_id=?1", [&oldest])?;
        tx.execute("DELETE FROM conversations WHERE id=?1", [&oldest])?;
    }
    Ok(())
}
