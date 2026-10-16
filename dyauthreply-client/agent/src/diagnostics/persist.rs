//! On-disk copy of the diagnostics the server has not accepted yet, so a restart
//! or crash does not lose them. Events are redacted before they are buffered, so
//! nothing is written here that would not also be uploaded.
use super::DiagnosticEvent;
use serde::Deserialize;
use serde_json::{Map, Value};
use std::{fs, io::Write, path::Path};

/// Far above what the bounded buffer can hold; anything larger is not ours.
const MAX_FILE_BYTES: u64 = 16 * 1024 * 1024;

/// A saved [`DiagnosticEvent`]; its level is `&'static str` in memory.
#[derive(Deserialize)]
struct Saved {
    at: String,
    level: String,
    target: String,
    message: String,
    fields: Map<String, Value>,
}

/// The saved events, or none when the file is missing, oversized or corrupt.
pub(super) fn read(path: &Path) -> Vec<DiagnosticEvent> {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return Vec::new();
    };
    if !metadata.is_file() || metadata.len() > MAX_FILE_BYTES {
        return Vec::new();
    }
    let saved: Vec<Saved> = fs::read(path)
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default();
    saved
        .into_iter()
        .filter_map(|event| {
            let level = ["ERROR", "WARN", "INFO"]
                .into_iter()
                .find(|level| *level == event.level)?;
            Some(DiagnosticEvent {
                at: event.at,
                level,
                target: event.target,
                message: event.message,
                fields: event.fields,
            })
        })
        .collect()
}

/// Atomically replaces the saved events (owner-only), or removes the file when
/// none remain.
pub(super) fn write(path: &Path, events: &[DiagnosticEvent]) -> anyhow::Result<()> {
    if events.is_empty() {
        return remove(path);
    }
    let temporary = path.with_extension("json.tmp");
    remove(&temporary)?;
    let mut options = fs::OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(&serde_json::to_vec(events)?)?;
    file.sync_all()?;
    fs::rename(&temporary, path)?;
    Ok(())
}

pub(super) fn remove(path: &Path) -> anyhow::Result<()> {
    match fs::remove_file(path) {
        Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(error.into()),
        _ => Ok(()),
    }
}
