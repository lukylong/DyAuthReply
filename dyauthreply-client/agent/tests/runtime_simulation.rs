use std::{num::NonZeroU64, sync::Arc, time::Duration};

use dy_agent::{
    config::{RuntimeConfig, StorageConfig},
    health::{HealthHandle, HealthResponse, StorageHealthResponse, StorageStartupSnapshot},
    protocol::fixtures::verify_embedded_corpus,
    runtime::{
        breaker::{ReconnectBudget, ReconnectBudgetConfig, ReconnectDecision},
        fair_queue::{FairQueue, FairQueueConfig},
        heartbeat::{
            AccountHeartbeatDelta, DependencyHealth, HeartbeatAggregator, HeartbeatConfig,
        },
        lanes::{SignerAdmission, SignerJob},
        model::{AccountId, WorkClassCapacities, WorkEnvelope, WorkFence, WorkKind},
        supervisor::{AccountSpec, RuntimeHandle},
        timer::CentralTimer,
    },
    state::{AccountRuntimeState, InboundState, LifecycleState, OwnershipState, SendCapability},
    storage::{retention::DiskPressure, RecoveryReport, SegmentCatalog, SegmentStore, ZstdCodec},
    store::CoreStore,
};
use tempfile::TempDir;
use tokio::time::{sleep, timeout};
use uuid::Uuid;

const HTTP_LANES: u64 = 16;
const FAKE_PIPELINE_MS: u64 = 1 + 20 + 80;

fn envelope(account: usize, item: usize, kind: WorkKind) -> WorkEnvelope {
    WorkEnvelope::new(
        AccountId::new(format!("account-{account:03}")).expect("valid synthetic account"),
        format!("durable-{account:03}-{item:04}"),
        kind,
        WorkFence {
            actor_generation: 1,
            credential_generation: 1,
            lease_epoch: 1,
        },
        0,
    )
}

fn queue_for(total: usize, per_account: usize) -> FairQueue {
    let capacity = total.next_power_of_two().max(16);
    FairQueue::new(FairQueueConfig {
        global_capacity: capacity,
        per_account_capacity: per_account.max(1),
        class_capacities: WorkClassCapacities {
            manual: capacity,
            automatic: capacity,
            background: capacity,
        },
        max_manual_burst: 8,
    })
    .expect("valid simulation queue")
}

fn percentile_95(mut samples: Vec<u64>) -> u64 {
    samples.sort_unstable();
    let index = samples
        .len()
        .saturating_mul(95)
        .div_ceil(100)
        .saturating_sub(1);
    samples[index]
}

fn healthy_storage() -> StorageHealthResponse {
    StorageHealthResponse::startup(&StorageStartupSnapshot {
        pressure: DiskPressure::Normal,
        disposable_writes_allowed: true,
        background_work_paused: false,
        sealed_segment_count: 0,
        sealed_segment_bytes: 0,
        active_segment_count: 0,
        cleanup_deleted_segments: 0,
        recovery: RecoveryReport::default(),
    })
}

fn coordinator_config(mailbox_capacity: usize, queue_capacity: usize) -> RuntimeConfig {
    RuntimeConfig {
        account_mailbox_capacity: mailbox_capacity,
        global_queue_capacity: queue_capacity,
        per_account_queue_capacity: mailbox_capacity,
        drain_timeout_ms: 5_000,
        ..RuntimeConfig::recommended()
    }
}

fn start_coordinator(config: RuntimeConfig) -> (TempDir, RuntimeHandle) {
    let directory = tempfile::tempdir().expect("temporary runtime directory");
    let core = Arc::new(CoreStore::open(directory.path()).expect("core store"));
    let catalog: Arc<dyn SegmentCatalog> = core.clone();
    let storage_config = StorageConfig::recommended();
    let segments = SegmentStore::open_with_codec(
        directory.path().join("segments"),
        storage_config.segment_policies.clone(),
        catalog,
        Arc::new(ZstdCodec::default()),
    )
    .expect("segment store");
    let parity = verify_embedded_corpus().expect("embedded protocol corpus");
    let health = HealthHandle::new(HealthResponse::foundation(
        Uuid::new_v4(),
        Uuid::new_v4(),
        &parity,
        healthy_storage(),
    ));
    let runtime = RuntimeHandle::start(
        config,
        "installation-runtime-simulation",
        core,
        segments,
        storage_config,
        health,
    )
    .expect("runtime coordinator starts");
    (directory, runtime)
}

fn account_spec(account: usize) -> AccountSpec {
    AccountSpec {
        account_id: AccountId::new(format!("account-{account:03}"))
            .expect("valid synthetic account"),
        actor_generation: 1,
        state: AccountRuntimeState {
            lifecycle: LifecycleState::Running,
            ownership: OwnershipState::Owned,
            inbound: InboundState::WsHealthy,
            send: SendCapability::Sendable,
            credential_generation: 1,
            lease_epoch: 1,
        },
    }
}

async fn install_accounts(runtime: &RuntimeHandle, accounts: usize) {
    for account in 0..accounts {
        runtime
            .upsert_account(account_spec(account))
            .await
            .expect("synthetic account actor starts");
    }
}

async fn wait_for_dispatches(runtime: &RuntimeHandle, expected: u64) {
    timeout(Duration::from_secs(5), async {
        loop {
            let snapshot = runtime.snapshot().await;
            if snapshot.dispatched_total == expected {
                return;
            }
            assert!(
                snapshot.dispatched_total < expected,
                "unexpected extra dispatches: {snapshot:?}"
            );
            sleep(Duration::from_millis(1)).await;
        }
    })
    .await
    .expect("accepted work must make bounded forward progress");
}

async fn assert_clean_drain(runtime: &RuntimeHandle) {
    let stopped = timeout(Duration::from_secs(6), runtime.drain())
        .await
        .expect("runtime drain deadline")
        .expect("runtime drains");
    assert!(!stopped.accepting_work);
    assert_eq!(stopped.actor_count, 0);
    assert_eq!(stopped.central_timer_tasks, 0);
    assert_eq!(stopped.scheduled_timer_count, 0);
    assert_eq!(stopped.queue_depth, 0);
    assert_eq!(stopped.signer_queue_depth, 0);
    assert_eq!(stopped.signer_in_flight, 0);
    assert_eq!(stopped.unresolved_work, 0);

    assert_eq!(
        runtime
            .enqueue(envelope(0, usize::MAX, WorkKind::ManualSend))
            .await,
        dy_agent::runtime::model::AdmissionResult::Stopping
    );
}

#[test]
fn simulation_10_accounts_nominal_has_exact_accounting_and_p95_under_one_second() {
    let accounts = 10;
    let per_account = 100;
    let total = accounts * per_account;
    let mut queue = queue_for(total, per_account);
    for item in 0..per_account {
        for account in 0..accounts {
            let kind = match item % 3 {
                0 => WorkKind::ManualSend,
                1 => WorkKind::AutomaticReply,
                _ => WorkKind::InboundWakeup,
            };
            assert!(queue
                .try_enqueue(envelope(account, item, kind))
                .is_admitted());
        }
    }

    let mut latency_ms = Vec::with_capacity(total);
    for now_ms in 1..=u64::try_from(total).expect("total fits u64") {
        queue.pop(now_ms).expect("all accepted work dispatches");
        latency_ms.push(now_ms);
    }
    let snapshot = queue.snapshot();
    assert_eq!(snapshot.depth, 0);
    assert_eq!(snapshot.accepted, 1_000);
    assert_eq!(snapshot.dispatched, 1_000);
    assert!(snapshot.has_exact_work_accounting());
    assert!(percentile_95(latency_ms) <= 1_000);
}

#[test]
fn simulation_100_accounts_hotspot_serves_every_cold_account_in_one_round() {
    let mut queue = queue_for(199, 100);
    for item in 0..100 {
        assert!(queue
            .try_enqueue(envelope(0, item, WorkKind::AutomaticReply))
            .is_admitted());
    }
    for account in 1..100 {
        assert!(queue
            .try_enqueue(envelope(account, 0, WorkKind::AutomaticReply))
            .is_admitted());
    }

    let mut cold_seen = [false; 100];
    let mut latency_ms = Vec::with_capacity(199);
    for dispatch_index in 1..=199_u64 {
        let work = queue
            .pop(dispatch_index.saturating_mul(10))
            .expect("queued work dispatches");
        let account: usize = work
            .account_id
            .as_str()
            .trim_start_matches("account-")
            .parse()
            .expect("synthetic numeric suffix");
        if account > 0 {
            cold_seen[account] = true;
            assert!(dispatch_index <= 100, "cold account {account} starved");
        }
        latency_ms.push(dispatch_index.saturating_mul(10));
    }
    assert!(cold_seen[1..].iter().all(|seen| *seen));
    assert!(percentile_95(latency_ms) <= 2_000);
    assert!(queue.snapshot().has_exact_work_accounting());
}

#[test]
fn simulation_300_account_burst_stays_inside_fake_lane_and_five_second_gate() {
    let mut queue = queue_for(300, 4);
    for account in 0..300 {
        assert!(queue
            .try_enqueue(envelope(account, 0, WorkKind::AutomaticReply))
            .is_admitted());
    }

    let mut completions = Vec::with_capacity(300);
    for dispatch_index in 0..300_u64 {
        queue.pop(dispatch_index).expect("burst dispatches");
        let wave = dispatch_index / HTTP_LANES + 1;
        completions.push(wave.saturating_mul(FAKE_PIPELINE_MS));
    }
    let snapshot = queue.snapshot();
    assert_eq!(snapshot.dispatched, 300);
    assert_eq!(snapshot.depth, 0);
    assert!(snapshot.has_exact_work_accounting());
    assert!(percentile_95(completions) <= 5_000);
}

#[tokio::test(flavor = "current_thread")]
async fn coordinator_10_accounts_moves_all_work_mailbox_to_dispatcher_and_stops_cleanly() {
    const ACCOUNTS: usize = 10;
    const EVENTS_PER_ACCOUNT: usize = 100;
    const EXPECTED: u64 = (ACCOUNTS * EVENTS_PER_ACCOUNT) as u64;

    let (_directory, runtime) = start_coordinator(coordinator_config(128, 2_048));
    install_accounts(&runtime, ACCOUNTS).await;

    let mut admitted = 0_u64;
    for item in 0..EVENTS_PER_ACCOUNT {
        for account in 0..ACCOUNTS {
            let kind = match item % 3 {
                0 => WorkKind::ManualSend,
                1 => WorkKind::AutomaticReply,
                _ => WorkKind::InboundWakeup,
            };
            let result = runtime.enqueue(envelope(account, item, kind)).await;
            assert!(
                result.is_admitted(),
                "account={account} item={item}: {result:?}"
            );
            admitted = admitted.saturating_add(1);
        }
    }
    assert_eq!(admitted, EXPECTED);

    wait_for_dispatches(&runtime, EXPECTED).await;
    let running = runtime.snapshot().await;
    assert_eq!(running.actor_count, ACCOUNTS);
    assert_eq!(running.central_timer_tasks, 1);
    assert_eq!(running.queue_depth, 0);
    assert!(running.queue_high_water <= running.queue_capacity);
    assert_eq!(running.queue_rejected, 0);
    assert_eq!(running.stale_work_rejected, 0);
    assert_eq!(running.dispatched_total, EXPECTED);
    assert_eq!(running.unresolved_work, 0);

    assert_clean_drain(&runtime).await;
}

#[tokio::test(flavor = "current_thread")]
async fn coordinator_100_account_hotspot_cannot_starve_cold_actor_work() {
    const ACCOUNTS: usize = 100;
    const HOT_EVENTS: usize = 100;
    const EXPECTED: u64 = (HOT_EVENTS + ACCOUNTS - 1) as u64;

    let (_directory, runtime) = start_coordinator(coordinator_config(128, 512));
    install_accounts(&runtime, ACCOUNTS).await;

    for item in 0..HOT_EVENTS {
        assert!(runtime
            .enqueue(envelope(0, item, WorkKind::AutomaticReply))
            .await
            .is_admitted());
    }
    for account in 1..ACCOUNTS {
        assert!(runtime
            .enqueue(envelope(account, 0, WorkKind::AutomaticReply))
            .await
            .is_admitted());
    }

    // This traverses the real bounded actor mailbox -> fair queue -> shadow
    // dispatcher chain. Completion of all cold durable IDs within one bounded
    // wait complements the FairQueue one-round ordering assertion above and
    // catches actor/notification integration starvation.
    wait_for_dispatches(&runtime, EXPECTED).await;
    let running = runtime.snapshot().await;
    assert_eq!(running.actor_count, ACCOUNTS);
    assert_eq!(running.dispatched_total, EXPECTED);
    assert_eq!(running.queue_depth, 0);
    assert_eq!(running.queue_rejected, 0);
    assert_eq!(running.stale_work_rejected, 0);
    assert!(running.max_fairness_lag_ms <= 2_000);

    assert_clean_drain(&runtime).await;
}

#[tokio::test(flavor = "current_thread")]
async fn coordinator_300_accounts_share_one_timer_and_bounded_signer_lanes() {
    const ACCOUNTS: usize = 300;
    const SIGNER_LANES: usize = 4;

    let (_directory, runtime) = start_coordinator(coordinator_config(4, 1_024));
    install_accounts(&runtime, ACCOUNTS).await;
    for account in 0..ACCOUNTS {
        assert!(runtime
            .enqueue(envelope(account, 0, WorkKind::AutomaticReply))
            .await
            .is_admitted());
    }
    wait_for_dispatches(&runtime, ACCOUNTS as u64).await;

    let running = runtime.snapshot().await;
    assert_eq!(running.actor_count, ACCOUNTS);
    assert_eq!(running.central_timer_tasks, 1);
    assert!(running.scheduled_timer_count <= 8 * ACCOUNTS + 32);
    assert_eq!(running.dispatched_total, ACCOUNTS as u64);
    assert_eq!(running.queue_depth, 0);
    assert!(running.queue_high_water <= running.queue_capacity);
    assert_eq!(running.queue_rejected, 0);
    assert_eq!(running.stale_work_rejected, 0);
    assert_eq!(running.heartbeat_dirty_accounts, ACCOUNTS);

    // Traverse the coordinator's real signer admission -> per-lane worker
    // chain. `admit_signer` itself awaits account control, so fixed workers may
    // make progress between admissions even on a current-thread runtime.
    for account in 0..ACCOUNTS {
        let account_id = format!("account-{account:03}");
        assert!(matches!(
            runtime
                .admit_signer(SignerJob {
                    account_id,
                    durable_id: format!("sign-{account:03}"),
                    kind: WorkKind::AutomaticReply,
                    actor_generation: 1,
                    credential_generation: 1,
                    lease_epoch: 1,
                    plan_digest: [u8::try_from(account % 251).expect("digest byte"); 32],
                })
                .await,
            SignerAdmission::Accepted { .. }
        ));
    }
    let signer = runtime.snapshot().await;
    assert_eq!(signer.signer_concurrency, SIGNER_LANES);
    assert_eq!(signer.signer_lanes.len(), SIGNER_LANES);
    assert_eq!(signer.signer_admitted, ACCOUNTS as u64);
    assert!(signer.signer_queue_depth <= signer.signer_queue_capacity);
    assert!(signer.signer_in_flight <= SIGNER_LANES);
    assert_eq!(
        signer.signer_admitted,
        u64::try_from(signer.signer_queue_depth + signer.signer_in_flight)
            .expect("outstanding signer count fits u64")
            .saturating_add(signer.signer_completed)
            .saturating_add(signer.signer_failed)
            .saturating_add(signer.signer_cancelled),
        "every admitted signer job is queued, executing, or terminal",
    );
    assert!(signer.signer_max_queued <= signer.signer_queue_capacity);
    assert!(signer.signer_max_in_flight <= SIGNER_LANES);
    assert_eq!(
        signer
            .signer_lanes
            .iter()
            .map(|lane| lane.queued)
            .sum::<usize>(),
        signer.signer_queue_depth,
    );
    assert_eq!(
        signer
            .signer_lanes
            .iter()
            .filter(|lane| lane.in_flight)
            .count(),
        signer.signer_in_flight,
    );

    timeout(Duration::from_secs(5), async {
        loop {
            let signer = runtime.snapshot().await;
            assert!(signer.signer_in_flight <= SIGNER_LANES);
            assert!(signer.signer_queue_depth <= ACCOUNTS);
            if signer.signer_queue_depth == 0 && signer.signer_in_flight == 0 {
                break;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("fixed signer workers must drain every admitted account");

    let settled = runtime.snapshot().await;
    assert_eq!(settled.signer_queue_depth, 0);
    assert_eq!(settled.signer_in_flight, 0);
    assert_eq!(settled.signer_rejected, 0);
    assert_eq!(settled.signer_admitted, ACCOUNTS as u64);
    assert_eq!(settled.signer_completed, ACCOUNTS as u64);
    assert_eq!(settled.signer_failed, 0);
    assert_eq!(settled.signer_cancelled, 0);

    assert_clean_drain(&runtime).await;
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
enum SimTimer {
    Heartbeat,
    Cleanup,
    Health,
    Account(usize, WorkKind),
}

#[test]
fn simulation_300_idle_accounts_use_one_bounded_timer_and_coalesce_sleep_resume() {
    let mut timer = CentralTimer::new();
    timer.schedule_periodic(
        SimTimer::Heartbeat,
        30_000,
        NonZeroU64::new(30_000).unwrap(),
    );
    timer.schedule_periodic(SimTimer::Cleanup, 60_000, NonZeroU64::new(60_000).unwrap());
    timer.schedule_periodic(SimTimer::Health, 250, NonZeroU64::new(250).unwrap());
    for account in 0..300 {
        for (kind, interval) in [
            (WorkKind::KeepaliveLease, 20_000),
            (WorkKind::PendingRecovery, 60_000),
            (WorkKind::Reconcile, 300_000),
        ] {
            timer.schedule_periodic(
                SimTimer::Account(account, kind),
                interval,
                NonZeroU64::new(interval).unwrap(),
            );
        }
    }
    assert!(timer.snapshot().live_entries <= 8 * 300 + 32);

    let due = timer.drain_due(3_600_000);
    assert_eq!(due.len(), 903);
    let heartbeat = due
        .iter()
        .find(|item| item.key == SimTimer::Heartbeat)
        .expect("one aggregate heartbeat timer");
    assert_eq!(heartbeat.coalesced_periods, 120);
    assert!(heartbeat
        .next_deadline_ms
        .is_some_and(|next| next > 3_600_000));
    assert_eq!(
        due.iter()
            .filter(|item| item.key == SimTimer::Heartbeat)
            .count(),
        1
    );
}

#[test]
fn simulation_300_disconnect_storm_obeys_global_reconnect_budget() {
    let mut budget = ReconnectBudget::new(
        ReconnectBudgetConfig {
            capacity: 8,
            refill_tokens: 4,
            refill_interval_ms: 1_000,
        },
        0,
    )
    .expect("valid reconnect budget");
    let mut granted = 0_u64;
    for second in 0..=10_u64 {
        let now_ms = second * 1_000;
        for _ in 0..300 {
            if budget.try_acquire(now_ms) == ReconnectDecision::Granted {
                granted += 1;
            }
        }
        assert!(granted <= 8 + 4 * second);
    }
    assert_eq!(granted, 48);
}

#[test]
fn simulation_300_account_status_changes_make_one_aggregate_heartbeat_batch() {
    let mut heartbeat = HeartbeatAggregator::new(
        "installation-simulation",
        HeartbeatConfig {
            max_tracked_accounts: 300,
            max_accounts_per_batch: 300,
            max_payload_bytes: 512 * 1_024,
            max_inflight_batches: 1,
            full_refresh_interval_ms: 300_000,
            max_account_id_bytes: 256,
        },
    )
    .expect("valid heartbeat configuration");
    for account in 0..300 {
        heartbeat
            .publish(AccountHeartbeatDelta {
                account_id: format!("account-{account:03}"),
                actor_generation: 1,
                runtime: AccountRuntimeState {
                    lifecycle: LifecycleState::Running,
                    ownership: OwnershipState::Owned,
                    inbound: InboundState::WsHealthy,
                    send: SendCapability::Sendable,
                    credential_generation: 1,
                    lease_epoch: 1,
                },
                mailbox_depth: 0,
                signer_queue_depth: 0,
                last_activity_ms: None,
                transport: DependencyHealth::Closed,
                signer: DependencyHealth::Closed,
            })
            .expect("bounded publish");
    }
    let batches = heartbeat.prepare(30_000).expect("heartbeat preparation");
    assert_eq!(batches.len(), 1);
    assert_eq!(batches[0].accounts.len(), 300);
    assert!(batches[0]
        .accounts
        .windows(2)
        .all(|pair| pair[0].account_id < pair[1].account_id));
}
