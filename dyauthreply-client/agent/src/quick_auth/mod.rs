//! One-at-a-time disposable Chromium authorization owned entirely by the Rust Agent.
pub mod api;
mod cdp;
mod component;
mod installer;
#[cfg(test)]
mod live_probe;
mod sources;

use crate::onboarding::Importer;
use anyhow::{Context, Result};
use cdp::{Candidate, CandidateIdentity, CdpClient, Completeness};
use component::ComponentView;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs::File,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex as StdMutex,
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::sync::{watch, Mutex};

const SESSION_TIMEOUT: Duration = Duration::from_mins(30);
const START_TIMEOUT: Duration = Duration::from_secs(20);

#[derive(Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Create,
    Refresh,
    Recover,
}

#[derive(Clone, Serialize)]
pub struct IdentityView {
    sec_uid: String,
    nickname: String,
    unique_id: String,
    avatar: String,
}

impl From<CandidateIdentity> for IdentityView {
    fn from(value: CandidateIdentity) -> Self {
        Self {
            sec_uid: value.sec_uid,
            nickname: value.nickname,
            unique_id: value.unique_id,
            avatar: value.avatar,
        }
    }
}

#[derive(Clone, Serialize)]
pub struct SessionView {
    pub session_id: String,
    pub mode: Mode,
    pub target_account_id: Option<String>,
    pub state: &'static str,
    pub message: String,
    pub started_at_ms: u64,
    pub expires_at_ms: u64,
    pub completeness: Completeness,
    pub progress_fresh: bool,
    pub account: Option<IdentityView>,
    pub component: ComponentView,
}

struct SecretCandidate {
    bundle: String,
}

struct Session {
    view: SessionView,
    secret: Option<SecretCandidate>,
    child: Arc<StdMutex<Option<Child>>>,
    browser_ws: Arc<StdMutex<Option<String>>>,
    profile: PathBuf,
    cancel: watch::Sender<bool>,
}

type SessionResources = (
    Arc<StdMutex<Option<Child>>>,
    Arc<StdMutex<Option<String>>>,
    PathBuf,
);

impl Session {
    fn resources(&self) -> SessionResources {
        (
            self.child.clone(),
            self.browser_ws.clone(),
            self.profile.clone(),
        )
    }

    fn apply_progress(
        &mut self,
        state: &'static str,
        message: &str,
        completeness: Option<Completeness>,
    ) {
        if terminal(self.view.state)
            || matches!(self.view.state, "importing" | "awaiting_confirmation")
        {
            return;
        }
        self.view.state = state;
        self.view.message = message.into();
        self.view.progress_fresh = completeness.is_some();
        if let Some(completeness) = completeness {
            self.view.completeness = completeness;
        }
    }

    fn begin_import(&mut self, now: u64) -> Result<(String, Option<String>)> {
        anyhow::ensure!(
            self.view.state == "awaiting_confirmation" && !*self.cancel.borrow(),
            "账号资料尚未完成校验"
        );
        anyhow::ensure!(
            now < self.view.expires_at_ms,
            "快捷登录确认已超时，请重新登录"
        );
        anyhow::ensure!(
            self.view.progress_fresh && self.view.completeness.ready(),
            "采集字段尚未完成校验"
        );
        let bundle = self
            .secret
            .as_ref()
            .context("快捷登录凭证已释放")?
            .bundle
            .clone();
        self.view.state = "importing";
        self.view.message = "正在安全导入账号".into();
        Ok((bundle, self.view.target_account_id.clone()))
    }

    fn cancel_capture(&mut self) -> Result<bool> {
        if terminal(self.view.state) {
            return Ok(false);
        }
        anyhow::ensure!(
            self.view.state != "importing",
            "账号正在导入，请等待结果后关闭"
        );
        self.cancel.send_replace(true);
        self.secret = None;
        self.view.account = None;
        self.view.progress_fresh = false;
        self.view.state = "cancelled";
        self.view.message = "已取消快捷登录".into();
        Ok(true)
    }

    fn mark_failed(&mut self, error: String) -> bool {
        if terminal(self.view.state) || self.view.state == "importing" {
            return false;
        }
        self.cancel.send_replace(true);
        self.secret = None;
        self.view.account = None;
        self.view.progress_fresh = false;
        self.view.state = if error.contains("超时") {
            "timed_out"
        } else if error.contains("窗口已关闭") {
            "browser_exited"
        } else {
            "failed"
        };
        self.view.message = error;
        true
    }
}

async fn cancelled(stopped: &mut watch::Receiver<bool>) {
    loop {
        if *stopped.borrow_and_update() {
            return;
        }
        if stopped.changed().await.is_err() {
            return;
        }
    }
}

struct State {
    session: Option<Session>,
}

pub struct Broker {
    root: PathBuf,
    importer: Arc<Importer>,
    state: Mutex<State>,
    install: Arc<InstallProgress>,
}

struct InstallPhase {
    state: &'static str,
    message: String,
}

struct InstallProgress {
    phase: StdMutex<InstallPhase>,
    downloaded: AtomicU64,
    total: AtomicU64,
}

impl Drop for Broker {
    fn drop(&mut self) {
        let Ok(mut state) = self.state.try_lock() else {
            return;
        };
        let Some(session) = state.session.as_mut() else {
            return;
        };
        session.cancel.send_replace(true);
        session.secret = None;
        if let Ok(mut slot) = session.child.lock() {
            if let Some(mut child) = slot.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        let _ = std::fs::remove_dir_all(&session.profile);
    }
}

struct Launched {
    page_ws: String,
}

impl Broker {
    #[must_use]
    pub fn new(root: PathBuf, importer: Arc<Importer>) -> Arc<Self> {
        Arc::new(Self {
            root,
            importer,
            state: Mutex::new(State { session: None }),
            install: Arc::new(InstallProgress {
                phase: StdMutex::new(InstallPhase {
                    state: "idle",
                    message: String::new(),
                }),
                downloaded: AtomicU64::new(0),
                total: AtomicU64::new(0),
            }),
        })
    }

    #[must_use]
    pub fn component_status(&self) -> ComponentView {
        let mut view = component::status(&self.root);
        let (state, message) = self.install.phase.lock().map_or(
            ("failed", "快捷登录组件状态异常".into()),
            |phase| (phase.state, phase.message.clone()),
        );
        let downloaded = self.install.downloaded.load(Ordering::Relaxed);
        let total = self.install.total.load(Ordering::Relaxed);
        if state == "installing" {
            view.state = "installing";
            view.source = "managed";
            view.install_required = false;
            view.message = message;
            view.downloaded_bytes = downloaded;
            view.total_bytes = total;
            view.progress_percent = (total > 0).then(|| {
                u8::try_from(
                    downloaded
                        .saturating_mul(100)
                        .saturating_div(total)
                        .min(100),
                )
                .unwrap_or(100)
            });
        } else if state == "failed" && view.source != "managed" {
            view.state = "failed";
            view.install_required = true;
            view.message = message;
        } else if state == "failed" {
            view.message = format!("{message}；当前版本仍可使用");
        }
        view
    }

    /// Starts an on-demand managed browser install and returns immediately.
    /// # Errors
    /// Rejects active authorization or duplicate installation work.
    pub async fn install_component(self: &Arc<Self>) -> Result<ComponentView> {
        {
            let state = self.state.lock().await;
            anyhow::ensure!(
                state
                    .session
                    .as_ref()
                    .is_none_or(|session| terminal(session.view.state)),
                "请先结束当前快捷登录"
            );
        }
        {
            let mut phase = self
                .install
                .phase
                .lock()
                .map_err(|_| anyhow::anyhow!("快捷登录组件状态异常"))?;
            anyhow::ensure!(phase.state != "installing", "快捷登录组件正在安装");
            phase.state = "installing";
            phase.message = "正在下载快捷登录组件".into();
            self.install.downloaded.store(0, Ordering::Relaxed);
            self.install.total.store(0, Ordering::Relaxed);
        }
        let broker = Arc::downgrade(self);
        let root = self.root.clone();
        let progress_state = self.install.clone();
        tokio::spawn(async move {
            let callback: installer::Progress = Arc::new(move |downloaded, total| {
                progress_state
                    .downloaded
                    .store(downloaded, Ordering::Relaxed);
                progress_state.total.store(total, Ordering::Relaxed);
            });
            let result = installer::install_latest(&root, callback).await;
            let Some(broker) = broker.upgrade() else {
                return;
            };
            if let Ok(mut phase) = broker.install.phase.lock() {
                match result {
                    Ok(component) => {
                        phase.state = "idle";
                        phase.message = format!("快捷登录组件 {} 已安装", component.view.version);
                    }
                    Err(error) => {
                        phase.state = "failed";
                        phase.message = format!("安装失败：{error}");
                    }
                }
            }
            broker.importer.notify_quick_auth("component");
        });
        Ok(self.component_status())
    }

    /// Rolls back to the previous verified managed browser component.
    /// # Errors
    /// Rejects active authorization/installation or unavailable previous versions.
    pub async fn rollback_component(&self) -> Result<ComponentView> {
        {
            let state = self.state.lock().await;
            anyhow::ensure!(
                state
                    .session
                    .as_ref()
                    .is_none_or(|session| terminal(session.view.state)),
                "请先结束当前快捷登录"
            );
        }
        anyhow::ensure!(
            self.install
                .phase
                .lock()
                .map_err(|_| anyhow::anyhow!("快捷登录组件状态异常"))?
                .state
                != "installing",
            "快捷登录组件正在安装"
        );
        let root = self.root.clone();
        tokio::task::spawn_blocking(move || installer::rollback(&root)).await??;
        Ok(self.component_status())
    }

    /// Starts one disposable browser session and returns before user interaction completes.
    /// # Errors
    /// Rejects invalid modes/targets, missing components and concurrent sessions.
    pub async fn start(
        self: &Arc<Self>,
        mode: Mode,
        target_account_id: Option<String>,
    ) -> Result<SessionView> {
        anyhow::ensure!(
            self.install
                .phase
                .lock()
                .map_err(|_| anyhow::anyhow!("快捷登录组件状态异常"))?
                .state
                != "installing",
            "快捷登录组件正在安装"
        );
        let target_account_id = normalize_target(mode, target_account_id)?;
        if let Some(target) = target_account_id.as_deref() {
            self.importer.expected_scope(target)?;
        }
        let resolved = tokio::task::spawn_blocking({
            let root = self.root.clone();
            move || component::resolve(&root)
        })
        .await??;
        let mut state = self.state.lock().await;
        if state
            .session
            .as_ref()
            .is_some_and(|session| !terminal(session.view.state))
        {
            anyhow::bail!("已有快捷登录窗口正在使用");
        }
        let session_id = uuid::Uuid::new_v4().to_string();
        let started_at_ms = now_ms()?;
        let expires_at_ms = started_at_ms
            .saturating_add(u64::try_from(SESSION_TIMEOUT.as_millis()).unwrap_or(u64::MAX));
        let profile = self.root.join("quick-auth/sessions").join(&session_id);
        let (cancel, stopped) = watch::channel(false);
        let child = Arc::new(StdMutex::new(None));
        let browser_ws = Arc::new(StdMutex::new(None));
        let view = SessionView {
            session_id: session_id.clone(),
            mode,
            target_account_id,
            state: "launching",
            message: "正在打开安全登录窗口".into(),
            started_at_ms,
            expires_at_ms,
            completeness: Completeness::default(),
            progress_fresh: false,
            account: None,
            component: resolved.view.clone(),
        };
        state.session = Some(Session {
            view: view.clone(),
            secret: None,
            child: child.clone(),
            browser_ws: browser_ws.clone(),
            profile: profile.clone(),
            cancel: cancel.clone(),
        });
        drop(state);
        self.importer.notify_quick_auth(&session_id);
        let broker = Arc::downgrade(self);
        tokio::spawn(async move {
            let Some(broker) = broker.upgrade() else {
                return;
            };
            let result = broker
                .run(
                    session_id.clone(),
                    resolved.executable,
                    profile,
                    child,
                    browser_ws,
                    stopped,
                )
                .await;
            if let Err(error) = result {
                tracing::warn!(error = %error, "quick-auth session failed");
                broker
                    .fail_and_cleanup(&session_id, error.to_string())
                    .await;
            }
        });
        Ok(view)
    }

    /// Returns one sanitized in-memory authorization projection.
    /// # Errors
    /// Rejects malformed or stale session identifiers.
    pub async fn status(&self, session_id: &str) -> Result<SessionView> {
        validate_session_id(session_id)?;
        let state = self.state.lock().await;
        let session = state
            .session
            .as_ref()
            .filter(|session| session.view.session_id == session_id)
            .context("快捷登录会话不存在")?;
        Ok(session.view.clone())
    }

    /// Returns the latest sanitized authorization session for native UI recovery.
    pub async fn current(&self) -> Option<SessionView> {
        self.state
            .lock()
            .await
            .session
            .as_ref()
            .map(|session| session.view.clone())
    }

    /// Confirms the sanitized identity and publishes the held secret through onboarding.
    /// # Errors
    /// Rejects stale/double confirmation and durable import failures.
    pub async fn confirm(&self, session_id: &str) -> Result<Value> {
        validate_session_id(session_id)?;
        let (bundle, target) = {
            let mut state = self.state.lock().await;
            let session = current_mut(&mut state, session_id)?;
            anyhow::ensure!(child_running(&session.child)?, "登录窗口已关闭，请重新登录");
            session.begin_import(now_ms()?)?
        };
        self.importer.notify_quick_auth(session_id);
        let result = match self.importer.import_bundle(target, bundle).await {
            Ok(result) => result,
            Err(error) => {
                let mut state = self.state.lock().await;
                let session = current_mut(&mut state, session_id)?;
                session.view.state = "awaiting_confirmation";
                session.view.message = format!("导入失败：{error}");
                drop(state);
                self.importer.notify_quick_auth(session_id);
                return Err(error);
            }
        };
        self.cleanup(session_id).await;
        {
            let mut state = self.state.lock().await;
            let session = current_mut(&mut state, session_id)?;
            session.secret = None;
            session.view.state = "completed";
            session.view.message = "账号已导入，登录窗口已关闭".into();
        }
        self.importer.notify_quick_auth(session_id);
        let mut response = result;
        let reload = take_reload_intent(&mut response);
        response["runtime_reload_required"] = json!(reload);
        response["quick_auth"] = serde_json::to_value(self.status(session_id).await?)?;
        Ok(response)
    }

    /// Cancels only the matching owned authorization session.
    /// # Errors
    /// Rejects stale IDs; repeated cancellation is idempotent for the same terminal session.
    pub async fn cancel(&self, session_id: &str) -> Result<SessionView> {
        validate_session_id(session_id)?;
        let (resources, view) = {
            let mut state = self.state.lock().await;
            let session = current_mut(&mut state, session_id)?;
            if !session.cancel_capture()? {
                return Ok(session.view.clone());
            }
            (session.resources(), session.view.clone())
        };
        self.cleanup_resources(resources).await;
        self.importer.notify_quick_auth(session_id);
        Ok(view)
    }

    async fn run(
        &self,
        session_id: String,
        executable: PathBuf,
        profile: PathBuf,
        child: Arc<StdMutex<Option<Child>>>,
        browser_ws: Arc<StdMutex<Option<String>>>,
        mut stopped: watch::Receiver<bool>,
    ) -> Result<()> {
        let expires_at_ms = self.status(&session_id).await?.expires_at_ms;
        let deadline = tokio::time::Instant::now()
            + Duration::from_millis(expires_at_ms.saturating_sub(now_ms()?));
        let launched = tokio::select! {
            biased;
            () = cancelled(&mut stopped) => return Ok(()),
            result = tokio::time::timeout_at(deadline, launch(&executable, &profile, &child, &browser_ws)) => result.context("快捷登录已超时")??,
        };
        self.update(
            &session_id,
            "awaiting_login",
            "请在浏览器中完成抖音登录",
            None,
        )
        .await?;
        let mut cdp = tokio::select! {
            biased;
            () = cancelled(&mut stopped) => return Ok(()),
            result = tokio::time::timeout(START_TIMEOUT, CdpClient::connect(&launched.page_ws)) => result.context("浏览器连接超时")??,
        };
        tokio::select! {
            biased;
            () = cancelled(&mut stopped) => return Ok(()),
            result = cdp.initialize() => result?,
        }
        let mut capture_failures = 0_u32;
        loop {
            if *stopped.borrow_and_update() {
                return Ok(());
            }
            anyhow::ensure!(tokio::time::Instant::now() < deadline, "快捷登录已超时");
            if !child_running(&child)? {
                anyhow::bail!("登录窗口已关闭");
            }
            let mut poll_delay = Duration::from_secs(2);
            let collected = tokio::select! {
                biased;
                () = cancelled(&mut stopped) => return Ok(()),
                result = tokio::time::timeout_at(deadline, cdp.collect()) => result.context("快捷登录已超时")?,
            };
            if collected.is_ok() {
                capture_failures = 0;
            }
            match collected {
                Ok(mut capture) if capture.candidate.is_some() => {
                    let mut candidate = capture.candidate.take().context("采集结果缺失")?;
                    let verified = tokio::select! {
                        biased;
                        () = cancelled(&mut stopped) => return Ok(()),
                        result = tokio::time::timeout_at(deadline, self.verify_candidate(&session_id, &mut candidate)) => result.context("快捷登录已超时")?,
                    };
                    match verified {
                        Ok(()) => {
                            self.ready(&session_id, candidate, cdp.main_site_effort())
                                .await?;
                            return self
                                .watch_confirmation(&session_id, &child, &mut stopped, deadline)
                                .await;
                        }
                        Err(error) => {
                            let detail = format!("账号校验未通过：{error}");
                            self.update(
                                &session_id,
                                "collecting",
                                &detail,
                                Some(candidate.completeness),
                            )
                            .await?;
                        }
                    }
                }
                Ok(capture) => {
                    poll_delay = cdp.poll_delay();
                    self.report_waiting(&session_id, &cdp, capture.completeness)
                        .await?;
                }
                Err(error) => {
                    anyhow::ensure!(
                        !cdp.is_closed(),
                        "登录窗口已关闭或连接已中断，请重新打开登录窗口"
                    );
                    capture_failures = capture_failures.saturating_add(1);
                    anyhow::ensure!(
                        capture_failures < 30,
                        "连续采集失败，请检查登录页面后重试：{error}"
                    );
                    let detail = format!("本轮读取失败，旧字段状态已暂停显示：{error}（正在重试 {capture_failures}/30）");
                    self.update(&session_id, "collecting", &detail, None)
                        .await?;
                }
            }
            tokio::select! {
                () = tokio::time::sleep(poll_delay) => {},
                changed = stopped.changed() => {
                    if changed.is_err() || *stopped.borrow_and_update() { return Ok(()); }
                }
            }
        }
    }

    async fn report_waiting(
        &self,
        session_id: &str,
        cdp: &CdpClient,
        completeness: Completeness,
    ) -> Result<()> {
        let message = cdp.progress_message(&completeness);
        let logged_in = completeness.cookie();
        self.update(
            session_id,
            if logged_in {
                "collecting"
            } else {
                "awaiting_login"
            },
            if logged_in {
                &message
            } else {
                "请完成登录，系统会自动识别账号"
            },
            Some(completeness),
        )
        .await
    }

    async fn verify_candidate(&self, session_id: &str, candidate: &mut Candidate) -> Result<()> {
        self.update(
            session_id,
            "collecting",
            "登录成功，正在校验账号和发送凭证",
            Some(candidate.completeness.clone()),
        )
        .await?;
        let target = self.status(session_id).await?.target_account_id;
        let verified = self
            .importer
            .verify_bundle(target.as_deref(), &candidate.bundle)
            .await?;
        candidate.identity = CandidateIdentity {
            sec_uid: verified.sec_uid,
            nickname: verified.nickname,
            unique_id: verified.unique_id,
            avatar: verified.avatar,
        };
        Ok(())
    }

    async fn ready(
        &self,
        session_id: &str,
        candidate: Candidate,
        (elapsed_ms, attempt): (u64, u32),
    ) -> Result<()> {
        tracing::info!(elapsed_ms, attempt, "quick-auth capture ready");
        let mut state = self.state.lock().await;
        let session = current_mut(&mut state, session_id)?;
        if *session.cancel.borrow() || terminal(session.view.state) {
            return Ok(());
        }
        anyhow::ensure!(now_ms()? < session.view.expires_at_ms, "快捷登录已超时");
        session.secret = Some(SecretCandidate {
            bundle: candidate.bundle,
        });
        session.view.state = "awaiting_confirmation";
        session.view.message = "请确认识别到的抖音账号".into();
        session.view.completeness = candidate.completeness;
        session.view.progress_fresh = true;
        session.view.account = Some(candidate.identity.into());
        drop(state);
        self.importer.notify_quick_auth(session_id);
        Ok(())
    }

    async fn update(
        &self,
        session_id: &str,
        state_name: &'static str,
        message: &str,
        completeness: Option<Completeness>,
    ) -> Result<()> {
        let mut state = self.state.lock().await;
        let session = current_mut(&mut state, session_id)?;
        session.apply_progress(state_name, message, completeness);
        drop(state);
        self.importer.notify_quick_auth(session_id);
        Ok(())
    }

    async fn fail_and_cleanup(&self, session_id: &str, error: String) {
        let resources = {
            let mut state = self.state.lock().await;
            let Ok(session) = current_mut(&mut state, session_id) else {
                return;
            };
            if !session.mark_failed(error) {
                return;
            }
            session.resources()
        };
        self.cleanup_resources(resources).await;
        self.importer.notify_quick_auth(session_id);
    }

    async fn watch_confirmation(
        &self,
        session_id: &str,
        child: &Arc<StdMutex<Option<Child>>>,
        stopped: &mut watch::Receiver<bool>,
        deadline: tokio::time::Instant,
    ) -> Result<()> {
        loop {
            let state = self.status(session_id).await?.state;
            if terminal(state) {
                return Ok(());
            }
            // Once import starts, only its durable result may finish the session.
            if state != "importing" {
                let error = if tokio::time::Instant::now() >= deadline {
                    Some("快捷登录确认已超时，请重新登录")
                } else if !child_running(child)? {
                    Some("登录窗口已关闭，请重新登录")
                } else {
                    None
                };
                if let Some(error) = error {
                    self.fail_and_cleanup(session_id, error.into()).await;
                    continue;
                }
            }
            tokio::select! {
                () = cancelled(stopped) => return Ok(()),
                () = tokio::time::sleep(Duration::from_millis(250)) => {},
            }
        }
    }

    async fn cleanup(&self, session_id: &str) {
        let resources = {
            let state = self.state.lock().await;
            state.session.as_ref().and_then(|session| {
                (session.view.session_id == session_id).then(|| session.resources())
            })
        };
        if let Some(resources) = resources {
            self.cleanup_resources(resources).await;
        }
    }

    async fn cleanup_resources(&self, (child, browser_ws, profile): SessionResources) {
        let ws = browser_ws.lock().ok().and_then(|guard| guard.clone());
        if let Some(ws) = ws {
            let _ = close_browser(&ws).await;
        }
        let _ = tokio::task::spawn_blocking(move || stop_child_and_remove(&child, &profile)).await;
    }
}

fn normalize_target(mode: Mode, target: Option<String>) -> Result<Option<String>> {
    let target = target
        .map(|value| value.trim().to_owned())
        .filter(|v| !v.is_empty());
    match mode {
        Mode::Create => anyhow::ensure!(target.is_none(), "新增账号不能绑定已有账号"),
        Mode::Refresh | Mode::Recover => anyhow::ensure!(target.is_some(), "请选择需要维护的账号"),
    }
    if let Some(value) = &target {
        anyhow::ensure!(
            value.len() <= 128 && !value.chars().any(char::is_control),
            "账号编号无效"
        );
    }
    Ok(target)
}

fn current_mut<'a>(state: &'a mut State, session_id: &str) -> Result<&'a mut Session> {
    state
        .session
        .as_mut()
        .filter(|session| session.view.session_id == session_id)
        .context("快捷登录会话不存在")
}

fn terminal(value: &str) -> bool {
    matches!(
        value,
        "completed" | "cancelled" | "timed_out" | "browser_exited" | "failed"
    )
}

fn take_reload_intent(response: &mut Value) -> bool {
    response
        .as_object_mut()
        .and_then(|value| value.remove("_runtime_reload"))
        .and_then(|value| value.as_bool())
        .unwrap_or(false)
}

fn validate_session_id(value: &str) -> Result<()> {
    let parsed = uuid::Uuid::parse_str(value)?;
    anyhow::ensure!(parsed.to_string() == value, "快捷登录会话编号无效");
    Ok(())
}

fn now_ms() -> Result<u64> {
    Ok(u64::try_from(
        SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis(),
    )?)
}

// A covered login window (for example behind the client after scanning) is
// otherwise treated as hidden: rendering freezes, the main-site feed never
// mounts its video player and the formal UIFID is never computed. Keep covered
// windows live; a minimized window is restored by the collector instead.
const BROWSER_ARGS: &[&str] = &[
    "--remote-debugging-port=0",
    "--remote-debugging-address=127.0.0.1",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--disable-background-timer-throttling",
    "--disable-features=CalculateNativeWinOcclusion",
    "--new-window",
    "about:blank",
];

async fn launch(
    executable: &Path,
    profile: &Path,
    child_slot: &Arc<StdMutex<Option<Child>>>,
    browser_ws_slot: &Arc<StdMutex<Option<String>>>,
) -> Result<Launched> {
    create_private_dir(profile)?;
    let mut command = Command::new(executable);
    command
        .arg(format!("--user-data-dir={}", profile.display()))
        .args(BROWSER_ARGS)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(target_os = "macos")]
    command.arg("--use-mock-keychain");
    let child = command.spawn().context("快捷登录组件启动失败")?;
    let pid = child.id();
    *child_slot
        .lock()
        .map_err(|_| anyhow::anyhow!("浏览器进程状态异常"))? = Some(child);
    write_owner(profile, pid, executable)?;
    let active = profile.join("DevToolsActivePort");
    let content = tokio::time::timeout(START_TIMEOUT, async {
        loop {
            if let Ok(content) = std::fs::read_to_string(&active) {
                if !content.trim().is_empty() {
                    return Ok::<String, anyhow::Error>(content);
                }
            }
            anyhow::ensure!(child_running(child_slot)?, "快捷登录组件启动后退出");
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .context("快捷登录组件启动超时")??;
    let mut lines = content.lines();
    let port: u16 = lines.next().context("浏览器调试端口缺失")?.parse()?;
    anyhow::ensure!(port > 0, "浏览器调试端口无效");
    let browser_path = lines.next().context("浏览器调试会话缺失")?;
    anyhow::ensure!(
        browser_path.starts_with("/devtools/browser/"),
        "浏览器调试会话无效"
    );
    let browser_ws = format!("ws://127.0.0.1:{port}{browser_path}");
    *browser_ws_slot
        .lock()
        .map_err(|_| anyhow::anyhow!("浏览器连接状态异常"))? = Some(browser_ws);
    let page_ws = discover_page(port).await?;
    Ok(Launched { page_ws })
}

async fn discover_page(port: u16) -> Result<String> {
    let response = wreq::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(3))
        .build()?
        .get(format!("http://127.0.0.1:{port}/json/list"))
        .send()
        .await?;
    anyhow::ensure!(response.status().is_success(), "浏览器页面发现失败");
    let bytes = response.bytes().await?;
    anyhow::ensure!(bytes.len() <= 256 * 1024, "浏览器页面列表过大");
    let rows: Vec<Value> = serde_json::from_slice(&bytes)?;
    let raw = rows
        .iter()
        .find(|row| {
            row["type"] == "page"
                && row["url"].as_str().is_some_and(|url| {
                    url == "about:blank" || url.starts_with("https://creator.douyin.com/")
                })
        })
        .and_then(|row| row["webSocketDebuggerUrl"].as_str())
        .context("浏览器页面连接不存在")?;
    let uri: wreq::Uri = raw.parse()?;
    anyhow::ensure!(
        uri.scheme_str() == Some("ws")
            && uri.host() == Some("127.0.0.1")
            && uri.port_u16() == Some(port),
        "浏览器页面连接越界"
    );
    Ok(raw.into())
}

fn child_running(child: &Arc<StdMutex<Option<Child>>>) -> Result<bool> {
    let mut guard = child
        .lock()
        .map_err(|_| anyhow::anyhow!("浏览器进程状态异常"))?;
    guard
        .as_mut()
        .map_or(Ok(false), |child| Ok(child.try_wait()?.is_none()))
}

async fn close_browser(raw: &str) -> Result<()> {
    let mut browser = CdpClient::connect(raw).await?;
    let _ = browser.close().await;
    Ok(())
}

fn stop_child_and_remove(child: &Arc<StdMutex<Option<Child>>>, profile: &Path) -> Result<()> {
    let mut owned = child
        .lock()
        .map_err(|_| anyhow::anyhow!("浏览器进程状态异常"))?
        .take();
    if let Some(child) = owned.as_mut() {
        for _ in 0..50 {
            if child.try_wait()?.is_some() {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if child.try_wait()?.is_none() {
            child.kill()?;
            let _ = child.wait();
        }
    }
    if profile.is_dir() {
        std::fs::remove_dir_all(profile)?;
    }
    Ok(())
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

fn write_owner(profile: &Path, pid: u32, executable: &Path) -> Result<()> {
    let path = profile.join("dyauthreply-owner.json");
    let mut file = File::create(&path)?;
    serde_json::to_writer(
        &mut file,
        &json!({"pid":pid,"executable":executable,"started_at_ms":now_ms()?}),
    )?;
    file.sync_all()?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn session(root: &Path) -> Session {
        let (cancel, _) = watch::channel(false);
        Session {
            view: SessionView {
                session_id: uuid::Uuid::new_v4().to_string(),
                mode: Mode::Create,
                target_account_id: None,
                state: "collecting",
                message: String::new(),
                started_at_ms: 1,
                expires_at_ms: 100,
                completeness: Completeness::new([true; 6]).with_main(true, true, true),
                progress_fresh: true,
                account: None,
                component: component::status(root),
            },
            secret: Some(SecretCandidate {
                bundle: "synthetic-private-bundle".into(),
            }),
            child: Arc::new(StdMutex::new(None)),
            browser_ws: Arc::new(StdMutex::new(None)),
            profile: root.join("owned"),
            cancel,
        }
    }

    #[test]
    fn failed_read_marks_old_progress_stale_and_success_repairs_it() {
        let root = tempfile::tempdir().unwrap();
        let mut s = session(root.path());
        s.apply_progress("collecting", "read failed", None);
        assert!(!s.view.progress_fresh);
        s.apply_progress("collecting", "recovered", Some(Completeness::default()));
        assert!(s.view.progress_fresh);
        assert!(!s.view.completeness.ready());
        let projection = serde_json::to_string(&s.view).unwrap();
        assert!(!projection.contains("synthetic-private-bundle"));
    }

    #[test]
    fn expired_or_stale_confirmation_never_starts_import() {
        let root = tempfile::tempdir().unwrap();
        let mut s = session(root.path());
        s.view.state = "awaiting_confirmation";
        assert!(s.begin_import(100).is_err());
        assert_eq!(s.view.state, "awaiting_confirmation");
        s.view.progress_fresh = false;
        assert!(s.begin_import(99).is_err());
        s.view.progress_fresh = true;
        assert!(s.begin_import(99).is_ok());
        assert!(s.begin_import(99).is_err()); // no double submit
    }

    #[test]
    fn in_flight_import_cannot_be_cancelled_or_overwritten_by_late_events() {
        let root = tempfile::tempdir().unwrap();
        let mut s = session(root.path());
        s.view.state = "awaiting_confirmation";
        s.begin_import(2).unwrap();
        assert!(s.cancel_capture().is_err());
        assert!(!s.mark_failed("登录窗口已关闭".into()));
        s.apply_progress("collecting", "late response", None);
        assert_eq!(s.view.state, "importing");
        assert!(!*s.cancel.borrow());
        assert!(s.secret.is_some());
    }

    #[test]
    fn cancelled_and_failed_sessions_release_secrets_and_ignore_late_progress() {
        let root = tempfile::tempdir().unwrap();
        for error in [None, Some("快捷登录确认已超时"), Some("登录窗口已关闭")] {
            let mut s = session(root.path());
            if let Some(error) = error {
                assert!(s.mark_failed(error.into()));
            } else {
                assert!(s.cancel_capture().unwrap());
            }
            let terminal_state = s.view.state;
            assert!(s.secret.is_none());
            assert!(!s.view.progress_fresh);
            s.apply_progress("collecting", "late response", Some(Completeness::default()));
            assert_eq!(s.view.state, terminal_state);
            assert!(!s.cancel_capture().unwrap());
        }
    }

    #[test]
    fn saved_cleanup_handles_do_not_remove_a_replacement_session() {
        let root = tempfile::tempdir().unwrap();
        let mut s = session(root.path());
        create_private_dir(&s.profile).unwrap();
        let (old_child, _, old_profile) = s.resources();
        s.cancel_capture().unwrap();
        s.profile = root.path().join("replacement");
        create_private_dir(&s.profile).unwrap();
        stop_child_and_remove(&old_child, &old_profile).unwrap();
        assert!(!old_profile.exists());
        assert!(s.profile.exists());
    }

    #[tokio::test]
    async fn cancellation_interrupts_a_pending_operation_and_already_cancelled_work() {
        let (tx, mut rx) = watch::channel(false);
        let task = tokio::spawn(async move {
            tokio::select! {
                () = cancelled(&mut rx) => true,
                () = std::future::pending::<()>() => false,
            }
        });
        tx.send_replace(true);
        assert!(task.await.unwrap());
        let mut rx = tx.subscribe();
        tokio::time::timeout(Duration::from_millis(100), cancelled(&mut rx))
            .await
            .unwrap();
    }

    #[test]
    fn covered_login_windows_stay_live_and_load_nothing_remote_at_start() {
        for flag in [
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-background-timer-throttling",
            "--disable-features=CalculateNativeWinOcclusion",
        ] {
            assert!(BROWSER_ARGS.contains(&flag), "{flag}");
        }
        assert_eq!(BROWSER_ARGS.last(), Some(&"about:blank"));
        assert!(BROWSER_ARGS.contains(&"--remote-debugging-address=127.0.0.1"));
        assert!(!BROWSER_ARGS
            .iter()
            .any(|arg| arg.contains("enable-automation")));
    }

    #[test]
    fn modes_require_the_correct_target_shape() {
        assert!(normalize_target(Mode::Create, None).unwrap().is_none());
        assert!(normalize_target(Mode::Create, Some("a".into())).is_err());
        assert_eq!(
            normalize_target(Mode::Refresh, Some("a".into())).unwrap(),
            Some("a".into())
        );
        assert!(normalize_target(Mode::Recover, None).is_err());
    }

    #[test]
    fn cleanup_removes_only_the_explicit_session_profile() {
        let root = tempfile::tempdir().unwrap();
        let profile = root.path().join("session");
        let neighbor = root.path().join("neighbor");
        std::fs::create_dir_all(&profile).unwrap();
        std::fs::create_dir_all(&neighbor).unwrap();
        std::fs::write(profile.join("secret"), b"x").unwrap();
        let child = Arc::new(StdMutex::new(None));
        stop_child_and_remove(&child, &profile).unwrap();
        assert!(!profile.exists());
        assert!(neighbor.exists());
    }

    #[test]
    fn confirmation_returns_reload_intent_without_triggering_generic_request_restart() {
        let mut response = json!({"success":true,"_runtime_reload":true});
        let reload = take_reload_intent(&mut response);
        response["runtime_reload_required"] = json!(reload);
        assert_eq!(response["runtime_reload_required"], true);
        assert!(response.get("_runtime_reload").is_none());
    }
}
