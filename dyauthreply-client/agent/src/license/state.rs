//! Signed entitlement verification and bounded owner-only local publication.
use super::{fs, iso, json, now, string, Context, File, LicenseConfig, Path, Result, Value};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use ed25519_dalek::{pkcs8::DecodePublicKey, Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use std::io::{Read, Write};
#[derive(Deserialize, Serialize, Clone)]
pub(super) struct Claims {
    pub iss: String,
    pub sub: String,
    pub activation_id: String,
    pub license_key_id: String,
    pub device_fingerprint: String,
    pub lease_sequence: u64,
    pub iat: i64,
    pub exp: i64,
    pub grace_until: Option<i64>,
    pub feature_flags: Value,
}
pub(super) fn verify(pem: &str, state: &Value) -> Result<Claims> {
    let token = string(state, "lease_token");
    anyhow::ensure!(token.len() <= 64 * 1024, "invalid license token size");
    let parts: Vec<_> = token.split('.').collect();
    anyhow::ensure!(
        parts.len() == 3 && parts[0].len() < 2048,
        "invalid license token"
    );
    let header: Value = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(parts[0])?)?;
    anyhow::ensure!(
        header["alg"] == "EdDSA" && header["typ"] == "JWT",
        "invalid license signature algorithm"
    );
    let key = VerifyingKey::from_public_key_pem(pem)?;
    anyhow::ensure!(!key.is_weak(), "invalid license trust key");
    let signature = Signature::from_slice(&URL_SAFE_NO_PAD.decode(parts[2])?)?;
    key.verify_strict(format!("{}.{}", parts[0], parts[1]).as_bytes(), &signature)
        .context("license signature mismatch")?;
    let c: Claims = serde_json::from_slice(&URL_SAFE_NO_PAD.decode(parts[1])?)?;
    anyhow::ensure!(
        c.iss == "dyauthreply-license"
            && c.sub == c.activation_id
            && c.activation_id == string(state, "activation_id")
            && c.license_key_id == string(state, "license_key_id")
            && c.device_fingerprint == string(state, "device_fingerprint")
            && c.lease_sequence == state["lease_sequence"].as_u64().unwrap_or(0),
        "license binding mismatch"
    );
    anyhow::ensure!(
        c.iat <= now()? + 60 && c.exp > c.iat && c.exp - c.iat <= 86400,
        "invalid license time bounds"
    );
    Ok(c)
}
pub(super) fn operation_lock(path: &Path) -> Result<File> {
    let lock = path.with_extension("lock");
    fs::create_dir_all(lock.parent().context("license root missing")?)?;
    anyhow::ensure!(!lock.is_symlink(), "invalid license lock path");
    let mut options = std::fs::OpenOptions::new();
    options.create(true).read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options.open(lock)?;
    fs2::FileExt::try_lock_exclusive(&file).context("授权操作正在进行，请稍后重试")?;
    Ok(file)
}
pub(super) fn load(path: &Path) -> Result<Value> {
    let link = match fs::symlink_metadata(path) {
        Ok(meta) => meta,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(json!({})),
        Err(e) => return Err(e.into()),
    };
    anyhow::ensure!(
        link.file_type().is_file() && !link.file_type().is_symlink(),
        "invalid license state path"
    );
    let file = File::open(path)?;
    let mut meta = file.metadata()?;
    anyhow::ensure!(
        meta.is_file() && meta.len() <= 256 * 1024,
        "invalid license state file"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if meta.permissions().mode() & 0o077 != 0 {
            let mut permissions = meta.permissions();
            permissions.set_mode(0o600);
            file.set_permissions(permissions)?;
            meta = file.metadata()?;
        }
        anyhow::ensure!(
            meta.permissions().mode().trailing_zeros() >= 6,
            "license state must be owner-only"
        );
    }
    let mut raw = Vec::new();
    file.take(256 * 1024 + 1).read_to_end(&mut raw)?;
    anyhow::ensure!(raw.len() <= 256 * 1024, "license state exceeds bound");
    serde_json::from_slice(&raw).context("invalid license state")
}
pub(super) fn save(path: &Path, value: &Value) -> Result<()> {
    let parent = path.parent().context("license state parent missing")?;
    fs::create_dir_all(parent)?;
    let temp = path.with_extension("native-new");
    let bytes = serde_json::to_vec(value)?;
    anyhow::ensure!(bytes.len() <= 256 * 1024, "license state exceeds bound");
    let mut options = std::fs::OpenOptions::new();
    options.create(true).write(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    anyhow::ensure!(
        !temp.is_symlink() && !path.is_symlink(),
        "invalid license state path"
    );
    let mut file = options.open(&temp)?;
    file.write_all(&bytes)?;
    file.sync_all()?;
    drop(file);
    fs::rename(&temp, path)?;
    #[cfg(unix)]
    File::open(parent)?.sync_all()?;
    Ok(())
}
pub(super) fn public(config: &LicenseConfig, state: &Value) -> Value {
    let now = now().unwrap_or(i64::MAX);
    let (status, error, claims) = if string(state, "activation_id").is_empty() {
        ("unactivated", "", None)
    } else if matches!(string(state, "local_state"), "revoked" | "invalid") {
        (
            string(state, "local_state"),
            string(state, "last_error"),
            None,
        )
    } else {
        match verify(&config.public_key_pem, state) {
            Ok(c) => {
                let status = if now <= c.exp {
                    "active"
                } else if c.grace_until.is_some_and(|t| now <= t) {
                    "grace"
                } else {
                    "expired"
                };
                (status, string(state, "last_error"), Some(c))
            }
            Err(_) => ("invalid", "授权签名或设备绑定校验失败", None),
        }
    };
    let mut out = json!({"state":status,"state_label":match status{"active"=>"已激活","grace"=>"离线宽限期","expired"=>"已过期","revoked"=>"已撤销","invalid"=>"授权无效",_=>"未激活"},"can_use_business":matches!(status,"active"|"grace"),"needs_activation":status=="unactivated","last_error":error,"runtime":"rust"});
    for field in [
        "device_fingerprint",
        "device_name",
        "os_type",
        "os_version",
        "app_version",
        "activation_status",
        "license_key_id",
        "masked_code",
        "activated_at",
        "last_check_in_at",
        "next_check_in_at",
        "last_valid_until",
        "expires_at",
        "lease_expires_at",
        "lease_sequence",
        "heartbeat_interval_minutes",
        "grace_period_minutes",
        "plan",
    ] {
        out[field] = state[field].clone();
    }
    if let Some(c) = claims {
        out["plan"]["feature_flags"] = c.feature_flags;
        out["lease_expires_at"] = json!(iso(c.exp));
        out["last_valid_until"] = json!(c.grace_until.map(iso));
    }
    out
}
