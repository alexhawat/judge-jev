//! Provider boundary for one batched System One operation.

use crate::canonical::CanonicalState;
use crate::gates::sha256_prefixed;
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

pub fn validate_recorded_state(recorded: &Value, state: &CanonicalState) -> Result<()> {
    let recorded_hash = recorded
        .get("state_hash")
        .and_then(Value::as_str)
        .context("recorded replay needs an unredacted evaluated-content state_hash provenance")?;
    let current_hash = sha256_prefixed(state.text.as_bytes());
    if recorded_hash != current_hash {
        anyhow::bail!("recorded replay state hash does not match the newly filtered input");
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
                if saved.answers.is_empty() {
                    anyhow::bail!("recorded replay needs non-empty answers");
                }
                if saved.rubric_version.is_none() {
                    anyhow::bail!("recorded replay needs rubric_version provenance");
                }
                let recorded_requested = saved
                    .requested_model
                    .as_deref()
                    .context("recorded replay needs requested model provenance")?
                    .to_string();
                let recorded_resolved = saved
                    .model
                    .as_deref()
                    .context("recorded replay needs resolved model provenance")?
                    .to_string();
                if recorded_requested != model || recorded_resolved != model {
                    anyhow::bail!("recorded replay model provenance does not match the rubric pin");
                }
                Ok(BackendResponse {
                    answers: saved.answers,
                    usage: saved.usage,
                    request_id: saved.request_id,
                    requested_model: recorded_requested,
                    resolved_model: recorded_resolved,
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

#[derive(Debug, Deserialize)]
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
    let parsed = parse_cloudflare_response(response.as_bytes(), requested_model)?;
    Ok(BackendResponse {
        answers: parsed.answers,
        usage: parsed.usage,
        request_id,
        requested_model: requested_model.into(),
        resolved_model: parsed.model,
        provenance: "live_model".into(),
    })
}

fn parse_cloudflare_response(bytes: &[u8], requested_model: &str) -> Result<CloudflareResponse> {
    let value: Value = serde_json::from_slice(bytes).context("decode Cloudflare response")?;
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
    Ok(parsed)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

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

    #[test]
    fn cloudflare_parser_accepts_direct_and_wrapped_contracts_and_enforces_pin() {
        let direct = br#"{"model":"jev-1.13.0","answers":{"q":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1,"output_tokens":2}}"#;
        let wrapped = br#"{"success":true,"result":{"model":"jev-1.13.0","answers":{"q":{"type":"noul","noul":0.9}},"usage":{"input_tokens":1,"output_tokens":2}},"errors":[]}"#;
        assert_eq!(
            parse_cloudflare_response(direct, "jev-1.13.0")
                .unwrap()
                .model,
            "jev-1.13.0"
        );
        assert_eq!(
            parse_cloudflare_response(wrapped, "jev-1.13.0")
                .unwrap()
                .model,
            "jev-1.13.0"
        );
        assert!(parse_cloudflare_response(direct, "jev-next")
            .unwrap_err()
            .to_string()
            .contains("rubric pins"));
        assert!(parse_cloudflare_response(b"not-json", "jev-1.13.0").is_err());
        assert_eq!(retry_after_seconds(None, Some("nan")), None);
        assert_eq!(retry_after_seconds(None, Some("inf")), None);
        assert_eq!(retry_after_seconds(None, Some("-1")), None);
    }

    #[test]
    fn recorded_backend_requires_answer_version_and_model_provenance() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "judge-jev-recorded-backend-{}-{stamp}.json",
            std::process::id()
        ));
        let base = serde_json::json!({
            "rubric_id": "assistant-reply",
            "rubric_version": "3.0.0",
            "rubric_hash": format!("sha256:{}", "0".repeat(64)),
            "answers": {"q": {"type": "noul", "noul": 0.9}},
            "model": "jev-1.13.0",
            "requested_model": "jev-1.13.0"
        });
        let state = CanonicalState::of(serde_json::json!({"x": 1})).unwrap();
        let state_record =
            serde_json::json!({"state_hash": sha256_prefixed(state.text.as_bytes())});
        validate_recorded_state(&state_record, &state).unwrap();
        assert!(validate_recorded_state(&serde_json::json!({}), &state).is_err());
        assert!(validate_recorded_state(
            &serde_json::json!({"state_hash": format!("sha256:{}", "0".repeat(64))}),
            &state,
        )
        .is_err());
        fs::write(&path, serde_json::to_vec(&base).unwrap()).unwrap();
        let valid = run_backend(
            "replay",
            &state,
            HashMap::new(),
            "jev-1.13.0",
            &Value::Null,
            Some(&path),
        )
        .unwrap();
        assert_eq!(valid.provenance, "recorded_model");

        let mut missing_requested = base.clone();
        missing_requested["requested_model"] = Value::Null;
        let mut changed_model = base.clone();
        changed_model["model"] = Value::String("jev-next".into());
        let mut empty_answers = base.clone();
        empty_answers["answers"] = serde_json::json!({});
        for invalid in [missing_requested, changed_model, empty_answers] {
            fs::write(&path, serde_json::to_vec(&invalid).unwrap()).unwrap();
            assert!(run_backend(
                "replay",
                &state,
                HashMap::new(),
                "jev-1.13.0",
                &Value::Null,
                Some(&path),
            )
            .is_err());
        }
        fs::remove_file(path).unwrap();
    }
}
