//! Exclusive browser handover using the account's original complete Chromium profile.
use super::{child_running, component, launch, now_ms, profiles, Broker, CdpClient};
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
use std::{
    io::Write,
    path::{Path, PathBuf},
    process::Child,
    sync::{Arc, Mutex as StdMutex},
    time::Duration,
};
use tokio::sync::watch;

// Creator remains an identity/login bootstrap only. Its IM SDK (aid 2906)
// returned 8101/raw=1 with sender-only IDs in the controlled account comparison.
const LOGIN_URL: &str = "https://creator.douyin.com/creator-micro/data/following/chat";
const WEB_MESSAGES_URL: &str = "https://www.douyin.com/chat?isPopup=1";
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
        if !self.profile.as_os_str().is_empty() && !profiles::retained(&self.profile) {
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

    /// Opens the original account browser, or establishes it through an interactive login.
    /// # Errors
    /// Rejects concurrent browsers, missing consent, expired licensing and foreign identities.
    pub async fn creator_start(self: &Arc<Self>, id: String, confirmed: bool) -> Result<View> {
        uuid::Uuid::parse_str(&id)?;
        let _operation = self.reserve_component()?;
        self.require_browser_closed().await?;
        let service = self.importer.creator_service()?;
        let source = service.registry().load(&id)?;
        let generation = service.loaded_generation(&id).context("账号尚未加载")?;
        let saved_profile =
            profiles::lookup(&self.root, &id, &source.expected_sec_uid, generation)?;
        let resolved = component::resolve(&self.root)?;
        let original = service.begin_creator_hold(&id, confirmed).await?;
        let session_id = uuid::Uuid::new_v4().to_string();
        let view = View {
            session_id: session_id.clone(),
            account_id: id.clone(),
            state: "launching".into(),
            message: if saved_profile.is_some() {
                "正在打开此账号保存的完整浏览器环境".into()
            } else {
                "首次建立账号浏览器环境，请在网页中登录此账号；之后可复用".into()
            },
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
        let profile = saved_profile
            .unwrap_or_else(|| self.root.join("quick-auth/sessions").join(&session_id));
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
            // Close and join Chromium BEFORE any native verification, import or reconnection.
            let resources = (child, ws, profile.clone());
            let result = match broker.close_resources(&resources).await {
                Ok(()) => {
                    broker
                        .publish_creator_result(&profile, &source, result)
                        .await
                }
                Err(error) => Err(error),
            };
            broker.cleanup_resources(resources).await;
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

    async fn publish_creator_result(
        &self,
        profile: &Path,
        source: &CredentialImport,
        result: Result<Option<String>>,
    ) -> Result<bool> {
        let bundle = result?;
        let Some(bundle) = bundle else {
            return Ok(false);
        };
        let response = self
            .importer
            .import_creator_bundle(source.account_id.clone(), bundle)
            .await?;
        profiles::bind(
            &self.root,
            profile,
            &source.account_id,
            &source.expected_sec_uid,
            response["generation"]
                .as_u64()
                .context("同步凭证版本缺失")?,
        )?;
        anyhow::ensure!(
            response["_runtime_reload"] != true,
            "新凭证已保存，请重启客户端后继续"
        );
        Ok(true)
    }

    async fn run_creator(
        &self,
        run: CreatorRun<'_>,
        mut finish: watch::Receiver<bool>,
    ) -> Result<Option<String>> {
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
        close_restored_pages(&mut cdp).await?;
        self.creator_update(
            id,
            "awaiting_login",
            "正在校验此账号的完整浏览器环境；如显示登录页，请登录当前账号",
        )
        .await;
        initialize_creator(&mut cdp, source, child, &finish).await?;
        // Retain even if the user closes directly; rebinding to native generation happens only after import.
        profiles::bind(
            &self.root,
            profile,
            &source.account_id,
            &source.expected_sec_uid,
            generation,
        )?;
        self.creator_update(
            id,
            "open",
            "已打开抖音网页私信，客户端收发连接已暂停；结束时同步并交还会话",
        )
        .await;
        let maximum = tokio::time::Instant::now() + Duration::from_hours(4);
        loop {
            if !child_running(child)? || cdp.is_closed() {
                return Ok(None);
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
            if *finish.borrow() {
                self.creator_update(
                    id,
                    "syncing",
                    "正在同步当前浏览器登录态；关闭窗口后再恢复客户端连接",
                )
                .await;
                return self.capture_creator(source, &mut cdp).await.map(Some);
            }
            // Do not poll using a second native session while the browser owns the account.
            tokio::select! { () = tokio::time::sleep(Duration::from_secs(3)) => {}, _ = finish.changed() => {} }
        }
    }

    async fn capture_creator(
        &self,
        source: &CredentialImport,
        cdp: &mut CdpClient,
    ) -> Result<String> {
        cdp.initialize().await?;
        let limit = tokio::time::Instant::now() + Duration::from_secs(120);
        loop {
            anyhow::ensure!(
                tokio::time::Instant::now() < limit,
                "同步超时；浏览器环境已保留，请重新进入平台后结束同步"
            );
            if let Some(candidate) = cdp.collect().await?.candidate {
                anyhow::ensure!(
                    candidate.identity.sec_uid == source.expected_sec_uid,
                    "同步账号身份不匹配"
                );
                return Ok(candidate.bundle);
            }
            tokio::time::sleep(Duration::from_secs(1)).await;
        }
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
    // No setCookies or UA impersonation: reuse the original profile unchanged.
    cdp.navigate(LOGIN_URL).await?;
    let deadline = tokio::time::Instant::now() + super::SESSION_TIMEOUT;
    loop {
        if *finish.borrow() {
            anyhow::bail!("已取消打开平台，请确认凭证后手动恢复");
        }
        if let Ok(identity) = browser_identity(cdp).await {
            if identity["wrong_origin"] != true
                && check_identity(&identity, &source.expected_sec_uid)?
            {
                break;
            }
        }
        anyhow::ensure!(
            child_running(child)? && !cdp.is_closed(),
            "平台窗口已提前关闭"
        );
        anyhow::ensure!(
            tokio::time::Instant::now() < deadline,
            "等待登录超时，请重新进入平台完成登录"
        );
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
    // Use the official ordinary-web IM entry without rewriting its requests,
    // cookies, signing fields or platform decisions. Identity alone never proves delivery.
    cdp.navigate(WEB_MESSAGES_URL).await?;
    cdp.command("Page.bringToFront", json!({})).await?;
    Ok(())
}

// This endpoint belongs to the browser process launched for this account only.
// Avoid reconnecting restored background IM tabs on every persistent-profile open.
fn restored_page_ids(value: &Value, keep: &str) -> Vec<String> {
    value["targetInfos"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|t| t["type"] == "page")
        .filter_map(|t| t["targetId"].as_str())
        .filter(|id| !id.is_empty() && *id != keep)
        .map(str::to_owned)
        .collect()
}
async fn close_restored_pages(cdp: &mut CdpClient) -> Result<()> {
    let current = cdp.command("Target.getTargetInfo", json!({})).await?;
    let keep = current["targetInfo"]["targetId"]
        .as_str()
        .context("当前浏览器页面缺失")?;
    let targets = cdp.command("Target.getTargets", json!({})).await?;
    for id in restored_page_ids(&targets, keep) {
        cdp.command("Target.closeTarget", json!({"targetId":id}))
            .await?;
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
    fn restored_tabs_are_pruned_without_closing_current_page_or_workers() {
        let targets = json!({"targetInfos":[
            {"type":"page","targetId":"current"},
            {"type":"page","targetId":"old-chat"},
            {"type":"page","targetId":"old-capture"},
            {"type":"service_worker","targetId":"worker"}
        ]});
        assert_eq!(
            restored_page_ids(&targets, "current"),
            vec!["old-chat", "old-capture"]
        );
    }

    #[tokio::test]
    async fn account_identity_is_verified_before_entering_ordinary_web_chat() {
        use futures_util::{SinkExt, StreamExt};
        for scope in ["self", "other"] {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let endpoint = format!("ws://{}/devtools/page/test", listener.local_addr().unwrap());
            let server = tokio::spawn(async move {
                let (socket, _) = listener.accept().await.unwrap();
                let mut socket = tokio_tungstenite::accept_async(socket).await.unwrap();
                let mut methods = Vec::new();
                let mut urls = Vec::new();
                while let Some(Ok(message)) = socket.next().await {
                    let Ok(text) = message.to_text() else {
                        break;
                    };
                    let request: Value = serde_json::from_str(text).unwrap();
                    let method = request["method"].as_str().unwrap().to_owned();
                    if method == "Page.navigate" {
                        urls.push(request["params"]["url"].as_str().unwrap().to_owned());
                    }
                    let result = if method == "Runtime.evaluate" {
                        json!({"result":{"value":{"sec_uid":scope}}})
                    } else {
                        json!({})
                    };
                    methods.push(method);
                    if socket
                        .send(tokio_tungstenite::tungstenite::Message::Text(
                            json!({"id":request["id"],"result":result})
                                .to_string()
                                .into(),
                        ))
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
                (methods, urls)
            });
            let mut cdp = CdpClient::connect(&endpoint).await.unwrap();
            let source = CredentialImport {
                account_id: "test".into(),
                expected_sec_uid: "self".into(),
                user_agent: String::new(),
                storage_state: json!({}),
            };
            let (_finish, finished) = watch::channel(false);
            let result =
                initialize_creator(&mut cdp, &source, &Arc::new(StdMutex::new(None)), &finished)
                    .await;
            drop(cdp);
            let (methods, urls) = server.await.unwrap();
            if scope == "self" {
                assert!(result.is_ok());
                assert_eq!(urls, vec![LOGIN_URL, WEB_MESSAGES_URL]);
            } else {
                assert!(result.is_err());
                assert_eq!(urls, vec![LOGIN_URL]);
            }
            assert!(!methods
                .iter()
                .any(|m| m == "Network.setCookies" || m == "Network.setUserAgentOverride"));
        }
    }

    #[test]
    fn persistent_profile_requires_the_expected_account_identity() {
        assert!(check_identity(&json!({"sec_uid":"other"}), "self").is_err());
        assert!(!check_identity(&json!({"sec_uid":""}), "self").unwrap());
        assert!(check_identity(&json!({"sec_uid":"self"}), "self").unwrap());
        assert!(check_identity(&json!({"wrong_origin":true}), "self").is_err());
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
