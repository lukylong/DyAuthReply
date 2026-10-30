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
const MIN_WINDOWS_BUILD: u32 = 10_240;
const WINDOWS_11_BUILD: u32 = 22_000;

#[derive(Deserialize)]
struct Pin {
    #[serde(flatten)]
    standard: VersionPin,
    windows_compat: VersionPin,
}

#[derive(Deserialize)]
struct VersionPin {
    version: String,
    /// Ordered URL templates with `{version}` and `{platform}`; first reachable wins.
    sources: Vec<String>,
    platforms: BTreeMap<String, PinnedArchive>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct PinnedArchive {
    pub sha256: String,
    pub size: u64,
}

#[derive(Debug)]
pub struct PinnedSpec {
    pub version: String,
    pub archive: PinnedArchive,
    pub sources: Vec<String>,
    pub variant: &'static str,
    pub windows_build: Option<u32>,
}

impl PinnedSpec {
    #[must_use]
    pub fn selection_reason(&self) -> String {
        match (self.variant, self.windows_build) {
            ("windows_compat", Some(build)) => {
                format!("检测到 Windows build {build}，自动选择兼容版浏览器")
            }
            (_, Some(build)) => format!("检测到 Windows build {build}，自动选择标准版浏览器"),
            _ => "当前系统使用标准版浏览器".into(),
        }
    }
}

/// # Errors
/// Rejects a malformed shipped pin or a platform it does not cover.
pub fn pinned(platform: &'static str) -> Result<PinnedSpec> {
    let windows_build = if platform == "win64" {
        // sysinfo reads CurrentBuildNumber from HKLM, not a compatibility-mode
        // spoofable version string or an arbitrary environment variable.
        anyhow::ensure!(
            cfg!(target_os = "windows"),
            "Windows 组件需要读取本机 Windows 版本"
        );
        Some(parse_windows_build(
            sysinfo::System::kernel_version().as_deref(),
        )?)
    } else {
        None
    };
    pinned_for_build(platform, windows_build)
}

fn parse_windows_build(raw: Option<&str>) -> Result<u32> {
    let value = raw
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .context("未读取到 Windows 内部版本号，请刷新状态后重试")?;
    anyhow::ensure!(
        value.bytes().all(|value| value.is_ascii_digit()),
        "Windows 内部版本号格式异常"
    );
    value.parse().context("Windows 内部版本号超出范围")
}

fn pinned_for_build(platform: &str, windows_build: Option<u32>) -> Result<PinnedSpec> {
    let pin: Pin = serde_json::from_str(PIN).context("快捷登录组件版本清单无效")?;
    select_pin(&pin, platform, windows_build)
}

fn select_pin(pin: &Pin, platform: &str, windows_build: Option<u32>) -> Result<PinnedSpec> {
    let use_compat = if platform == "win64" {
        let build = windows_build.context("Windows 系统版本缺失，尚未选择浏览器组件")?;
        anyhow::ensure!(
            build >= MIN_WINDOWS_BUILD,
            "当前 Windows build {build} 低于本客户端组件要求，请使用 Windows 10 或更新系统"
        );
        build < WINDOWS_11_BUILD
    } else {
        false
    };
    let selected = if use_compat {
        &pin.windows_compat
    } else {
        &pin.standard
    };
    anyhow::ensure!(valid_version(&selected.version), "快捷登录组件版本无效");
    let archive = selected
        .platforms
        .get(platform)
        .cloned()
        .context("快捷登录组件不支持当前系统")?;
    anyhow::ensure!(
        valid_digest(&archive.sha256) && (1024 * 1024..=MAX_ARCHIVE_BYTES).contains(&archive.size),
        "快捷登录组件版本清单无效"
    );
    let sources = selected
        .sources
        .iter()
        .map(|template| source_url(template, &selected.version, platform))
        .collect::<Result<Vec<_>>>()?;
    anyhow::ensure!((1..=8).contains(&sources.len()), "快捷登录组件下载源无效");
    Ok(PinnedSpec {
        version: selected.version.clone(),
        archive,
        sources,
        variant: if use_compat {
            "windows_compat"
        } else {
            "standard"
        },
        windows_build: if platform == "win64" {
            windows_build
        } else {
            None
        },
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
            let spec = pinned_for_build(platform, Some(WINDOWS_11_BUILD)).unwrap();
            assert!(valid_version(&spec.version));
            assert!(valid_digest(&spec.archive.sha256));
        }
    }

    #[test]
    fn shipped_sources_keep_the_official_origin_as_last_resort() {
        let spec = pinned_for_build("win64", Some(WINDOWS_11_BUILD)).unwrap();
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
    fn windows_build_selection_is_decided_before_download() {
        let pin: Pin = serde_json::from_str(PIN).unwrap();
        assert_ne!(pin.windows_compat.version, pin.standard.version);
        for build in [
            10_240, 14_393, 17_763, 18_362, 19_041, 19_044, 19_045, 20_348, 21_999,
        ] {
            let spec = pinned_for_build("win64", Some(build)).unwrap();
            assert_eq!(spec.version, pin.windows_compat.version, "build={build}");
            assert_eq!(spec.variant, "windows_compat");
            assert_eq!(
                spec.archive.sha256,
                pin.windows_compat.platforms["win64"].sha256
            );
            assert!(spec.sources.iter().all(|url| url.contains(&spec.version)));
            assert!(spec.selection_reason().contains(&build.to_string()));
        }
        for build in [22_000, 22_621, 22_631, 26_100, 99_999] {
            let spec = pinned_for_build("win64", Some(build)).unwrap();
            assert_eq!(spec.version, pin.standard.version);
            assert_eq!(spec.variant, "standard");
        }
    }

    #[test]
    fn unknown_and_unsupported_windows_do_not_silently_receive_a_new_browser() {
        for build in [
            None,
            Some(0),
            Some(7601),
            Some(9200),
            Some(9600),
            Some(10_239),
        ] {
            assert!(pinned_for_build("win64", build).is_err(), "{build:?}");
        }
        for raw in [
            None,
            Some(""),
            Some("unknown"),
            Some("10.0.19045"),
            Some("-19045"),
            Some("4294967296"),
        ] {
            assert!(parse_windows_build(raw).is_err(), "{raw:?}");
        }
        assert_eq!(parse_windows_build(Some(" 19045 ")).unwrap(), 19_045);
    }

    #[test]
    fn macos_keeps_standard_pin_and_does_not_inherit_windows_policy() {
        let pin: Pin = serde_json::from_str(PIN).unwrap();
        for platform in ["mac-arm64", "mac-x64"] {
            let spec = pinned_for_build(platform, Some(19_045)).unwrap();
            assert_eq!(spec.version, pin.standard.version);
            assert_eq!(spec.variant, "standard");
            assert_eq!(spec.windows_build, None);
            assert_eq!(spec.archive.sha256, pin.standard.platforms[platform].sha256);
        }
        assert!(pinned_for_build("win32", Some(19_045)).is_err());
    }

    #[test]
    fn compatibility_pin_keeps_the_same_archive_trust_contract() {
        let mut pin: Pin = serde_json::from_str(PIN).unwrap();
        let spec = select_pin(&pin, "win64", Some(19_045)).unwrap();
        assert_eq!(spec.sources.last().unwrap(), &format!(
            "https://storage.googleapis.com/chrome-for-testing-public/{}/win64/chrome-win64.zip", spec.version));
        pin.windows_compat
            .platforms
            .get_mut("win64")
            .unwrap()
            .sha256 = "invalid".into();
        assert!(select_pin(&pin, "win64", Some(19_045)).is_err());
        // A bad compatibility artifact does not rewrite the standard channel.
        assert!(select_pin(&pin, "win64", Some(26_100)).is_ok());
    }

    #[test]
    #[cfg(target_os = "windows")]
    fn real_windows_host_uses_its_registry_build_for_selection() {
        let build = parse_windows_build(sysinfo::System::kernel_version().as_deref()).unwrap();
        let selected = pinned("win64").unwrap();
        let expected = pinned_for_build("win64", Some(build)).unwrap();
        assert_eq!(selected.version, expected.version);
        assert_eq!(selected.windows_build, Some(build));
        println!(
            "WINDOWS_COMPONENT_SELECTION_PASS build={build} version={} variant={}",
            selected.version, selected.variant
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
