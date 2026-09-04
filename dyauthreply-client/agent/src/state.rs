//! Orthogonal account runtime state and derived operator-facing status.
//!
//! Each axis records one fact only. In particular, "receive only" is not a
//! lifecycle state: it is derived from a live inbound path and a blocked send
//! capability. Keeping the axes independent prevents send-side risk control
//! from incorrectly taking an otherwise healthy receiver offline.

use std::fmt;

use serde::{Deserialize, Serialize};

/// Process-level progress for one managed account.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LifecycleState {
    /// Resources have not finished starting.
    #[default]
    Starting,
    /// The account is allowed to perform normal work.
    Running,
    /// Automatic sends are paused while inbound processing stays active.
    PausedAuto,
    /// New work is rejected while in-flight work is allowed to settle.
    Draining,
    /// The account hit a terminal runtime fault and requires recovery.
    Faulted,
    /// All work for the account has ended.
    Stopped,
}

/// Whether this agent currently owns the account's fenced lease.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum OwnershipState {
    /// No lease is assigned to this agent.
    #[default]
    Unowned,
    /// Lease acquisition is in progress.
    Acquiring,
    /// This agent owns a current lease and may perform fenced side effects.
    Owned,
    /// A newer owner or fence epoch displaced this agent.
    Lost,
    /// The owned lease passed its validity deadline.
    Expired,
}

/// State of the account's inbound transport.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InboundState {
    /// No inbound connection is active.
    #[default]
    Disconnected,
    /// The primary inbound path is being established.
    Connecting,
    /// The primary WebSocket path is operating normally.
    WsHealthy,
    /// HTTP reconciliation remains usable while WebSocket is impaired.
    HttpDegraded,
    /// Inbound retries are temporarily suppressed after a failure.
    Backoff,
}

impl InboundState {
    #[must_use]
    const fn is_usable(self) -> bool {
        matches!(self, Self::WsHealthy | Self::HttpDegraded)
    }
}

/// Ability to perform outbound message side effects.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SendCapability {
    /// Sending has not been probed or enabled yet.
    #[default]
    Unknown,
    /// Sending is operating normally.
    Sendable,
    /// Inbound may continue, but this account must not send.
    ReceiveOnly,
    /// The platform has restricted sending while inbound may remain healthy.
    RiskControlled,
    /// Shared platform credentials are expired, blocking receive and send.
    AuthExpired,
}

/// Stable, derived status shown to operators and API clients.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AccountDisplayStatus {
    Starting,
    AcquiringOwnership,
    NotOwned,
    Healthy,
    Degraded,
    ReceiveOnly,
    AuthenticationInvalid,
    RiskControlled,
    LeaseExpired,
    OwnershipLost,
    PausedAuto,
    Draining,
    Faulted,
    Stopped,
}

impl AccountDisplayStatus {
    /// A concise Chinese label suitable for the existing desktop UI.
    #[must_use]
    pub const fn label(self) -> &'static str {
        match self {
            Self::Starting => "启动中",
            Self::AcquiringOwnership => "正在获取租约",
            Self::NotOwned => "未持有租约",
            Self::Healthy => "运行正常",
            Self::Degraded => "降级运行",
            Self::ReceiveOnly => "仅接收",
            Self::AuthenticationInvalid => "认证失效",
            Self::RiskControlled => "发送风控（仅接收）",
            Self::LeaseExpired => "租约失效",
            Self::OwnershipLost => "租约已丢失",
            Self::PausedAuto => "自动回复已暂停",
            Self::Draining => "停止中",
            Self::Faulted => "运行故障",
            Self::Stopped => "已停止",
        }
    }
}

impl fmt::Display for AccountDisplayStatus {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.label())
    }
}

/// Complete runtime state for one account.
///
/// The generation and epoch counters accompany the orthogonal enum facts so
/// callers can reject stale credential work and fenced lease side effects.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
pub struct AccountRuntimeState {
    pub lifecycle: LifecycleState,
    pub ownership: OwnershipState,
    pub inbound: InboundState,
    pub send: SendCapability,
    pub credential_generation: u64,
    pub lease_epoch: u64,
}

impl AccountRuntimeState {
    /// Returns whether the account may accept new inbound work now.
    #[must_use]
    pub const fn can_receive(&self) -> bool {
        matches!(
            self.lifecycle,
            LifecycleState::Running | LifecycleState::PausedAuto
        ) && matches!(self.ownership, OwnershipState::Owned)
            && self.inbound.is_usable()
            && !matches!(self.send, SendCapability::AuthExpired)
    }

    /// Returns whether an operator may start a manual outbound side effect.
    ///
    /// `PausedAuto` intentionally allows manual replies. It pauses automatic
    /// rule execution rather than silently disabling the manual workbench.
    #[must_use]
    pub const fn can_send(&self) -> bool {
        matches!(
            self.lifecycle,
            LifecycleState::Running | LifecycleState::PausedAuto
        ) && matches!(self.ownership, OwnershipState::Owned)
            && matches!(self.send, SendCapability::Sendable)
    }

    /// Returns whether a new automatic reply may be started now.
    #[must_use]
    pub const fn can_auto_reply(&self) -> bool {
        matches!(self.lifecycle, LifecycleState::Running)
            && matches!(self.ownership, OwnershipState::Owned)
            && matches!(self.send, SendCapability::Sendable)
    }

    /// Derives one display value without collapsing the four stored axes.
    ///
    /// Terminal lifecycle state wins first, followed by credential and lease
    /// validity. Send risk control is intentionally more specific than the
    /// generic receive-only status. Transport fallback and unknown capability
    /// are reported as degraded rather than as authentication failure.
    #[must_use]
    pub const fn display_status(&self) -> AccountDisplayStatus {
        match self.lifecycle {
            LifecycleState::Stopped => return AccountDisplayStatus::Stopped,
            LifecycleState::Faulted => return AccountDisplayStatus::Faulted,
            LifecycleState::Draining => return AccountDisplayStatus::Draining,
            LifecycleState::Starting => return AccountDisplayStatus::Starting,
            LifecycleState::Running | LifecycleState::PausedAuto => {}
        }

        if matches!(self.send, SendCapability::AuthExpired) {
            return AccountDisplayStatus::AuthenticationInvalid;
        }

        match self.ownership {
            OwnershipState::Expired => return AccountDisplayStatus::LeaseExpired,
            OwnershipState::Lost => return AccountDisplayStatus::OwnershipLost,
            OwnershipState::Unowned => return AccountDisplayStatus::NotOwned,
            OwnershipState::Acquiring => return AccountDisplayStatus::AcquiringOwnership,
            OwnershipState::Owned => {}
        }

        if matches!(self.send, SendCapability::RiskControlled) {
            return AccountDisplayStatus::RiskControlled;
        }

        if matches!(self.lifecycle, LifecycleState::PausedAuto) {
            return AccountDisplayStatus::PausedAuto;
        }

        if matches!(self.send, SendCapability::ReceiveOnly) && self.can_receive() {
            return AccountDisplayStatus::ReceiveOnly;
        }

        if matches!(self.inbound, InboundState::WsHealthy)
            && matches!(self.send, SendCapability::Sendable)
        {
            return AccountDisplayStatus::Healthy;
        }

        AccountDisplayStatus::Degraded
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn healthy_state() -> AccountRuntimeState {
        AccountRuntimeState {
            lifecycle: LifecycleState::Running,
            ownership: OwnershipState::Owned,
            inbound: InboundState::WsHealthy,
            send: SendCapability::Sendable,
            credential_generation: 7,
            lease_epoch: 42,
        }
    }

    #[test]
    fn healthy_account_can_receive_and_send() {
        let state = healthy_state();

        assert!(state.can_receive());
        assert!(state.can_send());
        assert!(state.can_auto_reply());
        assert_eq!(state.display_status(), AccountDisplayStatus::Healthy);
    }

    #[test]
    fn expired_authentication_blocks_receive_and_send() {
        let state = AccountRuntimeState {
            send: SendCapability::AuthExpired,
            ..healthy_state()
        };

        assert!(!state.can_receive());
        assert!(!state.can_send());
        assert!(!state.can_auto_reply());
        assert_eq!(
            state.display_status(),
            AccountDisplayStatus::AuthenticationInvalid
        );
    }

    #[test]
    fn risk_control_preserves_inbound_receive_only_operation() {
        let state = AccountRuntimeState {
            send: SendCapability::RiskControlled,
            ..healthy_state()
        };

        assert!(state.can_receive());
        assert!(!state.can_send());
        assert!(!state.can_auto_reply());
        assert_eq!(state.display_status(), AccountDisplayStatus::RiskControlled);
    }

    #[test]
    fn expired_lease_blocks_all_work() {
        let state = AccountRuntimeState {
            ownership: OwnershipState::Expired,
            ..healthy_state()
        };

        assert!(!state.can_receive());
        assert!(!state.can_send());
        assert!(!state.can_auto_reply());
        assert_eq!(state.display_status(), AccountDisplayStatus::LeaseExpired);
    }

    #[test]
    fn explicit_receive_only_capability_keeps_inbound_active() {
        let state = AccountRuntimeState {
            send: SendCapability::ReceiveOnly,
            ..healthy_state()
        };

        assert!(state.can_receive());
        assert!(!state.can_send());
        assert!(!state.can_auto_reply());
        assert_eq!(state.display_status(), AccountDisplayStatus::ReceiveOnly);
    }

    #[test]
    fn http_fallback_is_usable_but_reported_as_degraded() {
        let state = AccountRuntimeState {
            inbound: InboundState::HttpDegraded,
            ..healthy_state()
        };

        assert!(state.can_receive());
        assert!(state.can_send());
        assert!(state.can_auto_reply());
        assert_eq!(state.display_status(), AccountDisplayStatus::Degraded);
    }

    #[test]
    fn inbound_backoff_blocks_receive_and_reports_degraded() {
        let state = AccountRuntimeState {
            inbound: InboundState::Backoff,
            ..healthy_state()
        };

        assert!(!state.can_receive());
        assert!(state.can_send());
        assert!(state.can_auto_reply());
        assert_eq!(state.display_status(), AccountDisplayStatus::Degraded);
    }

    #[test]
    fn paused_auto_keeps_receive_and_manual_send_but_blocks_automatic_reply() {
        let state = AccountRuntimeState {
            lifecycle: LifecycleState::PausedAuto,
            ..healthy_state()
        };

        assert!(state.can_receive());
        assert!(state.can_send());
        assert!(!state.can_auto_reply());
        assert_eq!(state.display_status(), AccountDisplayStatus::PausedAuto);
    }

    #[test]
    fn stopped_lifecycle_wins_and_blocks_all_work() {
        let state = AccountRuntimeState {
            lifecycle: LifecycleState::Stopped,
            ownership: OwnershipState::Expired,
            send: SendCapability::AuthExpired,
            ..healthy_state()
        };

        assert!(!state.can_receive());
        assert!(!state.can_send());
        assert!(!state.can_auto_reply());
        assert_eq!(state.display_status(), AccountDisplayStatus::Stopped);
    }

    #[test]
    fn state_serializes_axes_and_fence_generations_without_collapsing_them() {
        let state = AccountRuntimeState {
            send: SendCapability::RiskControlled,
            ..healthy_state()
        };

        let value = serde_json::to_value(state).expect("runtime state must serialize");
        assert_eq!(value["lifecycle"], "running");
        assert_eq!(value["ownership"], "owned");
        assert_eq!(value["inbound"], "ws_healthy");
        assert_eq!(value["send"], "risk_controlled");
        assert_eq!(value["credential_generation"], 7);
        assert_eq!(value["lease_epoch"], 42);
    }
}
