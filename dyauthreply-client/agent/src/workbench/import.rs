//! One-time read-only legacy import. Published transactionally; runtime never reads Python DB.
use super::{iso, now_ms, prune, Workbench};
use anyhow::Result;
use rusqlite::{params, Connection, OpenFlags, OptionalExtension};
use serde_json::json;
use std::path::Path;

fn timestamp(value: Option<String>) -> i64 {
    value
        .and_then(|s| {
            chrono::DateTime::parse_from_rfc3339(&s)
                .map(|d| d.timestamp_millis())
                .ok()
                .or_else(|| {
                    chrono::NaiveDateTime::parse_from_str(&s, "%Y-%m-%d %H:%M:%S%.f")
                        .ok()
                        .and_then(|d| d.and_local_timezone(chrono_tz::Asia::Shanghai).single())
                        .map(|d| d.timestamp_millis())
                })
        })
        .unwrap_or(0)
}
impl Workbench {
    /// # Errors
    /// Imports only into an empty projection; repeat completed imports are no-ops.
    /// Never changes source credentials, status, sender decisions or source history.
    pub fn import_legacy(&self, source: &Path) -> Result<serde_json::Value> {
        let mut db = self.lock()?;
        let tx = db.transaction()?;
        let done: Option<String> = tx
            .query_row(
                "SELECT value FROM meta WHERE key='legacy_import'",
                [],
                |r| r.get(0),
            )
            .optional()?;
        let repair = done.is_some();
        if let Some(report) = done {
            let report: serde_json::Value = serde_json::from_str(&report)?;
            if report["timezone"] == "Asia/Shanghai" {
                return Ok(report);
            }
        }
        let count: i64 = tx.query_row("SELECT count(*) FROM accounts", [], |r| r.get(0))?;
        anyhow::ensure!(
            count == 0 || repair,
            "import requires empty native workbench"
        );
        if repair {
            tx.execute("DELETE FROM messages WHERE source='legacy'", [])?;
        }
        let mut legacy = Connection::open_with_flags(source, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        let read = legacy.transaction()?;
        let ids = import_accounts(&read, &tx)?;
        let mut convs = 0;
        let mut messages = 0;
        for account in &ids {
            let mut query=read.prepare("SELECT id,platform_conversation_id,CAST(platform_conversation_short_id AS TEXT),peer_sec_uid,peer_nickname,peer_avatar,peer_unique_id,last_message_at,last_message_preview FROM core_douyin_conversation WHERE account_id=?1 AND is_deleted=0 ORDER BY last_message_at DESC LIMIT 2000")?;
            let rows = query.query_map([account], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, Option<String>>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, Option<String>>(4)?,
                    r.get::<_, Option<String>>(5)?,
                    r.get::<_, Option<String>>(6)?,
                    r.get::<_, Option<String>>(7)?,
                    r.get::<_, Option<String>>(8)?,
                ))
            })?;
            for row in rows {
                let (id, platform, short, peer, nickname, avatar, unique, at, preview) = row?;
                if platform.is_empty() {
                    continue;
                }
                let data = json!({"peer_nickname":nickname,"peer_avatar":avatar,"peer_unique_id":unique,"unread_count":0});
                anyhow::ensure!(
                    data.to_string().len() < 4096 && platform.len() <= 256 && peer.len() <= 256,
                    "conversation metadata oversized"
                );
                let exists:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM conversations WHERE account_id=?1 AND platform_id=?2)",params![account,platform],|r|r.get(0))?;
                if !exists {
                    super::reserve_conversation(&tx, account)?;
                }
                tx.execute("INSERT INTO conversations(id,account_id,platform_id,short_id,peer,data,updated,preview) VALUES(?1,?2,?3,?4,?5,?6,?7,?8) ON CONFLICT(account_id,platform_id) DO NOTHING",params![id,account,platform,short.unwrap_or_default(),peer,data.to_string(),timestamp(at.clone()),preview.clone().unwrap_or_default()])?;
                // Duplicate legacy local conversations use the canonical platform-scoped route.
                let canonical: String = tx.query_row(
                    "SELECT id FROM conversations WHERE account_id=?1 AND platform_id=?2",
                    params![account, platform],
                    |r| r.get(0),
                )?;
                tx.execute(
                    "UPDATE conversations SET updated=?1,preview=?2 WHERE id=?3",
                    params![
                        timestamp(at.clone()),
                        preview.clone().unwrap_or_default(),
                        canonical
                    ],
                )?;
                convs += 1;
                messages += import_messages(&read, &tx, &id, account, &canonical)?;
            }
            tx.execute("UPDATE conversations SET updated=coalesce((SELECT MAX(at_ms) FROM messages WHERE conversation_id=conversations.id),updated),preview=coalesce((SELECT substr(content,1,200) FROM messages WHERE conversation_id=conversations.id ORDER BY at_ms DESC,id DESC LIMIT 1),preview) WHERE account_id=?1",[account])?;
            prune(&tx, account, now_ms())?;
        }
        let retained: i64 = tx.query_row("SELECT count(*) FROM messages", [], |r| r.get(0))?;
        let report = json!({"accounts":ids.len(),"conversations":convs,"imported_messages":messages,"retained_messages":retained,"retention_days":30,"timezone":"Asia/Shanghai","imported_at":iso(now_ms())});
        tx.execute(
            "INSERT INTO meta(key,value) VALUES('legacy_import',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [report.to_string()],
        )?;
        tx.commit()?;
        Ok(report)
    }
}

fn import_accounts(
    read: &rusqlite::Transaction<'_>,
    tx: &rusqlite::Transaction<'_>,
) -> Result<Vec<String>> {
    let mut accounts=read.prepare("SELECT id,sec_uid,nickname,avatar,unique_id,daily_reply_quota FROM core_douyin_account WHERE is_deleted=0 AND deleted_at IS NULL ORDER BY id LIMIT 3001")?;
    let rows = accounts.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
            r.get::<_, Option<String>>(3)?,
            r.get::<_, Option<String>>(4)?,
            r.get::<_, i64>(5)?,
        ))
    })?;
    let mut ids = Vec::new();
    for row in rows {
        let (id, sec, nickname, avatar, unique, quota) = row?;
        uuid::Uuid::parse_str(&id)?;
        let data = json!({"id":id,"sec_uid":sec,"nickname":nickname,"avatar":avatar,"unique_id":unique,"daily_reply_quota":quota});
        anyhow::ensure!(
            data.to_string().len() < 4 * 1024 && ids.len() < 3000,
            "account import capacity exceeded"
        );
        tx.execute(
            "INSERT INTO accounts(id,sec_uid,data) VALUES(?1,?2,?3) ON CONFLICT(id) DO UPDATE SET data=excluded.data WHERE accounts.sec_uid=excluded.sec_uid",
            params![id, sec, data.to_string()],
        )?;
        let bound: String =
            tx.query_row("SELECT sec_uid FROM accounts WHERE id=?1", [&id], |r| {
                r.get(0)
            })?;
        anyhow::ensure!(bound == sec, "import account scope changed");
        ids.push(id);
    }

    Ok(ids)
}

fn import_messages(
    read: &rusqlite::Transaction<'_>,
    tx: &rusqlite::Transaction<'_>,
    source_id: &str,
    account: &str,
    canonical: &str,
) -> Result<usize> {
    let mut messages = 0;
    let mut mq=read.prepare("SELECT id,external_msg_id,direction,content_type,content,received_at FROM core_douyin_message WHERE conversation_id=?1 AND is_deleted=0 ORDER BY received_at DESC LIMIT 10000")?;
    let rows = mq.query_map([source_id], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, String>(2)?,
            r.get::<_, String>(3)?,
            r.get::<_, String>(4)?,
            r.get::<_, Option<String>>(5)?,
        ))
    })?;
    for row in rows {
        let (mid, external, direction, kind, content, at) = row?;
        let at = timestamp(at);
        if at < now_ms() - super::RETENTION_MS || at > now_ms() + 300_000 {
            continue;
        }
        anyhow::ensure!(
            content.len() <= 16 * 1024,
            "legacy content exceeds projection limit"
        );
        // Legacy inbound keys are srv_<19-digit-server-id>; outgoing keys are plain IDs.
        let server = external
            .strip_prefix("srv_")
            .or_else(|| external.strip_prefix("im:"))
            .unwrap_or(&external);
        let server = if server.is_empty() {
            format!("legacy:{mid}")
        } else {
            server.into()
        };
        messages+=tx.execute("INSERT INTO messages(id,account_id,conversation_id,server_id,direction,kind,content,at_ms,source) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'legacy') ON CONFLICT(account_id,server_id) DO NOTHING",params![mid,account,canonical,server,direction,kind,content,at])?;
    }
    Ok(messages)
}

#[cfg(test)]
mod tests {
    #[test]
    fn legacy_naive_times_are_shanghai_not_utc() {
        assert_eq!(
            super::timestamp(Some("2026-09-05 16:37:30.112000".into())),
            super::timestamp(Some("2026-09-05T08:37:30.112Z".into()))
        );
    }
}
