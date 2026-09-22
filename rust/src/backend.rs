//! Provider boundary for one batched System One operation.

use crate::canonical::CanonicalState;
use crate::models::{Answer, SavedJudgment, Usage};
use crate::retry::{is_retryable_status, retry_after_seconds, Outcome, RetryPolicy};
use crate::typesafe::{mock, pinned_answers, LiveClient, QuestionPayload};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::path::Path;

pub const BACKEND_IDS: [&str; 3] = ["typesafe", "cloudflare", "replay"];
const CLOUDFLARE_MODEL: &str = "typesafe/jev";

#[derive(Debug, Clone, Serialize)]
pub struct BackendCapabilities {
    pub question_types: Vec<String>,
    pub uncertainty: HashMap<String, String>,
}

impl Default for BackendCapabilities {
    fn default() -> Self {
        Self {
            question_types: vec!["noul".into(), "choice".into(), "score".into()],
            uncertainty: [
                ("noul".into(), "distance_from_half".into()),
                ("choice".into(), "distribution_confidence".into()),
                ("score".into(), "distribution_confidence".into()),
            ]
            .into_iter()
            .collect(),
        }
    }
}

pub struct BackendResponse {
    pub answers: HashMap<String, Answer>,
    pub usage: Usage,
    pub request_id: Option<String>,
    pub requested_model: String,
    pub resolved_model: String,
    pub provenance: String,
}

pub fn resolve_backend(explicit: Option<&str>, mock_mode: bool) -> Result<String, String> {
    let env_backend = std::env::var("JUDGE_JEV_BACKEND").ok();
    let selected = explicit
        .map(ToOwned::to_owned)
        .or(env_backend)
        .unwrap_or_else(|| {
            if mock_mode {
                "replay".into()
            } else {
                "typesafe".into()
            }
        });
    if !BACKEND_IDS.contains(&selected.as_str()) {
        return Err(format!(
            "unknown backend {selected:?}; expected one of {}",
            BACKEND_IDS.join(", ")
        ));
    }
    if mock_mode && selected != "replay" {
        return Err(
            "--mock is the canned replay backend and cannot be combined with a live backend".into(),
        );
    }
    Ok(selected)
}

pub fn validate_capabilities(
    capabilities: &BackendCapabilities,
    questions: &HashMap<String, QuestionPayload>,
) -> Result<()> {
    for (name, question) in questions {
        if !capabilities.question_types.contains(&question.qtype) {
            anyhow::bail!(
                "backend does not support {:?} question {:?}",
                question.qtype,
                name
            );
        }
    }
    Ok(())
}

pub fn run_backend(
    backend_id: &str,
    state: &CanonicalState,
    questions: HashMap<String, QuestionPayload>,
    model: &str,
    raw: &Value,
    replay_input: Option<&Path>,
) -> Result<BackendResponse> {
    validate_capabilities(&BackendCapabilities::default(), &questions)?;
    match backend_id {
        "typesafe" => {
            let client = LiveClient::from_env()?;
            let (answers, usage, request_id, resolved_model) =
                client.system_one(state, questions, model)?;
            Ok(BackendResponse {
                answers,
                usage,
                request_id,
                requested_model: model.into(),
                resolved_model,
                provenance: "live_model".into(),
            })
        }
        "replay" => {
            if let Some(path) = replay_input {
                let saved: SavedJudgment =
                    serde_json::from_str(&std::fs::read_to_string(path).with_context(|| {
                        format!("cannot load replay record {}", path.display())
                    })?)
                    .context("replay record must be a saved judgment")?;
                let resolved_model = saved.model.clone().unwrap_or_else(|| model.into());
                Ok(BackendResponse {
                    answers: saved.answers,
                    usage: saved.usage,
                    request_id: saved.request_id,
                    requested_model: saved.requested_model.unwrap_or_else(|| model.into()),
                    resolved_model,
                    provenance: "recorded_model".into(),
                })
            } else {
                let pinned = pinned_answers(raw)?;
                let (answers, usage, request_id) =
                    mock::system_one(state, &questions, model, &pinned);
                Ok(BackendResponse {
                    answers,
                    usage,
                    request_id,
                    requested_model: model.into(),
                    resolved_model: model.into(),
                    provenance: "canned_demo".into(),
                })
            }
        }
        "cloudflare" => cloudflare_system_one(state, questions, model),
        _ => anyhow::bail!("unknown backend {backend_id:?}"),
    }
}

#[derive(Serialize)]
struct CloudflareInput {
    state: String,
    questions: HashMap<String, QuestionPayload>,
}

#[derive(Serialize)]
struct CloudflareRequest {
    model: String,
    input: CloudflareInput,
}

#[derive(Deserialize)]
struct CloudflareResponse {
    model: String,
    answers: HashMap<String, Answer>,
    usage: Usage,
}

#[derive(Deserialize)]
struct CloudflareEnvelope {
    success: bool,
    #[serde(default)]
    result: Option<CloudflareResponse>,
    #[serde(default)]
    errors: Value,
}

fn cloudflare_system_one(
    state: &CanonicalState,
    questions: HashMap<String, QuestionPayload>,
    requested_model: &str,
) -> Result<BackendResponse> {
    let token = std::env::var("CLOUDFLARE_API_TOKEN")
        .context("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are required")?;
    let account = std::env::var("CLOUDFLARE_ACCOUNT_ID")
        .context("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are required")?;
    let url = std::env::var("JUDGE_JEV_CLOUDFLARE_URL").unwrap_or_else(|_| {
        format!("https://api.cloudflare.com/client/v4/accounts/{account}/ai/run")
    });
    let body = CloudflareRequest {
        model: CLOUDFLARE_MODEL.into(),
        input: CloudflareInput {
            state: state.text.clone(),
            questions,
        },
    };
    let retry = RetryPolicy::from_env()?;
    let response = retry.run(|timeout_secs, _attempt| {
        let request = minreq::post(&url)
            .with_timeout(timeout_secs)
            .with_header("Authorization", format!("Bearer {token}"))
            .with_header("Content-Type", "application/json")
            .with_json(&body);
        let request = match request.context("Cloudflare request serialization failed") {
            Ok(request) => request,
            Err(error) => return Outcome::Fatal(error),
        };
        match request.send() {
            Ok(response) if (200..300).contains(&response.status_code) => Outcome::Done(response),
            Ok(response) => {
                let status = response.status_code;
                let body = String::from_utf8_lossy(response.as_bytes()).to_string();
                let error = anyhow::anyhow!("Cloudflare API error {status}: {body}");
                if is_retryable_status(status) {
                    Outcome::Retry {
                        error,
                        after: retry_after_seconds(
                            response.headers.get("retry-after-ms").map(String::as_str),
                            response.headers.get("retry-after").map(String::as_str),
                        ),
                    }
                } else {
                    Outcome::Fatal(error)
                }
            }
            Err(error) => Outcome::Retry {
                error: anyhow::Error::new(error).context("Cloudflare HTTP transport failed"),
                after: None,
            },
        }
    })?;
    let request_id = response.headers.get("cf-ray").cloned();
    let value: Value =
        serde_json::from_slice(response.as_bytes()).context("decode Cloudflare response")?;
    let parsed: CloudflareResponse = if value.get("success").is_some() {
        let envelope: CloudflareEnvelope = serde_json::from_value(value)?;
        if !envelope.success {
            anyhow::bail!("Cloudflare API error: {}", envelope.errors);
        }
        envelope
            .result
            .context("Cloudflare response has no result")?
    } else {
        serde_json::from_value(value)?
    };
    if parsed.model != requested_model {
        anyhow::bail!(
            "Cloudflare alias {CLOUDFLARE_MODEL:?} resolved to {:?}, but rubric pins {requested_model:?}",
            parsed.model
        );
    }
    Ok(BackendResponse {
        answers: parsed.answers,
        usage: parsed.usage,
        request_id,
        requested_model: requested_model.into(),
        resolved_model: parsed.model,
        provenance: "live_model".into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backend_precedence_and_conflicts_are_explicit() {
        let old = std::env::var("JUDGE_JEV_BACKEND").ok();
        std::env::remove_var("JUDGE_JEV_BACKEND");
        assert_eq!(resolve_backend(None, false).unwrap(), "typesafe");
        assert_eq!(resolve_backend(None, true).unwrap(), "replay");
        assert!(resolve_backend(Some("typo"), false)
            .unwrap_err()
            .contains("unknown backend"));
        assert!(resolve_backend(Some("typesafe"), true)
            .unwrap_err()
            .contains("cannot be combined"));
        if let Some(value) = old {
            std::env::set_var("JUDGE_JEV_BACKEND", value);
        }
    }

    #[test]
    fn capabilities_distinguish_noul_distance_from_distribution_confidence() {
        let capabilities = BackendCapabilities::default();
        assert_eq!(capabilities.uncertainty["noul"], "distance_from_half");
        assert_eq!(capabilities.uncertainty["score"], "distribution_confidence");
        assert_eq!(
            capabilities.uncertainty["choice"],
            "distribution_confidence"
        );
    }
}
