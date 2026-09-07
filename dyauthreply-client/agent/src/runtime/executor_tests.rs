// Included inside supervisor::tests so the real-runtime fixture is shared.
use crate::runtime::executor::{ExecutionFuture, ExecutionOutcome};

struct TestExecutor {
    started: mpsc::Sender<(AccountId, String)>,
    permits: Arc<tokio::sync::Semaphore>,
}
impl WorkExecutor for TestExecutor {
    fn execute(
        &self,
        work: WorkEnvelope,
        _control: watch::Receiver<AccountControl>,
    ) -> ExecutionFuture {
        let started = self.started.clone();
        let permits = self.permits.clone();
        Box::pin(async move {
            started
                .try_send((work.account_id, work.durable_id.clone()))
                .unwrap();
            match work.durable_id.as_str() {
                "panic" => panic!("synthetic handler panic"),
                "hang" => std::future::pending::<ExecutionOutcome>().await,
                id if id.starts_with("slow") => {
                    permits.acquire().await.unwrap().forget();
                    ExecutionOutcome::Finished
                }
                _ => ExecutionOutcome::Finished,
            }
        })
    }
}
fn test_executor() -> (Arc<TestExecutor>, mpsc::Receiver<(AccountId, String)>) {
    let (started, receiver) = mpsc::channel(64);
    (
        Arc::new(TestExecutor {
            started,
            permits: Arc::new(tokio::sync::Semaphore::new(0)),
        }),
        receiver,
    )
}
fn execution_work(spec: &AccountSpec, id: &str) -> WorkEnvelope {
    WorkEnvelope::new(
        spec.account_id.clone(),
        id,
        WorkKind::ManualSend,
        WorkFence {
            actor_generation: spec.actor_generation,
            credential_generation: spec.state.credential_generation,
            lease_epoch: spec.state.lease_epoch,
        },
        0,
    )
}
async fn wait_execution(
    runtime: &RuntimeHandle,
    check: impl Fn(&RuntimeHealthResponse) -> bool,
) -> RuntimeHealthResponse {
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let health = runtime.snapshot().await;
            if check(&health) {
                return health;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("execution state reached")
}

#[tokio::test]
async fn real_dispatcher_serializes_each_account_without_blocking_peers() {
    let (_dir, runtime) = runtime();
    let (executor, mut started) = test_executor();
    runtime
        .install_executor(
            executor.clone(),
            ExecutionConfig {
                max_in_flight: 2,
                timeout: Duration::from_secs(2),
            },
        )
        .await
        .unwrap();
    let slow = account_spec(0);
    let peer = account_spec(1);
    for spec in [&slow, &peer] {
        runtime.upsert_account(spec.clone()).await.unwrap();
    }
    for (spec, id) in [(&slow, "slow-1"), (&slow, "slow-2"), (&peer, "fast")] {
        assert_eq!(
            runtime.enqueue(execution_work(spec, id)).await,
            AdmissionResult::Accepted
        );
    }
    let first = tokio::time::timeout(Duration::from_secs(1), started.recv())
        .await
        .unwrap()
        .unwrap();
    let second = tokio::time::timeout(Duration::from_secs(1), started.recv())
        .await
        .unwrap()
        .unwrap();
    let seen = HashSet::from([first.1, second.1]);
    assert_eq!(
        seen,
        HashSet::from(["slow-1".to_owned(), "fast".to_owned()])
    );
    assert!(started.try_recv().is_err());
    executor.permits.add_permits(1);
    let third = tokio::time::timeout(Duration::from_secs(1), started.recv())
        .await
        .unwrap()
        .unwrap();
    assert_eq!(third.1, "slow-2");
    executor.permits.add_permits(1);
    let health = wait_execution(&runtime, |h| h.execution_completed == 3).await;
    assert_eq!(health.execution_mode, crate::health::ExecutorMode::Attached);
    assert_eq!(health.execution_capacity, 2);
    assert_eq!(health.execution_recovery_needed, 0);
    runtime.drain().await.unwrap();
}

#[tokio::test]
async fn actual_handler_concurrency_is_globally_bounded() {
    let (_dir, runtime) = runtime();
    let (executor, mut started) = test_executor();
    runtime
        .install_executor(
            executor.clone(),
            ExecutionConfig {
                max_in_flight: 2,
                timeout: Duration::from_secs(2),
            },
        )
        .await
        .unwrap();
    for n in 0..3 {
        let spec = account_spec(n);
        runtime.upsert_account(spec.clone()).await.unwrap();
        runtime.enqueue(execution_work(&spec, "slow")).await;
    }
    for _ in 0..2 {
        tokio::time::timeout(Duration::from_secs(1), started.recv())
            .await
            .unwrap()
            .unwrap();
    }
    assert_eq!(runtime.snapshot().await.execution_active, 2);
    assert!(started.try_recv().is_err());
    executor.permits.add_permits(1);
    tokio::time::timeout(Duration::from_secs(1), started.recv())
        .await
        .unwrap()
        .unwrap();
    executor.permits.add_permits(2);
    wait_execution(&runtime, |h| h.execution_completed == 3).await;
    runtime.drain().await.unwrap();
}

#[tokio::test]
async fn timed_out_and_panicked_jobs_release_the_account_slot_and_record_recovery() {
    let (_dir, runtime) = runtime();
    let (executor, mut started) = test_executor();
    runtime
        .install_executor(
            executor,
            ExecutionConfig {
                max_in_flight: 1,
                timeout: Duration::from_millis(100),
            },
        )
        .await
        .unwrap();
    let spec = account_spec(0);
    runtime.upsert_account(spec.clone()).await.unwrap();
    for (id, expected) in [("hang", 1), ("panic", 2)] {
        runtime.enqueue(execution_work(&spec, id)).await;
        tokio::time::timeout(Duration::from_secs(1), started.recv())
            .await
            .unwrap()
            .unwrap();
        wait_execution(&runtime, |h| {
            h.execution_recovery_needed == expected && h.execution_active == 0
        })
        .await;
    }
    runtime.enqueue(execution_work(&spec, "fast")).await;
    wait_execution(&runtime, |h| h.execution_completed == 1).await;
    let health = runtime.drain().await.unwrap();
    assert_eq!(health.execution_recovery_needed, 2);
}

#[tokio::test]
async fn credential_rotation_cancels_inflight_business_future() {
    let (_dir, runtime) = runtime();
    let (executor, mut started) = test_executor();
    runtime
        .install_executor(
            executor,
            ExecutionConfig {
                max_in_flight: 1,
                timeout: Duration::from_secs(60),
            },
        )
        .await
        .unwrap();
    let mut spec = account_spec(0);
    runtime.upsert_account(spec.clone()).await.unwrap();
    runtime.enqueue(execution_work(&spec, "hang")).await;
    tokio::time::timeout(Duration::from_secs(1), started.recv())
        .await
        .unwrap()
        .unwrap();
    spec.state.credential_generation += 1;
    runtime
        .update_account_control(&spec.account_id, spec.control())
        .await
        .unwrap();
    wait_execution(&runtime, |h| {
        h.execution_recovery_needed == 1 && h.execution_active == 0
    })
    .await;
    runtime.drain().await.unwrap();
}

#[tokio::test]
async fn bounded_drain_observes_aborted_execution_children() {
    let mut cfg = RuntimeConfig::recommended();
    cfg.drain_timeout_ms = 100;
    let (_dir, runtime) = runtime_with_config(cfg);
    let (executor, mut started) = test_executor();
    runtime
        .install_executor(
            executor,
            ExecutionConfig {
                max_in_flight: 1,
                timeout: Duration::from_secs(60),
            },
        )
        .await
        .unwrap();
    let spec = account_spec(0);
    runtime.upsert_account(spec.clone()).await.unwrap();
    runtime.enqueue(execution_work(&spec, "hang")).await;
    tokio::time::timeout(Duration::from_secs(1), started.recv())
        .await
        .unwrap()
        .unwrap();
    tokio::time::timeout(Duration::from_secs(3), async {
        while runtime.drain().await.is_err() {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    let health = runtime.snapshot().await;
    assert_eq!(health.execution_active, 0);
    assert_eq!(health.execution_recovery_needed, 1);
    assert!(!health.dispatcher_alive);
}

#[tokio::test]
async fn capability_result_can_settle_without_discarding_durable_completion() {
    let (_dir, runtime) = runtime();
    let (executor, mut started) = test_executor();
    runtime
        .install_executor(
            executor.clone(),
            ExecutionConfig {
                max_in_flight: 1,
                timeout: Duration::from_secs(2),
            },
        )
        .await
        .unwrap();
    let spec = account_spec(0);
    runtime.upsert_account(spec.clone()).await.unwrap();
    let work = execution_work(&spec, "slow-result");
    runtime.enqueue(work.clone()).await;
    tokio::time::timeout(Duration::from_secs(1), started.recv())
        .await
        .unwrap()
        .unwrap();
    runtime
        .update_send_capability(
            &spec.account_id,
            work.fence(),
            SendCapability::RiskControlled,
        )
        .await
        .unwrap();
    executor.permits.add_permits(1);
    let health = wait_execution(&runtime, |h| h.execution_completed == 1).await;
    assert_eq!(health.execution_recovery_needed, 0);
    assert_ne!(
        runtime
            .enqueue(execution_work(&spec, "blocked-new-send"))
            .await,
        AdmissionResult::Accepted
    );
    runtime.drain().await.unwrap();
}

struct RuntimeOwningExecutor {
    _runtime: RuntimeHandle,
}
impl WorkExecutor for RuntimeOwningExecutor {
    fn execute(
        &self,
        _work: WorkEnvelope,
        _control: watch::Receiver<AccountControl>,
    ) -> ExecutionFuture {
        Box::pin(async { ExecutionOutcome::Finished })
    }
}
#[tokio::test]
async fn drain_releases_executor_that_owns_a_runtime_handle() {
    let (_dir, runtime) = runtime();
    let executor = Arc::new(RuntimeOwningExecutor {
        _runtime: runtime.clone(),
    });
    let weak = Arc::downgrade(&executor);
    runtime
        .install_executor(
            executor,
            ExecutionConfig {
                max_in_flight: 2,
                timeout: Duration::from_secs(2),
            },
        )
        .await
        .unwrap();
    runtime.drain().await.unwrap();
    assert!(
        weak.upgrade().is_none(),
        "executor/runtime ownership cycle retained after drain"
    );
}
#[tokio::test]
async fn receive_status_does_not_clear_risk_control_or_accept_stale_fences() {
    let (_dir, runtime) = runtime();
    let mut spec = account_spec(0);
    spec.state.send = SendCapability::RiskControlled;
    runtime.upsert_account(spec.clone()).await.unwrap();
    let fence = execution_work(&spec, "inbound").fence();
    assert_eq!(
        runtime
            .update_inbound_state(
                &spec.account_id,
                fence,
                crate::state::InboundState::HttpDegraded
            )
            .await
            .unwrap(),
        AdmissionResult::Accepted
    );
    let control = runtime.account_control(&spec.account_id).await.unwrap();
    assert_eq!(control.borrow().state.send, SendCapability::RiskControlled);
    let mut stale = fence;
    stale.credential_generation += 1;
    assert_eq!(
        runtime
            .update_inbound_state(
                &spec.account_id,
                stale,
                crate::state::InboundState::WsHealthy
            )
            .await
            .unwrap(),
        AdmissionResult::Stale
    );
    assert_eq!(
        control.borrow().state.inbound,
        crate::state::InboundState::HttpDegraded
    );
    runtime.drain().await.unwrap();
}

struct SettlingExecutor {
    runtime: RuntimeHandle,
    started: Arc<tokio::sync::Notify>,
}
impl WorkExecutor for SettlingExecutor {
    fn execute(
        &self,
        work: WorkEnvelope,
        _control: watch::Receiver<AccountControl>,
    ) -> ExecutionFuture {
        let runtime = self.runtime.clone();
        let started = self.started.clone();
        Box::pin(async move {
            started.notify_one();
            while *runtime.inner.phase.borrow() == RuntimePhase::Running {
                tokio::task::yield_now().await;
            }
            // Durable settlement may finish during drain. State publication
            // returns Stopping, but must not be stuck behind the drain gate.
            let result = runtime
                .update_send_capability(&work.account_id, work.fence(), SendCapability::Sendable)
                .await
                .unwrap();
            assert_eq!(result, AdmissionResult::Stopping);
            ExecutionOutcome::Finished
        })
    }
}
#[tokio::test]
async fn drain_allows_inflight_handler_to_settle_without_gate_deadlock() {
    let (_dir, runtime) = runtime();
    let started = Arc::new(tokio::sync::Notify::new());
    runtime
        .install_executor(
            Arc::new(SettlingExecutor {
                runtime: runtime.clone(),
                started: started.clone(),
            }),
            ExecutionConfig {
                max_in_flight: 1,
                timeout: Duration::from_secs(2),
            },
        )
        .await
        .unwrap();
    let spec = account_spec(0);
    runtime.upsert_account(spec.clone()).await.unwrap();
    runtime.enqueue(execution_work(&spec, "settlement")).await;
    started.notified().await;
    let health = runtime.drain().await.unwrap();
    assert_eq!(health.execution_completed, 1);
    assert_eq!(health.execution_recovery_needed, 0);
}

#[tokio::test]
async fn automatic_activation_never_promotes_unknown_send_capability() {
    let (_dir, runtime) = runtime();
    let mut spec = account_spec(0);
    spec.state.lifecycle = LifecycleState::PausedAuto;
    spec.state.send = SendCapability::Unknown;
    runtime.upsert_account(spec.clone()).await.unwrap();
    runtime
        .activate_automatic_account(&spec.account_id)
        .await
        .unwrap();
    let state = runtime.account_control(&spec.account_id).await.unwrap();
    assert_eq!(state.borrow().state.send, SendCapability::Unknown);
    assert_eq!(state.borrow().state.lifecycle, LifecycleState::Running);
    runtime
        .update_send_capability(
            &spec.account_id,
            execution_work(&spec, "capability").fence(),
            SendCapability::Sendable,
        )
        .await
        .unwrap();
    runtime
        .activate_automatic_account(&spec.account_id)
        .await
        .unwrap();
    assert_eq!(state.borrow().state.lifecycle, LifecycleState::Running);
    runtime.drain().await.unwrap();
}
