use std::{net::SocketAddr, path::PathBuf};

use anyhow::{Context, Result};

use crate::storage::{retention::WatermarkPolicy, SegmentPolicies, SegmentPolicy};

pub const DEFAULT_AGENT_PORT: u16 = 18_765;
pub const MIN_RUNTIME_DRAIN_TIMEOUT_MS: u64 = 100;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AgentConfig {
    pub data_dir: PathBuf,
    pub bind_addr: SocketAddr,
    pub storage: StorageConfig,
    pub runtime: RuntimeConfig,
}

/// Bounded rolling-storage policy used until the remote control plane supplies
/// calibrated per-installation values.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StorageConfig {
    pub segment_policies: SegmentPolicies,
    pub watermarks: WatermarkPolicy,
}

/// Hard bounds for the process-wide account runtime.
///
/// These values deliberately describe one installation, not one account. This
/// prevents memory, signing, reconnect, and heartbeat load from multiplying by
/// the hosted-account count without an upper bound.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RuntimeConfig {
    pub account_mailbox_capacity: usize,
    pub global_queue_capacity: usize,
    pub per_account_queue_capacity: usize,
    pub manual_burst: usize,
    pub signer_lanes: usize,
    pub signer_queue_capacity: usize,
    pub reconnect_rate_per_second: u32,
    pub reconnect_burst: u32,
    pub heartbeat_interval_ms: u64,
    pub heartbeat_full_interval_ms: u64,
    pub heartbeat_max_accounts: usize,
    pub storage_cleanup_interval_ms: u64,
    pub drain_timeout_ms: u64,
}

impl RuntimeConfig {
    /// Conservative defaults for the deterministic 10/100/300 account gates.
    #[must_use]
    pub const fn recommended() -> Self {
        Self {
            account_mailbox_capacity: 64,
            global_queue_capacity: 8_192,
            per_account_queue_capacity: 128,
            manual_burst: 8,
            signer_lanes: 4,
            signer_queue_capacity: 512,
            reconnect_rate_per_second: 4,
            reconnect_burst: 8,
            heartbeat_interval_ms: 30_000,
            heartbeat_full_interval_ms: 300_000,
            heartbeat_max_accounts: 512,
            storage_cleanup_interval_ms: 60_000,
            drain_timeout_ms: 10_000,
        }
    }

    /// Rejects values that would make the bounded runtime inert or internally
    /// contradictory.
    ///
    /// # Errors
    ///
    /// Returns a diagnostic naming the invalid relationship.
    pub fn validate(&self) -> Result<()> {
        if self.account_mailbox_capacity == 0
            || self.global_queue_capacity == 0
            || self.per_account_queue_capacity == 0
            || self.manual_burst == 0
            || self.signer_lanes == 0
            || self.signer_queue_capacity == 0
            || self.heartbeat_max_accounts == 0
        {
            anyhow::bail!("runtime capacities and manual burst must be positive");
        }
        if self.per_account_queue_capacity > self.global_queue_capacity {
            anyhow::bail!("per-account queue capacity cannot exceed the global capacity");
        }
        if self.signer_lanes > self.signer_queue_capacity {
            anyhow::bail!("signer lane count cannot exceed its queue capacity");
        }
        if self.reconnect_rate_per_second == 0 || self.reconnect_burst == 0 {
            anyhow::bail!("reconnect rate and burst must be positive");
        }
        if self.heartbeat_interval_ms == 0
            || self.heartbeat_full_interval_ms < self.heartbeat_interval_ms
            || self.storage_cleanup_interval_ms == 0
        {
            anyhow::bail!(
                "runtime intervals must be positive and full heartbeat cannot be shorter than delta heartbeat"
            );
        }
        if self.drain_timeout_ms < MIN_RUNTIME_DRAIN_TIMEOUT_MS {
            anyhow::bail!(
                "runtime drain timeout must be at least {MIN_RUNTIME_DRAIN_TIMEOUT_MS} milliseconds"
            );
        }
        Ok(())
    }
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
            None => crate::engine_gate::client_root_from_env()?.join("agent-v2"),
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

        let runtime = RuntimeConfig::recommended();
        runtime.validate()?;

        Ok(Self {
            data_dir,
            bind_addr,
            storage: StorageConfig::recommended(),
            runtime,
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
    fn runtime_defaults_are_process_wide_and_bounded() {
        let runtime = RuntimeConfig::recommended();
        runtime.validate().expect("recommended runtime config");
        assert!(runtime.per_account_queue_capacity < runtime.global_queue_capacity);
        assert!(runtime.signer_lanes < runtime.signer_queue_capacity);
        assert!(runtime.heartbeat_max_accounts >= 300);
        assert_eq!(runtime.reconnect_rate_per_second, 4);
        assert_eq!(runtime.reconnect_burst, 8);
    }

    #[test]
    fn invalid_runtime_relationships_are_rejected() {
        let mut runtime = RuntimeConfig::recommended();
        runtime.per_account_queue_capacity = runtime.global_queue_capacity + 1;
        assert!(runtime.validate().is_err());

        let mut runtime = RuntimeConfig::recommended();
        runtime.heartbeat_full_interval_ms = runtime.heartbeat_interval_ms - 1;
        assert!(runtime.validate().is_err());
        let mut runtime = RuntimeConfig::recommended();
        runtime.drain_timeout_ms = MIN_RUNTIME_DRAIN_TIMEOUT_MS - 1;
        assert!(runtime.validate().is_err());
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
