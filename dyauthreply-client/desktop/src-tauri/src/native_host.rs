//! Own one native child, or attach to a proven compatible external instance without owning its exit.
use super::native_ws::NativeSocket;
use futures::StreamExt;
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::sync::Mutex;
use uuid::Uuid;
pub const PORT: u16 = 18765;
#[derive(Clone, Serialize)]
pub struct Status {
    pub phase: &'static str,
    pub owned: bool,
    pub pid: Option<u32>,
    pub error: Option<String>,
}
#[derive(Clone, Deserialize)]
struct Identity {
    api_version: u32,
    service: String,
    installation_id: Uuid,
    boot_id: Uuid,
    bind: String,
    binary_sha256: String,
    nonce: String,
    proof: String,
}
#[derive(Clone)]
pub(super) struct Connection {
    pub token: String,
    pub boot_id: Uuid,
    binary_sha256: String,
}
struct Inner {
    status: Status,
    child: Option<Child>,
    connection: Option<Connection>,
}
pub struct NativeHost {
    pub exit_ready: AtomicBool,
    pub quit_pending: AtomicBool,
    closing: AtomicBool,
    root: PathBuf,
    executable: PathBuf,
    inner: Mutex<Inner>,
    client: reqwest::Client,
    pub socket: Mutex<Option<NativeSocket>>,
}
#[derive(Serialize)]
pub struct NativeResponse {
    pub status: u16,
    pub body: String,
}
impl NativeHost {
    pub fn new(root: PathBuf, executable: PathBuf) -> Result<Self, String> {
        Ok(Self {
            exit_ready: AtomicBool::new(false),
            quit_pending: AtomicBool::new(false),
            closing: AtomicBool::new(false),
            root,
            executable,
            inner: Mutex::new(Inner {
                status: Status {
                    phase: "starting",
                    owned: false,
                    pid: None,
                    error: None,
                },
                child: None,
                connection: None,
            }),
            client: reqwest::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .timeout(Duration::from_secs(50))
                .build()
                .map_err(|_| "本地连接初始化失败")?,
            socket: Mutex::new(None),
        })
    }
    pub async fn status(&self) -> Status {
        self.inner.lock().await.status.clone()
    }
    pub async fn start(&self) -> Result<(), String> {
        let mut state = self.inner.lock().await;
        if state.connection.is_some() {
            return Ok(());
        }
        if state.status.phase == "failed" {
            return Err(state
                .status
                .error
                .clone()
                .unwrap_or_else(|| "请重新启动服务".into()));
        }
        match self.start_locked(&mut state).await {
            Ok(()) => Ok(()),
            Err(e) => {
                state.status.phase = "failed";
                state.status.error = Some(e.clone());
                Err(e)
            }
        }
    }
    async fn start_locked(&self, state: &mut Inner) -> Result<(), String> {
        if self.closing.load(Ordering::Acquire) {
            return Err("服务正在退出".into());
        }
        let binary = self.executable.clone();
        let expected = tauri::async_runtime::spawn_blocking(move || digest(&binary))
            .await
            .map_err(|_| "服务文件校验失败")??;
        if tokio::net::TcpStream::connect(("127.0.0.1", PORT))
            .await
            .is_ok()
        {
            let token = read_token(&self.root)?;
            let identity = self.verify_identity(&token, &expected).await?;
            state.connection = Some(Connection {
                token,
                boot_id: identity.boot_id,
                binary_sha256: identity.binary_sha256.clone(),
            });
            state.status = Status {
                phase: "ready",
                owned: false,
                pid: None,
                error: None,
            };
            return Ok(());
        }
        std::fs::create_dir_all(self.root.join("agent-v2")).map_err(|_| "服务数据目录创建失败")?;
        let log = Arc::new(std::sync::Mutex::new(RollingLog::new(
            self.root.join("logs"),
        )?));
        let mut command = Command::new(&self.executable);
        command
            .env("CLIENT_DATA_DIR", &self.root)
            .env("DY_AGENT_DATA_DIR", self.root.join("agent-v2"))
            .env("DY_AGENT_PARENT_STDIN", "1")
            .env("DY_AGENT_BIND", format!("127.0.0.1:{PORT}"))
            .env_remove("DY_AGENT_HOSTED_CONFIG")
            .env_remove("DY_AGENT_MESSAGING_CONFIG")
            .env_remove("DY_AGENT_LICENSE_CONFIG")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .spawn()
            .map_err(|_| "原生服务启动失败，请检查程序文件")?;
        if let Some(stdout) = child.stdout.take() {
            pipe_log(stdout, log.clone());
        }
        if let Some(stderr) = child.stderr.take() {
            pipe_log(stderr, log);
        }
        state.status.owned = true;
        state.status.pid = Some(child.id());
        state.child = Some(child);
        for _ in 0..120 {
            if self.closing.load(Ordering::Acquire) {
                return Err("服务正在退出".into());
            }
            if let Some(child) = state.child.as_mut() {
                if child
                    .try_wait()
                    .map_err(|_| "服务进程状态读取失败")?
                    .is_some()
                {
                    return Err("原生服务启动后退出，请查看运行日志".into());
                }
            }
            if let Ok(token) = read_token(&self.root) {
                if let Ok(identity) = self.verify_identity(&token, &expected).await {
                    state.connection = Some(Connection {
                        token,
                        boot_id: identity.boot_id,
                        binary_sha256: identity.binary_sha256.clone(),
                    });
                    state.status.phase = "ready";
                    return Ok(());
                }
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        Err("原生服务启动超时，请查看运行日志".into())
    }
    async fn verify_identity(&self, token: &str, expected: &str) -> Result<Identity, String> {
        let nonce = Uuid::new_v4().simple().to_string();
        let response = self
            .client
            .get(format!(
                "http://127.0.0.1:{PORT}/api/agent/v1/identity?nonce={nonce}"
            ))
            .timeout(Duration::from_secs(2))
            .send()
            .await
            .map_err(|_| "本地服务暂未就绪")?;
        if !response.status().is_success() {
            return Err("已有服务版本不兼容，请先退出已有客户端服务".into());
        }
        let bytes = bounded_body(response, 8192).await?;
        let identity: Identity =
            serde_json::from_slice(&bytes).map_err(|_| "本地服务身份格式错误")?;
        let installation = std::fs::read_to_string(self.root.join("agent-v2/installation_id"))
            .map_err(|_| "本地设备标识读取失败")?;
        verify_proof(&identity, token, expected, &nonce, installation.trim())?;
        Ok(identity)
    }
    pub(super) async fn connection(&self) -> Result<Connection, String> {
        let mut state = self.inner.lock().await;
        if self.closing.load(Ordering::Acquire) {
            return Err("服务正在退出".into());
        }
        if let Some(child) = state.child.as_mut() {
            if child
                .try_wait()
                .map_err(|_| "服务进程状态读取失败")?
                .is_some()
            {
                state.connection = None;
                state.status.phase = "failed";
                return Err("原生服务已退出，请重新连接".into());
            }
        }
        let mut connection = state.connection.clone().ok_or_else(|| {
            state
                .status
                .error
                .clone()
                .unwrap_or_else(|| "服务正在启动".into())
        })?;
        let owned = state.child.is_some();
        drop(state);
        let identity = self
            .verify_identity(&connection.token, &connection.binary_sha256)
            .await?;
        if owned && identity.boot_id != connection.boot_id {
            return Err("服务实例已变更，请重新连接".into());
        }
        connection.boot_id = identity.boot_id;
        Ok(connection)
    }

    pub async fn request(
        &self,
        path: String,
        method: String,
        body: Option<String>,
        admin_token: Option<String>,
    ) -> Result<NativeResponse, String> {
        let url = ipc_url(&path)?;
        let method: reqwest::Method = method.parse().map_err(|_| "请求方法无效")?;
        if !matches!(method.as_str(), "GET" | "POST" | "PATCH" | "PUT" | "DELETE") {
            return Err("请求方法未开放".into());
        }
        if body.as_ref().is_some_and(|v| v.len() > 3 * 1024 * 1024) {
            return Err("请求内容过大".into());
        }
        let auth = self.connection().await?;
        let mut request = self
            .client
            .request(method, url)
            .bearer_auth(auth.token)
            .header("Content-Type", "application/json");
        if let Some(token) = admin_token {
            if token.len() > 1024 {
                return Err("管理会话无效".into());
            }
            request = request.header("X-Admin-Token", token);
        }
        if let Some(body) = body {
            request = request.body(body);
        }
        let response = request
            .send()
            .await
            .map_err(|_| "本地服务请求失败，请检查连接")?;
        let status = response.status().as_u16();
        let body = String::from_utf8(bounded_body(response, 8 * 1024 * 1024).await?)
            .map_err(|_| "本地服务返回格式错误")?;
        Ok(NativeResponse { status, body })
    }
    pub async fn reload_configuration(&self) -> Result<(), String> {
        if self.status().await.owned {
            return self.restart().await;
        }
        let before = self.connection().await?.boot_id;
        let response = self
            .request(
                "/api/client/v1/runtime/reload".into(),
                "POST".into(),
                None,
                None,
            )
            .await?;
        if response.status >= 400 {
            return Err("配置已保存，请由原启动器重启服务".into());
        }
        for _ in 0..120 {
            tokio::time::sleep(Duration::from_millis(500)).await;
            if self.connection().await.is_ok_and(|c| c.boot_id != before) {
                return Ok(());
            }
        }
        Err("配置已保存，等待服务重新连接超时".into())
    }
    pub async fn restart(&self) -> Result<(), String> {
        self.stop_owned(false).await?;
        self.closing.store(false, Ordering::Release);
        {
            let mut state = self.inner.lock().await;
            state.status.phase = "starting";
            state.status.error = None;
            state.connection = None;
        }
        self.start().await
    }
    pub async fn stop_owned(&self, for_update: bool) -> Result<(), String> {
        self.closing.store(true, Ordering::Release);
        if let Some(socket) = self.socket.lock().await.take() {
            // Let the socket task publish its terminal event so the WebView does
            // not retain a stale connection id across an owned-agent restart.
            if socket
                .sender
                .try_send(serde_json::json!({"type":"__desktop_close"}))
                .is_err()
            {
                socket.task.abort();
            }
            let _ = socket.task.await;
        }
        let mut state = self.inner.lock().await;
        if state.child.is_none() {
            if for_update && state.connection.is_some() {
                self.closing.store(false, Ordering::Release);
                return Err("服务由另一个启动器管理，请退出该服务后再更新".into());
            }
            state.connection = None;
            state.status.phase = "stopped";
            return Ok(());
        }
        state.status.phase = "stopping";
        // Closing only this child's inherited stdin is an ownership-scoped graceful signal.
        if let Some(child) = state.child.as_mut() {
            drop(child.stdin.take());
        }
        for _ in 0..280 {
            if let Some(exit) = state
                .child
                .as_mut()
                .unwrap()
                .try_wait()
                .map_err(|_| "服务退出状态读取失败")?
            {
                state.child = None;
                state.connection = None;
                state.status.phase = "stopped";
                if for_update && !exit.success() {
                    return Err("服务异常退出，请检查后重试更新".into());
                }
                if for_update {
                    drop(state);
                    wait_for_port_release(PORT, 40, Duration::from_millis(50)).await?;
                }
                return Ok(());
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }

        Err("服务仍在结束已接收任务，暂未执行退出或安装，请稍后重试".into())
    }
}

async fn wait_for_port_release(port: u16, attempts: u16, delay: Duration) -> Result<(), String> {
    for _ in 0..attempts {
        if tokio::net::TcpStream::connect(("127.0.0.1", port))
            .await
            .is_err()
        {
            return Ok(());
        }
        tokio::time::sleep(delay).await;
    }
    Err("原生服务端口仍被占用，更新已停止".into())
}
pub fn data_root() -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("CLIENT_DATA_DIR") {
        let path = PathBuf::from(path);
        if path.is_absolute() {
            return Ok(path);
        }
        return Err("客户端数据路径必须是绝对路径".into());
    }
    #[cfg(target_os = "macos")]
    let root = directories::BaseDirs::new()
        .ok_or("用户目录不可用")?
        .home_dir()
        .join("Library/Application Support/DyAuthReply");
    #[cfg(target_os = "windows")]
    let root =
        PathBuf::from(std::env::var_os("APPDATA").ok_or("用户数据目录不可用")?).join("DyAuthReply");
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let root = directories::BaseDirs::new()
        .ok_or("用户目录不可用")?
        .data_dir()
        .join("DyAuthReply");
    Ok(root)
}

pub fn executable() -> Result<PathBuf, String> {
    let name = if cfg!(windows) {
        "dy-agent.exe"
    } else {
        "dy-agent"
    };
    let path = std::env::current_exe()
        .map_err(|_| "程序位置读取失败")?
        .parent()
        .ok_or("程序目录缺失")?
        .join(name);
    if path.is_file() {
        return Ok(path);
    }
    #[cfg(debug_assertions)]
    {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../agent/target/debug")
            .join(name);
        if path.is_file() {
            return Ok(path);
        }
    }
    Err("安装文件缺少原生服务，请重新安装".into())
}
fn read_token(root: &Path) -> Result<String, String> {
    let path = root.join("agent-v2/native-api-token");
    let file = File::open(path).map_err(|_| "本地连接凭据尚未生成")?;
    let metadata = file.metadata().map_err(|_| "连接凭据读取失败")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err("连接凭据权限异常".into());
        }
    }
    if metadata.len() > 256 {
        return Err("连接凭据格式异常".into());
    }
    let mut token = String::new();
    file.take(257)
        .read_to_string(&mut token)
        .map_err(|_| "连接凭据读取失败")?;
    let token = token.trim().to_owned();
    if !(32..=256).contains(&token.len()) {
        return Err("连接凭据格式异常".into());
    }
    Ok(token)
}
fn verify_proof(
    v: &Identity,
    token: &str,
    expected: &str,
    nonce: &str,
    installation: &str,
) -> Result<(), String> {
    if v.api_version != 1
        || v.service != "dy-agent"
        || v.nonce != nonce
        || v.installation_id.to_string() != installation
        || v.bind != format!("127.0.0.1:{PORT}")
    {
        return Err("本地服务身份不匹配".into());
    }
    let input = format!(
        "1\n{}\n{}\n{}\n{}\n{}",
        v.bind, v.installation_id, v.boot_id, v.binary_sha256, v.nonce
    );
    let bytes = hex_bytes(&v.proof)?;
    let mut mac = Hmac::<Sha256>::new_from_slice(token.as_bytes()).map_err(|_| "连接校验失败")?;
    mac.update(input.as_bytes());
    mac.verify_slice(&bytes)
        .map_err(|_| "本地服务身份校验失败")?;
    if v.binary_sha256 != expected {
        return Err("已有服务版本不同，请先退出已有服务再启动新版客户端".into());
    }
    Ok(())
}
fn hex_bytes(value: &str) -> Result<Vec<u8>, String> {
    if value.len() != 64 || !value.is_ascii() {
        return Err("身份校验格式错误".into());
    }
    (0..value.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&value[i..i + 2], 16).map_err(|_| "身份校验格式错误".into()))
        .collect()
}
fn ipc_url(path: &str) -> Result<url::Url, String> {
    if path.len() > 4096
        || !path.starts_with("/api/client/v1/")
        || path.contains(['\\', '#', '\r', '\n'])
    {
        return Err("请求路径未开放".into());
    }
    let url =
        url::Url::parse(&format!("http://127.0.0.1:{PORT}{path}")).map_err(|_| "请求路径无效")?;
    if !url.path().starts_with("/api/client/v1/") {
        return Err("请求路径未开放".into());
    }
    Ok(url)
}
async fn bounded_body(response: reqwest::Response, limit: usize) -> Result<Vec<u8>, String> {
    if response.content_length().is_some_and(|n| n > limit as u64) {
        return Err("服务响应过大".into());
    }
    let mut data = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(part) = stream.next().await {
        let part = part.map_err(|_| "读取服务响应失败")?;
        if data.len() + part.len() > limit {
            return Err("服务响应过大".into());
        }
        data.extend_from_slice(&part);
    }
    Ok(data)
}
fn digest(path: &Path) -> Result<String, String> {
    let mut f = File::open(path).map_err(|_| "原生服务文件不存在")?;
    let mut hash = Sha256::new();
    let mut bytes = [0u8; 65536];
    loop {
        let n = f.read(&mut bytes).map_err(|_| "原生服务校验失败")?;
        if n == 0 {
            break;
        }
        hash.update(&bytes[..n]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
struct RollingLog {
    dir: PathBuf,
    file: File,
    bytes: u64,
}
impl RollingLog {
    fn new(dir: PathBuf) -> Result<Self, String> {
        std::fs::create_dir_all(&dir).map_err(|_| "日志目录创建失败")?;
        let file = open_log(&dir.join("native-agent.log")).map_err(|_| "日志文件打开失败")?;
        let bytes = file.metadata().map_err(|_| "日志信息读取失败")?.len();
        Ok(Self { dir, file, bytes })
    }
    fn write(&mut self, data: &[u8]) -> std::io::Result<()> {
        if self.bytes + data.len() as u64 > 1024 * 1024 {
            // The bounded copy avoids renaming an open file on Windows.
            self.file.flush()?;
            for index in (1..=3).rev() {
                let from = self.dir.join(format!("native-agent.log.{index}"));
                let to = self.dir.join(format!("native-agent.log.{}", index + 1));
                if from.exists() {
                    std::fs::copy(from, to)?;
                }
            }
            std::fs::copy(
                self.dir.join("native-agent.log"),
                self.dir.join("native-agent.log.1"),
            )?;
            self.file.set_len(0)?;
            self.bytes = 0;
        }
        self.file.write_all(data)?;
        self.bytes += data.len() as u64;
        Ok(())
    }
}
fn open_log(path: &Path) -> std::io::Result<File> {
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(path)
}
fn pipe_log(mut input: impl Read + Send + 'static, log: Arc<std::sync::Mutex<RollingLog>>) {
    std::thread::spawn(move || {
        let mut data = [0u8; 4096];
        while let Ok(n) = input.read(&mut data) {
            if n == 0 {
                break;
            }
            if let Ok(mut log) = log.lock() {
                let _ = log.write(&data[..n]);
            }
        }
    });
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixed_origin_paths_cannot_escape() {
        for p in [
            "https://example.org/",
            "//example.org",
            "/api/client/v1/../../admin",
            "/api/client/v1/%2e%2e/%2e%2e/admin",
            "/api/client/v1/a#fragment",
        ] {
            assert!(ipc_url(p).is_err());
        }
        assert!(ipc_url("/api/client/v1/douyin/rule?page=1").is_ok());
    }
    #[test]
    fn challenge_is_bound_to_nonce_binary_root_and_boot() {
        let token = "a".repeat(64);
        let mut v = Identity {
            api_version: 1,
            service: "dy-agent".into(),
            installation_id: Uuid::new_v4(),
            boot_id: Uuid::new_v4(),
            bind: format!("127.0.0.1:{PORT}"),
            binary_sha256: "b".repeat(64),
            nonce: Uuid::new_v4().simple().to_string(),
            proof: String::new(),
        };
        let mut mac = Hmac::<Sha256>::new_from_slice(token.as_bytes()).unwrap();
        mac.update(
            format!(
                "1\n{}\n{}\n{}\n{}\n{}",
                v.bind, v.installation_id, v.boot_id, v.binary_sha256, v.nonce
            )
            .as_bytes(),
        );
        v.proof = mac
            .finalize()
            .into_bytes()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert!(verify_proof(
            &v,
            &token,
            &v.binary_sha256,
            &v.nonce,
            &v.installation_id.to_string()
        )
        .is_ok());
        assert!(verify_proof(
            &v,
            "wrong",
            &v.binary_sha256,
            &v.nonce,
            &v.installation_id.to_string()
        )
        .is_err());
        assert!(verify_proof(
            &v,
            &token,
            "wrong",
            &v.nonce,
            &v.installation_id.to_string()
        )
        .is_err());
        assert!(verify_proof(
            &v,
            &token,
            &v.binary_sha256,
            "old",
            &v.installation_id.to_string()
        )
        .is_err());
    }
    #[tokio::test]
    async fn update_port_gate_rejects_a_listener_then_accepts_release() {
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", 0))
            .await
            .unwrap();
        let port = listener.local_addr().unwrap().port();
        assert!(wait_for_port_release(port, 1, Duration::ZERO)
            .await
            .is_err());
        drop(listener);
        assert!(wait_for_port_release(port, 1, Duration::ZERO).await.is_ok());
    }
}
