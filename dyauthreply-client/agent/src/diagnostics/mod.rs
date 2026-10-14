//! Opt-in runtime diagnostics (off by default). When the user enables it, warnings,
//! errors and a fixed set of business milestones are captured from tracing,
//! redacted through a field allow-list, buffered (bounded) and uploaded by
//! [`upload`]. Never captured: message text, nicknames, `sec_uid`s, cookies, tokens,
//! URL paths/parameters. Account IDs are hashed.
pub mod upload;

use regex::Regex;
use serde::Serialize;
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
    fmt,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, LazyLock, Mutex, OnceLock,
    },
};
use tracing::{field::Field, Event, Level, Subscriber};
use tracing_subscriber::{layer::Context, Layer};

pub const MAX_BUFFERED_EVENTS: usize = 500;
const MAX_VALUE_CHARS: usize = 300;
/// INFO events worth reporting; everything else at INFO stays local.
const INFO_MESSAGES: &[&str] = &[
    "dy-agent runtime is ready",
    "inbound page processed",
    "auto-reply queued",
    "auto-reply dropped by cooldown or quota",
    "auto-reply deferred by reply guard",
    "native send completed",
    "peer profile backfilled",
    "conversation peer resolved from member list",
    "quick-auth component downloaded and verified",
];
/// Tracing field -> reported key. Fields not listed are dropped.
const FIELDS: &[(&str, &str)] = &[
    ("account_id", "account"),
    ("rule_id", "rule"),
    ("step", "step"),
    ("status", "status"),
    ("body", "body"),
    ("classification", "classification"),
    ("response_codes", "response_codes"),
    ("error", "error"),
    ("retry_attempt", "retry_attempt"),
    ("inserted", "inserted"),
    ("candidates", "candidates"),
    ("ignored", "ignored"),
    ("deferred", "deferred"),
    ("reconciled_sends", "reconciled_sends"),
    ("members", "members"),
    ("host", "host"),
    ("source", "host"),
];

#[derive(Clone, Debug, Serialize)]
pub struct DiagnosticEvent {
    pub at: String,
    pub level: &'static str,
    pub target: String,
    pub message: String,
    pub fields: Map<String, Value>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct UploadStatus {
    pub last_upload_at: Option<String>,
    pub last_error: String,
    pub uploaded_events: u64,
}

pub struct Shared {
    enabled: AtomicBool,
    buffer: Mutex<VecDeque<DiagnosticEvent>>,
    status: Mutex<UploadStatus>,
    state_file: OnceLock<PathBuf>,
}

static SHARED: LazyLock<Arc<Shared>> = LazyLock::new(|| Arc::new(Shared::new()));

#[must_use]
pub fn shared() -> Arc<Shared> {
    SHARED.clone()
}

/// The tracing layer; installed once at startup and inert until enabled.
#[must_use]
pub fn layer() -> DiagnosticsLayer {
    DiagnosticsLayer::new(shared())
}

impl Default for Shared {
    fn default() -> Self {
        Self::new()
    }
}

impl Shared {
    #[must_use]
    pub fn new() -> Self {
        Self {
            enabled: AtomicBool::new(false),
            buffer: Mutex::new(VecDeque::new()),
            status: Mutex::new(UploadStatus::default()),
            state_file: OnceLock::new(),
        }
    }
    /// Loads the persisted switch; absence or corruption means disabled.
    pub fn load(&self, data_dir: &Path) {
        let path = data_dir.join("diagnostics.json");
        let enabled = std::fs::read(&path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .is_some_and(|value| value["enabled"] == true);
        let _ = self.state_file.set(path);
        self.enabled.store(enabled, Ordering::Release);
    }
    #[must_use]
    pub fn enabled(&self) -> bool {
        self.enabled.load(Ordering::Acquire)
    }
    /// Persists the switch. Turning it off discards anything not yet uploaded.
    /// # Errors
    /// Reports an unwritable data directory.
    pub fn set_enabled(&self, enabled: bool) -> anyhow::Result<()> {
        if let Some(path) = self.state_file.get() {
            let temporary = path.with_extension("json.pending");
            std::fs::write(&temporary, json!({ "enabled": enabled }).to_string())?;
            std::fs::rename(&temporary, path)?;
        }
        self.enabled.store(enabled, Ordering::Release);
        if !enabled {
            self.buffer
                .lock()
                .map_err(|_| anyhow::anyhow!("诊断缓冲区锁异常"))?
                .clear();
        }
        Ok(())
    }
    pub fn push(&self, event: DiagnosticEvent) {
        if let Ok(mut buffer) = self.buffer.lock() {
            if buffer.len() >= MAX_BUFFERED_EVENTS {
                buffer.pop_front();
            }
            buffer.push_back(event);
        }
    }
    #[must_use]
    pub fn take(&self) -> Vec<DiagnosticEvent> {
        self.buffer
            .lock()
            .map(|mut buffer| buffer.drain(..).collect())
            .unwrap_or_default()
    }
    /// Puts back a batch that failed to upload, oldest first, keeping the bound.
    pub fn restore(&self, events: Vec<DiagnosticEvent>) {
        if !self.enabled() {
            return;
        }
        if let Ok(mut buffer) = self.buffer.lock() {
            for event in events.into_iter().rev() {
                if buffer.len() >= MAX_BUFFERED_EVENTS {
                    break;
                }
                buffer.push_front(event);
            }
        }
    }
    #[must_use]
    pub fn pending(&self) -> usize {
        self.buffer.lock().map_or(0, |b| b.len())
    }
    #[must_use]
    pub fn status(&self) -> UploadStatus {
        self.status.lock().map(|s| s.clone()).unwrap_or_default()
    }
    pub fn record(&self, update: impl FnOnce(&mut UploadStatus)) {
        if let Ok(mut status) = self.status.lock() {
            update(&mut status);
        }
    }
}

pub struct DiagnosticsLayer {
    shared: Arc<Shared>,
}

impl DiagnosticsLayer {
    #[must_use]
    pub fn new(shared: Arc<Shared>) -> Self {
        Self { shared }
    }
}

impl<S: Subscriber> Layer<S> for DiagnosticsLayer {
    fn on_event(&self, event: &Event<'_>, _ctx: Context<'_, S>) {
        if !self.shared.enabled() {
            return;
        }
        let meta = event.metadata();
        let level = match *meta.level() {
            Level::ERROR => "ERROR",
            Level::WARN => "WARN",
            Level::INFO => "INFO",
            _ => return,
        };
        if !meta.target().starts_with("dy_agent") {
            return;
        }
        let mut visitor = Collect::default();
        event.record(&mut visitor);
        if level == "INFO" && !INFO_MESSAGES.contains(&visitor.message.as_str()) {
            return;
        }
        self.shared.push(DiagnosticEvent {
            at: now_rfc3339(),
            level,
            target: truncate(meta.target(), 128),
            message: redact(&visitor.message),
            fields: visitor.fields,
        });
    }
}

#[derive(Default)]
struct Collect {
    message: String,
    fields: Map<String, Value>,
}

impl Collect {
    fn put(&mut self, field: &Field, value: Value) {
        if field.name() == "message" {
            self.message = value
                .as_str()
                .map_or_else(|| value.to_string(), str::to_owned);
            return;
        }
        let Some((_, key)) = FIELDS.iter().find(|(name, _)| *name == field.name()) else {
            return;
        };
        let value = match (field.name(), value) {
            ("account_id", v) => json!(hash_account(v.as_str().unwrap_or_default())),
            ("source", Value::String(url)) => json!(host_of(&url)),
            (_, Value::String(text)) => json!(redact(&text)),
            (_, other) => other,
        };
        self.fields.insert((*key).to_owned(), value);
    }
}

impl tracing::field::Visit for Collect {
    fn record_str(&mut self, field: &Field, value: &str) {
        self.put(field, json!(value));
    }
    fn record_i64(&mut self, field: &Field, value: i64) {
        self.put(field, json!(value));
    }
    fn record_u64(&mut self, field: &Field, value: u64) {
        self.put(field, json!(value));
    }
    fn record_bool(&mut self, field: &Field, value: bool) {
        self.put(field, json!(value));
    }
    fn record_debug(&mut self, field: &Field, value: &dyn fmt::Debug) {
        let text = format!("{value:?}");
        let text = text.trim_matches('"').to_owned();
        self.put(field, json!(text));
    }
}

static SEC_UID: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"MS4wLj[A-Za-z0-9_\-]+").expect("valid"));
static URL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"https?://([A-Za-z0-9.\-]+)[^\s]*").expect("valid"));
static LONG_NUMBER: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\d{8,}").expect("valid"));

/// Strips identities and URL paths from free text and bounds its length.
#[must_use]
pub fn redact(text: &str) -> String {
    let text = SEC_UID.replace_all(text, "[sec_uid]");
    let text = URL.replace_all(&text, "https://$1/…");
    let text = LONG_NUMBER.replace_all(&text, "[id]");
    truncate(&text, MAX_VALUE_CHARS)
}

#[must_use]
pub fn hash_account(id: &str) -> String {
    use std::fmt::Write;
    let digest = Sha256::digest(id.as_bytes());
    digest.iter().take(6).fold(String::new(), |mut out, b| {
        let _ = write!(out, "{b:02x}");
        out
    })
}

fn host_of(url: &str) -> String {
    url.parse::<wreq::Uri>()
        .ok()
        .and_then(|u| u.host().map(str::to_owned))
        .unwrap_or_default()
}

/// Current UTC time as RFC 3339 (chrono is built without its clock feature).
#[must_use]
pub fn now_rfc3339() -> String {
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX));
    chrono::DateTime::from_timestamp_millis(millis)
        .map(|at| at.to_rfc3339())
        .unwrap_or_default()
}

fn truncate(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

#[cfg(test)]
mod tests;
