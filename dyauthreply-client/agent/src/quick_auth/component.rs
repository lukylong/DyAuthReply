use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::File,
    io::Read,
    path::{Component, Path, PathBuf},
};

pub(crate) const MAX_MANIFEST_BYTES: u64 = 64 * 1024;
const MAX_BROWSER_BYTES: u64 = 1024 * 1024 * 1024;
pub const CAPTURE_CONTRACT: u16 = 1;

#[derive(Clone, Serialize)]
pub struct ComponentView {
    pub state: &'static str,
    pub version: String,
    pub source: &'static str,
    pub install_required: bool,
    pub message: String,
    pub downloaded_bytes: u64,
    pub total_bytes: u64,
    pub progress_percent: Option<u8>,
    pub can_rollback: bool,
}

#[derive(Clone)]
pub struct ResolvedComponent {
    pub executable: PathBuf,
    pub view: ComponentView,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Manifest {
    pub version: String,
    pub target: String,
    pub executable: String,
    pub sha256: String,
    pub capture_contract: u16,
    #[serde(default)]
    pub archive_sha256: String,
    #[serde(default)]
    pub source_url: String,
}

pub fn resolve(root: &Path) -> Result<ResolvedComponent> {
    recover_manifest(root)?;
    let manifest_path = root.join("components/chromium/current.json");
    if manifest_path.is_file() {
        return managed_from(root, &manifest_path);
    }
    #[cfg(debug_assertions)]
    if let Some(path) = development_browser() {
        return Ok(ResolvedComponent {
            executable: path,
            view: ComponentView {
                state: "ready",
                version: "development".into(),
                source: "development_browser",
                install_required: false,
                message: "本地调试浏览器已就绪".into(),
                downloaded_bytes: 0,
                total_bytes: 0,
                progress_percent: None,
                can_rollback: root.join("components/chromium/previous.json").is_file(),
            },
        });
    }
    anyhow::bail!("快捷登录组件尚未安装")
}

pub fn status(root: &Path) -> ComponentView {
    match resolve(root) {
        Ok(component) => component.view,
        Err(error) => ComponentView {
            state: if !root.join("components/chromium/current.json").exists() {
                "missing"
            } else if error.to_string().contains("不匹配") || error.to_string().contains("不兼容")
            {
                "incompatible"
            } else {
                "corrupt"
            },
            version: String::new(),
            source: "managed",
            install_required: true,
            message: error.to_string(),
            downloaded_bytes: 0,
            total_bytes: 0,
            progress_percent: None,
            can_rollback: root.join("components/chromium/previous.json").is_file(),
        },
    }
}

pub(crate) fn managed_from(root: &Path, manifest_path: &Path) -> Result<ResolvedComponent> {
    let metadata = std::fs::symlink_metadata(manifest_path)?;
    anyhow::ensure!(
        metadata.is_file() && !metadata.file_type().is_symlink(),
        "快捷登录组件清单无效"
    );
    anyhow::ensure!(metadata.len() <= MAX_MANIFEST_BYTES, "快捷登录组件清单过大");
    let manifest: Manifest = serde_json::from_slice(&std::fs::read(manifest_path)?)?;
    anyhow::ensure!(valid_version(&manifest.version), "快捷登录组件版本无效");
    anyhow::ensure!(manifest.target == target(), "快捷登录组件与当前系统不匹配");
    anyhow::ensure!(
        manifest.capture_contract == CAPTURE_CONTRACT,
        "快捷登录组件采集协议不兼容"
    );
    anyhow::ensure!(valid_digest(&manifest.sha256), "快捷登录组件摘要无效");
    let relative = Path::new(&manifest.executable);
    anyhow::ensure!(safe_relative(relative), "快捷登录组件路径无效");
    let executable = manifest_path
        .parent()
        .context("快捷登录组件目录缺失")?
        .join(relative);
    let executable_metadata =
        std::fs::symlink_metadata(&executable).context("快捷登录组件程序不存在")?;
    anyhow::ensure!(
        executable_metadata.is_file()
            && !executable_metadata.file_type().is_symlink()
            && (1..=MAX_BROWSER_BYTES).contains(&executable_metadata.len()),
        "快捷登录组件程序无效"
    );
    let digest = file_digest(&executable)?;
    anyhow::ensure!(
        digest == manifest.sha256.to_ascii_lowercase(),
        "快捷登录组件校验失败"
    );
    let component_root = root.join("components/chromium").canonicalize()?;
    anyhow::ensure!(
        executable.canonicalize()?.starts_with(component_root),
        "快捷登录组件路径越界"
    );
    Ok(ResolvedComponent {
        executable,
        view: ComponentView {
            state: "ready",
            version: manifest.version,
            source: "managed",
            install_required: false,
            message: "快捷登录组件已就绪".into(),
            downloaded_bytes: 0,
            total_bytes: 0,
            progress_percent: None,
            can_rollback: root.join("components/chromium/previous.json").is_file(),
        },
    })
}

pub(crate) fn file_digest(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; 64 * 1024].into_boxed_slice();
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Ok(format!("{:x}", digest.finalize()))
}

pub(crate) fn safe_relative(path: &Path) -> bool {
    !path.as_os_str().is_empty()
        && !path.is_absolute()
        && path
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
}

pub(crate) fn valid_digest(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

pub(crate) fn valid_version(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+' | b'_'))
        && value.bytes().any(|byte| byte.is_ascii_digit())
}

pub(crate) fn recover_manifest(root: &Path) -> Result<()> {
    let component = root.join("components/chromium");
    let current = component.join("current.json");
    let previous = component.join("previous.json");
    if !current.exists() && previous.is_file() {
        std::fs::create_dir_all(&component)?;
        std::fs::copy(previous, current)?;
    }
    Ok(())
}

#[must_use]
pub fn target() -> &'static str {
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    {
        "aarch64-apple-darwin"
    }
    #[cfg(all(target_os = "macos", target_arch = "x86_64"))]
    {
        "x86_64-apple-darwin"
    }
    #[cfg(all(target_os = "windows", target_arch = "x86_64"))]
    {
        "x86_64-pc-windows-msvc"
    }
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    {
        "x86_64-unknown-linux-gnu"
    }
    #[cfg(not(any(
        all(target_os = "macos", target_arch = "aarch64"),
        all(target_os = "macos", target_arch = "x86_64"),
        all(target_os = "windows", target_arch = "x86_64"),
        all(target_os = "linux", target_arch = "x86_64")
    )))]
    {
        "unsupported"
    }
}

#[cfg(debug_assertions)]
fn development_browser() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("DY_QUICK_AUTH_CHROMIUM").map(PathBuf::from) {
        if path.is_file() {
            return Some(path);
        }
    }
    let candidates: &[&str] = if cfg!(target_os = "macos") {
        &[
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    } else if cfg!(target_os = "windows") {
        &[
            r"C:\Program Files\Chromium\Application\chrome.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
    } else {
        &[
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
        ]
    };
    candidates
        .iter()
        .map(PathBuf::from)
        .find(|path| path.is_file())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn managed_component_requires_target_hash_and_bounded_relative_path() {
        let root = tempfile::tempdir().unwrap();
        let component = root.path().join("components/chromium");
        std::fs::create_dir_all(&component).unwrap();
        let executable = component.join("chromium-test");
        File::create(&executable)
            .unwrap()
            .write_all(b"browser")
            .unwrap();
        let digest = file_digest(&executable).unwrap();
        std::fs::write(
            component.join("current.json"),
            serde_json::json!({"version":"1.2.3","target":target(),"executable":"chromium-test","sha256":digest,"capture_contract":CAPTURE_CONTRACT}).to_string(),
        )
        .unwrap();
        let ready = managed_from(root.path(), &component.join("current.json")).unwrap();
        assert_eq!(ready.view.state, "ready");
        assert_eq!(ready.view.source, "managed");

        std::fs::write(
            component.join("current.json"),
            serde_json::json!({"version":"1.2.3","target":target(),"executable":"../outside","sha256":"0".repeat(64),"capture_contract":CAPTURE_CONTRACT}).to_string(),
        )
        .unwrap();
        assert!(managed_from(root.path(), &component.join("current.json")).is_err());

        std::fs::write(
            component.join("current.json"),
            serde_json::json!({"version":"1.2.3","target":"other-target","executable":"chromium-test","sha256":"0".repeat(64),"capture_contract":CAPTURE_CONTRACT}).to_string(),
        )
        .unwrap();
        assert_eq!(status(root.path()).state, "incompatible");
    }
}
