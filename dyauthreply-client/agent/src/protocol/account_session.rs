//! Account-scoped native credential refresh and inbox reads. No legacy worker,
//! Python process or local synthetic lease is started by these read operations.
use super::{
    credentials::AccountCredentials,
    dtrait::DtraitSession,
    http_plan::{percent_encode_rfc3986, OrderedHeader, TicketGuardMode, TicketGuardSigningInput},
    inbox::{decode_get_by_user, encode_get_by_user, InboxPage},
    live_http::{HttpResult, ProtocolHttpClient, SessionEndpoint},
    native_signer::{derive_ecdh_key, ree_public_key, ticket_client_data, NativeSigner},
};
use serde::Serialize;
use serde_json::Value;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const IDENTITY_PATH: &str = "/passport/safe/get_identity_security_token/";
use super::live_sender::{reference_headers, REFERENCE_UA};

#[derive(Debug, Clone, Serialize, thiserror::Error)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AccountRequestError {
    #[error("imported account identity does not match authenticated cookie")]
    AccountMismatch,
    #[error("native transport failure at {step}")]
    Transport { step: &'static str },
    #[error("native signing failure at {step}")]
    Signing { step: &'static str },
    #[error("HTTP {status} at {step}")]
    Http { step: &'static str, status: u16 },
    #[error("business result at {step}: {code:?}")]
    Business {
        step: &'static str,
        code: Option<i64>,
    },
    #[error("malformed response at {step}")]
    Decode { step: &'static str },
}

#[derive(Clone)]
pub struct IdentityCredentials {
    pub token: String,
    pub device_id: String,
}

#[path = "self_profile.rs"]
mod self_profile;
pub use self_profile::{VerifiedSelf, WorkItem, WorksPage};

pub struct NativeAccountSession {
    pub credentials: AccountCredentials,
    http: ProtocolHttpClient,
    signer: NativeSigner,
    ecdh: Option<[u8; 32]>,
    certificate_retry_at: Option<Instant>,
    identity: Option<(IdentityCredentials, Instant)>,
    dtrait: Option<DtraitSession>,
    self_profile: Option<(VerifiedSelf, Instant, String)>,
}

impl NativeAccountSession {
    #[must_use]
    pub fn new(
        credentials: AccountCredentials,
        http: ProtocolHttpClient,
        signer: NativeSigner,
    ) -> Self {
        Self {
            credentials,
            http,
            signer,
            ecdh: None,
            certificate_retry_at: None,
            identity: None,
            dtrait: None,
            self_profile: None,
        }
    }
    #[must_use]
    pub const fn ecdh_ready(&self) -> bool {
        self.ecdh.is_some()
    }

    /// Binds outbound signing to this session's imported Cookie and ticket guard.
    /// A negotiated ECDH key selects HMAC; otherwise the verified P-256 key uses
    /// the protocol's ECDSA mode. No secret material is logged or serialized.
    /// # Errors
    /// Rejects missing signing material or a missing browser fingerprint.
    pub fn send_credentials(
        &self,
        generation: u64,
    ) -> Result<super::live_sender::SendCredentials, AccountRequestError> {
        if self.self_profile.as_ref().is_none_or(|(_, at, binding)| {
            at.elapsed() > Duration::from_secs(300) || *binding != self.credentials.binding_digest()
        }) {
            return Err(AccountRequestError::Signing {
                step: "verified_self_required",
            });
        }
        let fingerprint = self.credentials.web_fingerprint().to_owned();
        if generation == 0 || !self.credentials.has_signing_material() || fingerprint.is_empty() {
            return Err(AccountRequestError::Signing {
                step: "send_credentials",
            });
        }
        Ok(super::live_sender::SendCredentials {
            account_id: self.credentials.account_id.to_string(),
            canonical_sec_uid: self.credentials.expected_sec_uid.clone(),
            binding_digest: self.credentials.binding_digest(),
            credential_generation: generation,
            cookie: self.credentials.cookie_header("www.douyin.com"),
            ms_token: self.credentials.query_ms_token.clone(),
            private_key: self.credentials.private_key.clone(),
            ticket: self.credentials.ticket.clone(),
            ts_sign: self.credentials.ts_sign.clone(),
            fingerprint,
            ecdh_key: self.ecdh,
        })
    }
    fn headers(&self, host: &str) -> Vec<OrderedHeader> {
        vec![
            OrderedHeader::new("user-agent", &self.credentials.user_agent),
            OrderedHeader::new("cookie", self.credentials.cookie_header(host)),
        ]
    }
    async fn request(
        &self,
        endpoint: SessionEndpoint,
        query: &str,
        headers: &[OrderedHeader],
        body: &[u8],
        step: &'static str,
    ) -> Result<HttpResult, AccountRequestError> {
        self.http
            .session_request(endpoint, query, headers, body)
            .await
            .map_err(|_| AccountRequestError::Transport { step })
    }
    async fn signed_query(
        &self,
        query: String,
        step: &'static str,
    ) -> Result<String, AccountRequestError> {
        let ab = self
            .signer
            .sign_query(query.clone(), String::new())
            .await
            .map_err(|_| AccountRequestError::Signing { step })?;
        Ok(format!("{query}&a_bogus={}", percent_encode_rfc3986(&ab)))
    }
    /// Fetch server ECDH certificate using the account's current www cookies.
    /// # Errors
    /// Keeps network, business and decode failures distinct and secret-free.
    pub async fn refresh_certificate(&mut self) -> Result<(), AccountRequestError> {
        const STEP: &str = "certificate";
        let mut headers = self.headers("www.douyin.com");
        headers.extend([
            OrderedHeader::new("x-secsdk-csrf-request", "1"),
            OrderedHeader::new("x-secsdk-csrf-version", "1.2.22"),
            OrderedHeader::new("referer", "https://www.douyin.com/"),
            OrderedHeader::new("accept", "*/*"),
        ]);
        let bootstrap = self
            .request(SessionEndpoint::Csrf, "", &headers, b"", STEP)
            .await?;
        let csrf = bootstrap
            .csrf_token
            .as_deref()
            .and_then(|raw| raw.split(',').nth(1))
            .map(str::trim)
            .unwrap_or_default();
        let query = format!(
            "aid=6383&is_from_ttaccountsdk=1&msToken={}",
            percent_encode_rfc3986(&self.credentials.query_ms_token)
        );
        let query = self.signed_query(query, STEP).await?;
        let mut headers = self.headers("www.douyin.com");
        headers.extend([
            OrderedHeader::new("x-tt-session-dtrait", ""),
            OrderedHeader::new("referer", "https://www.douyin.com/"),
            OrderedHeader::new("accept", "application/json"),
            OrderedHeader::new("content-type", "application/x-www-form-urlencoded"),
            OrderedHeader::new("origin", "https://www.douyin.com"),
            OrderedHeader::new("accept-language", "zh-CN,zh;q=0.9"),
            OrderedHeader::new("sec-fetch-dest", "empty"),
            OrderedHeader::new("sec-fetch-mode", "cors"),
            OrderedHeader::new("sec-fetch-site", "same-origin"),
        ]);
        if !csrf.is_empty() {
            headers.push(OrderedHeader::new("x-secsdk-csrf-token", csrf));
        }
        let response = self
            .request(
                SessionEndpoint::Certificate,
                &query,
                &headers,
                b"server_data=1,aid=6383",
                STEP,
            )
            .await?;
        let payload = checked_json(&response, STEP)?;
        if payload["message"] != "success" {
            return Err(AccountRequestError::Business {
                step: STEP,
                code: payload["data"]["error_code"].as_i64(),
            });
        }
        let server = payload["data"]["server_cert"]
            .as_str()
            .ok_or(AccountRequestError::Decode { step: STEP })?;
        self.ecdh = Some(
            derive_ecdh_key(&self.credentials.private_key, server)
                .map_err(|_| AccountRequestError::Signing { step: STEP })?,
        );
        self.certificate_retry_at = None;
        Ok(())
    }

    async fn guard_headers(
        &mut self,
        path: &'static str,
    ) -> Result<Vec<OrderedHeader>, AccountRequestError> {
        const STEP: &str = "ticket_guard";
        if !self.credentials.has_signing_material() {
            return Ok(vec![]);
        }
        if self.ecdh.is_none()
            && self.credentials.client_cert.starts_with("pub.")
            && self
                .certificate_retry_at
                .is_none_or(|deadline| Instant::now() >= deadline)
            && self.refresh_certificate().await.is_err()
        {
            self.certificate_retry_at = Some(Instant::now() + Duration::from_secs(60));
        }
        let timestamp = unix_seconds()?;
        let input = TicketGuardSigningInput {
            path,
            ticket: self.credentials.ticket.clone(),
            ts_sign: self.credentials.ts_sign.clone(),
            private_key: self.credentials.private_key.clone(),
            timestamp,
            ecdh_key: self.ecdh.map(|k| k.to_vec()),
            t_trust: (!self
                .credentials
                .cookie("www.douyin.com", "_bd_ticket_crypt_cookie")
                .is_empty())
            .then_some(1),
            req_content: "ticket,path,timestamp",
            sign_payload: format!(
                "ticket={}&path={path}&timestamp={timestamp}",
                self.credentials.ticket
            ),
            mode: if self.ecdh.is_some() {
                TicketGuardMode::Hmac
            } else {
                TicketGuardMode::Ecdsa
            },
        };
        Ok(vec![
            OrderedHeader::new(
                "bd-ticket-guard-client-data",
                ticket_client_data(&input)
                    .map_err(|_| AccountRequestError::Signing { step: STEP })?,
            ),
            OrderedHeader::new(
                "bd-ticket-guard-ree-public-key",
                ree_public_key(&self.credentials.private_key)
                    .map_err(|_| AccountRequestError::Signing { step: STEP })?,
            ),
            OrderedHeader::new("bd-ticket-guard-version", "2"),
            OrderedHeader::new(
                "bd-ticket-guard-web-version",
                if self.credentials.ts_sign.starts_with("ts.1") {
                    "1"
                } else {
                    "2"
                },
            ),
            OrderedHeader::new(
                "bd-ticket-guard-web-sign-type",
                if self.ecdh.is_some() { "1" } else { "0" },
            ),
        ])
    }

    /// Account-local 240-second cache, matching the reference sender.
    /// # Errors
    /// Rejects malformed or negative server results; never marks sendable here.
    pub async fn identity(&mut self) -> Result<IdentityCredentials, AccountRequestError> {
        const STEP: &str = "identity";
        if let Some((cached, at)) = &self.identity {
            if at.elapsed() < Duration::from_secs(240) {
                return Ok(cached.clone());
            }
        }
        let mut headers = self.guard_headers(IDENTITY_PATH).await?;
        headers.push(OrderedHeader::new(
            "cookie",
            self.credentials.cookie_header("www.douyin.com"),
        ));
        let trace = uuid::Uuid::new_v4().simple().to_string()[..8].to_owned();
        let csrf = self
            .credentials
            .cookie("www.douyin.com", "passport_csrf_token");
        let csrf = if csrf.is_empty() {
            self.credentials
                .cookie("www.douyin.com", "passport_csrf_token_default")
        } else {
            csrf
        };
        headers.extend([
            OrderedHeader::new("user-agent", REFERENCE_UA),
            OrderedHeader::new("accept", "application/json, text/javascript"),
            OrderedHeader::new("referer", "https://www.douyin.com/chat?isPopup=1"),
            OrderedHeader::new(
                "sec-ch-ua",
                "\"Not=A?Brand\";v=\"99\", \"Google Chrome\";v=\"151\", \"Chromium\";v=\"151\"",
            ),
            OrderedHeader::new("sec-ch-ua-mobile", "?0"),
            OrderedHeader::new("sec-ch-ua-platform", "\"Windows\""),
            OrderedHeader::new(
                "accept-language",
                "zh-CN,zh;q=0.9,en;q=0.8,zh-TW;q=0.7,ja;q=0.6",
            ),
            OrderedHeader::new("priority", "u=1, i"),
            OrderedHeader::new("sec-fetch-dest", "empty"),
            OrderedHeader::new("sec-fetch-mode", "cors"),
            OrderedHeader::new("sec-fetch-site", "same-origin"),
            OrderedHeader::new("x-tt-passport-csrf-token", csrf),
            OrderedHeader::new("x-tt-passport-trace-id", &trace),
        ]);
        if !self.credentials.dtrait_blob.is_empty() {
            if self.dtrait.is_none() {
                self.dtrait = Some(
                    DtraitSession::new()
                        .map_err(|_| AccountRequestError::Signing { step: "dtrait" })?,
                );
            }
            if let Some(session) = &self.dtrait {
                headers.push(OrderedHeader::new(
                    "x-tt-session-dtrait",
                    session
                        .header(
                            IDENTITY_PATH,
                            &self.credentials.dtrait_blob,
                            unix_seconds()?,
                        )
                        .map_err(|_| AccountRequestError::Signing { step: "dtrait" })?,
                ));
            }
        } else if self.credentials.dtrait_path == IDENTITY_PATH
            && !self.credentials.dtrait_header.is_empty()
        {
            headers.push(OrderedHeader::new(
                "x-tt-session-dtrait",
                &self.credentials.dtrait_header,
            ));
        }
        let query = format!(
            "{}&msToken={}",
            identity_query(&trace),
            percent_encode_rfc3986(&self.credentials.query_ms_token)
        );
        let query = self.signed_query(query, STEP).await?;
        let response = self
            .request(SessionEndpoint::Identity, &query, &headers, b"", STEP)
            .await?;
        let payload = checked_json(&response, STEP)?;
        let token = payload["data"]["identity_security_token"]
            .as_str()
            .unwrap_or_default();
        if token.is_empty() || payload.get("message").is_some_and(|v| v != "success") {
            return Err(AccountRequestError::Business {
                step: STEP,
                code: payload["data"]["error_code"].as_i64(),
            });
        }
        let result = IdentityCredentials {
            token: token.to_owned(),
            device_id: payload["data"]["device_id"]
                .as_str()
                .unwrap_or_default()
                .to_owned(),
        };
        self.identity = Some((result.clone(), Instant::now()));
        Ok(result)
    }

    /// Resolve a send ticket from the current account's conversation scope.
    /// # Errors
    /// Requires a matching conversation ID/short ID and a nonempty ticket.
    pub async fn conversation_context(
        &self,
        id: &str,
        short_id: u64,
    ) -> Result<super::inbox::ConversationContext, AccountRequestError> {
        const STEP: &str = "conversation";
        let request = super::inbox::encode_conversation_info(id, short_id, 10001, REFERENCE_UA)
            .map_err(|_| AccountRequestError::Decode { step: STEP })?;
        let mut headers = reference_headers();
        headers.push(OrderedHeader::new(
            "cookie",
            self.credentials.cookie_header("imapi.douyin.com"),
        ));
        let response = self
            .request(SessionEndpoint::Conversation, "", &headers, &request, STEP)
            .await?;
        if !(200..300).contains(&response.status) {
            return Err(AccountRequestError::Http {
                step: STEP,
                status: response.status,
            });
        }
        let result = super::inbox::decode_conversation_info(&response.body)
            .map_err(|_| AccountRequestError::Decode { step: STEP })?;
        if result.status_code != 0 {
            return Err(AccountRequestError::Business {
                step: STEP,
                code: Some(result.status_code),
            });
        }
        if result.conversation_id != id || result.short_id != short_id || result.ticket.is_empty() {
            return Err(AccountRequestError::Decode { step: STEP });
        }
        Ok(result)
    }

    /// Establishes a native Frontier socket after authenticated self verification.
    /// # Errors
    /// No response-body contents are included in errors.
    pub async fn frontier_socket(&mut self) -> Result<wreq::ws::WebSocket, AccountRequestError> {
        let profile = self.verify_self().await?;
        self.http
            .frontier_socket(
                &profile.user_id,
                &self.credentials.cookie("www.douyin.com", "sessionid"),
                &self.credentials.cookie_header("www.douyin.com"),
                &self.credentials.user_agent,
            )
            .await
            .map_err(|error| match error {
                super::live_http::HttpExecutionError::WebSocketHandshake(status) => {
                    AccountRequestError::Http {
                        step: "frontier",
                        status,
                    }
                }
                _ => AccountRequestError::Transport { step: "frontier" },
            })
    }

    /// # Errors
    /// Returns HTTP, wire or authentication errors without advancing a cursor.
    pub async fn inbox(&self, cursor: u64, limit: u16) -> Result<InboxPage, AccountRequestError> {
        const STEP: &str = "inbox";
        let body = encode_get_by_user(cursor, limit)
            .map_err(|_| AccountRequestError::Decode { step: STEP })?;
        let mut headers = self.headers("imapi.douyin.com");
        headers.extend([
            OrderedHeader::new("content-type", "application/x-protobuf"),
            OrderedHeader::new("accept", "application/x-protobuf"),
            OrderedHeader::new("origin", "https://creator.douyin.com"),
            OrderedHeader::new("referer", "https://creator.douyin.com/"),
            OrderedHeader::new("sec-fetch-dest", "empty"),
            OrderedHeader::new("sec-fetch-mode", "cors"),
            OrderedHeader::new("sec-fetch-site", "same-site"),
        ]);
        let response = self
            .request(SessionEndpoint::Inbox, "", &headers, &body, STEP)
            .await?;
        if !(200..300).contains(&response.status) {
            return Err(AccountRequestError::Http {
                step: STEP,
                status: response.status,
            });
        }
        decode_get_by_user(&response.body).map_err(|_| AccountRequestError::Decode { step: STEP })
    }
}

fn checked_json(response: &HttpResult, step: &'static str) -> Result<Value, AccountRequestError> {
    if !(200..300).contains(&response.status) {
        return Err(AccountRequestError::Http {
            step,
            status: response.status,
        });
    }
    serde_json::from_slice(&response.body).map_err(|_| AccountRequestError::Decode { step })
}
fn unix_seconds() -> Result<u64, AccountRequestError> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .map_err(|_| AccountRequestError::Signing { step: "clock" })
}
#[must_use]
pub fn identity_query(trace: &str) -> String {
    format!("passport_jssdk_version=4.2.3&passport_jssdk_type=lite&is_from_ttaccountsdk=1&aid=6383&language=zh&scene=web_im&auto_retry_req=0&skip_verify=false&identity_token_force_get_tag=0&biz_trace_id={trace}&id_token_version=1.2.10")
}
