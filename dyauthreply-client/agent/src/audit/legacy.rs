use super::{
    excerpt, json, params, prune, upsert, AuditStore, Connection, Context, OptionalExtension, Path,
    Record, Result, Value, KEEP_MS,
};
use rusqlite::OpenFlags;
fn timestamp(raw: Option<String>) -> i64 {
    raw.and_then(|v| {
        chrono::DateTime::parse_from_rfc3339(&v)
            .ok()
            .map(|v| v.timestamp_millis())
            .or_else(|| {
                chrono::NaiveDateTime::parse_from_str(&v, "%Y-%m-%d %H:%M:%S%.f")
                    .ok()
                    .and_then(|t| t.and_local_timezone(chrono_tz::Asia::Shanghai).single())
                    .map(|v| v.timestamp_millis())
            })
    })
    .unwrap_or(0)
}
impl AuditStore {
    /// # Errors
    /// Read-only legacy import; the completion marker makes repeat imports idempotent.
    pub fn import_legacy(&self, path: &Path) -> Result<Value> {
        let mut db = self.lock()?;
        let tx = db.transaction()?;
        if let Some(raw) = tx
            .query_row(
                "SELECT value FROM meta WHERE key='legacy_import'",
                [],
                |r| r.get::<_, String>(0),
            )
            .optional()?
        {
            return Ok(serde_json::from_str(&raw)?);
        }
        let mut source = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        let source = source.transaction()?;
        let mut q=source.prepare("SELECT l.id,l.account_id,c.platform_conversation_id,l.matched_rule_id,l.reply_text,l.reply_links,l.result,l.error_message,l.duration_ms,l.sent_at,l.sys_create_datetime,m.content FROM core_douyin_reply_log l LEFT JOIN core_douyin_conversation c ON c.id=l.conversation_id LEFT JOIN core_douyin_message m ON m.id=l.trigger_message_id WHERE l.is_deleted=0 ORDER BY l.sys_create_datetime DESC LIMIT 100000")?;
        let mut rows = q.query([])?;
        let mut retained = 0;
        while let Some(row) = rows.next()? {
            let created = timestamp(row.get(10)?);
            if created < crate::workbench::now_ms() - KEEP_MS {
                continue;
            }
            let text = row.get::<_, Option<String>>(4)?.unwrap_or_default();
            let links: Value =
                serde_json::from_str(row.get::<_, Option<String>>(5)?.as_deref().unwrap_or("[]"))?;
            let links = links
                .as_array()
                .map(|rows| {
                    rows.iter()
                        .filter_map(|v| v.as_str().or_else(|| v["url"].as_str()))
                        .collect::<Vec<_>>()
                        .join("\n")
                })
                .unwrap_or_default();
            let record = Record {
                id: format!("legacy:{}", row.get::<_, String>(0)?),
                account_id: row.get(1)?,
                mode: "automatic".into(),
                conversation_id: row.get::<_, Option<String>>(2)?.unwrap_or_default(),
                rule_id: row.get(3)?,
                reply_text: excerpt(format!("{text}\n{links}").trim()),
                result: row.get(6)?,
                error_message: excerpt(&row.get::<_, Option<String>>(7)?.unwrap_or_default()),
                duration_ms: row.get::<_, i64>(8)?.max(0),
                delivered_at_ms: {
                    let t = timestamp(row.get(9)?);
                    (t > 0).then_some(t)
                },
                created_at_ms: created,
                trigger_message_content: excerpt(
                    &row.get::<_, Option<String>>(11)?.unwrap_or_default(),
                ),
                batch_id: None,
                platform_message_ids: vec![],
                attempt_count: 0,
            };
            retained += usize::from(upsert(&tx, &record)?);
        }
        // Small aggregate survives detail eviction; never seed quota from disposable message counts.
        let mut daily=source.prepare("SELECT account_id,substr(coalesce(sent_at,sys_create_datetime),1,10),count(*) FROM core_douyin_reply_log WHERE is_deleted=0 AND result='success' AND sys_create_datetime>=?1 GROUP BY account_id,substr(coalesce(sent_at,sys_create_datetime),1,10)")?;
        let start = chrono::DateTime::from_timestamp_millis(crate::workbench::now_ms() - KEEP_MS)
            .context("invalid time")?
            .with_timezone(&chrono_tz::Asia::Shanghai)
            .format("%Y-%m-%d")
            .to_string();
        for row in daily.query_map([start], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, i64>(2)?,
            ))
        })? {
            let (account, day, count) = row?;
            tx.execute("INSERT INTO legacy_daily VALUES(?1,?2,?3) ON CONFLICT(account_id,day) DO UPDATE SET success=excluded.success",params![account,day,count])?;
        }
        let report = json!({"legacy_records":retained,"retention_days":30});
        tx.execute(
            "INSERT INTO meta VALUES('legacy_import',?1)",
            [report.to_string()],
        )?;
        prune(&tx)?;
        tx.commit()?;
        Ok(report)
    }
}
