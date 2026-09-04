//! Foundation for the native `DyAuthReply` account runtime.
//!
//! This crate is deliberately unable to connect to Douyin or send messages in
//! its first migration gate.  It only establishes process identity, state,
//! durable correctness primitives, and a non-production health surface.

pub mod config;
pub mod health;
pub mod identity;
pub mod protocol;
pub mod state;
pub mod store;

pub const CORE_SCHEMA_VERSION: u32 = 1;
pub const PROTOCOL_MODE: &str = "shadow-disabled";
