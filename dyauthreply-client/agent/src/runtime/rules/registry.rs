//! Atomic whole-revision publication; readers pin immutable compiled snapshots.
use super::RuleEngine;
use anyhow::{Context, Result};
use std::sync::{Arc, RwLock};
#[derive(Default)]
pub struct RuleRegistry {
    current: RwLock<Option<Arc<RuleEngine>>>,
}
impl RuleRegistry {
    /// # Errors
    /// Rejects stale/conflicting revisions or an unavailable registry.
    pub fn publish(&self, engine: Arc<RuleEngine>) -> Result<()> {
        let mut slot = self
            .current
            .write()
            .ok()
            .context("rule registry unavailable")?;
        if let Some(previous) = slot.as_ref() {
            anyhow::ensure!(engine.revision >= previous.revision, "stale rule revision");
            if engine.revision == previous.revision {
                anyhow::ensure!(
                    engine.digest == previous.digest,
                    "same rule revision has different contents"
                );
                return Ok(());
            }
        }
        *slot = Some(engine);
        Ok(())
    }
    /// # Errors
    /// Reports a poisoned registry rather than substituting an empty rule set.
    pub fn snapshot(&self) -> Result<Option<Arc<RuleEngine>>> {
        Ok(self
            .current
            .read()
            .ok()
            .context("rule registry unavailable")?
            .clone())
    }
}
