//! Durable coalescing of the two directions of one mutual-follow notification.
//! Claims share the outbox/guard transaction; disposable chat history is not a ledger.
use super::{params, Connection, GuardPolicy, LeaseToken, OptionalExtension, StoreError};

const PAIR_WINDOW_US: i64 = 5_000_000;
const RETAIN_MS: i64 = 10 * 60 * 1000;
const MAX_CLAIMS: u32 = 10_000;

pub(super) fn ensure_schema(c: &Connection) -> Result<(), StoreError> {
    // Additive v4 extension: older clients can still open the database after rollback.
    c.execute_batch("CREATE TABLE IF NOT EXISTS mutual_follow_claims (
        account_id TEXT NOT NULL, conversation_id TEXT NOT NULL, notice_at_us INTEGER NOT NULL,
        batch_id TEXT NOT NULL REFERENCES outbound_batches(id) ON DELETE CASCADE,
        created_at_ms INTEGER NOT NULL,
        PRIMARY KEY(account_id,conversation_id,notice_at_us));
        CREATE INDEX IF NOT EXISTS mutual_follow_claims_age ON mutual_follow_claims(created_at_ms);")?;
    Ok(())
}

fn already_claimed(
    c: &Connection,
    lease: &LeaseToken,
    policy: &GuardPolicy,
    at_us: u64,
) -> Result<bool, StoreError> {
    let at = i64::try_from(at_us)
        .ok()
        .filter(|at| *at > 0)
        .ok_or(StoreError::InvalidInput("invalid mutual-follow timestamp"))?;
    Ok(c.query_row(
        "SELECT 1 FROM mutual_follow_claims WHERE account_id=?1 AND conversation_id=?2
         AND notice_at_us BETWEEN ?3 AND ?4 LIMIT 1",
        params![
            lease.account_id,
            policy.conversation_id,
            at.saturating_sub(PAIR_WINDOW_US),
            at.saturating_add(PAIR_WINDOW_US)
        ],
        |_| Ok(()),
    )
    .optional()?
    .is_some())
}

pub(super) fn record(
    c: &Connection,
    lease: &LeaseToken,
    policy: &GuardPolicy,
    at_us: u64,
    batch: &str,
    now: i64,
) -> Result<(), StoreError> {
    c.execute(
        "DELETE FROM mutual_follow_claims WHERE created_at_ms < ?1",
        [now.saturating_sub(RETAIN_MS)],
    )?;
    let count: u32 = c.query_row("SELECT count(*) FROM mutual_follow_claims", [], |r| {
        r.get(0)
    })?;
    if count >= MAX_CLAIMS {
        return Err(StoreError::ReplyGuardBlocked {
            reason: "guard_storage_capacity",
            retry_at_ms: None,
        });
    }
    let at = i64::try_from(at_us)
        .map_err(|_| StoreError::InvalidInput("invalid mutual-follow timestamp"))?;
    c.execute("INSERT INTO mutual_follow_claims(account_id,conversation_id,notice_at_us,batch_id,created_at_ms)
        VALUES(?1,?2,?3,?4,?5)", params![lease.account_id,policy.conversation_id,at,batch,now])?;
    Ok(())
}

/// Suppress a paired notice before reserving quota or creating a second batch.
pub(super) fn coalesce<'a>(
    c: &Connection,
    lease: &LeaseToken,
    receipt: &super::InboundReceipt,
    reply: Option<super::InboundReplyPlan<'a>>,
    policy: Option<&GuardPolicy>,
    now: i64,
) -> Result<Option<super::InboundReplyPlan<'a>>, StoreError> {
    if let (Some(at), Some(policy)) = (reply.and_then(|p| p.mutual_follow_at_us), policy) {
        if already_claimed(c, lease, policy, at)? {
            super::audit_feed::decision(
                c,
                lease,
                receipt.payload.as_deref(),
                super::DecisionAudit {
                    result: "skipped",
                    reason: "互关通知已触发回复",
                    rule_id: Some(&policy.rule_id),
                },
                now,
            )?;
            return Ok(None);
        }
    }
    Ok(reply)
}
