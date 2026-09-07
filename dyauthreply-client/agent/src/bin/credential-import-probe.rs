//! Explicit local test driver: decrypts only in Rust memory, never prints/saves a credential bundle.
use anyhow::{Context, Result};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};
#[tokio::main]
async fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().collect();
    anyhow::ensure!(
        (3..=4).contains(&args.len()),
        "credential-import-probe NATIVE_ROOT SOURCE_ACCOUNT_ID [TARGET_ACCOUNT_ID]"
    );
    let root = PathBuf::from(&args[1]);
    let registry = dy_agent::credential_store::registry::Registry::open(&root)?;
    let source = registry.load(&args[2])?;
    let cookie = source.storage_state["cookies"]
        .as_array()
        .context("cookies missing")?
        .iter()
        .map(|v| {
            format!(
                "{}={}",
                v["name"].as_str().unwrap_or(""),
                v["value"].as_str().unwrap_or("")
            )
        })
        .collect::<Vec<_>>()
        .join("; ");
    let mut server = source.storage_state["_bd_ticket"].clone();
    let key = server
        .as_object_mut()
        .context("ticket missing")?
        .remove("private_key")
        .unwrap_or(Value::Null);
    let package = json!({"cookie":cookie,"cookie_headers":source.storage_state["_cookie_headers"],"ticket_guard_server_data":URL_SAFE_NO_PAD.encode(serde_json::to_vec(&server)?),"keys":{"ec_privateKey":key},"ua":source.user_agent,"sec_uid":source.expected_sec_uid,"dtrait_blob":source.storage_state["_dtrait"]["blob"],"session_dtrait":source.storage_state["_dtrait"]["header"],"session_dtrait_path":source.storage_state["_dtrait"]["path"]});
    let bundle = format!(
        "DYCRED1.{}",
        URL_SAFE_NO_PAD.encode(serde_json::to_vec(&package)?)
    );
    let token = std::fs::read_to_string(root.join("native-api-token"))?;
    let endpoint = args.get(3).map_or_else(
        || "/api/client/v1/douyin/account/quick-create".into(),
        |id| format!("/api/client/v1/douyin/account/{id}/import-credential"),
    );
    let client = wreq::Client::builder().no_proxy().build()?;
    let response = client
        .post(format!("http://127.0.0.1:18765{endpoint}"))
        .header("Authorization", format!("Bearer {}", token.trim()))
        .header("Content-Type", "application/json")
        .body(serde_json::to_vec(&json!({"bundle":bundle}))?)
        .timeout(Duration::from_secs(40))
        .send()
        .await?;
    let status = response.status().as_u16();
    let result: Value = serde_json::from_slice(&response.bytes().await?)?;
    println!(
        "{}",
        json!({"http_status":status,"id":result["id"],"generation":result["generation"],"reload_required":result["_runtime_reload"],"success":result["success"],"error":result["detail"]})
    );
    anyhow::ensure!(
        (200..300).contains(&status),
        "credential import was rejected"
    );
    Ok(())
}
