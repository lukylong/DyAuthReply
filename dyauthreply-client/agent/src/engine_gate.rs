//! Client-wide engine ownership, independent of checkout/native data directory.
//! The durable Rust selection survives exit/crash; closing a GUI cannot revive
//! the old worker's independent reply history. Locks are never unlinked.
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};

pub const ENGINE_LOCK: &str = "message-engine.lock";
pub const ENGINE_SELECTION: &str = "message-engine.json";
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Selection {
    version: u8,
    selected: String,
    native_data_dir: PathBuf,
}
/// Holds both updated worker exclusion and legacy launcher exclusion until all
/// native sockets/executors have drained. There is no PID-based lock takeover.
pub struct EngineGate {
    _launcher: File,
    _engine: File,
}
/// Holds cross-engine locks while legacy state is imported. Until activation,
/// dropping this guard leaves no sticky Rust selection behind.
pub struct EngineTransition {
    client_root: PathBuf,
    launcher: File,
    engine: File,
}
impl EngineTransition {
    /// Excludes both updated and legacy launchers without selecting an engine.
    /// # Errors
    /// Rejects an active owner or unsafe lock path.
    pub fn acquire(client_root: &Path) -> Result<Self> {
        fs::create_dir_all(client_root)?;
        let client_root = fs::canonicalize(client_root)?;
        let mut launcher = locked_file(&client_root.join("launcher.lock"))?;
        // Clear stale PID bytes only after taking the old launcher's own inode.
        launcher.set_len(0)?;
        launcher.flush()?;
        launcher.sync_all()?;
        let engine = locked_file(&client_root.join(ENGINE_LOCK))?;
        Ok(Self {
            client_root,
            launcher,
            engine,
        })
    }

    /// Publishes the durable Rust selection after migration/preflight succeeds.
    /// # Errors
    /// Rejects corrupt markers or a different correctness directory.
    pub fn activate(self, native_data: &Path) -> Result<EngineGate> {
        fs::create_dir_all(native_data)?;
        let native_data = fs::canonicalize(native_data)?;
        let path = self.client_root.join(ENGINE_SELECTION);
        if let Some(selection) = read_selection(&path)? {
            anyhow::ensure!(
                selection.native_data_dir == native_data,
                "native engine is bound to another data directory; migrate correctness state before switching"
            );
        } else {
            publish_selection(
                &self.client_root,
                &path,
                &Selection {
                    version: 1,
                    selected: "rust".into(),
                    native_data_dir: native_data,
                },
            )?;
        }
        Ok(EngineGate {
            _launcher: self.launcher,
            _engine: self.engine,
        })
    }
}
impl EngineGate {
    /// Selects Rust once, while excluding any cooperating engine/old launcher.
    /// # Errors
    /// Rejects a busy owner, corrupt marker, or a different native correctness DB.
    pub fn acquire_native(client_root: &Path, native_data: &Path) -> Result<Self> {
        EngineTransition::acquire(client_root)?.activate(native_data)
    }
}
fn locked_file(path: &Path) -> Result<File> {
    reject_link(path)?;
    let mut options = OpenOptions::new();
    options.create(true).read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options
        .open(path)
        .context("cannot open client engine lock")?;
    fs2::FileExt::try_lock_exclusive(&file)
        .context("another client engine or launcher is active")?;
    Ok(file)
}
fn reject_link(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(meta) => anyhow::ensure!(
            meta.is_file() && !meta.file_type().is_symlink(),
            "engine metadata must be a regular file"
        ),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.into()),
    }
    Ok(())
}
fn read_selection(path: &Path) -> Result<Option<Selection>> {
    reject_link(path)?;
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    let mut bytes = Vec::new();
    file.take(4097).read_to_end(&mut bytes)?;
    anyhow::ensure!(bytes.len() <= 4096, "engine selection exceeds bound");
    let value: Selection = serde_json::from_slice(&bytes).context("invalid engine selection")?;
    anyhow::ensure!(
        value.version == 1 && value.selected == "rust" && value.native_data_dir.is_absolute(),
        "unsupported engine selection"
    );
    Ok(Some(value))
}
fn publish_selection(root: &Path, path: &Path, selection: &Selection) -> Result<()> {
    let temporary = root.join(format!(".message-engine.{}.tmp", uuid::Uuid::new_v4()));
    let result = (|| -> Result<()> {
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temporary)?;
        file.write_all(&serde_json::to_vec(selection)?)?;
        file.sync_all()?;
        drop(file);
        fs::rename(&temporary, path)?;
        #[cfg(unix)]
        File::open(root)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    result
}
/// Matches client launcher roots, NOT the versioned native data folder.
/// # Errors
/// Linux/manual deployments must provide their actual client root explicitly.
pub fn client_root_from_env() -> Result<PathBuf> {
    if let Some(path) = std::env::var_os("CLIENT_DATA_DIR") {
        anyhow::ensure!(!path.is_empty(), "CLIENT_DATA_DIR is empty");
        let path = PathBuf::from(path);
        anyhow::ensure!(
            path.is_absolute(),
            "CLIENT_DATA_DIR must be absolute for cross-engine ownership"
        );
        return Ok(path);
    }
    #[cfg(target_os = "macos")]
    {
        Ok(directories::BaseDirs::new()
            .context("home directory missing")?
            .home_dir()
            .join("Library/Application Support/DyAuthReply"))
    }
    #[cfg(target_os = "windows")]
    {
        Ok(PathBuf::from(
            std::env::var_os("APPDATA").context("APPDATA missing; set CLIENT_DATA_DIR")?,
        )
        .join("DyAuthReply"))
    }
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    {
        anyhow::bail!("set CLIENT_DATA_DIR to the shared legacy/native client root");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn same_client_different_native_folders_cannot_compete_or_reset_history() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path().join("client");
        let a = d.path().join("a");
        let b = d.path().join("b");
        let first = EngineGate::acquire_native(&root, &a).unwrap();
        assert!(EngineGate::acquire_native(&root, &b).is_err());
        let marker = fs::read(root.join(ENGINE_SELECTION)).unwrap();
        drop(first);
        assert!(EngineGate::acquire_native(&root, &b).is_err());
        assert_eq!(fs::read(root.join(ENGINE_SELECTION)).unwrap(), marker);
        assert!(EngineGate::acquire_native(&root, &a).is_ok());
    }
    #[test]
    fn failed_migration_releases_locks_without_selecting_rust() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path().join("client");
        let transition = EngineTransition::acquire(&root).unwrap();
        assert!(!root.join(ENGINE_SELECTION).exists());
        assert!(EngineTransition::acquire(&root).is_err());
        drop(transition);
        let transition = EngineTransition::acquire(&root).unwrap();
        assert!(!root.join(ENGINE_SELECTION).exists());
        let native = d.path().join("native");
        let gate = transition.activate(&native).unwrap();
        assert!(root.join(ENGINE_SELECTION).is_file());
        drop(gate);
        assert!(EngineGate::acquire_native(&root, &native).is_ok());
    }
    #[test]
    fn stale_pid_is_cleared_only_after_lock_and_lock_inode_is_preserved() {
        let d = tempfile::tempdir().unwrap();
        let root = d.path();
        fs::write(root.join("launcher.lock"), b"99999999").unwrap();
        let gate = EngineGate::acquire_native(root, &root.join("native")).unwrap();
        assert!(fs::read(root.join("launcher.lock")).unwrap().is_empty());
        drop(gate);
        assert!(root.join("launcher.lock").exists());
        assert!(root.join(ENGINE_LOCK).exists());
    }
    #[test]
    fn corrupt_and_future_markers_fail_closed_without_replacement() {
        let d = tempfile::tempdir().unwrap();
        let path = d.path().join(ENGINE_SELECTION);
        for raw in [
            "broken",
            r#"{"version":2,"selected":"rust","native_data_dir":"/tmp/native"}"#,
        ] {
            fs::write(&path, raw).unwrap();
            assert!(EngineGate::acquire_native(d.path(), &d.path().join("native")).is_err());
            assert_eq!(fs::read_to_string(&path).unwrap(), raw);
        }
    }
}
