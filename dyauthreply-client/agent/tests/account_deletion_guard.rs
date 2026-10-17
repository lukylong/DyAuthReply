use dy_agent::{
    business::Edit,
    config::{RuntimeConfig, StorageConfig},
    credential_store::registry::Registry,
    health::{HealthHandle, HealthResponse, StorageHealthResponse, StorageStartupSnapshot},
    protocol::{credentials::CredentialImport, fixtures::verify_embedded_corpus},
    runtime::{
        messaging::{AutomationPolicy, ManualService, MessagingSettings},
        supervisor::RuntimeHandle,
    },
    storage::{retention::DiskPressure, RecoveryReport, SegmentCatalog, SegmentStore},
    store::CoreStore,
};
use serde_json::json;
use std::sync::Arc;
use uuid::Uuid;

#[tokio::test]
async fn deletion_guard_preserves_bound_credentials_for_loaded_and_dormant_accounts() {
    for loaded in [true, false] {
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
            "test-installation",
            core.clone(),
            segments,
            storage,
            health,
        )
        .unwrap();
        let id = Uuid::new_v4().to_string();
        let policy: AutomationPolicy = serde_json::from_value(json!({"account_id":id,"enabled":false,"daily_quota":10,"min_interval_seconds":0,"max_interval_seconds":0})).unwrap();
        let input = CredentialImport {
            account_id: id.clone(),
            expected_sec_uid: "synthetic-scope".into(),
            user_agent: "Chrome/151.0".into(),
            storage_state: json!({"cookies":[{"name":"sessionid","value":"synthetic-session"}],"_bd_ticket":{}}),
        };
        if loaded {
            Registry::open(root.path())
                .unwrap()
                .put(&input, "测试账号", 1, Some(&policy))
                .unwrap();
        }
        let settings = MessagingSettings {
            api_token: "x".repeat(32),
            credential_files: vec![],
            credential_store: None,
            registry_root: Some(root.path().to_path_buf()),
            rule_config_file: None,
            automation: vec![],
        };
        let service = ManualService::load(&settings, None, runtime.clone(), core).unwrap();
        if !loaded {
            let record = service
                .registry()
                .put(&input, "测试账号", 1, Some(&policy))
                .unwrap()
                .0;
            service.store_imported_profile(&record, &policy).unwrap();
        }
        let rule = service.change_business(Edit::Rule{id:None,input:json!({"name":"停用但绑定","account_ids":[id],"match_type":"default","reply_text":"test","status":false})}).await.unwrap();
        let before = service.business_snapshot().unwrap().document.revision;
        let error = service.retire_account(&id).await.unwrap_err().to_string();
        assert!(error.contains("停用但绑定"));
        assert!(service.registry().load(&id).is_ok());
        assert_eq!(
            service.business_snapshot().unwrap().document.revision,
            before
        );
        service
            .change_business(Edit::Rule {
                id: Some(rule["id"].as_str().unwrap().into()),
                input: json!({"account_ids":[]}),
            })
            .await
            .unwrap();
        service.retire_account(&id).await.unwrap();
        assert!(service.registry().load(&id).is_err());
        assert!(!service
            .business_snapshot()
            .unwrap()
            .policies
            .contains_key(&id));
        assert_eq!(service.business_snapshot().unwrap().document.rules.len(), 1);
        assert!(service
            .change_business(Edit::Rule {
                id: Some(rule["id"].as_str().unwrap().into()),
                input: json!({"account_ids":[id]})
            })
            .await
            .is_err());
        runtime.drain().await.unwrap();
    }
}
