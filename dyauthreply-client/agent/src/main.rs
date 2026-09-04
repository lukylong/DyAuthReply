use std::env;

use anyhow::{bail, Context, Result};
use dy_agent::{
    config::AgentConfig,
    health::{router, HealthResponse},
    identity,
    protocol::fixtures::verify_embedded_corpus,
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
    let store = CoreStore::open(&config.data_dir)?;
    let schema_version = store.schema_version()?;
    if schema_version != CORE_SCHEMA_VERSION {
        bail!("unsupported core schema version {schema_version}; expected {CORE_SCHEMA_VERSION}");
    }

    let health = HealthResponse::foundation(identity.installation_id, identity.boot_id, &parity);
    match command {
        Command::Check => {
            println!(
                "{}",
                serde_json::to_string_pretty(&health).context("cannot serialize health result")?
            );
        }
        Command::Serve => serve(config, health, store).await?,
        Command::Version | Command::VerifyProtocol => {
            unreachable!("offline commands exit before initializing the Agent")
        }
    }

    Ok(())
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

async fn serve(config: AgentConfig, health: HealthResponse, _store: CoreStore) -> Result<()> {
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
