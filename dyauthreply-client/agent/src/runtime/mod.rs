//! Bounded, event-driven multi-account runtime primitives.
//!
//! The default no-executor runtime remains `shadow-disabled`; explicitly configured
//! native executors perform protocol I/O only under account control and signed leases.
//! Queues contain only durable opaque IDs and generation/fence metadata.

pub mod account;
pub mod breaker;
pub mod executor;
pub mod fair_queue;
pub mod heartbeat;
pub mod hosted;
pub mod inbound;
pub mod inbound_policy;
pub mod lanes;
pub mod messaging;
pub mod metrics;
pub mod model;
pub mod rules;
pub mod storage_maintenance;
pub mod supervisor;
pub mod timer;
