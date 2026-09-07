//! Authenticated card projection from the local business source to public Django landing pages.
use super::{decode_json_response, string, NativeLicense};
use anyhow::{Context, Result};
use serde_json::{json, Value};
use std::path::Path;

const MAX_COVER_BYTES: usize = 2 * 1024 * 1024;

impl NativeLicense {
    /// # Errors
    /// Rejects inactive authorization, malformed card data or failed remote projection.
    pub async fn sync_card(&self, card: &Value) -> Result<Value> {
        let (activation_id, activation_token) = self.activation()?;
        let id = card_text(card, "id", 128)?;
        uuid::Uuid::parse_str(id).context("卡片ID无效")?;
        let title = card_text(card, "title", 600)?;
        let target = card_text(card, "target_url", 4096)?;
        anyhow::ensure!(!title.trim().is_empty(), "卡片标题无效");
        valid_http_url(target)?;
        let cover_file_id = optional_nullable_text(card, "cover_file_id", 128)?;
        let payload = json!({
            "activation_id":activation_id,
            "activation_token":activation_token,
            "id":id,
            "title":title,
            "description":optional_text(card,"description",4096)?,
            "cover_file_id":cover_file_id,
            "target_url":target,
            "remark":optional_nullable_text(card,"remark",4096)?,
            "status":card["status"].as_bool().unwrap_or(true),
        });
        let result = self.post("cards/upsert", &payload).await?;
        anyhow::ensure!(
            result["ok"] == true && result["id"] == id,
            "卡片同步响应不匹配"
        );
        let landing = card_text(&result, "landing_url", 4096)?;
        valid_public_url(landing)?;
        let uri: wreq::Uri = landing.parse().context("卡片公网链接格式错误")?;
        let scheme = uri.scheme_str().context("卡片公网链接缺少协议")?;
        let authority = uri.authority().context("卡片公网链接缺少主机")?;
        let cover_url = cover_file_id
            .as_str()
            .filter(|value| !value.is_empty())
            .map(|id| format!("{scheme}://{authority}/api/core/file_manager/proxy/{id}"));
        Ok(json!({"landing_url":landing,"cover_url":cover_url}))
    }

    /// # Errors
    /// Rejects inactive authorization, invalid identity or failed remote deletion.
    pub async fn delete_card(&self, id: &str) -> Result<()> {
        uuid::Uuid::parse_str(id).context("卡片ID无效")?;
        let (activation_id, activation_token) = self.activation()?;
        let result = self
            .post(
                "cards/delete",
                &json!({"activation_id":activation_id,"activation_token":activation_token,"id":id}),
            )
            .await?;
        anyhow::ensure!(
            result["ok"] == true && result["id"] == id,
            "卡片删除响应不匹配"
        );
        Ok(())
    }

    /// # Errors
    /// Accepts a bounded image only and validates the public response before returning it.
    pub async fn upload_card_cover(&self, name: &str, mime: &str, bytes: Vec<u8>) -> Result<Value> {
        validate_cover(name, mime, &bytes)?;
        let (activation_id, activation_token) = self.activation()?;
        let part = wreq::multipart::Part::bytes(bytes)
            .file_name(name.to_owned())
            .mime_str(mime)?;
        let form = wreq::multipart::Form::new()
            .text("activation_id", activation_id)
            .text("activation_token", activation_token)
            .part("file", part);
        let response = self
            .client
            .post(format!("{}/cards/cover", self.auth_prefix()))
            .multipart(form)
            .send()
            .await
            .map_err(|_| anyhow::anyhow!("封面上传服务暂时不可达"))?;
        let result = decode_json_response(response).await?;
        let id = card_text(&result, "cover_file_id", 128)?;
        uuid::Uuid::parse_str(id).context("封面文件ID无效")?;
        let url = card_text(&result, "cover_url", 4096)?;
        valid_public_url(url)?;
        Ok(json!({"cover_file_id":id,"cover_url":url}))
    }

    fn activation(&self) -> Result<(String, String)> {
        anyhow::ensure!(self.status()["can_use_business"] == true, "当前授权不可用");
        let state = self
            .cache
            .read()
            .map_err(|_| anyhow::anyhow!("授权状态锁异常"))?;
        let id = string(&state, "activation_id");
        let token = string(&state, "activation_token");
        anyhow::ensure!(!id.is_empty() && !token.is_empty(), "授权凭证缺失");
        Ok((id.into(), token.into()))
    }
}

fn card_text<'a>(value: &'a Value, key: &str, limit: usize) -> Result<&'a str> {
    let text = value[key].as_str().context("卡片字段格式错误")?;
    anyhow::ensure!(
        text.len() <= limit
            && !text
                .chars()
                .any(|c| c.is_control() && !matches!(c, '\n' | '\r' | '\t')),
        "卡片字段超过限制"
    );
    Ok(text)
}

fn optional_text<'a>(value: &'a Value, key: &str, limit: usize) -> Result<&'a str> {
    if value.get(key).is_none_or(Value::is_null) {
        return Ok("");
    }
    card_text(value, key, limit)
}

fn optional_nullable_text(value: &Value, key: &str, limit: usize) -> Result<Value> {
    if value.get(key).is_none_or(Value::is_null) {
        return Ok(Value::Null);
    }
    Ok(json!(card_text(value, key, limit)?))
}

fn valid_http_url(raw: &str) -> Result<()> {
    let uri: wreq::Uri = raw.parse().context("卡片目标链接格式错误")?;
    anyhow::ensure!(
        matches!(uri.scheme_str(), Some("http" | "https")) && uri.host().is_some(),
        "卡片目标链接仅支持HTTP/HTTPS"
    );
    Ok(())
}

fn valid_public_url(raw: &str) -> Result<()> {
    let uri: wreq::Uri = raw.parse().context("卡片公网链接格式错误")?;
    anyhow::ensure!(
        uri.scheme_str() == Some("https")
            || (uri.scheme_str() == Some("http")
                && matches!(uri.host(), Some("127.0.0.1" | "localhost"))),
        "卡片公网链接必须使用HTTPS"
    );
    Ok(())
}

fn validate_cover(name: &str, mime: &str, bytes: &[u8]) -> Result<()> {
    let path = Path::new(name);
    anyhow::ensure!(
        !bytes.is_empty()
            && bytes.len() <= MAX_COVER_BYTES
            && path.file_name().and_then(|value| value.to_str()) == Some(name)
            && name.len() <= 255
            && !name.chars().any(char::is_control),
        "封面文件无效或过大"
    );
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    anyhow::ensure!(
        matches!(
            extension.as_str(),
            "jpg" | "jpeg" | "png" | "webp" | "gif" | "bmp"
        ) && matches!(
            mime,
            "image/jpeg" | "image/png" | "image/webp" | "image/gif" | "image/bmp"
        ),
        "封面仅支持指定图片格式"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn card_urls_and_cover_boundaries_are_strict() {
        assert!(valid_http_url("https://example.com/a").is_ok());
        assert!(valid_http_url("javascript:alert(1)").is_err());
        assert!(valid_public_url("http://example.com/a").is_err());
        assert!(validate_cover("cover.png", "image/png", &[1]).is_ok());
        assert!(validate_cover("../cover.png", "image/png", &[1]).is_err());
        assert!(validate_cover("cover.html", "text/html", &[1]).is_err());
        assert!(validate_cover("cover.png", "image/png", &[]).is_err());
    }
}
