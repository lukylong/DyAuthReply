//! Explicit authenticated, read-only native account probe. No sends or worker takeover.
use dy_agent::protocol::{
    account_session::NativeAccountSession,
    credentials::{AccountCredentials, MAX_CREDENTIAL_BYTES},
    live_http::ProtocolHttpClient,
    native_signer::NativeSigner,
};
use serde_json::json;
use std::{env, fs::File, io::Read};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<_> = env::args().collect();
    anyhow::ensure!(
        args.len() >= 3 && args.len() <= 7 && args.len() % 2 == 1 && args[1] == "--credentials",
        "usage: account-probe --credentials FILE [--expect-message-id ID] [--cursor-us CURSOR]"
    );
    let mut expected_message_id = None;
    let mut cursor = None;
    for option in args[3..].as_chunks::<2>().0 {
        match option[0].as_str() {
            "--expect-message-id" if expected_message_id.is_none() => {
                expected_message_id = Some(option[1].parse::<u64>()?);
            }
            "--cursor-us" if cursor.is_none() => cursor = Some(option[1].parse::<u64>()?),
            _ => anyhow::bail!("unknown or duplicate probe option"),
        }
    }
    let file = File::open(&args[2])?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        anyhow::ensure!(
            file.metadata()?.permissions().mode().trailing_zeros() >= 6,
            "credential file must be owner-only"
        );
    }
    let mut bytes = Vec::new();
    file.take((MAX_CREDENTIAL_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    let credentials = AccountCredentials::import_json(&bytes)?;
    let account_id = credentials.account_id.to_string();
    let missing: Vec<_> = [
        "sessionid",
        "uifid",
        "passport_mfa_token",
        "x_tt_token",
        "passport_csrf_token",
        "s_v_web_id",
    ]
    .into_iter()
    .filter(|name| credentials.cookie("www.douyin.com", name).is_empty())
    .collect();
    let material = credentials.has_signing_material();
    let dtrait_blob = !credentials.dtrait_blob.is_empty();
    let dtrait_header = !credentials.dtrait_header.is_empty();
    let http = ProtocolHttpClient::for_user_agent(2, &credentials.user_agent)?;
    let mut session = NativeAccountSession::new(credentials, http, NativeSigner::new(2)?);
    let identity = match session.identity().await {
        Ok(value) => json!({"ready":true,"device_id_available":!value.device_id.is_empty()}),
        Err(error) => json!({"ready":false,"error":error}),
    };
    let inbox = match session.inbox(cursor.unwrap_or(0), 50).await {
        Ok(page) => {
            let context = if let Some(message) =
                page.messages.iter().find(|m| m.conversation_short_id > 0)
            {
                match session
                    .conversation_context(&message.conversation_id, message.conversation_short_id)
                    .await
                {
                    Ok(_) => json!({"ready":true}),
                    Err(error) => json!({"ready":false,"error":error}),
                }
            } else {
                json!({"ready":false,"reason":"no_conversation_in_page"})
            };
            json!({"protocol_status":page.status_code,"wrapper_present":page.wrapper_present,
                "expected_message_seen":expected_message_id.map(|id| page.messages.iter().any(|m|m.server_message_id==id)),
                "messages":page.messages.len(),"cursor_available":page.next_cursor>0,"conversation_context":context})
        }
        Err(error) => json!({"error":error}),
    };
    println!(
        "{}",
        serde_json::to_string_pretty(&json!({"account_id":account_id,"implementation":"rust",
        "signing_material_present":material,"dtrait_blob_present":dtrait_blob,"dtrait_header_present":dtrait_header,
        "missing_www_cookie_fields":missing,"ecdh_ready":session.ecdh_ready(),"identity":identity,"inbox":inbox,
        "send_capability":"unproven","worker_takeover":false}))?
    );
    Ok(())
}
