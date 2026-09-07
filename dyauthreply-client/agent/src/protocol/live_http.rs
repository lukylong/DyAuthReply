//! Bounded, no-retry HTTP execution. Browser profile is explicit, not inferred
//! from successful offline fixture tests. Public probe has no credentials.
use super::http_plan::RequestPlan;
use futures_util::StreamExt;
use serde::Serialize;
use std::{fmt, sync::Arc, time::Duration};
use thiserror::Error;
use tokio::sync::Semaphore;
use wreq::{Client, Method};
use wreq_util::{Emulation, Profile};

const MAX_RESPONSE: usize = 2 * 1024 * 1024;
pub const PROBE_ENDPOINT: &str = "https://www.douyin.com/robots.txt";

/// Fixed read-only/session bootstrap endpoints, never arbitrary URLs.
#[derive(Clone, Copy)]
pub enum SessionEndpoint {
    Identity,
    QueryUser,
    SelfPage,
    ProfileOther,
    Works,
    Csrf,
    Certificate,
    Inbox,
    Conversation,
}
impl SessionEndpoint {
    pub(super) const fn url(self) -> &'static str {
        match self {
            Self::Identity => "https://www.douyin.com/passport/safe/get_identity_security_token/",
            Self::QueryUser => "https://www.douyin.com/aweme/v1/web/query/user/",
            Self::SelfPage => "https://www.douyin.com/user/self",
            Self::ProfileOther => "https://www.douyin.com/aweme/v1/web/user/profile/other/",
            Self::Works => "https://www.douyin.com/aweme/v1/web/aweme/post/",
            Self::Csrf => "https://www.douyin.com/service/2/abtest_config/",
            Self::Certificate => "https://www.douyin.com/passport/ticket_guard/get_client_cert/",
            Self::Inbox => "https://imapi.douyin.com/v1/message/get_by_user",
            Self::Conversation => "https://imapi.douyin.com/v2/conversation/get_info_list",
        }
    }
    const fn method(self) -> Method {
        match self {
            Self::Identity
            | Self::QueryUser
            | Self::SelfPage
            | Self::ProfileOther
            | Self::Works => Method::GET,
            Self::Csrf => Method::HEAD,
            Self::Certificate | Self::Inbox | Self::Conversation => Method::POST,
        }
    }
}

#[derive(Debug, Error)]
pub enum HttpExecutionError {
    #[error("HTTP client initialization failed")]
    Initialization,
    #[error("Frontier handshake HTTP {0}")]
    WebSocketHandshake(u16),
    #[error("HTTP execution capacity exhausted")]
    Busy,
    #[error("HTTP timeout; delivery unknown")]
    Timeout,
    #[error("HTTP transport failed; delivery unknown")]
    Transport,
    #[error("HTTP response exceeds bound; delivery unknown")]
    ResponseLimit,
}

pub struct HttpResult {
    pub status: u16,
    pub body: Vec<u8>,
    pub version: String,
    pub(super) csrf_token: Option<String>,
}
impl fmt::Debug for HttpResult {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("HttpResult")
            .field("status", &self.status)
            .field("body_bytes", &self.body.len())
            .field("version", &self.version)
            .finish_non_exhaustive()
    }
}

#[derive(Clone)]
pub struct ProtocolHttpClient {
    client: Client,
    lanes: Arc<Semaphore>,
    profile_name: String,
    #[cfg(test)]
    pub(super) test_endpoint: Option<String>,
}
impl fmt::Debug for ProtocolHttpClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ProtocolHttpClient")
            .field("profile", &self.profile_name)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Serialize)]
pub struct ProbeResult {
    pub endpoint: &'static str,
    pub profile: String,
    pub http_status: u16,
    pub http_version: String,
    pub response_bytes: usize,
    pub account_authenticated: bool,
    pub send_capability_verified: bool,
}

impl ProtocolHttpClient {
    #[must_use]
    pub fn profile_name(&self) -> &str {
        &self.profile_name
    }

    /// Shares the process-wide network admission budget across TLS profiles.
    #[must_use]
    pub fn sharing_budget(mut self, existing: &Self) -> Self {
        self.lanes = existing.lanes.clone();
        self
    }
    /// # Errors
    /// Rejects invalid concurrency or TLS profile configuration.
    pub fn new(concurrency: usize) -> Result<Self, HttpExecutionError> {
        Self::with_profile(concurrency, Profile::Chrome131)
    }

    /// Match the existing adapter's rule: newest available Chrome profile no
    /// newer than the imported UA. Unsupported UAs require explicit migration.
    /// # Errors
    /// Rejects unsupported UA or client construction failure.
    pub fn for_user_agent(
        concurrency: usize,
        user_agent: &str,
    ) -> Result<Self, HttpExecutionError> {
        let major = user_agent
            .split("Chrome/")
            .nth(1)
            .or_else(|| user_agent.split("Chromium/").nth(1))
            .and_then(|version| version.split('.').next())
            .and_then(|major| major.parse::<u16>().ok())
            .ok_or(HttpExecutionError::Initialization)?;
        let profile = Profile::VARIANTS
            .iter()
            .filter_map(|profile| {
                format!("{profile:?}")
                    .strip_prefix("Chrome")?
                    .parse::<u16>()
                    .ok()
                    .filter(|version| *version <= major)
                    .map(|version| (version, *profile))
            })
            .max_by_key(|(version, _)| *version)
            .map(|(_, profile)| profile)
            .ok_or(HttpExecutionError::Initialization)?;
        Self::with_profile(concurrency, profile)
    }

    fn with_profile(concurrency: usize, profile: Profile) -> Result<Self, HttpExecutionError> {
        if !(1..=32).contains(&concurrency) {
            return Err(HttpExecutionError::Initialization);
        }
        let profile_name = format!("{profile:?}").to_lowercase();
        let profile = Emulation::builder().profile(profile).headers(false).build();
        let client = Client::builder()
            .emulation(profile)
            .redirect(wreq::redirect::Policy::none())
            .retry(wreq::retry::Policy::never())
            .no_proxy()
            .pool_max_idle_per_host(2)
            .timeout(Duration::from_secs(15))
            .connect_timeout(Duration::from_secs(8))
            .build()
            .map_err(|_| HttpExecutionError::Initialization)?;
        Ok(Self {
            client,
            profile_name,
            lanes: Arc::new(Semaphore::new(concurrency)),
            #[cfg(test)]
            test_endpoint: None,
        })
    }

    /// Native bounded WebSocket upgrade; HTTP admission is released after handshake.
    /// The URL/token and original transport error are never exposed in diagnostics.
    /// # Errors
    /// Rejects malformed credentials, network overload, failed upgrade or protocol negotiation.
    pub async fn frontier_socket(
        &self,
        device: &str,
        session: &str,
        cookie: &str,
        ua: &str,
    ) -> Result<wreq::ws::WebSocket, HttpExecutionError> {
        if cookie.len() > 192 * 1024 || ua.len() > 4096 {
            return Err(HttpExecutionError::Initialization);
        }
        let url = super::frontier::connection_url(device, session)
            .map_err(|_| HttpExecutionError::Initialization)?;
        let _lane = self
            .lanes
            .try_acquire()
            .map_err(|_| HttpExecutionError::Busy)?;
        let response = self
            .client
            .websocket(url)
            .protocols(["binary", "base64", "pbbp2"])
            .header("user-agent", ua)
            .header("cookie", cookie)
            .header("origin", "https://www.douyin.com")
            .max_frame_size(super::frontier::MAX_FRAME_BYTES)
            .max_message_size(super::frontier::MAX_FRAME_BYTES)
            .read_buffer_size(16 * 1024)
            .write_buffer_size(8 * 1024)
            .max_write_buffer_size(64 * 1024)
            .send()
            .await
            .map_err(|_| HttpExecutionError::Transport)?;
        if response.status().as_u16() != 101 {
            return Err(HttpExecutionError::WebSocketHandshake(
                response.status().as_u16(),
            ));
        }
        response
            .into_websocket()
            .await
            .map_err(|_| HttpExecutionError::Transport)
    }

    /// Executes one fixed account read/bootstrap request with caller-scoped headers.
    /// # Errors
    /// Rejects unbounded requests, transport failures and oversized responses.
    pub async fn session_request(
        &self,
        endpoint: SessionEndpoint,
        query: &str,
        headers: &[super::http_plan::OrderedHeader],
        body: &[u8],
    ) -> Result<HttpResult, HttpExecutionError> {
        if query.len() > 16384 || headers.len() > 64 || body.len() > 2 * 1024 * 1024 {
            return Err(HttpExecutionError::Initialization);
        }
        let url = if query.is_empty() {
            endpoint.url().to_owned()
        } else {
            format!("{}?{query}", endpoint.url())
        };
        let mut request = self
            .client
            .request(endpoint.method(), url)
            .body(body.to_vec());
        let mut total = 0usize;
        for header in headers {
            total = total.saturating_add(header.name.len() + header.value.len());
            if total > 192 * 1024 {
                return Err(HttpExecutionError::Initialization);
            }
            request = request.header(&header.name, &header.value);
        }
        self.execute_request(request, Duration::from_secs(15)).await
    }

    // Sending is exposed only through the durable/fenced live_sender module.
    pub(super) async fn execute(
        &self,
        plan: &RequestPlan,
    ) -> Result<HttpResult, HttpExecutionError> {
        let endpoint = plan.final_url();
        #[cfg(test)]
        let test_url = self.test_endpoint.as_ref().map(|target| {
            let query = plan
                .final_url()
                .split_once('?')
                .map_or("", |(_, query)| query);
            format!("{target}?{query}")
        });
        #[cfg(test)]
        let endpoint = test_url.as_deref().unwrap_or(endpoint);
        let mut request = self
            .client
            .request(Method::POST, endpoint)
            .body(plan.body().to_vec())
            .timeout(Duration::from_millis(plan.timeout_ms()));
        for header in plan.headers() {
            request = request.header(&header.name, &header.value);
        }
        self.execute_request(request, Duration::from_millis(plan.timeout_ms()))
            .await
    }

    async fn execute_request(
        &self,
        request: wreq::RequestBuilder,
        timeout: Duration,
    ) -> Result<HttpResult, HttpExecutionError> {
        let _permit = self
            .lanes
            .clone()
            .try_acquire_owned()
            .map_err(|_| HttpExecutionError::Busy)?;
        tokio::time::timeout(timeout, async {
            let response = request.send().await.map_err(|error| {
                if error.is_timeout() {
                    HttpExecutionError::Timeout
                } else {
                    HttpExecutionError::Transport
                }
            })?;
            let status = response.status().as_u16();
            let csrf_token = response
                .headers()
                .get("x-ware-csrf-token")
                .and_then(|v| v.to_str().ok())
                .filter(|v| v.len() <= 8192)
                .map(str::to_owned);
            let version = format!("{:?}", response.version());
            if response
                .content_length()
                .is_some_and(|size| size > MAX_RESPONSE as u64)
            {
                return Err(HttpExecutionError::ResponseLimit);
            }
            let mut stream = response.bytes_stream();
            let mut body = Vec::new();
            while let Some(chunk) = stream.next().await {
                let chunk = chunk.map_err(|_| HttpExecutionError::Transport)?;
                if body.len().saturating_add(chunk.len()) > MAX_RESPONSE {
                    return Err(HttpExecutionError::ResponseLimit);
                }
                body.extend_from_slice(&chunk);
            }
            Ok(HttpResult {
                status,
                body,
                version,
                csrf_token,
            })
        })
        .await
        .map_err(|_| HttpExecutionError::Timeout)?
    }

    /// Read-only real HTTPS check. It deliberately does not load account cookies.
    /// # Errors
    /// Reports transport/timeout/size failures, without exposing request data.
    pub async fn probe(&self) -> Result<ProbeResult, HttpExecutionError> {
        let request = self.client.get(PROBE_ENDPOINT)
            .header("user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
            .header("accept", "text/plain");
        let response = self
            .execute_request(request, Duration::from_secs(15))
            .await?;
        Ok(ProbeResult {
            endpoint: PROBE_ENDPOINT,
            profile: self.profile_name.clone(),
            http_status: response.status,
            http_version: response.version,
            response_bytes: response.body.len(),
            account_authenticated: false,
            send_capability_verified: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::TcpListener,
    };
    async fn server(
        reply: &'static [u8],
        delay: Duration,
    ) -> (String, tokio::task::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let join = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut request = [0_u8; 4096];
            let _ = stream.read(&mut request).await;
            tokio::time::sleep(delay).await;
            let _ = stream.write_all(reply).await;
        });
        (format!("http://{address}"), join)
    }
    #[tokio::test]
    async fn real_socket_preserves_body_and_does_not_follow_redirects() {
        let (url, join) = server(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/secret\r\nContent-Length: 4\r\nConnection: close\r\n\r\ntest", Duration::ZERO).await;
        let client = ProtocolHttpClient::new(1).unwrap();
        let response = client
            .execute_request(client.client.get(url), Duration::from_secs(2))
            .await
            .unwrap();
        assert_eq!(response.status, 302);
        assert_eq!(response.body, b"test");
        join.await.unwrap();
    }
    #[tokio::test]
    async fn declared_oversized_body_is_rejected_before_reading() {
        let (url, join) = server(
            b"HTTP/1.1 200 OK\r\nContent-Length: 999999999\r\nConnection: close\r\n\r\n",
            Duration::ZERO,
        )
        .await;
        let client = ProtocolHttpClient::new(1).unwrap();
        assert!(matches!(
            client
                .execute_request(client.client.get(url), Duration::from_secs(2))
                .await,
            Err(HttpExecutionError::ResponseLimit)
        ));
        join.await.unwrap();
    }
    #[tokio::test]
    async fn real_socket_timeout_is_explicit_and_secret_free() {
        let (url, join) = server(
            b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n",
            Duration::from_millis(200),
        )
        .await;
        let client = ProtocolHttpClient::new(1).unwrap();
        let error = client
            .execute_request(
                client.client.get(format!("{url}/?secret=COOKIE")),
                Duration::from_millis(30),
            )
            .await
            .unwrap_err();
        assert!(matches!(error, HttpExecutionError::Timeout));
        assert!(!error.to_string().contains("COOKIE"));
        join.await.unwrap();
    }
}
