//! Challenge-bound desktop attachment and authenticated graceful lifecycle control.
use anyhow::{Context, Result};
use axum::{
    extract::{Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    net::SocketAddr,
    path::Path,
    sync::Arc,
};
use tokio::sync::watch;
use uuid::Uuid;

#[derive(Clone)]
pub struct DesktopIpc {
    inner: Arc<Inner>,
}
struct Inner {
    token: String,
    installation_id: Uuid,
    boot_id: Uuid,
    bind: String,
    binary_sha256: String,
    shutdown: watch::Sender<bool>,
}
#[derive(Serialize, Deserialize)]
pub struct Identity {
    pub api_version: u32,
    pub service: String,
    pub installation_id: Uuid,
    pub boot_id: Uuid,
    pub bind: String,
    pub binary_sha256: String,
    pub nonce: String,
    pub proof: String,
}
#[derive(Deserialize)]
struct Challenge {
    nonce: String,
}
#[derive(Deserialize)]
struct Shutdown {
    boot_id: Uuid,
}
impl DesktopIpc {
    /// # Errors
    /// Rejects invalid token material or an unreadable current executable.
    pub fn new(
        token: String,
        installation_id: Uuid,
        boot_id: Uuid,
        bind: SocketAddr,
    ) -> Result<(Self, watch::Receiver<bool>)> {
        anyhow::ensure!(
            (32..=256).contains(&token.len()),
            "invalid native IPC token"
        );
        let (shutdown, rx) = watch::channel(false);
        Ok((
            Self {
                inner: Arc::new(Inner {
                    token,
                    installation_id,
                    boot_id,
                    bind: bind.to_string(),
                    binary_sha256: binary_digest(&std::env::current_exe()?)?,
                    shutdown,
                }),
            },
            rx,
        ))
    }
    pub fn router(&self) -> Router {
        let public = Router::new()
            .route("/api/agent/v1/identity", get(identity))
            .with_state(self.clone());
        let private = Router::new()
            .route("/api/agent/v1/lifecycle/shutdown", post(shutdown))
            .with_state(self.clone());
        public.merge(crate::runtime::messaging::api::secure_router(
            private,
            self.inner.token.clone(),
        ))
    }
    /// Parent death closes the inherited pipe. One process-level blocking reader lives outside
    /// Tokio's blocking pool, so Ctrl-C cannot leave async runtime teardown waiting for stdin.
    pub fn watch_parent(&self) {
        if std::env::var_os("DY_AGENT_PARENT_STDIN").as_deref() != Some(std::ffi::OsStr::new("1")) {
            return;
        }
        let stop = self.inner.shutdown.clone();
        std::thread::spawn(move || {
            let mut byte = [0u8; 1];
            let mut stdin = std::io::stdin().lock();
            loop {
                match stdin.read(&mut byte) {
                    Ok(0) | Err(_) => {
                        stop.send_replace(true);
                        break;
                    }
                    Ok(_) => {}
                }
            }
        });
    }
}
fn proof_input(bind: &str, installation: Uuid, boot: Uuid, digest: &str, nonce: &str) -> String {
    format!("1\n{bind}\n{installation}\n{boot}\n{digest}\n{nonce}")
}
async fn identity(State(ipc): State<DesktopIpc>, Query(query): Query<Challenge>) -> Response {
    if query.nonce.len() != 32 || !query.nonce.bytes().all(|b| b.is_ascii_hexdigit()) {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let s = &ipc.inner;
    let mut mac =
        Hmac::<Sha256>::new_from_slice(s.token.as_bytes()).expect("HMAC accepts any key length");
    mac.update(
        proof_input(
            &s.bind,
            s.installation_id,
            s.boot_id,
            &s.binary_sha256,
            &query.nonce,
        )
        .as_bytes(),
    );
    let proof = format!("{:x}", mac.finalize().into_bytes());
    Json(Identity {
        api_version: 1,
        service: "dy-agent".into(),
        installation_id: s.installation_id,
        boot_id: s.boot_id,
        bind: s.bind.clone(),
        binary_sha256: s.binary_sha256.clone(),
        nonce: query.nonce,
        proof,
    })
    .into_response()
}
async fn shutdown(State(ipc): State<DesktopIpc>, Json(input): Json<Shutdown>) -> Response {
    if input.boot_id != ipc.inner.boot_id {
        return (
            StatusCode::CONFLICT,
            Json(serde_json::json!({"detail":"服务实例已变更，请重新确认"})),
        )
            .into_response();
    }
    ipc.inner.shutdown.send_replace(true);
    (
        StatusCode::ACCEPTED,
        Json(serde_json::json!({"status":"draining"})),
    )
        .into_response()
}
/// # Errors
/// Bounded streaming digest for identity binding; no executable bytes are exposed.
pub fn binary_digest(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    anyhow::ensure!(
        file.metadata()?.len() <= 256 * 1024 * 1024,
        "native binary exceeds bound"
    );
    let mut hash = Sha256::new();
    let mut chunk = [0u8; 8192];
    loop {
        let n = file.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        hash.update(&chunk[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
/// Creates owner-only IPC material and optional build-pinned authority config.
/// Existing remote authorities are retained. Legacy loopback authorities are retired when a
/// packaged build pins a remote authority, because the local development service is not bundled.
/// # Errors
/// Rejects mismatched tokens, invalid build trust material and write failures.
pub fn provision(root: &Path) -> Result<String> {
    let authority = pinned_authority()?;
    provision_with_authority(
        root,
        authority
            .as_ref()
            .map(|(server, key)| (*server, key.as_str())),
    )
}

fn provision_with_authority(root: &Path, authority: Option<(&str, &str)>) -> Result<String> {
    if let Some((server, key)) = authority {
        validate_authority(server, key)?;
    }
    let token_path = root.join("native-api-token");
    let config_path = root.join("native-license.json");
    let existing: Option<serde_json::Value> = root
        .join("native-license.json")
        .is_file()
        .then(|| crate::runtime::messaging::read_private(&config_path))
        .transpose()?;
    let token = if token_path.exists() {
        let token = std::fs::read_to_string(&token_path)?;
        token.trim().to_owned()
    } else if let Some(config) = &existing {
        config["api_token"]
            .as_str()
            .context("native API token missing")?
            .into()
    } else {
        format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple())
    };
    anyhow::ensure!(
        (32..=256).contains(&token.len()),
        "native API token malformed"
    );
    if !token_path.exists() {
        write_new(&token_path, token.as_bytes())?;
    }
    if let Some(mut config) = existing {
        anyhow::ensure!(
            config["api_token"] == token,
            "native license/IPC token mismatch"
        );
        if let Some((server, key)) = authority {
            let old_server = config["server_url"].as_str().unwrap_or_default();
            if is_loopback_authority(old_server) && !is_loopback_authority(server) {
                let state_file = client_parent(root)?.join(".license-state.json");
                reset_loopback_state(&state_file, server)?;
                config["server_url"] = serde_json::json!(server);
                config["public_key_pem"] = serde_json::json!(key);
                config["state_file"] = serde_json::json!(state_file);
                replace_private(&config_path, &serde_json::to_vec(&config)?)?;
            }
        }
    } else if let Some((server, key)) = authority {
        let config = serde_json::json!({"server_url":server,"public_key_pem":key,"state_file":client_parent(root)?.join(".license-state.json"),"api_token":token});
        write_new(&config_path, &serde_json::to_vec(&config)?)?;
    }
    if root.join("messaging.json").is_file() {
        let config: serde_json::Value =
            crate::runtime::messaging::read_private(&root.join("messaging.json"))?;
        anyhow::ensure!(
            config["api_token"] == token,
            "native messaging/IPC token mismatch"
        );
    }
    Ok(token)
}

fn pinned_authority() -> Result<Option<(&'static str, String)>> {
    use base64::Engine;
    let server = option_env!("CLIENT_LICENSE_SERVER_URL").filter(|value| !value.trim().is_empty());
    let encoded =
        option_env!("LICENSE_LEASE_PUBLIC_KEY_B64").filter(|value| !value.trim().is_empty());
    let (Some(server), Some(encoded)) = (server, encoded) else {
        anyhow::ensure!(
            server.is_none() && encoded.is_none(),
            "incomplete native authority"
        );
        return Ok(None);
    };
    let key = String::from_utf8(base64::engine::general_purpose::STANDARD.decode(encoded.trim())?)?;
    Ok(Some((server, key)))
}

fn validate_authority(server: &str, key: &str) -> Result<()> {
    use ed25519_dalek::pkcs8::DecodePublicKey;
    let uri: wreq::Uri = server.parse()?;
    anyhow::ensure!(
        uri.scheme_str() == Some("https") || is_loopback_authority(server),
        "invalid native authority URL"
    );
    ed25519_dalek::VerifyingKey::from_public_key_pem(key)?;
    Ok(())
}

fn client_parent(root: &Path) -> Result<&Path> {
    if root.file_name() == Some(std::ffi::OsStr::new("agent-v2")) {
        root.parent().context("native root requires client parent")
    } else {
        Ok(root)
    }
}

fn is_loopback_authority(value: &str) -> bool {
    value.parse::<wreq::Uri>().is_ok_and(|uri| {
        uri.scheme_str() == Some("http") && matches!(uri.host(), Some("127.0.0.1" | "localhost"))
    })
}

fn reset_loopback_state(path: &Path, server: &str) -> Result<()> {
    if !path.is_file() {
        return Ok(());
    }
    let current: serde_json::Value = crate::runtime::messaging::read_private(path)?;
    if !is_loopback_authority(current["server_url"].as_str().unwrap_or_default()) {
        return Ok(());
    }
    let mut next = serde_json::json!({
        "server_url": server,
        "local_state": "unactivated",
        "last_error": "",
        "app_version": option_env!("CLIENT_APP_VERSION").unwrap_or(env!("CARGO_PKG_VERSION")),
    });
    for field in ["device_fingerprint", "device_name", "os_type", "os_version"] {
        if !current[field].is_null() {
            next[field] = current[field].clone();
        }
    }
    let pending = path.with_extension("renewal.json");
    if pending.exists() {
        anyhow::ensure!(!pending.is_symlink(), "invalid renewal request path");
        std::fs::remove_file(pending)?;
    }
    replace_private(path, &serde_json::to_vec(&next)?)
}

fn replace_private(path: &Path, data: &[u8]) -> Result<()> {
    anyhow::ensure!(
        path.is_file() && !path.is_symlink(),
        "invalid private file path"
    );
    let temporary = path.with_extension(format!("{}.pending", Uuid::new_v4().simple()));
    write_new(&temporary, data)?;
    std::fs::rename(&temporary, path)?;
    #[cfg(unix)]
    File::open(path.parent().context("missing config parent")?)?.sync_all()?;
    Ok(())
}
fn write_new(path: &Path, data: &[u8]) -> Result<()> {
    let temporary = path.with_extension(format!("{}.pending", Uuid::new_v4().simple()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(data)?;
    file.sync_all()?;
    std::fs::rename(&temporary, path)?;
    #[cfg(unix)]
    File::open(path.parent().context("missing config parent")?)?.sync_all()?;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    use axum::{body::Body, http::Request};
    use tower::ServiceExt;
    #[tokio::test]
    async fn shutdown_requires_private_token_and_exact_boot() {
        let (ipc, rx) = DesktopIpc::new(
            "a".repeat(64),
            Uuid::new_v4(),
            Uuid::new_v4(),
            "127.0.0.1:18765".parse().unwrap(),
        )
        .unwrap();
        for (token, boot, status) in [
            ("wrong".into(), ipc.inner.boot_id, 401),
            ("a".repeat(64), Uuid::new_v4(), 409),
            ("a".repeat(64), ipc.inner.boot_id, 202),
        ] {
            let res = ipc
                .router()
                .oneshot(
                    Request::post("/api/agent/v1/lifecycle/shutdown")
                        .header("authorization", format!("Bearer {token}"))
                        .header("content-type", "application/json")
                        .body(Body::from(serde_json::json!({"boot_id":boot}).to_string()))
                        .unwrap(),
                )
                .await
                .unwrap();
            assert_eq!(res.status().as_u16(), status);
        }
        assert!(*rx.borrow());
    }
    #[test]
    fn provisioning_reuses_existing_token_and_rejects_mixed_config() {
        let dir = tempfile::tempdir().unwrap();
        let token = provision(dir.path()).unwrap();
        assert_eq!(token, provision(dir.path()).unwrap());
        let path = dir.path().join("messaging.json");
        write_new(&path, br#"{"api_token":"different"}"#).unwrap();
        assert!(provision(dir.path()).is_err());
    }

    #[test]
    fn packaged_authority_retires_loopback_config_and_stale_activation() {
        const PUBLIC_KEY: &str = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAtTl6mfAJdPjt302mOKJ9OJ9WllGFCdG4dfIraLYL8po=\n-----END PUBLIC KEY-----\n";
        let client = tempfile::tempdir().unwrap();
        let root = client.path().join("agent-v2");
        std::fs::create_dir_all(&root).unwrap();
        let token = "a".repeat(64);
        write_new(&root.join("native-api-token"), token.as_bytes()).unwrap();
        let state_file = client.path().join(".license-state.json");
        write_new(
            &root.join("native-license.json"),
            &serde_json::to_vec(&serde_json::json!({
                "server_url":"http://127.0.0.1:8000",
                "public_key_pem":PUBLIC_KEY,
                "state_file":state_file,
                "api_token":token,
            }))
            .unwrap(),
        )
        .unwrap();
        write_new(
            &state_file,
            &serde_json::to_vec(&serde_json::json!({
                "server_url":"http://127.0.0.1:8000",
                "activation_id":"local-only",
                "activation_token":"secret",
                "refresh_token":"secret",
                "lease_token":"secret",
                "device_fingerprint":"stable-device",
                "device_name":"test",
                "os_type":"test-os",
                "os_version":"1",
            }))
            .unwrap(),
        )
        .unwrap();
        write_new(
            &state_file.with_extension("renewal.json"),
            br#"{"request":{"activation_id":"local-only"}}"#,
        )
        .unwrap();

        let result =
            provision_with_authority(&root, Some(("https://pro.zhenyangtang.com.cn", PUBLIC_KEY)))
                .unwrap();
        assert_eq!(result, token);
        let config: serde_json::Value =
            crate::runtime::messaging::read_private(&root.join("native-license.json")).unwrap();
        let state: serde_json::Value =
            crate::runtime::messaging::read_private(&state_file).unwrap();
        assert_eq!(config["server_url"], "https://pro.zhenyangtang.com.cn");
        assert_eq!(config["api_token"], token);
        assert_eq!(state["server_url"], "https://pro.zhenyangtang.com.cn");
        assert_eq!(state["local_state"], "unactivated");
        assert_eq!(state["device_fingerprint"], "stable-device");
        assert!(state["activation_id"].is_null());
        assert!(!state_file.with_extension("renewal.json").exists());
        assert_eq!(
            provision_with_authority(&root, Some(("https://pro.zhenyangtang.com.cn", PUBLIC_KEY)),)
                .unwrap(),
            token
        );
    }

    #[test]
    fn packaged_authority_does_not_replace_an_existing_remote_authority() {
        const PUBLIC_KEY: &str = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAtTl6mfAJdPjt302mOKJ9OJ9WllGFCdG4dfIraLYL8po=\n-----END PUBLIC KEY-----\n";
        let client = tempfile::tempdir().unwrap();
        let root = client.path().join("agent-v2");
        std::fs::create_dir_all(&root).unwrap();
        let token = "b".repeat(64);
        write_new(&root.join("native-api-token"), token.as_bytes()).unwrap();
        write_new(
            &root.join("native-license.json"),
            &serde_json::to_vec(&serde_json::json!({
                "server_url":"https://custom.example",
                "public_key_pem":PUBLIC_KEY,
                "state_file":client.path().join(".license-state.json"),
                "api_token":token,
            }))
            .unwrap(),
        )
        .unwrap();
        provision_with_authority(&root, Some(("https://pro.zhenyangtang.com.cn", PUBLIC_KEY)))
            .unwrap();
        let config: serde_json::Value =
            crate::runtime::messaging::read_private(&root.join("native-license.json")).unwrap();
        assert_eq!(config["server_url"], "https://custom.example");
    }
}
