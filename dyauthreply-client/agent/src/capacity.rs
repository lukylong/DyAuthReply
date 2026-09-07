//! Conservative hardware estimate, distinct from measured CPU load or platform entitlement.
use crate::{
    config::{RuntimeConfig, StorageConfig},
    health::{HealthHandle, HealthResponse, StorageHealthResponse, StorageStartupSnapshot},
    protocol::fixtures::verify_embedded_corpus,
    runtime::{
        messaging::ManualService,
        model::{AccountId, AdmissionResult, WorkEnvelope, WorkFence, WorkKind},
        supervisor::{AccountSpec, RuntimeHandle},
    },
    state::{AccountRuntimeState, InboundState, LifecycleState, OwnershipState, SendCapability},
    storage::{retention::DiskPressure, RecoveryReport, SegmentCatalog, SegmentStore, ZstdCodec},
    store::CoreStore,
};
use anyhow::{Context, Result};
use axum::{
    extract::State,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tokio::time::timeout;
use uuid::Uuid;

const MIB: u64 = 1024 * 1024;
pub const VALIDATED_CEILING: u32 = 300;
const BENCHMARK_WAVES: u64 = 20;
const BENCHMARK_TIMEOUT: Duration = Duration::from_secs(8);
const BALANCED_ACCOUNT_MEMORY: u64 = 16 * MIB;
const CONSERVATIVE_ACCOUNT_MEMORY: u64 = 24 * MIB;
const BENCHMARK_MODEL: &str = "measured-runtime-v2";

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    #[serde(default)]
    pub conservative: bool,
    pub manual_limit: Option<u32>,
}

struct Meter {
    system: sysinfo::System,
    sampled: Instant,
    cpu_ready: bool,
}
pub struct Capacity {
    meter: Mutex<Meter>,
    db: Mutex<rusqlite::Connection>,
    benchmarking: AtomicBool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct BenchmarkResult {
    pub estimated_accounts: u32,
    pub tested_accounts: u32,
    pub tested_events: u64,
    pub duration_ms: u64,
    pub completed_at_ms: u64,
    pub model_version: String,
    #[serde(default)]
    pub scheduler_events_per_second: u64,
    #[serde(default)]
    pub cpu_usage_percent: u32,
    #[serde(default)]
    pub available_memory_bytes: u64,
    #[serde(default)]
    pub cpu_limit_accounts: u32,
    #[serde(default)]
    pub memory_limit_accounts: u32,
    #[serde(default)]
    pub scheduler_limit_accounts: u32,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct MeasuredCapacity {
    estimated: u32,
    cpu_limit: u32,
    memory_limit: u32,
    scheduler_limit: u32,
}
#[derive(Serialize)]
pub struct Estimate {
    pub logical_cpus: u32,
    pub total_memory_bytes: u64,
    pub available_memory_bytes: u64,
    pub hardware_limit: u32,
    pub validated_ceiling: u32,
    pub effective_limit: u32,
    pub hosted_accounts: u32,
    pub occupancy_percent: Option<u32>,
    pub memory_headroom_accounts: u32,
    pub recommended_min_accounts: u32,
    pub recommended_max_accounts: u32,
    pub limiting_factor: &'static str,
    pub policy: Policy,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct MemorySample {
    available: u64,
    used: u64,
    pressure_percent: u32,
    source: &'static str,
}

fn normalize_memory_sample(
    total: u64,
    reported_available: u64,
    reported_used: u64,
) -> MemorySample {
    let used = reported_used.min(total);
    let derived_available = total.saturating_sub(used);
    let (available, source) = if reported_used > 0 && derived_available > reported_available {
        // sysinfo 0.37 on macOS subtracts compressor pages from reclaimable pages and may
        // under-report under compression. total-used is a conservative fallback.
        (derived_available, "derived_total_minus_used")
    } else {
        (reported_available.min(total), "system_available")
    };
    let pressure_percent = if total == 0 {
        0
    } else {
        u32::try_from(
            total
                .saturating_sub(available)
                .saturating_mul(100)
                .div_ceil(total),
        )
        .unwrap_or(100)
        .min(100)
    };
    MemorySample {
        available,
        used,
        pressure_percent,
        source,
    }
}

fn measured_capacity(
    cpus: u32,
    total_memory: u64,
    available_memory: u64,
    cpu_usage_percent: u32,
    scheduler_events_per_second: u64,
    conservative: bool,
) -> MeasuredCapacity {
    let per_account_memory = if conservative {
        CONSERVATIVE_ACCOUNT_MEMORY
    } else {
        BALANCED_ACCOUNT_MEMORY
    };
    let system_reserve = (total_memory / 10).max(1024 * MIB);
    let memory_limit =
        u32::try_from(available_memory.saturating_sub(system_reserve) / per_account_memory)
            .unwrap_or(u32::MAX);

    let accounts_per_cpu = if conservative { 12 } else { 25 };
    let idle_percent = 100_u32.saturating_sub(cpu_usage_percent.min(100));
    let cpu_limit = cpus
        .saturating_mul(accounts_per_cpu)
        .saturating_mul(idle_percent.max(20))
        .checked_div(75)
        .unwrap_or(0)
        .min(cpus.saturating_mul(accounts_per_cpu));

    // Reserve 75% of measured scheduler throughput for protocol parsing, signing,
    // storage and UI. The remaining 25% budgets two peak work items/account/second.
    let scheduler_limit = u32::try_from(scheduler_events_per_second / 8)
        .unwrap_or(u32::MAX)
        .min(VALIDATED_CEILING);
    let raw = memory_limit
        .min(cpu_limit)
        .min(scheduler_limit)
        .clamp(1, VALIDATED_CEILING);
    let estimated = if raw >= 10 { raw / 5 * 5 } else { raw };
    MeasuredCapacity {
        estimated,
        cpu_limit,
        memory_limit,
        scheduler_limit,
    }
}

/// Calculates a planning estimate; coefficients deliberately include unmeasured TLS/session headroom.
#[must_use]
pub fn estimate(cpus: u32, total: u64, available: u64, hosted: u32, policy: Policy) -> Estimate {
    let divisor = if policy.conservative { 8 } else { 4 };
    let budget = (total / divisor).min(total.saturating_sub(2 * 1024 * MIB));
    let memory_limit = u32::try_from(budget / (32 * MIB)).unwrap_or(u32::MAX);
    let cpu_limit = cpus.saturating_mul(if policy.conservative { 12 } else { 25 });
    let range_cpu_min = if policy.conservative { 6 } else { 12 };
    let range_cpu_max = if policy.conservative { 12 } else { 25 };
    let recommended_min = cpus
        .saturating_mul(range_cpu_min)
        .min(u32::try_from(budget / (32 * MIB)).unwrap_or(u32::MAX))
        .min(VALIDATED_CEILING);
    let recommended_max = cpus
        .saturating_mul(range_cpu_max)
        .min(u32::try_from(budget / (16 * MIB)).unwrap_or(u32::MAX))
        .min(VALIDATED_CEILING)
        .max(recommended_min);
    let hardware_limit = memory_limit.min(cpu_limit);
    let effective = hardware_limit
        .min(VALIDATED_CEILING)
        .min(policy.manual_limit.unwrap_or(u32::MAX));
    let headroom =
        u32::try_from(available.saturating_sub(1024 * MIB) / (32 * MIB)).unwrap_or(u32::MAX);
    let limiting = if policy
        .manual_limit
        .is_some_and(|n| n < hardware_limit.min(VALIDATED_CEILING))
    {
        "manual"
    } else if hardware_limit > VALIDATED_CEILING {
        "validated"
    } else if memory_limit <= cpu_limit {
        "memory"
    } else {
        "cpu"
    };
    Estimate {
        logical_cpus: cpus,
        total_memory_bytes: total,
        available_memory_bytes: available,
        hardware_limit,
        validated_ceiling: VALIDATED_CEILING,
        effective_limit: effective,
        hosted_accounts: hosted,
        occupancy_percent: (effective > 0).then(|| hosted.saturating_mul(100).div_ceil(effective)),
        memory_headroom_accounts: headroom.min(effective.saturating_sub(hosted)),
        recommended_min_accounts: recommended_min,
        recommended_max_accounts: recommended_max,
        limiting_factor: limiting,
        policy,
    }
}

impl Capacity {
    /// # Errors
    /// Rejects invalid stored policy or inaccessible local settings.
    pub fn open(root: &Path) -> Result<Arc<Self>> {
        let path = root.join("capacity.sqlite3");
        anyhow::ensure!(!path.is_symlink(), "承载配置路径异常");
        let db = rusqlite::Connection::open(path)?;
        db.busy_timeout(Duration::from_secs(2))?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA journal_size_limit=1048576; CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),json TEXT NOT NULL); INSERT OR IGNORE INTO policy VALUES(1,'{\"conservative\":false,\"manual_limit\":null}'); CREATE TABLE IF NOT EXISTS benchmark(id INTEGER PRIMARY KEY CHECK(id=1),json TEXT NOT NULL);")?;
        let mut system = sysinfo::System::new();
        system.refresh_memory();
        system.refresh_cpu_usage();
        let result = Arc::new(Self {
            db: Mutex::new(db),
            meter: Mutex::new(Meter {
                system,
                sampled: Instant::now(),
                cpu_ready: false,
            }),
            benchmarking: AtomicBool::new(false),
        });
        result.policy()?;
        Ok(result)
    }
    fn policy(&self) -> Result<Policy> {
        let db = self
            .db
            .lock()
            .map_err(|_| anyhow::anyhow!("承载配置繁忙"))?;
        let raw: String = db.query_row("SELECT json FROM policy WHERE id=1", [], |r| r.get(0))?;
        let policy: Policy = serde_json::from_str(&raw)?;
        validate(&policy)?;
        Ok(policy)
    }
    /// # Errors
    /// Reads one cached hardware sample; never mutates any account policy or ownership.
    pub fn snapshot(&self, hosted: u32) -> Result<Value> {
        let policy = self.policy()?;
        let mut meter = self
            .meter
            .lock()
            .map_err(|_| anyhow::anyhow!("资源检测繁忙"))?;
        if meter.sampled.elapsed() >= Duration::from_secs(5) {
            meter.system.refresh_memory();
            meter.system.refresh_cpu_usage();
            meter.sampled = Instant::now();
            meter.cpu_ready = true;
        }
        let cpus = u32::try_from(meter.system.cpus().len()).unwrap_or(u32::MAX);
        let total = meter.system.total_memory();
        anyhow::ensure!(cpus > 0 && total > 0, "设备信息读取失败，请稍后重试");
        let memory = normalize_memory_sample(
            total,
            meter.system.available_memory(),
            meter.system.used_memory(),
        );
        let mut value =
            serde_json::to_value(estimate(cpus, total, memory.available, hosted, policy))?;
        value["reported_available_memory_bytes"] = json!(meter.system.available_memory());
        value["used_memory_bytes"] = json!(memory.used);
        value["memory_pressure_percent"] = json!(memory.pressure_percent);
        value["memory_sample_source"] = json!(memory.source);
        value["cpu_usage_percent"] = if meter.cpu_ready {
            json!(meter.system.global_cpu_usage())
        } else {
            Value::Null
        };
        value["estimated"] = json!(true);
        value["model_version"] = json!("planning-range-v2");
        value["benchmark"] = serde_json::to_value(self.benchmark_result()?)?;
        Ok(value)
    }

    fn benchmark_result(&self) -> Result<Option<BenchmarkResult>> {
        let db = self
            .db
            .lock()
            .map_err(|_| anyhow::anyhow!("性能测试记录繁忙"))?;
        let raw = db.query_row("SELECT json FROM benchmark WHERE id=1", [], |row| {
            row.get::<_, String>(0)
        });
        match raw {
            Ok(raw) => {
                let result: BenchmarkResult = serde_json::from_str(&raw)?;
                Ok((result.model_version == BENCHMARK_MODEL).then_some(result))
            }
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(error) => Err(error.into()),
        }
    }

    fn save_benchmark_result(&self, result: &BenchmarkResult) -> Result<()> {
        self.db
            .lock()
            .map_err(|_| anyhow::anyhow!("性能测试记录繁忙"))?
            .execute(
                "INSERT INTO benchmark(id,json) VALUES(1,?1) ON CONFLICT(id) DO UPDATE SET json=excluded.json",
                [serde_json::to_string(result)?],
            )?;
        Ok(())
    }

    async fn benchmark_snapshot(&self) -> Result<Value> {
        {
            let mut meter = self
                .meter
                .lock()
                .map_err(|_| anyhow::anyhow!("资源检测繁忙"))?;
            meter.system.refresh_memory();
            meter.system.refresh_cpu_usage();
        }
        tokio::time::sleep(sysinfo::MINIMUM_CPU_UPDATE_INTERVAL).await;
        {
            let mut meter = self
                .meter
                .lock()
                .map_err(|_| anyhow::anyhow!("资源检测繁忙"))?;
            meter.system.refresh_memory();
            meter.system.refresh_cpu_usage();
            meter.sampled = Instant::now();
            meter.cpu_ready = true;
        }
        self.snapshot(0)
    }

    /// Runs one bounded, network-free exercise over the real Rust scheduler and persists its result.
    /// # Errors
    /// Rejects concurrent tests and reports scheduler/cleanup failures without changing account state.
    pub async fn benchmark(&self) -> Result<BenchmarkResult> {
        anyhow::ensure!(
            self.benchmarking
                .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                .is_ok(),
            "性能测试正在进行中"
        );
        let _guard = BenchmarkGuard(&self.benchmarking);
        let snapshot = self.benchmark_snapshot().await?;
        let policy = self.policy()?;
        let candidates = [
            VALIDATED_CEILING,
            VALIDATED_CEILING * 3 / 4,
            VALIDATED_CEILING / 2,
            VALIDATED_CEILING / 4,
        ];
        let mut accepted = None;
        for accounts in candidates {
            if let Ok(trial) = exercise_runtime_candidate(accounts).await {
                accepted = Some(trial);
                break;
            }
        }
        let trial = accepted.context("本机性能测试未通过，请关闭高占用应用后重试")?;
        let cpus = u32::try_from(
            snapshot["logical_cpus"]
                .as_u64()
                .context("处理器测试数据异常")?,
        )?;
        let total_memory = snapshot["total_memory_bytes"]
            .as_u64()
            .context("内存测试数据异常")?;
        let available_memory = snapshot["available_memory_bytes"]
            .as_u64()
            .context("内存测试数据异常")?;
        let cpu_usage = snapshot["cpu_usage_percent"]
            .as_f64()
            .unwrap_or(100.0)
            .round()
            .clamp(0.0, 100.0)
            .to_string()
            .parse::<u32>()?;
        let measured = measured_capacity(
            cpus,
            total_memory,
            available_memory,
            cpu_usage,
            trial.events_per_second,
            policy.conservative,
        );
        let estimated_accounts = measured.estimated.min(trial.accounts).max(1);
        let result = BenchmarkResult {
            estimated_accounts,
            tested_accounts: trial.accounts,
            tested_events: trial.tested_events,
            duration_ms: trial.duration_ms,
            completed_at_ms: SystemTime::now()
                .duration_since(UNIX_EPOCH)?
                .as_millis()
                .try_into()?,
            model_version: BENCHMARK_MODEL.into(),
            scheduler_events_per_second: trial.events_per_second,
            cpu_usage_percent: cpu_usage,
            available_memory_bytes: available_memory,
            cpu_limit_accounts: measured.cpu_limit,
            memory_limit_accounts: measured.memory_limit,
            scheduler_limit_accounts: measured.scheduler_limit,
        };
        self.save_benchmark_result(&result)?;
        Ok(result)
    }
    /// # Errors
    /// Rejects invalid values without replacing the current policy.
    pub fn save(&self, policy: &Policy) -> Result<()> {
        validate(policy)?;
        let mut db = self
            .db
            .lock()
            .map_err(|_| anyhow::anyhow!("承载配置繁忙"))?;
        let transaction = db.transaction()?;
        transaction.execute(
            "UPDATE policy SET json=?1 WHERE id=1",
            [serde_json::to_string(policy)?],
        )?;
        transaction.execute("DELETE FROM benchmark", [])?;
        transaction.commit()?;
        Ok(())
    }
    /// # Errors
    /// Applies the same stable hardware/policy estimate used by the settings UI.
    pub fn admission_limit(&self, hosted: u32) -> Result<u32> {
        let fallback = u32::try_from(
            self.snapshot(hosted)?["effective_limit"]
                .as_u64()
                .context("承载估算异常")?,
        )
        .map_err(anyhow::Error::from)?;
        let benchmark = self
            .benchmark_result()?
            .map_or(fallback, |result| result.estimated_accounts);
        Ok(benchmark.min(self.policy()?.manual_limit.unwrap_or(u32::MAX)))
    }
}

struct BenchmarkGuard<'a>(&'a AtomicBool);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct TrialResult {
    accounts: u32,
    tested_events: u64,
    duration_ms: u64,
    events_per_second: u64,
}

impl Drop for BenchmarkGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

async fn exercise_runtime_candidate(accounts: u32) -> Result<TrialResult> {
    let root = std::env::temp_dir().join(format!("dy-capacity-{}", Uuid::new_v4().simple()));
    fs::create_dir(&root)?;
    let result = exercise_runtime_candidate_inner(&root, accounts).await;
    tokio::task::spawn_blocking({
        let root = root.clone();
        move || fs::remove_dir_all(root)
    })
    .await??;
    result
}

async fn exercise_runtime_candidate_inner(root: &Path, accounts: u32) -> Result<TrialResult> {
    let runtime = benchmark_runtime(root)?;
    let started = Instant::now();
    let workload = async {
        for account in 0..accounts {
            runtime.upsert_account(benchmark_account(account)?).await?;
        }
        for wave in 0..BENCHMARK_WAVES {
            for account in 0..accounts {
                let account_id = AccountId::new(format!("capacity-{account:04}"))?;
                let result = runtime
                    .enqueue(WorkEnvelope::new(
                        account_id,
                        format!("capacity-{wave:02}-{account:04}"),
                        match wave % 3 {
                            0 => WorkKind::ManualSend,
                            1 => WorkKind::AutomaticReply,
                            _ => WorkKind::InboundWakeup,
                        },
                        WorkFence {
                            actor_generation: 1,
                            credential_generation: 1,
                            lease_epoch: 1,
                        },
                        runtime.monotonic_now_ms(),
                    ))
                    .await;
                anyhow::ensure!(
                    matches!(result, AdmissionResult::Accepted),
                    "性能测试队列拥堵"
                );
            }
        }
        let expected = u64::from(accounts).saturating_mul(BENCHMARK_WAVES);
        timeout(BENCHMARK_TIMEOUT, async {
            loop {
                let state = runtime.snapshot().await;
                if state.dispatched_total >= expected && state.queue_depth == 0 {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(2)).await;
            }
        })
        .await
        .context("性能测试调度超时")?;
        Ok::<(), anyhow::Error>(())
    }
    .await;
    let stopped = timeout(BENCHMARK_TIMEOUT, runtime.drain()).await;
    let duration_ms = u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX);
    let stopped = stopped.context("性能测试清理超时")??;
    anyhow::ensure!(
        stopped.actor_count == 0
            && stopped.central_timer_tasks == 0
            && stopped.queue_depth == 0
            && stopped.unresolved_work == 0,
        "性能测试未完整清理"
    );
    workload?;
    drop(runtime);
    let tested_events = u64::from(accounts).saturating_mul(BENCHMARK_WAVES);
    let events_per_second = tested_events
        .saturating_mul(1000)
        .checked_div(duration_ms.max(1))
        .unwrap_or(0);
    anyhow::ensure!(events_per_second > 0, "性能测试吞吐异常");
    Ok(TrialResult {
        accounts,
        tested_events,
        duration_ms,
        events_per_second,
    })
}

fn benchmark_account(account: u32) -> Result<AccountSpec> {
    Ok(AccountSpec {
        account_id: AccountId::new(format!("capacity-{account:04}"))?,
        actor_generation: 1,
        state: AccountRuntimeState {
            lifecycle: LifecycleState::Running,
            ownership: OwnershipState::Owned,
            inbound: InboundState::WsHealthy,
            send: SendCapability::Sendable,
            credential_generation: 1,
            lease_epoch: 1,
        },
    })
}

fn benchmark_runtime(root: &Path) -> Result<RuntimeHandle> {
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
    RuntimeHandle::start(
        RuntimeConfig::recommended(),
        "capacity-benchmark",
        core,
        segments,
        storage,
        health,
    )
}

fn validate(policy: &Policy) -> Result<()> {
    anyhow::ensure!(
        policy
            .manual_limit
            .is_none_or(|v| (1..=VALIDATED_CEILING).contains(&v)),
        "手动上限须在 1–300 之间"
    );
    Ok(())
}
#[derive(Clone)]
struct App {
    capacity: Arc<Capacity>,
    service: Arc<ManualService>,
}
pub fn router(capacity: Arc<Capacity>, service: Arc<ManualService>, token: String) -> Router {
    crate::runtime::messaging::api::secure_router(
        Router::new()
            .route("/api/client/v1/runtime/capacity", get(read).put(save))
            .route(
                "/api/client/v1/runtime/capacity/benchmark",
                post(run_benchmark),
            )
            .with_state(App { capacity, service }),
        token,
    )
}
async fn response(app: App, policy: Option<Policy>) -> Response {
    let result = tokio::task::spawn_blocking(move || -> Result<Value> {
        if let Some(policy) = policy {
            app.capacity.save(&policy)?;
        }
        let count = app
            .service
            .registry()
            .list()?
            .into_iter()
            .filter(|r| !r.deleted)
            .count();
        app.capacity.snapshot(u32::try_from(count)?)
    })
    .await;
    match result {
        Ok(Ok(value)) => Json(value).into_response(),
        Ok(Err(error)) => (
            axum::http::StatusCode::BAD_REQUEST,
            Json(json!({"detail":error.to_string()})),
        )
            .into_response(),
        Err(_) => (
            axum::http::StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({"detail":"资源检测失败"})),
        )
            .into_response(),
    }
}
async fn read(State(app): State<App>) -> Response {
    response(app, None).await
}
async fn save(State(app): State<App>, Json(policy): Json<Policy>) -> Response {
    response(app, Some(policy)).await
}

async fn run_benchmark(State(app): State<App>) -> Response {
    match app.capacity.benchmark().await {
        Ok(result) => Json(json!({"benchmark":result})).into_response(),
        Err(error) => (
            axum::http::StatusCode::CONFLICT,
            Json(json!({"detail":error.to_string()})),
        )
            .into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn hardware_profiles_and_manual_limits_are_distinct_from_load() {
        let low = estimate(2, 4 * 1024 * MIB, 1024 * MIB, 2, Policy::default());
        let high = estimate(16, 32 * 1024 * MIB, 16 * 1024 * MIB, 2, Policy::default());
        assert_eq!(low.effective_limit, 32);
        assert_eq!(high.hardware_limit, 256);
        assert!(high.effective_limit > low.effective_limit);
        assert_eq!(
            (high.recommended_min_accounts, high.recommended_max_accounts),
            (192, 300)
        );
        assert_eq!(
            (low.recommended_min_accounts, low.recommended_max_accounts),
            (24, 50)
        );
        let conservative = estimate(
            10,
            16 * 1024 * MIB,
            4 * 1024 * MIB,
            2,
            Policy {
                conservative: true,
                manual_limit: None,
            },
        );
        assert_eq!(conservative.effective_limit, 64);
        assert_eq!(
            (
                conservative.recommended_min_accounts,
                conservative.recommended_max_accounts
            ),
            (60, 120)
        );
        assert_eq!(
            estimate(32, 64 * 1024 * MIB, 0, 301, Policy::default()).occupancy_percent,
            Some(101)
        );
        assert_eq!(
            estimate(
                16,
                32 * 1024 * MIB,
                0,
                2,
                Policy {
                    conservative: false,
                    manual_limit: Some(10)
                }
            )
            .effective_limit,
            10
        );
        assert_eq!(
            estimate(2, 1024 * MIB, 0, 2, Policy::default()).occupancy_percent,
            None
        );
    }
    #[test]
    fn compressed_memory_zero_uses_conservative_fallback() {
        let gib = 1024 * MIB;
        let compressed = normalize_memory_sample(16 * gib, 0, 13 * gib);
        assert_eq!(compressed.available, 3 * gib);
        assert_eq!(compressed.used, 13 * gib);
        assert_eq!(compressed.pressure_percent, 82);
        assert_eq!(compressed.source, "derived_total_minus_used");

        let normal = normalize_memory_sample(16 * gib, 5 * gib, 11 * gib);
        assert_eq!(normal.available, 5 * gib);
        assert_eq!(normal.pressure_percent, 69);
        assert_eq!(normal.source, "system_available");
    }
    #[test]
    fn measured_result_changes_with_live_cpu_and_memory() {
        let gib = 1024 * MIB;
        let balanced = measured_capacity(10, 16 * gib, 4 * gib, 30, 60_000, false);
        assert_eq!(balanced.estimated, 150);
        assert_eq!(balanced.memory_limit, 153);
        assert_eq!(balanced.cpu_limit, 233);
        assert_eq!(balanced.scheduler_limit, 300);

        let busy_cpu = measured_capacity(10, 16 * gib, 4 * gib, 80, 60_000, false);
        assert_eq!(busy_cpu.estimated, 65);
        let low_memory = measured_capacity(10, 16 * gib, 2 * gib, 30, 60_000, false);
        assert_eq!(low_memory.estimated, 25);
        let conservative = measured_capacity(10, 16 * gib, 4 * gib, 30, 60_000, true);
        assert_eq!(conservative.estimated, 100);
    }
    #[test]
    fn policy_roundtrip_rejects_invalid_without_mutation() {
        let dir = tempfile::tempdir().unwrap();
        let capacity = Capacity::open(dir.path()).unwrap();
        assert!(capacity.benchmark_result().unwrap().is_none());
        capacity
            .save(&Policy {
                conservative: true,
                manual_limit: Some(25),
            })
            .unwrap();
        assert!(capacity
            .save(&Policy {
                conservative: false,
                manual_limit: Some(0)
            })
            .is_err());
        capacity
            .save_benchmark_result(&BenchmarkResult {
                estimated_accounts: 88,
                tested_accounts: 88,
                tested_events: 264,
                duration_ms: 123,
                completed_at_ms: 456,
                model_version: "runtime-scheduler-v1".into(),
                scheduler_events_per_second: 2_000,
                cpu_usage_percent: 30,
                available_memory_bytes: 3 * 1024 * MIB,
                cpu_limit_accounts: 100,
                memory_limit_accounts: 88,
                scheduler_limit_accounts: 88,
            })
            .unwrap();
        drop(capacity);
        let reopened = Capacity::open(dir.path()).unwrap();
        assert_eq!(reopened.policy().unwrap().manual_limit, Some(25));
        assert!(reopened.benchmark_result().unwrap().is_none());
        reopened
            .save_benchmark_result(&BenchmarkResult {
                estimated_accounts: 88,
                tested_accounts: 300,
                tested_events: 6_000,
                duration_ms: 123,
                completed_at_ms: 456,
                model_version: BENCHMARK_MODEL.into(),
                scheduler_events_per_second: 48_000,
                cpu_usage_percent: 30,
                available_memory_bytes: 3 * 1024 * MIB,
                cpu_limit_accounts: 100,
                memory_limit_accounts: 88,
                scheduler_limit_accounts: 300,
            })
            .unwrap();
        assert_eq!(
            reopened
                .benchmark_result()
                .unwrap()
                .unwrap()
                .estimated_accounts,
            88
        );
        reopened
            .save(&Policy {
                conservative: false,
                manual_limit: None,
            })
            .unwrap();
        assert!(reopened.benchmark_result().unwrap().is_none());
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn runtime_benchmark_exercises_and_cleans_isolated_scheduler() {
        let trial = exercise_runtime_candidate(8).await.unwrap();
        assert_eq!(trial.accounts, 8);
        assert_eq!(trial.tested_events, 8 * BENCHMARK_WAVES);
        assert!(trial.duration_ms <= u64::try_from(BENCHMARK_TIMEOUT.as_millis()).unwrap());
        assert!(trial.events_per_second > 0);
    }
}
