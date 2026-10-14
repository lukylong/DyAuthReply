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
    assert!(!shared.enabled());
    shared.set_enabled(true).unwrap();
    let reloaded = Shared::new();
    reloaded.load(dir.path());
    assert!(reloaded.enabled());
}
