//! Read-only legacy chat import. No Python interpreter or credential export.
fn main() -> anyhow::Result<()> {
    let args: Vec<_> = std::env::args().collect();
    anyhow::ensure!(
        args.len() == 3,
        "import-legacy-business LEGACY_DB NATIVE_DATA_DIR"
    );
    let store = dy_agent::workbench::Workbench::open(std::path::Path::new(&args[2]))?;
    println!("{}", store.import_legacy(std::path::Path::new(&args[1]))?);
    Ok(())
}
