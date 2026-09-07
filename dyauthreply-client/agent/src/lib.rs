//! Native `DyAuthReply` account protocol, durable messaging and event runtime.
//!
//! Explicit messaging configuration enables licensed live account execution;
//! absent configuration leaves the offline/shadow foundation active. Client-wide
//! engine ownership and signed account leases precede hosted business work.

pub mod config;
pub mod credential_store;
pub mod engine_gate;
pub mod health;
pub mod identity;
pub mod license;
pub mod protocol;
pub mod runtime;
pub mod state;
pub mod storage;
pub mod store;

pub const CORE_SCHEMA_VERSION: u32 = 4;
pub const PROTOCOL_MODE: &str = "shadow-disabled";

pub mod control_plane;

pub mod workbench;

pub mod business;

pub mod desktop_ipc;

pub mod audit;

pub mod onboarding;

pub mod admin;
pub mod capacity;
pub mod quick_auth;
