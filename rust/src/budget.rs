//! The ~32k token budget a System One request has to fit in.
//!
//! Neither runtime checked it. Oversized state was sent, the API rejected it after
//! the round trip, and the caller got an opaque error indistinguishable from a
//! transient outage — both surface as exit 10, so "too much state" and "the API is
//! down" looked the same and were handled the same. `agent-trajectory` is the
//! rubric most likely to hit it: a long agentic session runs past 150k characters
//! without trying.
//!
//! `python/src/judge_jev/budget.py` carries the full rationale; this is its mirror
//! and must produce the same number and the same message. The estimate is made
//! before the request is built, over the canonical form from `canonical.rs` — the
//! exact bytes that would go on the wire, and the only measure the two runtimes can
//! agree on. The heuristic is bytes/4: not accurate tokenization, and it does not
//! need to be.

use crate::canonical::{canonical_json, canonical_size, CanonicalState};
use crate::models::Rubric;
use anyhow::{bail, Result};
use serde_json::{Map, Value};
use tracing::info;

/// Roughly what a System One call admits, shared between state and questions.
pub const DEFAULT_TOKEN_BUDGET: u64 = 32_000;

pub const TOKEN_BUDGET_ENV: &str = "JUDGE_JEV_TOKEN_BUDGET";

/// Characters per token, near enough. See the module docs.
const BYTES_PER_TOKEN: u64 = 4;

/// Round up, so a request is never estimated at zero tokens.
pub fn estimate_tokens(size_bytes: usize) -> u64 {
    (size_bytes as u64).div_ceil(BYTES_PER_TOKEN)
}

/// The ceiling, from the environment or the default.
pub fn token_budget() -> Result<u64> {
    let raw = match std::env::var(TOKEN_BUDGET_ENV) {
        Ok(value) => value,
        Err(_) => return Ok(DEFAULT_TOKEN_BUDGET),
    };
    if raw.trim().is_empty() {
        return Ok(DEFAULT_TOKEN_BUDGET);
    }
    match raw.parse::<u64>() {
        Ok(value) if value > 0 => Ok(value),
        _ => bail!("{TOKEN_BUDGET_ENV} must be a positive integer, got '{raw}'"),
    }
}

/// The questions as they are sent: type, instructions, criteria.
///
/// `stage` is routing metadata and never leaves the process, so it is not counted
/// against a budget it does not spend.
pub fn questions_payload(rubric: &Rubric) -> Result<Value> {
    let mut payload = Map::new();
    for (name, spec) in &rubric.questions {
        let mut question = Map::new();
        question.insert("type".into(), Value::String(spec.qtype.clone()));
        if let Some(instructions) = &spec.instructions {
            question.insert("instructions".into(), Value::String(instructions.clone()));
        }
        let criteria = serde_json::to_value(&spec.criteria)?;
        if !criteria.is_null() {
            question.insert("criteria".into(), criteria);
        }
        payload.insert(name.clone(), Value::Object(question));
    }
    Ok(Value::Object(payload))
}

/// The key worth shrinking first, with its estimate.
///
/// `questions` competes as a contributor of its own: when a rubric's questions are
/// what blows the budget, naming a state key would send the caller after the wrong
/// thing.
fn largest_contributor(state: &Value, questions_bytes: usize) -> Result<(String, u64)> {
    let mut sizes: Vec<(String, usize)> = Vec::new();
    if let Some(object) = state.as_object() {
        for (key, value) in object {
            sizes.push((key.clone(), canonical_size(value)?));
        }
    }
    sizes.push(("questions".to_string(), questions_bytes));
    // Sorted so the answer never depends on mapping order in either runtime.
    sizes.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    let (key, size) = sizes.remove(0);
    Ok((key, estimate_tokens(size)))
}

/// Estimate the request, log it, and refuse to send an oversized one.
///
/// Returns the estimate. Runs in mock mode too: the guard is about the request that
/// would be built, and catching an oversized trajectory in CI without paying for a
/// call is most of the value.
pub fn check_budget(state: &CanonicalState, rubric: &Rubric) -> Result<u64> {
    let questions_bytes = canonical_json(&questions_payload(rubric)?)?.len();
    let state_tokens = estimate_tokens(state.size());
    let questions_tokens = estimate_tokens(questions_bytes);
    let total = estimate_tokens(state.size() + questions_bytes);
    let budget = token_budget()?;

    // At INFO on every run, so it is visible long before it becomes a problem.
    info!("token estimate {total} of budget {budget} (state {state_tokens}, questions {questions_tokens})");

    if total > budget {
        let (key, key_tokens) = largest_contributor(&state.value, questions_bytes)?;
        bail!(
            "state is too large for one system_one call: estimated {total} tokens \
             against a {budget} token budget; largest contributor is '{key}' at \
             {key_tokens} tokens (raise {TOKEN_BUDGET_ENV} to override)"
        );
    }
    Ok(total)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn estimate_rounds_up() {
        // Mirrored in python/tests/test_budget.py.
        assert_eq!(estimate_tokens(0), 0);
        assert_eq!(estimate_tokens(1), 1);
        assert_eq!(estimate_tokens(4), 1);
        assert_eq!(estimate_tokens(5), 2);
        assert_eq!(estimate_tokens(4000), 1000);
    }

    #[test]
    fn largest_contributor_picks_the_biggest_key() {
        let state = json!({"goal": "short", "steps": "x".repeat(500)});
        let (key, _) = largest_contributor(&state, 10).unwrap();
        assert_eq!(key, "steps");
    }

    #[test]
    fn questions_can_be_the_largest_contributor() {
        let state = json!({"goal": "short"});
        let (key, _) = largest_contributor(&state, 10_000).unwrap();
        assert_eq!(key, "questions");
    }

    #[test]
    fn ties_break_by_name_so_both_runtimes_name_the_same_key() {
        let state = json!({"bbb": "xxxx", "aaa": "xxxx"});
        let (key, _) = largest_contributor(&state, 0).unwrap();
        assert_eq!(key, "aaa");
    }
}
