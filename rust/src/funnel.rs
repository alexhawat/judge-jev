use crate::models::{Answer, JudgmentResult, SavedJudgment};
use crate::paths::repo_root;
use crate::routing::route_verdict;
use crate::rubric::load_rubric;
use crate::typesafe::{build_questions, filter_state, mock, pinned_answers, LiveClient};
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
    let state = filter_state(&raw, &rubric.state_filter);

    let questions = build_questions(&rubric);
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
            client.system_one(state, questions, &rubric.model)?;
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
    })
}

pub fn replay_judgment(saved: &SavedJudgment) -> Result<JudgmentResult> {
    let rubric = load_rubric(&saved.rubric_id)?;
    let routed = route_verdict(&rubric, &saved.answers);
    let floor = rubric
        .confidence_floors
        .get(&rubric.stakes)
        .copied()
        .unwrap_or(0.0);
    Ok(JudgmentResult {
        rubric_id: rubric.id,
        verdict: routed.verdict,
        confidence: routed.confidence,
        stage: routed.stage,
        model: saved.model.clone().unwrap_or_else(|| rubric.model.clone()),
        usage: saved.usage.clone(),
        answers: saved.answers.clone(),
        routing_reason: format!("replay: {}", routed.reason),
        mock: saved.mock,
        deciding_answers: routed.deciding,
        confidence_floor: floor,
        request_id: saved.request_id.clone(),
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
