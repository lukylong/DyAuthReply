use std::{net::SocketAddr, path::PathBuf};

use anyhow::{Context, Result};
use directories::ProjectDirs;

pub const DEFAULT_AGENT_PORT: u16 = 18_765;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AgentConfig {
    pub data_dir: PathBuf,
    pub bind_addr: SocketAddr,
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
