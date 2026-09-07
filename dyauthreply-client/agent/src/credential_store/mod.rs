//! One-time native legacy credential migration and immutable encrypted snapshots.
//! Runtime reads only the native snapshot; neither Django nor Python is invoked.
mod fernet;
use crate::protocol::credentials::AccountCredentials;
use anyhow::{Context, Result};
use fernet::{Key, MAX_TOKEN};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::Duration,
};
const MAX_ACCOUNTS: usize = 3000;
const MAX_STORE_BYTES: usize = 64 * 1024 * 1024;
const MAX_MANIFEST_BYTES: usize = 4 * 1024 * 1024;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    version: u8,
    source_digest: String,
    accounts: Vec<AccountEntry>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AccountEntry {
    account_id: String,
    expected_sec_uid: String,
    nickname: String,
    binding_digest: String,
    payload_digest: String,
}
#[derive(Serialize)]
pub struct ImportReport {
    pub accounts: usize,
    pub skipped_without_credentials: usize,
    pub reused: bool,
    pub destination: PathBuf,
}

fn read_private_bytes(path: &Path, limit: usize) -> Result<Vec<u8>> {
    read_bytes(path, limit, true)
}
fn read_bytes(path: &Path, limit: usize, owner_only: bool) -> Result<Vec<u8>> {
    let meta = fs::symlink_metadata(path).context("credential file missing")?;
    anyhow::ensure!(
        meta.is_file() && !meta.file_type().is_symlink() && meta.len() <= u64::try_from(limit)?,
        "invalid credential file"
    );
    #[cfg(not(unix))]
    let _ = owner_only;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        anyhow::ensure!(
            !owner_only || meta.permissions().mode().trailing_zeros() >= 6,
            "credential file must be owner-only"
        );
    }
    let mut bytes = Vec::new();
    File::open(path)?
        .take(u64::try_from(limit)? + 1)
        .read_to_end(&mut bytes)?;
    anyhow::ensure!(bytes.len() <= limit, "credential file exceeds bound");
    Ok(bytes)
}
fn private_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}
fn legacy_key(root: &Path) -> Result<Key> {
    // Legacy launchers wrote .env with default umask. Migration reads without
    // modifying it; the fresh native key and every native output are owner-only.
    let env = read_bytes(&root.join(".env"), 1024 * 1024, false)?;
    let env = std::str::from_utf8(&env).context("invalid legacy environment encoding")?;
    let mut value = None;
    for line in env.lines() {
        let line = line.trim().strip_prefix("export ").unwrap_or(line.trim());
        if let Some((name, raw)) = line.split_once('=') {
            if name.trim() == "DOUYIN_STORAGE_ENCRYPTION_KEY" {
                anyhow::ensure!(value.is_none(), "duplicate legacy encryption key");
                let raw = raw.trim();
                let token = if let Some(quote) =
                    raw.chars().next().filter(|c| matches!(c, '\'' | '"'))
                {
                    let end = raw[1..]
                        .find(quote)
                        .context("invalid encryption-key quoting")?
                        + 1;
                    anyhow::ensure!(
                        raw[end + 1..].trim().is_empty() || raw[end + 1..].trim().starts_with('#'),
                        "invalid key suffix"
                    );
                    &raw[1..end]
                } else {
                    raw.split('#').next().unwrap_or_default().trim()
                };
                value = Some(Key::parse(token.as_bytes())?);
            }
        }
    }
    value.context("legacy encryption key missing")
}
fn validate_id(id: &str) -> Result<()> {
    anyhow::ensure!(
        uuid::Uuid::parse_str(id).is_ok_and(|v| v.to_string() == id),
        "invalid credential account ID"
    );
    Ok(())
}
fn read_manifest(root: &Path) -> Result<Manifest> {
    let raw = read_private_bytes(&root.join("manifest.json"), MAX_MANIFEST_BYTES)?;
    let manifest: Manifest = serde_json::from_slice(&raw).context("invalid credential manifest")?;
    anyhow::ensure!(
        manifest.version == 1 && manifest.accounts.len() <= MAX_ACCOUNTS,
        "unsupported credential store"
    );
    let mut ids = BTreeSet::new();
    for entry in &manifest.accounts {
        validate_id(&entry.account_id)?;
        anyhow::ensure!(ids.insert(&entry.account_id), "duplicate stored account");
    }
    anyhow::ensure!(
        manifest.source_digest == fingerprint(&manifest.accounts)?,
        "credential manifest fingerprint mismatch"
    );
    Ok(manifest)
}
/// Loads a bounded account subset from native encrypted storage into memory only.
/// # Errors
/// Rejects tampering, bad permissions/key, metadata mismatch or missing requested accounts.
pub fn load_accounts(root: &Path, requested: &[String]) -> Result<Vec<AccountCredentials>> {
    anyhow::ensure!(
        requested.len() <= 300,
        "runtime account admission exceeds current batch limit"
    );
    let manifest = read_manifest(root)?;
    let key = Key::parse(&read_private_bytes(&root.join("key.fernet"), 128)?)?;
    let wanted: BTreeSet<_> = requested.iter().collect();
    anyhow::ensure!(
        wanted.len() == requested.len(),
        "duplicate requested account"
    );
    let mut result = Vec::new();
    let mut total = 0usize;
    for entry in manifest
        .accounts
        .iter()
        .filter(|e| wanted.contains(&e.account_id))
    {
        let token = read_private_bytes(
            &root.join(format!("{}.fernet", entry.account_id)),
            MAX_TOKEN,
        )?;
        total = total
            .checked_add(token.len())
            .context("credential byte overflow")?;
        anyhow::ensure!(
            total <= MAX_STORE_BYTES,
            "credential snapshot exceeds budget"
        );
        let plain = key.decrypt(&token)?;
        let credentials = AccountCredentials::import_json(&plain)?;
        anyhow::ensure!(
            credentials.account_id.as_str() == entry.account_id
                && credentials.expected_sec_uid == entry.expected_sec_uid
                && credentials.binding_digest() == entry.binding_digest
                && format!("{:x}", Sha256::digest(&plain)) == entry.payload_digest,
            "encrypted account binding mismatch"
        );
        result.push(credentials);
    }
    anyhow::ensure!(
        result.len() == wanted.len(),
        "requested credential is absent from native snapshot"
    );
    Ok(result)
}
/// Reads legacy `SQLite` in a consistent read transaction, validates each encrypted
/// state, then publishes a complete native encrypted snapshot. Source is untouched.
/// Existing snapshots are immutable: identical import is idempotent, changes need
/// a new destination/version (runtime credential rotation is separate).
/// # Errors
/// Any malformed row/token, path escape or capacity failure aborts publication.
pub fn import_legacy(root: &Path, destination: &Path) -> Result<ImportReport> {
    anyhow::ensure!(
        root.is_absolute() && destination.is_absolute(),
        "credential roots must be absolute"
    );
    let root = fs::canonicalize(root)?;
    let parent = destination.parent().context("missing credential parent")?;
    fs::create_dir_all(parent)?;
    let name = destination
        .file_name()
        .and_then(|s| s.to_str())
        .context("invalid credential destination")?;
    let stage = parent.join(format!(".{name}.pending"));
    fs::create_dir(&stage)
        .context("credential import already active or incomplete; inspect pending directory")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&stage, fs::Permissions::from_mode(0o700))?;
    }
    let result = build_snapshot(&root, &stage);
    let (manifest, skipped) = match result {
        Ok(v) => v,
        Err(error) => {
            let _ = fs::remove_dir_all(&stage);
            return Err(error);
        }
    };
    let publish = (|| -> Result<bool> {
        if destination.exists() {
            let old = read_manifest(destination)?;
            anyhow::ensure!(
                old.source_digest == manifest.source_digest,
                "native snapshot differs; import to a new version before explicit reload"
            );
            for chunk in old.accounts.chunks(300) {
                load_accounts(
                    destination,
                    &chunk
                        .iter()
                        .map(|e| e.account_id.clone())
                        .collect::<Vec<_>>(),
                )?;
            }
            fs::remove_dir_all(&stage)?;
            return Ok(true);
        }
        fs::rename(&stage, destination)?;
        #[cfg(unix)]
        File::open(parent)?.sync_all()?;
        Ok(false)
    })();
    if publish.is_err() {
        let _ = fs::remove_dir_all(&stage);
    }
    Ok(ImportReport {
        accounts: manifest.accounts.len(),
        skipped_without_credentials: skipped,
        reused: publish?,
        destination: destination.into(),
    })
}
fn build_snapshot(root: &Path, stage: &Path) -> Result<(Manifest, usize)> {
    let legacy = legacy_key(root)?;
    let native = Key::generate();
    private_write(&stage.join("key.fernet"), native.encoded().as_bytes())?;
    let mut db = rusqlite::Connection::open_with_flags(
        root.join("db.sqlite3"),
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY,
    )?;
    db.busy_timeout(Duration::from_secs(3))?;
    let tx = db.transaction()?;
    let mut query=tx.prepare("SELECT substr(id,1,257),substr(COALESCE(sec_uid,''),1,257),substr(nickname,1,257),substr(COALESCE(user_agent,''),1,4097),substr(storage_state_path,1,513) FROM core_douyin_account WHERE is_deleted=0 ORDER BY id LIMIT 3001")?;
    let mut rows = query.query([])?;
    let mut manifest = Manifest {
        version: 1,
        source_digest: String::new(),
        accounts: vec![],
    };
    let mut skipped = 0;
    let mut count = 0;
    let mut total = 0usize;

    while let Some(row) = rows.next()? {
        count += 1;
        anyhow::ensure!(count <= MAX_ACCOUNTS, "legacy account limit reached");
        let id: String = row.get(0)?;
        validate_id(&id)?;
        let scope: String = row.get(1)?;
        let nickname: String = row.get(2)?;
        let ua: String = row.get(3)?;
        let path: String = row.get(4)?;
        if path.is_empty() {
            skipped += 1;
            continue;
        }
        anyhow::ensure!(
            (path == format!("storage/{id}.bin") || path == format!("storage\\{id}.bin"))
                && !scope.is_empty()
                && scope.len() <= 256
                && ua.len() <= 4096,
            "invalid legacy credential metadata"
        );
        let source = root.join("douyin/storage").join(format!("{id}.bin"));
        let resolved = fs::canonicalize(&source)?;
        anyhow::ensure!(
            resolved.starts_with(root.join("douyin/storage")),
            "legacy credential path escapes storage"
        );
        let token = read_bytes(&source, MAX_TOKEN, false)?;
        let storage: serde_json::Value = serde_json::from_slice(&legacy.decrypt(&token)?)
            .context("invalid encrypted legacy state")?;
        let envelope = serde_json::to_vec(
            &serde_json::json!({"account_id":id,"expected_sec_uid":scope,"user_agent":ua,"storage_state":storage}),
        )?;
        let credentials = AccountCredentials::import_json(&envelope)?;
        let entry = AccountEntry {
            payload_digest: format!("{:x}", Sha256::digest(&envelope)),
            account_id: id.clone(),
            expected_sec_uid: scope,
            nickname,
            binding_digest: credentials.binding_digest(),
        };
        let encrypted = native.encrypt(&envelope)?;
        total += encrypted.len();
        anyhow::ensure!(
            total <= MAX_STORE_BYTES,
            "native credential snapshot exceeds budget"
        );
        private_write(&stage.join(format!("{id}.fernet")), &encrypted)?;
        manifest.accounts.push(entry);
    }
    manifest.source_digest = fingerprint(&manifest.accounts)?;
    let bytes = serde_json::to_vec(&manifest)?;
    anyhow::ensure!(
        bytes.len() <= MAX_MANIFEST_BYTES,
        "credential manifest exceeds budget"
    );
    private_write(&stage.join("manifest.json"), &bytes)?;
    #[cfg(unix)]
    File::open(stage)?.sync_all()?;
    Ok((manifest, skipped))
}

fn fingerprint(entries: &[AccountEntry]) -> Result<String> {
    let mut hash = Sha256::new();
    for entry in entries {
        let bytes = serde_json::to_vec(entry)?;
        hash.update(u64::try_from(bytes.len())?.to_be_bytes());
        hash.update(bytes);
    }
    Ok(format!("{:x}", hash.finalize()))
}

#[cfg(test)]
mod tests;

pub mod registry;
