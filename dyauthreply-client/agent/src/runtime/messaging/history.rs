//! On-demand read repair for an opened conversation. Projection only: old
//! messages must never become new automatic-reply triggers or advance inbox cursors.
use super::{Context, ManualService, Result};
use std::{
    collections::BTreeMap,
    sync::Arc,
    time::{Duration, Instant},
};

fn claim(cache: &mut BTreeMap<String, Instant>, key: &str, now: Instant) -> bool {
    if cache
        .get(key)
        .is_some_and(|last| now.saturating_duration_since(*last) < Duration::from_secs(5))
    {
        return false;
    }
    if cache.len() >= 16 {
        if let Some(oldest) = cache
            .iter()
            .min_by_key(|(_, at)| **at)
            .map(|(key, _)| key.clone())
        {
            cache.remove(&oldest);
        }
    }
    cache.insert(key.into(), now);
    true
}
impl ManualService {
    /// Fetches at most 20 recent messages for an already-owned local conversation.
    /// # Errors
    /// Rejects unknown routes, stale credential slots and failed read-only fetches.
    pub async fn refresh_conversation_history(
        &self,
        account: &str,
        conversation: &str,
    ) -> Result<bool> {
        let _transport = self.native_transport(account).await?;
        // A credential replacement or removal cannot race the final projection.
        let _configuration = self
            .inner
            .configuration_gate
            .try_read()
            .context("conversation history configuration is changing")?;
        let db = self.workbench();
        let aid = account.to_owned();
        let cid = conversation.to_owned();
        let (id, short) = tokio::task::spawn_blocking(move || db.route(&aid, &cid)).await??;
        let short = short.parse::<u64>()?;
        let slot = self.slot(account)?;
        let Ok(mut throttle) = slot.history_refresh.try_lock() else {
            return Ok(false);
        };
        if !claim(&mut throttle, conversation, Instant::now()) {
            return Ok(false);
        }
        let messages = tokio::time::timeout(Duration::from_secs(4), async {
            slot.session
                .lock()
                .await
                .conversation_history(&id, short)
                .await
        })
        .await
        .context("conversation history timeout")??;
        anyhow::ensure!(
            Arc::ptr_eq(&slot, &self.slot(account)?),
            "conversation history credentials changed"
        );
        let events: Vec<_> = messages
            .iter()
            .map(crate::runtime::inbound::InboundEvent::from)
            .collect();
        let db = self.workbench();
        let aid = account.to_owned();
        let own = slot.canonical_sec_uid.clone();
        tokio::task::spawn_blocking(move || db.project(&aid, &own, &events)).await??;
        Ok(true)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn history_reads_are_throttled_and_bounded_per_account() {
        let now = Instant::now();
        let mut cache = BTreeMap::new();
        assert!(claim(&mut cache, "one", now));
        assert!(!claim(&mut cache, "one", now));
        assert!(claim(&mut cache, "one", now + Duration::from_secs(5)));
        let mut other_account = BTreeMap::new();
        assert!(claim(&mut other_account, "one", now));
        for i in 0..32 {
            claim(
                &mut cache,
                &format!("chat-{i}"),
                now + Duration::from_secs(6),
            );
        }
        assert_eq!(cache.len(), 16);
    }
}
