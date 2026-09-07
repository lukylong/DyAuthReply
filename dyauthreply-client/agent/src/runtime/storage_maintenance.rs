//! Process-wide rolling-storage maintenance executed on a blocking lane.

use std::{
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{Context, Result};

use crate::{
    config::StorageConfig,
    health::{StorageHealthResponse, StorageStartupSnapshot},
    storage::{
        retention::{plan_cleanup_with_previous, DiskSnapshot},
        RecoveryReport, SegmentCatalog, SegmentStore,
    },
    store::CoreStore,
};

/// Cloneable owner for one segment store. Blocking work is joined by the
/// cleanup task; runtime shutdown stays Draining until the worker is observed.
#[derive(Clone)]
pub struct StorageMaintenance {
    core: Arc<CoreStore>,
    segments: Arc<Mutex<SegmentStore>>,
    config: StorageConfig,
    startup_recovery: RecoveryReport,
}

impl StorageMaintenance {
    #[must_use]
    pub fn new(core: Arc<CoreStore>, segments: SegmentStore, config: StorageConfig) -> Self {
        let startup_recovery = segments.recovery_report().clone();
        Self {
            core,
            segments: Arc::new(Mutex::new(segments)),
            config,
            startup_recovery,
        }
    }

    /// Samples disk state, applies one bounded retention plan, samples again,
    /// persists hysteresis state, and returns a fresh health projection.
    ///
    /// # Errors
    ///
    /// Returns storage, clock, join, or lock-poisoning diagnostics. A poisoned
    /// segment store remains fail-closed and should make the runtime degraded.
    pub async fn run_once(&self) -> Result<StorageHealthResponse> {
        let this = self.clone();
        tokio::task::spawn_blocking(move || this.run_once_blocking())
            .await
            .context("storage-maintenance blocking worker failed")?
    }

    fn run_once_blocking(&self) -> Result<StorageHealthResponse> {
        let now_ms = unix_time_ms()?;
        let mut segments = self
            .segments
            .lock()
            .map_err(|_| anyhow::anyhow!("segment-store lock is poisoned"))?;
        let disk = disk_snapshot(segments.root())?;
        let previous_pressure = self
            .core
            .storage_cleanup_state()?
            .map(|state| state.last_pressure);
        let manifests = SegmentCatalog::list_manifests(self.core.as_ref())?;
        let cleanup = plan_cleanup_with_previous(
            &manifests,
            &self.config.segment_policies,
            disk,
            self.config.watermarks,
            now_ms,
            previous_pressure,
        )?;
        let cleanup_deleted_segments = segments.apply_cleanup(&cleanup)?;
        let remaining = SegmentCatalog::list_manifests(self.core.as_ref())?;
        let observed = plan_cleanup_with_previous(
            &remaining,
            &self.config.segment_policies,
            disk_snapshot(segments.root())?,
            self.config.watermarks,
            now_ms,
            Some(cleanup.pressure),
        )?;
        self.core
            .record_storage_cleanup_state(now_ms, observed.pressure)?;
        let sealed_segment_bytes = remaining.iter().try_fold(0_u64, |total, manifest| {
            total
                .checked_add(manifest.stored_bytes)
                .context("sealed segment byte total overflow")
        })?;

        Ok(StorageHealthResponse::startup(&StorageStartupSnapshot {
            pressure: observed.pressure,
            disposable_writes_allowed: observed.allow_disposable_writes,
            background_work_paused: observed.pause_background,
            sealed_segment_count: remaining.len(),
            sealed_segment_bytes,
            active_segment_count: segments.active_writer_count(),
            cleanup_deleted_segments,
            recovery: self.startup_recovery.clone(),
        }))
    }
}

fn unix_time_ms() -> Result<i64> {
    i64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .context("system clock is before the Unix epoch")?
            .as_millis(),
    )
    .context("system time does not fit storage timestamp")
}

fn disk_snapshot(path: &std::path::Path) -> Result<DiskSnapshot> {
    Ok(DiskSnapshot {
        total_bytes: fs2::total_space(path)?,
        available_bytes: fs2::available_space(path)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{config::StorageConfig, storage::ZstdCodec};

    #[tokio::test]
    async fn maintenance_runs_on_real_isolated_store_and_updates_durable_state() {
        let directory = tempfile::tempdir().expect("temporary data directory");
        let core = Arc::new(CoreStore::open(directory.path()).expect("core store"));
        let catalog: Arc<dyn SegmentCatalog> = core.clone();
        let config = StorageConfig::recommended();
        let segments = SegmentStore::open_with_codec(
            directory.path().join("segments"),
            config.segment_policies.clone(),
            catalog,
            Arc::new(ZstdCodec::default()),
        )
        .expect("segment store");
        let maintenance = StorageMaintenance::new(core.clone(), segments, config);

        let health = maintenance.run_once().await.expect("maintenance run");

        assert!(health.database_integrity_verified);
        assert!(health.segment_integrity_verified);
        let saved = core
            .storage_cleanup_state()
            .expect("cleanup state")
            .expect("persisted observation");
        let expected = match saved.last_pressure {
            crate::storage::retention::DiskPressure::Normal => "normal",
            crate::storage::retention::DiskPressure::High => "high",
            crate::storage::retention::DiskPressure::Critical => "critical",
        };
        assert_eq!(health.pressure, expected);
    }
}
