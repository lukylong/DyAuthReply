//! Native configuration import, without Python or plaintext credential export.
fn main() -> anyhow::Result<()> {
    let args: Vec<_> = std::env::args().collect();
    anyhow::ensure!(
        (3..=4).contains(&args.len()),
        "import-legacy-settings LEGACY_DB NATIVE_ROOT [CARD_PUBLIC_BASE_URL]"
    );
    println!(
        "{}",
        dy_agent::business::import_legacy(
            std::path::Path::new(&args[1]),
            std::path::Path::new(&args[2]),
            args.get(3).map_or("", String::as_str)
        )?
    );
    Ok(())
}
