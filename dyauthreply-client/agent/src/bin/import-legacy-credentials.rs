//! Native migration CLI; diagnostics never include keys, cookies or plaintext.
fn main() -> anyhow::Result<()> {
    let args: Vec<_> = std::env::args().collect();
    anyhow::ensure!(
        args.len() == 3,
        "import-legacy-credentials LEGACY_CLIENT_ROOT NATIVE_SNAPSHOT_DIRECTORY"
    );
    let report = dy_agent::credential_store::import_legacy(
        std::path::Path::new(&args[1]),
        std::path::Path::new(&args[2]),
    )?;
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}
