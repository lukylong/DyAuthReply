use anyhow::{Context, Result};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use tokio::net::TcpStream;
use tokio_tungstenite::{tungstenite::Message, MaybeTlsStream, WebSocketStream};

const MAX_CDP_MESSAGE: usize = 2 * 1024 * 1024;
const COMMAND_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(8);

// The site computes the formal UIFID (a fingerprint) while setting up its video
// player. /jingxuan loads normally and carries a player; in a freshly logged-in
// profile a full document load of the recommendation feed (even the navigation
// link's own `?recommend=1&from_nav=1`) sat on "加载中" for a minute without
// UIFID, and `?recommend=1` does not always redirect. Enter on /jingxuan, never
// load the recommendation feed as a document, and switch in-app only if needed.
const SELECTED_URL: &str = "https://www.douyin.com/jingxuan";
// Let a /jingxuan document produce UIFID by itself before switching routes.
const SELECTED_SETTLE_SECS: u64 = 8;
// A hidden page does not render, so the fingerprint never runs. Restore it, but
// do not fight a user who keeps minimizing the window.
const RESTORE_INTERVAL_SECS: u64 = 5;
// The recommendation feed reached by a full load can sit on "加载中" with no
// video, or play without issuing the formal UIFID.
const STALLED_FEED_SECS: u64 = 25;
const STALLED_UIFID_SECS: u64 = 60;

// Follow the actual visible recommendation link with automatic retries. A click
// acknowledgement is not evidence that the recommendation feed was mounted.
// Resolve a click point from the actual visible navigation; the collector sends
// the normal browser mouse sequence below.
const OPEN_RECOMMENDATION: &str = r"(() => {
  if (location.origin !== 'https://www.douyin.com') return {status:'wrong_origin'};
  if (!location.pathname.startsWith('/jingxuan')) return {status:'already'};
  if (!['interactive','complete'].includes(document.readyState) || document.visibilityState !== 'visible') return {status:'not_ready'};
  const link = Array.from(document.querySelectorAll('a[href]')).find(a => {
    try {
      const u = new URL(a.href, location.href);
      return u.origin === 'https://www.douyin.com' && u.pathname === '/'
        && u.searchParams.get('recommend') === '1' && a.getClientRects().length > 0;
    } catch (_) { return false; }
  });
  if (!link) return {status:'not_ready'};
  const r = link.getBoundingClientRect();
  const x = r.left + r.width/2, y = r.top + r.height/2;
  if (![x,y,r.width,r.height].every(Number.isFinite) || r.width <= 0 || r.height <= 0
      || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return {status:'not_ready'};
  const hit = document.elementFromPoint(x,y);
  if (!hit || !link.contains(hit)) return {status:'not_ready'};
  return {status:'target',x,y};
})()";

// Fallback when the link is covered, the window is hidden or a real click did not
// switch: let the site's own link handler perform the in-app route change.
const CLICK_RECOMMENDATION: &str = r"(() => {
  if (location.origin !== 'https://www.douyin.com') return {status:'wrong_origin'};
  if (!location.pathname.startsWith('/jingxuan')) return {status:'already'};
  const link = Array.from(document.querySelectorAll('a[href]')).find(a => {
    try {
      const u = new URL(a.href, location.href);
      return u.origin === 'https://www.douyin.com' && u.pathname === '/' && u.searchParams.get('recommend') === '1';
    } catch (_) { return false; }
  });
  if (!link) return {status:'not_ready'};
  link.click();
  return {status:'clicked'};
})()";

const DTRAIT_HOOK: &str = r"(() => {
  if (window.__dyauthreplyDtraitHookInstalled) return;
  const original = JSON.stringify;
  const publish = (value, path) => {
    window.__dyauthreplyLatestDtraitBlob = String(value || '');
    window.__dyauthreplyLatestDtraitPath = String(path || '');
    if (typeof window.__dyauthreplyCaptureSample === 'function') {
      try { window.__dyauthreplyCaptureSample(original({blob:String(value || ''),path:String(path || '')})); } catch (_) {}
    }
  };
  const wrapped = function(value) {
    try {
      if (value && typeof value === 'object' && typeof value.dtrait === 'string'
          && value.dtrait.length > 20 && typeof value.path === 'string') {
        publish(value.dtrait, value.path);
      }
    } catch (_) {}
    return Reflect.apply(original, this, arguments);
  };
  try { Object.defineProperty(wrapped, 'toString', {value: () => original.toString()}); } catch (_) {}
  JSON.stringify = wrapped;
  window.__dyauthreplyDtraitHookInstalled = true;
})();";

const PAGE_INFO: &str = r"(async (readIdentity) => {
  const read = (names) => names.map(k => localStorage.getItem(k) || '').filter(Boolean);
  const keys = read(['security-sdk/s_sdk_crypt_sdk_new','security-sdk/s_sdk_crypt_sdk']);
  const protects = read(['security-sdk/s_sdk_sign_data_key/web_protect_new','security-sdk/s_sdk_sign_data_key_new/web_protect','security-sdk/s_sdk_sign_data_key/web_protect']);
  let account = {};
  if (readIdentity && location.hostname === 'creator.douyin.com') {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 3000);
      try {
        const response = await fetch('/web/api/media/user/info/', {credentials:'include',signal:controller.signal});
        const data = await response.json();
        if (response.ok && data.status_code === 0) {
          const user = data.user || {};
          const urls = user.avatar_thumb?.url_list || user.avatar_larger?.url_list || [];
          account = {nickname:String(user.nickname || ''),sec_uid:String(user.sec_uid || ''),
            unique_id:String(user.unique_id || user.short_id || ''),avatar:String(urls[0] || '')};
        }
      } finally { clearTimeout(timer); }
    } catch (_) {}
  }
  return {origin:location.hostname,pathname:location.pathname,keys:keys[0] || '',key_candidates:keys,
    web_protect:protects[0] || '',ticket_candidates:protects,
    ua:navigator.userAgent || '',account,feed_videos:document.querySelectorAll('video').length,
    visibility:document.visibilityState,
    browser_profile:{platform:navigator.platform,width:screen.width,height:screen.height,language:navigator.language,cpu_core_num:navigator.hardwareConcurrency,device_memory:navigator.deviceMemory,timezone_name:Intl.DateTimeFormat().resolvedOptions().timeZone,referer:'https://www.douyin.com/jingxuan',geo:[innerWidth,innerHeight,outerWidth,outerHeight,screen.availWidth,screen.availHeight,screen.width,screen.height]},
    dtrait_blob:String(window.__dyauthreplyLatestDtraitBlob || ''),
    dtrait_path:String(window.__dyauthreplyLatestDtraitPath || '')};
})";

#[derive(Clone, Serialize)]
#[serde(transparent)]
pub struct Completeness(BTreeMap<&'static str, bool>);

impl Default for Completeness {
    fn default() -> Self {
        Self::new([false; 6]).with_main(false, false, false)
    }
}

impl Completeness {
    pub(super) fn new(checks: [bool; 6]) -> Self {
        let [cookie, scoped_cookies, server_data, private_key, dtrait, identity] = checks;
        let ready = checks.into_iter().all(std::convert::identity);
        Self(BTreeMap::from([
            ("cookie", cookie),
            ("scoped_cookies", scoped_cookies),
            ("server_data", server_data),
            ("private_key", private_key),
            ("dtrait", dtrait),
            ("identity", identity),
            ("ready", ready),
        ]))
    }

    pub(super) fn with_main(mut self, uifid: bool, fingerprint: bool, sdk_token: bool) -> Self {
        self.0.insert("main_uifid", uifid);
        self.0.insert("main_fingerprint", fingerprint);
        self.0.insert("sdk_token", sdk_token);
        let ready = self.ready() && uifid && fingerprint && sdk_token;
        self.0.insert("ready", ready);
        self
    }

    pub fn missing_fields(&self) -> String {
        [
            ("cookie", "登录会话"),
            ("scoped_cookies", "分域登录会话"),
            ("main_uifid", "主站UIFID"),
            ("main_fingerprint", "主站指纹"),
            ("sdk_token", "SDK Token"),
            ("server_data", "发送票据"),
            ("private_key", "签名密钥"),
            ("dtrait", "设备采样"),
            ("identity", "账号资料"),
        ]
        .into_iter()
        .filter(|(key, _)| !self.0.get(key).copied().unwrap_or(false))
        .map(|(_, label)| label)
        .collect::<Vec<_>>()
        .join("、")
    }

    #[must_use]
    pub fn cookie(&self) -> bool {
        self.0.get("cookie").copied().unwrap_or(false)
    }

    #[must_use]
    pub fn ready(&self) -> bool {
        self.0.get("ready").copied().unwrap_or(false)
    }
}

#[derive(Clone, Serialize)]
pub struct CandidateIdentity {
    pub sec_uid: String,
    pub nickname: String,
    pub unique_id: String,
    pub avatar: String,
}

pub struct Candidate {
    pub bundle: String,
    pub identity: CandidateIdentity,
    pub completeness: Completeness,
}

pub struct Capture {
    pub completeness: Completeness,
    pub candidate: Option<Candidate>,
}

#[derive(Default)]
struct NetworkCapture {
    server_data: String,
    dtrait_header: String,
    dtrait_path: String,
    request_urls: HashMap<String, String>,
    query_ms_token: String,
    main_site: bool,
    contexts: HashMap<i64, String>,
    dtrait_blob: String,
    dtrait_blob_path: String,
}

pub struct CdpClient {
    socket: WebSocketStream<MaybeTlsStream<TcpStream>>,
    next_id: u64,
    captured: NetworkCapture,
    creator_identity: Option<Value>,
    creator_session: Option<String>,
    creator_material: Value,
    main_started: Option<tokio::time::Instant>,
    closed: bool,
    endpoint_base: String,
    main_page: Option<Box<CdpClient>>,
    current_path: String,
    recommendation_attempts: u32,
    recommendation_last_attempt: Option<tokio::time::Instant>,
    route_action_at: Option<tokio::time::Instant>,
    switch_step: u8,
    feed_videos: u64,
    recovery_attempts: u32,
    page_hidden: bool,
    restored_at: Option<tokio::time::Instant>,
    restore_attempts: u32,
}

impl CdpClient {
    /// # Errors
    /// Rejects non-loopback targets, failed handshakes and malformed protocol responses.
    pub async fn connect(raw: &str) -> Result<Self> {
        validate_ws(raw)?;
        let (socket, _) = tokio_tungstenite::connect_async(raw).await?;
        Ok(Self {
            socket,
            next_id: 0,
            captured: NetworkCapture::default(),
            creator_identity: None,
            creator_session: None,
            creator_material: Value::Null,
            main_started: None,
            closed: false,
            endpoint_base: format!(
                "ws://{}",
                raw.parse::<wreq::Uri>()?
                    .authority()
                    .context("CDP地址无效")?
            ),
            main_page: None,
            current_path: String::new(),
            recommendation_attempts: 0,
            recommendation_last_attempt: None,
            route_action_at: None,
            switch_step: 0,
            feed_videos: 0,
            recovery_attempts: 0,
            page_hidden: false,
            restored_at: None,
            restore_attempts: 0,
        })
    }

    /// # Errors
    /// Requires the page target to support the bounded CDP domains used by the collector.
    pub async fn initialize(&mut self) -> Result<()> {
        self.initialize_at("https://creator.douyin.com/creator-micro/data/following/chat")
            .await
    }

    async fn initialize_at(&mut self, url: &str) -> Result<()> {
        self.command("Network.enable", json!({})).await?;
        self.command("Runtime.enable", json!({})).await?;
        self.command(
            "Runtime.addBinding",
            json!({"name":"__dyauthreplyCaptureSample"}),
        )
        .await?;
        self.command("Page.enable", json!({})).await?;
        self.command(
            "Page.addScriptToEvaluateOnNewDocument",
            json!({"source":DTRAIT_HOOK}),
        )
        .await?;
        self.navigate(url).await?;
        Ok(())
    }

    /// # Errors
    /// Returns malformed/oversized CDP, page or credential data without exposing secrets.
    pub async fn collect(&mut self) -> Result<Capture> {
        let [_, creator_www, creator_cookies, _, creator_page] =
            self.snapshot(self.creator_identity.is_none()).await?;
        if self.creator_identity.is_none() {
            let sid = cookie_value(&creator_cookies, "sessionid")
                .or_else(|| cookie_value(&creator_cookies, "sessionid_ss"));
            if creator_page["origin"] == "creator.douyin.com"
                && !bounded(&creator_page["account"], "sec_uid", 256).is_empty()
                && sid.is_some()
            {
                let main = self.open_main_page().await?;
                self.creator_identity = Some(creator_page["account"].clone());
                self.creator_session = sid;
                self.main_page = Some(Box::new(main));
                self.main_started = Some(tokio::time::Instant::now());
            }
            return Ok(waiting_capture(
                cookie_value(&creator_www, "sessionid").is_some(),
            ));
        }
        anyhow::ensure!(
            creator_page["origin"] == "creator.douyin.com",
            "创作者页面已跳转，请回到登录页面后重试"
        );
        anyhow::ensure!(
            self.creator_session
                .as_deref()
                .is_some_and(|sid| same_session(&creator_cookies, sid)),
            "采集期间登录会话发生变化，请重新登录"
        );
        // Keep the creator tab alive: IM device sampling can finish AFTER identity
        // lookup. Re-read its material, but do not reissue the remote profile API.
        self.creator_material = creator_page;
        self.creator_material["captured_ticket"] = json!(self.captured.server_data);
        let main = self.main_page.as_mut().context("主站采集页面未建立")?;
        let result = main.snapshot(false).await;
        if main.is_closed() {
            self.closed = true;
        }
        let [storage, www, creator, imapi, mut page] = result?;
        anyhow::ensure!(
            page["origin"] == "www.douyin.com",
            "主站页面已跳转，请完成验证后重试"
        );
        let sid = cookie_value(&www, "sessionid").or_else(|| cookie_value(&www, "sessionid_ss"));
        anyhow::ensure!(
            sid == self.creator_session,
            "主站与创作者中心登录会话不一致，请统一账号后重试"
        );
        if main.page_hidden {
            main.restore_hidden_window().await?;
        } else if !has_main_uifid(&www) {
            if main.current_path.starts_with("/jingxuan") {
                main.open_recommendation_if_redirected().await?;
            } else if main.current_path == "/" {
                main.recover_stalled_recommendation().await?;
            }
        }
        page["account"] = self.creator_identity.clone().unwrap_or(Value::Null);
        merge_session_material(&mut page, &self.creator_material);
        build_candidate(&storage, &www, &creator, &imapi, &page, &main.captured)
    }

    async fn snapshot(&mut self, read_identity: bool) -> Result<[Value; 5]> {
        // These read-only commands are independent. Pipeline them over the same
        // socket, retaining per-origin cookies and dispatching replies by id.
        let [storage, www, creator, imapi, page] = self
            .commands([
                ("Storage.getCookies", json!({})),
                (
                    "Network.getCookies",
                    json!({"urls":["https://www.douyin.com/"]}),
                ),
                (
                    "Network.getCookies",
                    json!({"urls":["https://creator.douyin.com/"]}),
                ),
                (
                    "Network.getCookies",
                    json!({"urls":["https://imapi.douyin.com/"]}),
                ),
                (
                    "Runtime.evaluate",
                    json!({"expression":format!("{PAGE_INFO}({read_identity})"),"awaitPromise":true,"returnByValue":true}),
                ),
            ])
            .await?;
        anyhow::ensure!(
            page.get("exceptionDetails").is_none(),
            "浏览器页面正在跳转或脚本读取失败，请完成页面加载/验证"
        );
        let mut page = page
            .pointer("/result/value")
            .filter(|value| value.is_object())
            .cloned()
            .context("浏览器页面未返回有效采集结果")?;
        if matches!(
            page["origin"].as_str(),
            Some("creator.douyin.com" | "www.douyin.com")
        ) && bounded_text(&page, "dtrait_blob", 65_536).is_empty()
            && !self.captured.dtrait_blob.is_empty()
        {
            page["dtrait_blob"] = json!(self.captured.dtrait_blob);
            page["dtrait_path"] = json!(self.captured.dtrait_blob_path);
        }
        self.current_path = bounded_text(&page, "pathname", 512).into();
        self.feed_videos = page["feed_videos"].as_u64().unwrap_or(0);
        self.page_hidden = page["visibility"].as_str() == Some("hidden");
        Ok([storage, www, creator, imapi, page])
    }

    // Retry automatically until the session deadline, never asking the user to
    // click. Per loaded /jingxuan document: a real mouse click on the visible link,
    // then the link's own handler, then reload /jingxuan and start over.
    async fn open_recommendation_if_redirected(&mut self) -> Result<()> {
        let interval = if self.recommendation_attempts < 3 {
            3
        } else {
            10
        };
        if self
            .recommendation_last_attempt
            .is_some_and(|last| last.elapsed().as_secs() < interval)
        {
            return Ok(());
        }
        // Back off read failures too, so they interleave with successful polls.
        self.recommendation_last_attempt = Some(tokio::time::Instant::now());
        self.command("Page.bringToFront", json!({})).await?;
        let target = self.evaluate_status(OPEN_RECOMMENDATION).await?;
        let status = target["status"].as_str();
        if !matches!(status, Some("target" | "not_ready")) {
            anyhow::ensure!(
                status == Some("already"),
                "主站推荐入口未就绪，正在等待页面加载或验证"
            );
            return Ok(());
        }
        if self
            .route_action_at
            .is_some_and(|at| at.elapsed().as_secs() < SELECTED_SETTLE_SECS)
        {
            return Ok(());
        }
        let mouse = status == Some("target") && self.switch_step == 0;
        let action = if mouse {
            "mouse_click"
        } else if self.switch_step <= 1 {
            "link_handler"
        } else {
            "reload_selected"
        };
        // Count attempts before dispatch so a failing send still backs off.
        self.recommendation_attempts = self.recommendation_attempts.saturating_add(1);
        self.route_action_at = Some(tokio::time::Instant::now());
        tracing::info!(
            attempt = self.recommendation_attempts,
            action,
            "quick-auth main site on selected feed; switching to recommendation"
        );
        match action {
            "mouse_click" => {
                self.switch_step = 1;
                let (x, y) = recommendation_click_point(&target)?;
                for (kind, buttons) in
                    [("mouseMoved", 0), ("mousePressed", 1), ("mouseReleased", 0)]
                {
                    self.command("Input.dispatchMouseEvent", json!({
                        "type":kind,"x":x,"y":y,"button":if kind=="mouseMoved" {"none"} else {"left"},
                        "buttons":buttons,"clickCount":i32::from(kind!="mouseMoved"),"pointerType":"mouse"
                    })).await?;
                }
                Ok(())
            }
            "link_handler" => {
                self.switch_step = 2;
                self.evaluate_status(CLICK_RECOMMENDATION).await.map(|_| ())
            }
            _ => self.navigate(SELECTED_URL).await,
        }
    }

    // A recommendation feed reached by a full load can stall. Without asking the
    // user, go back to /jingxuan and switch in-app again, until the deadline.
    async fn recover_stalled_recommendation(&mut self) -> Result<()> {
        let grace = if self.feed_videos == 0 {
            STALLED_FEED_SECS
        } else {
            STALLED_UIFID_SECS
        };
        if self
            .route_action_at
            .is_some_and(|at| at.elapsed().as_secs() < grace)
        {
            return Ok(());
        }
        self.recovery_attempts = self.recovery_attempts.saturating_add(1);
        tracing::info!(
            attempt = self.recovery_attempts,
            videos = self.feed_videos,
            "quick-auth recommendation feed stalled without UIFID; returning to selected feed"
        );
        self.navigate(SELECTED_URL).await
    }

    // Covered windows stay live via launch flags; a minimized window or a
    // background tab still freezes, so bring this page back without asking.
    async fn restore_hidden_window(&mut self) -> Result<()> {
        if self
            .restored_at
            .is_some_and(|at| at.elapsed().as_secs() < RESTORE_INTERVAL_SECS)
        {
            return Ok(());
        }
        self.restored_at = Some(tokio::time::Instant::now());
        self.restore_attempts = self.restore_attempts.saturating_add(1);
        tracing::info!(
            attempt = self.restore_attempts,
            "quick-auth main site page was hidden; restoring its window"
        );
        let window = self
            .command("Browser.getWindowForTarget", json!({}))
            .await?;
        let window_id = window["windowId"]
            .as_i64()
            .context("登录窗口状态读取失败")?;
        self.command(
            "Browser.setWindowBounds",
            json!({"windowId":window_id,"bounds":{"windowState":"normal"}}),
        )
        .await?;
        self.command("Page.bringToFront", json!({})).await?;
        // The frozen time must not count as a stalled feed.
        self.route_action_at = Some(tokio::time::Instant::now());
        Ok(())
    }

    async fn evaluate_status(&mut self, expression: &str) -> Result<Value> {
        let result = self
            .command(
                "Runtime.evaluate",
                json!({"expression":expression,"returnByValue":true}),
            )
            .await?;
        anyhow::ensure!(
            result.get("exceptionDetails").is_none(),
            "主站推荐入口读取失败，正在自动重试"
        );
        result
            .pointer("/result/value")
            .cloned()
            .context("主站推荐入口未返回状态")
    }

    async fn open_main_page(&mut self) -> Result<Self> {
        let created = self
            .command(
                "Target.createTarget",
                json!({"url":"about:blank","background":false}),
            )
            .await?;
        let target = created["targetId"]
            .as_str()
            .filter(|s| {
                !s.is_empty()
                    && s.len() <= 128
                    && s.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
            })
            .context("主站页面创建失败")?;
        let endpoint = format!("{}/devtools/page/{target}", self.endpoint_base);
        let result = async {
            let mut main = tokio::time::timeout(COMMAND_TIMEOUT, Self::connect(&endpoint))
                .await
                .context("主站连接超时")??;
            main.captured.main_site = true;
            main.initialize_at(SELECTED_URL).await?;
            Ok::<Self, anyhow::Error>(main)
        }
        .await;
        if result.is_err() {
            let _ = self
                .command("Target.closeTarget", json!({"targetId":target}))
                .await;
        }
        result
    }

    /// Requests graceful shutdown of the exact browser endpoint.
    /// # Errors
    /// Propagates a bounded CDP transport failure; the caller may still stop its owned child.
    pub async fn close(&mut self) -> Result<()> {
        self.command("Browser.close", json!({})).await.map(|_| ())
    }

    pub fn poll_delay(&self) -> std::time::Duration {
        // Poll local state more responsively during initial main-site loading,
        // not remote identity/verification APIs. Back off if the site stalls.
        if self
            .main_started
            .is_some_and(|t| t.elapsed().as_secs() < 30)
        {
            std::time::Duration::from_millis(500)
        } else {
            std::time::Duration::from_secs(2)
        }
    }

    #[cfg(test)]
    pub(super) fn sample_observation(&self) -> (usize, bool, &'static str, u32) {
        let route = self.main_page.as_ref().map_or("not_open", |main| {
            if main.current_path.starts_with("/jingxuan") {
                "selected"
            } else if main.current_path == "/" {
                "main_root"
            } else {
                "other"
            }
        });
        (
            self.captured.contexts.len(),
            !self.captured.dtrait_blob.is_empty(),
            route,
            self.main_page
                .as_ref()
                .map_or(0, |main| main.recommendation_attempts),
        )
    }

    pub fn is_closed(&self) -> bool {
        self.closed
    }

    /// Main-site time and automatic actions (route switches, stall recoveries,
    /// window restores) spent before the capture became ready.
    pub fn main_site_effort(&self) -> (u64, u32) {
        let elapsed = self.main_started.map_or(0, |at| {
            u64::try_from(at.elapsed().as_millis()).unwrap_or(u64::MAX)
        });
        let actions = self.main_page.as_ref().map_or(0, |main| {
            main.recommendation_attempts
                .saturating_add(main.recovery_attempts)
                .saturating_add(main.restore_attempts)
        });
        (elapsed, actions)
    }

    pub fn progress_message(&self, completeness: &Completeness) -> String {
        let Some(started) = self.main_started else {
            return "登录已识别，正在读取创作者账号资料".into();
        };
        let seconds = started.elapsed().as_secs();
        if let Some(main) = self
            .main_page
            .as_ref()
            .filter(|_| completeness.0.get("main_uifid") == Some(&false))
        {
            if main.page_hidden {
                return "登录窗口被最小化或切到后台，正在自动恢复并继续采集，无需手动操作".into();
            }
            if main.current_path.starts_with("/jingxuan") && main.recommendation_attempts > 0 {
                return format!(
                    "主站停留在精选页，正在自动切换到推荐页（已尝试 {} 次），无需手动操作",
                    main.recommendation_attempts
                );
            }
            if main.recovery_attempts > 0 {
                return format!(
                    "推荐页加载停滞，已自动刷新或重新切换 {} 次，正在等待主站UIFID（{seconds} 秒），无需手动操作",
                    main.recovery_attempts
                );
            }
        }
        let missing = completeness.missing_fields();
        if seconds >= 30 {
            let uifid_hint = if completeness.0.get("main_uifid") == Some(&false) {
                "临时 UIFID 不替代正式 UIFID；"
            } else {
                ""
            };
            format!("主站初始化已等待 {seconds} 秒，仍缺少：{missing}。请查看登录窗口是否有验证或加载错误；{uifid_hint}未完成前不会导入")
        } else {
            format!("正在读取主站信息（{seconds} 秒），等待：{missing}")
        }
    }

    async fn command(&mut self, method: &str, params: Value) -> Result<Value> {
        let [result] = self.commands([(method, params)]).await?;
        Ok(result)
    }

    async fn navigate(&mut self, url: &str) -> Result<()> {
        self.route_action_at = Some(tokio::time::Instant::now());
        self.switch_step = 0;
        let result = self.command("Page.navigate", json!({"url":url})).await?;
        anyhow::ensure!(
            result["errorText"].as_str().is_none_or(str::is_empty),
            "登录页面导航失败，请检查网络后重试"
        );
        Ok(())
    }

    async fn commands<const N: usize>(
        &mut self,
        commands: [(&str, Value); N],
    ) -> Result<[Value; N]> {
        let response = tokio::time::timeout(COMMAND_TIMEOUT, async {
            let mut ids = [0; N];
            for (index, (method, params)) in commands.into_iter().enumerate() {
                self.send_only(method, params).await?;
                ids[index] = self.next_id;
            }
            let mut results: [Value; N] = std::array::from_fn(|_| Value::Null);
            let mut received = [false; N];
            while received.contains(&false) {
                let Some(Ok(message)) = self.socket.next().await else {
                    self.closed = true;
                    anyhow::bail!("CDP连接已关闭");
                };
                let text = match message {
                    Message::Text(text) => text,
                    Message::Ping(value) => {
                        self.socket.send(Message::Pong(value)).await?;
                        continue;
                    }
                    Message::Close(_) => {
                        self.closed = true;
                        anyhow::bail!("CDP连接已关闭");
                    }
                    _ => continue,
                };
                anyhow::ensure!(text.len() <= MAX_CDP_MESSAGE, "CDP响应过大");
                let value: Value = serde_json::from_str(&text)?;
                if value.get("method").is_some() {
                    observe_event(&mut self.captured, &value);
                    continue;
                }
                let Some(index) = ids.iter().position(|id| value["id"].as_u64() == Some(*id))
                else {
                    continue;
                };
                if received[index] {
                    continue;
                }
                if value.get("error").is_some() {
                    anyhow::bail!("浏览器采集指令执行失败");
                }
                results[index] = value["result"].clone();
                received[index] = true;
            }
            Ok::<_, anyhow::Error>(results)
        })
        .await
        .context("浏览器采集响应超时")??;
        Ok(response)
    }

    async fn send_only(&mut self, method: &str, params: Value) -> Result<()> {
        self.next_id = self.next_id.checked_add(1).context("CDP指令序号溢出")?;
        let sent = self
            .socket
            .send(Message::Text(
                json!({"id":self.next_id,"method":method,"params":params})
                    .to_string()
                    .into(),
            ))
            .await;
        if sent.is_err() {
            self.closed = true;
            anyhow::bail!("CDP发送失败，连接已关闭");
        }
        Ok(())
    }
}

fn validate_ws(raw: &str) -> Result<()> {
    let uri: wreq::Uri = raw.parse()?;
    anyhow::ensure!(
        uri.scheme_str() == Some("ws") && matches!(uri.host(), Some("127.0.0.1" | "localhost")),
        "CDP只允许本机连接"
    );
    Ok(())
}

// Only samples from default-world execution contexts belonging to this owned
// page and explicitly allowed login origins may supplement top-frame readings.
fn observe_sample(capture: &mut NetworkCapture, event: &Value) -> bool {
    let params = &event["params"];
    match event["method"].as_str().unwrap_or_default() {
        "Runtime.executionContextCreated" => {
            let context = &params["context"];
            if let (Some(id), Some(origin)) = (context["id"].as_i64(), context["origin"].as_str()) {
                if context.pointer("/auxData/isDefault") == Some(&Value::Bool(true))
                    && matches!(
                        origin,
                        "https://creator.douyin.com"
                            | "https://www.douyin.com"
                            | "https://passport.douyin.com"
                    )
                {
                    if capture.contexts.len() >= 128 {
                        capture.contexts.clear();
                    }
                    capture.contexts.insert(id, origin.into());
                }
            }
        }
        "Runtime.executionContextDestroyed" => {
            if let Some(id) = params["executionContextId"].as_i64() {
                capture.contexts.remove(&id);
            }
        }
        "Runtime.executionContextsCleared" => {
            capture.contexts.clear();
        }
        "Runtime.bindingCalled" => {
            if params["name"] != "__dyauthreplyCaptureSample"
                || !params["executionContextId"]
                    .as_i64()
                    .is_some_and(|id| capture.contexts.contains_key(&id))
            {
                return true;
            }
            if let Some(raw) = params["payload"].as_str().filter(|s| s.len() <= 128 * 1024) {
                if let Ok(sample) = serde_json::from_str::<Value>(raw) {
                    let blob = bounded_text(&sample, "blob", 65_536);
                    let path = bounded_text(&sample, "path", 512);
                    if blob.len() > 20 && path.starts_with('/') && !path.starts_with("//") {
                        capture.dtrait_blob = blob.into();
                        capture.dtrait_blob_path = path.into();
                    }
                }
            }
        }
        _ => return false,
    }
    true
}

fn observe_event(capture: &mut NetworkCapture, event: &Value) {
    if observe_sample(capture, event) {
        return;
    }
    let method = event["method"].as_str().unwrap_or_default();
    let params = &event["params"];
    if method == "Network.requestWillBeSent" {
        if let (Some(id), Some(url)) = (
            params["requestId"].as_str(),
            params.pointer("/request/url").and_then(Value::as_str),
        ) {
            if url.len() <= 16_384 && id.len() <= 256 {
                if capture.request_urls.len() >= 256 {
                    capture.request_urls.clear();
                }
                capture.request_urls.insert(id.into(), url.into());
                if capture.main_site {
                    if let Some(token) = token_from_url(url) {
                        capture.query_ms_token = token;
                    }
                }
            }
        }
        return;
    }
    let url = params["requestId"]
        .as_str()
        .and_then(|id| capture.request_urls.get(id))
        .map(String::as_str)
        .or_else(|| params.pointer("/response/url").and_then(Value::as_str))
        .unwrap_or("");
    let host = url
        .parse::<wreq::Uri>()
        .ok()
        .filter(|u| u.scheme_str() == Some("https"))
        .and_then(|u| u.host().map(str::to_owned))
        .unwrap_or_default();
    let expected = if capture.main_site {
        "www.douyin.com"
    } else {
        "creator.douyin.com"
    };
    if host != expected {
        return;
    }
    let Some(headers) = params
        .get("headers")
        .or_else(|| params.pointer("/response/headers"))
        .and_then(Value::as_object)
    else {
        return;
    };
    if method == "Network.responseReceivedExtraInfo" || method == "Network.responseReceived" {
        if let Some(value) = header(headers, "bd-ticket-guard-server-data", 64 * 1024) {
            capture.server_data = value;
        }
        if capture.main_site {
            if let Some(value) = header(headers, "x-ms-token", 8192)
                .filter(|v| crate::protocol::credentials::valid_query_token(v))
            {
                capture.query_ms_token = value;
            }
        }
    }
    if method == "Network.requestWillBeSentExtraInfo" {
        if let Some(value) = header(headers, "x-tt-session-dtrait", 128 * 1024) {
            capture.dtrait_path = url_path(url).unwrap_or_default();
            capture.dtrait_header = value;
        }
    }
}

fn token_from_url(raw: &str) -> Option<String> {
    let uri: wreq::Uri = raw.parse().ok()?;
    if uri.scheme_str() != Some("https")
        || !matches!(uri.host(), Some("www.douyin.com" | "imapi.douyin.com"))
    {
        return None;
    }
    let value = uri
        .query()?
        .split('&')
        .filter_map(|p| p.split_once('='))
        .find(|(k, _)| *k == "msToken")?
        .1;
    let bytes = value.as_bytes();
    let mut decoded = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            if i + 2 >= bytes.len() {
                return None;
            }
            decoded.push(
                u8::from_str_radix(std::str::from_utf8(&bytes[i + 1..i + 3]).ok()?, 16).ok()?,
            );
            i += 3;
        } else {
            decoded.push(bytes[i]);
            i += 1;
        }
    }
    let token = String::from_utf8(decoded).ok()?;
    crate::protocol::credentials::valid_query_token(&token).then_some(token)
}

fn waiting_capture(logged_in: bool) -> Capture {
    Capture {
        completeness: Completeness::new([logged_in, false, false, false, false, false])
            .with_main(false, false, false),
        candidate: None,
    }
}

fn header(map: &serde_json::Map<String, Value>, name: &str, limit: usize) -> Option<String> {
    map.iter().find_map(|(key, value)| {
        let value = value.as_str()?;
        (key.eq_ignore_ascii_case(name)
            && !value.is_empty()
            && value.len() <= limit
            && !value.chars().any(char::is_control))
        .then(|| value.to_owned())
    })
}

fn url_path(raw: &str) -> Option<String> {
    let uri: wreq::Uri = raw.parse().ok()?;
    let path = uri.path();
    (path.len() <= 512).then(|| path.to_owned())
}

fn build_candidate(
    storage: &Value,
    www: &Value,
    creator: &Value,
    imapi: &Value,
    page: &Value,
    captured: &NetworkCapture,
) -> Result<Capture> {
    let www_header = cookie_header(www)?;
    let creator_header = cookie_header(creator)?;
    let imapi_header = cookie_header(imapi)?;
    let session = cookie_value(www, "sessionid")
        .or_else(|| cookie_value(www, "sessionid_ss"))
        .unwrap_or_default();
    if session.is_empty() {
        return Ok(Capture {
            completeness: Completeness::default(),
            candidate: None,
        });
    }
    let server_data = captured_server_data(storage, captured);
    let keys = bounded_text(page, "keys", 256 * 1024);
    let web_protect = bounded_text(page, "web_protect", 256 * 1024);
    let user_agent = bounded_text(page, "ua", 2048);
    let dtrait_blob = bounded_text(page, "dtrait_blob", 65_536);
    let page_dtrait_path = bounded_text(page, "dtrait_path", 512);
    let account = page.get("account").filter(|value| value.is_object());
    let sec_uid = account.map_or("", |value| bounded(value, "sec_uid", 256));
    let nickname = account.map_or("", |value| bounded(value, "nickname", 512));
    let unique_id = account.map_or("", |value| bounded(value, "unique_id", 256));
    let avatar = account.map_or("", |value| bounded(value, "avatar", 4096));
    let private_key = has_private_key(keys)
        || page["key_candidates"]
            .as_array()
            .into_iter()
            .flatten()
            .take(4)
            .filter_map(Value::as_str)
            .any(has_private_key);
    let scoped = [&www_header, &creator_header, &imapi_header]
        .iter()
        .all(|v| !v.is_empty())
        && same_session(creator, &session)
        && same_session(imapi, &session);
    let sdk_token = captured_sdk_token(www, captured);
    let uifid = has_main_uifid(www);
    let fingerprint = cookie_value(www, "s_v_web_id").is_some_and(|s| !s.is_empty());
    // A whole dtrait header is encrypted for one path and timestamp. Accepting
    // a QR/check header as send-ready caused message/send to be rejected as
    // risk-controlled. Require the captured trait blob so native requests can
    // rebuild the header for their current path.
    let dtrait = !dtrait_blob.is_empty();
    let identity = !sec_uid.is_empty() && !nickname.is_empty();
    let completeness = Completeness::new([
        true,
        scoped,
        !server_data.is_empty() || !web_protect.is_empty(),
        private_key,
        dtrait,
        identity && !user_agent.is_empty(),
    ])
    .with_main(
        uifid,
        fingerprint,
        crate::protocol::credentials::valid_query_token(&sdk_token),
    );
    if !completeness.ready() {
        return Ok(Capture {
            completeness,
            candidate: None,
        });
    }
    let mut payload = json!({
        "capture_version":2,
        "query_ms_token":sdk_token,
        "query_ms_token_captured_at_ms":super::now_ms()?,
        "browser_profile":page["browser_profile"],
        "cookie":www_header,
        "cookie_headers":{
            "www.douyin.com":www_header,
            "creator.douyin.com":creator_header,
            "imapi.douyin.com":imapi_header,
        },
        "ticket_guard_server_data":server_data,
        "web_protect":web_protect,
        "keys":keys,
        "ua":user_agent,
        "sec_uid":sec_uid,
        "nickname":nickname,
        "unique_id":unique_id,
        "dtrait_blob":dtrait_blob,
        "session_dtrait":captured.dtrait_header,
        "session_dtrait_path":if page_dtrait_path.is_empty(){&captured.dtrait_path}else{page_dtrait_path},
    });
    let bundle = matching_bundle(&mut payload, page, keys, server_data, web_protect)?;
    Ok(Capture {
        completeness: completeness.clone(),
        candidate: Some(Candidate {
            bundle,
            identity: CandidateIdentity {
                sec_uid: sec_uid.into(),
                nickname: nickname.into(),
                unique_id: unique_id.into(),
                avatar: avatar.into(),
            },
            completeness,
        }),
    })
}

// A shared-session Cookie can carry the creator SDK's current ticket while
// www localStorage still contains an older ticket. Pair by signature/certificate,
// never by merely choosing the most recently visited origin.
fn merge_session_material(page: &mut Value, creator: &Value) {
    for (list, single) in [
        ("key_candidates", "keys"),
        ("ticket_candidates", "web_protect"),
    ] {
        let mut values = page[list].as_array().cloned().unwrap_or_default();
        values.push(page[single].clone());
        values.extend(creator[list].as_array().into_iter().flatten().cloned());
        values.push(creator[single].clone());
        if list == "ticket_candidates" {
            values.push(creator["captured_ticket"].clone());
        }
        values.retain(|v| v.as_str().is_some_and(|s| !s.is_empty()));
        values.dedup();
        page[list] = Value::Array(values);
    }
    if bounded_text(page, "dtrait_blob", 65_536).is_empty() {
        page["dtrait_blob"] = creator["dtrait_blob"].clone();
        page["dtrait_path"] = creator["dtrait_path"].clone();
    }
    if bounded_text(page, "web_protect", 256 * 1024).is_empty() {
        page["web_protect"] = creator["web_protect"].clone();
    }
}

fn captured_sdk_token(www: &Value, captured: &NetworkCapture) -> String {
    if crate::protocol::credentials::valid_query_token(&captured.query_ms_token) {
        captured.query_ms_token.clone()
    } else {
        cookie_value(www, "msToken")
            .or_else(|| cookie_value(www, "msToken_ss"))
            .unwrap_or_default()
    }
}

fn same_session(cookies: &Value, session: &str) -> bool {
    cookie_value(cookies, "sessionid")
        .or_else(|| cookie_value(cookies, "sessionid_ss"))
        .as_deref()
        == Some(session)
}

fn captured_server_data<'a>(storage: &'a Value, captured: &'a NetworkCapture) -> &'a str {
    let cookie_server = storage["cookies"]
        .as_array()
        .into_iter()
        .flatten()
        .find(|cookie| {
            cookie["name"].as_str() == Some("bd_ticket_guard_server_data")
                && cookie["domain"].as_str().is_some_and(valid_douyin_domain)
        })
        .and_then(|cookie| cookie["value"].as_str())
        .unwrap_or_default();
    if captured.server_data.is_empty() {
        cookie_server
    } else {
        &captured.server_data
    }
}

fn matching_bundle(
    payload: &mut Value,
    page: &Value,
    keys: &str,
    server_data: &str,
    web_protect: &str,
) -> Result<String> {
    // Match the main-site SDK key to its own ticket; never mix creator keys.
    let key_candidates: Vec<&str> = page["key_candidates"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .chain(std::iter::once(keys))
        .take(4)
        .collect();
    let ticket_candidates: Vec<&str> = std::iter::once(server_data)
        .chain(
            page["ticket_candidates"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(Value::as_str),
        )
        .chain(std::iter::once(web_protect))
        .filter(|s| !s.is_empty())
        .take(8)
        .collect();
    let mut selected = None;
    'search: for key in key_candidates {
        for ticket in &ticket_candidates {
            payload["keys"] = json!(key);
            payload["ticket_guard_server_data"] = json!(ticket);
            let bytes = serde_json::to_vec(payload)?;
            anyhow::ensure!(
                bytes.len() <= crate::protocol::credentials::MAX_CREDENTIAL_BYTES,
                "采集凭证过大"
            );
            let bundle = format!("DYCRED1.{}", URL_SAFE_NO_PAD.encode(bytes));
            if crate::protocol::credentials::AccountCredentials::import_extension(
                "quick-auth-candidate".into(),
                &bundle,
            )
            .is_ok()
            {
                selected = Some(bundle);
                break 'search;
            }
        }
    }
    selected.context("主站票据、Cookie和签名密钥尚未配套，请等待主站初始化完成")
}

fn cookie_header(value: &Value) -> Result<String> {
    let cookies = value["cookies"]
        .as_array()
        .context("浏览器Cookie响应无效")?;
    anyhow::ensure!(cookies.len() <= 512, "浏览器Cookie数量超限");
    let mut unique = BTreeMap::new();
    for cookie in cookies {
        let name = cookie["name"]
            .as_str()
            .context("浏览器Cookie名称格式无效")?;
        let value = cookie["value"].as_str().context("浏览器Cookie值格式无效")?;
        // Chromium can store unrelated nameless cookies (for example a bare
        // browser marker). Match the extension's cookie?.name filter: omit only
        // that entry, without editing browser storage or any named credential.
        if name.is_empty() {
            continue;
        }
        if name.len() > 256
            || value.len() > 32_768
            || name.chars().any(char::is_control)
            || value.chars().any(char::is_control)
        {
            anyhow::bail!("浏览器Cookie字段无效");
        }
        if matches!(name, "sessionid" | "sessionid_ss" | "sid_tt") {
            anyhow::ensure!(
                unique.get(name).is_none_or(|previous| *previous == value),
                "浏览器存在冲突的登录Cookie"
            );
        }
        unique.insert(name, value);
    }
    Ok(unique
        .into_iter()
        .map(|(name, value)| format!("{name}={value}"))
        .collect::<Vec<_>>()
        .join("; "))
}

fn recommendation_click_point(value: &Value) -> Result<(f64, f64)> {
    let coordinate = |key: &str| {
        value[key]
            .as_f64()
            .filter(|v| v.is_finite() && (0.0..=32_768.0).contains(v))
            .context("主站推荐入口位置无效")
    };
    Ok((coordinate("x")?, coordinate("y")?))
}

fn has_main_uifid(www: &Value) -> bool {
    cookie_value(www, "UIFID").is_some_and(|value| !value.is_empty())
}

fn cookie_value(value: &Value, name: &str) -> Option<String> {
    value["cookies"]
        .as_array()?
        .iter()
        .find(|cookie| cookie["name"].as_str() == Some(name))
        .and_then(|cookie| cookie["value"].as_str())
        .map(str::to_owned)
}

fn valid_douyin_domain(raw: &str) -> bool {
    let domain = raw.trim_start_matches('.');
    domain == "douyin.com" || domain.ends_with(".douyin.com")
}

fn bounded_text<'a>(value: &'a Value, field: &str, limit: usize) -> &'a str {
    bounded(value, field, limit)
}

fn bounded<'a>(value: &'a Value, field: &str, limit: usize) -> &'a str {
    value[field]
        .as_str()
        .filter(|text| text.len() <= limit && !text.chars().any(char::is_control))
        .unwrap_or_default()
}

fn has_private_key(raw: &str) -> bool {
    let Ok(mut value) = serde_json::from_str::<Value>(raw) else {
        return false;
    };
    for _ in 0..2 {
        let Some(text) = value.get("data").and_then(Value::as_str) else {
            break;
        };
        let Ok(parsed) = serde_json::from_str::<Value>(text) else {
            break;
        };
        value = parsed;
    }
    value
        .get("ec_privateKey")
        .or_else(|| value.get("private_key"))
        .and_then(Value::as_str)
        .is_some_and(|key| !key.is_empty() && key.len() <= 16_384)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::net::TcpListener;
    use tokio_tungstenite::accept_async;

    fn cookies() -> Value {
        json!({"cookies":[
            {"name":"","value":"synthetic-browser-marker","domain":"www.douyin.com"},
            {"name":"sessionid","value":"session-one","domain":".douyin.com"},
            {"name":"msToken","value":"S".repeat(164),"domain":".douyin.com"},
            {"name":"UIFID","value":"synthetic-uifid","domain":".douyin.com"},
            {"name":"s_v_web_id","value":"verify_synthetic","domain":"www.douyin.com"},
            {"name":"bd_ticket_guard_ts_sign_id","value":"ts.1","domain":".douyin.com"}
        ]})
    }

    #[test]
    fn frame_samples_require_a_live_default_context_on_an_allowed_origin() {
        let mut capture = NetworkCapture::default();
        let context = |id, origin, default| json!({"method":"Runtime.executionContextCreated","params":{"context":{"id":id,"origin":origin,"auxData":{"isDefault":default}}}});
        let binding = |id, blob: &str, path: &str| json!({"method":"Runtime.bindingCalled","params":{"name":"__dyauthreplyCaptureSample","executionContextId":id,"payload":json!({"blob":blob,"path":path}).to_string()}});
        let blob = "synthetic-device-sample-abcdefghijklmnopqrstuvwxyz";
        for (id, origin, default) in [
            (1, "https://foreign.example", true),
            (2, "https://passport.douyin.com", false),
        ] {
            observe_event(&mut capture, &context(id, origin, default));
            observe_event(&mut capture, &binding(id, blob, "/passport/example/"));
            assert!(capture.dtrait_blob.is_empty());
        }
        observe_event(
            &mut capture,
            &context(3, "https://passport.douyin.com", true),
        );
        for (value, path) in [("short", "/example"), (blob, "//foreign.example/")] {
            observe_event(&mut capture, &binding(3, value, path));
            assert!(capture.dtrait_blob.is_empty());
        }
        observe_event(&mut capture, &binding(3, blob, "/passport/example/"));
        assert_eq!(capture.dtrait_blob, blob);
        observe_event(
            &mut capture,
            &json!({"method":"Runtime.executionContextDestroyed","params":{"executionContextId":3}}),
        );
        observe_event(
            &mut capture,
            &binding(
                3,
                "another-device-sample-abcdefghijklmnopqrstuvwxyz",
                "/different",
            ),
        );
        assert_eq!(capture.dtrait_blob, blob);
        observe_event(&mut capture, &context(4, "https://www.douyin.com", true));
        observe_event(
            &mut capture,
            &json!({"method":"Runtime.executionContextsCleared","params":{}}),
        );
        assert!(capture.contexts.is_empty());
    }

    #[tokio::test]
    async fn navigation_failure_does_not_advance_or_erase_creator_material() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            while let Some(Ok(Message::Text(raw))) = socket.next().await {
                let request: Value = serde_json::from_str(&raw).unwrap();
                let result = match request["method"].as_str().unwrap() {
                    "Runtime.evaluate" => {
                        json!({"result":{"value":{"origin":"creator.douyin.com","account":{"sec_uid":"self","nickname":"SAMPLE"}}}})
                    }
                    "Page.navigate" => json!({"errorText":"synthetic-private-error"}),
                    _ => cookies(),
                };
                if socket
                    .send(Message::Text(
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
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        client.captured.server_data = "synthetic-creator-ticket".into();
        assert!(client.initialize().await.is_err());
        assert!(client.collect().await.is_err());
        assert!(client.creator_identity.is_none());
        assert!(!client.captured.main_site);
        assert_eq!(client.captured.server_data, "synthetic-creator-ticket");
        peer.abort();
    }

    #[tokio::test(start_paused = true)]
    async fn a_stalled_cdp_batch_has_one_bounded_deadline() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let _socket = accept_async(stream).await.unwrap();
            std::future::pending::<()>().await;
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        let started = tokio::time::Instant::now();
        let result = client
            .commands([("Test.read", json!({})), ("Test.second", json!({}))])
            .await;
        assert!(result.is_err());
        assert_eq!(started.elapsed(), COMMAND_TIMEOUT);
        peer.abort();
    }

    #[tokio::test]
    async fn peer_closure_is_terminal_instead_of_repeated_polling() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            let _ = socket.next().await;
            let _ = socket.close(None).await;
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        assert!(client.command("Test.read", json!({})).await.is_err());
        assert!(client.is_closed());
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn page_evaluation_exception_is_reported_instead_of_silent_waiting() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            while let Some(Ok(Message::Text(text))) = socket.next().await {
                let request: Value = serde_json::from_str(&text).unwrap();
                let result = if request["method"] == "Runtime.evaluate" {
                    json!({"exceptionDetails":{"text":"synthetic-private-page-error"}})
                } else {
                    cookies()
                };
                if socket
                    .send(Message::Text(
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
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        let result = client.collect().await;
        peer.abort();
        assert!(
            result.is_err(),
            "page JS failures must not look like missing UIFID"
        );
        assert!(!result
            .err()
            .unwrap()
            .to_string()
            .contains("synthetic-private-page-error"));
    }

    #[tokio::test]
    async fn collection_pipelines_five_reads_before_waiting_for_responses() {
        // A CDP peer releases results only once the whole read batch arrives.
        // This deterministically detects serial round trips without timing a real site.
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            let mut requests = Vec::new();
            for _ in 0..5 {
                let Some(Ok(Message::Text(text))) = socket.next().await else {
                    return;
                };
                requests.push(serde_json::from_str::<Value>(&text).unwrap());
            }
            // Events and responses may arrive interleaved and out of order.
            socket.send(Message::Text(json!({"method":"Network.requestWillBeSent","params":{
                "requestId":"synthetic","request":{"url":"https://www.douyin.com/path?msToken=SSSSSSSSSSSSSSSSSSSSSSSSSSSSSSSS"}
            }}).to_string().into())).await.unwrap();
            for request in requests.into_iter().rev() {
                let result = if request["method"] == "Runtime.evaluate" {
                    json!({"result":{"value":{"origin":"www.douyin.com"}}})
                } else {
                    cookies()
                };
                socket
                    .send(Message::Text(
                        json!({"id":request["id"],"result":result})
                            .to_string()
                            .into(),
                    ))
                    .await
                    .unwrap();
            }
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        client.creator_identity = Some(json!({"sec_uid":"synthetic","nickname":"SAMPLE"}));
        client.creator_session = Some("session-one".into());
        client.captured.main_site = true;
        let result =
            tokio::time::timeout(std::time::Duration::from_secs(1), client.snapshot(false)).await;
        task.abort();
        let capture = result
            .expect("five reads should be in flight together")
            .unwrap();
        assert!(cookie_value(&capture[1], "sessionid").is_some());
        assert_eq!(capture[4]["origin"], "www.douyin.com");
        assert_eq!(client.captured.query_ms_token, "S".repeat(32));
    }

    async fn serve_capture_peer(stream: TcpStream, private: String, server_data: String) {
        let mut host = "about:blank";
        let mut reads = 0;
        let mut socket = accept_async(stream).await.unwrap();
        while let Some(Ok(Message::Text(text))) = socket.next().await {
            let request: Value = serde_json::from_str(&text).unwrap();
            let method = request["method"].as_str().unwrap();
            if method == "Page.navigate" {
                let url = request["params"]["url"].as_str().unwrap();
                host = if url.starts_with("https://www.douyin.com/") {
                    assert_eq!(url, SELECTED_URL);
                    "www.douyin.com"
                } else {
                    assert_eq!(
                        url,
                        "https://creator.douyin.com/creator-micro/data/following/chat"
                    );
                    "creator.douyin.com"
                };
            }
            let result = match method {
                "Target.createTarget" => json!({"targetId":"synthetic-main"}),
                "Storage.getCookies" => json!({"cookies":[
                    {"name":"bd_ticket_guard_server_data","value":server_data,"domain":".douyin.com"}
                ]}),
                "Network.getCookies" => cookies(),
                "Runtime.evaluate" => {
                    reads += 1;
                    // Creator sampling deliberately arrives after the first identity
                    // read. Main never supplies it: navigating creator away would stall.
                    let dtrait = if host == "creator.douyin.com" && reads >= 2 {
                        "abcdefghijklmnopqrstuvwxyz"
                    } else {
                        ""
                    };
                    if host == "creator.douyin.com" && reads >= 2 {
                        assert!(
                            request["params"]["expression"]
                                .as_str()
                                .unwrap()
                                .ends_with("(false)"),
                            "do not re-fetch remote identity every poll"
                        );
                    }
                    json!({"result":{"value":{
                        "origin":host,"keys":json!({"ec_privateKey":private}).to_string(),"web_protect":"","ua":"Chrome/140",
                        "dtrait_blob":dtrait,"dtrait_path":"/identity",
                        "account":{"sec_uid":"scope-one","nickname":"测试账号","unique_id":"douyin-one","avatar":"https://example.com/a.png"}
                    }}})
                }
                _ => json!({}),
            };
            if socket
                .send(Message::Text(
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
    }

    #[tokio::test]
    async fn fake_cdp_peer_produces_complete_bounded_candidate() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let signing: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/native_signing.json")).unwrap();
        let private = signing["private_key"].as_str().unwrap().to_owned();
        let cert = format!(
            "pub.{}",
            crate::protocol::native_signer::ree_public_key(&private).unwrap()
        );
        let server_data =
            json!({"ticket":"one","ts_sign":"ts.1.ok","client_cert":cert}).to_string();
        let task = tokio::spawn(async move {
            let mut peers = tokio::task::JoinSet::new();
            for _ in 0..2 {
                let (stream, _) = listener.accept().await.unwrap();
                peers.spawn(serve_capture_peer(
                    stream,
                    private.clone(),
                    server_data.clone(),
                ));
            }
            while let Some(result) = peers.join_next().await {
                result.unwrap();
            }
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/one"))
            .await
            .unwrap();
        client.initialize().await.unwrap();
        assert!(client.collect().await.unwrap().candidate.is_none());
        let candidate = client.collect().await.unwrap().candidate.unwrap();
        assert!(candidate.completeness.ready());
        assert_eq!(candidate.identity.nickname, "测试账号");
        assert!(candidate.bundle.starts_with("DYCRED1."));
        drop(client);
        task.await.unwrap();
    }

    #[test]
    fn unrelated_static_dtrait_header_is_not_send_ready() {
        let storage = json!({"cookies":[
            {"name":"sessionid","value":"session-one","domain":".douyin.com"},
            {"name":"bd_ticket_guard_server_data","value":"server","domain":".douyin.com"}
        ]});
        let scoped = json!({"cookies":[{"name":"sessionid","value":"session-one"}]});
        let page = json!({
            "keys":"{\"ec_privateKey\":\"private\"}",
            "ua":"Chrome/152.0",
            "account":{"sec_uid":"scope","nickname":"name"}
        });
        let captured = NetworkCapture {
            server_data: "server".to_owned(),
            dtrait_header: "d0_qr_only".to_owned(),
            dtrait_path: "/passport/web/check_qrconnect/".to_owned(),
            request_urls: HashMap::new(),
            ..NetworkCapture::default()
        };
        let result =
            build_candidate(&storage, &scoped, &scoped, &scoped, &page, &captured).unwrap();
        assert!(!result.completeness.ready());
        assert!(result.candidate.is_none());
    }

    // Serves one scripted recommendation attempt per round: bring the page to
    // front, report the link status, then expect the chosen switching action.
    async fn serve_recommendation_rounds(
        listener: TcpListener,
        rounds: Vec<(&'static str, &'static str)>,
    ) {
        let (stream, _) = listener.accept().await.unwrap();
        let mut socket = accept_async(stream).await.unwrap();
        for (status, action) in rounds {
            let methods: &[&str] = match action {
                "mouse" => &["Input.dispatchMouseEvent"; 3],
                "link" => &["Runtime.evaluate"],
                _ => &["Page.navigate"],
            };
            for (index, method) in ["Page.bringToFront", "Runtime.evaluate"]
                .iter()
                .chain(methods)
                .enumerate()
            {
                let message = socket.next().await.unwrap().unwrap();
                let value: Value = serde_json::from_str(message.to_text().unwrap()).unwrap();
                assert_eq!(value["method"], *method);
                let response = match (*method, index) {
                    ("Runtime.evaluate", 1) => {
                        assert_eq!(value["params"]["expression"], OPEN_RECOMMENDATION);
                        json!({"result":{"value":{"status":status,"x":70,"y":55}}})
                    }
                    ("Runtime.evaluate", _) => {
                        assert_eq!(value["params"]["expression"], CLICK_RECOMMENDATION);
                        json!({"result":{"value":{"status":"clicked"}}})
                    }
                    ("Input.dispatchMouseEvent", _) => {
                        assert_eq!(value["params"]["x"], 70.0);
                        assert_eq!(value["params"]["y"], 55.0);
                        json!({})
                    }
                    ("Page.navigate", _) => {
                        assert_eq!(value["params"]["url"], SELECTED_URL);
                        json!({"frameId":"synthetic"})
                    }
                    _ => json!({}),
                };
                socket
                    .send(Message::Text(
                        json!({"id":value["id"],"result":response})
                            .to_string()
                            .into(),
                    ))
                    .await
                    .unwrap();
            }
        }
    }

    fn rewind_recommendation(client: &mut CdpClient, seconds: u64) {
        let past = tokio::time::Instant::now() - std::time::Duration::from_secs(seconds);
        client.recommendation_last_attempt = Some(past);
        client.route_action_at = client.route_action_at.map(|_| past);
    }

    #[tokio::test]
    async fn recommendation_click_acknowledgement_is_not_navigation_success() {
        // A real click that does not switch is followed by the link's own handler,
        // then a fresh /jingxuan document. Never a full load of the feed itself.
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(serve_recommendation_rounds(
            listener,
            vec![
                ("target", "mouse"),
                ("target", "link"),
                ("target", "reload"),
                ("target", "mouse"),
            ],
        ));
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        client.open_recommendation_if_redirected().await.unwrap();
        client.open_recommendation_if_redirected().await.unwrap();
        assert_eq!(
            client.recommendation_attempts, 1,
            "immediate retry must back off"
        );
        for expected in 2..=3 {
            rewind_recommendation(&mut client, SELECTED_SETTLE_SECS);
            client.open_recommendation_if_redirected().await.unwrap();
            assert_eq!(client.recommendation_attempts, expected);
        }
        rewind_recommendation(&mut client, 3);
        client.open_recommendation_if_redirected().await.unwrap();
        assert_eq!(
            client.recommendation_attempts, 3,
            "later attempts back off longer"
        );
        rewind_recommendation(&mut client, 10);
        client.open_recommendation_if_redirected().await.unwrap();
        assert_eq!(client.recommendation_attempts, 4);
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn covered_recommendation_link_uses_the_site_handler_then_reloads_selected() {
        // A covered link or hidden window keeps the hit test failing forever.
        // That must not wait without acting or end in a manual-click request.
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(serve_recommendation_rounds(
            listener,
            vec![
                ("not_ready", "link"),
                ("not_ready", "reload"),
                ("not_ready", "link"),
                ("not_ready", "reload"),
            ],
        ));
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        for expected in 1..=4 {
            rewind_recommendation(&mut client, 10);
            client.open_recommendation_if_redirected().await.unwrap();
            assert_eq!(client.recommendation_attempts, expected);
        }
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn a_fresh_selected_document_settles_before_fallbacks() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            for method in ["Page.bringToFront", "Runtime.evaluate"] {
                let message = socket.next().await.unwrap().unwrap();
                let value: Value = serde_json::from_str(message.to_text().unwrap()).unwrap();
                assert_eq!(value["method"], method);
                let response = json!({"result":{"value":{"status":"target","x":70,"y":55}}});
                socket
                    .send(Message::Text(
                        json!({"id":value["id"],"result":response})
                            .to_string()
                            .into(),
                    ))
                    .await
                    .unwrap();
            }
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        client.route_action_at = Some(tokio::time::Instant::now());
        client.open_recommendation_if_redirected().await.unwrap();
        assert_eq!(
            client.recommendation_attempts, 0,
            "a fresh /jingxuan gets time to issue UIFID before any switch"
        );
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn a_hidden_main_page_restores_its_window_without_fighting_the_user() {
        // Minimized or backgrounded pages do not render, so the fingerprint that
        // yields UIFID never runs. Restore once, then back off.
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            for method in [
                "Browser.getWindowForTarget",
                "Browser.setWindowBounds",
                "Page.bringToFront",
            ] {
                let message = socket.next().await.unwrap().unwrap();
                let value: Value = serde_json::from_str(message.to_text().unwrap()).unwrap();
                assert_eq!(value["method"], method);
                let response = match method {
                    "Browser.getWindowForTarget" => json!({"windowId":7,"bounds":{}}),
                    "Browser.setWindowBounds" => {
                        assert_eq!(value["params"]["windowId"], 7);
                        assert_eq!(value["params"]["bounds"]["windowState"], "normal");
                        json!({})
                    }
                    _ => json!({}),
                };
                socket
                    .send(Message::Text(
                        json!({"id":value["id"],"result":response})
                            .to_string()
                            .into(),
                    ))
                    .await
                    .unwrap();
            }
            // Nothing else may arrive while the restore interval is running.
            assert!(
                tokio::time::timeout(std::time::Duration::from_millis(200), socket.next())
                    .await
                    .is_err()
            );
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        client.page_hidden = true;
        client.restore_hidden_window().await.unwrap();
        assert_eq!(client.restore_attempts, 1);
        assert!(
            client.route_action_at.is_some(),
            "frozen time must not count as a stall"
        );
        client.restore_hidden_window().await.unwrap();
        assert_eq!(client.restore_attempts, 1, "do not fight the user");
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn a_stalled_recommendation_feed_recovers_without_the_user() {
        // Observed live: a fully loaded recommendation page stayed on "加载中"
        // with no video. Return to /jingxuan and switch in-app again.
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            for _ in 0..2 {
                let message = socket.next().await.unwrap().unwrap();
                let value: Value = serde_json::from_str(message.to_text().unwrap()).unwrap();
                assert_eq!(value["method"], "Page.navigate");
                assert_eq!(value["params"]["url"], SELECTED_URL);
                socket
                    .send(Message::Text(
                        json!({"id":value["id"],"result":{}}).to_string().into(),
                    ))
                    .await
                    .unwrap();
            }
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        let ago = |s| Some(tokio::time::Instant::now() - std::time::Duration::from_secs(s));
        client.route_action_at = ago(STALLED_FEED_SECS - 1);
        client.recover_stalled_recommendation().await.unwrap();
        assert_eq!(client.recovery_attempts, 0, "a loading feed gets its grace");
        client.route_action_at = ago(STALLED_FEED_SECS);
        client.recover_stalled_recommendation().await.unwrap();
        assert_eq!(client.recovery_attempts, 1);
        client.feed_videos = 2;
        client.route_action_at = ago(STALLED_FEED_SECS);
        client.recover_stalled_recommendation().await.unwrap();
        assert_eq!(client.recovery_attempts, 1, "a playing feed waits longer");
        client.route_action_at = ago(STALLED_UIFID_SECS);
        client.recover_stalled_recommendation().await.unwrap();
        assert_eq!(client.recovery_attempts, 2);
        peer.await.unwrap();
    }

    #[tokio::test]
    async fn recommendation_read_failures_back_off_between_polls() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            for method in ["Page.bringToFront", "Runtime.evaluate"] {
                let message = socket.next().await.unwrap().unwrap();
                let value: Value = serde_json::from_str(message.to_text().unwrap()).unwrap();
                assert_eq!(value["method"], method);
                let response = if method == "Runtime.evaluate" {
                    json!({"exceptionDetails":{"text":"synthetic"}})
                } else {
                    json!({})
                };
                socket
                    .send(Message::Text(
                        json!({"id":value["id"],"result":response})
                            .to_string()
                            .into(),
                    ))
                    .await
                    .unwrap();
            }
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        let error = client
            .open_recommendation_if_redirected()
            .await
            .unwrap_err();
        assert!(!error.to_string().contains("手动"));
        client.open_recommendation_if_redirected().await.unwrap();
        assert_eq!(client.recommendation_attempts, 0);
        peer.await.unwrap();
    }

    #[test]
    fn recommendation_coordinates_are_bounded_and_typed() {
        assert_eq!(
            recommendation_click_point(&json!({"x":70,"y":55})).unwrap(),
            (70.0, 55.0)
        );
        for value in [
            json!({}),
            json!({"x":"70","y":55}),
            json!({"x":-1,"y":55}),
            json!({"x":1,"y":32769}),
        ] {
            assert!(recommendation_click_point(&value).is_err());
        }
    }

    #[test]
    fn empty_main_uifid_requires_bootstrap_and_does_not_pass_completeness() {
        let mut scoped = cookies();
        for cookie in scoped["cookies"].as_array_mut().unwrap() {
            if cookie["name"] == "UIFID" {
                cookie["value"] = json!("");
            }
        }
        assert!(!has_main_uifid(&scoped));
        assert!(has_main_uifid(&cookies()));
        assert!(!has_main_uifid(&json!({"cookies":[]})));
    }

    #[test]
    fn temporary_uifid_does_not_pass_main_site_completeness() {
        let mut scoped = cookies();
        for cookie in scoped["cookies"].as_array_mut().unwrap() {
            if cookie["name"] == "UIFID" {
                cookie["name"] = json!("UIFID_TEMP");
            }
        }
        let capture = build_candidate(
            &cookies(),
            &scoped,
            &scoped,
            &scoped,
            &json!({"ua":"synthetic","account":{"sec_uid":"self","nickname":"SAMPLE"}}),
            &NetworkCapture::default(),
        )
        .unwrap();
        assert!(!capture.completeness.0["main_uifid"]);
        assert!(capture.candidate.is_none());
        assert!(capture.completeness.missing_fields().contains("主站UIFID"));
    }

    #[tokio::test]
    async fn capture_polling_backs_off_and_explains_missing_fields() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            accept_async(stream).await.unwrap()
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        let _socket = peer.await.unwrap();
        assert_eq!(client.poll_delay().as_millis(), 2000);
        client.main_started = Some(tokio::time::Instant::now());
        assert_eq!(client.poll_delay().as_millis(), 500);
        client.main_started =
            Some(tokio::time::Instant::now() - std::time::Duration::from_secs(31));
        assert_eq!(client.poll_delay().as_millis(), 2000);
        let message =
            client.progress_message(&Completeness::new([true; 6]).with_main(false, true, true));
        assert!(message.contains("31 秒"));
        assert!(message.contains("主站UIFID"));
        assert!(message.contains("未完成前不会导入"));
    }

    #[tokio::test]
    async fn batch_rejects_errors_and_ignores_late_replies() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let peer = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            let Some(Ok(Message::Text(first))) = socket.next().await else {
                panic!("request missing");
            };
            let first: Value = serde_json::from_str(&first).unwrap();
            socket
                .send(Message::Text(
                    json!({"id":first["id"],"error":{"code":-1}})
                        .to_string()
                        .into(),
                ))
                .await
                .unwrap();
            let Some(Ok(Message::Text(second))) = socket.next().await else {
                panic!("request missing");
            };
            let second: Value = serde_json::from_str(&second).unwrap();
            for response in [
                json!({"id":first["id"],"result":{"stale":true}}),
                json!({"id":second["id"],"result":{"fresh":true}}),
            ] {
                socket
                    .send(Message::Text(response.to_string().into()))
                    .await
                    .unwrap();
            }
        });
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/test"))
            .await
            .unwrap();
        assert!(client.command("Test.read", json!({})).await.is_err());
        assert_eq!(
            client.command("Test.read", json!({})).await.unwrap(),
            json!({"fresh":true})
        );
        peer.await.unwrap();
    }

    #[test]
    fn cdp_and_cookie_boundaries_reject_remote_or_malformed_values() {
        assert!(validate_ws("ws://example.com/devtools/page/1").is_err());
        assert!(validate_ws("wss://127.0.0.1/devtools/page/1").is_err());
        assert!(cookie_header(&json!({"cookies":[{"name":"a","value":"bad\n"}]})).is_err());
        assert!(valid_douyin_domain(".passport.douyin.com"));
        assert!(!valid_douyin_domain("douyin.com.example.org"));
        assert_eq!(
            serde_json::to_value(Completeness::default()).unwrap()["ready"],
            false
        );
    }

    #[test]
    fn cookie_header_ignores_browser_unnamed_entries_only() {
        // Chromium accepts an empty cookie name. An unrelated nameless entry
        // must not poison all three scoped snapshots or change login cookies.
        let value = json!({"cookies":[
            {"name":"", "value":"synthetic-browser-marker"},
            {"name":"sessionid", "value":"synthetic-session"},
            {"name":"UIFID", "value":"real-browser-field"},
            {"name":"optional", "value":""}
        ]});
        assert_eq!(
            cookie_header(&value).unwrap(),
            "UIFID=real-browser-field; optional=; sessionid=synthetic-session"
        );
        assert_eq!(
            cookie_value(&value, "sessionid").as_deref(),
            Some("synthetic-session")
        );
        assert_eq!(
            cookie_value(&value, "UIFID").as_deref(),
            Some("real-browser-field")
        );
        for malformed in [
            json!({"value":"missing-name"}),
            json!({"name":false,"value":"bad"}),
            json!({"name":"a","value":123}),
            json!({"name":"a","value":"bad\r\n"}),
        ] {
            assert!(cookie_header(&json!({"cookies":[malformed]})).is_err());
        }
        assert!(cookie_header(&json!({"cookies":[
            {"name":"sessionid","value":"one"},
            {"name":"","value":"marker"},
            {"name":"sessionid","value":"other"}
        ]}))
        .is_err());
    }

    #[test]
    fn network_events_capture_only_bounded_security_headers() {
        let mut capture = NetworkCapture::default();
        observe_event(
            &mut capture,
            &json!({"method":"Network.requestWillBeSent","params":{"requestId":"one","request":{"url":"https://creator.douyin.com/passport/safe/get_identity_security_token/"}}}),
        );
        observe_event(
            &mut capture,
            &json!({"method":"Network.requestWillBeSentExtraInfo","params":{"requestId":"one","headers":{"X-Tt-Session-Dtrait":"dtrait-one"}}}),
        );
        observe_event(
            &mut capture,
            &json!({"method":"Network.responseReceivedExtraInfo","params":{"requestId":"one","headers":{"Bd-Ticket-Guard-Server-Data":"server-one"}}}),
        );
        assert_eq!(capture.dtrait_header, "dtrait-one");
        assert_eq!(
            capture.dtrait_path,
            "/passport/safe/get_identity_security_token/"
        );
        assert_eq!(capture.server_data, "server-one");
    }
}
