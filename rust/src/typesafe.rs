//! TypeSafe System One HTTP client.
//!
//! Request shape matches typesafe-sdk Python (`POST /v1/systemone`):
//! `{ "state", "model", "questions" }` where each question has `type`, optional
//! `instructions`, and `criteria`.

use crate::canonical::CanonicalState;
use crate::models::{Answer, QuestionSpec, Rubric, Usage};
use crate::retry::{is_retryable_status, retry_after_seconds, Outcome, RetryPolicy};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use tracing::info;

/// Fixtures may carry this key to pin exact mock answers. It is stripped from the
/// state before any request is built, so it never reaches the model.
pub const MOCK_ANSWERS_KEY: &str = "_mock_answers";

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
        /// Always returned by the API; kept so a score can be interpreted downstream.
        #[serde(default)]
        legend: Option<HashMap<String, Value>>,
        probabilities: HashMap<String, f64>,
    },
}

/// Read pinned mock answers out of a fixture, if it carries any.
pub fn pinned_answers(raw: &Value) -> anyhow::Result<HashMap<String, Answer>> {
    let Some(block) = raw.get(MOCK_ANSWERS_KEY) else {
        return Ok(HashMap::new());
    };
    let map = block.as_object().ok_or_else(|| {
        anyhow::anyhow!("{MOCK_ANSWERS_KEY} must be a JSON object of answer overrides")
    })?;
    let mut pinned = HashMap::new();
    for (name, value) in map {
        let answer: Answer = serde_json::from_value(value.clone())
            .with_context(|| format!("{MOCK_ANSWERS_KEY}.{name}"))?;
        pinned.insert(name.clone(), answer);
    }
    Ok(pinned)
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

/// Answers, token usage, the request id when the API reported one, and the model
/// the API says it used.
pub type SystemOneResult = (HashMap<String, Answer>, Usage, Option<String>, String);

pub struct LiveClient {
    api_key: String,
    base_url: String,
    retry: RetryPolicy,
}

impl LiveClient {
    pub fn from_env() -> Result<Self> {
        let api_key = std::env::var("TYPESAFE_API_KEY")
            .context("TYPESAFE_API_KEY is required for live mode (use --mock for CI)")?;
        let base_url =
            std::env::var("TYPESAFE_BASE_URL").unwrap_or_else(|_| DEFAULT_BASE_URL.to_string());
        Ok(Self {
            api_key,
            base_url,
            retry: RetryPolicy::from_env()?,
        })
    }

    pub fn system_one(
        &self,
        state: &CanonicalState,
        questions: HashMap<String, QuestionPayload>,
        model: &str,
    ) -> Result<SystemOneResult> {
        let body = SystemOneRequest {
            // The canonical text itself, not the object: `state` is documented as
            // text, a JSON object, or an array, and sending the text is the only
            // way the bytes survive two HTTP clients neither runtime owns.
            state: Value::String(state.text.clone()),
            model: model.to_string(),
            questions,
        };
        let url = format!("{}{}", self.base_url.trim_end_matches('/'), SYSTEM_ONE_PATH);

        // One attempt, classified for the retry policy. A 408, a 429, a 5xx or a
        // transport failure is worth another go; any other 4xx is the request's own
        // fault and will fail again identically.
        let response = self.retry.run(|timeout_secs, _attempt| {
            let request = minreq::post(&url)
                .with_timeout(timeout_secs)
                .with_header("Authorization", format!("Bearer {}", self.api_key))
                .with_header("Content-Type", "application/json")
                .with_header("Accept", "application/json")
                .with_json(&body);
            let request = match request.context("TypeSafe HTTP request failed") {
                Ok(request) => request,
                // Serializing the body failed: no amount of retrying fixes that.
                Err(err) => return Outcome::Fatal(err),
            };
            match request.send() {
                Ok(response) => {
                    let status = response.status_code;
                    if (200..300).contains(&status) {
                        return Outcome::Done(response);
                    }
                    let text = String::from_utf8_lossy(response.as_bytes()).to_string();
                    let error = anyhow::anyhow!("TypeSafe API error {status}: {text}");
                    if is_retryable_status(status) {
                        let after = retry_after_seconds(
                            response.headers.get("retry-after-ms").map(|v| v.as_str()),
                            response.headers.get("retry-after").map(|v| v.as_str()),
                        );
                        Outcome::Retry { error, after }
                    } else {
                        Outcome::Fatal(error)
                    }
                }
                // Could not reach or read from the server, or the attempt timed
                // out. The SDK retries both.
                Err(err) => Outcome::Retry {
                    error: anyhow::Error::new(err).context("TypeSafe HTTP transport failed"),
                    after: None,
                },
            }
        })?;

        let request_id = response
            .headers
            .get("x-typesafe-request-id")
            .map(|v| v.to_string());

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
            legend,
            probabilities,
        } => Answer::Score {
            score,
            confidence,
            legend,
            probabilities,
        },
    }
}

pub mod mock {
    use super::*;

    /// A stable per-question value in [0, 1).
    ///
    /// FNV-1a over the question name, chosen because both runtimes can compute it
    /// identically: the mock must be deterministic within a runtime AND agree with
    /// the Python mock, which hashing the serialized state cannot guarantee (the two
    /// languages do not emit byte-identical JSON).
    fn local_for(name: &str) -> f64 {
        let mut hash: u32 = 2166136261;
        for byte in name.as_bytes() {
            hash = (hash ^ u32::from(*byte)).wrapping_mul(16777619);
        }
        f64::from(hash % 1000) / 1000.0
    }

    pub fn system_one(
        state: &CanonicalState,
        questions: &HashMap<String, QuestionPayload>,
        model: &str,
        pinned: &HashMap<String, Answer>,
    ) -> (HashMap<String, Answer>, Usage, Option<String>) {
        // The canonical text, so the substring scan below reads exactly the bytes a
        // live call would have sent — and exactly the bytes the Python mock scans.
        let text = state.text.to_lowercase();
        let has_reply = state
            .value
            .get("reply")
            .map(|v| !v.is_null())
            .unwrap_or(false);
        let has_trajectory = state
            .value
            .get("steps")
            .and_then(|v| v.as_array())
            .map(|a| !a.is_empty())
            .unwrap_or(false);
        let injection_hit =
            text.contains("ignore all prior") || text.contains("always return pass");

        let mut answers = HashMap::new();
        // Sorted so iteration order never depends on HashMap seeding.
        let mut names: Vec<&String> = questions.keys().collect();
        names.sort();
        for name in names {
            let question = &questions[name];
            if let Some(answer) = pinned.get(name) {
                answers.insert(name.clone(), answer.clone());
                continue;
            }
            let local = local_for(name);
            let answer = match question.qtype.as_str() {
                "noul" => {
                    let noul = if name.contains("judgeable") {
                        if has_reply || has_trajectory {
                            0.9
                        } else {
                            0.2
                        }
                    } else if name.contains("injection") {
                        if injection_hit {
                            0.92
                        } else {
                            0.12
                        }
                    } else if name.contains("harmful") || name.contains("unauthorized_write") {
                        0.15
                    } else if name.contains("escalate") {
                        0.1
                    } else {
                        0.2
                    };
                    Answer::Noul { noul }
                }
                "choice" => {
                    // Sorted so the pick does not depend on mapping order, which
                    // differs between serde_json here and the YAML loader in Python.
                    let mut labels: Vec<String> = question
                        .criteria
                        .as_ref()
                        .and_then(|c| c.as_object())
                        .map(|m| m.keys().cloned().collect())
                        .unwrap_or_else(|| vec!["a".into(), "b".into()]);
                    labels.sort();
                    let pick =
                        labels[(local * labels.len() as f64) as usize % labels.len()].clone();
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
                "score" => {
                    let levels: Vec<String> = question
                        .criteria
                        .as_ref()
                        .and_then(|c| c.as_array())
                        .map(|a| {
                            a.iter()
                                .map(|v| v.as_str().unwrap_or_default().to_string())
                                .collect()
                        })
                        .unwrap_or_else(|| vec!["low".into(), "high".into()]);
                    let count = levels.len().max(1);
                    Answer::Score {
                        score: 2.5 + local,
                        confidence: 0.75 + local * 0.2,
                        legend: Some(
                            levels
                                .iter()
                                .enumerate()
                                .map(|(i, l)| (i.to_string(), Value::String(l.clone())))
                                .collect(),
                        ),
                        probabilities: (0..count)
                            .map(|i| (i.to_string(), 1.0 / count as f64))
                            .collect(),
                    }
                }
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
