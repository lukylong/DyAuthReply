fn main() -> anyhow::Result<()> {
    let args: Vec<_> = std::env::args().collect();
    anyhow::ensure!(args.len() == 3, "import-legacy-audit LEGACY_DB NATIVE_ROOT");
    let audit = dy_agent::audit::AuditStore::open(std::path::Path::new(&args[2]))?;
    println!("{}", audit.import_legacy(std::path::Path::new(&args[1]))?);
    Ok(())
}
