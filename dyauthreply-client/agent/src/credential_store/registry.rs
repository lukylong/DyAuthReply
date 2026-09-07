//! Mutable encrypted native credential registry. One ciphertext/current generation per stable account.
use super::{private_write, read_manifest, read_private_bytes, Key, MAX_TOKEN};
use crate::protocol::credentials::{AccountCredentials, CredentialImport};
use anyhow::{Context, Result};
use rusqlite::{params, Connection, OptionalExtension};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::{collections::BTreeSet, path::Path, sync::Mutex};
#[derive(Clone, Serialize)]
pub struct AccountRecord {
    pub id: String,
    pub sec_uid: String,
    pub generation: u64,
    pub nickname: String,
    pub deleted: bool,
    pub verified_at_ms: i64,
}
pub struct Registry {
    db: Mutex<Connection>,
    key: Key,
}
impl Registry {
    /// # Errors
    /// Never replaces a missing key for an existing registry, or silently opens unsupported schemas.
    pub fn open(root: &Path) -> Result<Self> {
        let path = root.join("accounts.sqlite3");
        let key_path = root.join("accounts.key");
        if !key_path.exists() {
            anyhow::ensure!(!path.exists(), "账号密钥缺失，保留原数据等待恢复");
            private_write(&key_path, Key::generate().encoded().as_bytes())?;
        }
        let key = Key::parse(&read_private_bytes(&key_path, 128)?)?;
        let db = Connection::open(&path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
        }
        db.busy_timeout(std::time::Duration::from_secs(2))?;
        let version: i64 = db.pragma_query_value(None, "user_version", |r| r.get(0))?;
        anyhow::ensure!(version <= 1, "账号存储版本不匹配");
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA journal_size_limit=4194304; PRAGMA max_page_count=32768;
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,sec_uid TEXT NOT NULL UNIQUE,generation INTEGER NOT NULL,digest TEXT NOT NULL,session_hash TEXT NOT NULL,cipher BLOB NOT NULL,nickname TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0,verified_at INTEGER NOT NULL,policy_json TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS active_session_owner ON accounts(session_hash) WHERE deleted=0;
PRAGMA user_version=1;")?;
        validate_schema(&db)?;
        Ok(Self {
            db: Mutex::new(db),
            key,
        })
    }
    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Connection>> {
        self.db
            .lock()
            .map_err(|_| anyhow::anyhow!("账号存储锁异常"))
    }
    /// # Errors
    /// One-time native snapshot adoption; deleted accounts never reappear on subsequent startups.
    pub fn seed(&self, snapshot: Option<&Path>, files: &[std::path::PathBuf]) -> Result<()> {
        if self.lock()?.query_row(
            "SELECT EXISTS(SELECT 1 FROM metadata WHERE key='seeded')",
            [],
            |r| r.get::<_, bool>(0),
        )? {
            return Ok(());
        }
        if let Some(root) = snapshot {
            let manifest = read_manifest(root)?;
            let key = Key::parse(&read_private_bytes(&root.join("key.fernet"), 128)?)?;
            for item in manifest.accounts {
                let plain = key.decrypt(&read_private_bytes(
                    &root.join(format!("{}.fernet", item.account_id)),
                    MAX_TOKEN,
                )?)?;
                anyhow::ensure!(
                    format!("{:x}", Sha256::digest(&plain)) == item.payload_digest,
                    "凭证快照摘要不匹配"
                );
                let input: CredentialImport = serde_json::from_slice(&plain)?;
                let credentials = AccountCredentials::import_json(&plain)?;
                anyhow::ensure!(
                    credentials.expected_sec_uid == item.expected_sec_uid
                        && credentials.binding_digest() == item.binding_digest,
                    "凭证快照身份不匹配"
                );
                if self.find_scope(&item.expected_sec_uid)?.is_none() {
                    self.put(&input, &item.nickname, 0, None)?;
                }
            }
        } else {
            for path in files {
                let input: CredentialImport = crate::runtime::messaging::read_private(path)?;
                if self.find_scope(&input.expected_sec_uid)?.is_none() {
                    self.put(&input, "抖音账号", 0, None)?;
                }
            }
        }
        self.lock()?
            .execute("INSERT OR IGNORE INTO metadata VALUES('seeded','1')", [])?;
        Ok(())
    }
    /// # Errors
    /// Metadata only; encrypted payloads are never part of the public account list.
    pub fn list(&self) -> Result<Vec<AccountRecord>> {
        let db = self.lock()?;
        let mut q=db.prepare("SELECT id,sec_uid,generation,nickname,deleted,verified_at FROM accounts ORDER BY id LIMIT 3001")?;
        let rows = q
            .query_map([], record)?
            .collect::<rusqlite::Result<Vec<_>>>()?;
        anyhow::ensure!(rows.len() <= 3000, "账号存储数量超限");
        Ok(rows)
    }
    /// # Errors
    /// Includes tombstones so reimport preserves account identity and deduplication state.
    pub fn find_scope(&self, scope: &str) -> Result<Option<AccountRecord>> {
        Ok(self.lock()?.query_row("SELECT id,sec_uid,generation,nickname,deleted,verified_at FROM accounts WHERE sec_uid=?1",[scope],record).optional()?)
    }
    /// # Errors
    /// Validates decrypted envelope identity/generation/digest before returning in-memory material.
    pub fn load(&self, id: &str) -> Result<CredentialImport> {
        let (scope, digest, cipher): (String, String, Vec<u8>) = self.lock()?.query_row(
            "SELECT sec_uid,digest,cipher FROM accounts WHERE id=?1 AND deleted=0",
            [id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        let plain = self.key.decrypt(&cipher)?;
        anyhow::ensure!(
            format!("{:x}", Sha256::digest(&plain)) == digest,
            "账号凭证摘要不匹配"
        );
        let input: CredentialImport = serde_json::from_slice(&plain)?;
        anyhow::ensure!(
            input.account_id == id && input.expected_sec_uid == scope,
            "账号凭证身份不匹配"
        );
        AccountCredentials::import_json(&plain)?;
        Ok(input)
    }
    /// # Errors
    /// Stable-ID upsert; identical data preserves generation. Cross-account session reuse is rejected.
    pub fn put(
        &self,
        input: &CredentialImport,
        nickname: &str,
        verified: i64,
        policy: Option<&crate::runtime::messaging::AutomationPolicy>,
    ) -> Result<(AccountRecord, bool)> {
        uuid::Uuid::parse_str(&input.account_id)?;
        anyhow::ensure!(
            !input.expected_sec_uid.is_empty() && nickname.len() <= 600,
            "账号身份字段无效"
        );
        let plain = encode(input)?;
        let credentials = AccountCredentials::import_json(&plain)?;
        let session_hash = format!(
            "{:x}",
            Sha256::digest(credentials.cookie("www.douyin.com", "sessionid").as_bytes())
        );
        let digest = format!("{:x}", Sha256::digest(&plain));
        let mut db = self.lock()?;
        let tx = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let duplicate: Option<String> = tx
            .query_row(
                "SELECT id FROM accounts WHERE deleted=0 AND session_hash=?1 AND id!=?2",
                params![session_hash, input.account_id],
                |r| r.get(0),
            )
            .optional()?;
        anyhow::ensure!(duplicate.is_none(), "该登录会话已被另一个账号托管");
        let old: Option<(String, u64, String, bool)> = tx
            .query_row(
                "SELECT sec_uid,generation,digest,deleted FROM accounts WHERE id=?1",
                [&input.account_id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .optional()?;
        let changed = old
            .as_ref()
            .is_none_or(|(_, _, old, deleted)| old != &digest || *deleted);
        if let Some((scope, ..)) = &old {
            anyhow::ensure!(
                scope == &input.expected_sec_uid,
                "凭证所属账号与目标账号不一致"
            );
        }
        let generation = old.as_ref().map_or(Ok(1), |(_, g, _, _)| {
            if changed {
                g.checked_add(1).context("凭证版本已达上限")
            } else {
                Ok(*g)
            }
        })?;
        if !changed {
            tx.execute(
                "UPDATE accounts SET nickname=?1,verified_at=?2 WHERE id=?3",
                params![nickname, verified, input.account_id],
            )?;
            tx.commit()?;
            return Ok((
                AccountRecord {
                    id: input.account_id.clone(),
                    sec_uid: input.expected_sec_uid.clone(),
                    generation,
                    nickname: nickname.into(),
                    deleted: false,
                    verified_at_ms: verified,
                },
                false,
            ));
        }
        if old.is_none() {
            let count: i64 = tx.query_row("SELECT count(*) FROM accounts", [], |r| r.get(0))?;
            anyhow::ensure!(count < 3000, "账号数量已达上限");
        }
        let cipher = self.key.encrypt(&plain)?;
        let total: i64 = tx.query_row(
            "SELECT coalesce(sum(length(cipher)),0) FROM accounts WHERE id!=?1",
            [&input.account_id],
            |r| r.get(0),
        )?;
        anyhow::ensure!(
            total + i64::try_from(cipher.len())? <= 64 * 1024 * 1024,
            "凭证存储容量已达上限"
        );
        tx.execute("INSERT INTO accounts VALUES(?1,?2,?3,?4,?5,?6,?7,0,?8,?9) ON CONFLICT(id) DO UPDATE SET generation=excluded.generation,digest=excluded.digest,session_hash=excluded.session_hash,cipher=excluded.cipher,nickname=excluded.nickname,deleted=0,verified_at=excluded.verified_at,policy_json=coalesce(excluded.policy_json,accounts.policy_json)",params![input.account_id,input.expected_sec_uid,generation,digest,session_hash,cipher,nickname,verified,policy.map(serde_json::to_string).transpose()?])?;
        tx.commit()?;
        Ok((
            AccountRecord {
                id: input.account_id.clone(),
                sec_uid: input.expected_sec_uid.clone(),
                generation,
                nickname: nickname.into(),
                deleted: false,
                verified_at_ms: verified,
            },
            changed,
        ))
    }
    /// # Errors
    /// Returns the persisted user policy for crash recovery before runtime reconciliation.
    pub fn policy(&self, id: &str) -> Result<Option<crate::runtime::messaging::AutomationPolicy>> {
        let raw: Option<String> =
            self.lock()?
                .query_row("SELECT policy_json FROM accounts WHERE id=?1", [id], |r| {
                    r.get(0)
                })?;
        raw.map(|r| serde_json::from_str(&r).map_err(Into::into))
            .transpose()
    }
    /// # Errors
    /// Removes current encrypted material but retains a stable-ID tombstone; protocol history is untouched.
    pub fn delete(&self, id: &str) -> Result<()> {
        let mut db = self.lock()?;
        let tx = db.transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)?;
        let generation: Option<u64> = tx
            .query_row(
                "SELECT generation FROM accounts WHERE id=?1 AND deleted=0",
                [id],
                |row| row.get(0),
            )
            .optional()?;
        let next = generation
            .context("账号不存在")?
            .checked_add(1)
            .context("凭证版本已达上限")?;
        anyhow::ensure!(
            tx.execute(
                "UPDATE accounts SET deleted=1,cipher=x'',generation=?1 WHERE id=?2 AND deleted=0",
                params![next, id],
            )? == 1,
            "账号状态已变更"
        );
        tx.commit()?;
        Ok(())
    }
}
fn validate_schema(db: &Connection) -> Result<()> {
    let mut statement = db.prepare("PRAGMA table_info(accounts)")?;
    let actual = statement
        .query_map([], |row| row.get::<_, String>(1))?
        .collect::<rusqlite::Result<BTreeSet<_>>>()?;
    let expected = [
        "id",
        "sec_uid",
        "generation",
        "digest",
        "session_hash",
        "cipher",
        "nickname",
        "deleted",
        "verified_at",
        "policy_json",
    ]
    .into_iter()
    .map(str::to_owned)
    .collect::<BTreeSet<_>>();
    anyhow::ensure!(actual == expected, "账号存储结构不匹配");
    Ok(())
}
fn record(r: &rusqlite::Row<'_>) -> rusqlite::Result<AccountRecord> {
    Ok(AccountRecord {
        id: r.get(0)?,
        sec_uid: r.get(1)?,
        generation: r.get(2)?,
        nickname: r.get(3)?,
        deleted: r.get(4)?,
        verified_at_ms: r.get(5)?,
    })
}
pub(crate) fn encode(input: &CredentialImport) -> Result<Vec<u8>> {
    let bytes = serde_json::to_vec(
        &serde_json::json!({"account_id":input.account_id,"expected_sec_uid":input.expected_sec_uid,"user_agent":input.user_agent,"storage_state":input.storage_state}),
    )?;
    anyhow::ensure!(bytes.len() <= 1024 * 1024, "凭证数据过大");
    Ok(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input(id: &str, scope: &str, session: &str) -> CredentialImport {
        CredentialImport {
            account_id: id.into(),
            expected_sec_uid: scope.into(),
            user_agent: "Chrome/151.0".into(),
            storage_state: serde_json::json!({"cookies":[{"name":"sessionid","value":session}],"_bd_ticket":{}}),
        }
    }
    #[test]
    fn encrypted_generation_and_tombstone_keep_stable_identity() {
        let root = tempfile::tempdir().unwrap();
        let registry = Registry::open(root.path()).unwrap();
        let id = uuid::Uuid::new_v4().to_string();
        let mut value = input(&id, "scope", "session-one");
        let (first, changed) = registry.put(&value, "昵称", 1, None).unwrap();
        assert!(changed);
        assert_eq!(first.generation, 1);
        assert!(!registry.put(&value, "昵称", 2, None).unwrap().1);
        assert_eq!(registry.load(&id).unwrap().expected_sec_uid, "scope");
        value.storage_state["cookies"][0]["value"] = serde_json::json!("session-two");
        assert_eq!(
            registry.put(&value, "昵称", 3, None).unwrap().0.generation,
            2
        );
        registry.delete(&id).unwrap();
        assert!(registry.load(&id).is_err());
        assert!(registry.find_scope("scope").unwrap().unwrap().deleted);
        assert_eq!(
            registry.put(&value, "昵称", 4, None).unwrap().0.generation,
            4
        );
        let bytes = std::fs::read(root.path().join("accounts.sqlite3")).unwrap();
        assert!(!bytes
            .windows("session-two".len())
            .any(|w| w == b"session-two"));
    }
    #[test]
    fn duplicate_session_and_wrong_scope_are_rejected_without_replacing_data() {
        let root = tempfile::tempdir().unwrap();
        let registry = Registry::open(root.path()).unwrap();
        let id = uuid::Uuid::new_v4().to_string();
        registry
            .put(&input(&id, "one", "session"), "one", 1, None)
            .unwrap();
        assert!(registry
            .put(
                &input(&uuid::Uuid::new_v4().to_string(), "two", "session"),
                "two",
                1,
                None
            )
            .is_err());
        assert!(registry
            .put(&input(&id, "two", "new"), "two", 1, None)
            .is_err());
        assert_eq!(registry.list().unwrap().len(), 1);
    }
    #[test]
    fn missing_key_for_existing_registry_does_not_initialize_empty_replacement() {
        let root = tempfile::tempdir().unwrap();
        drop(Registry::open(root.path()).unwrap());
        std::fs::remove_file(root.path().join("accounts.key")).unwrap();
        assert!(Registry::open(root.path()).is_err());
    }
    #[test]
    fn malformed_v1_schema_is_rejected_instead_of_opened_partially() {
        let root = tempfile::tempdir().unwrap();
        let registry = Registry::open(root.path()).unwrap();
        registry
            .lock()
            .unwrap()
            .execute_batch(
                "DROP TABLE accounts; CREATE TABLE accounts(id TEXT PRIMARY KEY); PRAGMA user_version=1;",
            )
            .unwrap();
        drop(registry);
        assert!(Registry::open(root.path()).is_err());
    }
}

#[cfg(test)]
mod retry_tests {
    use super::*;
    #[test]
    fn identical_put_keeps_ciphertext_and_metadata_changes_do_not_advance_generation() {
        let root = tempfile::tempdir().unwrap();
        let store = Registry::open(root.path()).unwrap();
        let id = uuid::Uuid::new_v4().to_string();
        let input = CredentialImport {
            account_id: id.clone(),
            expected_sec_uid: "self".into(),
            user_agent: "Chrome/151.0".into(),
            storage_state: serde_json::json!({"cookies":[{"name":"sessionid","value":"synthetic-only"}]}),
        };
        store.put(&input, "first", 1, None).unwrap();
        let before: Vec<u8> = store
            .lock()
            .unwrap()
            .query_row("SELECT cipher FROM accounts", [], |r| r.get(0))
            .unwrap();
        let (record, changed) = store.put(&input, "updated name", 2, None).unwrap();
        assert!(!changed);
        assert_eq!(record.generation, 1);
        let after: Vec<u8> = store
            .lock()
            .unwrap()
            .query_row("SELECT cipher FROM accounts", [], |r| r.get(0))
            .unwrap();
        assert_eq!(before, after);
        drop(store);
        let reopened = Registry::open(root.path()).unwrap();
        assert_eq!(reopened.load(&id).unwrap().expected_sec_uid, "self");
    }
}
