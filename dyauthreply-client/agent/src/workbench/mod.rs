//! Disposable, bounded chat projection. Send claims and leases live in `CoreStore`;
//! cached peer metadata may resolve a notice's recipient, never authorize a send.
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
mod content;
pub(crate) use content::reply_match_text;
mod import;
pub(crate) mod media;
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
/// A peer whose public profile could not be read is retried at most this often.
pub(crate) const PEER_PROFILE_RETRY_MS: i64 = 6 * 3_600_000;
/// Rows written before profile backfill stored the `sec_uid` or this text as the name.
const PLACEHOLDER_NAME: &str = "未知联系人";

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
        add_media_columns(&db)?;
        // Group chats were projected before direct-message filtering existed;
        // their platform IDs are bare digits, unlike `0:1:uid:uid` single chats.
        db.execute_batch(
            "DELETE FROM messages WHERE conversation_id IN
                 (SELECT id FROM conversations WHERE platform_id NOT GLOB '*[^0-9]*');
             DELETE FROM conversations WHERE platform_id NOT GLOB '*[^0-9]*';",
        )?;
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
            "UPDATE accounts SET data=json_set(data,'$.nickname',?1,'$.avatar',?2,'$.unique_id',CASE WHEN ?3<>'' THEN ?3 ELSE coalesce(json_extract(data,'$.unique_id'),'') END,'$.follower_count',?4,'$.following_count',?5,'$.aweme_count',?6,'$.total_favorited',?7,'$.last_profile_sync_at',?8,'$.last_profile_sync_at_ms',?9) WHERE id=?10 AND sec_uid=?11",
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
    /// Resolves an own-side notice through an existing account-bound conversation.
    /// Missing, self, fallback or mismatched routes stay pending, never guessed.
    pub(crate) fn notice_peer(
        &self,
        account: &str,
        own: &str,
        platform: &str,
        short_id: &str,
    ) -> Result<Option<String>> {
        let peer: Option<String> = self
            .lock()?
            .query_row(
                "SELECT c.peer FROM conversations c JOIN accounts a ON a.id=c.account_id
             WHERE c.account_id=?1 AND a.sec_uid=?2 AND c.platform_id=?3 AND c.short_id=?4",
                params![account, own, platform, short_id],
                |r| r.get(0),
            )
            .optional()?;
        Ok(peer.filter(|p| valid_peer(p) && p != own))
    }
    /// Claims up to `limit` conversations whose peer is known but whose public profile is
    /// missing, recording the attempt so each is retried at most every
    /// [`PEER_PROFILE_RETRY_MS`].
    /// # Errors
    /// Reports projection read/write failures.
    pub fn peers_missing_profile(
        &self,
        account: &str,
        limit: u32,
        now: i64,
    ) -> Result<Vec<String>> {
        let mut db = self.lock()?;
        let tx = db.transaction()?;
        let due: Vec<String> = {
            let mut q = tx.prepare(
                "SELECT id FROM conversations WHERE account_id=?1 AND peer<>'' AND peer NOT LIKE 'fallback\\_%' ESCAPE '\\'
                 AND coalesce(json_extract(data,'$.peer_nickname'),'') IN ('',peer,?2)
                 AND coalesce(json_extract(data,'$.peer_profile_attempt_ms'),0) <= ?3
                 ORDER BY updated DESC,id LIMIT ?4",
            )?;
            let rows = q.query_map(
                params![
                    account,
                    PLACEHOLDER_NAME,
                    now - PEER_PROFILE_RETRY_MS,
                    limit.min(20)
                ],
                |r| r.get(0),
            )?;
            rows.collect::<rusqlite::Result<_>>()?
        };
        for id in &due {
            tx.execute(
                "UPDATE conversations SET data=json_set(data,'$.peer_profile_attempt_ms',?1) WHERE id=?2",
                params![now, id],
            )?;
        }
        tx.commit()?;
        Ok(due)
    }
    /// Claims up to `limit` direct chats with no known peer (we spoke first), as
    /// `(id, platform_id, short_id)`; each is retried at most every [`PEER_PROFILE_RETRY_MS`].
    /// # Errors
    /// Reports projection read/write failures.
    pub fn peerless_conversations(
        &self,
        account: &str,
        limit: u32,
        now: i64,
    ) -> Result<Vec<(String, String, u64)>> {
        let mut db = self.lock()?;
        let tx = db.transaction()?;
        let due: Vec<(String, String, String)> = {
            let mut q = tx.prepare(
                "SELECT id,platform_id,short_id FROM conversations WHERE account_id=?1 AND peer=''
                 AND platform_id LIKE '0:1:%'
                 AND coalesce(json_extract(data,'$.peer_resolve_attempt_ms'),0) <= ?2
                 ORDER BY updated DESC,id LIMIT ?3",
            )?;
            let rows = q.query_map(
                params![account, now - PEER_PROFILE_RETRY_MS, limit.min(20)],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )?;
            rows.collect::<rusqlite::Result<_>>()?
        };
        let mut result = Vec::with_capacity(due.len());
        for (id, platform, short) in due {
            tx.execute(
                "UPDATE conversations SET data=json_set(data,'$.peer_resolve_attempt_ms',?1) WHERE id=?2",
                params![now, id],
            )?;
            if let Ok(short) = short.parse::<u64>() {
                result.push((id, platform, short));
            }
        }
        tx.commit()?;
        Ok(result)
    }
    /// Records a verified peer for a conversation that has none; never replaces one.
    /// Returns whether the peer was recorded.
    /// # Errors
    /// Rejects invalid peer identities and projection write failures.
    pub fn set_conversation_peer(
        &self,
        account: &str,
        conversation: &str,
        peer: &str,
    ) -> Result<bool> {
        anyhow::ensure!(valid_peer(peer), "invalid conversation peer");
        let changed = self.lock()?.execute(
            "UPDATE conversations SET peer=?1 WHERE account_id=?2 AND id=?3 AND peer=''",
            params![peer, account, conversation],
        )?;
        Ok(changed == 1)
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
            if v["peer_nickname"]
                .as_str()
                .is_some_and(|name| name == peer || name == PLACEHOLDER_NAME)
            {
                v["peer_nickname"] = json!("");
            }
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
        let mut q=db.prepare("SELECT id,direction,kind,content,at_ms,server_id,client_id,media FROM (SELECT * FROM messages WHERE account_id=?1 AND conversation_id=?2 ORDER BY at_ms DESC,id DESC LIMIT 200) ORDER BY at_ms,id")?;
        let rows=q.query_map(params![account,conversation],|r|{
            let media=r.get::<_,Option<String>>(7)?.and_then(|m|serde_json::from_str::<Value>(&m).ok()).unwrap_or(Value::Null);
            Ok(json!({"id":r.get::<_,String>(0)?,"direction":r.get::<_,String>(1)?,"content_type":r.get::<_,String>(2)?,"content":r.get::<_,String>(3)?,"received_at":iso(r.get(4)?),"processed":true,"server_message_id":r.get::<_,String>(5)?,"client_message_id":r.get::<_,String>(6)?,"media":media}))
        })?;
        Ok(Value::Array(rows.collect::<rusqlite::Result<Vec<_>>>()?))
    }
    /// Encrypted image source for one account-scoped message, if it has one.
    /// # Errors
    /// Rejects cross-account routes and corrupt stored sources.
    pub fn media_source(
        &self,
        account: &str,
        conversation: &str,
        message: &str,
    ) -> Result<Option<media::MediaSource>> {
        self.route(account, conversation)?;
        let raw: Option<Option<String>> = self
            .lock()?
            .query_row(
                "SELECT media_source FROM messages WHERE account_id=?1 AND conversation_id=?2 AND id=?3",
                params![account, conversation, message],
                |r| r.get(0),
            )
            .optional()?;
        raw.flatten()
            .map(|s| serde_json::from_str(&s).context("stored media source invalid"))
            .transpose()
    }
    /// Called before durable receipts are consumed. Retry is idempotent by exact server ID;
    /// a projection failure leaves receipt payloads available for restart recovery.
    /// # Errors
    /// Rejects malformed/oversized events and conflicting immutable delivery identities.
    pub fn project(&self, account: &str, own: &str, events: &[InboundEvent]) -> Result<()> {
        // Keep the display batch compatible with a complete durable inbound page.
        // A separate hard-coded limit can poison every subsequent HTTP/WS drain.
        anyhow::ensure!(
            events.len() <= usize::try_from(crate::runtime::inbound::MAX_PENDING_RECORDS)?,
            "too many projection events"
        );
        let mut db = self.lock()?;
        let tx = db.transaction()?;
        let mut changed = std::collections::BTreeSet::new();
        let now = now_ms();
        for event in events {
            // The durable spool accepts a bounded 1 MiB page. A stricter 64 KiB
            // display-input cap poisoned valid rich receipts and every subsequent
            // HTTP/WS drain. Keep parsing bounded by the upstream spool budget;
            // visible text/media retain their independent, smaller display limits.
            anyhow::ensure!(
                event.version == 1
                    && u64::try_from(event.content_json.len())?
                        <= crate::runtime::inbound::MAX_PENDING_BYTES
                    && event.conversation_id.len() <= 256,
                "invalid projection event"
            );
            // Only direct messages belong here; control packets are not visible messages.
            if !content::is_direct_message(event.message_type)
                || event.conversation_id.is_empty()
                || event.create_time_us == 0
            {
                continue;
            }
            let Some(described) = content::describe(&event.content_json, event.text.as_deref())
            else {
                changed.extend(drop_hidden(&tx, account, &event.server_message_id)?);
                continue;
            };
            let at = i64::try_from(event.create_time_us / 1000)?;
            if at < now - RETENTION_MS || at > now + 300_000 {
                continue;
            }
            let direction = if event.sender_sec_uid == own {
                "out"
            } else {
                "in"
            };
            anyhow::ensure!(
                described.content.len() <= 16 * 1024 && !event.server_message_id.is_empty(),
                "invalid message projection"
            );
            if direction == "in" {
                changed.extend(repair_peer(&tx, account, event)?);
            }
            let row = Projected::new(&described, direction, at)?;
            match reproject(&tx, account, event, &row)? {
                Stored::New => {}
                Stored::Unchanged => continue,
                Stored::Changed(conversation) => {
                    changed.insert(conversation);
                    continue;
                }
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
                tx.execute("INSERT INTO conversations(id,account_id,platform_id,short_id,peer,data) VALUES(?1,?2,?3,?4,?5,?6)",params![id,account,event.conversation_id,event.conversation_short_id,peer,json!({"peer_nickname":"","unread_count":0}).to_string()])?;
                conversation = Some(id);
            }
            let id = conversation.context("conversation missing")?;
            tx.execute("INSERT INTO messages(id,account_id,conversation_id,server_id,client_id,direction,kind,content,at_ms,media,media_source) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)",params![uuid::Uuid::new_v4().to_string(),account,id,event.server_message_id,event.client_message_id,direction,row.kind,row.content,at,row.media,row.source])?;
            tx.execute(
                "UPDATE conversations SET updated=?1,preview=?2 WHERE id=?3 AND updated<=?1",
                params![at, row.content.chars().take(200).collect::<String>(), id],
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
/// One message as stored in the projection.
struct Projected<'a> {
    kind: &'static str,
    content: &'a str,
    direction: &'static str,
    at: i64,
    media: Option<String>,
    source: Option<String>,
}

impl<'a> Projected<'a> {
    fn new(described: &'a content::Described, direction: &'static str, at: i64) -> Result<Self> {
        Ok(Self {
            kind: described.kind,
            content: &described.content,
            direction,
            at,
            media: described.media.as_ref().map(Value::to_string),
            source: described
                .source
                .as_ref()
                .map(serde_json::to_string)
                .transpose()?,
        })
    }
}

enum Stored {
    New,
    Unchanged,
    Changed(String),
}

/// Handles a message that may already be stored.
/// Its route (conversation, direction) and any text body are immutable: a change
/// means corruption. Parser-generated labels and media may be refreshed, so a newer
/// version re-projecting history neither fails nor keeps an older, poorer label.
fn reproject(
    tx: &rusqlite::Transaction<'_>,
    account: &str,
    event: &InboundEvent,
    row: &Projected<'_>,
) -> Result<Stored> {
    type Old = (
        String,
        String,
        String,
        String,
        String,
        String,
        Option<String>,
    );
    let old: Option<Old> = tx
        .query_row(
            "SELECT c.platform_id,m.direction,m.content,m.source,m.conversation_id,m.kind,m.media FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.account_id=?1 AND m.server_id=?2",
            params![account, event.server_message_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?, r.get(6)?)),
        )
        .optional()?;
    let Some((platform, direction, content, source, conversation, kind, media)) = old else {
        return Ok(Stored::New);
    };
    if source == "legacy" {
        anyhow::ensure!(platform == event.conversation_id, "legacy route conflict");
        tx.execute("UPDATE messages SET direction=?1,content=?2,client_id=?3,at_ms=?4,source='native' WHERE account_id=?5 AND server_id=?6",params![row.direction,row.content,event.client_message_id,row.at,account,event.server_message_id])?;
        return Ok(Stored::Changed(conversation));
    }
    anyhow::ensure!(
        platform == event.conversation_id && direction == row.direction,
        "conflicting server message projection"
    );
    if content == row.content && (media.is_some() || row.media.is_none()) {
        return Ok(Stored::Unchanged);
    }
    anyhow::ensure!(
        kind != "text" && row.kind != "text",
        "conflicting server message projection"
    );
    tx.execute(
        "UPDATE messages SET kind=?1,content=?2,media=?3,media_source=?4 WHERE account_id=?5 AND server_id=?6",
        params![row.kind, row.content, row.media, row.source, account, event.server_message_id],
    )?;
    Ok(Stored::Changed(conversation))
}

/// Removes a placeholder an older version stored for what is now a hidden packet.
fn drop_hidden(
    tx: &rusqlite::Transaction<'_>,
    account: &str,
    server_id: &str,
) -> Result<Option<String>> {
    let conversation: Option<String> = tx
        .query_row(
            "SELECT conversation_id FROM messages WHERE account_id=?1 AND server_id=?2 AND kind<>'text' AND source='native'",
            params![account, server_id],
            |r| r.get(0),
        )
        .optional()?;
    if conversation.is_some() {
        tx.execute(
            "DELETE FROM messages WHERE account_id=?1 AND server_id=?2 AND kind<>'text' AND source='native'",
            params![account, server_id],
        )?;
    }
    Ok(conversation)
}

/// A conversation first seen through our own message has no peer yet; the first
/// reply names it. Runs before message dedup so re-projected history repairs old rows.
fn repair_peer(
    tx: &rusqlite::Transaction<'_>,
    account: &str,
    event: &InboundEvent,
) -> Result<Option<String>> {
    if !valid_peer(&event.sender_sec_uid) {
        return Ok(None);
    }
    let repaired = tx.execute(
        "UPDATE conversations SET peer=?1 WHERE account_id=?2 AND platform_id=?3 AND peer=''",
        params![event.sender_sec_uid, account, event.conversation_id],
    )?;
    if repaired == 0 {
        return Ok(None);
    }
    Ok(Some(tx.query_row(
        "SELECT id FROM conversations WHERE account_id=?1 AND platform_id=?2",
        params![account, event.conversation_id],
        |r| r.get(0),
    )?))
}
/// Adds rich-media columns in place; the schema version stays 1 so an older client
/// can still open (and ignore) them after a downgrade.
fn add_media_columns(db: &Connection) -> Result<()> {
    let columns: Vec<String> = db
        .prepare("SELECT name FROM pragma_table_info('messages')")?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    for column in ["media", "media_source"] {
        if !columns.iter().any(|c| c == column) {
            db.execute_batch(&format!("ALTER TABLE messages ADD COLUMN {column} TEXT"))?;
        }
    }
    // Media (inline previews) counts toward the same rolling byte budget as bodies.
    db.execute_batch("
CREATE TRIGGER IF NOT EXISTS message_media_bytes_insert AFTER INSERT ON messages BEGIN UPDATE meta SET value=CAST(CAST(value AS INTEGER)+length(CAST(coalesce(new.media,'') AS BLOB)) AS TEXT) WHERE key='message_bytes'; END;
CREATE TRIGGER IF NOT EXISTS message_media_bytes_delete AFTER DELETE ON messages BEGIN UPDATE meta SET value=CAST(CAST(value AS INTEGER)-length(CAST(coalesce(old.media,'') AS BLOB)) AS TEXT) WHERE key='message_bytes'; END;
CREATE TRIGGER IF NOT EXISTS message_media_bytes_update AFTER UPDATE OF media ON messages BEGIN UPDATE meta SET value=CAST(CAST(value AS INTEGER)+length(CAST(coalesce(new.media,'') AS BLOB))-length(CAST(coalesce(old.media,'') AS BLOB)) AS TEXT) WHERE key='message_bytes'; END;")?;
    Ok(())
}
fn valid_peer(sec_uid: &str) -> bool {
    !sec_uid.is_empty()
        && sec_uid.len() <= 256
        && !sec_uid.starts_with("fallback_")
        && !sec_uid.chars().any(char::is_control)
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
