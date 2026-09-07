//! Native authorization diagnostic/renewal command; output is redacted public status.
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<_> = std::env::args().collect();
    anyhow::ensure!(
        args.len() == 3,
        "native-license CONFIG_FILE status|renew|activate|deactivate"
    );
    let config = dy_agent::runtime::messaging::read_private(std::path::Path::new(&args[1]))?;
    let manager = dy_agent::license::NativeLicense::new(config)?;
    let result = match args[2].as_str() {
        "status" => manager.status(),
        "renew" => manager.renew(true).await?,
        "activate" => {
            let mut code = String::new();
            std::io::stdin().read_line(&mut code)?;
            manager.activate(code.trim()).await?
        }
        "deactivate" => manager.deactivate().await?,
        _ => anyhow::bail!("unknown license command"),
    };
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}
