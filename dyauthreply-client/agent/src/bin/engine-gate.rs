//! Process-level engine exclusion probe. No database, network or account cookie.
use std::io::{self, Write};
fn main() -> anyhow::Result<()> {
    let args: Vec<_> = std::env::args().collect();
    anyhow::ensure!(
        args.len() == 3,
        "engine-gate CLIENT_ROOT NATIVE_DATA_DIR (holds until stdin closes)"
    );
    let _gate = dy_agent::engine_gate::EngineGate::acquire_native(
        std::path::Path::new(&args[1]),
        std::path::Path::new(&args[2]),
    )?;
    println!("NATIVE_ENGINE_OWNED");
    io::stdout().flush()?;
    let mut line = String::new();
    io::stdin().read_line(&mut line)?;
    Ok(())
}
