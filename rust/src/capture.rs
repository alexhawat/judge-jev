//! Bounded, opt-in capture of judgment evidence.

use crate::models::{ConditionValue, FieldValue, JudgmentResult, RoutingRule, Rubric};
use crate::{canonical::canonical_json, gates::sha256_prefixed};
use anyhow::{Context, Result};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{mpsc, Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub const OWNED_PREFIX: &str = "judge-jev-capture-v1-";
pub const OWNED_SUFFIX: &str = ".jsonl";

static FILE_SEQUENCE: AtomicUsize = AtomicUsize::new(0);
type ProcessRuleCounts = Mutex<(u32, HashMap<(String, usize), usize>)>;
static PROCESS_RULE_COUNTS: OnceLock<ProcessRuleCounts> = OnceLock::new();

pub fn utc_timestamp() -> String {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64;
    let days = seconds.div_euclid(86_400);
    let day_seconds = seconds.rem_euclid(86_400);
    // Howard Hinnant's civil-from-days conversion, epoch shifted to 1970-01-01.
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let mut year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = mp + if mp < 10 { 3 } else { -9 };
    year += i64::from(month <= 2);
    let hour = day_seconds / 3600;
    let minute = (day_seconds % 3600) / 60;
    let second = day_seconds % 60;
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

pub fn random_fraction() -> f64 {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64;
    let mixed = nanos.wrapping_mul(0x9E37_79B9_7F4A_7C15) ^ u64::from(std::process::id());
    ((mixed >> 11) as f64) / ((1u64 << 53) as f64)
}

#[derive(Debug, Clone)]
pub struct CapturePolicy {
    pub audit_rate: f64,
    pub uncertain_below: f64,
    pub boundary_margin: f64,
    pub per_rule_quota: usize,
    pub per_rule_rate: f64,
    pub queue_records: usize,
    pub queue_bytes: usize,
    pub record_bytes: usize,
    pub file_bytes: usize,
    pub directory_bytes: usize,
    pub retention_files: usize,
    pub shutdown: Duration,
}

impl Default for CapturePolicy {
    fn default() -> Self {
        Self {
            audit_rate: env_f64("JUDGE_JEV_CAPTURE_AUDIT_RATE", 0.02),
            uncertain_below: env_f64("JUDGE_JEV_CAPTURE_UNCERTAIN_BELOW", 0.65),
            boundary_margin: env_f64("JUDGE_JEV_CAPTURE_BOUNDARY_MARGIN", 0.10),
            per_rule_quota: 25,
            per_rule_rate: 0.005,
            queue_records: 128,
            queue_bytes: 4 * 1024 * 1024,
            record_bytes: 512 * 1024,
            file_bytes: 8 * 1024 * 1024,
            directory_bytes: 128 * 1024 * 1024,
            retention_files: 32,
            shutdown: Duration::from_millis(250),
        }
    }
}

fn env_f64(name: &str, default: f64) -> f64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

#[derive(Debug, Default, Clone, Serialize)]
pub struct CaptureStats {
    pub written: usize,
    pub dropped: usize,
    pub errors: usize,
}

fn numeric_margin(left: f64, op: &str, right: f64) -> (f64, bool) {
    match op {
        ">=" => (left - right, left >= right),
        ">" => (left - right, left > right),
        "<=" => (right - left, left <= right),
        "<" => (right - left, left < right),
        "==" => (-(left - right).abs(), left == right),
        "!=" => ((left - right).abs(), left != right),
        _ => (f64::NAN, false),
    }
}

pub fn rule_margins(
    rubric: &Rubric,
    answers: &std::collections::HashMap<String, crate::models::Answer>,
) -> Vec<Value> {
    rubric
        .routing
        .rules
        .iter()
        .enumerate()
        .map(|(index, rule)| margin_for_rule(index, rule, answers))
        .collect()
}

fn margin_for_rule(
    index: usize,
    rule: &RoutingRule,
    answers: &std::collections::HashMap<String, crate::models::Answer>,
) -> Value {
    let mut conditions = Vec::new();
    let mut numeric = Vec::new();
    let mut evaluable = true;
    let mut matched = true;
    for condition in &rule.all {
        let observed = answers
            .get(&condition.answer)
            .and_then(|a| a.field(&condition.field));
        let threshold = match &condition.value {
            ConditionValue::Number(n) => json!(n),
            ConditionValue::Text(s) => json!(s),
        };
        let (kind, condition_match, margin, observed_json) = match observed {
            None => {
                evaluable = false;
                ("unevaluable", false, None, Value::Null)
            }
            Some(FieldValue::Text(text)) => {
                let target = condition.value.as_text();
                let hit = if condition.op == "==" {
                    text == target
                } else {
                    text != target
                };
                ("categorical", hit, None, json!(text))
            }
            Some(FieldValue::Num(number)) => {
                let target = condition.value.as_number().unwrap_or(f64::NAN);
                let (distance, hit) = numeric_margin(number, &condition.op, target);
                numeric.push(distance);
                ("numeric", hit, Some(distance), json!(number))
            }
        };
        matched &= condition_match;
        conditions.push(json!({
            "answer": condition.answer,
            "field": condition.field,
            "op": condition.op,
            "threshold": threshold,
            "observed": observed_json,
            "kind": kind,
            // Whether this condition matched is local to the condition. A prior
            // missing answer must not erase the evidence for later conditions.
            "matched": if kind == "unevaluable" { None } else { Some(condition_match) },
            "margin": margin,
        }));
    }
    let margin = numeric.into_iter().reduce(f64::min);
    json!({
        "rule": index,
        "verdict": rule.verdict,
        "default": rule.default,
        "evaluable": evaluable,
        "matched": rule.default || (matched && evaluable),
        "margin": margin,
        "conditions": conditions,
    })
}

fn redact_at(value: &mut Value, parts: &[&str]) {
    let Some((token, rest)) = parts.split_first() else {
        return;
    };
    let array = token.ends_with("[]");
    let key = if array {
        &token[..token.len() - 2]
    } else {
        token
    };
    let Some(object) = value.as_object_mut() else {
        return;
    };
    let Some(child) = object.get_mut(key) else {
        return;
    };
    if rest.is_empty() {
        *child = Value::String("[REDACTED]".into());
    } else if array {
        if let Some(items) = child.as_array_mut() {
            for item in items {
                redact_at(item, rest);
            }
        }
    } else {
        redact_at(child, rest);
    }
}

pub fn redact_state(state: &Value, paths: &[String]) -> Value {
    let mut copied = state.clone();
    for path in paths {
        redact_at(&mut copied, &path.split('.').collect::<Vec<_>>());
    }
    copied
}

#[allow(clippy::too_many_arguments)] // Fixed metadata is explicit for deterministic parity fixtures.
pub fn build_record(
    result: &JudgmentResult,
    rubric: &Rubric,
    filtered_state: &Value,
    redact_paths: &[String],
    captured_at: &str,
    runtime: Option<Value>,
    random: f64,
    policy: &CapturePolicy,
    rubric_hash: &str,
) -> Option<Value> {
    let margins = rule_margins(rubric, &result.answers);
    let mut reasons = Vec::new();
    if random < policy.audit_rate {
        reasons.push("audit");
    }
    if result.confidence < policy.uncertain_below {
        reasons.push("uncertain");
    }
    let deciding: std::collections::HashSet<&str> =
        result.deciding_answers.iter().map(String::as_str).collect();
    let near_boundary = margins.iter().any(|rule| {
        rule.get("conditions")
            .and_then(Value::as_array)
            .map(|items| {
                items.iter().any(|condition| {
                    condition.get("kind").and_then(Value::as_str) == Some("numeric")
                        && condition
                            .get("answer")
                            .and_then(Value::as_str)
                            .map(|id| deciding.contains(id))
                            .unwrap_or(false)
                        && condition
                            .get("margin")
                            .and_then(Value::as_f64)
                            .map(|m| m.abs() <= policy.boundary_margin)
                            .unwrap_or(false)
                })
            })
            .unwrap_or(false)
    });
    if near_boundary {
        reasons.push("boundary");
    }
    if result.routing_reason.contains("Downgraded from") {
        reasons.push("downgraded");
    }
    if matches!(result.verdict.as_str(), "review" | "escalate") {
        reasons.push("non_pass");
    }
    let matched_rule = margins
        .iter()
        .position(|m| m.get("matched") == Some(&Value::Bool(true)));
    if matched_rule
        .map(|rule| take_process_rule_quota(rubric_hash, rule, policy))
        .unwrap_or(false)
    {
        reasons.push("rule_quota");
    }
    if reasons.is_empty() {
        return None;
    }
    let state_hash = sha256_prefixed(canonical_json(filtered_state).ok()?.as_bytes());
    Some(json!({
        "version": 1,
        "captured_at": captured_at,
        "trigger_reasons": reasons,
        "rubric_id": result.rubric_id,
        "rubric_version": result.rubric_version,
        "rubric_hash": rubric_hash,
        "state_hash": state_hash,
        "runtime": runtime.unwrap_or_else(|| serde_json::to_value(&result.runtime).unwrap_or(Value::Null)),
        "backend": result.backend,
        "backend_provenance": result.backend_provenance,
        "requested_model": result.requested_model,
        "model": result.model,
        "state": redact_state(filtered_state, redact_paths),
        "answers": result.answers,
        "usage": result.usage,
        "verdict": result.verdict,
        "confidence": result.confidence,
        "confidence_floor": result.confidence_floor,
        "deciding_answers": result.deciding_answers,
        "routing_reason": result.routing_reason,
        "deterministic_gates": result.deterministic_gates,
        "rule_margins": margins,
    }))
}

fn take_process_rule_quota(rubric_hash: &str, rule: usize, policy: &CapturePolicy) -> bool {
    if policy.per_rule_quota == 0 || random_fraction() >= policy.per_rule_rate {
        return false;
    }
    let pid = std::process::id();
    let counts = PROCESS_RULE_COUNTS.get_or_init(|| Mutex::new((pid, HashMap::new())));
    let mut guard = counts.lock().expect("capture rule quota");
    if guard.0 != pid {
        *guard = (pid, HashMap::new());
    }
    let count = guard.1.entry((rubric_hash.to_string(), rule)).or_insert(0);
    if *count >= policy.per_rule_quota {
        return false;
    }
    *count += 1;
    true
}

pub struct CaptureWriter {
    sender: mpsc::SyncSender<Vec<u8>>,
    queued_bytes: Arc<AtomicUsize>,
    pending_records: Arc<AtomicUsize>,
    stats: Arc<Mutex<CaptureStats>>,
    stop: Arc<AtomicBool>,
    failed: Arc<AtomicBool>,
    done: mpsc::Receiver<()>,
    handle: Option<thread::JoinHandle<()>>,
    policy: CapturePolicy,
    pid: u32,
}

impl CaptureWriter {
    pub fn new(directory: &Path, policy: CapturePolicy) -> Result<Self> {
        // No filesystem work here: construction/enqueue stay memory-only on the
        // judgment path. The background thread validates and creates the directory.
        let (sender, receiver) = mpsc::sync_channel(policy.queue_records);
        let (done_tx, done) = mpsc::channel();
        let queued_bytes = Arc::new(AtomicUsize::new(0));
        let queued_for_thread = Arc::clone(&queued_bytes);
        let pending_records = Arc::new(AtomicUsize::new(0));
        let pending_for_thread = Arc::clone(&pending_records);
        let stats = Arc::new(Mutex::new(CaptureStats::default()));
        let stats_for_thread = Arc::clone(&stats);
        let stop = Arc::new(AtomicBool::new(false));
        let stop_for_thread = Arc::clone(&stop);
        let failed = Arc::new(AtomicBool::new(false));
        let failed_for_thread = Arc::clone(&failed);
        let dir = directory.to_path_buf();
        let thread_policy = policy.clone();
        let pid = std::process::id();
        let handle = thread::spawn(move || {
            writer_loop(
                &dir,
                pid,
                &thread_policy,
                receiver,
                &queued_for_thread,
                &pending_for_thread,
                &stats_for_thread,
                &stop_for_thread,
                &failed_for_thread,
            );
            let _ = done_tx.send(());
        });
        Ok(Self {
            sender,
            queued_bytes,
            pending_records,
            stats,
            stop,
            failed,
            done,
            handle: Some(handle),
            policy,
            pid,
        })
    }

    pub fn enqueue(&self, record: Option<Value>) -> bool {
        let Some(record) = record else { return false };
        if std::process::id() != self.pid
            || self.stop.load(Ordering::Acquire)
            || self.failed.load(Ordering::Acquire)
        {
            self.stats.lock().expect("capture stats").dropped += 1;
            return false;
        }
        let Ok(mut bytes) = serde_json::to_vec(&record) else {
            self.stats.lock().expect("capture stats").errors += 1;
            return false;
        };
        bytes.push(b'\n');
        if bytes.len() > self.policy.record_bytes {
            self.stats.lock().expect("capture stats").dropped += 1;
            return false;
        }
        let len = bytes.len();
        // Reserve before publishing to the receiver. Otherwise a fast writer can
        // consume the record before these counters are incremented. The CAS also
        // keeps concurrent producers from racing past the byte cap.
        if !reserve_bytes(&self.queued_bytes, len, self.policy.queue_bytes) {
            self.stats.lock().expect("capture stats").dropped += 1;
            return false;
        }
        self.pending_records.fetch_add(1, Ordering::AcqRel);
        if self.stop.load(Ordering::Acquire) || self.failed.load(Ordering::Acquire) {
            self.queued_bytes.fetch_sub(len, Ordering::AcqRel);
            self.pending_records.fetch_sub(1, Ordering::AcqRel);
            self.stats.lock().expect("capture stats").dropped += 1;
            return false;
        }
        match self.sender.try_send(bytes) {
            Ok(()) => true,
            Err(_) => {
                self.queued_bytes.fetch_sub(len, Ordering::AcqRel);
                self.pending_records.fetch_sub(1, Ordering::AcqRel);
                self.stats.lock().expect("capture stats").dropped += 1;
                false
            }
        }
    }

    pub fn close(mut self) -> CaptureStats {
        self.stop.store(true, Ordering::Release);
        if self.done.recv_timeout(self.policy.shutdown).is_ok() {
            if let Some(handle) = self.handle.take() {
                let _ = handle.join();
            }
        } else {
            // Transfer ownership of every still-pending record to the close
            // path. A detached worker may finish a blocked filesystem call, but
            // it will no longer report those records as written.
            let abandoned = self.pending_records.swap(0, Ordering::AcqRel);
            self.stats.lock().expect("capture stats").dropped += abandoned;
            let _ = self.handle.take();
        }
        self.stats.lock().expect("capture stats").clone()
    }
}

fn reserve_bytes(queued: &AtomicUsize, len: usize, cap: usize) -> bool {
    let mut current = queued.load(Ordering::Acquire);
    loop {
        let Some(next) = current.checked_add(len) else {
            return false;
        };
        if next > cap {
            return false;
        }
        match queued.compare_exchange_weak(current, next, Ordering::AcqRel, Ordering::Acquire) {
            Ok(_) => return true,
            Err(actual) => current = actual,
        }
    }
}

fn new_file(directory: &Path, pid: u32) -> Result<(File, PathBuf)> {
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let sequence = FILE_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let path = directory.join(format!(
        "{OWNED_PREFIX}{pid}-{stamp}-{sequence}{OWNED_SUFFIX}.active"
    ));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options.open(&path).context("create capture file")?;
    Ok((file, path))
}

fn finalize_file(mut file: File, active: &Path) -> Result<()> {
    file.flush()?;
    drop(file);
    let name = active
        .file_name()
        .and_then(|name| name.to_str())
        .context("capture active file has no name")?;
    let final_name = name
        .strip_suffix(".active")
        .context("capture active suffix missing")?;
    fs::rename(active, active.with_file_name(final_name))?;
    Ok(())
}

fn prepare_directory(directory: &Path) -> Result<()> {
    if directory
        .symlink_metadata()
        .map(|m| m.file_type().is_symlink())
        .unwrap_or(false)
    {
        anyhow::bail!("capture directory must not be a symlink");
    }
    fs::create_dir_all(directory).context("create capture directory")?;
    if directory
        .symlink_metadata()
        .map(|m| m.file_type().is_symlink() || !m.file_type().is_dir())
        .unwrap_or(true)
    {
        anyhow::bail!("capture directory must be a real directory");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(directory, fs::Permissions::from_mode(0o700));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)] // Worker state is passed as shared atomics owned by CaptureWriter.
fn writer_loop(
    directory: &Path,
    pid: u32,
    policy: &CapturePolicy,
    receiver: mpsc::Receiver<Vec<u8>>,
    queued: &AtomicUsize,
    pending: &AtomicUsize,
    stats: &Mutex<CaptureStats>,
    stop: &AtomicBool,
    failed: &AtomicBool,
) {
    let mut file: Option<File> = None;
    let mut active_path: Option<PathBuf> = None;
    let mut prepared = false;
    let mut size = 0usize;
    loop {
        let bytes = match receiver.recv_timeout(Duration::from_millis(20)) {
            Ok(bytes) => bytes,
            Err(mpsc::RecvTimeoutError::Timeout) if stop.load(Ordering::Acquire) => break,
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        };
        queued.fetch_sub(bytes.len(), Ordering::AcqRel);
        // close() transfers all pending records to its dropped count when the
        // finite shutdown deadline expires. Do not later persist queued records
        // that the caller was told were abandoned.
        if pending.load(Ordering::Acquire) == 0 {
            continue;
        }
        if !prepared {
            if prepare_directory(directory).is_err() {
                stats.lock().expect("capture stats").errors += 1;
                failed.store(true, Ordering::Release);
                if release_pending(pending) {
                    stats.lock().expect("capture stats").dropped += 1;
                }
                drain_failed(&receiver, queued, pending, stats);
                return;
            }
            prepared = true;
            match new_file(directory, pid) {
                Ok((new_file, new_path)) => {
                    file = Some(new_file);
                    active_path = Some(new_path);
                }
                Err(_) => {
                    stats.lock().expect("capture stats").errors += 1;
                    failed.store(true, Ordering::Release);
                    if release_pending(pending) {
                        stats.lock().expect("capture stats").dropped += 1;
                    }
                    drain_failed(&receiver, queued, pending, stats);
                    return;
                }
            }
        }
        if size > 0 && size + bytes.len() > policy.file_bytes {
            if finalize_file(
                file.take().expect("active capture file"),
                active_path.as_ref().expect("active capture path"),
            )
            .is_err()
            {
                stats.lock().expect("capture stats").errors += 1;
                failed.store(true, Ordering::Release);
                if release_pending(pending) {
                    stats.lock().expect("capture stats").dropped += 1;
                }
                drain_failed(&receiver, queued, pending, stats);
                break;
            }
            let _ = enforce_bounds(directory, policy, stats, 0);
            match new_file(directory, pid) {
                Ok((new_file, new_path)) => {
                    file = Some(new_file);
                    active_path = Some(new_path);
                    size = 0;
                }
                Err(_) => {
                    stats.lock().expect("capture stats").errors += 1;
                    failed.store(true, Ordering::Release);
                    if release_pending(pending) {
                        stats.lock().expect("capture stats").dropped += 1;
                    }
                    drain_failed(&receiver, queued, pending, stats);
                    break;
                }
            }
        }
        let has_room = match enforce_bounds(directory, policy, stats, bytes.len() as u64) {
            Ok(has_room) => has_room,
            Err(_) => {
                stats.lock().expect("capture stats").errors += 1;
                false
            }
        };
        if !has_room {
            if release_pending(pending) {
                stats.lock().expect("capture stats").dropped += 1;
            }
            continue;
        }
        match file
            .as_mut()
            .expect("active capture file")
            .write_all(&bytes)
        {
            Ok(()) => {
                size += bytes.len();
                if release_pending(pending) {
                    stats.lock().expect("capture stats").written += 1;
                }
            }
            Err(_) => {
                if release_pending(pending) {
                    stats.lock().expect("capture stats").errors += 1;
                }
                failed.store(true, Ordering::Release);
                drain_failed(&receiver, queued, pending, stats);
                break;
            }
        }
    }
    if let (Some(file), Some(active_path)) = (file, active_path) {
        if finalize_file(file, &active_path).is_err() {
            stats.lock().expect("capture stats").errors += 1;
        }
    }
    if prepared {
        let _ = enforce_bounds(directory, policy, stats, 0);
    }
}

fn release_pending(pending: &AtomicUsize) -> bool {
    let mut current = pending.load(Ordering::Acquire);
    while current > 0 {
        match pending.compare_exchange_weak(
            current,
            current - 1,
            Ordering::AcqRel,
            Ordering::Acquire,
        ) {
            Ok(_) => return true,
            Err(actual) => current = actual,
        }
    }
    false
}

fn drain_failed(
    receiver: &mpsc::Receiver<Vec<u8>>,
    queued: &AtomicUsize,
    pending: &AtomicUsize,
    stats: &Mutex<CaptureStats>,
) {
    while let Ok(bytes) = receiver.try_recv() {
        queued.fetch_sub(bytes.len(), Ordering::AcqRel);
        if release_pending(pending) {
            stats.lock().expect("capture stats").dropped += 1;
        }
    }
}

fn directory_size(directory: &Path) -> Result<u64> {
    let mut total = 0;
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        let metadata = fs::symlink_metadata(entry.path())?;
        if name.starts_with(OWNED_PREFIX)
            && (name.ends_with(OWNED_SUFFIX) || name.ends_with(&format!("{OWNED_SUFFIX}.active")))
            && metadata.file_type().is_file()
            && !metadata.file_type().is_symlink()
        {
            total += metadata.len();
        }
    }
    Ok(total)
}

fn owned_files(directory: &Path) -> Result<Vec<(PathBuf, fs::Metadata)>> {
    let mut files = Vec::new();
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        let metadata = fs::symlink_metadata(entry.path())?;
        if name.starts_with(OWNED_PREFIX)
            && name.ends_with(OWNED_SUFFIX)
            && metadata.file_type().is_file()
            && !metadata.file_type().is_symlink()
        {
            files.push((entry.path(), metadata));
        }
    }
    files.sort_by_key(|(_, m)| m.modified().unwrap_or(UNIX_EPOCH));
    Ok(files)
}

fn enforce_bounds(
    directory: &Path,
    policy: &CapturePolicy,
    stats: &Mutex<CaptureStats>,
    incoming_bytes: u64,
) -> Result<bool> {
    let mut files = owned_files(directory)?;
    // Active files contribute to the cap but are never eviction candidates.
    // Reserve the pending record before deciding the directory is full so a
    // finalized file at the cap can be removed and capture can resume.
    let mut total = directory_size(directory)?;
    while !files.is_empty()
        && (files.len() > policy.retention_files
            || total + incoming_bytes > policy.directory_bytes as u64)
    {
        let (path, _metadata) = files.remove(0);
        match fs::remove_file(path) {
            Ok(()) => {
                total = directory_size(directory)?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                total = directory_size(directory)?;
            }
            Err(_) => stats.lock().expect("capture stats").errors += 1,
        }
    }
    Ok(total + incoming_bytes <= policy.directory_bytes as u64)
}

pub fn prune(directory: &Path, older_than: Duration, apply: bool) -> Result<Vec<PathBuf>> {
    if directory
        .symlink_metadata()
        .map(|m| m.file_type().is_symlink())
        .unwrap_or(false)
    {
        anyhow::bail!("capture directory must not be a symlink");
    }
    if !directory.exists() {
        return Ok(Vec::new());
    }
    let cutoff = SystemTime::now()
        .checked_sub(older_than)
        .unwrap_or(UNIX_EPOCH);
    let mut selected = Vec::new();
    for (path, metadata) in owned_files(directory)? {
        if metadata.modified().unwrap_or(SystemTime::now()) < cutoff {
            selected.push(path);
        }
    }
    if apply {
        for path in &selected {
            fs::remove_file(path)?;
        }
    }
    Ok(selected)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Answer, JudgmentResult};
    use crate::rubric::{load_rubric, rubric_content_hash};
    use std::collections::HashMap;

    fn temp_dir(label: &str) -> PathBuf {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("judge-jev-{label}-{}-{stamp}", std::process::id()))
    }

    #[test]
    fn nested_redaction_does_not_mutate_input() {
        let state = json!({"steps": [{"token": "secret", "keep": 1}], "auth": {"password": "pw"}});
        let redacted = redact_state(&state, &["steps[].token".into(), "auth.password".into()]);
        assert_eq!(state["steps"][0]["token"], "secret");
        assert_eq!(redacted["steps"][0]["token"], "[REDACTED]");
        assert!(!serde_json::to_string(&redacted).unwrap().contains("secret"));
    }

    #[test]
    fn margins_include_nonfiring_and_strict_equality_boundary() {
        let rubric = load_rubric("assistant-reply").unwrap();
        let mut answers = HashMap::new();
        answers.insert("screen.judgeable".into(), Answer::Noul { noul: 0.5 });
        let margins = rule_margins(&rubric, &answers);
        assert_eq!(margins.len(), rubric.routing.rules.len());
        assert_eq!(margins[0]["margin"], 0.0);
        assert_eq!(margins[0]["matched"], false);
        assert_eq!(margins[1]["evaluable"], false);

        let mut later_answer = HashMap::new();
        later_answer.insert(
            "score.helpfulness".into(),
            Answer::Score {
                score: 1.0,
                confidence: 0.9,
                legend: None,
                probabilities: HashMap::new(),
            },
        );
        let later_margins = rule_margins(&rubric, &later_answer);
        assert_eq!(later_margins[9]["conditions"][0]["matched"], Value::Null);
        assert_eq!(later_margins[9]["conditions"][1]["matched"], true);
    }

    #[test]
    fn process_rule_quota_is_exact() {
        let policy = CapturePolicy {
            per_rule_quota: 2,
            per_rule_rate: 1.0,
            ..CapturePolicy::default()
        };
        assert!(take_process_rule_quota("rubric-a", 991, &policy));
        assert!(take_process_rule_quota("rubric-a", 991, &policy));
        assert!(!take_process_rule_quota("rubric-a", 991, &policy));
        assert!(take_process_rule_quota("rubric-b", 991, &policy));
    }

    #[test]
    fn concurrent_byte_reservations_cannot_cross_cap() {
        let queued = AtomicUsize::new(0);
        assert!(reserve_bytes(&queued, 6, 10));
        assert!(!reserve_bytes(&queued, 5, 10));
        assert_eq!(queued.load(Ordering::Acquire), 6);
    }

    #[test]
    fn writer_is_bounded_rotates_and_two_instances_do_not_share_append() {
        let directory = temp_dir("capture-concurrent");
        let policy = CapturePolicy {
            audit_rate: 1.0,
            record_bytes: 128,
            file_bytes: 48,
            directory_bytes: 256,
            retention_files: 8,
            shutdown: Duration::from_secs(1),
            ..CapturePolicy::default()
        };
        let first = CaptureWriter::new(&directory, policy.clone()).unwrap();
        let second = CaptureWriter::new(&directory, policy).unwrap();
        for index in 0..8 {
            let record = Some(json!({"i": index, "value": "abcdefgh"}));
            first.enqueue(record.clone());
            second.enqueue(record);
        }
        let first_stats = first.close();
        let second_stats = second.close();
        assert!(first_stats.written + first_stats.dropped > 0);
        assert!(second_stats.written + second_stats.dropped > 0);
        let entries = fs::read_dir(&directory)
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        assert!(entries
            .iter()
            .all(|entry| !entry.file_name().to_string_lossy().ends_with(".active")));
        assert!(directory_size(&directory).unwrap() <= 256);
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn writer_without_selected_record_creates_no_empty_file() {
        let directory = temp_dir("capture-empty");
        let writer = CaptureWriter::new(&directory, CapturePolicy::default()).unwrap();
        let stats = writer.close();
        assert_eq!(stats.written, 0);
        assert_eq!(stats.dropped, 0);
        assert_eq!(stats.errors, 0);
        assert!(!directory.exists());
    }

    #[test]
    fn directory_cap_evicts_finalized_file_and_preserves_active_writer() {
        let directory = temp_dir("capture-cap-resume");
        fs::create_dir_all(&directory).unwrap();
        let old = directory.join(format!("{OWNED_PREFIX}old{OWNED_SUFFIX}"));
        let other_active = directory.join(format!("{OWNED_PREFIX}other{OWNED_SUFFIX}.active"));
        fs::write(&old, vec![b'x'; 90]).unwrap();
        fs::write(&other_active, b"y").unwrap();
        let policy = CapturePolicy {
            directory_bytes: 128,
            record_bytes: 128,
            file_bytes: 128,
            shutdown: Duration::from_secs(1),
            ..CapturePolicy::default()
        };
        let writer = CaptureWriter::new(&directory, policy.clone()).unwrap();
        assert!(writer.enqueue(Some(json!({"value": "z".repeat(30)}))));
        let stats = writer.close();
        assert_eq!(stats.written, 1);
        assert!(!old.exists());
        assert!(other_active.exists());
        assert!(directory_size(&directory).unwrap() <= policy.directory_bytes as u64);
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn oversize_is_dropped_and_prune_ignores_unrelated_files() {
        let directory = temp_dir("capture-prune");
        let policy = CapturePolicy {
            record_bytes: 16,
            shutdown: Duration::from_secs(1),
            ..CapturePolicy::default()
        };
        let writer = CaptureWriter::new(&directory, policy).unwrap();
        assert!(!writer.enqueue(Some(json!({"payload": "x".repeat(100)}))));
        assert!(writer.close().dropped >= 1);
        fs::create_dir_all(&directory).unwrap();
        let owned = directory.join(format!("{OWNED_PREFIX}old{OWNED_SUFFIX}"));
        let unrelated = directory.join("unrelated.jsonl");
        fs::write(&owned, b"{}\n").unwrap();
        fs::write(&unrelated, b"keep").unwrap();
        let selected = prune(&directory, Duration::ZERO, true).unwrap();
        assert!(selected.contains(&owned));
        assert!(unrelated.exists());
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn timestamp_has_canonical_utc_shape() {
        let value = utc_timestamp();
        assert_eq!(value.len(), 20);
        assert!(value.ends_with('Z') && value.contains('T'));
    }

    #[test]
    fn failed_background_setup_counts_and_rejects_records() {
        let directory = temp_dir("capture-failed-setup");
        fs::write(&directory, b"not a directory").unwrap();
        let policy = CapturePolicy {
            shutdown: Duration::from_secs(1),
            ..CapturePolicy::default()
        };
        let writer = CaptureWriter::new(&directory, policy).unwrap();
        let _ = writer.enqueue(Some(json!({"first": true})));
        thread::sleep(Duration::from_millis(30));
        assert!(!writer.enqueue(Some(json!({"after_failure": true}))));
        let stats = writer.close();
        assert!(stats.errors >= 1);
        assert!(stats.dropped >= 1);
        fs::remove_file(directory).unwrap();
    }

    #[test]
    fn inherited_writer_pid_is_rejected() {
        let directory = temp_dir("capture-fork-pid");
        let mut writer = CaptureWriter::new(&directory, CapturePolicy::default()).unwrap();
        writer.pid = writer.pid.wrapping_add(1);
        assert!(!writer.enqueue(Some(json!({"child": true}))));
        assert_eq!(writer.close().dropped, 1);
        let _ = fs::remove_dir_all(directory);
    }

    #[test]
    fn record_bytes_match_shared_cross_runtime_fixture() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let fixture: Value = serde_json::from_str(
            &fs::read_to_string(root.join("fixtures/capture-parity-input.json")).unwrap(),
        )
        .unwrap();
        let result: JudgmentResult = serde_json::from_value(fixture["result"].clone()).unwrap();
        let rubric = load_rubric(fixture["rubric_id"].as_str().unwrap()).unwrap();
        let rubric_hash = rubric_content_hash(&rubric).unwrap();
        assert_eq!(result.rubric_hash, rubric_hash);
        let state = fixture["state"].clone();
        let redact_paths = fixture["redact_paths"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap().to_string())
            .collect::<Vec<_>>();
        let policy = CapturePolicy {
            audit_rate: 1.0,
            per_rule_rate: 0.0,
            ..CapturePolicy::default()
        };
        let record = build_record(
            &result,
            &rubric,
            &state,
            &redact_paths,
            fixture["captured_at"].as_str().unwrap(),
            Some(fixture["runtime"].clone()),
            0.0,
            &policy,
            &rubric_hash,
        )
        .unwrap();
        assert_eq!(record["usage"], fixture["result"]["usage"]);
        assert_eq!(
            record["deterministic_gates"],
            fixture["result"]["deterministic_gates"]
        );
        let mut actual = serde_json::to_vec(&record).unwrap();
        actual.push(b'\n');
        let expected = fs::read(root.join("fixtures/capture-record-v1.jsonl")).unwrap();
        assert!(!actual
            .windows(b"private@example.test".len())
            .any(|window| window == b"private@example.test"));
        assert!(!actual
            .windows(b"secret-token".len())
            .any(|window| window == b"secret-token"));
        assert_eq!(actual, expected);
    }
}
