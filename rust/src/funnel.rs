use crate::budget::check_budget;
use crate::canonical::CanonicalState;
use crate::models::{Answer, JudgmentResult, Runtime, SavedJudgment};
use crate::paths::repo_root;
use crate::routing::route_verdict;
use crate::rubric::load_rubric;
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
    Ok(value)
}

pub fn run_judgment(rubric_id: &str, input_path: &Path, mock_mode: bool) -> Result<JudgmentResult> {
    let rubric = load_rubric(rubric_id)?;
    let raw = load_input(input_path)?;
    // Canonical from here on: every request is built from these exact bytes, and
    // both runtimes build the same ones.
    let state = CanonicalState::of(filter_state(&raw, &rubric.state_filter)?)?;

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

    let routed = route_verdict(&rubric, &answers);

    info!(
        verdict = %routed.verdict,
        stage = %routed.stage,
        confidence = routed.confidence,
        deciding = %routed.deciding.join(","),
        "funnel complete"
    );

    Ok(JudgmentResult {
        rubric_id: rubric.id,
        rubric_version: rubric.version.clone(),
        verdict: routed.verdict,
        confidence: routed.confidence,
        stage: routed.stage,
        model,
        usage,
        answers,
        routing_reason: routed.reason,
        mock: mock_mode,
        deciding_answers: routed.deciding,
        confidence_floor: rubric
            .confidence_floors
            .get(&rubric.stakes)
            .copied()
            .unwrap_or(0.0),
        request_id,
        runtime: Runtime::default(),
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
    let drift = version_drift(saved, &rubric.version);
    if let Some(message) = &drift {
        if !allow_version_drift {
            anyhow::bail!("{message}; re-run with --allow-version-drift to route it anyway");
        }
    }
    let routed = route_verdict(&rubric, &saved.answers);
    // Under drift the result records the version it was ROUTED under, so the reason
    // is the only place the original version survives. Say it there.
    let prefix = match &drift {
        Some(message) => format!("replay ({message})"),
        None => "replay".to_string(),
    };
    let floor = rubric
        .confidence_floors
        .get(&rubric.stakes)
        .copied()
        .unwrap_or(0.0);
    Ok(JudgmentResult {
        rubric_id: rubric.id,
        rubric_version: rubric.version.clone(),
        verdict: routed.verdict,
        confidence: routed.confidence,
        stage: routed.stage,
        model: saved.model.clone().unwrap_or_else(|| rubric.model.clone()),
        usage: saved.usage.clone(),
        answers: saved.answers.clone(),
        routing_reason: format!("{prefix}: {}", routed.reason),
        mock: saved.mock,
        deciding_answers: routed.deciding,
        confidence_floor: floor,
        request_id: saved.request_id.clone(),
        runtime: Runtime::default(),
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
