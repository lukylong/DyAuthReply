//! Native local activation/renewal producer. Remote authority remains Django.
pub mod api;
mod cards;
mod catalog;
mod state;
use anyhow::{Context, Result};
use futures_util::StreamExt;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    fs::{self, File},
    path::{Path, PathBuf},
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    sync::{watch, Mutex},
    task::JoinHandle,
};

#[derive(Deserialize, Clone)]
#[serde(deny_unknown_fields)]
pub struct LicenseConfig {
    pub server_url: String,
    pub public_key_pem: String,
    pub state_file: PathBuf,
    pub api_token: String,
}
#[derive(Debug, thiserror::Error)]
#[error("授权服务 HTTP {status}: {detail}")]
struct RemoteFailure {
    status: u16,
    detail: String,
}
pub struct NativeLicense {
    config: LicenseConfig,
    client: wreq::Client,
    operation: Mutex<()>,
    cache: std::sync::RwLock<Value>,
}
pub struct RenewalTask {
    stop: watch::Sender<bool>,
    join: JoinHandle<()>,
}
impl RenewalTask {
    /// # Errors
    /// Returns bounded transport, signature, ownership or persistence failures.
    pub async fn stop(self) -> Result<()> {
        self.stop.send_replace(true);
        self.join.await?;
        Ok(())
    }
}
pub(super) fn string<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key].as_str().unwrap_or("")
}
pub(super) fn now() -> Result<i64> {
    Ok(i64::try_from(
        SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
    )?)
}
pub(super) fn iso(seconds: i64) -> String {
    chrono::DateTime::from_timestamp(seconds, 0).map_or_else(String::new, |t| t.to_rfc3339())
}
fn next_due(value: &Value) -> Option<i64> {
    let text = string(value, "next_check_in_at");
    if let Ok(date) = chrono::DateTime::parse_from_rfc3339(text) {
        return Some(date.timestamp());
    }
    // Legacy Django's configured Asia/Shanghai timezone emitted naive ISO dates.
    chrono::NaiveDateTime::parse_from_str(text, "%Y-%m-%dT%H:%M:%S%.f")
        .ok()?
        .and_local_timezone(chrono_tz::Asia::Shanghai)
        .single()
        .map(|date| date.timestamp())
}
impl NativeLicense {
    /// # Errors
    /// Returns bounded transport, signature, ownership or persistence failures.
    pub fn new(mut config: LicenseConfig) -> Result<Arc<Self>> {
        config.server_url = config.server_url.trim_end_matches('/').into();
        anyhow::ensure!(
            (32..=256).contains(&config.api_token.len()) && config.public_key_pem.len() < 8192,
            "invalid native license configuration"
        );
        let uri: wreq::Uri = config.server_url.parse()?;
        anyhow::ensure!(
            uri.scheme_str() == Some("https")
                || (uri.scheme_str() == Some("http")
                    && matches!(uri.host(), Some("127.0.0.1" | "localhost"))),
            "license authority requires HTTPS or explicit loopback"
        );
        fs::create_dir_all(config.state_file.parent().context("license root missing")?)?;
        let _initial_lock = state::operation_lock(&config.state_file)?;
        let mut cached = state::load(&config.state_file)?;
        if string(&cached, "device_fingerprint").is_empty() {
            cached["device_fingerprint"] = json!(uuid::Uuid::new_v4().simple().to_string());
            cached["device_name"] = json!("本地设备");
            cached["os_type"] = json!(std::env::consts::OS);
            cached["os_version"] = json!("");
            cached["local_state"] = json!("unactivated");
            cached["server_url"] = json!(config.server_url);
            state::save(&config.state_file, &cached)?;
        }
        anyhow::ensure!(
            string(&cached, "server_url").is_empty()
                || string(&cached, "server_url").trim_end_matches('/') == config.server_url,
            "license authority changed; explicit activation required"
        );
        let client = wreq::Client::builder()
            .redirect(wreq::redirect::Policy::none())
            .retry(wreq::retry::Policy::never())
            .timeout(Duration::from_secs(12))
            .build()?;
        Ok(Arc::new(Self {
            config,
            client,
            operation: Mutex::new(()),
            cache: std::sync::RwLock::new(cached),
        }))
    }
    /// # Errors
    /// Builds private hosted settings only from the currently verified local entitlement.
    pub fn hosted_settings(
        &self,
        accounts: &[crate::credential_store::registry::AccountRecord],
    ) -> Result<Option<crate::runtime::hosted::HostedSettings>> {
        if self.status()["can_use_business"] != true || accounts.is_empty() {
            return Ok(None);
        }
        let current = self
            .cache
            .read()
            .map_err(|_| anyhow::anyhow!("授权状态锁异常"))?;
        let activation_id = uuid::Uuid::parse_str(string(&current, "activation_id"))?;
        let token = string(&current, "activation_token");
        anyhow::ensure!(!token.is_empty(), "授权凭证缺失");
        let operations = accounts
            .iter()
            .map(|a| crate::control_plane::AccountLeaseOperation {
                local_account_id: a.id.clone(),
                platform: "douyin".into(),
                platform_account_id: a.sec_uid.clone(),
                action: crate::control_plane::LeaseAction::Acquire,
                expected_epoch: 0,
            })
            .collect();
        Ok(Some(crate::runtime::hosted::HostedSettings {
            server: self.config.server_url.clone(),
            public_key_pem: self.config.public_key_pem.clone(),
            activation_id,
            activation_token: token.into(),
            accounts: operations,
            auth_state_file: Some(self.config.state_file.clone()),
        }))
    }
    pub fn token(&self) -> String {
        self.config.api_token.clone()
    }
    pub fn status(&self) -> Value {
        state::public(
            &self.config,
            &self
                .cache
                .read()
                .unwrap_or_else(std::sync::PoisonError::into_inner),
        )
    }
    pub fn state_file(&self) -> &Path {
        &self.config.state_file
    }
    fn cache(&self, value: Value) {
        *self
            .cache
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = value;
    }
    async fn file_lock(&self) -> Result<File> {
        let path = self.config.state_file.clone();
        tokio::task::spawn_blocking(move || state::operation_lock(&path)).await?
    }
    async fn post(&self, path: &str, payload: &Value) -> Result<Value> {
        let response = self
            .client
            .post(format!("{}/{path}", self.auth_prefix()))
            .header("content-type", "application/json")
            .body(serde_json::to_vec(payload)?)
            .send()
            .await
            .map_err(|_| anyhow::anyhow!("授权服务暂时不可达"))?;
        decode_json_response(response).await
    }
    pub(super) fn auth_prefix(&self) -> String {
        if self.config.server_url.ends_with("/api/client-auth") {
            self.config.server_url.clone()
        } else {
            format!("{}/api/client-auth", self.config.server_url)
        }
    }
    fn apply(&self, mut current: Value, remote: &Value, expected_sequence: u64) -> Result<Value> {
        for key in [
            "activation_id",
            "activation_token",
            "refresh_token",
            "license_key_id",
            "masked_code",
            "expires_at",
            "last_valid_until",
            "lease_token",
            "lease_expires_at",
            "lease_sequence",
            "heartbeat_interval_minutes",
            "grace_period_minutes",
            "plan",
        ] {
            if !remote[key].is_null() {
                current[key] = remote[key].clone();
            }
        }
        anyhow::ensure!(
            remote["status"] == "active",
            "license activation is not active"
        );
        current["activation_status"] = remote["status"].clone();
        current["server_url"] = json!(self.config.server_url);
        current["app_version"] =
            json!(option_env!("CLIENT_APP_VERSION").unwrap_or(env!("CARGO_PKG_VERSION")));
        let claims = state::verify(&self.config.public_key_pem, &current)?;
        anyhow::ensure!(
            claims.lease_sequence > expected_sequence,
            "stale license renewal sequence"
        );
        anyhow::ensure!(
            !string(&current, "activation_token").is_empty()
                && !string(&current, "refresh_token").is_empty(),
            "missing refreshed license credentials"
        );
        let time = now()?;
        current["local_state"] = json!(if time <= claims.exp {
            "active"
        } else if claims.grace_until.is_some_and(|until| time <= until) {
            "grace"
        } else {
            "expired"
        });
        current["last_error"] = json!("");
        current["last_check_in_at"] = json!(iso(time));
        let heartbeat = current["heartbeat_interval_minutes"]
            .as_i64()
            .unwrap_or(30)
            .clamp(1, 1440)
            * 60;
        current["next_check_in_at"] = json!(iso(claims
            .exp
            .saturating_sub(120)
            .min(time + heartbeat)
            .max(time + 1)));
        current["lease_expires_at"] = json!(iso(claims.exp));
        current["last_valid_until"] = json!(claims.grace_until.map(iso));
        current["lease_payload"] = serde_json::to_value(claims)?;
        Ok(current)
    }
    /// # Errors
    /// Returns bounded transport, signature, ownership or persistence failures.
    pub async fn renew(&self, force: bool) -> Result<Value> {
        let _local = self.operation.lock().await;
        let _file = self.file_lock().await?;
        let current = state::load(&self.config.state_file)?;
        if string(&current, "activation_id").is_empty() {
            self.cache(current);
            return Ok(self.status());
        }
        anyhow::ensure!(
            string(&current, "server_url").trim_end_matches('/') == self.config.server_url,
            "license authority mismatch"
        );
        let pending_path = self.config.state_file.with_extension("renewal.json");
        if !force
            && !pending_path.exists()
            && next_due(&current).is_some_and(|due| due > now().unwrap_or(i64::MAX))
            && state::verify(&self.config.public_key_pem, &current)
                .is_ok_and(|c| c.exp > now().unwrap_or(i64::MAX) + 120)
        {
            self.cache(current);
            return Ok(self.status());
        }
        let support = self.post("native-capabilities", &json!({})).await?;
        anyhow::ensure!(
            support["renewal_idempotency"] == 1,
            "授权服务需要更新原生续签协议"
        );
        let mut pending = state::load(&pending_path)?;
        let seq = current["lease_sequence"].as_u64().unwrap_or(0);
        if !pending.is_null()
            && pending["previous_sequence"]
                .as_u64()
                .is_some_and(|v| seq > v)
        {
            fs::remove_file(&pending_path)?;
            pending = json!({});
        }
        if pending["request"].is_null() {
            pending = json!({"server":self.config.server_url,"previous_sequence":seq,"request":{"request_id":uuid::Uuid::new_v4().to_string(),"activation_id":current["activation_id"],"refresh_token":current["refresh_token"],"app_version":option_env!("CLIENT_APP_VERSION").unwrap_or(env!("CARGO_PKG_VERSION")),"machine_meta":{"runtime":"rust"}}});
            state::save(&pending_path, &pending)?;
        }
        anyhow::ensure!(
            pending["server"] == self.config.server_url
                && pending["request"]["activation_id"] == current["activation_id"]
                && pending["previous_sequence"] == seq,
            "pending renewal binding mismatch"
        );
        let remote = match self.post("check-in", &pending["request"]).await {
            Ok(value) => value,
            Err(error) => {
                let mut failed = current.clone();
                if let Some(remote) = error.downcast_ref::<RemoteFailure>() {
                    if matches!(remote.status, 400 | 401 | 403) {
                        failed["local_state"] = json!(if remote.detail.contains("撤销")
                            || remote.detail.contains("激活状态不可用")
                        {
                            "revoked"
                        } else {
                            "invalid"
                        });
                    }
                }
                failed["last_error"] = json!(error.to_string());
                state::save(&self.config.state_file, &failed)?;
                self.cache(failed);
                return Err(error);
            }
        };
        anyhow::ensure!(
            remote["request_id"] == pending["request"]["request_id"],
            "authority lacks native idempotent renewal support"
        );
        anyhow::ensure!(
            remote["activation_id"] == current["activation_id"]
                && remote["license_key_id"] == current["license_key_id"],
            "renewal activation binding mismatch"
        );
        let next = self.apply(current, &remote, seq)?;
        state::save(&self.config.state_file, &next)?;
        self.cache(next);
        fs::remove_file(pending_path)?;
        Ok(self.status())
    }
    /// # Errors
    /// Returns bounded transport, signature, ownership or persistence failures.
    pub async fn activate(&self, code: &str) -> Result<Value> {
        anyhow::ensure!(
            !code.trim().is_empty() && code.len() <= 256,
            "请输入有效卡密"
        );
        let _local = self.operation.lock().await;
        let _file = self.file_lock().await?;
        let current = self
            .cache
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        let payload = json!({"license_code":code.trim(),"device_fingerprint":current["device_fingerprint"],"device_name":current["device_name"],"os_type":current["os_type"],"os_version":current["os_version"],"app_version":option_env!("CLIENT_APP_VERSION").unwrap_or(env!("CARGO_PKG_VERSION")),"machine_meta":{"runtime":"rust"}});
        let response = self.post("activate", &payload).await?;
        let mut next = self.apply(current, &response, 0)?;
        next["activated_at"] = json!(iso(now()?));
        state::save(&self.config.state_file, &next)?;
        self.cache(next);
        let pending = self.config.state_file.with_extension("renewal.json");
        if pending.exists() {
            fs::remove_file(pending)?;
        }
        Ok(self.status())
    }
    /// # Errors
    /// Returns bounded transport, signature, ownership or persistence failures.
    pub async fn deactivate(&self) -> Result<Value> {
        let _local = self.operation.lock().await;
        let _file = self.file_lock().await?;
        let mut current = state::load(&self.config.state_file)?;
        let confirmed=self.post("deactivate",&json!({"activation_id":current["activation_id"],"activation_token":current["activation_token"],"reason":"客户端主动解绑"})).await?;
        anyhow::ensure!(confirmed["ok"] == true, "解绑尚未确认");
        for field in [
            "activation_id",
            "activation_token",
            "refresh_token",
            "lease_token",
            "license_key_id",
            "masked_code",
        ] {
            current[field] = json!("");
        }
        for field in [
            "plan",
            "lease_payload",
            "activated_at",
            "last_check_in_at",
            "next_check_in_at",
            "last_valid_until",
            "lease_expires_at",
            "expires_at",
        ] {
            current[field] = Value::Null;
        }
        current["lease_sequence"] = json!(0);
        current["activation_status"] = json!("deactivated");
        current["local_state"] = json!("unactivated");
        current["last_error"] = json!("");
        state::save(&self.config.state_file, &current)?;
        self.cache(current);
        let pending = self.config.state_file.with_extension("renewal.json");
        if pending.exists() {
            fs::remove_file(pending)?;
        }
        Ok(self.status())
    }
    pub fn start(self: &Arc<Self>, mut ticks: watch::Receiver<u64>) -> RenewalTask {
        let manager = self.clone();
        let (stop, mut stopped) = watch::channel(false);
        let join = tokio::spawn(async move {
            let mut next = 0;
            loop {
                tokio::select! { biased; _=stopped.changed()=>break, changed=ticks.changed()=>{if changed.is_err(){break;}let at=*ticks.borrow_and_update();if at==u64::MAX{break;}
                    if at<next {continue;}
                    let result=manager.renew(false).await;next=at.saturating_add(if result.is_ok(){30_000}else{10_000});
                    if result.is_err(){tracing::warn!("native license renewal pending; retry retains durable request identity");}
                }}
            }
        });
        RenewalTask { stop, join }
    }
}

pub(super) async fn decode_json_response(response: wreq::Response) -> Result<Value> {
    let status = response.status().as_u16();
    let mut data = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| anyhow::anyhow!("授权响应读取失败"))?;
        anyhow::ensure!(
            data.len() + chunk.len() <= 256 * 1024,
            "授权响应超过大小限制"
        );
        data.extend(chunk);
    }
    if !(200..300).contains(&status) {
        let body: Value = serde_json::from_slice(&data).unwrap_or(Value::Null);
        let detail = body["detail"]
            .as_str()
            .or_else(|| body["message"].as_str())
            .unwrap_or("授权请求失败")
            .chars()
            .take(256)
            .collect();
        return Err(RemoteFailure { status, detail }.into());
    }
    serde_json::from_slice(&data).context("授权响应格式错误")
}

#[cfg(test)]
mod tests;
