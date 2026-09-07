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

const DTRAIT_HOOK: &str = r"(() => {
  if (window.__dyauthreplyDtraitHookInstalled) return;
  const original = JSON.stringify;
  const publish = (value, path) => {
    window.__dyauthreplyLatestDtraitBlob = String(value || '');
    window.__dyauthreplyLatestDtraitPath = String(path || '');
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

const PAGE_INFO: &str = r"(async () => {
  const keys = localStorage.getItem('security-sdk/s_sdk_crypt_sdk') || '';
  const webProtect = localStorage.getItem('security-sdk/s_sdk_sign_data_key/web_protect') || '';
  const dtraitBlob = String(window.__dyauthreplyLatestDtraitBlob || '');
  const dtraitPath = String(window.__dyauthreplyLatestDtraitPath || '');
  let account = {};
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    const response = await fetch('https://creator.douyin.com/web/api/media/user/info/',
      {credentials:'include', signal:controller.signal});
    clearTimeout(timer);
    const data = await response.json();
    if (response.ok && data.status_code === 0) {
      const user = data.user || {};
      const urls = (user.avatar_thumb && user.avatar_thumb.url_list)
        || (user.avatar_larger && user.avatar_larger.url_list) || [];
      account = {
        nickname: String(user.nickname || ''),
        sec_uid: String(user.sec_uid || ''),
        unique_id: String(user.unique_id || user.short_id || ''),
        uid: String(user.uid || ''),
        avatar: String(urls[0] || '')
      };
    }
  } catch (_) {}
  return {keys, web_protect: webProtect, ua: navigator.userAgent || '',
    dtrait_blob: dtraitBlob, dtrait_path: dtraitPath, account};
})()";

#[derive(Clone, Serialize)]
#[serde(transparent)]
pub struct Completeness(BTreeMap<&'static str, bool>);

impl Default for Completeness {
    fn default() -> Self {
        Self::new([false; 6])
    }
}

impl Completeness {
    fn new(checks: [bool; 6]) -> Self {
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
}

pub struct CdpClient {
    socket: WebSocketStream<MaybeTlsStream<TcpStream>>,
    next_id: u64,
    captured: NetworkCapture,
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
        })
    }

    /// # Errors
    /// Requires the page target to support the bounded CDP domains used by the collector.
    pub async fn initialize(&mut self) -> Result<()> {
        self.command("Network.enable", json!({})).await?;
        self.command("Runtime.enable", json!({})).await?;
        self.command("Page.enable", json!({})).await?;
        self.command(
            "Page.addScriptToEvaluateOnNewDocument",
            json!({"source":DTRAIT_HOOK}),
        )
        .await?;
        self.send_only(
            "Page.navigate",
            json!({"url":"https://creator.douyin.com/creator-micro/im/message"}),
        )
        .await?;
        Ok(())
    }

    /// # Errors
    /// Returns malformed/oversized CDP, page or credential data without exposing secrets.
    pub async fn collect(&mut self) -> Result<Capture> {
        let storage = self.command("Storage.getCookies", json!({})).await?;
        let www = self.cookies_for("https://www.douyin.com/").await?;
        let creator = self.cookies_for("https://creator.douyin.com/").await?;
        let imapi = self.cookies_for("https://imapi.douyin.com/").await?;
        let page = self
            .command(
                "Runtime.evaluate",
                json!({"expression":PAGE_INFO,"awaitPromise":true,"returnByValue":true}),
            )
            .await?;
        build_candidate(
            &storage,
            &www,
            &creator,
            &imapi,
            page.pointer("/result/value").unwrap_or(&Value::Null),
            &self.captured,
        )
    }

    /// Requests graceful shutdown of the exact browser endpoint.
    /// # Errors
    /// Propagates a bounded CDP transport failure; the caller may still stop its owned child.
    pub async fn close(&mut self) -> Result<()> {
        self.command("Browser.close", json!({})).await.map(|_| ())
    }

    async fn cookies_for(&mut self, url: &str) -> Result<Value> {
        self.command("Network.getCookies", json!({"urls":[url]}))
            .await
    }

    async fn command(&mut self, method: &str, params: Value) -> Result<Value> {
        self.next_id = self.next_id.checked_add(1).context("CDP指令序号溢出")?;
        let id = self.next_id;
        self.socket
            .send(Message::Text(
                json!({"id":id,"method":method,"params":params})
                    .to_string()
                    .into(),
            ))
            .await?;
        let response = tokio::time::timeout(COMMAND_TIMEOUT, async {
            loop {
                let message = self.socket.next().await.context("CDP连接已关闭")??;
                let text = match message {
                    Message::Text(text) => text,
                    Message::Ping(value) => {
                        self.socket.send(Message::Pong(value)).await?;
                        continue;
                    }
                    Message::Close(_) => anyhow::bail!("CDP连接已关闭"),
                    _ => continue,
                };
                anyhow::ensure!(text.len() <= MAX_CDP_MESSAGE, "CDP响应过大");
                let value: Value = serde_json::from_str(&text)?;
                if value.get("method").is_some() {
                    observe_event(&mut self.captured, &value);
                    continue;
                }
                if value["id"].as_u64() != Some(id) {
                    continue;
                }
                if value.get("error").is_some() {
                    anyhow::bail!("浏览器采集指令执行失败");
                }
                return Ok(value["result"].clone());
            }
        })
        .await
        .context("浏览器采集响应超时")??;
        Ok(response)
    }

    async fn send_only(&mut self, method: &str, params: Value) -> Result<()> {
        self.next_id = self.next_id.checked_add(1).context("CDP指令序号溢出")?;
        self.socket
            .send(Message::Text(
                json!({"id":self.next_id,"method":method,"params":params})
                    .to_string()
                    .into(),
            ))
            .await?;
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

fn observe_event(capture: &mut NetworkCapture, event: &Value) {
    let method = event["method"].as_str().unwrap_or_default();
    let params = &event["params"];
    if method == "Network.requestWillBeSent" {
        if let (Some(id), Some(url)) = (
            params["requestId"].as_str(),
            params.pointer("/request/url").and_then(Value::as_str),
        ) {
            if url.len() <= 4096 {
                capture.request_urls.insert(id.into(), url.into());
            }
        }
        return;
    }
    let Some(headers) = params.get("headers").and_then(Value::as_object) else {
        return;
    };
    if method == "Network.responseReceivedExtraInfo" || method == "Network.responseReceived" {
        if let Some(value) = header(headers, "bd-ticket-guard-server-data", 64 * 1024) {
            capture.server_data = value;
        }
    }
    if method == "Network.requestWillBeSentExtraInfo" {
        if let Some(value) = header(headers, "x-tt-session-dtrait", 128 * 1024) {
            capture.dtrait_header = value;
            if let Some(url) = params["requestId"]
                .as_str()
                .and_then(|id| capture.request_urls.get(id))
            {
                capture.dtrait_path = url_path(url).unwrap_or_default();
            }
        }
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
    let server_data = if captured.server_data.is_empty() {
        cookie_server
    } else {
        &captured.server_data
    };
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
    let private_key = has_private_key(keys);
    let scoped = [&www_header, &creator_header, &imapi_header]
        .iter()
        .all(|value| !value.is_empty());
    let dtrait = !dtrait_blob.is_empty() || !captured.dtrait_header.is_empty();
    let identity = !sec_uid.is_empty() && !nickname.is_empty();
    let completeness = Completeness::new([
        true,
        scoped,
        !server_data.is_empty(),
        private_key,
        dtrait,
        identity && !user_agent.is_empty(),
    ]);
    if !completeness.ready() {
        return Ok(Capture {
            completeness,
            candidate: None,
        });
    }
    let payload = json!({
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
    let bytes = serde_json::to_vec(&payload)?;
    anyhow::ensure!(
        bytes.len() <= crate::protocol::credentials::MAX_CREDENTIAL_BYTES,
        "采集凭证过大"
    );
    let bundle = format!("DYCRED1.{}", URL_SAFE_NO_PAD.encode(bytes));
    crate::protocol::credentials::AccountCredentials::import_extension(
        "quick-auth-candidate".into(),
        &bundle,
    )?;
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

fn cookie_header(value: &Value) -> Result<String> {
    let cookies = value["cookies"]
        .as_array()
        .context("浏览器Cookie响应无效")?;
    anyhow::ensure!(cookies.len() <= 512, "浏览器Cookie数量超限");
    let mut unique = BTreeMap::new();
    for cookie in cookies {
        let name = cookie["name"].as_str().unwrap_or_default();
        let value = cookie["value"].as_str().unwrap_or_default();
        if name.is_empty()
            || name.len() > 256
            || value.len() > 32_768
            || name.chars().any(char::is_control)
            || value.chars().any(char::is_control)
        {
            anyhow::bail!("浏览器Cookie字段无效");
        }
        unique.insert(name, value);
    }
    Ok(unique
        .into_iter()
        .map(|(name, value)| format!("{name}={value}"))
        .collect::<Vec<_>>()
        .join("; "))
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
            {"name":"sessionid","value":"session-one","domain":".douyin.com"},
            {"name":"msToken","value":"token-one","domain":".douyin.com"},
            {"name":"bd_ticket_guard_ts_sign_id","value":"ts.1","domain":".douyin.com"}
        ]})
    }

    #[tokio::test]
    async fn fake_cdp_peer_produces_complete_bounded_candidate() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut socket = accept_async(stream).await.unwrap();
            while let Some(Ok(Message::Text(text))) = socket.next().await {
                let request: Value = serde_json::from_str(&text).unwrap();
                let method = request["method"].as_str().unwrap();
                let result = match method {
                    "Storage.getCookies" => json!({"cookies":[
                        {"name":"bd_ticket_guard_server_data","value":"{\"ticket\":\"one\",\"ts_sign\":\"ts.1.ok\",\"client_cert\":\"cert\"}","domain":".douyin.com"}
                    ]}),
                    "Network.getCookies" => cookies(),
                    "Runtime.evaluate" => json!({"result":{"value":{
                        "keys":"{\"ec_privateKey\":\"private\"}","web_protect":"","ua":"Chrome/140",
                        "dtrait_blob":"abcdefghijklmnopqrstuvwxyz","dtrait_path":"/identity",
                        "account":{"sec_uid":"scope-one","nickname":"测试账号","unique_id":"douyin-one","avatar":"https://example.com/a.png"}
                    }}}),
                    _ => json!({}),
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
        let mut client = CdpClient::connect(&format!("ws://{address}/devtools/page/one"))
            .await
            .unwrap();
        client.initialize().await.unwrap();
        let candidate = client.collect().await.unwrap().candidate.unwrap();
        assert!(candidate.completeness.ready());
        assert_eq!(candidate.identity.nickname, "测试账号");
        assert!(candidate.bundle.starts_with("DYCRED1."));
        drop(client);
        task.await.unwrap();
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
