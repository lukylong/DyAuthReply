//! Optional versioned audit change feed. Bounded metadata only; no send decision depends on it.
use super::{params, Connection, CoreStore, OptionalExtension, StoreError};
use crate::audit::Record;
#[derive(Clone, Copy)]
pub struct DecisionAudit<'a> {
    pub result: &'a str,
    pub reason: &'a str,
    pub rule_id: Option<&'a str>,
}
pub struct AuditChanges {
    pub minimum: i64,
    pub maximum: i64,
    pub through: i64,
    pub records: Vec<Record>,
}
impl CoreStore {
    /// # Errors
    /// Adds a backward-compatible observation extension without changing correctness columns.
    pub fn enable_audit_journal(&self) -> Result<(), StoreError> {
        let mut connection = self.lock_connection()?;
        let c = connection.transaction()?;
        let existing: Option<String> = c
            .query_row(
                "SELECT value FROM meta WHERE key='audit_extension_version'",
                [],
                |r| r.get(0),
            )
            .optional()?;
        if existing.as_deref().is_some_and(|v| v != "1") {
            return Err(StoreError::InvalidInput("unsupported audit extension"));
        }
        c.execute_batch("CREATE TABLE IF NOT EXISTS audit_changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,batch_id TEXT,data TEXT,record_key TEXT);
CREATE INDEX IF NOT EXISTS audit_change_key ON audit_changes(record_key,seq DESC);
CREATE TRIGGER IF NOT EXISTS audit_batch_insert AFTER INSERT ON outbound_batches BEGIN INSERT INTO audit_changes(batch_id) VALUES(new.id); DELETE FROM audit_changes WHERE seq<=(SELECT max(seq)-4096 FROM audit_changes); END;
CREATE TRIGGER IF NOT EXISTS audit_batch_update AFTER UPDATE ON outbound_batches BEGIN INSERT INTO audit_changes(batch_id) VALUES(new.id); DELETE FROM audit_changes WHERE seq<=(SELECT max(seq)-4096 FROM audit_changes); END;
CREATE INDEX IF NOT EXISTS audit_batch_created ON outbound_batches(created_at_ms DESC,id);
INSERT OR IGNORE INTO meta(key,value) VALUES('audit_extension_version','1');")?;
        let version: String = c.query_row(
            "SELECT value FROM meta WHERE key='audit_extension_version'",
            [],
            |r| r.get(0),
        )?;
        if version != "1" {
            return Err(StoreError::InvalidInput("unsupported audit extension"));
        }
        c.commit()?;
        Ok(())
    }
    /// # Errors
    /// Reads a consistent feed snapshot; seq gaps are returned for projection repair.
    pub fn audit_changes(&self, after: i64, limit: u32) -> Result<AuditChanges, StoreError> {
        let c = self.lock_connection()?;
        let (minimum, maximum) = c.query_row(
            "SELECT coalesce(min(seq),0),coalesce(max(seq),0) FROM audit_changes",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        let mut q = c.prepare(
            "SELECT seq,batch_id,data FROM audit_changes WHERE seq>?1 ORDER BY seq LIMIT ?2",
        )?;
        let rows = q.query_map(params![after, limit.min(4096)], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, Option<String>>(2)?,
            ))
        })?;
        let mut out = AuditChanges {
            minimum,
            maximum,
            through: after,
            records: vec![],
        };
        for row in rows {
            let (seq, id, data) = row?;
            out.through = seq;
            if let Some(id) = id {
                if let Some(record) = batch_record(&c, &id)? {
                    out.records.push(record);
                }
            } else if let Some(data) = data {
                out.records.push(
                    serde_json::from_str(&data)
                        .map_err(|_| StoreError::InvalidInput("invalid audit journal event"))?,
                );
            }
        }
        Ok(out)
    }
    /// # Errors
    /// Bounded recent outbox backfill; it never marks an uncertain result failed.
    pub fn audit_snapshot_page(
        &self,
        cutoff: i64,
        before: Option<&(i64, String)>,
        limit: u32,
    ) -> Result<Vec<Record>, StoreError> {
        let c = self.lock_connection()?;
        let mut q=c.prepare("SELECT id FROM outbound_batches WHERE created_at_ms>=?1 AND (created_at_ms<?2 OR (created_at_ms=?2 AND id<?3)) ORDER BY created_at_ms DESC,id DESC LIMIT ?4")?;
        let ids = q
            .query_map(
                params![
                    cutoff,
                    before.map_or(i64::MAX, |v| v.0),
                    before.map_or("~", |v| v.1.as_str()),
                    limit.min(512)
                ],
                |r| r.get::<_, String>(0),
            )?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        ids.iter()
            .filter_map(|id| batch_record(&c, id).transpose())
            .collect()
    }
    /// # Errors
    /// Returns the correctness-store identity used to reject stale projection cursors.
    pub fn audit_source_id(&self) -> Result<String, StoreError> {
        let c = self.lock_connection()?;
        Ok(super::read_database_id(&c)?.to_string())
    }
    /// # Errors
    /// Logs a deferred observation only while its canonical decision is still pending.
    pub fn observe_audit_wait(
        &self,
        lease: &super::LeaseToken,
        key: super::InboundReceiptKey<'_>,
        audit: DecisionAudit<'_>,
    ) -> Result<(), StoreError> {
        let mut c = self.lock_connection()?;
        let tx = c.transaction()?;
        let now = super::current_time_ms()?;
        super::require_fence(&tx, lease, now)?;
        let decided:bool=tx.query_row("SELECT EXISTS(SELECT 1 FROM inbound_receipts WHERE account_id=?1 AND stream='douyin-im-decision' AND event_id=?2)",params![lease.account_id,key.event_id],|r|r.get(0))?;
        if !decided {
            let receipt = super::load_inbound_receipt(
                &tx,
                &lease.account_id,
                key.stream,
                key.generation,
                key.event_id,
            )?;
            if receipt.payload_hash != key.payload_hash {
                return Err(StoreError::InvalidInput("audit observation hash mismatch"));
            }
            decision(&tx, lease, receipt.payload.as_deref(), audit, now)?;
        }
        tx.commit()?;
        Ok(())
    }
    /// # Errors
    /// Reads durable native quota usage independent of retained audit/chat bodies.
    pub fn native_reply_today(
        &self,
    ) -> Result<std::collections::BTreeMap<String, u64>, StoreError> {
        let now = chrono::DateTime::from_timestamp_millis(super::current_time_ms()?)
            .ok_or(StoreError::InvalidInput("invalid clock"))?
            .with_timezone(&chrono_tz::Asia::Shanghai);
        let start = now
            .date_naive()
            .and_hms_opt(0, 0, 0)
            .and_then(|v| v.and_local_timezone(chrono_tz::Asia::Shanghai).single())
            .ok_or(StoreError::InvalidInput("invalid day"))?
            .timestamp_millis();
        let c = self.lock_connection()?;
        let mut q=c.prepare("SELECT account_id,used_count FROM reply_guard_scopes WHERE kind='account' AND day_start_ms=?1")?;
        let result = q
            .query_map([start], |r| Ok((r.get(0)?, r.get(1)?)))?
            .collect::<rusqlite::Result<_>>()?;
        Ok(result)
    }
}
pub(super) fn decision(
    c: &Connection,
    lease: &super::LeaseToken,
    payload: Option<&[u8]>,
    audit: DecisionAudit<'_>,
    now: i64,
) -> Result<(), StoreError> {
    if !super::table_exists(c, "audit_changes")? {
        return Ok(());
    }
    let Some(payload) = payload else {
        return Ok(());
    };
    let Ok(event) = serde_json::from_slice::<crate::runtime::inbound::InboundEvent>(payload) else {
        return Ok(());
    };
    let record = Record {
        id: format!("auto:{}:{}", lease.account_id, event.server_message_id),
        account_id: lease.account_id.clone(),
        mode: "automatic".into(),
        result: audit.result.into(),
        conversation_id: event.conversation_id,
        rule_id: audit.rule_id.map(str::to_owned),
        reply_text: String::new(),
        error_message: audit.reason.into(),
        trigger_message_content: crate::audit::excerpt(event.text.as_deref().unwrap_or("")),
        created_at_ms: now,
        delivered_at_ms: None,
        duration_ms: 0,
        batch_id: None,
        platform_message_ids: vec![],
        attempt_count: 0,
    };
    let data = serde_json::to_string(&record)
        .map_err(|_| StoreError::InvalidInput("audit serialization failed"))?;
    let old: Option<String> = c
        .query_row(
            "SELECT data FROM audit_changes WHERE record_key=?1 ORDER BY seq DESC LIMIT 1",
            [&record.id],
            |r| r.get(0),
        )
        .optional()?
        .flatten();
    if old
        .as_deref()
        .and_then(|v| serde_json::from_str::<Record>(v).ok())
        .is_some_and(|r| r.result == record.result && r.error_message == record.error_message)
    {
        return Ok(());
    }
    c.execute(
        "INSERT INTO audit_changes(data,record_key) VALUES(?1,?2)",
        params![data, record.id],
    )?;
    c.execute(
        "DELETE FROM audit_changes WHERE seq<=(SELECT max(seq)-4096 FROM audit_changes)",
        [],
    )?;
    Ok(())
}
fn batch_record(c: &Connection, id: &str) -> Result<Option<Record>, StoreError> {
    let row=c.query_row("SELECT account_id,trigger_id,response_id,status,created_at_ms,updated_at_ms FROM outbound_batches WHERE id=?1",[id],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?,r.get::<_,String>(2)?,r.get::<_,String>(3)?,r.get::<_,i64>(4)?,r.get::<_,i64>(5)?))).optional()?;
    let Some((account, trigger, route, status, created, updated)) = row else {
        return Ok(None);
    };
    let route: serde_json::Value = serde_json::from_str(&route).unwrap_or_default();
    let automatic = trigger.starts_with("auto:");
    let mut q=c.prepare("SELECT payload,status,platform_message_id,attempt_count,updated_at_ms,last_error FROM outbound_segments WHERE batch_id=?1 ORDER BY ordinal")?;
    let rows = q.query_map([id], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, Option<String>>(2)?,
            r.get::<_, u64>(3)?,
            r.get::<_, i64>(4)?,
            r.get::<_, Option<String>>(5)?,
        ))
    })?;
    let (mut texts, mut ids, mut attempts, mut delivered, mut error) =
        (Vec::new(), Vec::new(), 0, None, String::new());
    for row in rows {
        let (text, state, server, count, time, err) = row?;
        texts.push(crate::audit::excerpt(&text));
        attempts += count;
        if state == "confirmed" {
            if let Some(server) = server {
                ids.push(server);
            }
            delivered = Some(delivered.map_or(time, |old: i64| old.max(time)));
        }
        if let Some(err) = err {
            error = crate::audit::excerpt(&err);
        }
    }
    let result = match status.as_str() {
        "confirmed" => "success",
        "rejected" => "failed",
        "partial" => "partial",
        "sending" | "uncertain" => "uncertain",
        _ => "pending",
    };
    Ok(Some(Record {
        id: if automatic {
            format!("auto:{account}:{}", trigger.trim_start_matches("auto:"))
        } else {
            format!("manual:{id}")
        },
        account_id: account,
        mode: if automatic { "automatic" } else { "manual" }.into(),
        result: result.into(),
        conversation_id: route["conversation_id"].as_str().unwrap_or("").into(),
        rule_id: route["rule_id"].as_str().map(str::to_owned),
        reply_text: crate::audit::excerpt(&texts.join("\n")),
        error_message: error,
        trigger_message_content: String::new(),
        created_at_ms: created,
        delivered_at_ms: delivered,
        duration_ms: updated.saturating_sub(created).max(0),
        batch_id: Some(id.into()),
        platform_message_ids: ids,
        attempt_count: attempts,
    }))
}
