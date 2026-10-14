//! Pinned quick-auth browser archive. The China mirror is tried first and Google's
//! origin second, but only the digest pinned in the shipped client is trusted, so a
//! mirror can speed a download up and never change what gets installed.
use super::{
    component::{valid_digest, valid_version},
    installer::Progress,
};
use anyhow::{Context, Result};
use futures_util::StreamExt;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, future::Future, path::Path, time::Duration};
use tokio::io::AsyncWriteExt;

const PIN: &str = include_str!("../../quick-auth-component.json");
const MAX_ARCHIVE_BYTES: u64 = 512 * 1024 * 1024;
/// A source that stops sending for this long is abandoned for the next one.
const IDLE_TIMEOUT: Duration = Duration::from_secs(45);
const SOURCE_TIMEOUT: Duration = Duration::from_mins(30);

#[derive(Deserialize)]
struct Pin {
    version: String,
    /// Ordered URL templates with `{version}` and `{platform}`; first reachable wins.
    sources: Vec<String>,
    platforms: BTreeMap<String, PinnedArchive>,
}

#[derive(Clone, Deserialize)]
pub struct PinnedArchive {
    pub sha256: String,
    pub size: u64,
}

pub struct PinnedSpec {
    pub version: String,
    pub archive: PinnedArchive,
    pub sources: Vec<String>,
}

/// # Errors
/// Rejects a malformed shipped pin or a platform it does not cover.
pub fn pinned(platform: &'static str) -> Result<PinnedSpec> {
    let pin: Pin = serde_json::from_str(PIN).context("快捷登录组件版本清单无效")?;
    anyhow::ensure!(valid_version(&pin.version), "快捷登录组件版本无效");
    let archive = pin
        .platforms
        .get(platform)
        .cloned()
        .context("快捷登录组件不支持当前系统")?;
    anyhow::ensure!(
        valid_digest(&archive.sha256) && (1024 * 1024..=MAX_ARCHIVE_BYTES).contains(&archive.size),
        "快捷登录组件版本清单无效"
    );
    let sources = pin
        .sources
        .iter()
        .map(|template| source_url(template, &pin.version, platform))
        .collect::<Result<Vec<_>>>()?;
    anyhow::ensure!((1..=8).contains(&sources.len()), "快捷登录组件下载源无效");
    Ok(PinnedSpec {
        version: pin.version,
        archive,
        sources,
    })
}

/// Expands one source template. Only HTTPS without query/fragment is accepted; the
/// archive digest, not the host, is what establishes trust.
fn source_url(template: &str, version: &str, platform: &str) -> Result<String> {
    anyhow::ensure!(
        template.contains("{version}") && template.contains("{platform}"),
        "快捷登录组件下载源无效"
    );
    let url = template
        .replace("{version}", version)
        .replace("{platform}", platform);
    let uri: wreq::Uri = url.parse()?;
    anyhow::ensure!(
        uri.scheme_str() == Some("https")
            && uri.host().is_some_and(|host| !host.is_empty())
            && uri.query().is_none()
            && !url.contains('#'),
        "快捷登录组件下载源无效"
    );
    Ok(url)
}

/// Downloads the pinned archive, falling back across sources until one matches the pin.
/// Returns the source actually used.
/// # Errors
/// Fails only when every source is unreachable, stalls, or serves different bytes.
pub async fn download_verified(
    spec: &PinnedSpec,
    output: &Path,
    progress: Progress,
) -> Result<String> {
    first_verified(&spec.sources, &spec.archive.sha256, |url| {
        let progress = progress.clone();
        let size = spec.archive.size;
        async move { fetch(&url, output, size, progress).await }
    })
    .await
}

async fn first_verified<F, Fut>(urls: &[String], expected: &str, mut attempt: F) -> Result<String>
where
    F: FnMut(String) -> Fut,
    Fut: Future<Output = Result<String>>,
{
    for url in urls {
        match attempt(url.clone()).await {
            Ok(digest) if digest.eq_ignore_ascii_case(expected) => {
                tracing::info!(source = %url, "quick-auth component downloaded and verified");
                return Ok(url.clone());
            }
            Ok(_) => {
                tracing::warn!(source = %url, "quick-auth component digest mismatch; trying next source");
            }
            Err(error) => {
                tracing::warn!(source = %url, %error, "quick-auth component source failed; trying next source");
            }
        }
    }
    anyhow::bail!("快捷登录组件下载失败：国内镜像与官方源均不可用或校验未通过，请检查网络后重试")
}

async fn fetch(url: &str, output: &Path, expected_size: u64, progress: Progress) -> Result<String> {
    let response = wreq::Client::builder()
        .redirect(wreq::redirect::Policy::none())
        .retry(wreq::retry::Policy::never())
        .timeout(SOURCE_TIMEOUT)
        .build()?
        .get(url)
        .send()
        .await?;
    anyhow::ensure!(response.status().is_success(), "HTTP {}", response.status());
    if let Some(length) = response.content_length() {
        anyhow::ensure!(length == expected_size, "unexpected archive size {length}");
    }
    let mut file = tokio::fs::File::create(output).await?;
    let mut stream = response.bytes_stream();
    let mut digest = Sha256::new();
    let mut downloaded = 0_u64;
    while let Some(chunk) = tokio::time::timeout(IDLE_TIMEOUT, stream.next())
        .await
        .context("download stalled")?
    {
        let chunk = chunk?;
        downloaded = downloaded
            .checked_add(u64::try_from(chunk.len())?)
            .context("archive size overflow")?;
        anyhow::ensure!(downloaded <= expected_size, "archive larger than pinned");
        digest.update(&chunk);
        file.write_all(&chunk).await?;
        progress(downloaded, expected_size);
    }
    anyhow::ensure!(downloaded == expected_size, "archive incomplete");
    file.sync_all().await?;
    Ok(format!("{:x}", digest.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shipped_pin_covers_every_supported_platform() {
        for platform in ["mac-arm64", "mac-x64", "win64"] {
            let spec = pinned(platform).unwrap();
            assert!(valid_version(&spec.version));
            assert!(valid_digest(&spec.archive.sha256));
        }
    }

    #[test]
    fn shipped_sources_keep_the_official_origin_as_last_resort() {
        let spec = pinned("win64").unwrap();
        assert!(spec
            .sources
            .iter()
            .all(|url| url.ends_with("/win64/chrome-win64.zip")));
        assert_eq!(
            spec.sources.last().unwrap(),
            &format!(
                "https://storage.googleapis.com/chrome-for-testing-public/{}/win64/chrome-win64.zip",
                spec.version
            )
        );
    }

    #[test]
    fn source_templates_must_be_plain_https_for_the_exact_archive() {
        assert_eq!(
            source_url(
                "https://oss.example.cn/cft/{version}/{platform}/chrome-{platform}.zip",
                "1.2.3",
                "win64"
            )
            .unwrap(),
            "https://oss.example.cn/cft/1.2.3/win64/chrome-win64.zip"
        );
        for bad in [
            "http://oss.example.cn/{version}/{platform}.zip",
            "https://oss.example.cn/fixed.zip",
            "https://oss.example.cn/{version}/{platform}.zip?token=1",
            "https://oss.example.cn/{version}/{platform}.zip#x",
        ] {
            assert!(source_url(bad, "1.2.3", "win64").is_err(), "{bad}");
        }
    }

    #[tokio::test]
    async fn tampered_or_failing_mirror_falls_back_to_a_verified_source() {
        let urls = vec!["mirror".to_owned(), "origin".to_owned()];
        let good = "a".repeat(64);
        let used = first_verified(&urls, &good, |url| {
            let good = good.clone();
            async move {
                if url == "mirror" {
                    Ok("b".repeat(64))
                } else {
                    Ok(good)
                }
            }
        })
        .await
        .unwrap();
        assert_eq!(used, "origin");
        let used = first_verified(&urls, &good, |url| {
            let good = good.clone();
            async move {
                anyhow::ensure!(url != "mirror", "stalled");
                Ok(good)
            }
        })
        .await
        .unwrap();
        assert_eq!(used, "origin");
    }

    #[tokio::test]
    async fn no_unverified_archive_is_ever_accepted() {
        let urls = vec!["mirror".to_owned(), "origin".to_owned()];
        let result = first_verified(&urls, &"a".repeat(64), |_| async { Ok("b".repeat(64)) }).await;
        assert!(result.is_err());
    }
}
