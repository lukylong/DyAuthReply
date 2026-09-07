//! Bounded account automation policy. No process/thread/timer per account.
use super::{
    AccountId, AdmissionResult, Context, Deserialize, ManualService, OutboundSegmentDraft, Result,
    Route, SystemTime, WorkEnvelope, WorkKind, UNIX_EPOCH,
};
use crate::{
    runtime::{
        inbound::InboundEvent,
        inbound_policy::{eligibility, Eligibility},
        rules::{MatchInput, ReplyPlan, RuleEngine},
    },
    store::{
        CoreStore, GuardPolicy, InboundReceipt, InboundReceiptKey, InboundReplyPlan, LeaseToken,
        StoreError,
    },
};
use chrono::{DateTime, NaiveTime, Utc};
use chrono_tz::Tz;
use sha2::{Digest, Sha256};
#[derive(Clone, Deserialize, serde::Serialize)]
#[serde(deny_unknown_fields)]
pub struct AutomationPolicy {
    pub account_id: String,
    pub enabled: bool,
    #[serde(default)]
    pub enabled_since_us: u64,
    pub daily_quota: u32,
    pub min_interval_seconds: u32,
    pub max_interval_seconds: u32,
    #[serde(default)]
    pub silent_start: Option<String>,
    #[serde(default)]
    pub silent_end: Option<String>,
    #[serde(default)]
    pub daily_peer_limit: bool,
    #[serde(default)]
    pub blocked_peers: Vec<String>,
    #[serde(default)]
    pub blocked_content_keywords: Vec<String>,
    #[serde(default)]
    pub blocked_nickname_keywords: Vec<String>,
}
impl AutomationPolicy {
    pub(crate) fn validate(&self) -> Result<()> {
        anyhow::ensure!(
            !self.account_id.is_empty()
                && self.daily_quota <= 1_000_000
                && self.min_interval_seconds <= self.max_interval_seconds
                && self.max_interval_seconds <= 86400
                && self.blocked_content_keywords.len() <= 1000
                && self.blocked_nickname_keywords.len() <= 1000
                && self
                    .blocked_content_keywords
                    .iter()
                    .chain(&self.blocked_nickname_keywords)
                    .all(|v| !v.trim().is_empty() && v.len() <= 1024)
                && self.blocked_peers.len() <= 10000
                && self
                    .blocked_peers
                    .iter()
                    .all(|p| !p.is_empty() && p.len() <= 256),
            "invalid automatic account policy"
        );
        for v in [&self.silent_start, &self.silent_end].into_iter().flatten() {
            parse_time(v)?;
        }
        Ok(())
    }
    pub(super) fn silent(&self, timezone: &str, now: i64) -> Result<bool> {
        let (Some(start), Some(end)) = (&self.silent_start, &self.silent_end) else {
            return Ok(false);
        };
        let local = DateTime::<Utc>::from_timestamp_millis(now)
            .context("invalid policy time")?
            .with_timezone(&timezone.parse::<Tz>()?);
        let (start, end) = (parse_time(start)?, parse_time(end)?);
        let t = local.time();
        Ok(if start <= end {
            start <= t && t <= end
        } else {
            t >= start || t <= end
        })
    }
    pub(super) fn interval_ms(&self, event_id: &str) -> u64 {
        let bytes = Sha256::digest(format!("{}/{}", self.account_id, event_id));
        let number = u64::from_le_bytes(bytes[..8].try_into().expect("digest prefix"));
        1000 * (u64::from(self.min_interval_seconds)
            + number % u64::from(self.max_interval_seconds - self.min_interval_seconds + 1))
    }
}
fn parse_time(v: &str) -> Result<NaiveTime> {
    NaiveTime::parse_from_str(v, "%H:%M:%S")
        .or_else(|_| NaiveTime::parse_from_str(v, "%H:%M"))
        .context("invalid silent window")
}
impl ManualService {
    pub(super) async fn plan_automatic(&self, work: &WorkEnvelope) -> Result<()> {
        let _configuration = self.inner.configuration_gate.read().await;
        let config = self.inner.business.snapshot()?;
        let Some(policy) = config
            .policies
            .get(work.account_id.as_str())
            .filter(|p| p.enabled)
            .cloned()
        else {
            return Ok(());
        };
        let state = self
            .inner
            .runtime
            .account_control(&work.account_id)
            .await
            .context("account unavailable")?;
        if !state.borrow().matches(work) || !state.borrow().state.can_attempt_auto_reply() {
            return Ok(());
        }
        let grant = self.authorized(work.account_id.as_str(), WorkKind::AutomaticReply)?;
        let engine = config.engine.clone();
        let now = i64::try_from(SystemTime::now().duration_since(UNIX_EPOCH)?.as_millis())?;
        let own = self.inner.accounts[&work.account_id]
            .session
            .lock()
            .await
            .credentials
            .expected_sec_uid
            .clone();
        let store = self.inner.store.clone();
        let generation = i64::try_from(work.credential_generation)?;
        let workbench = self.workbench();
        let batches = tokio::task::spawn_blocking(move || -> Result<Vec<String>> {
            let cutoff = u64::try_from(store.ensure_inbound_live_start(&grant.token, generation)?)?
                .max(policy.enabled_since_us);
            PlanPass {
                workbench: Some(&workbench),
                store: &store,
                lease: &grant.token,
                policy: &policy,
                engine: &engine,
                own: &own,
                cutoff,
                now,
            }
            .run()
        })
        .await??;
        for batch in batches {
            *self.inner.accounts[&work.account_id]
                .last_automatic_command
                .lock()
                .await = Some(batch.clone());
            let queued = self
                .inner
                .runtime
                .enqueue(WorkEnvelope::new(
                    work.account_id.clone(),
                    batch,
                    WorkKind::AutomaticReply,
                    work.fence(),
                    self.inner.runtime.monotonic_now_ms(),
                ))
                .await;
            anyhow::ensure!(
                matches!(
                    queued,
                    AdmissionResult::Accepted | AdmissionResult::Duplicate
                ),
                "automatic queue unavailable; durable recovery required"
            );
        }
        Ok(())
    }
    pub(super) async fn activate_configured_auto(&self, id: &AccountId) -> Result<()> {
        let _configuration = self.inner.configuration_gate.read().await;
        if self
            .inner
            .business
            .snapshot()?
            .policies
            .get(id.as_str())
            .is_some_and(|p| p.enabled)
        {
            self.inner.runtime.activate_automatic_account(id).await?;
        }
        Ok(())
    }
}

struct PlanPass<'a> {
    workbench: Option<&'a crate::workbench::Workbench>,
    store: &'a CoreStore,
    lease: &'a LeaseToken,
    policy: &'a AutomationPolicy,
    engine: &'a RuleEngine,
    own: &'a str,
    cutoff: u64,
    now: i64,
}
enum Planned {
    Queued(String),
    Dropped,
    Blocked,
}
impl PlanPass<'_> {
    fn run(&self) -> Result<Vec<String>> {
        let mut batches = Vec::new();
        for receipt in self.store.pending_inbound_receipts(self.lease, 128)? {
            let Some(payload) = receipt.payload.as_ref() else {
                continue;
            };
            let Ok(event) = serde_json::from_slice::<InboundEvent>(payload) else {
                continue;
            };
            let decision = eligibility(
                &event,
                self.own,
                self.cutoff,
                u64::try_from(self.now)? * 1000,
            );
            if decision != Eligibility::Candidate {
                if decision.is_terminal_ignore() {
                    self.store.consume_inbound_audited(
                        self.lease,
                        key(&receipt),
                        crate::store::DecisionAudit {
                            result: "skipped",
                            reason: "启用前或已过期消息，不补发",
                            rule_id: None,
                        },
                    )?;
                }
                continue;
            }
            if self.policy.silent(self.engine.timezone_name(), self.now)? {
                self.store.observe_audit_wait(
                    self.lease,
                    key(&receipt),
                    crate::store::DecisionAudit {
                        result: "silent",
                        reason: "静默时段，暂缓处理",
                        rule_id: None,
                    },
                )?;
                continue;
            }
            let nickname = self
                .workbench
                .map(|w| w.peer_nickname(&self.lease.account_id, &event.conversation_id))
                .transpose()?
                .flatten();
            if nickname.is_none() && !self.policy.blocked_nickname_keywords.is_empty() {
                self.store.observe_audit_wait(
                    self.lease,
                    key(&receipt),
                    crate::store::DecisionAudit {
                        result: "pending",
                        reason: "对方资料尚未就绪，暂缓匹配",
                        rule_id: None,
                    },
                )?;
                continue;
            }
            let text = event.text.clone().unwrap_or_default();
            let plan = if self.policy.blocked_peers.contains(&event.sender_sec_uid)
                || self
                    .policy
                    .blocked_content_keywords
                    .iter()
                    .any(|k| text.to_lowercase().contains(&k.to_lowercase()))
                || self.policy.blocked_nickname_keywords.iter().any(|k| {
                    nickname
                        .as_deref()
                        .unwrap_or("")
                        .to_lowercase()
                        .contains(&k.to_lowercase())
                }) {
                None
            } else {
                self.engine.evaluate(&MatchInput {
                    account_id: self.lease.account_id.clone(),
                    text,
                    channel: "dm".into(),
                    at_ms: self.now,
                    peer_nickname: nickname.unwrap_or_default(),
                })?
            };
            let Some(plan) = plan.filter(|p| !p.segments.is_empty()) else {
                self.store.consume_inbound_audited(
                    self.lease,
                    key(&receipt),
                    crate::store::DecisionAudit {
                        result: "skipped",
                        reason: "未命中规则或命中黑名单",
                        rule_id: None,
                    },
                )?;
                continue;
            };
            match self.claim(&receipt, event, plan)? {
                Planned::Queued(id) => batches.push(id),
                Planned::Dropped => {}
                Planned::Blocked => break,
            }
        }
        Ok(batches)
    }
    fn claim(
        &self,
        receipt: &InboundReceipt,
        event: InboundEvent,
        plan: ReplyPlan,
    ) -> Result<Planned> {
        let route = Route {
            version: 2,
            conversation_id: event.conversation_id.clone(),
            short_id: event.conversation_short_id.parse()?,
            automatic: true,
            rule_id: Some(plan.rule_id.clone()),
            rule_revision: Some(plan.revision),
            expires_at_ms: Some(
                i64::try_from(event.create_time_us / 1000)?.saturating_add(300_000),
            ),
        };
        let segments: Vec<_> = plan
            .segments
            .into_iter()
            .map(OutboundSegmentDraft::text)
            .collect();
        let route = serde_json::to_string(&route)?;
        let guard = GuardPolicy {
            rule_id: plan.rule_id,
            conversation_id: event.conversation_id,
            peer_id: event.sender_sec_uid,
            timezone: self.engine.timezone_name().into(),
            daily_quota: self.policy.daily_quota,
            cooldown_ms: u64::from(plan.cooldown_seconds) * 1000,
            minimum_interval_ms: self.policy.interval_ms(&event.server_message_id),
            daily_peer_limit: self.policy.daily_peer_limit,
        };
        match self.store.consume_inbound_guarded(
            self.lease,
            key(receipt),
            InboundReplyPlan {
                response_id: &route,
                segments: &segments,
                pending_batch_limit: 32,
            },
            &guard,
        ) {
            Ok(outcome) => Ok(outcome
                .batch
                .map_or(Planned::Dropped, |b| Planned::Queued(b.id))),
            Err(StoreError::ReplyGuardBlocked {
                reason: reason @ ("rule_cooldown" | "account_daily_quota" | "peer_daily_quota"),
                ..
            }) => {
                self.store.consume_inbound_audited(
                    self.lease,
                    key(receipt),
                    crate::store::DecisionAudit {
                        result: if reason == "rule_cooldown" {
                            "cooldown"
                        } else {
                            "quota_exceeded"
                        },
                        reason,
                        rule_id: Some(&guard.rule_id),
                    },
                )?;
                Ok(Planned::Dropped)
            }
            Err(StoreError::ReplyGuardBlocked { .. }) => Ok(Planned::Blocked),
            Err(e) => Err(e.into()),
        }
    }
}
fn key(receipt: &InboundReceipt) -> InboundReceiptKey<'_> {
    InboundReceiptKey {
        stream: &receipt.stream,
        generation: receipt.stream_generation,
        event_id: &receipt.event_id,
        payload_hash: &receipt.payload_hash,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn policy() -> AutomationPolicy {
        AutomationPolicy {
            account_id: "account".into(),
            enabled: true,
            enabled_since_us: 0,
            daily_quota: 200,
            min_interval_seconds: 8,
            max_interval_seconds: 25,
            silent_start: Some("22:00".into()),
            silent_end: Some("08:00".into()),
            daily_peer_limit: false,
            blocked_peers: vec![],
            blocked_content_keywords: vec![],
            blocked_nickname_keywords: vec![],
        }
    }
    #[test]
    fn silent_window_and_deterministic_interval_preserve_account_bounds() {
        let policy = policy();
        policy.validate().unwrap();
        let night = chrono::DateTime::parse_from_rfc3339("2026-09-05T15:00:00Z")
            .unwrap()
            .timestamp_millis();
        assert!(policy.silent("Asia/Shanghai", night).unwrap());
        assert!(!policy
            .silent("Asia/Shanghai", night - 8 * 3_600_000)
            .unwrap());
        for i in 0..1000 {
            let id = i.to_string();
            let delay = policy.interval_ms(&id);
            assert!((8000..=25000).contains(&delay));
            assert_eq!(delay, policy.interval_ms(&id));
        }
    }
    #[test]
    fn candidate_to_guarded_auto_route_is_durable_and_idempotent() {
        let dir = tempfile::tempdir().unwrap();
        let store = CoreStore::open(dir.path()).unwrap();
        let now = i64::try_from(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_millis(),
        )
        .unwrap();
        let lease = store
            .install_verified_account_lease("account", "instance", "boot", 1, now + 60_000)
            .unwrap()
            .token();
        let mut policy = policy();
        policy.silent_start = None;
        policy.silent_end = None;
        let raw = serde_json::json!({"version":1,"revision":1,"timezone":"Asia/Shanghai","rules":[{"id":"r","match_type":"contains","keywords":["hello"],"reply_text":"reply"}]});
        let engine = RuleEngine::from_json(&serde_json::to_vec(&raw).unwrap()).unwrap();
        let event = InboundEvent {
            version: 1,
            server_message_id: "123".into(),
            conversation_id: "conversation".into(),
            conversation_short_id: "456".into(),
            sender_uid: "42".into(),
            sender_sec_uid: "peer".into(),
            client_message_id: String::new(),
            message_type: 1,
            create_time_us: u64::try_from(now).unwrap() * 1000,
            content_json: r#"{"text":"hello"}"#.into(),
            text: Some("hello".into()),
        };
        let cutoff = u64::try_from(store.ensure_inbound_live_start(&lease, 1).unwrap()).unwrap();
        let message = crate::protocol::inbox::InboxMessage {
            conversation_id: event.conversation_id.clone(),
            conversation_short_id: 456,
            server_message_id: 123,
            sender_uid: 42,
            sender_sec_uid: event.sender_sec_uid.clone(),
            client_message_id: event.client_message_id.clone(),
            message_type: event.message_type,
            create_time_us: cutoff + 1,
            content_json: event.content_json.clone(),
            text: event.text.clone(),
        };
        let result = crate::runtime::inbound::commit_page(
            &store,
            &lease,
            1,
            None,
            crate::protocol::inbox::InboxPage {
                status_code: 0,
                status_message: String::new(),
                next_cursor: 100,
                wrapper_present: true,
                messages: vec![message],
            },
        )
        .unwrap();
        assert!(result.baseline);
        assert_eq!(result.inserted, 1);
        let pass = PlanPass {
            workbench: None,
            store: &store,
            lease: &lease,
            policy: &policy,
            engine: &engine,
            own: "self",
            cutoff,
            now: i64::try_from(cutoff / 1000).unwrap() + 1,
        };
        let ids = pass.run().unwrap();
        assert_eq!(ids.len(), 1);
        let batch = store.outbound_batch(&ids[0]).unwrap();
        let route: Route = serde_json::from_str(&batch.response_id).unwrap();
        assert_eq!(route.kind().unwrap(), WorkKind::AutomaticReply);
        assert_eq!(batch.segments[0].payload, "reply");
        store
            .transition_segment(
                &lease,
                &batch.segments[0].id,
                crate::store::SegmentTransition::StartAutomaticAttempt,
            )
            .unwrap();
        assert!(pass.run().unwrap().is_empty());
    }
    #[test]
    fn resume_boundary_and_blacklist_do_not_plan_old_or_blocked_messages() {
        for mode in ["before_enable", "content_block", "unknown_nickname"] {
            let dir = tempfile::tempdir().unwrap();
            let store = CoreStore::open(dir.path()).unwrap();
            let now = crate::workbench::now_ms();
            let lease = store
                .install_verified_account_lease("account", "i", "b", 1, now + 60000)
                .unwrap()
                .token();
            let mut policy = policy();
            policy.silent_start = None;
            policy.silent_end = None;
            if mode == "content_block" {
                policy.blocked_content_keywords.push("HELLO".into());
            }
            if mode == "unknown_nickname" {
                policy.blocked_nickname_keywords.push("advert".into());
            }
            let event = serde_json::json!({"version":1,"server_message_id":"123","conversation_id":"c","conversation_short_id":"456","sender_uid":"42","sender_sec_uid":"peer","client_message_id":"","message_type":1,"create_time_us":(now-1000)*1000,"content_json":"{\"text\":\"hello\"}","text":"hello"});
            let payload = serde_json::to_vec(&event).unwrap();
            let hash = format!("{:x}", Sha256::digest(&payload));
            store
                .record_inbound_page(
                    &lease,
                    "s",
                    1,
                    1,
                    &[crate::store::InboundReceiptDraft {
                        event_id: "123".into(),
                        payload,
                        payload_hash: hash,
                    }],
                )
                .unwrap();
            let engine=RuleEngine::from_json(br#"{"version":1,"revision":2,"timezone":"Asia/Shanghai","rules":[{"id":"r","match_type":"default","reply_text":"reply"}]}"#).unwrap();
            let pass = PlanPass {
                workbench: None,
                store: &store,
                lease: &lease,
                policy: &policy,
                engine: &engine,
                own: "self",
                cutoff: u64::try_from(if mode == "before_enable" {
                    now - 500
                } else {
                    now - 2000
                })
                .unwrap()
                    * 1000,
                now,
            };
            assert!(pass.run().unwrap().is_empty());
            assert_eq!(
                store.pending_inbound_receipts(&lease, 128).unwrap().len(),
                usize::from(mode == "unknown_nickname")
            );
        }
    }
}
