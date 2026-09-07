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
/// Creates owner-only IPC material and optional build-pinned first-install authority config.
/// Existing configurations are verified, never overwritten by build defaults.
/// # Errors
/// Rejects mismatched tokens, invalid build trust material and write failures.
pub fn provision(root: &Path) -> Result<String> {
    let token_path = root.join("native-api-token");
    let existing: Option<serde_json::Value> = root
        .join("native-license.json")
        .is_file()
        .then(|| crate::runtime::messaging::read_private(&root.join("native-license.json")))
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
    if let Some(config) = existing {
        anyhow::ensure!(
            config["api_token"] == token,
            "native license/IPC token mismatch"
        );
    } else if let (Some(server), Some(key)) = (
        option_env!("CLIENT_LICENSE_SERVER_URL").filter(|v| !v.trim().is_empty()),
        option_env!("LICENSE_LEASE_PUBLIC_KEY_B64").filter(|v| !v.trim().is_empty()),
    ) {
        use base64::Engine;
        use ed25519_dalek::pkcs8::DecodePublicKey;
        let key = String::from_utf8(base64::engine::general_purpose::STANDARD.decode(key.trim())?)?;
        ed25519_dalek::VerifyingKey::from_public_key_pem(&key)?;
        let parent = if root.file_name() == Some(std::ffi::OsStr::new("agent-v2")) {
            root.parent()
                .context("native root requires client parent")?
        } else {
            root
        };
        let config = serde_json::json!({"server_url":server,"public_key_pem":key,"state_file":parent.join(".license-state.json"),"api_token":token});
        write_new(
            &root.join("native-license.json"),
            &serde_json::to_vec(&config)?,
        )?;
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
}
