//! Account changes while the service keeps running. Adding, re-logging or
//! removing one account touches only that account: other accounts keep their
//! sockets, leases and send state instead of the whole service restarting.
use super::{shared_http, AccountId, AccountSlot, Context, ManualService, Result};
use crate::{
    protocol::credentials::AccountCredentials,
    runtime::supervisor::AccountSpec,
    state::{AccountRuntimeState, LifecycleState, SendCapability},
};
use std::sync::Arc;

/// What a hot account change did. `RestartRequired` keeps the previous
/// whole-service reload for configurations that cannot change while running.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum HotChange {
    Unchanged,
    Loaded,
    Replaced,
    Removed,
    RestartRequired,
}

impl ManualService {
    /// Loads a newly imported or re-imported account from the registry into the
    /// running service.
    /// # Errors
    /// Rejects unreadable credentials or a runtime that refuses the account.
    pub async fn hot_load_account(&self, id: &str) -> Result<HotChange> {
        let _change = self.inner.account_changes.lock().await;
        let Some(controller) = self.inner.controller.get().cloned() else {
            return Ok(HotChange::RestartRequired);
        };
        if !self.inner.hot_reload {
            return Ok(HotChange::RestartRequired);
        }
        let record = self
            .inner
            .registry
            .list()?
            .into_iter()
            .find(|r| r.id == id && !r.deleted)
            .context("account credentials unavailable")?;
        let existing = self.slot(id).ok();
        if let Some(slot) = &existing {
            if slot.credential_generation == record.generation {
                return Ok(HotChange::Unchanged);
            }
            if slot.canonical_sec_uid != record.sec_uid {
                return Ok(HotChange::RestartRequired);
            }
        }
        let credentials = AccountCredentials::from_state(self.inner.registry.load(id)?)?;
        anyhow::ensure!(
            credentials.expected_sec_uid == record.sec_uid && !record.sec_uid.is_empty(),
            "account scope changed"
        );
        let account = AccountId::new(id)?;
        let slot = self.new_slot(credentials, record.generation)?;
        self.inner
            .workbench
            .ensure_account(id, &slot.canonical_sec_uid)?;
        let current = self
            .inner
            .runtime
            .account_control(&account)
            .await
            .map(|control| *control.borrow());
        // Publish the slot before the new control generation, so work admitted
        // under the new fence never sees the previous session.
        self.accounts_write().insert(account.clone(), slot.clone());
        let spec = next_spec(&account, current, record.generation);
        let replaced = current.is_some();
        if let Err(error) = self.inner.runtime.upsert_account(spec).await {
            match existing {
                Some(previous) => self.accounts_write().insert(account, previous),
                None => self.accounts_write().remove(&account),
            };
            return Err(error);
        }
        if replaced {
            stop_frontier_owner(existing.as_deref()).await;
        } else {
            controller.add_account(id, &record.sec_uid);
        }
        tracing::info!(
            account_id = id,
            generation = record.generation,
            replaced,
            "account loaded without restarting other accounts"
        );
        Ok(if replaced {
            HotChange::Replaced
        } else {
            HotChange::Loaded
        })
    }

    fn new_slot(
        &self,
        credentials: AccountCredentials,
        generation: u64,
    ) -> Result<Arc<AccountSlot>> {
        let http = {
            let mut profiles = self
                .inner
                .http_profiles
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            shared_http(
                &mut profiles,
                &self.inner.http_budget,
                &credentials.user_agent,
            )?
        };
        Ok(Arc::new(AccountSlot::new(
            credentials,
            generation,
            http,
            self.inner.signer.clone(),
        )))
    }

    /// Unloads a retired account: releases its lease, stops its actor and
    /// socket, and forgets its slot. Other accounts are untouched.
    /// # Errors
    /// Returns a runtime that refuses removal.
    pub async fn hot_unload_account(&self, id: &str) -> Result<HotChange> {
        let _change = self.inner.account_changes.lock().await;
        let Some(controller) = self.inner.controller.get().cloned() else {
            return Ok(HotChange::RestartRequired);
        };
        let account = AccountId::new(id)?;
        let Ok(slot) = self.slot(id) else {
            return Ok(HotChange::Unchanged);
        };
        controller.remove_account(id);
        self.inner.runtime.remove_account(&account).await?;
        stop_frontier_owner(Some(&slot)).await;
        self.accounts_write().remove(&account);
        tracing::info!(
            account_id = id,
            "account removed without restarting other accounts"
        );
        Ok(HotChange::Removed)
    }
}

fn next_spec(
    account: &AccountId,
    current: Option<crate::runtime::account::AccountControl>,
    generation: u64,
) -> AccountSpec {
    match current {
        Some(control) => AccountSpec {
            account_id: account.clone(),
            // A new actor generation fences out every in-flight job of the
            // previous credentials; ownership and lease are kept.
            actor_generation: control.actor_generation.saturating_add(1),
            state: AccountRuntimeState {
                credential_generation: generation,
                send: SendCapability::Unknown,
                ..control.state
            },
        },
        None => AccountSpec {
            account_id: account.clone(),
            actor_generation: 1,
            state: AccountRuntimeState {
                lifecycle: LifecycleState::PausedAuto,
                credential_generation: generation,
                ..AccountRuntimeState::default()
            },
        },
    }
}

/// Closes a replaced/removed slot's socket. Its control watch already changed,
/// so the reader is exiting; this only bounds and observes that exit.
async fn stop_frontier_owner(slot: Option<&AccountSlot>) {
    let Some(slot) = slot else {
        return;
    };
    let Some(owner) = slot.frontier.lock().await.take() else {
        return;
    };
    owner.commands.send_replace(None);
    if tokio::time::timeout(std::time::Duration::from_secs(5), owner.join)
        .await
        .is_err()
    {
        tracing::warn!("previous Frontier reader did not stop within 5s");
    }
}
