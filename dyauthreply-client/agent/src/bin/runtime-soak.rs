//! Network-free repeated-wave load and leak probe over the real runtime coordinator.

use anyhow::{bail, Context, Result};
use dy_agent::{
    config::{RuntimeConfig, StorageConfig},
    health::{HealthHandle, HealthResponse, StorageHealthResponse, StorageStartupSnapshot},
    protocol::fixtures::verify_embedded_corpus,
    runtime::{
        model::{AccountId, AdmissionResult, WorkEnvelope, WorkFence, WorkKind},
        supervisor::{AccountSpec, RuntimeHandle},
    },
    state::{AccountRuntimeState, InboundState, LifecycleState, OwnershipState, SendCapability},
    storage::{retention::DiskPressure, RecoveryReport, SegmentCatalog, SegmentStore, ZstdCodec},
    store::CoreStore,
};
use serde::Serialize;
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::{task::JoinSet, time::timeout};
use uuid::Uuid;

const PRODUCTION_ACCOUNT_CAP: usize = 512;
const WAIT_LIMIT: Duration = Duration::from_secs(30);

#[derive(Clone)]
struct Options {
    accounts: usize,
    cycles: usize,
    events_per_account: usize,
    producers: usize,
    max_rss_mib: u64,
    max_growth_mib: u64,
    max_tail_growth_mib: u64,
    max_cpu_cores: f64,
    max_drain_ms: u64,
    max_backpressure_per_event: u64,
    max_thread_growth: usize,
}

#[derive(Clone, Copy, Default, Serialize)]
struct ProcessSample {
    rss_bytes: Option<u64>,
    cpu_ms: Option<u64>,
    threads: Option<usize>,
}

#[derive(Serialize)]
struct CycleReport {
    cycle: usize,
    install_ms: u64,
    dispatch_ms: u64,
    remove_ms: u64,
    accepted: u64,
    backpressure_retries: u64,
    rss_after_remove_bytes: Option<u64>,
    threads_after_remove: Option<usize>,
}

#[derive(Serialize)]
struct Report {
    accounts: usize,
    account_hard_cap: usize,
    hard_cap_rejected: bool,
    cycles: usize,
    events_per_account: usize,
    producers: usize,
    accepted: u64,
    backpressure_retries: u64,
    queue_rejected: u64,
    dispatched_total: u64,
    queue_peak: usize,
    scheduled_timer_peak: usize,
    rss_start_bytes: Option<u64>,
    rss_peak_bytes: Option<u64>,
    rss_final_bytes: Option<u64>,
    rss_growth_after_warm_bytes: Option<i64>,
    rss_tail_growth_bytes: Option<i64>,
    thread_start: Option<usize>,
    thread_peak: Option<usize>,
    thread_final: Option<usize>,
    thread_growth: Option<i64>,
    cpu_ms: Option<u64>,
    elapsed_ms: u64,
    cpu_cores_used: Option<f64>,
    throughput_events_per_second: f64,
    drain_ms: u64,
    post_drain_actor_count: usize,
    post_drain_timer_tasks: usize,
    post_drain_queue_depth: usize,
    post_drain_unresolved: usize,
    resource_sampling: bool,
    network_requests: u64,
    passed: bool,
    cycle_reports: Vec<CycleReport>,
}

struct RunStats {
    accepted: u64,
    backpressure_retries: u64,
    queue_peak: usize,
    timer_peak: usize,
    rss_peak: Option<u64>,
    thread_peak: Option<usize>,
    after_warm_rss: Option<u64>,
    cycles: Vec<CycleReport>,
}

#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() -> Result<()> {
    let options = parse_args()?;
    let root = temporary_root()?;
    let result = run(&root, &options).await;
    let cleanup = fs::remove_dir_all(&root);
    if result.is_ok() {
        cleanup.context("cannot remove soak data")?;
    }
    let report = result?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    if !report.passed {
        bail!("runtime soak thresholds failed");
    }
    Ok(())
}

async fn run(root: &Path, options: &Options) -> Result<Report> {
    let start_sample = process_sample();
    let started = Instant::now();
    let (runtime, _core) = start_runtime(root)?;
    let stats = execute_cycles(&runtime, options, start_sample).await?;
    let before_drain = runtime.snapshot().await;
    let drain_started = Instant::now();
    let stopped = timeout(WAIT_LIMIT, runtime.drain())
        .await
        .context("runtime drain timed out")??;
    let drain_ms = millis(drain_started.elapsed());
    let final_sample = process_sample();
    let elapsed_ms = millis(started.elapsed()).max(1);
    let cpu_ms = sample_delta(start_sample.cpu_ms, final_sample.cpu_ms);
    let cpu_cores = cpu_ms.and_then(|cpu| ratio(cpu, elapsed_ms));
    let thread_growth = signed_usize_delta(start_sample.threads, final_sample.threads);
    let rss_growth = signed_delta(stats.after_warm_rss, final_sample.rss_bytes);
    let rss_tail_growth = tail_rss_growth(&stats.cycles);
    let resource_sampling = start_sample.rss_bytes.is_some()
        && final_sample.rss_bytes.is_some()
        && start_sample.cpu_ms.is_some()
        && final_sample.cpu_ms.is_some();
    let rss_ok = stats
        .rss_peak
        .is_none_or(|rss| rss <= mib(options.max_rss_mib));
    let growth_ok = rss_growth.is_none_or(|growth| {
        growth <= i64::try_from(mib(options.max_growth_mib)).unwrap_or(i64::MAX)
    });
    let tail_growth_ok = rss_tail_growth.is_none_or(|growth| {
        growth <= i64::try_from(mib(options.max_tail_growth_mib)).unwrap_or(i64::MAX)
    });
    let cpu_ok = cpu_cores.is_none_or(|cores| cores <= options.max_cpu_cores);
    let threads_ok = thread_growth.is_none_or(|growth| {
        growth <= i64::try_from(options.max_thread_growth).unwrap_or(i64::MAX)
    });
    let state_ok = stopped.actor_count == 0
        && stopped.central_timer_tasks == 0
        && stopped.queue_depth == 0
        && stopped.unresolved_work == 0;
    let passed = resource_sampling
        && rss_ok
        && growth_ok
        && tail_growth_ok
        && cpu_ok
        && threads_ok
        && drain_ms <= options.max_drain_ms
        && before_drain.queue_rejected == stats.backpressure_retries
        && stats.backpressure_retries
            <= stats
                .accepted
                .saturating_mul(options.max_backpressure_per_event)
        && state_ok;

    Ok(Report {
        accounts: options.accounts,
        account_hard_cap: PRODUCTION_ACCOUNT_CAP,
        hard_cap_rejected: options.accounts == PRODUCTION_ACCOUNT_CAP,
        cycles: options.cycles,
        events_per_account: options.events_per_account,
        producers: options.producers,
        accepted: stats.accepted,
        backpressure_retries: stats.backpressure_retries,
        queue_rejected: before_drain.queue_rejected,
        dispatched_total: before_drain.dispatched_total,
        queue_peak: stats.queue_peak,
        scheduled_timer_peak: stats.timer_peak,
        rss_start_bytes: start_sample.rss_bytes,
        rss_peak_bytes: stats.rss_peak,
        rss_final_bytes: final_sample.rss_bytes,
        rss_growth_after_warm_bytes: rss_growth,
        rss_tail_growth_bytes: rss_tail_growth,
        thread_start: start_sample.threads,
        thread_peak: stats.thread_peak,
        thread_final: final_sample.threads,
        thread_growth,
        cpu_ms,
        elapsed_ms,
        cpu_cores_used: cpu_cores,
        throughput_events_per_second: ratio(stats.accepted, elapsed_ms)
            .map_or(0.0, |ratio| ratio * 1000.0),
        drain_ms,
        post_drain_actor_count: stopped.actor_count,
        post_drain_timer_tasks: stopped.central_timer_tasks,
        post_drain_queue_depth: stopped.queue_depth,
        post_drain_unresolved: stopped.unresolved_work,
        resource_sampling,
        network_requests: 0,
        passed,
        cycle_reports: stats.cycles,
    })
}

async fn execute_cycles(
    runtime: &RuntimeHandle,
    options: &Options,
    start_sample: ProcessSample,
) -> Result<RunStats> {
    let mut accepted_total = 0_u64;
    let mut backpressure_retries = 0_u64;
    let mut queue_peak = 0;
    let mut timer_peak = 0;
    let mut rss_peak = start_sample.rss_bytes;
    let mut thread_peak = start_sample.threads;
    let mut after_warm = None;
    let mut cycles = Vec::with_capacity(options.cycles);

    for cycle in 0..options.cycles {
        let installed = Instant::now();
        install_accounts(runtime, options.accounts, cycle + 1).await?;
        let install_ms = millis(installed.elapsed());
        let hard_cap_rejected = if options.accounts == PRODUCTION_ACCOUNT_CAP {
            runtime
                .upsert_account(account_spec(PRODUCTION_ACCOUNT_CAP, cycle + 1)?)
                .await
                .is_err()
        } else {
            true
        };
        if !hard_cap_rejected {
            bail!("runtime accepted an account above its configured hard cap");
        }

        let dispatched_before = runtime.snapshot().await.dispatched_total;
        let dispatch_started = Instant::now();
        let (accepted, retries) = enqueue_wave(runtime, options, cycle).await?;
        accepted_total = accepted_total.saturating_add(accepted);
        backpressure_retries = backpressure_retries.saturating_add(retries);
        wait_for_dispatches(runtime, dispatched_before.saturating_add(accepted)).await?;
        let dispatch_ms = millis(dispatch_started.elapsed());
        let active = runtime.snapshot().await;
        queue_peak = queue_peak.max(active.queue_high_water);
        timer_peak = timer_peak.max(active.scheduled_timer_count);
        let active_sample = process_sample();
        update_peak(&mut rss_peak, active_sample.rss_bytes);
        update_peak(&mut thread_peak, active_sample.threads);

        let removed = Instant::now();
        remove_accounts(runtime, options.accounts).await?;
        wait_for_account_cleanup(runtime).await?;
        let remove_ms = millis(removed.elapsed());
        tokio::time::sleep(Duration::from_millis(50)).await;
        let after_remove = process_sample();
        update_peak(&mut rss_peak, after_remove.rss_bytes);
        update_peak(&mut thread_peak, after_remove.threads);
        if cycle == 0 {
            after_warm = after_remove.rss_bytes;
        }
        cycles.push(CycleReport {
            cycle: cycle + 1,
            install_ms,
            dispatch_ms,
            remove_ms,
            accepted,
            backpressure_retries: retries,
            rss_after_remove_bytes: after_remove.rss_bytes,
            threads_after_remove: after_remove.threads,
        });
    }

    Ok(RunStats {
        accepted: accepted_total,
        backpressure_retries,
        queue_peak,
        timer_peak,
        rss_peak,
        thread_peak,
        after_warm_rss: after_warm,
        cycles,
    })
}

async fn install_accounts(
    runtime: &RuntimeHandle,
    accounts: usize,
    generation: usize,
) -> Result<()> {
    for account in 0..accounts {
        runtime
            .upsert_account(account_spec(account, generation)?)
            .await?;
    }
    Ok(())
}

fn account_spec(account: usize, generation: usize) -> Result<AccountSpec> {
    let generation = u64::try_from(generation)?;
    Ok(AccountSpec {
        account_id: AccountId::new(format!("soak-{account:05}"))?,
        actor_generation: generation,
        state: AccountRuntimeState {
            lifecycle: LifecycleState::Running,
            ownership: OwnershipState::Owned,
            inbound: InboundState::WsHealthy,
            send: SendCapability::Sendable,
            credential_generation: generation,
            lease_epoch: generation,
        },
    })
}

async fn enqueue_wave(
    runtime: &RuntimeHandle,
    options: &Options,
    cycle: usize,
) -> Result<(u64, u64)> {
    let producers = options.producers.min(options.accounts).max(1);
    let mut tasks = JoinSet::new();
    for producer in 0..producers {
        let runtime = runtime.clone();
        let options = options.clone();
        tasks.spawn(async move {
            let mut accepted = 0_u64;
            let mut backpressure = 0_u64;
            for event in 0..options.events_per_account {
                for account in (producer..options.accounts).step_by(producers) {
                    let generation = u64::try_from(cycle + 1)?;
                    let kind = match event % 3 {
                        0 => WorkKind::ManualSend,
                        1 => WorkKind::AutomaticReply,
                        _ => WorkKind::InboundWakeup,
                    };
                    let account_id = AccountId::new(format!("soak-{account:05}"))?;
                    let durable_id = format!("soak-{cycle:03}-{account:05}-{event:05}");
                    loop {
                        let result = runtime
                            .enqueue(WorkEnvelope::new(
                                account_id.clone(),
                                durable_id.clone(),
                                kind,
                                WorkFence {
                                    actor_generation: generation,
                                    credential_generation: generation,
                                    lease_epoch: generation,
                                },
                                runtime.monotonic_now_ms(),
                            ))
                            .await;
                        match result {
                            AdmissionResult::Accepted => break,
                            AdmissionResult::Full { .. } => {
                                backpressure = backpressure.saturating_add(1);
                                if backpressure
                                    > u64::try_from(options.accounts * options.events_per_account)?
                                        .saturating_mul(options.max_backpressure_per_event)
                                {
                                    bail!("bounded backpressure did not recover");
                                }
                                tokio::time::sleep(Duration::from_millis(1)).await;
                            }
                            _ => bail!("soak ingress rejected: {result:?}"),
                        }
                    }
                    accepted = accepted.saturating_add(1);
                }
            }
            Ok::<(u64, u64), anyhow::Error>((accepted, backpressure))
        });
    }
    let mut accepted = 0_u64;
    let mut backpressure = 0_u64;
    while let Some(result) = tasks.join_next().await {
        let (task_accepted, task_backpressure) = result??;
        accepted = accepted.saturating_add(task_accepted);
        backpressure = backpressure.saturating_add(task_backpressure);
    }
    Ok((accepted, backpressure))
}

async fn remove_accounts(runtime: &RuntimeHandle, accounts: usize) -> Result<()> {
    for account in 0..accounts {
        let id = AccountId::new(format!("soak-{account:05}"))?;
        anyhow::ensure!(
            runtime.remove_account(&id).await?,
            "account disappeared during soak"
        );
    }
    Ok(())
}

async fn wait_for_dispatches(runtime: &RuntimeHandle, minimum: u64) -> Result<()> {
    timeout(WAIT_LIMIT, async {
        loop {
            let snapshot = runtime.snapshot().await;
            if snapshot.dispatched_total >= minimum && snapshot.queue_depth == 0 {
                return;
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await
    .context("scheduler stopped making progress")?;
    Ok(())
}

async fn wait_for_account_cleanup(runtime: &RuntimeHandle) -> Result<()> {
    timeout(WAIT_LIMIT, async {
        loop {
            let snapshot = runtime.snapshot().await;
            if snapshot.actor_count == 0
                && snapshot.queue_depth == 0
                && snapshot.scheduled_timer_count <= 3
            {
                return;
            }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    })
    .await
    .context("account tasks/timers did not return to baseline")?;
    Ok(())
}

fn start_runtime(root: &Path) -> Result<(RuntimeHandle, Arc<CoreStore>)> {
    fs::create_dir_all(root)?;
    let core = Arc::new(CoreStore::open(root)?);
    let catalog: Arc<dyn SegmentCatalog> = core.clone();
    let storage = StorageConfig::recommended();
    let segments = SegmentStore::open_with_codec(
        root.join("segments"),
        storage.segment_policies.clone(),
        catalog,
        Arc::new(ZstdCodec::default()),
    )?;
    let health = HealthHandle::new(HealthResponse::foundation(
        Uuid::new_v4(),
        Uuid::new_v4(),
        &verify_embedded_corpus()?,
        StorageHealthResponse::startup(&StorageStartupSnapshot {
            pressure: DiskPressure::Normal,
            disposable_writes_allowed: true,
            background_work_paused: false,
            sealed_segment_count: 0,
            sealed_segment_bytes: 0,
            active_segment_count: 0,
            cleanup_deleted_segments: 0,
            recovery: RecoveryReport::default(),
        }),
    ));
    let runtime = RuntimeHandle::start(
        RuntimeConfig::recommended(),
        "runtime-soak",
        core.clone(),
        segments,
        storage,
        health,
    )?;
    Ok((runtime, core))
}

fn temporary_root() -> Result<PathBuf> {
    let path = std::env::temp_dir().join(format!("dy-agent-soak-{}", Uuid::new_v4().simple()));
    fs::create_dir(&path)?;
    Ok(path)
}

fn process_sample() -> ProcessSample {
    ProcessSample {
        rss_bytes: current_rss_bytes(),
        cpu_ms: process_cpu_ms(),
        threads: process_threads(),
    }
}

#[cfg(target_os = "linux")]
fn current_rss_bytes() -> Option<u64> {
    let status = fs::read_to_string("/proc/self/status").ok()?;
    status.lines().find_map(|line| {
        let kib = line
            .strip_prefix("VmRSS:")?
            .split_whitespace()
            .next()?
            .parse::<u64>()
            .ok()?;
        kib.checked_mul(1024)
    })
}

#[cfg(all(unix, not(target_os = "linux")))]
fn current_rss_bytes() -> Option<u64> {
    let output = Command::new("ps")
        .args(["-o", "rss=", "-p", &std::process::id().to_string()])
        .output()
        .ok()?;
    let kib = std::str::from_utf8(&output.stdout)
        .ok()?
        .trim()
        .parse::<u64>()
        .ok()?;
    kib.checked_mul(1024)
}

#[cfg(not(unix))]
fn current_rss_bytes() -> Option<u64> {
    None
}

#[cfg(target_os = "linux")]
fn process_threads() -> Option<usize> {
    let status = fs::read_to_string("/proc/self/status").ok()?;
    status.lines().find_map(|line| {
        line.strip_prefix("Threads:")?
            .split_whitespace()
            .next()?
            .parse()
            .ok()
    })
}

#[cfg(all(unix, not(target_os = "linux")))]
fn process_threads() -> Option<usize> {
    let output = Command::new("ps")
        .args(["-M", "-p", &std::process::id().to_string()])
        .output()
        .ok()?;
    output.status.success().then(|| {
        String::from_utf8_lossy(&output.stdout)
            .lines()
            .count()
            .saturating_sub(1)
    })
}

#[cfg(not(unix))]
fn process_threads() -> Option<usize> {
    None
}

#[cfg(unix)]
fn process_cpu_ms() -> Option<u64> {
    let output = Command::new("ps")
        .args(["-o", "time=", "-p", &std::process::id().to_string()])
        .output()
        .ok()?;
    parse_cpu_time(std::str::from_utf8(&output.stdout).ok()?.trim())
}

#[cfg(not(unix))]
fn process_cpu_ms() -> Option<u64> {
    None
}

fn parse_cpu_time(value: &str) -> Option<u64> {
    let (days, clock) = if let Some((days, clock)) = value.split_once('-') {
        (days.parse::<u64>().ok()?, clock)
    } else {
        (0, value)
    };
    let parts = clock.split(':').collect::<Vec<_>>();
    let (hours, minutes, seconds_ms) = match parts.as_slice() {
        [minutes, seconds] => (0, minutes.parse::<u64>().ok()?, parse_seconds_ms(seconds)?),
        [hours, minutes, seconds] => (
            hours.parse::<u64>().ok()?,
            minutes.parse::<u64>().ok()?,
            parse_seconds_ms(seconds)?,
        ),
        _ => return None,
    };
    ((days.checked_mul(24)?.checked_add(hours)?)
        .checked_mul(60)?
        .checked_add(minutes)?)
    .checked_mul(60_000)?
    .checked_add(seconds_ms)
}

fn parse_seconds_ms(value: &str) -> Option<u64> {
    let (seconds, fraction) = value.split_once('.').unwrap_or((value, ""));
    let seconds = seconds.parse::<u64>().ok()?;
    if !fraction.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let mut millis = fraction.chars().take(3).collect::<String>();
    while millis.len() < 3 {
        millis.push('0');
    }
    seconds
        .checked_mul(1000)?
        .checked_add(if millis.is_empty() {
            0
        } else {
            millis.parse().ok()?
        })
}

fn parse_args() -> Result<Options> {
    let mut options = Options {
        accounts: 300,
        cycles: 5,
        events_per_account: 20,
        producers: 64,
        max_rss_mib: 512,
        max_growth_mib: 128,
        max_tail_growth_mib: 16,
        max_cpu_cores: 8.0,
        max_drain_ms: 10_000,
        max_backpressure_per_event: 100,
        max_thread_growth: 0,
    };
    let mut args = std::env::args().skip(1);
    while let Some(name) = args.next() {
        let value = args
            .next()
            .with_context(|| format!("missing value for {name}"))?;
        match name.as_str() {
            "--accounts" => options.accounts = value.parse()?,
            "--cycles" => options.cycles = value.parse()?,
            "--events-per-account" => options.events_per_account = value.parse()?,
            "--producers" => options.producers = value.parse()?,
            "--max-rss-mib" => options.max_rss_mib = value.parse()?,
            "--max-growth-mib" => options.max_growth_mib = value.parse()?,
            "--max-tail-growth-mib" => options.max_tail_growth_mib = value.parse()?,
            "--max-cpu-cores" => options.max_cpu_cores = value.parse()?,
            "--max-drain-ms" => options.max_drain_ms = value.parse()?,
            "--max-backpressure-per-event" => options.max_backpressure_per_event = value.parse()?,
            "--max-thread-growth" => options.max_thread_growth = value.parse()?,
            _ => bail!("unknown argument {name:?}"),
        }
    }
    let total = options
        .accounts
        .checked_mul(options.cycles)
        .and_then(|value| value.checked_mul(options.events_per_account))
        .context("soak work count overflow")?;
    if !(1..=PRODUCTION_ACCOUNT_CAP).contains(&options.accounts)
        || !(1..=100).contains(&options.cycles)
        || !(1..=10_000).contains(&options.events_per_account)
        || !(1..=PRODUCTION_ACCOUNT_CAP).contains(&options.producers)
        || total > 10_000_000
        || options.max_rss_mib == 0
        || options.max_growth_mib == 0
        || options.max_tail_growth_mib == 0
        || !(0.1..=64.0).contains(&options.max_cpu_cores)
        || options.max_drain_ms == 0
        || options.max_backpressure_per_event == 0
    {
        bail!("runtime soak arguments exceed bounded limits");
    }
    Ok(options)
}

fn millis(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}

const fn mib(value: u64) -> u64 {
    value.saturating_mul(1024 * 1024)
}

fn update_peak<T: Ord + Copy>(peak: &mut Option<T>, value: Option<T>) {
    if let Some(value) = value {
        *peak = Some(peak.map_or(value, |old| old.max(value)));
    }
}

fn sample_delta(start: Option<u64>, end: Option<u64>) -> Option<u64> {
    Some(end?.saturating_sub(start?))
}

fn ratio(numerator: u64, denominator: u64) -> Option<f64> {
    let numerator = u32::try_from(numerator).ok()?;
    let denominator = u32::try_from(denominator).ok()?;
    (denominator != 0).then(|| f64::from(numerator) / f64::from(denominator))
}

fn signed_delta(start: Option<u64>, end: Option<u64>) -> Option<i64> {
    let start = i128::from(start?);
    let end = i128::from(end?);
    i64::try_from(end - start).ok()
}

fn signed_usize_delta(start: Option<usize>, end: Option<usize>) -> Option<i64> {
    let start = i128::try_from(start?).ok()?;
    let end = i128::try_from(end?).ok()?;
    i64::try_from(end - start).ok()
}

fn tail_rss_growth(cycles: &[CycleReport]) -> Option<i64> {
    let tail = &cycles[cycles.len().saturating_sub(10)..];
    signed_delta(
        tail.first()?.rss_after_remove_bytes,
        tail.last()?.rss_after_remove_bytes,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpu_time_parser_and_resource_deltas_are_bounded() {
        assert_eq!(parse_cpu_time("01:02.50"), Some(62_500));
        assert_eq!(parse_cpu_time("2:01:02"), Some(7_262_000));
        assert_eq!(parse_cpu_time("1-00:00:01"), Some(86_401_000));
        assert_eq!(parse_cpu_time("broken"), None);
        assert_eq!(sample_delta(Some(10), Some(15)), Some(5));
        assert_eq!(signed_delta(Some(20), Some(15)), Some(-5));
        assert_eq!(signed_usize_delta(Some(5), Some(5)), Some(0));
        let rows = (0..12)
            .map(|cycle| CycleReport {
                cycle,
                install_ms: 0,
                dispatch_ms: 0,
                remove_ms: 0,
                accepted: 0,
                backpressure_retries: 0,
                rss_after_remove_bytes: Some(cycle as u64 * 10),
                threads_after_remove: Some(5),
            })
            .collect::<Vec<_>>();
        assert_eq!(tail_rss_growth(&rows), Some(90));
    }

    #[test]
    fn argument_limits_reject_account_counts_above_runtime_cap() {
        assert_eq!(
            PRODUCTION_ACCOUNT_CAP,
            RuntimeConfig::recommended().heartbeat_max_accounts
        );
    }
}
