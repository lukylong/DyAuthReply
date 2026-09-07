//! Executable send pipeline: durable payload/IDs -> native signing -> fenced
//! `StartAttempt` -> HTTP once -> classified durable outcome. Not wired to the
//! legacy UI/worker; callers must obtain the hosted lease and current account facts.
use super::{
    classify_delivery, decode_send_message_response, encode_send_message_request,
    finalize_send_request,
    http_plan::{OrderedHeader, SEND_ENDPOINT, SEND_METHOD},
    live_http::ProtocolHttpClient,
    native_signer::NativeSigner,
    prepare_send_request, DeliveryClass, FingerprintInput, SendHttpPlanInput, SendRequestInput,
    TicketGuardCredential,
};
use crate::{
    runtime::{account::AccountControl, model::WorkKind},
    store::{CoreStore, LeaseToken, SegmentTransition},
};
use std::{
    sync::Arc,
    time::{Duration, Instant},
};
use thiserror::Error;

/// Account-bound imported material. No Debug/Serialize implementation: secrets
/// must not become a diagnostic payload by accident.
pub struct SendCredentials {
    pub account_id: String,
    pub canonical_sec_uid: String,
    pub binding_digest: String,
    pub credential_generation: u64,
    pub cookie: String,
    pub ms_token: String,
    pub private_key: String,
    pub ticket: String,
    pub ts_sign: String,
    pub fingerprint: String,
    pub dtrait_header: String,
    pub ecdh_key: Option<[u8; 32]>,
}

pub struct SendOperation {
    pub lease: LeaseToken,
    pub lease_deadline: Instant,
    pub control: tokio::sync::watch::Receiver<AccountControl>,
    pub kind: WorkKind,
    pub batch_id: String,
    pub segment_id: String,
    pub request: SendRequestInput,
    pub credentials: SendCredentials,
}

#[derive(Debug, Error)]
pub enum LiveSendError {
    #[error("account state or credential binding rejects send")]
    AccountState,
    #[error("durable send payload/ID is unavailable or mismatched")]
    Payload,
    #[error("native request signing failed; no network send attempted")]
    Signing,
    #[error("durable lease/fence check failed; no network send attempted")]
    Fence,
    #[error("attempt already claimed; no network send attempted")]
    AlreadyClaimed,
    #[error("send outcome persistence failed; inspect durable recovery state")]
    Persistence,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SendOutcome {
    pub classification: DeliveryClass,
    pub platform_message_id: Option<String>,
    pub response_codes: Option<String>,
}

pub struct LiveSender {
    signer: NativeSigner,
    http: ProtocolHttpClient,
    store: Arc<CoreStore>,
}
impl LiveSender {
    /// Inject process-owned resources; constructing an account sender must not
    /// silently create another signer pool or HTTP concurrency budget.
    #[must_use]
    pub fn new(store: Arc<CoreStore>, signer: NativeSigner, http: ProtocolHttpClient) -> Self {
        Self {
            signer,
            http,
            store,
        }
    }

    /// Sends a single already-durable text segment. Stable client message ID
    /// and payload are read from the outbox, never regenerated per attempt.
    /// # Errors
    /// Rejects account mismatches, signing errors, stale leases and duplicate
    /// claims before network I/O. Failed network I/O is persisted as uncertain.
    pub async fn send(&self, mut operation: SendOperation) -> Result<SendOutcome, LiveSendError> {
        if !send_allowed(&operation) {
            return Err(LiveSendError::AccountState);
        }
        if operation
            .lease_deadline
            .saturating_duration_since(Instant::now())
            < Duration::from_secs(20)
        {
            return Err(LiveSendError::Fence);
        }
        let store = self.store.clone();
        let batch_id = operation.batch_id.clone();
        let batch = tokio::task::spawn_blocking(move || store.outbound_batch(&batch_id))
            .await
            .map_err(|_| LiveSendError::Payload)?
            .map_err(|_| LiveSendError::Payload)?;
        if batch.account_id != operation.lease.account_id {
            return Err(LiveSendError::Payload);
        }
        let segment = batch
            .segments
            .iter()
            .find(|s| s.id == operation.segment_id && s.kind == "text")
            .ok_or(LiveSendError::Payload)?;
        if !matches!(
            segment.status,
            crate::store::SegmentStatus::Prepared | crate::store::SegmentStatus::Retryable
        ) {
            return Err(LiveSendError::AlreadyClaimed);
        }
        operation
            .request
            .client_msg_id
            .clone_from(&segment.client_message_id);
        operation.request.text.clone_from(&segment.payload);
        if segment.payload.len() > super::http_plan::MAX_BODY_BYTES {
            return Err(LiveSendError::Payload);
        }
        let expected_id = segment.client_message_id.clone();
        let plan = signed_plan(&self.signer, &operation).await?;
        if !send_allowed(&operation) {
            return Err(LiveSendError::AccountState);
        }
        if operation
            .lease_deadline
            .saturating_duration_since(Instant::now())
            < Duration::from_secs(16)
        {
            return Err(LiveSendError::Fence);
        }
        let store = self.store.clone();
        let lease = operation.lease.clone();
        let segment_id = operation.segment_id.clone();
        let transition = if operation.kind == WorkKind::AutomaticReply {
            SegmentTransition::StartAutomaticAttempt
        } else {
            SegmentTransition::StartAttempt
        };
        let claim = tokio::task::spawn_blocking(move || {
            store.transition_segment(&lease, &segment_id, transition)
        })
        .await
        .map_err(|_| LiveSendError::Fence)?
        .map_err(|_| LiveSendError::Fence)?;
        if !claim.applied {
            return Err(LiveSendError::AlreadyClaimed);
        }
        if !send_allowed(&operation) {
            self.persist_outcome(
                operation.lease,
                operation.segment_id,
                &SendOutcome {
                    classification: DeliveryClass::BusinessRejected,
                    platform_message_id: None,
                    response_codes: None,
                },
                &operation.credentials,
            )
            .await?;
            return Err(LiveSendError::AccountState);
        }
        let outcome = self
            .execute_plan(&plan, operation.lease_deadline, &expected_id)
            .await;
        self.persist_outcome(
            operation.lease,
            operation.segment_id,
            &outcome,
            &operation.credentials,
        )
        .await?;
        Ok(outcome)
    }

    async fn execute_plan(
        &self,
        plan: &super::http_plan::RequestPlan,
        deadline: Instant,
        expected_id: &str,
    ) -> SendOutcome {
        let network_deadline = tokio::time::Instant::from_std(
            deadline
                .checked_sub(Duration::from_secs(1))
                .unwrap_or_else(Instant::now),
        );
        match tokio::time::timeout_at(network_deadline, self.http.execute(plan)).await {
            Ok(Ok(response)) => match decode_send_message_response(&response.body) {
                Ok(decoded) => {
                    let classification =
                        classify_delivery(Some(response.status), &decoded, expected_id);
                    let delivered = matches!(
                        classification,
                        DeliveryClass::Delivered | DeliveryClass::DeliveredSoft
                    );
                    SendOutcome {
                        classification,
                        platform_message_id: delivered.then(|| decoded.server_msg_id.to_string()),
                        response_codes: Some(format!(
                            "http={},outer={:?},business={:?},raw_check={:?}",
                            response.status,
                            decoded.outer_status_present.then_some(decoded.status_code),
                            (decoded.business_payload_present && decoded.business_payload_valid)
                                .then_some(decoded.biz_status_code),
                            (decoded.business_payload_present && decoded.business_payload_valid)
                                .then_some(decoded.biz_raw_check_code)
                        )),
                    }
                }
                Err(_) => SendOutcome {
                    classification: if matches!(response.status, 401 | 403) {
                        DeliveryClass::LoginExpired
                    } else {
                        DeliveryClass::Uncertain
                    },
                    platform_message_id: None,
                    response_codes: None,
                },
            },
            Ok(Err(_)) | Err(_) => SendOutcome {
                classification: DeliveryClass::Uncertain,
                platform_message_id: None,
                response_codes: None,
            },
        }
    }

    async fn persist_outcome(
        &self,
        lease: LeaseToken,
        id: String,
        outcome: &SendOutcome,
        credentials: &SendCredentials,
    ) -> Result<(), LiveSendError> {
        let transition = if let Some(platform_message_id) = &outcome.platform_message_id {
            SegmentTransition::Confirm {
                platform_message_id: platform_message_id.clone(),
            }
        } else if matches!(
            outcome.classification,
            DeliveryClass::LoginExpired
                | DeliveryClass::RiskControlled
                | DeliveryClass::BusinessRejected
                | DeliveryClass::ProtocolRejected
        ) {
            SegmentTransition::Reject {
                error: format!(
                    "{:?}{}",
                    outcome.classification,
                    outcome
                        .response_codes
                        .as_ref()
                        .map_or_else(String::new, |codes| format!(" [{codes}]"))
                ),
            }
        } else {
            SegmentTransition::MarkUncertain {
                reason: "native_http_delivery_unconfirmed".to_owned(),
            }
        };
        let binding = credentials.binding_digest.clone();
        let canonical = credentials.canonical_sec_uid.clone();
        let capability = match outcome.classification {
            DeliveryClass::Delivered | DeliveryClass::DeliveredSoft => {
                crate::state::SendCapability::Sendable
            }
            DeliveryClass::RiskControlled => crate::state::SendCapability::RiskControlled,
            DeliveryClass::LoginExpired => crate::state::SendCapability::AuthExpired,
            _ => crate::state::SendCapability::Unknown,
        };
        let store = self.store.clone();
        tokio::task::spawn_blocking(move || {
            store.transition_segment_observed(
                &lease,
                &id,
                transition,
                crate::store::SendObservation {
                    canonical_sec_uid: &canonical,
                    credential_digest: &binding,
                    capability,
                },
            )
        })
        .await
        .map_err(|_| LiveSendError::Persistence)?
        .map_err(|_| LiveSendError::Persistence)?;
        Ok(())
    }
}

fn send_allowed(operation: &SendOperation) -> bool {
    if operation.control.has_changed().is_err() || Instant::now() >= operation.lease_deadline {
        return false;
    }
    let control = *operation.control.borrow();
    matches!(
        operation.kind,
        WorkKind::ManualSend | WorkKind::AutomaticReply
    ) && control.allows(operation.kind)
        && operation.credentials.account_id == operation.lease.account_id
        && operation.credentials.credential_generation == control.state.credential_generation
        && i64::try_from(control.state.lease_epoch).ok() == Some(operation.lease.fence_epoch)
}

#[cfg(test)]
pub(crate) const REFERENCE_UA: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36";

pub(super) fn browser_client_hints(user_agent: &str) -> (String, &'static str) {
    let version = user_agent
        .split("Chrome/")
        .nth(1)
        .or_else(|| user_agent.split("Chromium/").nth(1))
        .and_then(|value| value.split_whitespace().next())
        .unwrap_or("151.0.0.0");
    let major = version.split('.').next().unwrap_or("151");
    let platform = if user_agent.contains("Macintosh") {
        "\"macOS\""
    } else if user_agent.contains("Linux") && !user_agent.contains("Android") {
        "\"Linux\""
    } else {
        "\"Windows\""
    };
    (
        format!(
            "\"Not=A?Brand\";v=\"99\", \"Google Chrome\";v=\"{major}\", \"Chromium\";v=\"{major}\""
        ),
        platform,
    )
}

pub(super) fn headers_for_user_agent(user_agent: &str) -> Vec<OrderedHeader> {
    let (client_hint, platform) = browser_client_hints(user_agent);
    [
        ("content-type", "application/x-protobuf"),
        ("accept", "application/x-protobuf"),
        ("user-agent", user_agent),
        ("sec-ch-ua", client_hint.as_str()),
        ("sec-ch-ua-mobile", "?0"),
        ("sec-ch-ua-platform", platform),
        (
            "accept-language",
            "zh-CN,zh;q=0.9,en;q=0.8,zh-TW;q=0.7,ja;q=0.6",
        ),
        ("referer", "https://www.douyin.com/"),
        ("priority", "u=1, i"),
        ("sec-fetch-dest", "empty"),
        ("sec-fetch-mode", "cors"),
        ("sec-fetch-site", "same-origin"),
    ]
    .into_iter()
    .map(|(name, value)| OrderedHeader::new(name, value))
    .collect()
}

#[cfg(test)]
pub(super) fn reference_headers() -> Vec<OrderedHeader> {
    headers_for_user_agent(REFERENCE_UA)
}

async fn signed_plan(
    signer: &NativeSigner,
    operation: &SendOperation,
) -> Result<super::http_plan::RequestPlan, LiveSendError> {
    let body =
        encode_send_message_request(&operation.request).map_err(|_| LiveSendError::Payload)?;
    let credentials = &operation.credentials;
    let mut headers = headers_for_user_agent(&operation.request.user_agent);
    if !credentials.dtrait_header.is_empty() {
        headers.push(OrderedHeader::new(
            "x-tt-session-dtrait",
            &credentials.dtrait_header,
        ));
    }
    let timestamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| LiveSendError::Signing)?
        .as_secs();
    let plan = prepare_send_request(&SendHttpPlanInput {
        method: SEND_METHOD,
        url: SEND_ENDPOINT,
        raw_cookie_header: &credentials.cookie,
        query_ms_token: &credentials.ms_token,
        user_agent: &operation.request.user_agent,
        caller_headers: &headers,
        body: &body,
        timeout_ms: 15_000,
        fingerprint: FingerprintInput {
            verify_fp: &credentials.fingerprint,
            fp: &credentials.fingerprint,
        },
        ticket_guard: TicketGuardCredential {
            private_key: &credentials.private_key,
            ticket: &credentials.ticket,
            ts_sign: &credentials.ts_sign,
            timestamp,
            ecdh_key: credentials.ecdh_key.as_ref().map(<[u8; 32]>::as_slice),
        },
    })
    .map_err(|_| LiveSendError::Signing)?;
    let output = signer
        .sign(plan.clone())
        .await
        .map_err(|_| LiveSendError::Signing)?;
    finalize_send_request(plan, output).map_err(|_| LiveSendError::Signing)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        protocol::wire::{append_bytes_field, append_string_field, append_varint_field},
        state::{
            AccountRuntimeState, InboundState, LifecycleState, OwnershipState, SendCapability,
        },
        store::{OutboundSegmentDraft, SegmentStatus},
    };
    use std::time::Duration;
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::TcpListener,
    };

    fn sender(store: Arc<CoreStore>) -> LiveSender {
        LiveSender::new(
            store,
            NativeSigner::new(4).unwrap(),
            ProtocolHttpClient::for_user_agent(8, REFERENCE_UA).unwrap(),
        )
    }

    fn setup() -> (
        tempfile::TempDir,
        Arc<CoreStore>,
        SendOperation,
        tokio::sync::watch::Sender<AccountControl>,
    ) {
        let dir = tempfile::tempdir().unwrap();
        let store = Arc::new(CoreStore::open(dir.path()).unwrap());
        let now = i64::try_from(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis(),
        )
        .unwrap();
        let lease = store
            .install_verified_account_lease("synthetic", "install", "boot", 1, now + 60000)
            .unwrap()
            .token();
        store
            .restore_send_observation(&lease, "synthetic-sec", &"a".repeat(64))
            .unwrap();
        let batch = store
            .prepare_outbound_batch(
                &lease,
                "trigger",
                "response",
                &[OutboundSegmentDraft::text("durable synthetic text")],
            )
            .unwrap();
        let (control_sender, control) = tokio::sync::watch::channel(AccountControl {
            actor_generation: 1,
            state: AccountRuntimeState {
                lifecycle: LifecycleState::Running,
                ownership: OwnershipState::Owned,
                inbound: InboundState::WsHealthy,
                send: SendCapability::Sendable,
                credential_generation: 1,
                lease_epoch: 1,
            },
        });
        let op = SendOperation {
            lease,
            lease_deadline: Instant::now() + Duration::from_secs(60),
            control,
            kind: WorkKind::ManualSend,
            batch_id: batch.id.clone(),
            segment_id: batch.segments[0].id.clone(),
            request: SendRequestInput {
                conversation_id: "synthetic-conversation".to_owned(),
                conversation_short_id: 12,
                ticket: "synthetic-conversation-ticket".to_owned(),
                text: "must-be-overwritten".to_owned(),
                user_agent: REFERENCE_UA.to_owned(),
                client_msg_id: "must-be-overwritten".to_owned(),
                sequence_id: 1,
                stime: "1700000000000".to_owned(),
                message_type: 7,
                identity_security_token: String::new(),
                identity_security_device_id: String::new(),
                mentioned_users: vec![],
                ext: vec![],
            },
            credentials: SendCredentials {
                canonical_sec_uid: "synthetic-sec".into(),
                binding_digest: "a".repeat(64),
                account_id: "synthetic".to_owned(),
                credential_generation: 1,
                cookie: "sessionid=synthetic".to_owned(),
                ms_token: "synthetic-token".to_owned(),
                private_key: "1".to_owned(),
                ticket: "synthetic-ticket".to_owned(),
                ts_sign: "synthetic-ts".to_owned(),
                fingerprint: "verify_synthetic".to_owned(),
                dtrait_header: String::new(),
                ecdh_key: None,
            },
        };
        (dir, store, op, control_sender)
    }

    async fn mock_http(body: Vec<u8>) -> (String, tokio::task::JoinHandle<Vec<u8>>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut stream, _) = tokio::time::timeout(Duration::from_secs(5), listener.accept())
                .await
                .unwrap()
                .unwrap();
            let mut bytes = Vec::new();
            loop {
                let mut chunk = [0_u8; 4096];
                let size = stream.read(&mut chunk).await.unwrap();
                assert_ne!(size, 0);
                bytes.extend_from_slice(&chunk[..size]);
                if let Some(end) = bytes.windows(4).position(|x| x == b"\r\n\r\n") {
                    let headers = String::from_utf8_lossy(&bytes[..end]).to_lowercase();
                    let length: usize = headers
                        .lines()
                        .find_map(|line| line.strip_prefix("content-length: "))
                        .unwrap()
                        .parse()
                        .unwrap();
                    if bytes.len() >= end + 4 + length {
                        break;
                    }
                }
                assert!(bytes.len() < 16384);
            }
            let header = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                body.len()
            );
            stream.write_all(header.as_bytes()).await.unwrap();
            stream.write_all(&body).await.unwrap();
            bytes
        });
        (format!("http://{address}/v1/message/send"), server)
    }

    #[tokio::test]
    async fn live_control_change_and_credential_rotation_block_pending_operation() {
        let (_dir, store, mut operation, control) = setup();
        let sender = sender(store);
        operation.kind = WorkKind::AutomaticReply;
        control.send_modify(|state| state.state.lifecycle = LifecycleState::PausedAuto);
        assert!(!send_allowed(&operation));
        operation.kind = WorkKind::ManualSend;
        assert!(send_allowed(&operation));
        control.send_modify(|state| state.state.credential_generation += 1);
        assert!(!send_allowed(&operation));
        assert!(matches!(
            sender.send(operation).await,
            Err(LiveSendError::AccountState)
        ));
    }

    #[tokio::test]
    async fn expiring_lease_does_not_claim_or_send_and_unknown_is_manual_only() {
        let (_dir, store, mut operation, control) = setup();
        control.send_modify(|c| c.state.send = SendCapability::Unknown);
        assert!(send_allowed(&operation));
        operation.kind = WorkKind::AutomaticReply;
        assert!(send_allowed(&operation));
        operation.kind = WorkKind::ManualSend;
        operation.lease_deadline = Instant::now() + Duration::from_secs(5);
        let id = operation.batch_id.clone();
        assert!(matches!(
            sender(store.clone()).send(operation).await,
            Err(LiveSendError::Fence)
        ));
        assert_eq!(
            store.outbound_batch(&id).unwrap().segments[0].attempt_count,
            0
        );
    }

    #[tokio::test]
    async fn durable_sign_http_decode_confirm_pipeline_preserves_ids() {
        let (_dir, store, op, _control) = setup();
        let batch_id = op.batch_id.clone();
        let original = store.outbound_batch(&batch_id).unwrap();
        let client_id = &original.segments[0].client_message_id;
        let mut inner = Vec::new();
        append_varint_field(&mut inner, 1, 987).unwrap();
        append_string_field(&mut inner, 4, client_id).unwrap();
        let mut body = Vec::new();
        append_bytes_field(&mut body, 100, &inner).unwrap();
        let mut response = Vec::new();
        append_varint_field(&mut response, 3, 0).unwrap();
        append_bytes_field(&mut response, 6, &body).unwrap();
        let (url, server) = mock_http(response).await;
        let mut sender = sender(store.clone());
        sender.http.test_endpoint = Some(url);
        let outcome = sender.send(op).await.unwrap();
        assert_eq!(outcome.classification, DeliveryClass::Delivered);
        let request = server.await.unwrap();
        let request = String::from_utf8_lossy(&request);
        assert!(request.contains("a_bogus="));
        assert!(request
            .to_lowercase()
            .contains("bd-ticket-guard-client-data:"));
        assert!(request.contains(client_id));
        assert!(request.contains("durable synthetic text"));
        assert!(!request.contains("must-be-overwritten"));
        let saved = store.outbound_batch(&batch_id).unwrap();
        assert_eq!(saved.segments[0].status, SegmentStatus::Confirmed);
        assert_eq!(
            saved.segments[0].platform_message_id.as_deref(),
            Some("987")
        );
        assert_eq!(saved.segments[0].attempt_count, 1);
    }

    #[tokio::test]
    async fn malformed_response_is_uncertain_in_durable_store() {
        let (_dir, store, op, _control) = setup();
        let batch_id = op.batch_id.clone();
        let (url, server) = mock_http(b"not protobuf".to_vec()).await;
        let mut sender = sender(store.clone());
        sender.http.test_endpoint = Some(url);
        assert_eq!(
            sender.send(op).await.unwrap().classification,
            DeliveryClass::Uncertain
        );
        server.await.unwrap();
        assert_eq!(
            store.outbound_batch(&batch_id).unwrap().segments[0].status,
            SegmentStatus::Uncertain
        );
    }

    #[tokio::test]
    async fn released_lease_rejects_before_network_attempt() {
        let (_dir, store, op, _control) = setup();
        let id = op.batch_id.clone();
        store.release_account_lease(&op.lease).unwrap();
        let sender = sender(store.clone());
        assert!(matches!(sender.send(op).await, Err(LiveSendError::Fence)));
        assert_eq!(
            store.outbound_batch(&id).unwrap().segments[0].attempt_count,
            0
        );
    }

    #[test]
    fn live_header_set_matches_frozen_python_send_headers() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../protocol-fixtures/douyin_pc_im_http_plan_v1.json"
        )))
        .unwrap();
        let expected = &fixture["happy_cases"][0]["input"]["caller_headers"];
        let actual: Vec<_> = reference_headers()
            .into_iter()
            .map(|header| vec![header.name, header.value])
            .collect();
        assert_eq!(serde_json::to_value(actual).unwrap(), *expected);
    }

    #[test]
    fn imported_macos_browser_identity_is_preserved_for_live_send() {
        let user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36";
        let headers = headers_for_user_agent(user_agent);
        let value = |name: &str| {
            headers
                .iter()
                .find(|header| header.name == name)
                .map(|header| header.value.as_str())
                .unwrap()
        };
        assert_eq!(value("user-agent"), user_agent);
        assert_eq!(value("sec-ch-ua-platform"), "\"macOS\"");
        assert!(value("sec-ch-ua").contains("v=\"152\""));
    }

    #[tokio::test]
    async fn live_send_carries_path_bound_device_trait_header() {
        let (_dir, _store, mut operation, _control) = setup();
        operation.credentials.dtrait_header = "d0_captured_device_trait".to_owned();
        let plan = signed_plan(&NativeSigner::new(1).unwrap(), &operation)
            .await
            .unwrap();
        assert!(plan.headers().iter().any(|header| {
            header.name == "x-tt-session-dtrait" && header.value == "d0_captured_device_trait"
        }));
    }
    #[tokio::test]
    async fn automatic_transport_rejects_unguarded_batch_without_http() {
        let (_dir, store, mut op, _control) = setup();
        op.kind = WorkKind::AutomaticReply;
        let id = op.batch_id.clone();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let mut sender = sender(store.clone());
        sender.http.test_endpoint = Some(format!("http://{}/send", listener.local_addr().unwrap()));
        assert!(matches!(sender.send(op).await, Err(LiveSendError::Fence)));
        assert_eq!(
            store.outbound_batch(&id).unwrap().segments[0].attempt_count,
            0
        );
        assert!(
            tokio::time::timeout(Duration::from_millis(50), listener.accept())
                .await
                .is_err()
        );
    }
    #[tokio::test]
    async fn rejection_keeps_selected_numeric_codes_without_sensitive_response_body() {
        let (_dir, store, operation, _control) = setup();
        store
            .transition_segment(
                &operation.lease,
                &operation.segment_id,
                SegmentTransition::StartAttempt,
            )
            .unwrap();
        let outcome = SendOutcome {
            classification: DeliveryClass::RiskControlled,
            platform_message_id: None,
            response_codes: Some(
                "http=200,outer=Some(0),business=Some(8610),raw_check=Some(2)".into(),
            ),
        };
        sender(store.clone())
            .persist_outcome(
                operation.lease,
                operation.segment_id,
                &outcome,
                &operation.credentials,
            )
            .await
            .unwrap();
        let batch = store.outbound_batch(&operation.batch_id).unwrap();
        assert_eq!(batch.segments[0].attempt_count, 1);
        let error = batch.segments[0].last_error.as_deref().unwrap();
        assert!(error.contains("business=Some(8610)") && error.contains("raw_check=Some(2)"));
        assert!(!error.contains("cookie") && !error.contains("private_key"));
    }
}
