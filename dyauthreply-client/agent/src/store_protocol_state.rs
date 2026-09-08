//! Durable, credential-bound send evidence. Restoring a record never refreshes its age.
use super::{
    params, validate_required_tables, Connection, LeaseToken, OptionalExtension, StoreError,
};
use crate::state::SendCapability;
const BROWSER_IDENTITY_V2_META_KEY: &str = "browser_identity_binding";
#[derive(Clone, Copy)]
pub struct SendObservation<'a> {
    pub canonical_sec_uid: &'a str,
    pub credential_digest: &'a str,
    pub capability: SendCapability,
}
pub(super) fn create_schema(c: &Connection) -> Result<(), StoreError> {
    c.execute_batch("CREATE TABLE account_protocol_state(account_id TEXT PRIMARY KEY NOT NULL,canonical_sec_uid TEXT NOT NULL,
        credential_digest TEXT NOT NULL CHECK(length(credential_digest)=64),capability TEXT NOT NULL CHECK(capability IN ('unknown','sendable','receive_only','risk_controlled','auth_expired')),
        observed_at_ms INTEGER NOT NULL CHECK(observed_at_ms>=0),fence_epoch INTEGER NOT NULL CHECK(fence_epoch>0));")?;
    Ok(())
}
pub(super) fn validate_schema(c: &Connection) -> Result<(), StoreError> {
    validate_required_tables(
        c,
        &[(
            "account_protocol_state",
            &[
                "account_id",
                "canonical_sec_uid",
                "credential_digest",
                "capability",
                "observed_at_ms",
                "fence_epoch",
            ],
        )],
    )
}

pub(super) fn migrate_browser_identity_v2(c: &Connection) -> Result<(), StoreError> {
    c.execute_batch(&format!(
        "BEGIN IMMEDIATE;
         INSERT OR IGNORE INTO meta(key,value) VALUES('{BROWSER_IDENTITY_V2_META_KEY}','pending');
         UPDATE account_protocol_state
         SET capability='unknown',observed_at_ms=0
         WHERE capability='risk_controlled'
           AND (SELECT value FROM meta WHERE key='{BROWSER_IDENTITY_V2_META_KEY}')='pending';
         UPDATE meta SET value='2'
         WHERE key='{BROWSER_IDENTITY_V2_META_KEY}' AND value='pending';
         COMMIT;"
    ))?;
    Ok(())
}
fn validate(canonical: &str, digest: &str) -> Result<(), StoreError> {
    if canonical.is_empty()
        || canonical.len() > 256
        || digest.len() != 64
        || !digest.bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err(StoreError::InvalidInput(
            "invalid protocol observation binding",
        ));
    }
    Ok(())
}
fn label(cap: SendCapability) -> &'static str {
    match cap {
        SendCapability::Unknown => "unknown",
        SendCapability::Sendable => "sendable",
        SendCapability::ReceiveOnly => "receive_only",
        SendCapability::RiskControlled => "risk_controlled",
        SendCapability::AuthExpired => "auth_expired",
    }
}
pub(super) fn restore(
    c: &Connection,
    lease: &LeaseToken,
    canonical: &str,
    digest: &str,
    now: i64,
) -> Result<SendCapability, StoreError> {
    validate(canonical, digest)?;
    let row=c.query_row("SELECT canonical_sec_uid,credential_digest,capability,observed_at_ms FROM account_protocol_state WHERE account_id=?1",[&lease.account_id],|r|Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?,r.get::<_,String>(2)?,r.get::<_,i64>(3)?))).optional()?;
    if let Some((saved_canonical, saved_digest, cap, at)) = row {
        if saved_canonical == canonical {
            if saved_digest != digest {
                c.execute(
                    "UPDATE account_protocol_state SET credential_digest=?2,capability='unknown',observed_at_ms=0,fence_epoch=?3 WHERE account_id=?1 AND canonical_sec_uid=?4",
                    params![lease.account_id,digest,lease.fence_epoch,canonical],
                )?;
                return Ok(SendCapability::Unknown);
            }
            return Ok(match cap.as_str() {
                "risk_controlled" => SendCapability::RiskControlled,
                "receive_only" => SendCapability::ReceiveOnly,
                "auth_expired" => SendCapability::AuthExpired,
                // This is historical delivery evidence for the same credentials,
                // not a five-minute lease. Current verified identity and hosted
                // ownership independently gate all sends. A restart must not
                // disable automation only because the account was idle.
                "sendable" if at > 0 && at <= now => SendCapability::Sendable,
                _ => SendCapability::Unknown,
            });
        }
    } else {
        let count: u32 = c.query_row("SELECT count(*) FROM account_protocol_state", [], |r| {
            r.get(0)
        })?;
        if count >= 3000 {
            return Err(StoreError::InvalidInput("protocol state capacity reached"));
        }
    }
    c.execute("INSERT INTO account_protocol_state VALUES(?1,?2,?3,'unknown',0,?4) ON CONFLICT(account_id) DO UPDATE SET canonical_sec_uid=excluded.canonical_sec_uid,credential_digest=excluded.credential_digest,capability='unknown',observed_at_ms=0,fence_epoch=excluded.fence_epoch",
        params![lease.account_id,canonical,digest,lease.fence_epoch])?;
    Ok(SendCapability::Unknown)
}
pub(super) fn record(
    c: &Connection,
    lease: &LeaseToken,
    observation: SendObservation<'_>,
    now: i64,
) -> Result<(), StoreError> {
    validate(observation.canonical_sec_uid, observation.credential_digest)?;
    // Admission happens after verified self identity and before starting any HTTP send.
    let updated=c.execute("UPDATE account_protocol_state SET credential_digest=?3,capability=?4,observed_at_ms=?5,fence_epoch=?6 WHERE account_id=?1 AND canonical_sec_uid=?2",
        params![lease.account_id,observation.canonical_sec_uid,observation.credential_digest,label(observation.capability),now,lease.fence_epoch])?;
    if updated != 1 {
        return Err(StoreError::InvalidInput(
            "verified protocol scope must be initialized before sending",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::{CoreStore, OutboundSegmentDraft, SegmentTransition};
    fn fixture() -> (tempfile::TempDir, CoreStore, LeaseToken) {
        let dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(dir.path()).unwrap();
        let now = super::super::current_time_ms().unwrap();
        let lease = store
            .install_verified_account_lease("account", "instance", "boot", 1, now + 60_000)
            .unwrap()
            .token();
        (dir, store, lease)
    }
    #[test]
    fn credential_rotation_clears_stale_risk_but_same_binding_retains_it() {
        let (_dir, store, lease) = fixture();
        let c = store.lock_connection().unwrap();
        let a = "a".repeat(64);
        let b = "b".repeat(64);
        assert_eq!(
            restore(&c, &lease, "self", &a, 1000).unwrap(),
            SendCapability::Unknown
        );
        record(
            &c,
            &lease,
            SendObservation {
                canonical_sec_uid: "self",
                credential_digest: &a,
                capability: SendCapability::RiskControlled,
            },
            1000,
        )
        .unwrap();
        assert_eq!(
            restore(&c, &lease, "self", &a, 1500).unwrap(),
            SendCapability::RiskControlled
        );
        assert_eq!(
            restore(&c, &lease, "self", &b, 2000).unwrap(),
            SendCapability::Unknown
        );
        let rebound: (String, String, i64) = c
            .query_row(
                "SELECT credential_digest,capability,observed_at_ms FROM account_protocol_state",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .unwrap();
        assert_eq!(rebound, (b.clone(), "unknown".into(), 0));
        record(
            &c,
            &lease,
            SendObservation {
                canonical_sec_uid: "self",
                credential_digest: &b,
                capability: SendCapability::Sendable,
            },
            2000,
        )
        .unwrap();
        assert_eq!(
            restore(&c, &lease, "self", &b, 2001).unwrap(),
            SendCapability::Sendable
        );
        assert_eq!(
            restore(&c, &lease, "self", &a, 2001).unwrap(),
            SendCapability::Unknown
        );
        assert_eq!(
            restore(&c, &lease, "self", &a, 400_000).unwrap(),
            SendCapability::Unknown
        );
        assert_eq!(
            restore(&c, &lease, "different-account", &a, 400_000).unwrap(),
            SendCapability::Unknown
        );
    }

    #[test]
    fn same_credential_send_evidence_survives_idle_restart_without_refreshing_age() {
        let (_dir, store, lease) = fixture();
        let c = store.lock_connection().unwrap();
        let digest = "a".repeat(64);
        restore(&c, &lease, "self", &digest, 1000).unwrap();
        let observation = SendObservation {
            canonical_sec_uid: "self",
            credential_digest: &digest,
            capability: SendCapability::Sendable,
        };
        record(&c, &lease, observation, 1000).unwrap();
        assert_eq!(
            restore(&c, &lease, "self", &digest, 3_600_000).unwrap(),
            SendCapability::Sendable
        );
        let at: i64 = c
            .query_row(
                "SELECT observed_at_ms FROM account_protocol_state",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(at, 1000);
        record(
            &c,
            &lease,
            SendObservation {
                capability: SendCapability::RiskControlled,
                ..observation
            },
            3_600_001,
        )
        .unwrap();
        assert_eq!(
            restore(&c, &lease, "self", &digest, 7_200_000).unwrap(),
            SendCapability::RiskControlled
        );
        assert_eq!(
            restore(&c, &lease, "self", &"b".repeat(64), 7_200_000).unwrap(),
            SendCapability::Unknown
        );
    }

    #[test]
    fn browser_identity_v2_migration_clears_legacy_risk_exactly_once() {
        let (_dir, store, lease) = fixture();
        let digest = "a".repeat(64);
        let c = store.lock_connection().unwrap();
        c.execute(
            "DELETE FROM meta WHERE key=?1",
            [BROWSER_IDENTITY_V2_META_KEY],
        )
        .unwrap();
        restore(&c, &lease, "self", &digest, 1000).unwrap();
        record(
            &c,
            &lease,
            SendObservation {
                canonical_sec_uid: "self",
                credential_digest: &digest,
                capability: SendCapability::RiskControlled,
            },
            1000,
        )
        .unwrap();
        migrate_browser_identity_v2(&c).unwrap();
        assert_eq!(
            c.query_row(
                "SELECT capability,observed_at_ms FROM account_protocol_state",
                [],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
            )
            .unwrap(),
            ("unknown".into(), 0)
        );
        record(
            &c,
            &lease,
            SendObservation {
                canonical_sec_uid: "self",
                credential_digest: &digest,
                capability: SendCapability::RiskControlled,
            },
            2000,
        )
        .unwrap();
        migrate_browser_identity_v2(&c).unwrap();
        assert_eq!(
            c.query_row(
                "SELECT capability,observed_at_ms FROM account_protocol_state",
                [],
                |row| Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
            )
            .unwrap(),
            ("risk_controlled".into(), 2000)
        );
    }
    #[test]
    fn expired_auth_is_not_transferred_to_a_new_credential_bundle() {
        let (_dir, store, lease) = fixture();
        let c = store.lock_connection().unwrap();
        let a = "a".repeat(64);
        let b = "b".repeat(64);
        restore(&c, &lease, "self", &a, 1000).unwrap();
        record(
            &c,
            &lease,
            SendObservation {
                canonical_sec_uid: "self",
                credential_digest: &a,
                capability: SendCapability::AuthExpired,
            },
            1000,
        )
        .unwrap();
        assert_eq!(
            restore(&c, &lease, "self", &a, 2000).unwrap(),
            SendCapability::AuthExpired
        );
        assert_eq!(
            restore(&c, &lease, "self", &b, 2000).unwrap(),
            SendCapability::Unknown
        );
    }
    #[test]
    fn outcome_and_observation_commit_atomically_and_replay_does_not_refresh_age() {
        let (_dir, store, lease) = fixture();
        let digest = "a".repeat(64);
        store
            .restore_send_observation(&lease, "self", &digest)
            .unwrap();
        let batch = store
            .prepare_outbound_batch(
                &lease,
                "trigger",
                "route",
                &[OutboundSegmentDraft::text("hello")],
            )
            .unwrap();
        let id = &batch.segments[0].id;
        store
            .transition_segment(&lease, id, SegmentTransition::StartAttempt)
            .unwrap();
        store.lock_connection().unwrap().execute_batch("CREATE TEMP TRIGGER fail_observation BEFORE UPDATE ON account_protocol_state BEGIN SELECT RAISE(ABORT,'fixture observation failure'); END;").unwrap();
        let observation = SendObservation {
            canonical_sec_uid: "self",
            credential_digest: &digest,
            capability: SendCapability::Sendable,
        };
        assert!(store
            .transition_segment_observed(
                &lease,
                id,
                SegmentTransition::Confirm {
                    platform_message_id: "123".into()
                },
                observation
            )
            .is_err());
        assert_eq!(
            store.outbound_batch(&batch.id).unwrap().segments[0].status,
            crate::store::SegmentStatus::Sending
        );
        store
            .lock_connection()
            .unwrap()
            .execute_batch("DROP TRIGGER fail_observation")
            .unwrap();
        store
            .transition_segment_observed(
                &lease,
                id,
                SegmentTransition::Confirm {
                    platform_message_id: "123".into(),
                },
                observation,
            )
            .unwrap();
        let at: i64 = store
            .lock_connection()
            .unwrap()
            .query_row(
                "SELECT observed_at_ms FROM account_protocol_state",
                [],
                |r| r.get(0),
            )
            .unwrap();
        store
            .transition_segment_observed(
                &lease,
                id,
                SegmentTransition::Confirm {
                    platform_message_id: "123".into(),
                },
                SendObservation {
                    capability: SendCapability::RiskControlled,
                    ..observation
                },
            )
            .unwrap();
        let row: (String, i64) = store
            .lock_connection()
            .unwrap()
            .query_row(
                "SELECT capability,observed_at_ms FROM account_protocol_state",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(row, ("sendable".into(), at));
    }
}

#[cfg(test)]
mod migration_tests {
    use super::super::{
        guards, load_migration_backup, migrate_v3_to_v4, read_schema_version, CORE_SCHEMA_VERSION,
        DATABASE_ID_META_KEY, SCHEMA_V1_SQL, SCHEMA_V2_SQL, SCHEMA_V3,
    };
    use super::*;
    #[test]
    fn failed_state_migration_retains_v3_and_retry_has_verified_backup() {
        let dir = tempfile::tempdir().unwrap();
        let mut c = Connection::open(dir.path().join("core.sqlite3")).unwrap();
        c.execute_batch("CREATE TABLE meta(key TEXT PRIMARY KEY NOT NULL,value TEXT NOT NULL);")
            .unwrap();
        c.execute_batch(SCHEMA_V1_SQL).unwrap();
        c.execute_batch(SCHEMA_V2_SQL).unwrap();
        guards::create_schema(&c).unwrap();
        c.execute("INSERT INTO meta VALUES('schema_version','3')", [])
            .unwrap();
        c.execute(
            "INSERT INTO meta VALUES(?1,?2)",
            params![DATABASE_ID_META_KEY, uuid::Uuid::new_v4().to_string()],
        )
        .unwrap();
        c.pragma_update(None, "user_version", SCHEMA_V3).unwrap();
        c.execute_batch("CREATE TEMP TRIGGER fail_upgrade BEFORE UPDATE OF value ON meta WHEN OLD.key='schema_version' BEGIN SELECT RAISE(ABORT,'fixture upgrade failure'); END;").unwrap();
        assert!(migrate_v3_to_v4(&mut c, dir.path()).is_err());
        assert_eq!(read_schema_version(&c).unwrap(), SCHEMA_V3);
        assert!(!super::super::table_exists(&c, "account_protocol_state").unwrap());
        c.execute_batch("DROP TRIGGER fail_upgrade").unwrap();
        migrate_v3_to_v4(&mut c, dir.path()).unwrap();
        assert_eq!(read_schema_version(&c).unwrap(), CORE_SCHEMA_VERSION);
        let backup = load_migration_backup(&c, SCHEMA_V3, CORE_SCHEMA_VERSION)
            .unwrap()
            .unwrap();
        let b = Connection::open(dir.path().join(backup.relative_path)).unwrap();
        assert_eq!(read_schema_version(&b).unwrap(), SCHEMA_V3);
    }
}
