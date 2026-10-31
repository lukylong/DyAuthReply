//! Account-bound, same-path Chromium profiles. Never reconstruct a login from cookies.
use super::{create_private_dir, validate_session_id};
use anyhow::{Context, Result};
use rusqlite::{params, Connection, OptionalExtension};
use std::path::{Path, PathBuf};

const RETAIN: &str = "dyauthreply-retained-profile";
fn database(root: &Path) -> Result<Connection> {
    let directory = root.join("quick-auth/profiles");
    create_private_dir(&directory)?;
    let db = Connection::open(directory.join("bindings.sqlite3"))?;
    db.busy_timeout(std::time::Duration::from_secs(2))?;
    db.execute_batch("CREATE TABLE IF NOT EXISTS profiles(account TEXT PRIMARY KEY, scope TEXT NOT NULL, generation INTEGER NOT NULL, session TEXT NOT NULL);")?;
    Ok(db)
}
fn checked_path(root: &Path, session: &str) -> Result<PathBuf> {
    validate_session_id(session)?;
    let parent = root.join("quick-auth/sessions");
    let path = parent.join(session);
    anyhow::ensure!(
        !std::fs::symlink_metadata(&path)?.file_type().is_symlink(),
        "浏览器资料目录异常"
    );
    anyhow::ensure!(
        path.canonicalize()?.parent() == Some(parent.canonicalize()?.as_path()),
        "浏览器资料目录越界"
    );
    Ok(path)
}
pub(super) fn retained(path: &Path) -> bool {
    path.join(RETAIN).is_file()
}

pub(super) fn bind(
    root: &Path,
    profile: &Path,
    account: &str,
    scope: &str,
    generation: u64,
) -> Result<()> {
    validate_session_id(account)?;
    anyhow::ensure!(!scope.is_empty() && generation > 0, "浏览器资料身份缺失");
    let session = profile
        .file_name()
        .and_then(|v| v.to_str())
        .context("浏览器资料编号缺失")?;
    let checked = checked_path(root, session)?;
    anyhow::ensure!(
        checked.canonicalize()? == profile.canonicalize()?,
        "浏览器资料路径不匹配"
    );
    // Mark first: a failed metadata commit must not erase an authenticated profile.
    std::fs::write(profile.join(RETAIN), b"1\n")?;
    database(root)?.execute("INSERT INTO profiles VALUES(?1,?2,?3,?4) ON CONFLICT(account) DO UPDATE SET scope=excluded.scope,generation=excluded.generation,session=excluded.session", params![account, scope, generation, session])?;
    Ok(())
}
pub(super) fn lookup(
    root: &Path,
    account: &str,
    scope: &str,
    generation: u64,
) -> Result<Option<PathBuf>> {
    validate_session_id(account)?;
    let row: Option<(String, u64, String)> = database(root)?
        .query_row(
            "SELECT scope,generation,session FROM profiles WHERE account=?1",
            [account],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .optional()?;
    let Some((bound, version, session)) = row else {
        return Ok(None);
    };
    // An external credential replacement is not proof that this browser owns that session.
    if bound != scope || version != generation {
        return Ok(None);
    }
    let path = checked_path(root, &session)?;
    anyhow::ensure!(retained(&path), "完整浏览器资料缺失，请使用快捷登录重建");
    Ok(Some(path))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn complete_profile_survives_cleanup_at_same_path_and_is_account_generation_bound() {
        let root = tempfile::tempdir().unwrap();
        let account = uuid::Uuid::new_v4().to_string();
        let profile = root
            .path()
            .join("quick-auth/sessions")
            .join(uuid::Uuid::new_v4().to_string());
        create_private_dir(&profile).unwrap();
        std::fs::write(profile.join("Local State"), b"synthetic-device-state").unwrap();
        bind(root.path(), &profile, &account, "self", 7).unwrap();
        super::super::stop_child_and_remove(
            &std::sync::Arc::new(std::sync::Mutex::new(None)),
            &profile,
        )
        .unwrap();
        assert_eq!(
            lookup(root.path(), &account, "self", 7).unwrap(),
            Some(profile.clone())
        );
        assert_eq!(
            std::fs::read(profile.join("Local State")).unwrap(),
            b"synthetic-device-state"
        );
        assert!(lookup(root.path(), &account, "other", 7).unwrap().is_none());
        assert!(lookup(root.path(), &account, "self", 8).unwrap().is_none());
        assert!(
            lookup(root.path(), &uuid::Uuid::new_v4().to_string(), "self", 7)
                .unwrap()
                .is_none()
        );
        assert!(checked_path(root.path(), "../../other").is_err());
    }
}
