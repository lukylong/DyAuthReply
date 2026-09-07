use std::{
    fs::{self, File, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};

use anyhow::{Context, Result};
use serde::Serialize;
use uuid::Uuid;

pub const INSTALLATION_ID_FILE: &str = "installation_id";
pub const INSTANCE_LOCK_FILE: &str = "agent.lock";

/// Stable installation identity plus the identity of the current Agent boot.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct AgentIdentity {
    pub installation_id: Uuid,
    pub boot_id: Uuid,
}

/// Owns the operating-system lock for an Agent data directory.
///
/// The lock file is intentionally left in place when this value is dropped.
/// Closing the held file descriptor releases the lock without introducing an
/// unlink/recreate race between consecutive Agent processes.
#[derive(Debug)]
pub struct InstanceLock {
    _file: File,
    path: PathBuf,
}

impl InstanceLock {
    /// Acquires the exclusive lock for `data_dir` without waiting.
    ///
    /// # Errors
    ///
    /// Returns an error if the directory or lock file cannot be prepared, or
    /// if another Agent already holds the lock.
    pub fn acquire(data_dir: &Path) -> Result<Self> {
        fs::create_dir_all(data_dir).with_context(|| {
            format!("cannot create Agent data directory {}", data_dir.display())
        })?;

        let path = data_dir.join(INSTANCE_LOCK_FILE);
        let mut options = OpenOptions::new();
        options.create(true).read(true).write(true);
        #[cfg(unix)]
        options.mode(0o600);

        let mut file = options
            .open(&path)
            .with_context(|| format!("cannot open Agent lock file {}", path.display()))?;
        enforce_private_permissions(&path)?;

        fs2::FileExt::try_lock_exclusive(&file).with_context(|| {
            format!(
                "another Agent already owns data directory {}",
                data_dir.display()
            )
        })?;

        write_lock_owner(&mut file, &path)?;
        Ok(Self { _file: file, path })
    }

    #[must_use]
    pub fn lock_path(&self) -> &Path {
        &self.path
    }
}

/// Locks `data_dir`, loads or creates its durable installation identity, and
/// creates a fresh boot identity.
///
/// # Errors
///
/// Returns an error if the directory is already owned by another Agent, if
/// its identity file is corrupt, or if any required filesystem operation
/// fails.
pub fn initialize(data_dir: &Path) -> Result<(AgentIdentity, InstanceLock)> {
    let instance_lock = InstanceLock::acquire(data_dir)?;
    let installation_id = load_or_create_installation_id(data_dir)?;
    let identity = AgentIdentity {
        installation_id,
        boot_id: Uuid::new_v4(),
    };
    Ok((identity, instance_lock))
}

fn load_or_create_installation_id(data_dir: &Path) -> Result<Uuid> {
    let path = data_dir.join(INSTALLATION_ID_FILE);
    match fs::read_to_string(&path) {
        Ok(raw) => {
            enforce_private_permissions(&path)?;
            parse_installation_id(&path, &raw)
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            create_installation_id(data_dir, &path)
        }
        Err(error) => Err(error)
            .with_context(|| format!("cannot read installation identity {}", path.display())),
    }
}

fn parse_installation_id(path: &Path, raw: &str) -> Result<Uuid> {
    Uuid::parse_str(raw.trim())
        .with_context(|| format!("installation identity {} is corrupt", path.display()))
}

fn create_installation_id(data_dir: &Path, path: &Path) -> Result<Uuid> {
    let installation_id = Uuid::new_v4();
    let temporary_path = data_dir.join(format!(".{INSTALLATION_ID_FILE}.{}.tmp", Uuid::new_v4()));

    let result = (|| -> Result<()> {
        let mut options = OpenOptions::new();
        options.create_new(true).write(true);
        #[cfg(unix)]
        options.mode(0o600);

        let mut file = options.open(&temporary_path).with_context(|| {
            format!(
                "cannot create temporary installation identity {}",
                temporary_path.display()
            )
        })?;
        writeln!(file, "{installation_id}").with_context(|| {
            format!(
                "cannot write temporary installation identity {}",
                temporary_path.display()
            )
        })?;
        file.sync_all().with_context(|| {
            format!(
                "cannot sync temporary installation identity {}",
                temporary_path.display()
            )
        })?;
        drop(file);

        fs::rename(&temporary_path, path)
            .with_context(|| format!("cannot atomically install identity {}", path.display()))?;
        enforce_private_permissions(path)?;
        sync_directory(data_dir)?;
        Ok(())
    })();

    if result.is_err() {
        let _ = fs::remove_file(&temporary_path);
    }
    result?;
    Ok(installation_id)
}

fn write_lock_owner(file: &mut File, path: &Path) -> Result<()> {
    file.set_len(0)
        .with_context(|| format!("cannot clear Agent lock file {}", path.display()))?;
    writeln!(file, "{}", std::process::id())
        .with_context(|| format!("cannot write Agent lock file {}", path.display()))?;
    file.sync_data()
        .with_context(|| format!("cannot sync Agent lock file {}", path.display()))
}

#[cfg(unix)]
fn enforce_private_permissions(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o600))
        .with_context(|| format!("cannot set private permissions on {}", path.display()))
}

#[cfg(not(unix))]
fn enforce_private_permissions(_path: &Path) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
fn sync_directory(data_dir: &Path) -> Result<()> {
    File::open(data_dir)
        .and_then(|directory| directory.sync_all())
        .with_context(|| format!("cannot sync Agent data directory {}", data_dir.display()))
}

#[cfg(not(unix))]
fn sync_directory(_data_dir: &Path) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn installation_identity_persists_while_boot_identity_changes() {
        let temporary_directory = tempfile::tempdir().expect("temporary directory");

        let (first_identity, first_lock) =
            initialize(temporary_directory.path()).expect("first initialization");
        drop(first_lock);
        let (second_identity, _second_lock) =
            initialize(temporary_directory.path()).expect("second initialization");

        assert_eq!(
            first_identity.installation_id,
            second_identity.installation_id
        );
        assert_ne!(first_identity.boot_id, second_identity.boot_id);
    }

    #[test]
    fn second_lock_for_same_directory_fails() {
        let temporary_directory = tempfile::tempdir().expect("temporary directory");
        let (_identity, _first_lock) =
            initialize(temporary_directory.path()).expect("first initialization");

        let error = initialize(temporary_directory.path()).expect_err("second lock must fail");

        assert!(
            error
                .to_string()
                .contains("another Agent already owns data directory"),
            "unexpected error: {error:#}"
        );
    }

    #[test]
    fn corrupt_installation_identity_is_rejected_without_replacement() {
        let temporary_directory = tempfile::tempdir().expect("temporary directory");
        let identity_path = temporary_directory.path().join(INSTALLATION_ID_FILE);
        fs::write(&identity_path, "not-a-uuid\n").expect("write corrupt identity");

        let error = initialize(temporary_directory.path()).expect_err("identity must be rejected");

        assert!(
            error.to_string().contains("is corrupt"),
            "unexpected error: {error:#}"
        );
        assert_eq!(
            fs::read_to_string(identity_path).expect("identity remains readable"),
            "not-a-uuid\n"
        );
    }

    #[cfg(unix)]
    #[test]
    fn identity_and_lock_files_are_owner_only() {
        use std::os::unix::fs::PermissionsExt;

        let temporary_directory = tempfile::tempdir().expect("temporary directory");
        let (_identity, instance_lock) =
            initialize(temporary_directory.path()).expect("initialization");

        for path in [
            temporary_directory.path().join(INSTALLATION_ID_FILE),
            instance_lock.lock_path().to_path_buf(),
        ] {
            let mode = fs::metadata(&path)
                .expect("file metadata")
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(mode, 0o600, "unexpected permissions for {}", path.display());
        }
    }
}
