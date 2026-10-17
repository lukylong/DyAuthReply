use super::*;
use tracing_subscriber::layer::SubscriberExt;

fn capture(shared: &Arc<Shared>, emit: impl FnOnce()) {
    let subscriber = tracing_subscriber::registry().with(DiagnosticsLayer::new(shared.clone()));
    tracing::subscriber::with_default(subscriber, emit);
}

#[test]
fn nothing_is_captured_until_enabled_and_disabling_discards_the_buffer() {
    let shared = Arc::new(Shared::new());
    capture(
        &shared,
        || tracing::warn!(target: "dy_agent::x", step = "a", "platform request rejected"),
    );
    assert_eq!(shared.pending(), 0);
    shared.enabled.store(true, Ordering::Release);
    capture(
        &shared,
        || tracing::warn!(target: "dy_agent::x", step = "a", "platform request rejected"),
    );
    assert_eq!(shared.pending(), 1);
    shared.set_enabled(false).unwrap();
    assert_eq!(shared.pending(), 0);
}

#[test]
fn only_allow_listed_redacted_fields_and_milestones_are_kept() {
    let shared = Arc::new(Shared::new());
    shared.enabled.store(true, Ordering::Release);
    capture(&shared, || {
        tracing::warn!(
            target: "dy_agent::protocol",
            account_id = "1eacb7a5-f619-4e38-af62-081b296539e0",
            step = "self_works",
            status = 403u64,
            body = "Blocked for MS4wLjABAAAAsecret at https://www.douyin.com/aweme?x=1 conv 7485787829197406758",
            text = "私信正文",
            cookie = "sessionid=abc",
            "platform request rejected"
        );
        tracing::info!(target: "dy_agent::runtime", "some chatty info");
        tracing::info!(target: "dy_agent::runtime", inserted = 3u64, "inbound page processed");
        tracing::error!(target: "other_crate", "not ours");
        tracing::debug!(target: "dy_agent::x", "debug never leaves");
    });
    let events = shared.take();
    assert_eq!(events.len(), 2);
    let warn = &events[0];
    assert_eq!(
        (warn.level, warn.message.as_str()),
        ("WARN", "platform request rejected")
    );
    assert_eq!(
        warn.fields["account"],
        hash_account("1eacb7a5-f619-4e38-af62-081b296539e0")
    );
    assert_eq!(warn.fields["status"], 403);
    let body = warn.fields["body"].as_str().unwrap();
    assert!(
        !body.contains("MS4w") && !body.contains("aweme") && !body.contains("7485787829197406758")
    );
    assert!(body.contains("[sec_uid]") && body.contains("www.douyin.com") && body.contains("[id]"));
    assert!(warn.fields.get("text").is_none() && warn.fields.get("cookie").is_none());
    assert_eq!(events[1].fields["inserted"], 3);
}

#[test]
fn buffer_is_bounded_and_failed_batches_are_restored_first() {
    let shared = Arc::new(Shared::new());
    shared.enabled.store(true, Ordering::Release);
    capture(&shared, || {
        for i in 0..(MAX_BUFFERED_EVENTS + 20) {
            tracing::warn!(target: "dy_agent::x", attempt = i, "w");
        }
    });
    assert_eq!(shared.pending(), MAX_BUFFERED_EVENTS);
    let batch = shared.take();
    capture(&shared, || tracing::warn!(target: "dy_agent::x", "newer"));
    shared.restore(batch);
    assert_eq!(shared.pending(), MAX_BUFFERED_EVENTS);
    assert_eq!(shared.take().last().unwrap().message, "newer");
}

#[test]
fn switch_persists_across_loads() {
    let dir = tempfile::tempdir().unwrap();
    let shared = Shared::new();
    shared.load(dir.path());
    assert!(shared.enabled());
    shared.set_enabled(true).unwrap();
    let reloaded = Shared::new();
    reloaded.load(dir.path());
    assert!(reloaded.enabled());
}

fn enabled_in(dir: &Path) -> Shared {
    let shared = Shared::new();
    shared.load(dir);
    shared.set_enabled(true).unwrap();
    shared
}

fn emit(shared: Shared, messages: &[&'static str]) -> Arc<Shared> {
    let shared = Arc::new(shared);
    capture(&shared, || {
        for message in messages {
            tracing::warn!(target: "dy_agent::x", step = "send", "{message}");
        }
    });
    shared
}

fn reload(dir: &Path) -> Shared {
    let shared = Shared::new();
    shared.load(dir);
    shared
}

#[test]
fn unsent_events_survive_a_restart_in_order() {
    let dir = tempfile::tempdir().unwrap();
    let shared = emit(enabled_in(dir.path()), &["first", "second"]);
    shared.persist().unwrap();
    let restarted = reload(dir.path());
    assert_eq!(restarted.pending(), 2);
    let events = restarted.take();
    assert_eq!(
        events
            .iter()
            .map(|e| (e.level, e.message.as_str()))
            .collect::<Vec<_>>(),
        vec![("WARN", "first"), ("WARN", "second")]
    );
    assert_eq!(events[0].fields["step"], "send");
}

#[test]
fn accepted_uploads_are_not_sent_again_after_a_restart() {
    let dir = tempfile::tempdir().unwrap();
    let shared = emit(enabled_in(dir.path()), &["sent"]);
    shared.persist().unwrap();
    assert_eq!(shared.begin_upload().len(), 1);
    shared.finish_upload(true);
    shared.persist().unwrap();
    assert_eq!(reload(dir.path()).pending(), 0);
}

#[test]
fn a_batch_interrupted_mid_upload_is_kept_ahead_of_newer_events() {
    let dir = tempfile::tempdir().unwrap();
    let shared = emit(enabled_in(dir.path()), &["in flight"]);
    let batch = shared.begin_upload();
    assert_eq!(batch.len(), 1);
    capture(&shared, || tracing::warn!(target: "dy_agent::x", "newer"));
    assert_eq!(shared.pending(), 2);
    shared.persist().unwrap();
    let messages: Vec<_> = reload(dir.path())
        .take()
        .into_iter()
        .map(|e| e.message)
        .collect();
    assert_eq!(messages, ["in flight", "newer"]);
}

#[test]
fn a_failed_upload_goes_back_in_front_of_newer_events() {
    let shared = Arc::new(Shared::new());
    shared.enabled.store(true, Ordering::Release);
    capture(&shared, || tracing::warn!(target: "dy_agent::x", "older"));
    let _ = shared.begin_upload();
    capture(&shared, || tracing::warn!(target: "dy_agent::x", "newer"));
    shared.finish_upload(false);
    let messages: Vec<_> = shared.take().into_iter().map(|e| e.message).collect();
    assert_eq!(messages, ["older", "newer"]);
}

#[test]
fn disabling_deletes_the_saved_events() {
    let dir = tempfile::tempdir().unwrap();
    let shared = emit(enabled_in(dir.path()), &["private"]);
    shared.persist().unwrap();
    assert!(dir.path().join(PENDING_FILE).exists());
    shared.set_enabled(false).unwrap();
    assert!(!dir.path().join(PENDING_FILE).exists());
    shared.set_enabled(true).unwrap();
    assert_eq!(reload(dir.path()).pending(), 0);
}

#[test]
fn a_corrupt_saved_file_is_ignored_and_replaced() {
    let dir = tempfile::tempdir().unwrap();
    enabled_in(dir.path());
    std::fs::write(dir.path().join(PENDING_FILE), b"{not json").unwrap();
    let shared = emit(reload(dir.path()), &["fresh"]);
    assert!(shared.enabled());
    assert_eq!(shared.pending(), 1);
    shared.persist().unwrap();
    assert_eq!(reload(dir.path()).pending(), 1);
}

#[test]
fn diagnostics_defaults_on_only_when_no_saved_choice_exists() {
    let dir = tempfile::tempdir().unwrap();
    let shared = Shared::new();
    shared.load(dir.path());
    assert!(shared.enabled());
    shared.set_enabled(false).unwrap();
    assert!(!reload(dir.path()).enabled());
    std::fs::write(dir.path().join(SWITCH_FILE), b"{broken").unwrap();
    assert!(!reload(dir.path()).enabled());
    std::fs::write(dir.path().join(SWITCH_FILE), br#"{"enabled":"false"}"#).unwrap();
    assert!(!reload(dir.path()).enabled());
}
