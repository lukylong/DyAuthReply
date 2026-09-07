use std::{
    env,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{bail, Context, Result};
use dy_agent::{
    config::AgentConfig,
    health::{
        live_router, HealthHandle, HealthResponse, StorageHealthResponse, StorageStartupSnapshot,
    },
    identity,
    protocol::fixtures::verify_embedded_corpus,
    runtime::hosted::{HostedController, HostedSettings},
    runtime::supervisor::RuntimeHandle,
    runtime::{
        executor::ExecutionConfig,
        messaging::{ManualService, MessagingSettings},
    },
    storage::{
        retention::{plan_cleanup_with_previous, DiskSnapshot},
        SegmentCatalog, SegmentStore, ZstdCodec,
    },
    store::CoreStore,
    CORE_SCHEMA_VERSION,
};
use tokio::{net::TcpListener, signal};
use tracing::info;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("dy_agent=info")),
        )
        .init();

    let command = parse_command()?;
    if command == Command::Version {
        println!("dy-agent {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }

    let parity =
        verify_embedded_corpus().context("embedded protocol corpus verification failed")?;
    if command == Command::VerifyProtocol {
        println!(
            "{}",
            serde_json::to_string_pretty(&parity)
                .context("cannot serialize protocol parity report")?
        );
        return Ok(());
    }

    let config = AgentConfig::from_env()?;
    let (identity, _instance_lock) = identity::initialize(&config.data_dir)?;
    let (store, segment_store, storage_health) = initialize_storage(&config)?;
    let schema_version = store.schema_version()?;
    if schema_version != CORE_SCHEMA_VERSION {
        bail!("unsupported core schema version {schema_version}; expected {CORE_SCHEMA_VERSION}");
    }

    let health = HealthResponse::foundation(
        identity.installation_id,
        identity.boot_id,
        &parity,
        storage_health,
    );
    match command {
        Command::Check => {
            println!(
                "{}",
                serde_json::to_string_pretty(&health).context("cannot serialize health result")?
            );
        }
        Command::Serve => serve(config, health, store, segment_store).await?,
        Command::Version | Command::VerifyProtocol => {
            unreachable!("offline commands exit before initializing the Agent")
        }
    }

    Ok(())
}

fn initialize_storage(
    config: &AgentConfig,
) -> Result<(Arc<CoreStore>, SegmentStore, StorageHealthResponse)> {
    let store = Arc::new(CoreStore::open(&config.data_dir)?);
    let integrity = store.database_integrity()?;
    if !integrity.is_valid() {
        bail!(
            "core storage integrity failed: quick_check={:?}, foreign_key_violations={}",
            integrity.quick_check,
            integrity.foreign_key_violations
        );
    }

    let catalog: Arc<dyn SegmentCatalog> = store.clone();
    let mut segment_store = SegmentStore::open_with_codec(
        config.data_dir.join("segments"),
        config.storage.segment_policies.clone(),
        catalog,
        Arc::new(ZstdCodec::default()),
    )?;
    let recovery = segment_store.recovery_report().clone();
    let disk = DiskSnapshot {
        total_bytes: fs2::total_space(segment_store.root())?,
        available_bytes: fs2::available_space(segment_store.root())?,
    };
    let now_ms = i64::try_from(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .context("system clock is before the Unix epoch")?
            .as_millis(),
    )
    .context("system time does not fit storage timestamp")?;
    let previous_pressure = store
        .storage_cleanup_state()?
        .map(|state| state.last_pressure);
    let manifests = SegmentCatalog::list_manifests(store.as_ref())?;
    let cleanup = plan_cleanup_with_previous(
        &manifests,
        &config.storage.segment_policies,
        disk,
        config.storage.watermarks,
        now_ms,
        previous_pressure,
    )?;
    let cleanup_deleted_segments = segment_store.apply_cleanup(&cleanup)?;
    let remaining = SegmentCatalog::list_manifests(store.as_ref())?;
    let post_cleanup_disk = DiskSnapshot {
        total_bytes: fs2::total_space(segment_store.root())?,
        available_bytes: fs2::available_space(segment_store.root())?,
    };
    let observed = plan_cleanup_with_previous(
        &remaining,
        &config.storage.segment_policies,
        post_cleanup_disk,
        config.storage.watermarks,
        now_ms,
        Some(cleanup.pressure),
    )?;
    store.record_storage_cleanup_state(now_ms, observed.pressure)?;
    let sealed_segment_bytes = remaining.iter().try_fold(0_u64, |total, manifest| {
        total
            .checked_add(manifest.stored_bytes)
            .context("sealed segment byte total overflow")
    })?;
    let health = StorageHealthResponse::startup(&StorageStartupSnapshot {
        pressure: observed.pressure,
        disposable_writes_allowed: observed.allow_disposable_writes,
        background_work_paused: observed.pause_background,
        sealed_segment_count: remaining.len(),
        sealed_segment_bytes,
        active_segment_count: segment_store.active_writer_count(),
        cleanup_deleted_segments,
        recovery,
    });
    Ok((store, segment_store, health))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Command {
    Serve,
    Check,
    Version,
    VerifyProtocol,
}

fn parse_command() -> Result<Command> {
    let mut arguments = env::args().skip(1);
    let command = match arguments.next().as_deref() {
        None => Command::Serve,
        Some("--check") => Command::Check,
        Some("--version" | "-V") => Command::Version,
        Some("--verify-protocol") => Command::VerifyProtocol,
        Some(argument) => bail!(
            "unknown argument {argument:?}; expected --check, --verify-protocol, or --version"
        ),
    };
    if let Some(argument) = arguments.next() {
        bail!("unexpected extra argument {argument:?}");
    }
    Ok(command)
}

async fn serve(
    config: AgentConfig,
    mut health: HealthResponse,
    store: Arc<CoreStore>,
    segment_store: SegmentStore,
) -> Result<()> {
    // Exclude an installed legacy launcher before reading its SQLite/.env state.
    // Rust selection is published only after migration/onboarding succeeds.
    let engine_transition = begin_engine_transition()?;
    let (ipc, ipc_stop) = desktop_ipc(&config, &health)?;
    let (hosted_settings, messaging_settings, license) =
        onboarding_modes(&config.data_dir, &mut health)?;
    let _engine_gate = engine_transition
        .map(|transition| transition.activate(&config.data_dir))
        .transpose()?;
    let listener = TcpListener::bind(config.bind_addr)
        .await
        .with_context(|| format!("cannot bind Agent health server to {}", config.bind_addr))?;
    let health = HealthHandle::new(health);
    let runtime = RuntimeHandle::start(
        config.runtime.clone(),
        health.snapshot().instance_id.to_string(),
        store.clone(),
        segment_store,
        config.storage.clone(),
        health.clone(),
    )
    .context("cannot start bounded account runtime")?;
    let messaging = match start_messaging(
        messaging_settings,
        hosted_settings.as_ref(),
        runtime.clone(),
        store.clone(),
    )
    .await
    {
        Ok(value) => value,
        Err(error) => {
            runtime.drain().await?;
            return Err(error);
        }
    };
    let health_snapshot = health.snapshot();
    let hosted = if let Some(settings) = hosted_settings {
        match HostedController::start(
            settings,
            health_snapshot.instance_id,
            health_snapshot.boot_id,
            runtime.clone(),
            store,
        )
        .await
        {
            Ok(controller) => Some(controller),
            Err(error) => {
                runtime.drain().await?;
                return Err(error);
            }
        }
    } else {
        None
    };
    if let (Some((service, _)), Some(hosted)) = (&messaging, &hosted) {
        service.bind_controller(hosted.clone()).await?;
    }
    let renewal = license
        .as_ref()
        .map(|manager| manager.start(runtime.subscribe_control_ticks()));
    log_ready(&config, &health_snapshot);

    let ui_updates = messaging
        .as_ref()
        .map(|(service, _)| service.start_ui_updates());
    let frontier_for_shutdown = messaging.as_ref().map(|(service, _)| service.clone());
    let frontier_for_cleanup = frontier_for_shutdown.clone();
    let runtime_for_shutdown = runtime.clone();
    let hosted_for_shutdown = hosted.clone();
    ipc.watch_parent();
    let router = business_router(health, license, messaging, &config.data_dir).merge(ipc.router());
    let server_result = axum::serve(listener, router)
        .with_graceful_shutdown(shutdown_live_runtime(
            runtime_for_shutdown,
            hosted_for_shutdown,
            frontier_for_shutdown,
            ipc_stop,
        ))
        .await
        .context("Agent health server failed");
    if let Some(task) = ui_updates {
        task.abort();
        let _ = task.await;
    }
    if let Some(task) = renewal {
        task.stop().await?;
    }
    if let Some(service) = frontier_for_cleanup {
        service.stop_frontier().await?;
    }
    if let Some(controller) = hosted {
        if let Err(error) = controller.shutdown().await {
            tracing::error!(%error, "hosted ownership controller cleanup failed");
        }
    }
    // Also drain if the listener fails before the signal future completes.
    // Preserve the installation lock and store owners while a timed-out
    // cleanup is still finishing. Never exit the async main scope and release
    // ownership while a blocking writer can still touch the database.
    while let Err(error) = runtime.drain().await {
        tracing::error!(%error, "runtime remains draining; retaining installation lock");
    }
    server_result
}

type StartupModes = (
    Option<HostedSettings>,
    Option<MessagingSettings>,
    Option<Arc<dy_agent::license::NativeLicense>>,
);
fn begin_engine_transition() -> Result<Option<dy_agent::engine_gate::EngineTransition>> {
    env::var_os("CLIENT_DATA_DIR")
        .map(|_| {
            dy_agent::engine_gate::EngineTransition::acquire(
                &dy_agent::engine_gate::client_root_from_env()?,
            )
        })
        .transpose()
}

fn onboarding_modes(root: &std::path::Path, health: &mut HealthResponse) -> Result<StartupModes> {
    let license = load_native_license(root)?;
    let (legacy_hosted, settings) = if license.is_some() {
        let settings = native_config_path("DY_AGENT_MESSAGING_CONFIG", root, "messaging.json")
            .map(|path| dy_agent::runtime::messaging::read_private(std::path::Path::new(&path)))
            .transpose()?;
        (None, settings)
    } else {
        load_modes(health, root)?
    };
    let (hosted, settings) =
        dy_agent::onboarding::bootstrap(root, license.as_ref(), legacy_hosted, settings)?;
    if settings.is_some() {
        health.protocol_mode = "native-registry".into();
    }
    Ok((hosted, settings, license))
}

fn desktop_ipc(
    config: &AgentConfig,
    health: &HealthResponse,
) -> Result<(
    dy_agent::desktop_ipc::DesktopIpc,
    tokio::sync::watch::Receiver<bool>,
)> {
    let token = dy_agent::desktop_ipc::provision(&config.data_dir)?;
    dy_agent::desktop_ipc::DesktopIpc::new(
        token,
        health.instance_id,
        health.boot_id,
        config.bind_addr,
    )
}

fn business_router(
    health: HealthHandle,
    license: Option<Arc<dy_agent::license::NativeLicense>>,
    messaging: Option<(Arc<ManualService>, String)>,
    data_dir: &std::path::Path,
) -> axum::Router {
    let mut router = live_router(health.clone());
    if let Some(manager) = license.as_ref() {
        router = router.merge(dy_agent::license::api::router(manager.clone()));
    }
    if let Some((service, token)) = messaging {
        router = router.merge(dy_agent::capacity::router(
            service.capacity(),
            service.clone(),
            token.clone(),
        ));
        if let Some(manager) = license.as_ref() {
            let importer = Arc::new(dy_agent::onboarding::Importer::new(
                service.clone(),
                manager.clone(),
            ));
            router = router.merge(dy_agent::onboarding::router(&importer, token.clone()));
            router = router.merge(dy_agent::quick_auth::api::router(
                dy_agent::quick_auth::Broker::new(data_dir.to_path_buf(), importer),
                token.clone(),
            ));
        }
        router = router.merge(dy_agent::audit::api::router(service.clone(), token.clone()));
        let log_dir = data_dir.parent().unwrap_or(data_dir).join("logs");
        router = router.merge(dy_agent::admin::router(
            service.clone(),
            health,
            token.clone(),
            log_dir,
        ));
        router = router.merge(dy_agent::business::api::router(
            service.clone(),
            license,
            token.clone(),
        ));
        router = router.merge(dy_agent::workbench::api::router(
            service.clone(),
            service.workbench(),
            token.clone(),
        ));
        router = router.merge(dy_agent::runtime::messaging::api::router(service, token));
    }
    router
}

fn log_ready(config: &AgentConfig, health_snapshot: &HealthResponse) {
    info!(
        address = %config.bind_addr,
        data_dir = %config.data_dir.display(),
        protocol_mode = %health_snapshot.protocol_mode,
        protocol_parity_verified = health_snapshot.protocol_parity_verified,
        protocol_parity_all_verified = health_snapshot.protocol_parity_all_verified,
        central_timer_tasks = health_snapshot.runtime.central_timer_tasks,
        "dy-agent runtime is ready"
    );
}

fn native_config_path(
    name: &str,
    data_dir: &std::path::Path,
    file: &str,
) -> Option<std::ffi::OsString> {
    env::var_os(name).or_else(|| {
        let path = data_dir.join(file);
        path.is_file().then(|| path.into_os_string())
    })
}

fn load_native_license(
    data_dir: &std::path::Path,
) -> Result<Option<Arc<dy_agent::license::NativeLicense>>> {
    let license = native_config_path("DY_AGENT_LICENSE_CONFIG", data_dir, "native-license.json")
        .map(|path| -> Result<_> {
            let config = dy_agent::runtime::messaging::read_private(std::path::Path::new(&path))?;
            dy_agent::license::NativeLicense::new(config)
        })
        .transpose()?;
    Ok(license)
}

async fn shutdown_live_runtime(
    runtime_for_shutdown: RuntimeHandle,
    hosted_for_shutdown: Option<Arc<HostedController>>,
    frontier_for_shutdown: Option<Arc<ManualService>>,
    mut ipc_stop: tokio::sync::watch::Receiver<bool>,
) {
    tokio::select! {() = shutdown_signal()=>{},() = async {while !*ipc_stop.borrow_and_update(){if ipc_stop.changed().await.is_err(){break;}}}=>{}}
    if let Some(service) = frontier_for_shutdown {
        service.stop_notifications();
        if let Err(error) = service.stop_frontier().await {
            tracing::error!(%error,"Frontier shutdown failed");
        }
    }
    if let Some(controller) = hosted_for_shutdown {
        if let Err(error) = controller.shutdown().await {
            tracing::error!(%error, "hosted ownership controller stopped with error");
        }
    }
    if let Err(error) = runtime_for_shutdown.drain().await {
        tracing::error!(%error, "bounded account runtime drain failed");
    }
}

fn load_modes(
    health: &mut HealthResponse,
    data_dir: &std::path::Path,
) -> Result<(Option<HostedSettings>, Option<MessagingSettings>)> {
    let hosted_settings = native_config_path("DY_AGENT_HOSTED_CONFIG", data_dir, "hosted.json")
        .map(|path| HostedSettings::load(std::path::Path::new(&path)))
        .transpose()?;
    let messaging_settings: Option<MessagingSettings> =
        native_config_path("DY_AGENT_MESSAGING_CONFIG", data_dir, "messaging.json")
            .map(|path| dy_agent::runtime::messaging::read_private(std::path::Path::new(&path)))
            .transpose()?;
    if messaging_settings.is_some() {
        anyhow::ensure!(
            hosted_settings.is_some(),
            "native messaging requires hosted ownership"
        );
        let automatic = messaging_settings
            .as_ref()
            .is_some_and(|s| s.automation.iter().any(|p| p.enabled));
        health.protocol_mode = if automatic {
            "native-auto-ws"
        } else {
            "native-manual-ws"
        }
        .into();
        health.protocol_execution.account_worker = if automatic {
            "native_auto_ws"
        } else {
            "native_manual_ws"
        }
        .into();
    }
    Ok((hosted_settings, messaging_settings))
}

async fn start_messaging(
    settings: Option<MessagingSettings>,
    hosted: Option<&HostedSettings>,
    runtime: RuntimeHandle,
    store: Arc<CoreStore>,
) -> Result<Option<(Arc<ManualService>, String)>> {
    let messaging = if let Some(settings) = settings {
        let service = ManualService::load(&settings, hosted, runtime.clone(), store.clone())?;
        runtime
            .install_executor(
                service.clone(),
                ExecutionConfig {
                    max_in_flight: 8,
                    timeout: std::time::Duration::from_secs(40),
                },
            )
            .await?;
        service.register_accounts().await?;
        Some((service, settings.api_token))
    } else {
        None
    };
    Ok(messaging)
}

async fn shutdown_signal() {
    let ctrl_c = async {
        if let Err(error) = signal::ctrl_c().await {
            tracing::error!(%error, "cannot install Ctrl-C shutdown handler");
        }
    };

    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal as unix_signal, SignalKind};

        let terminate = async {
            match unix_signal(SignalKind::terminate()) {
                Ok(mut stream) => {
                    stream.recv().await;
                }
                Err(error) => {
                    tracing::error!(%error, "cannot install SIGTERM shutdown handler");
                    std::future::pending::<()>().await;
                }
            }
        };

        tokio::select! {
            () = ctrl_c => {},
            () = terminate => {},
        }
    }

    #[cfg(not(unix))]
    ctrl_c.await;
}
