use crate::answers::validate_answers;
use crate::budget::check_budget;
use crate::canonical::{canonical_json, CanonicalState};
use crate::gates::{
    build_state_projection, escalate_on_injection, evaluate_gates, evaluate_replay_gates,
    GateContext,
};
use crate::models::{Answer, JudgmentResult, Runtime, SavedJudgment};
use crate::paths::repo_root;
use crate::routing::route_verdict;
use crate::rubric::{load_rubric, rubric_content_hash};
use crate::state_filter::filter_state;
use crate::typesafe::{build_questions, mock, pinned_answers, LiveClient};
use anyhow::{anyhow, Result};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use tracing::info;

/// `--input -` reads stdin, so `judge-jev run ... | judge-jev replay --input -`
/// composes. It is also the only way to judge something that was never a file.
pub const STDIN_PATH: &str = "-";

/// Find the file a caller named, relative to the cwd or to the repo root.
///
/// `-` is stdin, not a path: without this guard the repo-relative fallback goes
/// looking for a file literally named `-` under the repo root.
pub fn resolve_input_path(path: &Path) -> PathBuf {
    if path == Path::new(STDIN_PATH) {
        return path.to_path_buf();
    }
    if path.is_file() {
        return path.to_path_buf();
    }
    let candidate = repo_root().join(path);
    if candidate.is_file() {
        return candidate;
    }
    path.to_path_buf()
}

pub fn read_input_text(path: &Path) -> Result<String> {
    if path == Path::new(STDIN_PATH) {
        let mut text = String::new();
        std::io::stdin()
            .read_to_string(&mut text)
            .map_err(|e| anyhow!("cannot read input {}: {e}", path.display()))?;
        return Ok(text);
    }
    // The message names the path the caller typed, not the resolved one, and is
    // worded exactly as the Python runtime words it.
    fs::read_to_string(resolve_input_path(path))
        .map_err(|e| anyhow!("cannot read input {}: {e}", path.display()))
}

pub fn load_input(path: &Path) -> Result<Value> {
    let text = read_input_text(path)?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|e| anyhow!("input {} is not valid JSON: {e}", path.display()))?;
    // arbitrary_precision keeps large integers exact, but it also lets a decimal
    // such as 1e999 parse even though Python's numeric domain cannot represent it.
    // Validate the complete document before filtering so an unselected value
    // cannot make runtime acceptance diverge.
    canonical_json(&value)
        .map_err(|e| anyhow!("input {} is not valid JSON: {e}", path.display()))?;
    if !value.is_object() {
        let kind = match &value {
            Value::Null => "null",
            Value::Bool(_) => "boolean",
            Value::Number(_) => "number",
            Value::String(_) => "string",
            Value::Array(_) => "array",
            Value::Object(_) => "object",
        };
        anyhow::bail!("input {} must be a JSON object, got {kind}", path.display());
    }
    Ok(value)
}

fn json_kind(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

fn validate_shipped_input_shape(rubric_id: &str, raw: &Value) -> Result<()> {
    let expected: &[(&str, &str)] = match rubric_id {
        "assistant-reply" => &[("prompt", "string"), ("reply", "string")],
        "agent-trajectory" => &[
            ("goal", "string"),
            ("steps", "array"),
            ("final_output", "string"),
        ],
        _ => return Ok(()),
    };
    for (name, wanted) in expected {
        if let Some(value) = raw.get(*name) {
            let valid = value.is_null()
                || (*wanted == "string" && value.is_string())
                || (*wanted == "array" && value.is_array());
            if !valid {
                anyhow::bail!(
                    "input field '{name}' must be a {wanted} or null, got {}",
                    json_kind(value)
                );
            }
        }
    }
    Ok(())
}

pub fn run_judgment(rubric_id: &str, input_path: &Path, mock_mode: bool) -> Result<JudgmentResult> {
    let rubric = load_rubric(rubric_id)?;
    let rubric_hash = rubric_content_hash(&rubric)?;
    let raw = load_input(input_path)?;
    validate_shipped_input_shape(&rubric.id, &raw)?;
    // Canonical from here on: every request is built from these exact bytes, and
    // both runtimes build the same ones.
    let filtered = filter_state(&raw, &rubric.state_filter)?;
    let state = CanonicalState::of(filtered.clone())?;
    let projection = build_state_projection(&rubric.state_filter, &filtered);

    let questions = build_questions(&rubric);
    // Before the request is built: an oversized state is a local failure, not a
    // round trip that comes back as an opaque API error.
    check_budget(&state, &rubric)?;
    info!(
        rubric = %rubric.id,
        model = %rubric.model,
        mock = mock_mode,
        questions = questions.len(),
        "funnel start"
    );

    let (answers, usage, request_id, model) = if mock_mode {
        let pinned = pinned_answers(&raw)?;
        let (answers, usage, request_id) =
            mock::system_one(&state, &questions, &rubric.model, &pinned);
        (answers, usage, request_id, rubric.model.clone())
    } else {
        let client = LiveClient::from_env()?;
        let (answers, usage, request_id, model) =
            client.system_one(&state, questions, &rubric.model)?;
        (answers, usage, request_id, model)
    };
    validate_answers(&rubric, &answers)?;

    let routed = route_verdict(&rubric, &answers);
    let floor = rubric
        .confidence_floors
        .get(&rubric.stakes)
        .copied()
        .unwrap_or(0.0);
    let gate_outcomes = evaluate_gates(&GateContext {
        rubric: &rubric,
        filtered_state: Some(&filtered),
        answers: Some(&answers),
        verdict: Some(&routed.verdict),
        confidence: Some(routed.confidence),
        confidence_floor: floor,
        confidence_candidate: routed.confidence_candidate.as_deref(),
        replay: false,
        budget_ok: true,
    });
    let (verdict, reason) = escalate_on_injection(&routed.verdict, &routed.reason, &gate_outcomes);

    info!(
        verdict = %verdict,
        stage = %routed.stage,
        confidence = routed.confidence,
        deciding = %routed.deciding.join(","),
        "funnel complete"
    );

    Ok(JudgmentResult {
        rubric_id: rubric.id,
        rubric_version: rubric.version.clone(),
        rubric_hash: rubric_hash.clone(),
        verdict,
        confidence: routed.confidence,
        stage: routed.stage,
        model,
        usage,
        answers,
        routing_reason: reason,
        mock: mock_mode,
        deciding_answers: routed.deciding,
        confidence_floor: floor,
        request_id,
        source_rubric_version: Some(rubric.version.clone()),
        source_rubric_hash: Some(rubric_hash),
        runtime: Runtime::default(),
        state_projection: projection,
        deterministic_gates: gate_outcomes,
    })
}

/// The message explaining why replaying *saved* would not re-derive its verdict.
///
/// `replay` exists to re-derive a verdict without paying for another call: for an
/// audit, for testing a threshold change, for explaining a past decision. All three
/// break silently if the rules moved underneath the answers, so a mismatch is
/// reported rather than routed. A result carrying no version at all cannot be
/// checked, which is the same failure wearing a different hat.
///
/// Worded identically to the Python runtime; scripts/check-parity.sh compares both.
pub fn version_drift(saved: &SavedJudgment, rubric_version: &str) -> Option<String> {
    match saved.rubric_version.as_deref() {
        None => Some(format!(
            "saved result carries no rubric_version, so it cannot be shown to have \
             been judged under {} {rubric_version}",
            saved.rubric_id
        )),
        Some(version) if version != rubric_version => Some(format!(
            "saved result was judged under {} {version}, but {rubric_version} is on disk",
            saved.rubric_id
        )),
        Some(_) => None,
    }
}

pub fn replay_judgment(saved: &SavedJudgment, allow_version_drift: bool) -> Result<JudgmentResult> {
    let rubric = load_rubric(&saved.rubric_id)?;
    validate_answers(&rubric, &saved.answers)?;
    let version_drift = version_drift(saved, &rubric.version);
    let current_hash = rubric_content_hash(&rubric)?;
    let hash_drift = saved.rubric_hash.as_ref().and_then(|saved_hash| {
        (saved_hash != &current_hash).then(|| {
            format!("saved result carries rubric_hash {saved_hash}, but {current_hash} is on disk")
        })
    });
    let blocking_drift = version_drift.as_ref().or(hash_drift.as_ref());
    if let Some(message) = blocking_drift {
        if !allow_version_drift {
            anyhow::bail!("{message}; re-run with --allow-version-drift to route it anyway");
        }
    }
    let routed = route_verdict(&rubric, &saved.answers);
    // Under drift the result records the version it was ROUTED under, so the reason
    // is the only place the original version survives. Say it there.
    let mut provenance_notes: Vec<String> = Vec::new();
    if let Some(message) = version_drift.or(hash_drift) {
        provenance_notes.push(message);
    }
    if saved.rubric_hash.is_none() {
        provenance_notes
            .push("saved result carries no rubric_hash, so content drift cannot be checked".into());
    }
    let prefix = if provenance_notes.is_empty() {
        "replay".to_string()
    } else {
        format!("replay ({})", provenance_notes.join("; "))
    };
    let floor = rubric
        .confidence_floors
        .get(&rubric.stakes)
        .copied()
        .unwrap_or(0.0);
    let gate_outcomes = evaluate_replay_gates(
        &GateContext {
            rubric: &rubric,
            filtered_state: None,
            answers: Some(&saved.answers),
            verdict: Some(&routed.verdict),
            confidence: Some(routed.confidence),
            confidence_floor: floor,
            confidence_candidate: routed.confidence_candidate.as_deref(),
            replay: true,
            budget_ok: true,
        },
        &saved.deterministic_gates,
    );
    let published_reason = format!("{prefix}: {}", routed.reason);
    let (verdict, published_reason) =
        escalate_on_injection(&routed.verdict, &published_reason, &gate_outcomes);
    Ok(JudgmentResult {
        rubric_id: rubric.id,
        rubric_version: rubric.version.clone(),
        rubric_hash: current_hash,
        verdict,
        confidence: routed.confidence,
        stage: routed.stage,
        model: saved.model.clone().unwrap_or_else(|| rubric.model.clone()),
        usage: saved.usage.clone(),
        answers: saved.answers.clone(),
        routing_reason: published_reason,
        mock: saved.mock,
        deciding_answers: routed.deciding,
        confidence_floor: floor,
        request_id: saved.request_id.clone(),
        source_rubric_version: saved
            .source_rubric_version
            .clone()
            .or_else(|| saved.rubric_version.clone()),
        source_rubric_hash: saved
            .source_rubric_hash
            .clone()
            .or_else(|| saved.rubric_hash.clone()),
        runtime: Runtime::default(),
        state_projection: saved.state_projection.clone(),
        deterministic_gates: gate_outcomes,
    })
}

pub fn answers_from_json(raw: &HashMap<String, Value>) -> Result<HashMap<String, Answer>> {
    raw.iter()
        .map(|(k, v)| {
            let answer: Answer = serde_json::from_value(v.clone())?;
            Ok((k.clone(), answer))
        })
        .collect()
}
