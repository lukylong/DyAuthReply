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
    platforms: BTreeMap<String, PinnedArchive>,
}

#[derive(Clone, Deserialize)]
pub struct PinnedArchive {
    pub sha256: String,
    pub size: u64,
}

pub struct PinnedSpec {
    pub version: String,
    pub platform: &'static str,
    pub archive: PinnedArchive,
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
    Ok(PinnedSpec {
        version: pin.version,
        platform,
        archive,
    })
}

/// Mirror first, origin second. Both serve the identical Chrome for Testing archive path.
#[must_use]
pub fn source_urls(version: &str, platform: &str) -> [String; 2] {
    [
        format!("https://cdn.npmmirror.com/binaries/chrome-for-testing/{version}/{platform}/chrome-{platform}.zip"),
        format!("https://storage.googleapis.com/chrome-for-testing-public/{version}/{platform}/chrome-{platform}.zip"),
    ]
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
    let urls = source_urls(&spec.version, spec.platform);
    first_verified(&urls, &spec.archive.sha256, |url| {
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
    fn sources_are_mirror_first_and_scoped_to_the_exact_archive() {
        let [mirror, origin] = source_urls("155.0.1", "win64");
        assert_eq!(
            mirror,
            "https://cdn.npmmirror.com/binaries/chrome-for-testing/155.0.1/win64/chrome-win64.zip"
        );
        assert_eq!(
            origin,
            "https://storage.googleapis.com/chrome-for-testing-public/155.0.1/win64/chrome-win64.zip"
        );
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
