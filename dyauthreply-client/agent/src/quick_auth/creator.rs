//! On-demand creator window: reuse the stored session, never initiate QR login.
use super::{child_running, component, launch, now_ms, Broker, CdpClient};
use crate::{
    protocol::{
        account_session::NativeAccountSession,
        credentials::{AccountCredentials, CredentialImport},
        live_http::ProtocolHttpClient,
    },
    runtime::messaging::ManualService,
};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::{
    io::Write,
    path::{Path, PathBuf},
    process::Child,
    sync::{Arc, Mutex as StdMutex},
    time::Duration,
};
use tokio::sync::watch;

const URL: &str = "https://creator.douyin.com/creator-micro/data/following/chat";
const HOSTS: [&str; 3] = ["www.douyin.com", "creator.douyin.com", "imapi.douyin.com"];
const AUTH_NAMES: [&str; 7] = [
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "sid_guard",
    "uid_tt",
    "bd_ticket_guard_server_data",
    "bd_ticket_guard_ts_sign_id",
];
const PAGE_IDENTITY: &str = r"(async () => {
  if(location.origin!=='https://creator.douyin.com') return {wrong_origin:true};
  const c=new AbortController(),t=setTimeout(()=>c.abort(),4000);
  try {const r=await fetch('/web/api/media/user/info/',{credentials:'include',signal:c.signal});
    const d=await r.json();return {sec_uid:d.status_code===0?String(d.user?.sec_uid||''):'',status:d.status_code};
  } catch {return {read_failed:true};} finally {clearTimeout(t);}
})()";

#[derive(Clone, Deserialize, Serialize)]
pub struct View {
    pub session_id: String,
    pub account_id: String,
    pub state: String,
    pub message: String,
    pub original_auto_reply: bool,
    pub auto_reply_restored: bool,
    pub credentials_updated: bool,
    pub started_at_ms: u64,
}

pub(super) struct Session {
    view: View,
    child: Arc<StdMutex<Option<Child>>>,
    profile: PathBuf,
    finish: watch::Sender<bool>,
}
impl Session {
    pub(super) fn active(&self) -> bool {
        !matches!(self.view.state.as_str(), "completed" | "needs_attention")
    }
}
impl Drop for Session {
    fn drop(&mut self) {
        if let Ok(mut slot) = self.child.lock() {
            if let Some(mut child) = slot.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        if !self.profile.as_os_str().is_empty() {
            let _ = std::fs::remove_dir_all(&self.profile);
        }
    }
}

pub(super) fn recover(root: &Path) -> Option<Session> {
    let raw = std::fs::read(root.join("creator-session.json")).ok()?;
    if raw.len() > 16 * 1024 {
        return None;
    }
    let mut view: View = serde_json::from_slice(&raw).ok()?;
    if view.state == "completed" {
        return None;
    }
    view.state = "needs_attention".into();
    view.message =
        "上次人工窗口异常结束，自动回复保持暂停；请重新进入平台校验，或更新凭证后手动恢复".into();
    view.auto_reply_restored = false;
    let (finish, _) = watch::channel(false);
    Some(Session {
        view,
        child: Arc::new(StdMutex::new(None)),
        profile: PathBuf::new(),
        finish,
    })
}

fn cookie_pairs(raw: &str) -> BTreeMap<String, String> {
    raw.split(';')
        .filter_map(|p| p.trim().split_once('='))
        .filter(|(name, value)| !name.is_empty() && !value.is_empty())
        .map(|(name, value)| (name.into(), value.into()))
        .collect()
}

fn restore_cookies(source: &CredentialImport) -> Result<Value> {
    let credentials = AccountCredentials::from_state(source.clone())?;
    let mut cookies = Vec::new();
    for host in HOSTS {
        let pairs = cookie_pairs(&credentials.cookie_header(host));
        anyhow::ensure!(
            pairs.contains_key("sessionid"),
            "现有凭证缺少登录会话，请先更新凭证"
        );
        for (name, value) in pairs {
            // Legacy imports retain scoped wire headers, not original cookie attributes.
            // Host-only root-path restoration avoids bleeding data into unrelated origins.
            cookies.push(json!({"name":name,"value":value,"domain":host,"path":"/","secure":true}));
        }
    }
    anyhow::ensure!(cookies.len() <= 1536, "账号 Cookie 数量异常");
    Ok(json!(cookies))
}

fn auth_changed(expected: &BTreeMap<String, String>, observed: &Value) -> bool {
    let Some(cookies) = observed["cookies"].as_array() else {
        return true;
    };
    AUTH_NAMES.iter().any(|name| {
        let values: Vec<_> = cookies
            .iter()
            .filter(|c| c["name"] == *name)
            .filter_map(|c| c["value"].as_str())
            .collect();
        match expected.get(*name) {
            Some(value) => values.is_empty() || values.iter().any(|v| *v != value),
            None => !values.is_empty(),
        }
    })
}

fn check_identity(value: &Value, expected: &str) -> Result<bool> {
    anyhow::ensure!(
        value["wrong_origin"] != true,
        "页面已离开创作者平台，请结束后重新进入"
    );
    let actual = value["sec_uid"].as_str().unwrap_or("");
    anyhow::ensure!(
        actual.is_empty() || actual == expected,
        "浏览器账号与托管账号不一致，自动回复保持暂停"
    );
    Ok(!actual.is_empty())
}

async fn browser_identity(cdp: &mut CdpClient) -> Result<Value> {
    let response = cdp
        .command(
            "Runtime.evaluate",
            json!({"expression":PAGE_IDENTITY,
        "awaitPromise":true,"returnByValue":true}),
        )
        .await?;
    anyhow::ensure!(
        response.get("exceptionDetails").is_none(),
        "平台身份读取失败"
    );
    response
        .pointer("/result/value")
        .cloned()
        .context("平台身份读取失败")
}

async fn browser_auth_changed(cdp: &mut CdpClient, source: &CredentialImport) -> Result<bool> {
    let credentials = AccountCredentials::from_state(source.clone())?;
    for host in HOSTS {
        let observed = cdp
            .command(
                "Network.getCookies",
                json!({"urls":[format!("https://{host}/")]}),
            )
            .await?;
        if auth_changed(&cookie_pairs(&credentials.cookie_header(host)), &observed) {
            return Ok(true);
        }
    }
    Ok(false)
}

#[derive(Debug, PartialEq, Eq)]
enum FinishAction {
    Reuse,
    Capture,
    Pause,
}

fn finish_action(original_valid: bool, browser_available: bool, changed: bool) -> FinishAction {
    if original_valid {
        FinishAction::Reuse
    } else if browser_available && changed {
        FinishAction::Capture
    } else {
        FinishAction::Pause
    }
}

struct CreatorRun<'a> {
    id: &'a str,
    source: &'a CredentialImport,
    generation: u64,
    executable: &'a Path,
    profile: &'a Path,
    child: &'a Arc<StdMutex<Option<Child>>>,
    ws: &'a Arc<StdMutex<Option<String>>>,
}

impl Broker {
    pub async fn creator_current(&self) -> Option<View> {
        self.creator.lock().await.as_ref().map(|s| s.view.clone())
    }

    /// Starts a bounded, disposable browser using the existing authenticated session.
    /// # Errors
    /// Rejects concurrent browsers, missing consent, expired licensing and foreign identities.
    pub async fn creator_start(self: &Arc<Self>, id: String, confirmed: bool) -> Result<View> {
        uuid::Uuid::parse_str(&id)?;
        let _operation = self.reserve_component()?;
        self.require_browser_closed().await?;
        let service = self.importer.creator_service()?;
        let source = service.registry().load(&id)?;
        restore_cookies(&source)?;
        let generation = service.loaded_generation(&id).context("账号尚未加载")?;
        let resolved = component::resolve(&self.root)?;
        let original = service.begin_creator_hold(&id, confirmed).await?;
        let session_id = uuid::Uuid::new_v4().to_string();
        let view = View {
            session_id: session_id.clone(),
            account_id: id.clone(),
            state: "launching".into(),
            message: "正在复用现有登录态打开创作者平台，无需扫码".into(),
            original_auto_reply: original,
            auto_reply_restored: false,
            credentials_updated: false,
            started_at_ms: now_ms()?,
        };
        // Marker contains public status only. Persisted disabled policy is the crash fence.
        if let Err(error) = self.persist_creator(&view) {
            let _ = service.finish_creator_hold(&id).await;
            return Err(error);
        }
        let profile = self.root.join("quick-auth/sessions").join(&session_id);
        let (finish, stopped) = watch::channel(false);
        let child = Arc::new(StdMutex::new(None));
        let ws = Arc::new(StdMutex::new(None));
        *self.creator.lock().await = Some(Session {
            view: view.clone(),
            child: child.clone(),
            profile: profile.clone(),
            finish,
        });
        let broker = Arc::clone(self);
        tokio::spawn(async move {
            let result = broker
                .run_creator(
                    CreatorRun {
                        id: &session_id,
                        source: &source,
                        generation,
                        executable: &resolved.executable,
                        profile: &profile,
                        child: &child,
                        ws: &ws,
                    },
                    stopped,
                )
                .await;
            broker
                .creator_update(&session_id, "closing", "正在关闭窗口并校验托管凭证")
                .await;
            broker.cleanup_resources((child, ws, profile)).await;
            broker
                .complete_creator(&session_id, &service, &id, result)
                .await;
        });
        Ok(view)
    }

    async fn complete_creator(
        &self,
        session_id: &str,
        service: &ManualService,
        account_id: &str,
        result: Result<bool>,
    ) {
        let checked = match result {
            Ok(updated) => verify_original(service, account_id).await.map(|()| updated),
            Err(error) => Err(error),
        };
        let (state, message, updated) = match checked {
            Ok(updated) => match service.finish_creator_hold(account_id).await {
                Ok(()) => (
                    "completed",
                    "窗口已关闭，凭证校验通过。自动回复保持关闭，如需继续，请在账号列表手动开启。"
                        .into(),
                    updated,
                ),
                Err(_) => (
                    "needs_attention",
                    "窗口已关闭，结束人工接管失败，请检查账号状态后手动开启自动回复".into(),
                    updated,
                ),
            },
            Err(error) => {
                let _ = service.finish_creator_hold(account_id).await;
                (
                    "needs_attention",
                    format!("{error}；窗口已关闭，自动回复保持关闭，请先处理登录问题"),
                    false,
                )
            }
        };
        if let Some(session) = self
            .creator
            .lock()
            .await
            .as_mut()
            .filter(|s| s.view.session_id == session_id)
        {
            session.view.state = state.into();
            session.view.message = message;
            session.view.auto_reply_restored = false;
            session.view.credentials_updated = updated;
            let _ = self.persist_creator(&session.view);
        }
        self.importer.notify_quick_auth("creator");
    }

    /// # Errors
    /// Only the matching session can be finished; stale UI actions cannot close another account.
    pub async fn creator_finish(&self, id: &str) -> Result<View> {
        let mut state = self.creator.lock().await;
        let session = state
            .as_mut()
            .filter(|s| s.view.session_id == id)
            .context("人工窗口会话不存在")?;
        if session.active() {
            session.finish.send_replace(true);
        }
        Ok(session.view.clone())
    }

    fn persist_creator(&self, view: &View) -> Result<()> {
        let temp = self.root.join("creator-session.pending");
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(&temp)?;
        file.write_all(&serde_json::to_vec(view)?)?;
        file.sync_all()?;
        std::fs::rename(temp, self.root.join("creator-session.json"))?;
        Ok(())
    }

    async fn creator_update(&self, id: &str, state: &str, message: &str) {
        if let Some(s) = self
            .creator
            .lock()
            .await
            .as_mut()
            .filter(|s| s.view.session_id == id)
        {
            s.view.state = state.into();
            s.view.message = message.into();
        }
        self.importer.notify_quick_auth("creator");
    }

    async fn run_creator(
        &self,
        run: CreatorRun<'_>,
        mut finish: watch::Receiver<bool>,
    ) -> Result<bool> {
        let CreatorRun {
            id,
            source,
            generation,
            executable,
            profile,
            child,
            ws,
        } = run;
        let launched = launch(executable, profile, child, ws).await?;
        let mut cdp = CdpClient::connect(&launched.page_ws).await?;
        if let Err(error) = initialize_creator(&mut cdp, source, child, &finish).await {
            if *finish.borrow() || cdp.is_closed() || !child_running(child)? {
                return Ok(false); // Completion still verifies stored credentials before resuming.
            }
            return Err(error);
        }
        self.creator_update(
            id,
            "open",
            "已免扫码进入当前账号；结束后自动回复保持关闭，需要时请在账号列表手动开启",
        )
        .await;
        let mut changed = false;
        let mut identity_check = tokio::time::Instant::now();
        let maximum = tokio::time::Instant::now() + Duration::from_hours(4);
        loop {
            if !child_running(child)? || cdp.is_closed() {
                break;
            }
            anyhow::ensure!(
                tokio::time::Instant::now() < maximum,
                "人工窗口已达到四小时上限"
            );
            let service = self.importer.creator_service()?;
            anyhow::ensure!(
                service.loaded_generation(&source.account_id) == Some(generation),
                "托管凭证版本已变化，请重新进入平台"
            );
            match browser_auth_changed(&mut cdp, source).await {
                Ok(value) => changed |= value,
                Err(_) if !child_running(child)? || cdp.is_closed() => {
                    break;
                }
                Err(_) => anyhow::bail!("浏览器凭证状态读取失败"),
            }
            if identity_check.elapsed() >= Duration::from_secs(30) || *finish.borrow() {
                let identity = match browser_identity(&mut cdp).await {
                    Ok(value) => value,
                    Err(_) if cdp.is_closed() || !child_running(child)? => break,
                    Err(error) => return Err(error),
                };
                anyhow::ensure!(
                    check_identity(&identity, &source.expected_sec_uid)?,
                    "平台登录态已失效"
                );
                identity_check = tokio::time::Instant::now();
            }
            if *finish.borrow() {
                self.creator_update(
                    id,
                    "closing",
                    "正在校验现有托管凭证，关闭浏览器不会直接判定登录失效",
                )
                .await;
                let verified = verify_original(&service, &source.account_id).await;
                match finish_action(
                    verified.is_ok(),
                    child_running(child)? && !cdp.is_closed(),
                    changed,
                ) {
                    FinishAction::Reuse => return Ok(false),
                    FinishAction::Pause => return verified.map(|()| false),
                    FinishAction::Capture => {
                        let result = self.synchronize_creator(id, source, &mut cdp).await;
                        // User closing a collection window cancels collection, not their old session.
                        // The outer completion path checks the stored credentials again after cleanup.
                        return match result {
                            Err(_) if cdp.is_closed() || !child_running(child)? => Ok(false),
                            other => other,
                        };
                    }
                }
            }
            tokio::select! { () = tokio::time::sleep(Duration::from_secs(3)) => {}, _ = finish.changed() => {} }
        }
        Ok(false)
    }
    async fn synchronize_creator(
        &self,
        id: &str,
        source: &CredentialImport,
        cdp: &mut CdpClient,
    ) -> Result<bool> {
        self.creator_update(
            id,
            "syncing",
            "现有凭证校验未通过，正在尝试从当前窗口恢复；直接关窗将结束同步并再次校验原凭证",
        )
        .await;
        cdp.initialize().await?;
        let limit = tokio::time::Instant::now() + Duration::from_secs(120);
        let bundle = loop {
            anyhow::ensure!(
                tokio::time::Instant::now() < limit,
                "现有凭证校验未通过且同步超时，请稍后重试或更新登录态"
            );
            if let Some(candidate) = cdp.collect().await?.candidate {
                anyhow::ensure!(
                    candidate.identity.sec_uid == source.expected_sec_uid,
                    "同步账号身份不匹配"
                );
                break candidate.bundle;
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        };
        let response = self
            .importer
            .import_creator_bundle(source.account_id.clone(), bundle)
            .await?;
        anyhow::ensure!(
            response["_runtime_reload"] != true,
            "新凭证已保存，请重启本地服务后手动开启自动回复"
        );
        Ok(true)
    }
}

async fn initialize_creator(
    cdp: &mut CdpClient,
    source: &CredentialImport,
    child: &Arc<StdMutex<Option<Child>>>,
    finish: &watch::Receiver<bool>,
) -> Result<()> {
    cdp.command("Network.enable", json!({})).await?;
    cdp.command("Page.enable", json!({})).await?;
    cdp.command("Runtime.enable", json!({})).await?;
    cdp.command(
        "Network.setUserAgentOverride",
        json!({"userAgent":source.user_agent}),
    )
    .await?;
    cdp.command(
        "Network.setCookies",
        json!({"cookies":restore_cookies(source)?}),
    )
    .await?;
    cdp.navigate(URL).await?;
    let deadline = tokio::time::Instant::now() + Duration::from_secs(45);
    loop {
        if *finish.borrow() {
            anyhow::bail!("已取消打开平台，请确认凭证后手动恢复");
        }
        if let Ok(identity) = browser_identity(cdp).await {
            if check_identity(&identity, &source.expected_sec_uid)? {
                break;
            }
        }
        anyhow::ensure!(
            child_running(child)? && !cdp.is_closed(),
            "平台窗口已提前关闭"
        );
        anyhow::ensure!(
            tokio::time::Instant::now() < deadline,
            "现有登录态未通过平台校验，请更新凭证后重试"
        );
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    Ok(())
}

async fn verify_original(service: &ManualService, id: &str) -> Result<()> {
    let source = service.registry().load(id)?;
    let credentials = AccountCredentials::from_state(source)?;
    let http = ProtocolHttpClient::for_user_agent(2, &credentials.user_agent)?;
    let mut session = NativeAccountSession::new(credentials, http, service.import_signer());
    tokio::time::timeout(Duration::from_secs(30), async {
        session.verify_for_import().await?;
        session.identity().await?;
        Ok::<(), anyhow::Error>(())
    })
    .await
    .context("托管凭证校验超时")?
    .context("托管凭证校验未通过")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn closing_with_changed_cookies_reuses_still_valid_credentials() {
        assert_eq!(finish_action(true, false, true), FinishAction::Reuse);
        assert_eq!(finish_action(true, true, true), FinishAction::Reuse);
        assert_eq!(finish_action(true, false, false), FinishAction::Reuse);
        assert_eq!(finish_action(true, true, false), FinishAction::Reuse);
    }

    #[test]
    fn expired_credentials_require_live_recovery_or_remain_paused() {
        assert_eq!(finish_action(false, true, true), FinishAction::Capture);
        assert_eq!(finish_action(false, false, true), FinishAction::Pause);
        assert_eq!(finish_action(false, false, false), FinishAction::Pause);
        assert_eq!(finish_action(false, true, false), FinishAction::Pause);
    }

    #[test]
    fn restored_cookies_are_host_scoped_and_never_exposed_in_views() {
        let source = CredentialImport {
            account_id: "sample".into(),
            expected_sec_uid: "self".into(),
            user_agent: "Chrome/152.0".into(),
            storage_state: json!({"cookies":[{"name":"sessionid","value":"SYNTHETIC"}],"_bd_ticket":{}}),
        };
        let cookies = restore_cookies(&source).unwrap();
        assert_eq!(
            cookies
                .as_array()
                .unwrap()
                .iter()
                .filter(|c| c["name"] == "sessionid")
                .count(),
            3
        );
        for c in cookies.as_array().unwrap() {
            assert!(HOSTS.contains(&c["domain"].as_str().unwrap()));
            assert_eq!(c["path"], "/");
        }
    }
    #[test]
    fn identity_mismatch_and_cookie_rotation_fail_closed() {
        assert!(check_identity(&json!({"sec_uid":"other"}), "self").is_err());
        assert!(!check_identity(&json!({"sec_uid":""}), "self").unwrap());
        assert!(check_identity(&json!({"sec_uid":"self"}), "self").unwrap());
        let expected = cookie_pairs("sessionid=old; bd_ticket_guard_server_data=old-ticket");
        assert!(!auth_changed(
            &expected,
            &json!({"cookies":[{"name":"sessionid","value":"old"},{"name":"bd_ticket_guard_server_data","value":"old-ticket"}]})
        ));
        assert!(auth_changed(
            &expected,
            &json!({"cookies":[{"name":"sessionid","value":"new"}]})
        ));
        assert!(auth_changed(
            &expected,
            &json!({"cookies":[{"name":"sessionid","value":"old"},{"name":"sessionid","value":"new","domain":".douyin.com"}]})
        ));
        assert!(auth_changed(&expected, &json!({"cookies":[]})));
    }
    #[test]
    fn interrupted_session_is_recovered_without_auto_resume() {
        let root = tempfile::tempdir().unwrap();
        let view = View {
            session_id: "id".into(),
            account_id: "account".into(),
            state: "open".into(),
            message: String::new(),
            original_auto_reply: true,
            auto_reply_restored: false,
            credentials_updated: false,
            started_at_ms: 1,
        };
        std::fs::write(
            root.path().join("creator-session.json"),
            serde_json::to_vec(&view).unwrap(),
        )
        .unwrap();
        let recovered = recover(root.path()).unwrap();
        assert!(!recovered.active());
        assert_eq!(recovered.view.state, "needs_attention");
        assert!(!recovered.view.auto_reply_restored);
    }
}
