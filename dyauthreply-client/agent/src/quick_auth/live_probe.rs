//! Explicit opt-in manual acceptance only: no account-store writes, import, or sending.
use super::*;
use crate::protocol::{
    account_session::NativeAccountSession, credentials::AccountCredentials,
    live_http::ProtocolHttpClient, native_signer::NativeSigner,
};

struct BrowserGuard {
    child: Arc<StdMutex<Option<Child>>>,
    profile: PathBuf,
}
impl Drop for BrowserGuard {
    fn drop(&mut self) {
        let _ = stop_child_and_remove(&self.child, &self.profile);
    }
}

#[tokio::test]
#[ignore = "requires an explicitly approved interactive login in an isolated browser"]
async fn live_capture_identity_only() -> Result<()> {
    let executable = PathBuf::from(
        std::env::var("DY_CAPTURE_TEST_BROWSER")
            .context("set the existing verified browser executable path")?,
    );
    let expected_name = std::env::var("DY_CAPTURE_TEST_ACCOUNT")
        .context("set the user-approved account nickname")?;
    anyhow::ensure!(
        executable.is_file() && !expected_name.is_empty(),
        "test configuration invalid"
    );
    let root = tempfile::tempdir()?;
    let child = Arc::new(StdMutex::new(None));
    let ws = Arc::new(StdMutex::new(None));
    let guard = BrowserGuard {
        child: child.clone(),
        profile: root.path().join("capture-only"),
    };
    let launched = launch(&executable, &guard.profile, &child, &ws).await?;
    let mut cdp = CdpClient::connect(&launched.page_ws).await?;
    cdp.initialize().await?;
    println!("LIVE_CAPTURE_WINDOW_OPEN: isolated profile; user login required; no import or sends");
    let started = tokio::time::Instant::now();
    let deadline = started + Duration::from_secs(600);
    let mut last = String::new();
    let candidate = loop {
        anyhow::ensure!(
            tokio::time::Instant::now() < deadline,
            "manual acceptance timed out"
        );
        anyhow::ensure!(
            child_running(&child)?,
            "test browser closed before completion"
        );
        let result = tokio::time::timeout_at(deadline, cdp.collect()).await?;
        match result {
            Ok(capture) => {
                let projection = format!(
                    "{}; sample_observation={:?}",
                    serde_json::to_string(&capture.completeness)?,
                    cdp.sample_observation()
                );
                if projection != last {
                    println!(
                        "LIVE_CAPTURE_PROGRESS: elapsed_ms={}; {projection}",
                        started.elapsed().as_millis()
                    );
                    last = projection;
                }
                if let Some(candidate) = capture.candidate {
                    break candidate;
                }
            }
            Err(_) => {
                println!("LIVE_CAPTURE_TRANSIENT_READ_FAILURE");
            }
        }
        tokio::time::sleep(cdp.poll_delay()).await;
    };
    anyhow::ensure!(
        candidate.identity.nickname == expected_name,
        "test account does not match approved nickname"
    );
    let credentials =
        AccountCredentials::import_extension("isolated-capture-test".into(), &candidate.bundle)?;
    let http = ProtocolHttpClient::for_user_agent(2, &credentials.user_agent)?;
    let mut session = NativeAccountSession::new(credentials, http, NativeSigner::new(2)?);
    let verified =
        tokio::time::timeout(Duration::from_secs(30), session.verify_for_import()).await??;
    anyhow::ensure!(
        verified.sec_uid == candidate.identity.sec_uid,
        "captured and authenticated identity mismatch"
    );
    println!("LIVE_CAPTURE_IDENTITY_PASS: all_fields=true; identity_matches=true; elapsed_ms={}; imported=false; sent=false", started.elapsed().as_millis());
    let _ = cdp.close().await;
    drop(guard);
    Ok(())
}
