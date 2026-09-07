//! Bounded public control-plane reads used by the local UI.
use super::{decode_json_response, NativeLicense};
use anyhow::{Context, Result};
use serde_json::{json, Value};

impl NativeLicense {
    /// # Errors
    /// Rejects malformed versions/URLs and unavailable or oversized authority responses.
    pub async fn app_update(&self, current: &str) -> Result<Value> {
        let current = if current.trim().is_empty() {
            option_env!("CLIENT_APP_VERSION").unwrap_or(env!("CARGO_PKG_VERSION"))
        } else {
            current.trim()
        };
        anyhow::ensure!(valid_version(current), "当前版本格式无效");
        let remote = self
            .public_get(&format!("{}/app-version", self.auth_prefix()))
            .await?;
        let latest = text(&remote, "version", 64)?;
        anyhow::ensure!(
            latest.is_empty() || valid_version(latest),
            "升级服务版本格式无效"
        );
        let macos = optional_url(&remote, "macos_url")?;
        let windows = optional_url(&remote, "windows_url")?;
        let release = optional_url(&remote, "release_page")?;
        let extension_version = text_or_empty(&remote, "extension_version", 64)?;
        anyhow::ensure!(
            extension_version.is_empty() || valid_version(extension_version),
            "插件版本格式无效"
        );
        let extension_file = extension_file(&remote)?;
        let extension_url = extension_url(&remote, &release, &extension_file)?;
        let download = match std::env::consts::OS {
            "macos" => macos,
            "windows" => windows,
            _ => release.clone(),
        };
        let has_update = newer(latest, current);
        Ok(json!({
            "current_version": current.trim_start_matches(['v','V']),
            "latest_version": latest,
            "has_update": has_update,
            "mandatory": remote["mandatory"].as_bool().unwrap_or(false) && has_update,
            "notes": text_or_empty(&remote,"notes",16*1024)?,
            "download_url": download,
            "release_page": release,
            "extension_version": extension_version,
            "extension_url": extension_url,
            "extension_file": extension_file,
        }))
    }

    /// # Errors
    /// Rejects excessive limits and malformed/oversized announcement catalog data.
    pub async fn announcements(&self, limit: u16) -> Result<Value> {
        anyhow::ensure!((1..=50).contains(&limit), "公告数量范围无效");
        let remote = self
            .public_get(&format!(
                "{}/announcements?limit={limit}",
                self.auth_prefix()
            ))
            .await?;
        let rows = match &remote {
            Value::Array(rows) => rows,
            Value::Object(_) if remote["code"] == 2000 => {
                remote["data"].as_array().context("公告响应格式错误")?
            }
            _ => anyhow::bail!("公告响应格式错误"),
        };
        anyhow::ensure!(rows.len() <= usize::from(limit), "公告响应数量超限");
        let mut result = Vec::with_capacity(rows.len());
        for row in rows {
            let id = text(row, "id", 128)?;
            let title = text(row, "title", 600)?;
            let content = text(row, "content", 16 * 1024)?;
            let level = text(row, "level", 16)?;
            anyhow::ensure!(
                !id.is_empty()
                    && !title.trim().is_empty()
                    && matches!(level, "info" | "warning" | "urgent"),
                "公告字段无效"
            );
            result.push(json!({
                "id":id,
                "title":title,
                "content":content,
                "level":level,
                "publish_time":optional_time(row,"publish_time")?,
                "expire_time":optional_time(row,"expire_time")?,
            }));
        }
        Ok(json!(result))
    }

    async fn public_get(&self, url: &str) -> Result<Value> {
        let response = self
            .client
            .get(url)
            .send()
            .await
            .map_err(|_| anyhow::anyhow!("授权服务暂时不可达"))?;
        decode_json_response(response).await
    }
}

fn text<'a>(value: &'a Value, key: &str, limit: usize) -> Result<&'a str> {
    let result = value[key].as_str().context("远程字段格式错误")?;
    anyhow::ensure!(
        result.len() <= limit
            && !result
                .chars()
                .any(|c| c.is_control() && !matches!(c, '\n' | '\r' | '\t')),
        "远程字段超过限制"
    );
    Ok(result)
}

fn text_or_empty<'a>(value: &'a Value, key: &str, limit: usize) -> Result<&'a str> {
    if value.get(key).is_none_or(Value::is_null) {
        return Ok("");
    }
    text(value, key, limit)
}

fn optional_time<'a>(value: &'a Value, key: &str) -> Result<Option<&'a str>> {
    if value.get(key).is_none_or(Value::is_null) {
        return Ok(None);
    }
    Ok(Some(text(value, key, 64)?))
}

fn optional_url(value: &Value, key: &str) -> Result<String> {
    let raw = text_or_empty(value, key, 4096)?.trim();
    if raw.is_empty() {
        return Ok(String::new());
    }
    let uri: wreq::Uri = raw.parse().context("升级地址格式错误")?;
    anyhow::ensure!(
        uri.scheme_str() == Some("https")
            || (uri.scheme_str() == Some("http")
                && matches!(uri.host(), Some("127.0.0.1" | "localhost"))),
        "升级地址必须使用HTTPS"
    );
    Ok(raw.into())
}

fn extension_file(value: &Value) -> Result<String> {
    let raw = text_or_empty(value, "extension_file", 128)?.trim();
    let name = if raw.is_empty() {
        "douyin-cred-extractor.zip"
    } else {
        raw
    };
    anyhow::ensure!(
        std::path::Path::new(name)
            .extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("zip"))
            && name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte)),
        "插件文件名无效"
    );
    Ok(name.into())
}

fn extension_url(remote: &Value, release: &str, file: &str) -> Result<String> {
    let configured = optional_url(remote, "extension_url")?;
    if !configured.is_empty() {
        return Ok(configured);
    }
    let base = release.trim_end_matches('/');
    let Some(repository) = base.strip_suffix("/releases/latest") else {
        anyhow::bail!("授权服务未提供浏览器插件下载地址");
    };
    let fallback = format!("{repository}/releases/latest/download/{file}");
    optional_url(&json!({"url":fallback}), "url")
}

fn valid_version(value: &str) -> bool {
    let value = value.trim().trim_start_matches(['v', 'V']);
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b".-+".contains(&b))
        && value.bytes().any(|b| b.is_ascii_digit())
}

fn version_parts(value: &str) -> Vec<u64> {
    value
        .trim()
        .trim_start_matches(['v', 'V'])
        .split(['-', '+'])
        .next()
        .unwrap_or("")
        .split('.')
        .map(|part| part.parse::<u64>().unwrap_or(0))
        .collect()
}

fn newer(latest: &str, current: &str) -> bool {
    let mut latest = version_parts(latest);
    let mut current = version_parts(current);
    let length = latest.len().max(current.len());
    latest.resize(length, 0);
    current.resize(length, 0);
    latest > current
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::license::LicenseConfig;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    async fn server(bodies: Vec<&'static str>) -> (String, tokio::task::JoinHandle<Vec<String>>) {
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let mut paths = Vec::new();
            for body in bodies {
                let (mut socket, _) = listener.accept().await.unwrap();
                let mut request = vec![0; 4096];
                let read = socket.read(&mut request).await.unwrap();
                let line = String::from_utf8_lossy(&request[..read])
                    .lines()
                    .next()
                    .unwrap_or("")
                    .to_string();
                paths.push(line);
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                socket.write_all(response.as_bytes()).await.unwrap();
            }
            paths
        });
        (format!("http://{address}"), task)
    }

    fn manager(root: &std::path::Path, server_url: String) -> std::sync::Arc<NativeLicense> {
        NativeLicense::new(LicenseConfig {
            server_url,
            public_key_pem: "test-public-key".into(),
            state_file: root.join("native-license.json"),
            api_token: "a".repeat(64),
        })
        .unwrap()
    }

    #[test]
    fn version_comparison_matches_legacy_numeric_contract() {
        assert!(newer("0.1.26", "v0.1.25"));
        assert!(newer("0.2", "0.1.99"));
        assert!(!newer("0.1.25-beta", "0.1.25"));
        assert!(!newer("0.1.2", "0.1.10"));
        assert!(valid_version("v0.1.25-beta+3"));
        assert!(!valid_version("../0.1.25"));
    }

    #[test]
    fn public_urls_require_https_except_explicit_loopback() {
        assert_eq!(
            optional_url(&json!({"url":"https://example.com/a"}), "url").unwrap(),
            "https://example.com/a"
        );
        assert!(optional_url(&json!({"url":"http://example.com/a"}), "url").is_err());
        assert!(optional_url(&json!({"url":"javascript:alert(1)"}), "url").is_err());
        assert!(optional_url(&json!({"url":"http://127.0.0.1:1/a"}), "url").is_ok());
        assert_eq!(
            extension_file(&json!({})).unwrap(),
            "douyin-cred-extractor.zip"
        );
        assert!(extension_file(&json!({"extension_file":"../plugin.zip"})).is_err());
        assert!(extension_file(&json!({"extension_file":"plugin.crx"})).is_err());
    }

    #[tokio::test]
    async fn public_catalog_is_bounded_normalized_and_uses_expected_routes() {
        let (url, task) = server(vec![
            r#"{"version":"0.1.26","mandatory":true,"notes":"line1\nline2","macos_url":"https://example.com/mac.zip","windows_url":"https://example.com/win.exe","release_page":"https://example.com/release","extension_version":"2.2.0","extension_url":"https://example.com/douyin-cred-extractor.zip","extension_file":"douyin-cred-extractor.zip"}"#,
            r#"[{"id":"one","title":"通知","content":"第一行\n第二行","level":"warning","publish_time":"2026-09-06T00:00:00+08:00","expire_time":null}]"#,
        ])
        .await;
        let root = tempfile::tempdir().unwrap();
        let manager = manager(root.path(), url);
        let update = manager.app_update("v0.1.25").await.unwrap();
        assert_eq!(update["current_version"], "0.1.25");
        assert_eq!(update["latest_version"], "0.1.26");
        assert_eq!(update["has_update"], true);
        assert_eq!(update["mandatory"], true);
        assert_eq!(update["extension_version"], "2.2.0");
        assert_eq!(
            update["extension_url"],
            "https://example.com/douyin-cred-extractor.zip"
        );
        let announcements = manager.announcements(10).await.unwrap();
        assert_eq!(announcements.as_array().unwrap().len(), 1);
        assert_eq!(announcements[0]["level"], "warning");
        assert!(manager.announcements(0).await.is_err());
        let paths = task.await.unwrap();
        assert!(paths[0].starts_with("GET /api/client-auth/app-version "));
        assert!(paths[1].starts_with("GET /api/client-auth/announcements?limit=10 "));
    }

    #[tokio::test]
    async fn older_authority_falls_back_to_rolling_release_asset() {
        let (url, task) = server(vec![
            r#"{"version":"0.1.25","mandatory":false,"notes":"","macos_url":"","windows_url":"","release_page":"https://github.com/example/dist/releases/latest"}"#,
        ])
        .await;
        let root = tempfile::tempdir().unwrap();
        let manager = manager(root.path(), url);

        let update = manager.app_update("0.1.25").await.unwrap();

        assert_eq!(update["extension_version"], "");
        assert_eq!(
            update["extension_url"],
            "https://github.com/example/dist/releases/latest/download/douyin-cred-extractor.zip"
        );
        assert_eq!(task.await.unwrap().len(), 1);
    }
}
