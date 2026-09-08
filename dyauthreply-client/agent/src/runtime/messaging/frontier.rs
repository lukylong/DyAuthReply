//! One event-driven socket owner per account. Existing central `KeepaliveLease`
//! work sends heartbeat/reconnect signals; no account timer or polling loop.
use super::{
    filter_pending, AccountControl, AdmissionResult, Context, Duration, InboundState,
    ManualService, ReceiveProcessing, Result, Serialize, WorkEnvelope, WorkKind,
};
use crate::{
    protocol::frontier::decode_frame,
    runtime::{
        breaker::DependencyOutcome,
        supervisor::{DependencyAdmission, DependencyKind},
    },
};
use tokio::{sync::watch, task::JoinHandle};
use wreq::ws::{message::Message, WebSocket};

#[derive(Default, Clone, Serialize)]
pub struct FrontierStatus {
    pub connected: bool,
    pub healthy: bool,
    pub frames: u64,
    pub messages: u64,
    pub acknowledgements: u64,
    pub connections: u64,
    pub last_error: Option<String>,
    pub last_control_status: Option<i64>,
    pub last_control_kind: Option<i64>,
    pub consecutive_failures: u32,
    pub retry_at_monotonic_ms: u64,
    #[serde(skip)]
    reconcile_pending: bool,
}
impl FrontierStatus {
    fn connected(&mut self) {
        self.connected = true;
        self.healthy = false;
        self.connections = self.connections.saturating_add(1);
        self.last_error = None;
        self.reconcile_pending = true;
    }
    fn failed(&mut self, now: u64, reason: String) {
        self.consecutive_failures = self.consecutive_failures.saturating_add(1);
        let delay = 2000u64
            .saturating_mul(1u64 << self.consecutive_failures.saturating_sub(1).min(5))
            .min(60_000);
        self.retry_at_monotonic_ms = now.saturating_add(delay);
        self.last_error = Some(reason);
    }
}
pub(super) struct FrontierOwner {
    commands: watch::Sender<Option<u64>>,
    join: JoinHandle<()>,
}
impl ManualService {
    pub(super) async fn maintain_frontier(&self, work: &WorkEnvelope) -> Result<()> {
        if self
            .inner
            .frontier_stopping
            .load(std::sync::atomic::Ordering::Acquire)
        {
            return Ok(());
        }
        let slot = self
            .inner
            .accounts
            .get(&work.account_id)
            .context("account missing")?;
        let mut owner = slot.frontier.lock().await;
        if owner.as_ref().is_some_and(|o| !o.join.is_finished()) {
            self.recover_frontier_gap(work).await;
            if let Some(owner) = owner.as_ref() {
                owner
                    .commands
                    .send_replace(Some(self.inner.runtime.monotonic_now_ms()));
            }
            return Ok(());
        }
        if let Some(old) = owner.take() {
            old.join.await.context("Frontier reader failed")?;
        }
        if slot.frontier_status.lock().await.retry_at_monotonic_ms
            > self.inner.runtime.monotonic_now_ms()
        {
            return Ok(());
        }
        let Some(controller) = self.inner.controller.get() else {
            return Ok(());
        };
        let Some(grant) = controller.authorization(work.account_id.as_str()) else {
            return Ok(());
        };
        if u64::try_from(grant.token.fence_epoch)? != work.lease_epoch {
            return Ok(());
        }
        let control = self
            .inner
            .runtime
            .account_control(&work.account_id)
            .await
            .context("account missing")?;
        if !control.borrow().matches(work) || !control.borrow().allows(WorkKind::Reconcile) {
            return Ok(());
        }
        let DependencyAdmission::Allowed(permit) = self
            .inner
            .runtime
            .try_dependency(&work.account_id, DependencyKind::Transport)
            .await
        else {
            return Ok(());
        };
        let connection = {
            let mut session = slot.session.lock().await;
            self.initialize_identity(work, &mut session).await?;
            session.frontier_socket().await
        };
        let outcome = if connection.is_ok() {
            DependencyOutcome::Success
        } else {
            DependencyOutcome::TransientFailure
        };
        self.inner
            .runtime
            .record_dependency_outcome(permit, outcome)
            .await;
        let Ok(socket) = connection else {
            slot.frontier_status.lock().await.failed(
                self.inner.runtime.monotonic_now_ms(),
                "frontier_handshake_failed".into(),
            );
            return Ok(());
        };
        if self
            .inner
            .frontier_stopping
            .load(std::sync::atomic::Ordering::Acquire)
            || !control.borrow().matches(work)
            || controller.authorization(work.account_id.as_str()).is_none()
        {
            return Ok(());
        }
        self.activate_configured_auto(&work.account_id).await?;
        let (commands, receiver) = watch::channel(Some(self.inner.runtime.monotonic_now_ms()));
        let service = self.clone();
        let task_work = work.clone();
        let join = tokio::spawn(async move {
            service
                .run_frontier(task_work, control, receiver, socket)
                .await;
        });
        *owner = Some(FrontierOwner { commands, join });
        Ok(())
    }
    /// Stops and joins socket owners before lease release/runtime shutdown.
    /// # Errors
    /// A reader panic remains visible instead of silently leaking a socket owner.
    pub async fn stop_frontier(&self) -> Result<()> {
        self.inner
            .frontier_stopping
            .store(true, std::sync::atomic::Ordering::Release);
        let mut joins = Vec::new();
        for slot in self.inner.accounts.values() {
            if let Some(owner) = slot.frontier.lock().await.take() {
                owner.commands.send_replace(None);
                joins.push(owner.join);
            }
        }
        for join in joins {
            join.await.context("Frontier reader shutdown failed")?;
        }
        Ok(())
    }
    /// Closes local UI notification sockets only during whole-process shutdown.
    /// Emergency account stop deliberately leaves this channel available for restart commands.
    pub fn stop_notifications(&self) {
        let _ = self
            .inner
            .workbench
            .changed
            .send(serde_json::json!({"type":"shutdown"}));
    }
    async fn run_frontier(
        &self,
        work: WorkEnvelope,
        mut control: watch::Receiver<AccountControl>,
        mut commands: watch::Receiver<Option<u64>>,
        mut socket: WebSocket,
    ) {
        let slot = &self.inner.accounts[&work.account_id];
        {
            let mut status = slot.frontier_status.lock().await;
            status.connected();
        }
        // A healthy new socket says nothing about messages missed while it
        // was disconnected. Repair via the durable HTTP cursor after connect,
        // not only before reconnect (which leaves a receive gap).
        self.recover_frontier_gap(&work).await;
        let result = self
            .receive_frontier(&work, &mut control, &mut commands, &mut socket)
            .await;
        {
            let mut status = slot.frontier_status.lock().await;
            status.connected = false;
            status.healthy = false;
            if let Err(error) = &result {
                status.failed(self.inner.runtime.monotonic_now_ms(), error.to_string());
            }
        }
        let _ =
            tokio::time::timeout(Duration::from_secs(2), socket.close(1000u16, "closing")).await;
        let _ = self
            .inner
            .runtime
            .update_inbound_state(&work.account_id, work.fence(), InboundState::HttpDegraded)
            .await;
        if !self
            .inner
            .frontier_stopping
            .load(std::sync::atomic::Ordering::Acquire)
        {
            let _ = self.reconcile(work.account_id.to_string()).await;
        }
    }
    async fn recover_frontier_gap(&self, work: &WorkEnvelope) {
        let status = &self.inner.accounts[&work.account_id].frontier_status;
        let pending = status.lock().await.reconcile_pending;
        if pending && self.reconcile(work.account_id.to_string()).await.is_ok() {
            status.lock().await.reconcile_pending = false;
        }
        // A full queue retains pending=true; the existing central keepalive
        // retries admission. No extra timers or whole-account scans are added.
    }

    async fn receive_frontier(
        &self,
        work: &WorkEnvelope,
        control: &mut watch::Receiver<AccountControl>,
        commands: &mut watch::Receiver<Option<u64>>,
        socket: &mut WebSocket,
    ) -> Result<()> {
        let connected_at = self.inner.runtime.monotonic_now_ms();
        let mut pending = Some(connected_at);
        send(
            socket,
            Message::Ping(pending.unwrap().to_le_bytes().to_vec().into()),
        )
        .await?;
        loop {
            if !control.borrow().matches(work)
                || !control.borrow().allows(WorkKind::Reconcile)
                || commands.borrow().is_none()
            {
                return Ok(());
            }
            anyhow::ensure!(
                self.inner
                    .controller
                    .get()
                    .and_then(|c| c.authorization(work.account_id.as_str()))
                    .is_some(),
                "Frontier lease expired"
            );
            tokio::select! {
                biased;
                changed=control.changed()=>{if changed.is_err(){return Ok(());}},
                changed=commands.changed()=>{
                    if changed.is_err(){return Ok(());}
                    let value=*commands.borrow_and_update();
                    let Some(now)=value else{return Ok(());};
                    anyhow::ensure!(pending.is_none_or(|at|now.saturating_sub(at)<15_000),"Frontier pong timeout");
                    if pending.is_none(){send(socket,Message::Ping(now.to_le_bytes().to_vec().into())).await?;pending=Some(now);}
                },
                message=socket.recv()=>{
                    let message=message.context("Frontier closed")?.map_err(|_|anyhow::anyhow!("Frontier read failed"))?;
                    match message {
                        Message::Pong(bytes)=>{
                            if pending.is_some_and(|nonce|bytes.as_ref()==nonce.to_le_bytes()){
                                pending=None;
                                let mut status=self.inner.accounts[&work.account_id].frontier_status.lock().await;
                                status.healthy=true;
                                if self.inner.runtime.monotonic_now_ms().saturating_sub(connected_at)>=20_000 {status.consecutive_failures=0;status.retry_at_monotonic_ms=0;}
                                drop(status);
                                self.inner.runtime.update_inbound_state(&work.account_id,work.fence(),InboundState::WsHealthy).await?;
                            }
                        },
                        Message::Ping(bytes)=>send(socket,Message::Pong(bytes)).await?,
                        Message::Binary(bytes)=>self.accept_frontier(work,bytes.as_ref(),socket).await?,
                        Message::Close(_)=>return Ok(()),
                        Message::Text(_)=>anyhow::bail!("unsupported Frontier text frame"),
                    }
                }
            }
        }
    }
    async fn accept_frontier(
        &self,
        work: &WorkEnvelope,
        bytes: &[u8],
        socket: &mut WebSocket,
    ) -> Result<()> {
        let permit = self.inner.frontier_decode.clone().acquire_owned().await?;
        let data = bytes.to_vec();
        let frame = tokio::task::spawn_blocking(move || {
            let _permit = permit;
            decode_frame(&data)
        })
        .await??;
        let count = frame.messages.len();
        if let Some(status) = frame.control_status {
            self.inner.accounts[&work.account_id]
                .frontier_status
                .lock()
                .await
                .last_control_kind = frame.control_kind;
            self.inner.accounts[&work.account_id]
                .frontier_status
                .lock()
                .await
                .last_control_status = Some(status);
        }
        let grant = self
            .inner
            .controller
            .get()
            .and_then(|c| c.authorization(work.account_id.as_str()))
            .context("Frontier lease unavailable")?;
        anyhow::ensure!(
            u64::try_from(grant.token.fence_epoch)? == work.lease_epoch,
            "stale Frontier lease"
        );
        let own = self.inner.accounts[&work.account_id]
            .canonical_sec_uid
            .clone();
        let store = self.inner.store.clone();
        let generation = i64::try_from(work.credential_generation)?;
        let projection = self.workbench();
        let summary = tokio::task::spawn_blocking(move || {
            persist_messages(
                &store,
                &grant.token,
                generation,
                &own,
                frame.messages,
                &projection,
            )
        })
        .await??;
        *self.inner.accounts[&work.account_id]
            .processing
            .lock()
            .await = summary;
        if let Some(ack) = frame.ack {
            send(socket, Message::Binary(ack.into())).await?;
            self.inner.accounts[&work.account_id]
                .frontier_status
                .lock()
                .await
                .acknowledgements += 1;
        }
        let sequence = {
            let mut status = self.inner.accounts[&work.account_id]
                .frontier_status
                .lock()
                .await;
            status.frames += 1;
            status.messages += u64::try_from(count)?;
            status.frames
        };
        if count > 0 {
            let queued = self
                .inner
                .runtime
                .enqueue(WorkEnvelope::new(
                    work.account_id.clone(),
                    format!("ws-{sequence}"),
                    WorkKind::InboundWakeup,
                    work.fence(),
                    self.inner.runtime.monotonic_now_ms(),
                ))
                .await;
            anyhow::ensure!(
                matches!(
                    queued,
                    AdmissionResult::Accepted | AdmissionResult::Duplicate
                ),
                "Frontier queue full; durable HTTP recovery required"
            );
        }
        Ok(())
    }
}
fn persist_messages(
    store: &crate::store::CoreStore,
    lease: &crate::store::LeaseToken,
    generation: i64,
    own: &str,
    messages: Vec<crate::protocol::inbox::InboxMessage>,
    projection: &crate::workbench::Workbench,
) -> Result<ReceiveProcessing> {
    let cutoff = u64::try_from(store.ensure_inbound_live_start(lease, generation)?)?;
    let before = filter_pending(store, lease, own, cutoff, Some(projection))?;
    let page = crate::protocol::inbox::InboxPage {
        status_code: 0,
        status_message: String::new(),
        wrapper_present: true,
        next_cursor: 0,
        messages,
    };
    let observed = super::super::inbound::reconcile_page_receipts(store, lease, own, &page)?;
    super::super::inbound::commit_frontier(store, lease, generation, page.messages)?;
    let mut after = filter_pending(store, lease, own, cutoff, Some(projection))?;
    after.reconciled_sends += before.reconciled_sends + observed.confirmed;
    after.receipt_conflicts += before.receipt_conflicts + observed.conflicts;
    Ok(after)
}

async fn send(socket: &mut WebSocket, message: Message) -> Result<()> {
    tokio::time::timeout(Duration::from_secs(5), socket.send(message))
        .await
        .context("Frontier write timed out")?
        .map_err(|_| anyhow::anyhow!("Frontier write failed"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn every_new_socket_requires_gap_reconciliation() {
        let mut status = FrontierStatus::default();
        status.connected();
        assert!(status.reconcile_pending);
        assert!(!status.healthy);
        status.reconcile_pending = false;
        status.healthy = true;
        status.connected();
        assert_eq!(status.connections, 2);
        assert!(status.reconcile_pending);
        assert!(!status.healthy);
    }
    #[test]
    fn connection_failures_back_off_without_own_timers_and_cap_at_one_minute() {
        let mut status = FrontierStatus::default();
        status.failed(100, "failure".into());
        assert_eq!(status.retry_at_monotonic_ms, 2100);
        status.failed(200, "failure".into());
        assert_eq!(status.retry_at_monotonic_ms, 4200);
        for _ in 0..40 {
            status.failed(1000, "failure".into());
        }
        assert_eq!(status.retry_at_monotonic_ms, 61_000);
    }
}
