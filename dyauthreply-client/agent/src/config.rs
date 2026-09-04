use std::{net::SocketAddr, path::PathBuf};

use anyhow::{Context, Result};
use directories::ProjectDirs;

use crate::storage::{retention::WatermarkPolicy, SegmentPolicies, SegmentPolicy};

pub const DEFAULT_AGENT_PORT: u16 = 18_765;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AgentConfig {
    pub data_dir: PathBuf,
    pub bind_addr: SocketAddr,
    pub storage: StorageConfig,
}

/// Bounded rolling-storage policy used until the remote control plane supplies
/// calibrated per-installation values.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageConfig {
    pub segment_policies: SegmentPolicies,
    pub watermarks: WatermarkPolicy,
}

impl StorageConfig {
    /// Returns conservative initial values. They are deployment defaults, not
    /// capacity promises; later load/soak gates may tune them.
    ///
    /// # Panics
    ///
    /// Panics only if these source-controlled constants stop satisfying the
    /// storage module's hard invariants. The quality gate exercises this path.
    #[must_use]
    pub fn recommended() -> Self {
        const MIB: u64 = 1024 * 1024;
        const GIB: u64 = 1024 * MIB;
        const DAY_MS: u64 = 24 * 60 * 60 * 1000;

        let chat = SegmentPolicy {
            retention_age_ms: 30 * DAY_MS,
            max_total_bytes: 2 * GIB,
            target_segment_bytes: 16 * MIB,
            max_record_bytes: MIB,
            minimum_segments: 2,
            persist: true,
            compress: false,
        };
        let audit = SegmentPolicy {
            retention_age_ms: 30 * DAY_MS,
            max_total_bytes: 512 * MIB,
            target_segment_bytes: 8 * MIB,
            max_record_bytes: 256 * 1024,
            minimum_segments: 2,
            persist: true,
            compress: false,
        };
        let debug = SegmentPolicy {
            retention_age_ms: 3 * DAY_MS,
            max_total_bytes: 256 * MIB,
            target_segment_bytes: 8 * MIB,
            max_record_bytes: 512 * 1024,
            minimum_segments: 1,
            persist: true,
            compress: false,
        };
        let segment_policies = SegmentPolicies::new(chat, audit, debug)
            .expect("built-in storage policy must satisfy hard bounds");
        let watermarks = WatermarkPolicy {
            low_recovery_basis_points: 7_500,
            high_basis_points: 8_500,
            critical_basis_points: 9_500,
            max_deletions_per_run: 32,
        }
        .validate()
        .expect("built-in watermarks must be ordered");

        Self {
            segment_policies,
            watermarks,
        }
    }
}

impl AgentConfig {
    /// Loads the foundation Agent configuration from its environment variables.
    ///
    /// # Errors
    ///
    /// Returns an error when the platform data directory cannot be resolved,
    /// `DY_AGENT_DATA_DIR` is empty, `DY_AGENT_BIND` is malformed, or the bind
    /// address is not loopback-only.
    pub fn from_env() -> Result<Self> {
        let data_dir = match std::env::var_os("DY_AGENT_DATA_DIR") {
            Some(value) => PathBuf::from(value),
            None => ProjectDirs::from("com", "dyauthreply", "DyAuthReply")
                .context("cannot resolve the platform data directory")?
                .data_local_dir()
                .join("agent-v2"),
        };

        let bind_addr: SocketAddr = std::env::var("DY_AGENT_BIND")
            .unwrap_or_else(|_| format!("127.0.0.1:{DEFAULT_AGENT_PORT}"))
            .parse()
            .context("DY_AGENT_BIND must be a socket address")?;

        Self::new(data_dir, bind_addr)
    }

    /// Validates explicit configuration values.
    ///
    /// # Errors
    ///
    /// Returns an error when `data_dir` is empty or `bind_addr` is not a
    /// loopback address. The loopback restriction prevents this unauthenticated
    /// foundation health endpoint from being exposed to the network.
    pub fn new(data_dir: PathBuf, bind_addr: SocketAddr) -> Result<Self> {
        if data_dir.as_os_str().is_empty() {
            anyhow::bail!("the Agent data directory must not be empty");
        }

        if !bind_addr.ip().is_loopback() {
            anyhow::bail!("the foundation Agent may bind only to loopback");
        }

        Ok(Self {
            data_dir,
            bind_addr,
            storage: StorageConfig::recommended(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn production_port_is_not_the_foundation_default() {
        assert_ne!(DEFAULT_AGENT_PORT, 8765);
    }

    #[test]
    fn storage_defaults_are_bounded_and_family_specific() {
        let storage = StorageConfig::recommended();
        let chat = storage
            .segment_policies
            .get(crate::storage::SegmentFamily::Chat);
        let audit = storage
            .segment_policies
            .get(crate::storage::SegmentFamily::Audit);
        let debug = storage
            .segment_policies
            .get(crate::storage::SegmentFamily::Debug);

        assert!(chat.retention_age_ms > debug.retention_age_ms);
        assert!(chat.max_total_bytes > audit.max_total_bytes);
        assert!(audit.max_total_bytes > debug.max_total_bytes);
        assert!(chat.target_segment_bytes <= chat.max_total_bytes);
        assert!(storage.watermarks.max_deletions_per_run > 0);
    }

    #[test]
    fn loopback_addresses_are_accepted() {
        for address in ["127.0.0.1:18765", "[::1]:18765"] {
            let config = AgentConfig::new(
                PathBuf::from("isolated-agent-data"),
                address.parse().expect("valid socket address"),
            )
            .expect("loopback address must be accepted");
            assert!(config.bind_addr.ip().is_loopback());
        }
    }

    #[test]
    fn non_loopback_address_is_rejected() {
        let error = AgentConfig::new(
            PathBuf::from("isolated-agent-data"),
            "0.0.0.0:18765".parse().expect("valid socket address"),
        )
        .expect_err("network bind must be rejected");

        assert!(error.to_string().contains("only to loopback"));
    }

    #[test]
    fn empty_data_directory_is_rejected() {
        let error = AgentConfig::new(
            PathBuf::new(),
            "127.0.0.1:18765".parse().expect("valid socket address"),
        )
        .expect_err("empty data directory must be rejected");

        assert!(error.to_string().contains("must not be empty"));
    }
}
