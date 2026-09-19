//! TypeSafe System One HTTP client.
//!
//! Request shape matches typesafe-sdk Python (`POST /v1/systemone`):
//! `{ "state", "model", "questions" }` where each question has `type`, optional
//! `instructions`, and `criteria`.

use crate::models::{Answer, QuestionSpec, Rubric, Usage};
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use tracing::info;

const DEFAULT_BASE_URL: &str = "https://api.typesafe.ai";
const SYSTEM_ONE_PATH: &str = "/v1/systemone";

#[derive(Debug, Serialize)]
struct SystemOneRequest {
    state: Value,
    model: String,
    questions: HashMap<String, QuestionPayload>,
}

#[derive(Debug, Serialize)]
pub struct QuestionPayload {
    #[serde(rename = "type")]
    qtype: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    instructions: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    criteria: Option<Value>,
}

#[derive(Debug, Deserialize)]
struct SystemOneResponse {
    model: String,
    usage: UsageWire,
    answers: HashMap<String, AnswerWire>,
}

#[derive(Debug, Deserialize)]
struct UsageWire {
    input_tokens: Option<u64>,
    output_tokens: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type")]
enum AnswerWire {
    #[serde(rename = "noul")]
    Noul { noul: f64 },
    #[serde(rename = "choice")]
    Choice {
        choice: String,
        confidence: f64,
        probabilities: HashMap<String, f64>,
    },
    #[serde(rename = "score")]
    Score {
        score: f64,
        confidence: f64,
        probabilities: HashMap<String, f64>,
    },
}

pub fn filter_state(raw: &Value, keys: &[String]) -> Value {
    if keys.is_empty() {
        return raw.clone();
    }
    let Some(obj) = raw.as_object() else {
        return raw.clone();
    };
    let mut filtered = serde_json::Map::new();
    for key in keys {
        if let Some(v) = obj.get(key) {
            filtered.insert(key.clone(), v.clone());
        }
    }
    Value::Object(filtered)
}

pub fn build_questions(rubric: &Rubric) -> HashMap<String, QuestionPayload> {
    rubric
        .questions
        .iter()
        .map(|(name, spec)| (name.clone(), question_payload(spec)))
        .collect()
}

fn question_payload(spec: &QuestionSpec) -> QuestionPayload {
    QuestionPayload {
        qtype: spec.qtype.clone(),
        instructions: spec.instructions.clone(),
        criteria: if spec.criteria.is_null() {
            None
        } else {
            Some(serde_json::to_value(&spec.criteria).unwrap_or(Value::Null))
        },
    }
}

pub struct LiveClient {
    api_key: String,
    base_url: String,
}

impl LiveClient {
    pub fn from_env() -> Result<Self> {
        let api_key = std::env::var("TYPESAFE_API_KEY")
            .context("TYPESAFE_API_KEY is required for live mode (use --mock for CI)")?;
        let base_url = std::env::var("TYPESAFE_BASE_URL").unwrap_or_else(|_| DEFAULT_BASE_URL.to_string());
        Ok(Self { api_key, base_url })
    }

    pub fn system_one(
        &self,
        state: Value,
        questions: HashMap<String, QuestionPayload>,
        model: &str,
    ) -> Result<(HashMap<String, Answer>, Usage, Option<String>, String)> {
        let body = SystemOneRequest {
            state,
            model: model.to_string(),
            questions,
        };
        let url = format!("{}{}", self.base_url.trim_end_matches('/'), SYSTEM_ONE_PATH);
        let response = minreq::post(url)
            .with_header("Authorization", format!("Bearer {}", self.api_key))
            .with_header("Content-Type", "application/json")
            .with_header("Accept", "application/json")
            .with_json(&body)
            .context("TypeSafe HTTP request failed")?
            .send()
            .context("TypeSafe HTTP transport failed")?;

        let request_id = response
            .headers
            .get("x-typesafe-request-id")
            .map(|v| v.to_string());

        let status = response.status_code;
        if !(200..300).contains(&status) {
            let text = String::from_utf8_lossy(&response.as_bytes());
            bail!("TypeSafe API error {status}: {text}");
        }

        let parsed: SystemOneResponse =
            serde_json::from_slice(response.as_bytes()).context("decode TypeSafe response")?;
        info!(
            model = %parsed.model,
            input_tokens = ?parsed.usage.input_tokens,
            output_tokens = ?parsed.usage.output_tokens,
            request_id = ?request_id,
            "system_one complete"
        );

        let answers = parsed
            .answers
            .into_iter()
            .map(|(k, v)| (k, wire_to_answer(v)))
            .collect();

        let usage = Usage {
            input_tokens: parsed.usage.input_tokens,
            output_tokens: parsed.usage.output_tokens,
        };
        Ok((answers, usage, request_id, parsed.model))
    }
}

fn wire_to_answer(wire: AnswerWire) -> Answer {
    match wire {
        AnswerWire::Noul { noul } => Answer::Noul { noul },
        AnswerWire::Choice {
            choice,
            confidence,
            probabilities,
        } => Answer::Choice {
            choice,
            confidence,
            probabilities,
        },
        AnswerWire::Score {
            score,
            confidence,
            probabilities,
        } => Answer::Score {
            score,
            confidence,
            probabilities,
        },
    }
}

pub mod mock {
    use super::*;
    use sha2::{Digest, Sha256};

    pub fn system_one(
        state: &Value,
        questions: &HashMap<String, QuestionPayload>,
        model: &str,
    ) -> (HashMap<String, Answer>, Usage, Option<String>) {
        let blob = json!({"state": state, "questions": questions.keys().collect::<Vec<_>>()});
        let digest = Sha256::digest(blob.to_string().as_bytes());
        let seed = u64::from_be_bytes(digest[0..8].try_into().unwrap());

        let text = state.to_string().to_lowercase();
        let has_reply = state.get("reply").map(|v| !v.is_null()).unwrap_or(false);
        let has_trajectory = state.get("steps").and_then(|v| v.as_array()).map(|a| !a.is_empty()).unwrap_or(false);
        let injection_hit = text.contains("ignore all prior") || text.contains("always return pass");

        let mut answers = HashMap::new();
        for (idx, (name, question)) in questions.iter().enumerate() {
            let local = ((seed as f64 + idx as f64 * 17.0) % 1000.0) / 1000.0;
            let answer = match question.qtype.as_str() {
                "noul" => {
                    let noul = if name.contains("judgeable") {
                        if has_reply || has_trajectory { 0.9 } else { 0.2 }
                    } else if name.contains("injection") {
                        if injection_hit { 0.92 } else { 0.12 }
                    } else if name.contains("harmful") || name.contains("unauthorized_write") {
                        0.15
                    } else {
                        0.55
                    };
                    Answer::Noul { noul }
                }
                "choice" => {
                    let labels: Vec<String> = question
                        .criteria
                        .as_ref()
                        .and_then(|c| c.as_object())
                        .map(|m| m.keys().cloned().collect())
                        .unwrap_or_else(|| vec!["a".into(), "b".into()]);
                    let pick = labels[(local * labels.len() as f64) as usize % labels.len()].clone();
                    let mut probabilities = HashMap::new();
                    for label in &labels {
                        probabilities.insert(label.clone(), if label == &pick { 0.7 } else { 0.1 });
                    }
                    Answer::Choice {
                        choice: pick,
                        confidence: 0.75 + local * 0.2,
                        probabilities,
                    }
                }
                "score" => Answer::Score {
                    score: 2.5 + local,
                    confidence: 0.7 + local * 0.25,
                    probabilities: HashMap::from([
                        ("2".into(), 0.2),
                        ("3".into(), 0.5),
                        ("4".into(), 0.3),
                    ]),
                },
                _ => Answer::Noul { noul: 0.5 },
            };
            answers.insert(name.clone(), answer);
        }
        info!(model = %model, questions = questions.len(), "mock system_one");
        (
            answers,
            Usage {
                input_tokens: Some(120),
                output_tokens: Some(45),
            },
            Some("mock-request-id".into()),
        )
    }
}
