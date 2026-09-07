use super::component::{
    self, file_digest, safe_relative, valid_digest, valid_version, Manifest, ResolvedComponent,
    CAPTURE_CONTRACT,
};
use anyhow::{Context, Result};
use futures_util::StreamExt;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::File,
    io::{Read, Write},
    path::{Component, Path},
    sync::Arc,
};
use tokio::io::AsyncWriteExt;

const CATALOG_URL: &str =
    "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json";
const MAX_CATALOG_BYTES: usize = 512 * 1024;
const MAX_ARCHIVE_BYTES: u64 = 512 * 1024 * 1024;
const MAX_UNPACKED_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_ARCHIVE_ENTRIES: usize = 30_000;

pub type Progress = Arc<dyn Fn(u64, u64) + Send + Sync>;

#[derive(Deserialize)]
struct Catalog {
    channels: Channels,
}

#[derive(Deserialize)]
#[serde(rename_all = "PascalCase")]
struct Channels {
    stable: Channel,
}

#[derive(Deserialize)]
struct Channel {
    version: String,
    downloads: Downloads,
}

#[derive(Deserialize)]
struct Downloads {
    chrome: Vec<Download>,
}

#[derive(Deserialize)]
struct Download {
    platform: String,
    url: String,
}

struct InstallSpec {
    version: String,
    url: String,
    executable_inside: &'static str,
}

/// Downloads and atomically selects the current stable managed authorization browser.
/// # Errors
/// Rejects untrusted catalogs/URLs, oversized archives, unsafe entries and failed publication.
pub async fn install_latest(root: &Path, progress: Progress) -> Result<ResolvedComponent> {
    let spec = catalog_spec().await?;
    if let Ok(current) = component::resolve(root) {
        if current.view.source == "managed" && current.view.version == spec.version {
            progress(1, 1);
            return Ok(current);
        }
    }
    let component = root.join("components/chromium");
    std::fs::create_dir_all(&component)?;
    cleanup_stages(&component)?;
    let stage = component.join(format!(".stage-{}", uuid::Uuid::new_v4()));
    create_private_dir(&stage)?;
    let archive = stage.join("browser.zip");
    let result = async {
        let archive_sha256 = download(&spec.url, &archive, progress).await?;
        let root = root.to_owned();
        let publish_stage = stage.clone();
        let publish_archive = archive.clone();
        tokio::task::spawn_blocking(move || {
            publish(
                &root,
                &publish_stage,
                &publish_archive,
                &spec,
                archive_sha256,
            )
        })
        .await?
    }
    .await;
    if stage.exists() {
        let _ = std::fs::remove_dir_all(&stage);
    }
    result
}

fn cleanup_stages(component: &Path) -> Result<()> {
    for entry in std::fs::read_dir(component)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() && entry.file_name().to_string_lossy().starts_with(".stage-")
        {
            std::fs::remove_dir_all(entry.path())?;
        }
    }
    Ok(())
}

/// Swaps the current and previous verified component manifests.
/// # Errors
/// Requires a valid previous component and rejects an unavailable/corrupt rollback.
pub fn rollback(root: &Path) -> Result<ResolvedComponent> {
    let component = root.join("components/chromium");
    let current = component.join("current.json");
    let previous = component.join("previous.json");
    anyhow::ensure!(
        current.is_file() && previous.is_file(),
        "没有可回退的快捷登录组件"
    );
    component::managed_from(root, &previous)?;
    let current_bytes = bounded_read(&current, component::MAX_MANIFEST_BYTES)?;
    let previous_bytes = bounded_read(&previous, component::MAX_MANIFEST_BYTES)?;
    atomic_write(&component.join("rollback.next.json"), &previous_bytes)?;
    atomic_write(&component.join("previous.next.json"), &current_bytes)?;
    replace(&component.join("rollback.next.json"), &current)?;
    replace(&component.join("previous.next.json"), &previous)?;
    let resolved = component::resolve(root)?;
    prune_versions(root)?;
    Ok(resolved)
}

async fn catalog_spec() -> Result<InstallSpec> {
    let response = wreq::Client::builder()
        .redirect(wreq::redirect::Policy::none())
        .retry(wreq::retry::Policy::never())
        .timeout(std::time::Duration::from_secs(20))
        .build()?
        .get(CATALOG_URL)
        .send()
        .await?;
    anyhow::ensure!(response.status().is_success(), "快捷登录组件目录获取失败");
    let bytes = response.bytes().await?;
    anyhow::ensure!(
        !bytes.is_empty() && bytes.len() <= MAX_CATALOG_BYTES,
        "快捷登录组件目录无效"
    );
    let catalog: Catalog = serde_json::from_slice(&bytes)?;
    anyhow::ensure!(
        valid_version(&catalog.channels.stable.version),
        "快捷登录组件版本无效"
    );
    let (platform, executable_inside) = platform_contract()?;
    let item = catalog
        .channels
        .stable
        .downloads
        .chrome
        .into_iter()
        .find(|item| item.platform == platform)
        .context("快捷登录组件不支持当前系统")?;
    validate_download_url(&item.url, &catalog.channels.stable.version, platform)?;
    Ok(InstallSpec {
        version: catalog.channels.stable.version,
        url: item.url,
        executable_inside,
    })
}

fn platform_contract() -> Result<(&'static str, &'static str)> {
    match component::target() {
        "aarch64-apple-darwin" => Ok((
            "mac-arm64",
            "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        )),
        "x86_64-apple-darwin" => Ok((
            "mac-x64",
            "chrome-mac-x64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        )),
        "x86_64-pc-windows-msvc" => Ok(("win64", "chrome-win64/chrome.exe")),
        _ => anyhow::bail!("快捷登录组件不支持当前系统"),
    }
}

fn validate_download_url(raw: &str, version: &str, platform: &str) -> Result<()> {
    let uri: wreq::Uri = raw.parse()?;
    let expected = format!("/chrome-for-testing-public/{version}/{platform}/chrome-{platform}.zip");
    anyhow::ensure!(
        uri.scheme_str() == Some("https")
            && uri.host() == Some("storage.googleapis.com")
            && uri.path() == expected
            && uri.query().is_none(),
        "快捷登录组件下载地址无效"
    );
    Ok(())
}

async fn download(raw: &str, output: &Path, progress: Progress) -> Result<String> {
    let response = wreq::Client::builder()
        .redirect(wreq::redirect::Policy::none())
        .retry(wreq::retry::Policy::never())
        .timeout(std::time::Duration::from_secs(600))
        .build()?
        .get(raw)
        .send()
        .await?;
    anyhow::ensure!(response.status().is_success(), "快捷登录组件下载失败");
    let total = response.content_length().unwrap_or(0);
    anyhow::ensure!(
        total == 0 || total <= MAX_ARCHIVE_BYTES,
        "快捷登录组件安装包过大"
    );
    let mut file = tokio::fs::File::create(output).await?;
    let mut stream = response.bytes_stream();
    let mut digest = Sha256::new();
    let mut downloaded = 0_u64;
    while let Some(chunk) = stream.next().await {
        let chunk = chunk?;
        downloaded = downloaded
            .checked_add(u64::try_from(chunk.len())?)
            .context("快捷登录组件大小溢出")?;
        anyhow::ensure!(downloaded <= MAX_ARCHIVE_BYTES, "快捷登录组件安装包过大");
        digest.update(&chunk);
        file.write_all(&chunk).await?;
        progress(downloaded, total);
    }
    anyhow::ensure!(downloaded > 1024 * 1024, "快捷登录组件安装包不完整");
    if total > 0 {
        anyhow::ensure!(downloaded == total, "快捷登录组件下载不完整");
    }
    file.sync_all().await?;
    Ok(format!("{:x}", digest.finalize()))
}

fn publish(
    root: &Path,
    stage: &Path,
    archive: &Path,
    spec: &InstallSpec,
    archive_sha256: String,
) -> Result<ResolvedComponent> {
    anyhow::ensure!(valid_digest(&archive_sha256), "快捷登录组件安装包摘要无效");
    let extracted = stage.join("extracted");
    extract(archive, &extracted)?;
    let inside = Path::new(spec.executable_inside);
    anyhow::ensure!(safe_relative(inside), "快捷登录组件程序路径无效");
    let executable = extracted.join(inside);
    let metadata = std::fs::symlink_metadata(&executable)?;
    anyhow::ensure!(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        "快捷登录组件程序无效"
    );
    let component = root.join("components/chromium");
    let versions = component.join("versions");
    std::fs::create_dir_all(&versions)?;
    let directory = format!("{}-{}", spec.version, component::target());
    anyhow::ensure!(valid_directory(&directory), "快捷登录组件目录名无效");
    let final_root = versions.join(&directory);
    if final_root.exists() {
        std::fs::remove_dir_all(&final_root)?;
    }
    std::fs::rename(&extracted, &final_root)?;
    let executable_relative = Path::new("versions").join(&directory).join(inside);
    let executable_sha256 = file_digest(&component.join(&executable_relative))?;
    let manifest = Manifest {
        version: spec.version.clone(),
        target: component::target().into(),
        executable: path_text(&executable_relative)?,
        sha256: executable_sha256,
        capture_contract: CAPTURE_CONTRACT,
        archive_sha256,
        source_url: spec.url.clone(),
    };
    publish_manifest(&component, &manifest)?;
    let resolved = component::resolve(root)?;
    prune_versions(root)?;
    Ok(resolved)
}

fn extract(archive_path: &Path, output: &Path) -> Result<()> {
    std::fs::create_dir_all(output)?;
    let mut archive = zip::ZipArchive::new(File::open(archive_path)?)?;
    anyhow::ensure!(
        archive.len() <= MAX_ARCHIVE_ENTRIES,
        "快捷登录组件文件数量超限"
    );
    let mut unpacked = 0_u64;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index)?;
        let enclosed = entry.enclosed_name().context("快捷登录组件包含越界路径")?;
        anyhow::ensure!(
            enclosed
                .components()
                .all(|part| matches!(part, Component::Normal(_))),
            "快捷登录组件包含无效路径"
        );
        let mode = entry.unix_mode().unwrap_or(0o644);
        unpacked = unpacked
            .checked_add(entry.size())
            .context("快捷登录组件展开大小溢出")?;
        anyhow::ensure!(unpacked <= MAX_UNPACKED_BYTES, "快捷登录组件展开大小超限");
        let destination = output.join(&enclosed);
        if mode & 0o170_000 == 0o120_000 {
            install_symlink(&mut entry, &enclosed, &destination)?;
            continue;
        }
        if entry.is_dir() {
            std::fs::create_dir_all(&destination)?;
            continue;
        }
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let mut file = File::create(&destination)?;
        let written = std::io::copy(&mut entry, &mut file)?;
        anyhow::ensure!(written == entry.size(), "快捷登录组件文件不完整");
        file.sync_all()?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&destination, std::fs::Permissions::from_mode(mode & 0o777))?;
        }
    }
    Ok(())
}

#[cfg(unix)]
fn install_symlink<R: Read>(entry: &mut R, relative: &Path, destination: &Path) -> Result<()> {
    use std::os::unix::fs::symlink;
    let mut raw = String::new();
    entry.take(4097).read_to_string(&mut raw)?;
    anyhow::ensure!(
        !raw.is_empty() && raw.len() <= 4096 && !raw.chars().any(char::is_control),
        "快捷登录组件符号链接无效"
    );
    let target = Path::new(&raw);
    anyhow::ensure!(safe_link(relative, target), "快捷登录组件符号链接越界");
    if let Some(parent) = destination.parent() {
        std::fs::create_dir_all(parent)?;
    }
    symlink(target, destination)?;
    Ok(())
}

#[cfg(not(unix))]
fn install_symlink<R: Read>(_entry: &mut R, _relative: &Path, _destination: &Path) -> Result<()> {
    anyhow::bail!("快捷登录组件包含不支持的符号链接")
}

fn safe_link(relative: &Path, target: &Path) -> bool {
    if target.is_absolute() || target.as_os_str().is_empty() {
        return false;
    }
    let mut depth = relative
        .parent()
        .map_or(0, |parent| parent.components().count());
    for part in target.components() {
        match part {
            Component::Normal(_) => depth = depth.saturating_add(1),
            Component::CurDir => {}
            Component::ParentDir if depth > 0 => depth -= 1,
            Component::ParentDir | Component::RootDir | Component::Prefix(_) => return false,
        }
    }
    true
}

fn publish_manifest(component: &Path, manifest: &Manifest) -> Result<()> {
    let next = component.join("current.next.json");
    atomic_write(&next, &serde_json::to_vec_pretty(manifest)?)?;
    let current = component.join("current.json");
    let previous = component.join("previous.json");
    if current.is_file() {
        std::fs::copy(&current, &previous)?;
    }
    replace(&next, &current)
}

fn replace(source: &Path, destination: &Path) -> Result<()> {
    if destination.exists() {
        std::fs::remove_file(destination)?;
    }
    std::fs::rename(source, destination)?;
    Ok(())
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let mut file = File::create(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn bounded_read(path: &Path, limit: u64) -> Result<Vec<u8>> {
    let metadata = std::fs::symlink_metadata(path)?;
    anyhow::ensure!(
        metadata.is_file() && metadata.len() <= limit,
        "快捷登录组件清单无效"
    );
    let mut result = Vec::with_capacity(usize::try_from(metadata.len())?);
    File::open(path)?.take(limit + 1).read_to_end(&mut result)?;
    anyhow::ensure!(
        u64::try_from(result.len())? <= limit,
        "快捷登录组件清单过大"
    );
    Ok(result)
}

fn prune_versions(root: &Path) -> Result<()> {
    let component = root.join("components/chromium");
    let mut keep = BTreeSet::new();
    for name in ["current.json", "previous.json"] {
        let path = component.join(name);
        if !path.is_file() {
            continue;
        }
        let manifest: Manifest =
            serde_json::from_slice(&bounded_read(&path, component::MAX_MANIFEST_BYTES)?)?;
        let relative = Path::new(&manifest.executable);
        let mut parts = relative.components();
        if parts.next() == Some(Component::Normal("versions".as_ref())) {
            if let Some(Component::Normal(version)) = parts.next() {
                keep.insert(version.to_owned());
            }
        }
    }
    let versions = component.join("versions");
    if !versions.is_dir() {
        return Ok(());
    }
    for entry in std::fs::read_dir(versions)? {
        let entry = entry?;
        if entry.file_type()?.is_dir() && !keep.contains(&entry.file_name()) {
            std::fs::remove_dir_all(entry.path())?;
        }
    }
    Ok(())
}

fn path_text(path: &Path) -> Result<String> {
    path.to_str()
        .map(str::to_owned)
        .context("快捷登录组件路径编码无效")
}

fn valid_directory(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 160
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
}

fn create_private_dir(path: &Path) -> Result<()> {
    std::fs::create_dir_all(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use zip::write::SimpleFileOptions;

    #[test]
    fn catalog_urls_are_exactly_scoped_to_version_and_platform() {
        assert!(validate_download_url(
            "https://storage.googleapis.com/chrome-for-testing-public/152.0.1/mac-arm64/chrome-mac-arm64.zip",
            "152.0.1",
            "mac-arm64"
        )
        .is_ok());
        assert!(
            validate_download_url("https://example.com/chrome.zip", "152.0.1", "mac-arm64")
                .is_err()
        );
        assert!(validate_download_url(
            "https://storage.googleapis.com/chrome-for-testing-public/other/mac-arm64/chrome-mac-arm64.zip",
            "152.0.1",
            "mac-arm64"
        )
        .is_err());
    }

    #[test]
    fn extraction_rejects_parent_paths_and_preserves_executable_mode() {
        let root = tempfile::tempdir().unwrap();
        let archive_path = root.path().join("component.zip");
        let file = File::create(&archive_path).unwrap();
        let mut writer = zip::ZipWriter::new(file);
        writer
            .start_file(
                "browser/chrome",
                SimpleFileOptions::default().unix_permissions(0o755),
            )
            .unwrap();
        writer.write_all(b"browser").unwrap();
        writer.finish().unwrap();
        let output = root.path().join("output");
        extract(&archive_path, &output).unwrap();
        assert_eq!(
            std::fs::read(output.join("browser/chrome")).unwrap(),
            b"browser"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                std::fs::metadata(output.join("browser/chrome"))
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o111,
                0o111
            );
        }
    }

    #[test]
    fn symlink_targets_must_remain_inside_the_extracted_root() {
        assert!(safe_link(
            Path::new("Browser.app/Versions/Current"),
            Path::new("152.0.1")
        ));
        assert!(safe_link(
            Path::new("Browser.app/Helpers"),
            Path::new("Versions/Current/Helpers")
        ));
        assert!(!safe_link(
            Path::new("Browser.app/link"),
            Path::new("../../outside")
        ));
        assert!(!safe_link(
            Path::new("Browser.app/link"),
            Path::new("/tmp/outside")
        ));
    }

    #[test]
    fn rollback_swaps_two_verified_manifests_and_keeps_both_versions() {
        let root = tempfile::tempdir().unwrap();
        let component = root.path().join("components/chromium");
        for version in ["1.0.0", "2.0.0"] {
            let executable = component
                .join("versions")
                .join(format!("{version}-{}", component::target()))
                .join("browser");
            std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
            std::fs::write(&executable, version).unwrap();
            let relative = executable.strip_prefix(&component).unwrap();
            let manifest = Manifest {
                version: version.into(),
                target: component::target().into(),
                executable: path_text(relative).unwrap(),
                sha256: file_digest(&executable).unwrap(),
                capture_contract: CAPTURE_CONTRACT,
                archive_sha256: "0".repeat(64),
                source_url: "https://storage.googleapis.com/chrome-for-testing-public/test".into(),
            };
            std::fs::write(
                component.join(if version == "2.0.0" {
                    "current.json"
                } else {
                    "previous.json"
                }),
                serde_json::to_vec(&manifest).unwrap(),
            )
            .unwrap();
        }

        let restored = rollback(root.path()).unwrap();

        assert_eq!(restored.view.version, "1.0.0");
        let previous: Manifest =
            serde_json::from_slice(&std::fs::read(component.join("previous.json")).unwrap())
                .unwrap();
        assert_eq!(previous.version, "2.0.0");
        assert_eq!(
            std::fs::read_dir(component.join("versions"))
                .unwrap()
                .count(),
            2
        );
    }
}
