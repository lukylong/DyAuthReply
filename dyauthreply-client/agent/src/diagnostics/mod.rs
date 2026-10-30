//! User-controllable runtime diagnostics: a missing preference defaults on after
//! configuration load; explicit opt-out remains off. While enabled, warnings,
//! errors and a fixed set of business milestones are captured from tracing,
//! redacted through a field allow-list, buffered (bounded, saved across restarts by
//! [`persist`]) and uploaded by [`upload`]. Never captured: message text, nicknames,
//! `sec_uid`s, cookies, tokens, URL paths/parameters. Account IDs are hashed.
mod persist;
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
const SWITCH_FILE: &str = "diagnostics.json";
const PENDING_FILE: &str = "diagnostics-pending.json";
/// INFO events worth reporting; everything else at INFO stays local.
const INFO_MESSAGES: &[&str] = &[
    "dy-agent runtime is ready",
    "inbound page processed",
    "auto-reply queued",
    "reply batch stopped",
    "account automatic policy changed",
    "auto-reply dropped by cooldown or quota",
    "auto-reply deferred by reply guard",
    "native send completed",
    "peer profile backfilled",
    "conversation peer resolved from member list",
    "quick-auth component downloaded and verified",
    "quick-auth component selected for system",
    "quick-auth main site waiting for UIFID; retrying",
    "quick-auth main site UIFID ready",
    "quick-auth main site page was hidden; restoring its window",
    "quick-auth capture ready",
];
/// Tracing field -> reported key. Fields not listed are dropped.
const FIELDS: &[(&str, &str)] = &[
    ("account_id", "account"),
    ("rule_id", "rule"),
    ("batch_id", "trace_id"),
    ("trigger_type", "trigger_type"),
    ("message_source", "message_source"),
    ("segment_index", "segment_index"),
    ("segment_count", "segment_count"),
    ("content_kind", "content_kind"),
    ("content_chars", "content_chars"),
    ("reason", "reason"),
    ("recheck_minutes", "recheck_minutes"),
    ("enabled", "enabled"),
    ("previous_enabled", "previous_enabled"),
    ("change_source", "change_source"),
    ("step", "step"),
    ("status", "status"),
    ("body", "body"),
    ("classification", "classification"),
    ("version", "version"),
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
    ("attempt", "attempt"),
    ("action", "phase"),
    ("videos", "count"),
    ("elapsed_ms", "elapsed_ms"),
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
    /// Why unsent events could not be saved for the next start; empty when saved.
    pub persist_error: String,
}

pub struct Shared {
    enabled: AtomicBool,
    buffer: Mutex<VecDeque<DiagnosticEvent>>,
    /// The batch being uploaded; it stays saved until the server accepts it.
    in_flight: Mutex<Vec<DiagnosticEvent>>,
    /// Unsent events changed since they were last saved.
    dirty: AtomicBool,
    persist_lock: Mutex<()>,
    status: Mutex<UploadStatus>,
    state_file: OnceLock<PathBuf>,
    pending_file: OnceLock<PathBuf>,
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
            in_flight: Mutex::new(Vec::new()),
            dirty: AtomicBool::new(false),
            persist_lock: Mutex::new(()),
            status: Mutex::new(UploadStatus::default()),
            state_file: OnceLock::new(),
            pending_file: OnceLock::new(),
        }
    }
    /// Loads the persisted switch and, while enabled, the events an earlier run
    /// had not uploaded. A missing preference defaults on; explicit opt-out,
    /// malformed settings and read failures remain off.
    pub fn load(&self, data_dir: &Path) {
        let path = data_dir.join(SWITCH_FILE);
        let enabled = match std::fs::read(&path) {
            Ok(bytes) => serde_json::from_slice::<Value>(&bytes)
                .ok()
                .and_then(|value| value["enabled"].as_bool())
                .unwrap_or(false),
            Err(error) => error.kind() == std::io::ErrorKind::NotFound,
        };
        let _ = self.state_file.set(path);
        let pending = data_dir.join(PENDING_FILE);
        self.enabled.store(enabled, Ordering::Release);
        if enabled {
            self.restore(persist::read(&pending));
        } else {
            self.note_persist(&persist::remove(&pending));
        }
        let _ = self.pending_file.set(pending);
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
            let lock_error = || anyhow::anyhow!("诊断缓冲区锁异常");
            self.buffer.lock().map_err(|_| lock_error())?.clear();
            self.in_flight.lock().map_err(|_| lock_error())?.clear();
            let _saving = self.persist_lock.lock().map_err(|_| lock_error())?;
            if let Some(path) = self.pending_file.get() {
                persist::remove(path)?;
            }
        }
        Ok(())
    }
    pub fn push(&self, event: DiagnosticEvent) {
        if let Ok(mut buffer) = self.buffer.lock() {
            if buffer.len() >= MAX_BUFFERED_EVENTS {
                buffer.pop_front();
            }
            buffer.push_back(event);
            self.dirty.store(true, Ordering::Release);
        }
    }
    #[must_use]
    pub fn take(&self) -> Vec<DiagnosticEvent> {
        let events: Vec<_> = self
            .buffer
            .lock()
            .map(|mut buffer| buffer.drain(..).collect())
            .unwrap_or_default();
        if !events.is_empty() {
            self.dirty.store(true, Ordering::Release);
        }
        events
    }
    /// Puts back a batch that failed to upload, oldest first, keeping the bound.
    pub fn restore(&self, events: Vec<DiagnosticEvent>) {
        if !self.enabled() || events.is_empty() {
            return;
        }
        if let Ok(mut buffer) = self.buffer.lock() {
            for event in events.into_iter().rev() {
                if buffer.len() >= MAX_BUFFERED_EVENTS {
                    break;
                }
                buffer.push_front(event);
            }
            self.dirty.store(true, Ordering::Release);
        }
    }
    /// Moves the buffered events into the in-flight batch and returns it. The
    /// batch stays saved until [`Self::finish_upload`] settles it.
    #[must_use]
    pub fn begin_upload(&self) -> Vec<DiagnosticEvent> {
        let (Ok(mut in_flight), Ok(mut buffer)) = (self.in_flight.lock(), self.buffer.lock())
        else {
            return Vec::new();
        };
        in_flight.extend(buffer.drain(..));
        in_flight.clone()
    }
    /// Forgets a batch the server accepted, or puts a rejected one back in front
    /// of the events captured meanwhile.
    pub fn finish_upload(&self, accepted: bool) {
        let batch = self
            .in_flight
            .lock()
            .map(|mut batch| std::mem::take(&mut *batch))
            .unwrap_or_default();
        if !accepted {
            self.restore(batch);
        } else if !batch.is_empty() {
            self.dirty.store(true, Ordering::Release);
        }
    }
    /// Saves the events the server has not accepted, so a restart or crash does
    /// not lose them. Does nothing when they have not changed since the last save.
    /// # Errors
    /// Reports an unwritable data directory (also kept in the status, because
    /// logging it would feed the buffer).
    pub fn persist(&self) -> anyhow::Result<()> {
        let Some(path) = self.pending_file.get() else {
            return Ok(());
        };
        let _saving = self
            .persist_lock
            .lock()
            .map_err(|_| anyhow::anyhow!("诊断保存锁异常"))?;
        if !self.dirty.swap(false, Ordering::AcqRel) {
            return Ok(());
        }
        let result = self
            .unsent()
            .and_then(|events| persist::write(path, &events));
        if result.is_err() {
            self.dirty.store(true, Ordering::Release);
        }
        self.note_persist(&result);
        result
    }
    fn unsent(&self) -> anyhow::Result<Vec<DiagnosticEvent>> {
        if !self.enabled() {
            return Ok(Vec::new());
        }
        let lock_error = || anyhow::anyhow!("诊断缓冲区锁异常");
        let in_flight = self.in_flight.lock().map_err(|_| lock_error())?;
        let buffer = self.buffer.lock().map_err(|_| lock_error())?;
        Ok(in_flight.iter().chain(buffer.iter()).cloned().collect())
    }
    fn note_persist(&self, result: &anyhow::Result<()>) {
        let message = result
            .as_ref()
            .err()
            .map(|error| redact(&error.to_string()))
            .unwrap_or_default();
        self.record(|status| status.persist_error = message);
    }
    /// Events not yet accepted by the server, including a batch being uploaded.
    #[must_use]
    pub fn pending(&self) -> usize {
        let in_flight = self.in_flight.lock().map_or(0, |b| b.len());
        in_flight + self.buffer.lock().map_or(0, |b| b.len())
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
            ("batch_id", v) => {
                use std::fmt::Write;
                let raw = v.as_str().unwrap_or_default();
                if raw.is_empty() {
                    return;
                }
                let digest = Sha256::digest(format!("diagnostic-reply:{raw}"));
                let trace = digest[..8].iter().fold(String::new(), |mut output, b| {
                    let _ = write!(output, "{b:02x}");
                    output
                });
                json!(trace)
            }
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
