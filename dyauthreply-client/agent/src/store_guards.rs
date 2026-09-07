//! Versioned persistent automatic-reply guard scopes, separate from disposable chat.
use super::{
    load_batch, params, validate_required_index, validate_required_tables, Connection,
    OptionalExtension, OutboundBatch, SegmentStatus, StoreError,
};
use serde::{Deserialize, Serialize};
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GuardPolicy {
    pub rule_id: String,
    pub conversation_id: String,
    pub peer_id: String,
    pub timezone: String,
    pub daily_quota: u32,
    pub cooldown_ms: u64,
    pub minimum_interval_ms: u64,
    pub daily_peer_limit: bool,
}
pub(super) fn create_schema(connection: &Connection) -> Result<(), StoreError> {
    connection.execute_batch("CREATE TABLE auto_reply_batches (
        batch_id TEXT PRIMARY KEY NOT NULL REFERENCES outbound_batches(id) ON DELETE CASCADE,
        account_id TEXT NOT NULL, policy_json TEXT NOT NULL, settled INTEGER NOT NULL DEFAULT 0 CHECK(settled IN (0,1)),
        created_at_ms INTEGER NOT NULL, settled_at_ms INTEGER);
        CREATE TABLE reply_guard_scopes (
        account_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('account','rule','peer')), scope_key TEXT NOT NULL, timezone TEXT NOT NULL,
        last_delivery_at_ms INTEGER, day_start_ms INTEGER, used_count INTEGER NOT NULL DEFAULT 0 CHECK(used_count>=0),
        reserved_batch_id TEXT REFERENCES auto_reply_batches(batch_id),updated_at_ms INTEGER NOT NULL,
        PRIMARY KEY(account_id,kind,scope_key));
        CREATE INDEX reply_guard_scopes_reserved_idx ON reply_guard_scopes(reserved_batch_id) WHERE reserved_batch_id IS NOT NULL;
        CREATE INDEX auto_reply_batches_pending_idx ON auto_reply_batches(account_id,settled,created_at_ms);")?;
    Ok(())
}
pub(super) fn validate_schema(connection: &Connection) -> Result<(), StoreError> {
    validate_required_tables(
        connection,
        &[
            (
                "auto_reply_batches",
                &[
                    "batch_id",
                    "account_id",
                    "policy_json",
                    "settled",
                    "created_at_ms",
                    "settled_at_ms",
                ],
            ),
            (
                "reply_guard_scopes",
                &[
                    "account_id",
                    "kind",
                    "scope_key",
                    "timezone",
                    "last_delivery_at_ms",
                    "day_start_ms",
                    "used_count",
                    "reserved_batch_id",
                    "updated_at_ms",
                ],
            ),
        ],
    )?;
    validate_required_index(connection, "reply_guard_scopes_reserved_idx")?;
    validate_required_index(connection, "auto_reply_batches_pending_idx")?;
    Ok(())
}

use chrono::{DateTime, Utc};
use chrono_tz::Tz;
use sha2::{Digest, Sha256};
const MAX_SCOPES_PER_ACCOUNT: u32 = 10_000;
impl GuardPolicy {
    fn validate(&self) -> Result<(), StoreError> {
        if self.rule_id.is_empty()
            || self.rule_id.len() > 128
            || self.conversation_id.is_empty()
            || self.conversation_id.len() > 256
            || self.peer_id.is_empty()
            || self.peer_id.len() > 256
            || self.timezone.parse::<Tz>().is_err()
            || self.daily_quota > 1_000_000
            || self.cooldown_ms > 31_536_000_000
            || self.minimum_interval_ms > 86_400_000
        {
            return Err(StoreError::InvalidInput("invalid automatic guard policy"));
        }
        Ok(())
    }
    fn scopes(&self) -> [(&'static str, String); 3] {
        let rule = serde_json::to_vec(&[&self.rule_id, &self.conversation_id])
            .expect("string tuple serializes");
        [
            ("account", "account".into()),
            ("rule", format!("{:x}", Sha256::digest(rule))),
            (
                "peer",
                format!("{:x}", Sha256::digest(self.peer_id.as_bytes())),
            ),
        ]
    }
    fn day_start(&self, now: i64) -> Result<i64, StoreError> {
        let tz = self
            .timezone
            .parse::<Tz>()
            .map_err(|_| StoreError::InvalidInput("invalid guard timezone"))?;
        let time = DateTime::<Utc>::from_timestamp_millis(now)
            .ok_or(StoreError::InvalidInput("invalid guard time"))?
            .with_timezone(&tz);
        // Some zones skip local midnight on a DST transition. Find the first
        // actual local minute of that date; ambiguous midnight uses its earliest instant.
        for minute in 0..1440 {
            let local = time
                .date_naive()
                .and_hms_opt(minute / 60, minute % 60, 0)
                .expect("bounded minute");
            if let Some(start) = local.and_local_timezone(tz).earliest() {
                return Ok(start.timestamp_millis());
            }
        }
        Err(StoreError::InvalidInput("guard date has no start"))
    }
}
struct Scope {
    last: Option<i64>,
    day: Option<i64>,
    used: u32,
    reserved: Option<String>,
    timezone: String,
}
fn read_scope(
    c: &Connection,
    account: &str,
    kind: &str,
    key: &str,
) -> Result<Option<Scope>, StoreError> {
    Ok(c.query_row("SELECT last_delivery_at_ms,day_start_ms,used_count,reserved_batch_id,timezone FROM reply_guard_scopes
        WHERE account_id=?1 AND kind=?2 AND scope_key=?3",params![account,kind,key],|r|Ok(Scope{last:r.get(0)?,day:r.get(1)?,used:r.get(2)?,reserved:r.get(3)?,timezone:r.get(4)?})).optional()?)
}
fn blocked(reason: &'static str, retry: Option<i64>) -> StoreError {
    StoreError::ReplyGuardBlocked {
        reason,
        retry_at_ms: retry,
    }
}
fn check_scope(
    scope: &Scope,
    kind: &str,
    batch: &OutboundBatch,
    policy: &GuardPolicy,
    now: i64,
    today: i64,
) -> Result<(), StoreError> {
    if scope.timezone != policy.timezone {
        return Err(blocked("timezone_change_requires_migration", None));
    }
    if scope.reserved.as_ref().is_some_and(|id| id != &batch.id) {
        return Err(blocked("pending_reply", None));
    }
    let used = if scope.day == Some(today) {
        scope.used
    } else {
        0
    };
    if kind == "account" && used >= policy.daily_quota {
        return Err(blocked("account_daily_quota", None));
    }
    if kind == "peer" && policy.daily_peer_limit && used > 0 {
        return Err(blocked("peer_daily_quota", None));
    }
    let delay = match kind {
        "account" => policy.minimum_interval_ms,
        "rule" => policy.cooldown_ms,
        _ => 0,
    };
    if let Some(last) = scope.last {
        let next = last.saturating_add(i64::try_from(delay).unwrap_or(i64::MAX));
        if now < next {
            return Err(blocked(
                if kind == "account" {
                    "account_interval"
                } else {
                    "rule_cooldown"
                },
                Some(next),
            ));
        }
    }
    Ok(())
}
pub(super) fn reserve(
    c: &Connection,
    batch: &OutboundBatch,
    policy: &GuardPolicy,
    now: i64,
) -> Result<(), StoreError> {
    policy.validate()?;
    let existing = c
        .query_row(
            "SELECT policy_json FROM auto_reply_batches WHERE batch_id=?1",
            [&batch.id],
            |r| r.get::<_, String>(0),
        )
        .optional()?;
    let encoded = serde_json::to_string(policy)
        .map_err(|_| StoreError::InvalidInput("invalid guard policy"))?;
    if let Some(existing) = existing {
        if existing != encoded {
            return Err(StoreError::IdempotencyConflict {
                entity: "reply guard policy",
                key: batch.id.clone(),
            });
        }
        return Ok(());
    }
    if policy.daily_quota == 0 {
        return Err(blocked("account_daily_quota", None));
    }
    let today = policy.day_start(now)?;
    let scopes = policy.scopes();
    let mut missing = 0u32;
    for (kind, key) in &scopes {
        if let Some(scope) = read_scope(c, &batch.account_id, kind, key)? {
            check_scope(&scope, kind, batch, policy, now, today)?;
        } else {
            missing += 1;
        }
    }
    if missing > 0 {
        let count: u32 = c.query_row(
            "SELECT count(*) FROM reply_guard_scopes WHERE account_id=?1",
            [&batch.account_id],
            |r| r.get(0),
        )?;
        if count.saturating_add(missing) > MAX_SCOPES_PER_ACCOUNT {
            return Err(blocked("guard_storage_capacity", None));
        }
    }
    c.execute("INSERT INTO auto_reply_batches(batch_id,account_id,policy_json,created_at_ms) VALUES(?1,?2,?3,?4)",params![batch.id,batch.account_id,encoded,now])?;
    for (kind, key) in scopes {
        c.execute("INSERT INTO reply_guard_scopes(account_id,kind,scope_key,timezone,reserved_batch_id,updated_at_ms) VALUES(?1,?2,?3,?4,?5,?6)
            ON CONFLICT(account_id,kind,scope_key) DO UPDATE SET reserved_batch_id=excluded.reserved_batch_id,updated_at_ms=excluded.updated_at_ms",
            params![batch.account_id,kind,key,policy.timezone,batch.id,now])?;
    }
    Ok(())
}
pub(super) fn settle(c: &Connection, batch_id: &str, now: i64) -> Result<(), StoreError> {
    let policy = c
        .query_row(
            "SELECT policy_json FROM auto_reply_batches WHERE batch_id=?1 AND settled=0",
            [batch_id],
            |r| r.get::<_, String>(0),
        )
        .optional()?;
    let Some(raw) = policy else {
        return Ok(());
    };
    let batch = load_batch(c, batch_id)?;
    if !batch
        .segments
        .iter()
        .all(|s| matches!(s.status, SegmentStatus::Confirmed | SegmentStatus::Rejected))
    {
        return Ok(());
    }
    let policy: GuardPolicy =
        serde_json::from_str(&raw).map_err(|_| StoreError::InvalidInput("corrupt guard policy"))?;
    policy.validate()?;
    // Confirmed segment timestamps record delivery (HTTP acknowledgement or
    // verified inbox receipt), not when an unresolved tail was later cancelled.
    let delivery_at: Option<i64> = c.query_row(
        "SELECT MAX(updated_at_ms) FROM outbound_segments WHERE batch_id=?1 AND status='confirmed'",
        [batch_id],
        |r| r.get(0),
    )?;
    let day = policy.day_start(delivery_at.unwrap_or(now))?;
    for (kind, key) in policy.scopes() {
        let scope = read_scope(c, &batch.account_id, kind, &key)?
            .ok_or(StoreError::InvalidInput("missing reserved guard"))?;
        if scope.reserved.as_deref() != Some(batch_id) {
            return Err(StoreError::InvalidInput(
                "guard reservation ownership mismatch",
            ));
        }
        if let Some(delivery_at) = delivery_at {
            c.execute("UPDATE reply_guard_scopes SET last_delivery_at_ms=?4,day_start_ms=?5,
                used_count=CASE WHEN day_start_ms=?5 THEN used_count+1 ELSE 1 END,reserved_batch_id=NULL,updated_at_ms=?6
                WHERE account_id=?1 AND kind=?2 AND scope_key=?3",params![batch.account_id,kind,key,delivery_at,day,now])?;
        } else {
            c.execute("UPDATE reply_guard_scopes SET reserved_batch_id=NULL,updated_at_ms=?4 WHERE account_id=?1 AND kind=?2 AND scope_key=?3",params![batch.account_id,kind,key,now])?;
        }
    }
    c.execute(
        "UPDATE auto_reply_batches SET settled=1,settled_at_ms=?2 WHERE batch_id=?1",
        params![batch_id, now],
    )?;
    Ok(())
}

/// Called inside the same transaction that increments the HTTP attempt count.
pub(super) fn require_reservation(
    c: &Connection,
    batch_id: &str,
    account: &str,
) -> Result<(), StoreError> {
    let raw=c.query_row("SELECT policy_json FROM auto_reply_batches WHERE batch_id=?1 AND account_id=?2 AND settled=0",
        params![batch_id,account],|r|r.get::<_,String>(0)).optional()?.ok_or(StoreError::InvalidInput("automatic batch has no active guard reservation"))?;
    let policy: GuardPolicy = serde_json::from_str(&raw)
        .map_err(|_| StoreError::InvalidInput("invalid automatic guard metadata"))?;
    for (kind, key) in policy.scopes() {
        let scope = read_scope(c, account, kind, &key)?
            .ok_or(StoreError::InvalidInput("automatic guard scope missing"))?;
        if scope.reserved.as_deref() != Some(batch_id) {
            return Err(StoreError::InvalidInput("automatic guard reservation lost"));
        }
    }
    Ok(())
}
