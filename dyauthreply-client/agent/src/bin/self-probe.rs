//! Explicit read-only native account-affinity probe. Never sends a message.
use dy_agent::{
    protocol::{
        account_session::NativeAccountSession, credentials::AccountCredentials,
        live_http::ProtocolHttpClient, native_signer::NativeSigner,
    },
    runtime::messaging::read_private,
};
#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<_> = std::env::args().collect();
    anyhow::ensure!(
        args.len() == 3 && args[1] == "--credentials",
        "self-probe --credentials FILE"
    );
    let raw: serde_json::Value = read_private(std::path::Path::new(&args[2]))?;
    let credentials = AccountCredentials::import_json(&serde_json::to_vec(&raw)?)?;
    let account = credentials.account_id.to_string();
    let http = ProtocolHttpClient::for_user_agent(2, &credentials.user_agent)?;
    let mut session = NativeAccountSession::new(credentials, http, NativeSigner::new(2)?);
    let result = session.verify_self().await?;
    println!(
        "{}",
        serde_json::json!({"account_id":account,"verified":true,"user_id_present":!result.user_id.is_empty(),"nickname_present":!result.nickname.is_empty()})
    );
    Ok(())
}
