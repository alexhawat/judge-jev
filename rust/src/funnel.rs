use crate::models::{Answer, JudgmentResult};
use crate::routing::{aggregate_confidence, route_verdict};
use crate::rubric::load_rubric;
use crate::paths::repo_root;
use crate::typesafe::{build_questions, filter_state, mock, LiveClient};
use anyhow::{Context, Result};
use serde_json::Value;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use tracing::info;

pub fn resolve_input_path(path: &Path) -> PathBuf {
    if path.is_file() {
        return path.to_path_buf();
    }
    let candidate = repo_root().join(path);
    if candidate.is_file() {
        return candidate;
    }
    path.to_path_buf()
}

pub fn load_input(path: &Path) -> Result<Value> {
    let text = fs::read_to_string(path).with_context(|| format!("read input {}", path.display()))?;
    let value: Value = serde_json::from_str(&text)?;
    Ok(value)
}

pub fn run_judgment(rubric_id: &str, input_path: &Path, mock_mode: bool) -> Result<JudgmentResult> {
    let rubric = load_rubric(rubric_id)?;
    let raw = load_input(&resolve_input_path(input_path))?;
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
        let (answers, usage, request_id) = mock::system_one(&state, &questions, &rubric.model);
        (answers, usage, request_id, rubric.model.clone())
    } else {
        let client = LiveClient::from_env()?;
        let (answers, usage, request_id, model) = client.system_one(state, questions, &rubric.model)?;
        (answers, usage, request_id, model)
    };

    let (verdict, reason, stage) = route_verdict(&rubric, &answers);
    let confidence = aggregate_confidence(&answers);

    info!(verdict = %verdict, stage = %stage, confidence, "funnel complete");

    Ok(JudgmentResult {
        rubric_id: rubric.id,
        verdict,
        confidence,
        stage,
        model,
        usage,
        answers,
        routing_reason: reason,
        mock: mock_mode,
        request_id,
    })
}

pub fn replay_judgment(saved: &JudgmentResult) -> Result<JudgmentResult> {
    let rubric = load_rubric(&saved.rubric_id)?;
    let (verdict, reason, stage) = route_verdict(&rubric, &saved.answers);
    Ok(JudgmentResult {
        rubric_id: rubric.id,
        verdict,
        confidence: aggregate_confidence(&saved.answers),
        stage,
        model: saved.model.clone(),
        usage: saved.usage.clone(),
        answers: saved.answers.clone(),
        routing_reason: format!("replay: {reason}"),
        mock: saved.mock,
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
