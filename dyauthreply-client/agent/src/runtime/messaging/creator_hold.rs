//! Account-level browser takeover. Durable disabled policy survives crashes.
use super::{ManualService, Result, WorkKind};

impl ManualService {
    pub(crate) fn creator_held(&self, id: &str) -> bool {
        self.inner
            .creator_holds
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .contains_key(id)
    }

    pub(crate) async fn begin_creator_hold(&self, id: &str, confirmed: bool) -> Result<bool> {
        self.authorized(id, WorkKind::Reconcile)?;
        let original;
        {
            let _gate = self.inner.configuration_gate.write().await;
            original = self
                .business_snapshot()?
                .policies
                .get(id)
                .ok_or_else(|| anyhow::anyhow!("账号策略不存在"))?
                .enabled;
            anyhow::ensure!(
                !original || confirmed,
                "请确认暂停此账号自动回复后再进入平台"
            );
            let mut held = self
                .inner
                .creator_holds
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            anyhow::ensure!(!held.contains_key(id), "该账号正在人工接管");
            held.insert(id.into(), original);
        }
        if let Err(error) = self
            .change_business_inner(
                crate::business::Edit::Account {
                    id: id.into(),
                    input: serde_json::json!({"auto_reply_enabled":false}),
                },
                true,
            )
            .await
        {
            self.release_creator_hold(id);
            return Err(error);
        }
        Ok(original)
    }

    pub(crate) fn release_creator_hold(&self, id: &str) {
        self.inner
            .creator_holds
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .remove(id);
    }

    /// Ending a browser session never opts the user back into automatic sending.
    pub(crate) async fn finish_creator_hold(&self, id: &str) -> Result<()> {
        let result = match self.business_snapshot() {
            Ok(snapshot)
                if snapshot
                    .policies
                    .get(id)
                    .is_some_and(|policy| policy.enabled) =>
            {
                self.change_business_inner(
                    crate::business::Edit::Account {
                        id: id.into(),
                        input: serde_json::json!({"auto_reply_enabled":false}),
                    },
                    true,
                )
                .await
                .map(|_| ())
            }
            Ok(_) => Ok(()),
            Err(error) => Err(error),
        };
        self.release_creator_hold(id);
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        business::Edit,
        config::{RuntimeConfig, StorageConfig},
        credential_store::registry::Registry,
        health::{HealthHandle, HealthResponse, StorageHealthResponse, StorageStartupSnapshot},
        protocol::{credentials::CredentialImport, fixtures::verify_embedded_corpus},
        runtime::{
            messaging::{AutomationPolicy, MessagingSettings},
            supervisor::RuntimeHandle,
        },
        storage::{retention::DiskPressure, RecoveryReport, SegmentCatalog, SegmentStore},
        store::CoreStore,
    };
    use serde_json::json;
    use std::sync::Arc;
    use uuid::Uuid;

    fn fixture(enabled: bool) -> (tempfile::TempDir, Arc<ManualService>, RuntimeHandle, String) {
        let root = tempfile::tempdir().unwrap();
        let core = Arc::new(CoreStore::open(root.path()).unwrap());
        let storage = StorageConfig::recommended();
        let catalog: Arc<dyn SegmentCatalog> = core.clone();
        let segments = SegmentStore::open(
            root.path().join("segments"),
            storage.segment_policies.clone(),
            catalog,
        )
        .unwrap();
        let parity = verify_embedded_corpus().unwrap();
        let health = HealthHandle::new(HealthResponse::foundation(
            Uuid::new_v4(),
            Uuid::new_v4(),
            &parity,
            StorageHealthResponse::startup(&StorageStartupSnapshot {
                pressure: DiskPressure::Normal,
                disposable_writes_allowed: true,
                background_work_paused: false,
                sealed_segment_count: 0,
                sealed_segment_bytes: 0,
                active_segment_count: 0,
                cleanup_deleted_segments: 0,
                recovery: RecoveryReport::default(),
            }),
        ));
        let runtime = RuntimeHandle::start(
            RuntimeConfig::recommended(),
            "creator-test",
            core.clone(),
            segments,
            storage,
            health,
        )
        .unwrap();
        let id = Uuid::new_v4().to_string();
        let policy: AutomationPolicy =
            serde_json::from_value(json!({"account_id":id,"enabled":enabled,
            "daily_quota":10,"min_interval_seconds":0,"max_interval_seconds":0}))
            .unwrap();
        let source = CredentialImport {
            account_id: id.clone(),
            expected_sec_uid: "self".into(),
            user_agent: "Chrome/152.0".into(),
            storage_state: json!({"cookies":[{"name":"sessionid","value":"synthetic"}],"_bd_ticket":{}}),
        };
        Registry::open(root.path())
            .unwrap()
            .put(&source, "测试", 1, Some(&policy))
            .unwrap();
        let service = ManualService::load(
            &MessagingSettings {
                api_token: "x".repeat(32),
                credential_files: vec![],
                credential_store: None,
                registry_root: Some(root.path().into()),
                rule_config_file: None,
                automation: vec![],
            },
            None,
            runtime.clone(),
            core,
        )
        .unwrap();
        (root, service, runtime, id)
    }

    #[tokio::test]
    async fn browser_hold_blocks_external_toggle_and_deletion() {
        let (_root, service, runtime, id) = fixture(false);
        service
            .inner
            .creator_holds
            .lock()
            .unwrap()
            .insert(id.clone(), true);
        let error = service
            .change_business(Edit::Account {
                id: id.clone(),
                input: json!({"auto_reply_enabled":false}),
            })
            .await
            .unwrap_err();
        assert!(error.to_string().contains("人工接管"));
        assert!(service
            .retire_account(&id)
            .await
            .unwrap_err()
            .to_string()
            .contains("创作者平台"));
        assert!(service.registry().load(&id).is_ok());
        service.finish_creator_hold(&id).await.unwrap();
        assert!(!service.creator_held(&id));
        assert!(!service.business_snapshot().unwrap().policies[&id].enabled);
        runtime.drain().await.unwrap();
    }

    #[tokio::test]
    async fn emergency_stop_and_finish_keep_automatic_reply_disabled() {
        let (_root, service, runtime, id) = fixture(false);
        service
            .inner
            .creator_holds
            .lock()
            .unwrap()
            .insert(id.clone(), true);
        service.change_business(Edit::EmergencyStop).await.unwrap();
        service.finish_creator_hold(&id).await.unwrap();
        assert!(!service.business_snapshot().unwrap().policies[&id].enabled);
        runtime.drain().await.unwrap();
    }

    #[tokio::test]
    async fn finish_never_reenables_automatic_reply_regardless_of_original_state() {
        for originally_enabled in [false, true] {
            let (_root, service, runtime, id) = fixture(false);
            service
                .inner
                .creator_holds
                .lock()
                .unwrap()
                .insert(id.clone(), originally_enabled);
            service.finish_creator_hold(&id).await.unwrap();
            assert!(!service.business_snapshot().unwrap().policies[&id].enabled);
            assert!(!service.creator_held(&id));
            runtime.drain().await.unwrap();
        }
    }

    #[tokio::test]
    async fn finish_disables_an_unexpected_enabled_policy_before_releasing_hold() {
        let (_root, service, runtime, id) = fixture(true);
        service
            .inner
            .creator_holds
            .lock()
            .unwrap()
            .insert(id.clone(), true);
        service.finish_creator_hold(&id).await.unwrap();
        assert!(!service.business_snapshot().unwrap().policies[&id].enabled);
        assert!(!service.creator_held(&id));
        runtime.drain().await.unwrap();
    }

    #[tokio::test]
    async fn missing_ownership_never_mutates_original_policy() {
        let (_root, service, runtime, id) = fixture(true);
        assert!(service.begin_creator_hold(&id, true).await.is_err());
        assert!(service.business_snapshot().unwrap().policies[&id].enabled);
        assert!(!service.creator_held(&id));
        runtime.drain().await.unwrap();
    }
}
