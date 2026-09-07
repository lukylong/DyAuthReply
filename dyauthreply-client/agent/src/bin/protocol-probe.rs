//! Explicit, credential-free native HTTPS diagnostic. Not an account health test.
use dy_agent::protocol::live_http::ProtocolHttpClient;
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let result = ProtocolHttpClient::new(1)?.probe().await?;
    println!("{}", serde_json::to_string_pretty(&result)?);
    Ok(())
}
