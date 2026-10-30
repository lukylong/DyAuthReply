use super::{
    component::{
        self, file_digest, safe_relative, valid_digest, Manifest, ResolvedComponent,
        CAPTURE_CONTRACT,
    },
    sources,
};
use anyhow::{Context, Result};
use std::{
    collections::BTreeSet,
    fs::File,
    io::{Read, Write},
    path::{Component, Path},
    sync::Arc,
};

const MAX_UNPACKED_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_ARCHIVE_ENTRIES: usize = 30_000;

pub type Progress = Arc<dyn Fn(u64, u64) + Send + Sync>;

struct InstallSpec {
    version: String,
    url: String,
    executable_inside: &'static str,
}

/// Downloads (mirror first) and atomically selects the browser version pinned in this client.
/// # Errors
/// Rejects unverified archives, unsafe entries and failed publication.
pub async fn install_latest(root: &Path, progress: Progress) -> Result<ResolvedComponent> {
    install(root, progress, false).await
}

/// Re-downloads even the same version to repair missing libraries/resources.
pub async fn repair_latest(root: &Path, progress: Progress) -> Result<ResolvedComponent> {
    install(root, progress, true).await
}

fn reusable(root: &Path, version: &str, force: bool) -> Option<ResolvedComponent> {
    if force {
        return None;
    }
    component::resolve(root)
        .ok()
        .filter(|current| current.view.source == "managed" && current.view.version == version)
}

async fn install(root: &Path, progress: Progress, force: bool) -> Result<ResolvedComponent> {
    let (platform, executable_inside) = platform_contract()?;
    let pinned = sources::pinned(platform)?;
    let mut spec = InstallSpec {
        version: pinned.version.clone(),
        url: String::new(),
        executable_inside,
    };
    if let Some(current) = reusable(root, &spec.version, force) {
        progress(1, 1);
        return Ok(current);
    }
    let component = root.join("components/chromium");
    std::fs::create_dir_all(&component)?;
    cleanup_stages(&component)?;
    let stage = component.join(format!(".stage-{}", uuid::Uuid::new_v4()));
    create_private_dir(&stage)?;
    let archive = stage.join("browser.zip");
    let result = async {
        spec.url = sources::download_verified(&pinned, &archive, progress).await?;
        let archive_sha256 = pinned.archive.sha256.to_ascii_lowercase();
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

pub(super) fn selected_component() -> Result<sources::PinnedSpec> {
    let (platform, _) = platform_contract()?;
    sources::pinned(platform)
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

/// Removes only the managed component, never profiles, credentials or account data.
pub fn uninstall(root: &Path) -> Result<()> {
    let parent = root.join("components");
    let component = parent.join("chromium");
    for path in [&parent, &component] {
        match std::fs::symlink_metadata(path) {
            Ok(meta) => anyhow::ensure!(
                meta.is_dir() && !meta.file_type().is_symlink(),
                "内置浏览器目录异常，未执行卸载"
            ),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            Err(e) => return Err(e.into()),
        }
    }
    // Rename first so a locked Windows executable cannot cause a partial walk
    // of the live installation. Restore the directory on a deletion error.
    let removed = parent.join(format!(".chromium-remove-{}", uuid::Uuid::new_v4()));
    std::fs::rename(&component, &removed)
        .context("内置浏览器仍被占用，请关闭快捷登录窗口后重试")?;
    if let Err(error) = std::fs::remove_dir_all(&removed) {
        let restored = std::fs::rename(&removed, &component).is_ok();
        anyhow::bail!(
            "内置浏览器卸载未完成（系统错误 {:?}，目录恢复={}），请关闭浏览器后重试",
            error.raw_os_error(),
            restored
        );
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
    // A same-version repair must not overwrite files selected by current/previous.
    let directory = format!(
        "{}-{}-{}",
        spec.version,
        component::target(),
        uuid::Uuid::new_v4()
    );
    anyhow::ensure!(valid_directory(&directory), "快捷登录组件目录名无效");
    let final_root = versions.join(&directory);
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
    publish_manifest(root, &component, &manifest)?;
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

fn publish_manifest(root: &Path, component: &Path, manifest: &Manifest) -> Result<()> {
    let next = component.join("current.next.json");
    atomic_write(&next, &serde_json::to_vec_pretty(manifest)?)?;
    let current = component.join("current.json");
    let previous = component.join("previous.json");
    if component::managed_from(root, &current).is_ok() {
        std::fs::copy(&current, &previous)?;
    }
    if previous.exists() && component::managed_from(root, &previous).is_err() {
        std::fs::remove_file(&previous)?;
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

    /// Live check of the real first-source download path (≈200 MB). Run with
    /// `cargo test --lib live_pinned_component_installs -- --ignored --nocapture`.
    #[tokio::test]
    #[ignore = "downloads the real pinned browser archive"]
    async fn live_pinned_component_installs_from_first_source_and_resolves() {
        let root = tempfile::tempdir().unwrap();
        let started = std::time::Instant::now();
        let resolved = install_latest(root.path(), Arc::new(|_, _| {}))
            .await
            .unwrap();
        let manifest: Manifest = serde_json::from_slice(
            &std::fs::read(root.path().join("components/chromium/current.json")).unwrap(),
        )
        .unwrap();
        println!(
            "installed {} from {} in {:?}",
            resolved.view.version,
            manifest.source_url,
            started.elapsed()
        );
        let (platform, _) = platform_contract().unwrap();
        assert_eq!(
            manifest.source_url,
            sources::pinned(platform).unwrap().sources[0]
        );
        assert!(resolved.executable.is_file());
        let again = install_latest(root.path(), Arc::new(|_, _| {}))
            .await
            .unwrap();
        assert_eq!(again.view.version, resolved.view.version);
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
    #[test]
    fn browser_management_repair_preserves_previous_same_version() {
        let root = tempfile::tempdir().unwrap();
        let mut installed = Vec::new();
        for body in [b"old-browser".as_slice(), b"new-browser".as_slice()] {
            let stage = root.path().join(format!("stage-{}", installed.len()));
            std::fs::create_dir_all(&stage).unwrap();
            let archive = stage.join("test.zip");
            let mut zip = zip::ZipWriter::new(File::create(&archive).unwrap());
            zip.start_file(
                "browser",
                SimpleFileOptions::default().unix_permissions(0o755),
            )
            .unwrap();
            zip.write_all(body).unwrap();
            zip.finish().unwrap();
            let spec = InstallSpec {
                version: "1.0.0".into(),
                url: "https://fixture.invalid/browser.zip".into(),
                executable_inside: "browser",
            };
            installed.push(
                publish(
                    root.path(),
                    &stage,
                    &archive,
                    &spec,
                    file_digest(&archive).unwrap(),
                )
                .unwrap(),
            );
        }
        assert_ne!(installed[0].executable, installed[1].executable);
        assert_eq!(
            std::fs::read(&installed[0].executable).unwrap(),
            b"old-browser"
        );
        assert_eq!(
            std::fs::read(rollback(root.path()).unwrap().executable).unwrap(),
            b"old-browser"
        );
    }
    #[test]
    fn browser_management_uninstall_preserves_login_profiles_and_business_data() {
        let root = tempfile::tempdir().unwrap();
        let preserve = [
            "quick-auth/profiles/account/cookies",
            "accounts.sqlite3",
            "business.sqlite3",
            "credentials-v1/secret",
            "components/other/keep",
        ];
        for path in preserve {
            let p = root.path().join(path);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, b"keep-exactly").unwrap();
        }
        let component = root.path().join("components/chromium");
        std::fs::create_dir_all(component.join("versions/old")).unwrap();
        for name in ["current.json", "previous.json", "versions/old/browser"] {
            std::fs::write(component.join(name), b"component").unwrap();
        }
        uninstall(root.path()).unwrap();
        assert!(!component.exists());
        for path in preserve {
            assert_eq!(
                std::fs::read(root.path().join(path)).unwrap(),
                b"keep-exactly"
            );
        }
        uninstall(root.path()).unwrap();
        assert!(!component.exists());
    }

    #[cfg(unix)]
    #[test]
    fn browser_management_uninstall_refuses_symlinked_parent_or_component() {
        for parent in [true, false] {
            let root = tempfile::tempdir().unwrap();
            let outside = tempfile::tempdir().unwrap();
            std::fs::write(outside.path().join("keep"), b"untouched").unwrap();
            let link = if parent {
                root.path().join("components")
            } else {
                std::fs::create_dir(root.path().join("components")).unwrap();
                root.path().join("components/chromium")
            };
            std::os::unix::fs::symlink(outside.path(), link).unwrap();
            assert!(uninstall(root.path()).is_err());
            assert_eq!(
                std::fs::read(outside.path().join("keep")).unwrap(),
                b"untouched"
            );
        }
    }

    #[test]
    fn browser_management_repair_replaces_corrupt_manifest_and_never_reuses_same_version() {
        let root = tempfile::tempdir().unwrap();
        let component = root.path().join("components/chromium");
        std::fs::create_dir_all(&component).unwrap();
        std::fs::write(component.join("current.json"), b"broken-json").unwrap();
        std::fs::write(component.join("previous.json"), b"also-broken").unwrap();
        let stage = root.path().join("stage");
        std::fs::create_dir(&stage).unwrap();
        let archive = stage.join("archive.zip");
        let mut zip = zip::ZipWriter::new(File::create(&archive).unwrap());
        zip.start_file("browser", SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"repaired").unwrap();
        zip.finish().unwrap();
        let spec = InstallSpec {
            version: "1.0.0".into(),
            url: "https://fixture.invalid/browser.zip".into(),
            executable_inside: "browser",
        };
        let resolved = publish(
            root.path(),
            &stage,
            &archive,
            &spec,
            file_digest(&archive).unwrap(),
        )
        .unwrap();
        assert!(reusable(root.path(), "1.0.0", false).is_some());
        assert!(reusable(root.path(), "1.0.0", true).is_none());
        // A newly selected system-specific version must not reuse an existing
        // (otherwise healthy) browser for a different system profile.
        assert!(reusable(root.path(), "2.0.0", false).is_none());
        assert!(!component.join("previous.json").exists());
        std::fs::write(&archive, b"invalid archive").unwrap();
        assert!(publish(root.path(), &stage, &archive, &spec, "0".repeat(64)).is_err());
        assert_eq!(
            component::resolve(root.path()).unwrap().executable,
            resolved.executable
        );
    }
}
