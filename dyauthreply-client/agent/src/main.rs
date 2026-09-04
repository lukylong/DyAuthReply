use std::{
    env,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};

use anyhow::{bail, Context, Result};
use dy_agent::{
    config::AgentConfig,
    health::{router, HealthResponse, StorageHealthResponse, StorageStartupSnapshot},
    identity,
    protocol::fixtures::verify_embedded_corpus,
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
    health: HealthResponse,
    _store: Arc<CoreStore>,
    _segment_store: SegmentStore,
) -> Result<()> {
    let listener = TcpListener::bind(config.bind_addr)
        .await
        .with_context(|| format!("cannot bind Agent health server to {}", config.bind_addr))?;
    info!(
        address = %config.bind_addr,
        data_dir = %config.data_dir.display(),
        protocol_mode = %health.protocol_mode,
        protocol_parity_verified = health.protocol_parity_verified,
        protocol_parity_all_verified = health.protocol_parity_all_verified,
        "dy-agent runtime is ready"
    );

    axum::serve(listener, router(health))
        .with_graceful_shutdown(shutdown_signal())
        .await
        .context("Agent health server failed")
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
