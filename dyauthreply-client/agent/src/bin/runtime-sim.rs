//! Deterministic, network-free runtime capacity probe.

use anyhow::{bail, Context, Result};
use dy_agent::{
    runtime::{
        fair_queue::{FairQueue, FairQueueConfig},
        model::{AccountId, WorkClassCapacities, WorkEnvelope, WorkFence, WorkKind},
    },
    PROTOCOL_MODE,
};
use serde::Serialize;

#[derive(Serialize)]
struct Report {
    accounts: usize,
    events_per_account: usize,
    accepted: u64,
    dispatched: u64,
    queue_peak: usize,
    p95_scheduler_ms: u64,
    exact_accounting: bool,
    protocol_mode: &'static str,
    network_requests: u64,
}

fn main() -> Result<()> {
    let (accounts, events_per_account) = parse_args()?;
    let total = accounts
        .checked_mul(events_per_account)
        .context("simulation work count overflow")?;
    if total > 1_000_000 {
        bail!("simulation total events must not exceed 1000000");
    }
    let capacity = total.next_power_of_two().max(16);
    let mut queue = FairQueue::new(FairQueueConfig {
        global_capacity: capacity,
        per_account_capacity: events_per_account.max(1),
        class_capacities: WorkClassCapacities {
            manual: capacity,
            automatic: capacity,
            background: capacity,
        },
        max_manual_burst: 8,
    })
    .context("invalid simulation queue")?;

    for item in 0..events_per_account {
        for account in 0..accounts {
            let kind = match item % 3 {
                0 => WorkKind::ManualSend,
                1 => WorkKind::AutomaticReply,
                _ => WorkKind::InboundWakeup,
            };
            let work = WorkEnvelope::new(
                AccountId::new(format!("simulation-{account:05}"))?,
                format!("durable-{account:05}-{item:05}"),
                kind,
                WorkFence {
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                },
                0,
            );
            if !queue.try_enqueue(work).is_admitted() {
                bail!("bounded simulation queue unexpectedly rejected work");
            }
        }
    }

    let mut latencies = Vec::with_capacity(total);
    for now_ms in 1..=u64::try_from(total).context("work count does not fit u64")? {
        queue
            .pop(now_ms)
            .context("simulation queue lost accepted work")?;
        latencies.push(now_ms);
    }
    latencies.sort_unstable();
    let p95_index = latencies
        .len()
        .saturating_mul(95)
        .div_ceil(100)
        .saturating_sub(1);
    let snapshot = queue.snapshot();
    let report = Report {
        accounts,
        events_per_account,
        accepted: snapshot.accepted,
        dispatched: snapshot.dispatched,
        queue_peak: snapshot.peak_depth,
        p95_scheduler_ms: latencies.get(p95_index).copied().unwrap_or_default(),
        exact_accounting: snapshot.has_exact_work_accounting(),
        protocol_mode: PROTOCOL_MODE,
        network_requests: 0,
    };
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

fn parse_args() -> Result<(usize, usize)> {
    let mut accounts = 300_usize;
    let mut events_per_account = 1_usize;
    let mut arguments = std::env::args().skip(1);
    while let Some(argument) = arguments.next() {
        let value = arguments
            .next()
            .with_context(|| format!("missing value for {argument}"))?;
        match argument.as_str() {
            "--accounts" => accounts = value.parse().context("invalid --accounts value")?,
            "--events-per-account" => {
                events_per_account = value
                    .parse()
                    .context("invalid --events-per-account value")?;
            }
            _ => bail!("unknown argument {argument:?}"),
        }
    }
    if !(1..=10_000).contains(&accounts) || !(1..=10_000).contains(&events_per_account) {
        bail!("simulation values must be in 1..=10000");
    }
    Ok((accounts, events_per_account))
}
