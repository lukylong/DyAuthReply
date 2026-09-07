//! Bounded disposable reply audit read-model. Protocol decisions and quota never read this DB.
use anyhow::{Context, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
};
pub mod api;
mod legacy;
#[cfg(test)]
mod tests;
const KEEP_MS: i64 = 30 * 86_400_000;
const MAX_ROWS: i64 = 100_000;
#[derive(Clone, Serialize, Deserialize)]
pub struct Record {
    pub id: String,
    pub account_id: String,
    pub mode: String,
    pub result: String,
    pub conversation_id: String,
    pub rule_id: Option<String>,
    pub reply_text: String,
    pub error_message: String,
    pub trigger_message_content: String,
    pub created_at_ms: i64,
    pub delivered_at_ms: Option<i64>,
    pub duration_ms: i64,
    pub batch_id: Option<String>,
    pub platform_message_ids: Vec<String>,
    pub attempt_count: u64,
}
pub struct AuditStore {
    db: Mutex<Connection>,
    revision: AtomicU64,
}
impl AuditStore {
    /// # Errors
    /// Rejects inaccessible or incompatible audit data; never opens protocol credentials.
    pub fn open(root: &Path) -> Result<Self> {
        let path = root.join("reply-audit.sqlite3");
        let db = Connection::open(&path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        }
        db.busy_timeout(std::time::Duration::from_secs(2))?;
        db.execute_batch("PRAGMA auto_vacuum=INCREMENTAL; PRAGMA journal_mode=WAL; PRAGMA journal_size_limit=4194304; PRAGMA max_page_count=32768;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,mode TEXT NOT NULL,result TEXT NOT NULL,created INTEGER NOT NULL,delivered INTEGER,duration INTEGER NOT NULL,record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS records_recent ON records(created DESC,id);
CREATE INDEX IF NOT EXISTS records_account ON records(account_id,mode,created DESC);
CREATE INDEX IF NOT EXISTS records_result ON records(mode,result,created DESC);
INSERT OR IGNORE INTO meta(key,value) SELECT 'bytes',CAST(coalesce(sum(length(CAST(record AS BLOB))),0) AS TEXT) FROM records;
CREATE TRIGGER IF NOT EXISTS audit_bytes_insert AFTER INSERT ON records BEGIN UPDATE meta SET value=CAST(CAST(value AS INTEGER)+length(CAST(new.record AS BLOB)) AS TEXT) WHERE key='bytes'; END;
CREATE TRIGGER IF NOT EXISTS audit_bytes_update AFTER UPDATE OF record ON records BEGIN UPDATE meta SET value=CAST(CAST(value AS INTEGER)+length(CAST(new.record AS BLOB))-length(CAST(old.record AS BLOB)) AS TEXT) WHERE key='bytes'; END;
CREATE TRIGGER IF NOT EXISTS audit_bytes_delete AFTER DELETE ON records BEGIN UPDATE meta SET value=CAST(CAST(value AS INTEGER)-length(CAST(old.record AS BLOB)) AS TEXT) WHERE key='bytes'; END;
CREATE TABLE IF NOT EXISTS legacy_daily(account_id TEXT NOT NULL,day TEXT NOT NULL,success INTEGER NOT NULL,PRIMARY KEY(account_id,day));")?;
        Ok(Self {
            db: Mutex::new(db),
            revision: AtomicU64::new(0),
        })
    }
    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Connection>> {
        self.db
            .lock()
            .map_err(|_| anyhow::anyhow!("audit lock poisoned"))
    }
    #[must_use]
    pub fn revision(&self) -> u64 {
        self.revision.load(Ordering::Relaxed)
    }
    /// Rebuilds after first start/journal overflow and commits rows+cursor together.
    /// # Errors
    /// Reports source/storage failure; caller must surface stale audit instead of inventing empty data.
    pub fn sync(&self, core: &crate::store::CoreStore) -> Result<bool> {
        let mut db = self.lock()?;
        let cursor: Option<i64> = db
            .query_row(
                "SELECT CAST(value AS INTEGER) FROM meta WHERE key='cursor'",
                [],
                |r| r.get(0),
            )
            .optional()?;
        let source_id = core.audit_source_id()?;
        let saved: Option<String> = db
            .query_row("SELECT value FROM meta WHERE key='core_id'", [], |r| {
                r.get(0)
            })
            .optional()?;
        let feed = core.audit_changes(cursor.unwrap_or(0), 256)?;
        let replaced = saved.as_deref() != Some(source_id.as_str())
            || cursor.is_some_and(|v| v > feed.maximum);
        let reset = replaced
            || cursor.is_none()
            || cursor.is_some_and(|c| c < feed.minimum.saturating_sub(1));
        let next = if reset { feed.maximum } else { feed.through };
        if !reset && cursor == Some(next) && feed.records.is_empty() {
            return Ok(false);
        }
        let tx = db.transaction()?;
        let mut changed = false;
        if replaced {
            tx.execute("DELETE FROM records WHERE id NOT LIKE 'legacy:%'", [])?;
        }
        if reset {
            let mut before = None;
            let mut total = 0usize;
            loop {
                let page = core.audit_snapshot_page(
                    crate::workbench::now_ms() - KEEP_MS,
                    before.as_ref(),
                    512,
                )?;
                if page.is_empty() {
                    break;
                }
                if let Some(last) = page.last() {
                    before = Some((
                        last.created_at_ms,
                        last.batch_id.clone().context("batch snapshot missing ID")?,
                    ));
                }
                let count = page.len();
                for record in page {
                    changed |= upsert(&tx, &record)?;
                }
                total += count;
                if count < 512 || total >= 100_000 {
                    break;
                }
            }
            for record in core
                .audit_changes(0, 4096)?
                .records
                .into_iter()
                .filter(|r| r.batch_id.is_none())
            {
                changed |= upsert(&tx, &record)?;
            }
        } else {
            for record in feed.records {
                changed |= upsert(&tx, &record)?;
            }
        }
        tx.execute("INSERT INTO meta VALUES('core_id',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[source_id])?;
        tx.execute("INSERT INTO meta VALUES('sync_pending',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[i32::from(next<feed.maximum).to_string()])?;
        tx.execute("INSERT INTO meta VALUES('cursor',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[next.to_string()])?;
        if reset && cursor.is_some() {
            tx.execute("INSERT INTO meta VALUES('journal_gap','1') ON CONFLICT(key) DO UPDATE SET value='1'",[])?;
        }
        prune(&tx)?;
        tx.commit()?;
        if changed {
            self.revision.fetch_add(1, Ordering::Relaxed);
        }
        Ok(changed)
    }
    /// # Errors
    /// Enforces bounded pagination and independent source/result filters.
    pub fn list(
        &self,
        account: Option<&str>,
        mode: &str,
        result: Option<&str>,
        page: u32,
        size: u32,
    ) -> Result<Value> {
        validate(mode, result)?;
        anyhow::ensure!(
            page > 0 && page <= 10000 && (1..=100).contains(&size),
            "分页参数无效"
        );
        let db = self.lock()?;
        let account = account.unwrap_or("");
        let result = result.unwrap_or("");
        let filter="created>=?1 AND (?2='' OR account_id=?2) AND (?3='all' OR mode=?3) AND (?4='' OR result=?4)";
        let cutoff = crate::workbench::now_ms() - KEEP_MS;
        let total: i64 = db.query_row(
            &format!("SELECT count(*) FROM records WHERE {filter}"),
            params![cutoff, account, mode, result],
            |r| r.get(0),
        )?;
        let mut query = db.prepare(&format!(
            "SELECT record FROM records WHERE {filter} ORDER BY created DESC,id LIMIT ?5 OFFSET ?6"
        ))?;
        let rows = query.query_map(
            params![cutoff, account, mode, result, size, (page - 1) * size],
            |r| r.get::<_, String>(0),
        )?;
        let items = rows
            .map(|r| Ok(serde_json::from_str::<Record>(&r?)?))
            .collect::<Result<Vec<_>>>()?;
        Ok(
            json!({"items":items,"total":total,"retention_days":30,"has_gap":gap(&db)?,"sync_pending":sync_pending(&db)?}),
        )
    }
    /// # Errors
    /// Counts retained records, not chat bubbles; uncertain/partial remain separate from failures.
    pub fn stats(&self, account: Option<&str>, mode: &str, scope: &str) -> Result<Value> {
        validate(mode, None)?;
        anyhow::ensure!(matches!(scope, "all" | "today"), "统计范围无效");
        let cutoff = if scope == "today" {
            today_start()?
        } else {
            crate::workbench::now_ms() - KEEP_MS
        };
        let db = self.lock()?;
        let mut q=db.prepare("SELECT result,count(*),total(duration) FROM records WHERE created>=?1 AND (?2='' OR account_id=?2) AND (?3='all' OR mode=?3) GROUP BY result")?;
        let rows = q.query_map(params![cutoff, account.unwrap_or(""), mode], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, u32>(1)?,
                r.get::<_, f64>(2)?,
            ))
        })?;
        let mut stats = json!({"total":0,"success":0,"failed":0,"skipped":0,"cooldown":0,"quota_exceeded":0,"silent":0,"pending":0,"uncertain":0,"partial":0,"avg_duration_ms":0.0,"retention_days":30,"has_gap":gap(&db)?,"sync_pending":sync_pending(&db)?});
        let (mut total, mut duration) = (0u32, 0.0f64);
        for row in rows {
            let (result, count, time) = row?;
            stats[&result] = json!(count);
            total += count;
            duration += time;
        }
        stats["total"] = json!(total);
        stats["avg_duration_ms"] = json!(if total > 0 {
            duration / f64::from(total)
        } else {
            0.0
        });
        Ok(stats)
    }
    /// # Errors
    /// Legacy counters are captured at import and independent of audit-body reclamation.
    pub fn legacy_today(&self) -> Result<std::collections::BTreeMap<String, u64>> {
        let day = chrono::DateTime::from_timestamp_millis(crate::workbench::now_ms())
            .context("invalid time")?
            .with_timezone(&chrono_tz::Asia::Shanghai)
            .format("%Y-%m-%d")
            .to_string();
        let db = self.lock()?;
        let mut q = db.prepare("SELECT account_id,success FROM legacy_daily WHERE day=?1")?;
        let rows = q
            .query_map([day], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect::<rusqlite::Result<_>>()?;
        Ok(rows)
    }
    /// # Errors
    /// Reclaims only audit rows/pages, never correctness state.
    pub fn maintain(&self) -> Result<()> {
        let mut db = self.lock()?;
        let tx = db.transaction()?;
        prune(&tx)?;
        tx.commit()?;
        db.execute_batch("PRAGMA incremental_vacuum(256); PRAGMA wal_checkpoint(PASSIVE);")?;
        Ok(())
    }
}
fn validate(mode: &str, result: Option<&str>) -> Result<()> {
    anyhow::ensure!(
        matches!(mode, "automatic" | "manual" | "all"),
        "记录来源无效"
    );
    anyhow::ensure!(
        result.is_none_or(|r| matches!(
            r,
            "" | "success"
                | "failed"
                | "skipped"
                | "cooldown"
                | "quota_exceeded"
                | "silent"
                | "pending"
                | "uncertain"
                | "partial"
        )),
        "结果筛选无效"
    );
    Ok(())
}
fn upsert(tx: &rusqlite::Transaction<'_>, record: &Record) -> Result<bool> {
    let text = serde_json::to_string(record)?;
    anyhow::ensure!(text.len() <= 16384, "audit record too large");
    let changed=tx.execute("INSERT INTO records VALUES(?1,?2,?3,?4,?5,?6,?7,?8) ON CONFLICT(id) DO UPDATE SET result=excluded.result,delivered=excluded.delivered,duration=excluded.duration,record=excluded.record WHERE records.record!=excluded.record AND (json_extract(records.record,'$.batch_id') IS NULL OR json_extract(excluded.record,'$.batch_id') IS NOT NULL)",params![record.id,record.account_id,record.mode,record.result,record.created_at_ms,record.delivered_at_ms,record.duration_ms,text])?>0;
    if changed {
        prune_bytes(tx)?;
    }
    Ok(changed)
}
fn prune(tx: &rusqlite::Transaction<'_>) -> Result<()> {
    tx.execute(
        "DELETE FROM records WHERE created<?1",
        [crate::workbench::now_ms() - KEEP_MS],
    )?;
    let removed=tx.execute("DELETE FROM records WHERE id IN (SELECT id FROM records ORDER BY created DESC,id LIMIT -1 OFFSET ?1)",[MAX_ROWS])?;
    if removed > 0 {
        tx.execute(
            "INSERT INTO meta VALUES('row_cap','1') ON CONFLICT(key) DO UPDATE SET value='1'",
            [],
        )?;
    }
    Ok(())
}
fn gap(db: &Connection) -> Result<bool> {
    Ok(db.query_row(
        "SELECT EXISTS(SELECT 1 FROM meta WHERE key IN ('row_cap','journal_gap') AND value='1')",
        [],
        |r| r.get(0),
    )?)
}
fn today_start() -> Result<i64> {
    let now = chrono::DateTime::from_timestamp_millis(crate::workbench::now_ms())
        .context("invalid time")?
        .with_timezone(&chrono_tz::Asia::Shanghai);
    Ok(now
        .date_naive()
        .and_hms_opt(0, 0, 0)
        .context("invalid day")?
        .and_local_timezone(chrono_tz::Asia::Shanghai)
        .single()
        .context("invalid timezone day")?
        .timestamp_millis())
}
pub(crate) fn excerpt(value: &str) -> String {
    value.chars().take(1024).collect()
}

fn prune_bytes(tx: &rusqlite::Transaction<'_>) -> Result<()> {
    loop {
        let bytes: i64 = tx.query_row(
            "SELECT CAST(value AS INTEGER) FROM meta WHERE key='bytes'",
            [],
            |r| r.get(0),
        )?;
        if bytes <= 32 * 1024 * 1024 {
            break;
        }
        tx.execute(
            "INSERT INTO meta VALUES('row_cap','1') ON CONFLICT(key) DO UPDATE SET value='1'",
            [],
        )?;
        anyhow::ensure!(tx.execute("DELETE FROM records WHERE id IN (SELECT id FROM records ORDER BY created,id LIMIT 128)",[])?>0,"audit byte counter mismatch");
    }
    Ok(())
}

fn sync_pending(db: &Connection) -> Result<bool> {
    Ok(db.query_row(
        "SELECT EXISTS(SELECT 1 FROM meta WHERE key='sync_pending' AND value='1')",
        [],
        |r| r.get(0),
    )?)
}
