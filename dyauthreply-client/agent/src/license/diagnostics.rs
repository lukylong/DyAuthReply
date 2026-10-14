//! Opt-in diagnostics upload, authenticated by the activation like card sync.
use super::{string, NativeLicense};
use anyhow::Result;
use serde_json::Value;

impl NativeLicense {
    /// # Errors
    /// Rejects an unactivated client and reports server-side refusals (rate limit, size).
    pub async fn upload_diagnostics(&self, mut payload: Value) -> Result<Value> {
        let (id, token) = {
            let state = self
                .cache
                .read()
                .map_err(|_| anyhow::anyhow!("授权状态锁异常"))?;
            (
                string(&state, "activation_id").to_owned(),
                string(&state, "activation_token").to_owned(),
            )
        };
        anyhow::ensure!(!id.is_empty() && !token.is_empty(), "未激活，无法上报诊断");
        payload["activation_id"] = Value::String(id);
        payload["activation_token"] = Value::String(token);
        let result = self.post("telemetry/ingest", &payload).await?;
        anyhow::ensure!(result["ok"] == true, "诊断上报未被接受");
        Ok(result)
    }
}
