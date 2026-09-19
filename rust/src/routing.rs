//! Route verdicts from Jev answers in code.
//!
//! Rules are declarative data, shared verbatim with the Python runtime. Nothing
//! here interprets a string as an expression: the two runtimes must agree on every
//! verdict, so this file mirrors `judge_jev/routing.py` step for step, including
//! the short-circuiting AND and the wording of the reasons it produces.

use crate::models::{stage_index, Answer, Condition, FieldValue, Rubric, GATED_VERDICTS};
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct Routed {
    pub verdict: String,
    pub reason: String,
    pub stage: String,
    pub deciding: Vec<String>,
    pub confidence: f64,
}

/// Compare one answer field against a condition's value.
fn compare(condition: &Condition, answer: &Answer) -> Option<bool> {
    let field = answer.field(&condition.field)?;
    let result = match field {
        FieldValue::Text(left) => {
            let right = condition.value.as_text();
            match condition.op.as_str() {
                "==" => left == right,
                "!=" => left != right,
                // Ordering a label is meaningless; the rubric loader rejects it.
                _ => return None,
            }
        }
        FieldValue::Num(left) => {
            let right = condition.value.as_number()?;
            match condition.op.as_str() {
                "<" => left < right,
                "<=" => left <= right,
                ">" => left > right,
                ">=" => left >= right,
                "==" => left == right,
                "!=" => left != right,
                _ => return None,
            }
        }
    };
    Some(result)
}

/// The earliest funnel stage among the questions a rule reads.
fn stage_for(rubric: &Rubric, answer_ids: &[String]) -> String {
    answer_ids
        .iter()
        .filter_map(|id| rubric.stage_of(id))
        .filter_map(|stage| stage_index(stage).map(|i| (i, stage)))
        .min_by_key(|(i, _)| *i)
        .map(|(_, stage)| stage.to_string())
        .unwrap_or_else(|| "route".to_string())
}

/// Confidence in the verdict: the least certain answer that produced it.
///
/// Only the answers the matched rule actually read count. Taking the minimum means
/// one uncertain input is enough to hold the whole verdict back, which is the point
/// of a floor.
pub fn decision_confidence(answers: &HashMap<String, Answer>, deciding: &[String]) -> f64 {
    deciding
        .iter()
        .filter_map(|id| answers.get(id))
        .map(|answer| answer.confidence())
        // No deciding answer means nothing established the verdict, so report 0.0.
        .fold(None::<f64>, |acc, c| Some(acc.map_or(c, |a: f64| a.min(c))))
        .unwrap_or(0.0)
}

/// The first rule whose conditions all hold wins. A rule that cannot be evaluated
/// escalates rather than being skipped.
pub fn route_verdict(rubric: &Rubric, answers: &HashMap<String, Answer>) -> Routed {
    for rule in &rubric.routing.rules {
        if rule.default {
            // The catch-all decided nothing, so there is no confidence to report.
            return Routed {
                verdict: rule.verdict.clone(),
                reason: rule.reason.clone(),
                stage: "route".to_string(),
                deciding: Vec::new(),
                confidence: 0.0,
            };
        }

        let mut matched = true;
        for condition in &rule.all {
            // A missing answer means the funnel cannot establish what this rule was
            // there to establish. Never treat that as "did not match".
            let Some(answer) = answers.get(&condition.answer) else {
                return unevaluable(
                    rubric,
                    rule.verdict.as_str(),
                    &condition.answer,
                    &rule.answer_ids(),
                );
            };
            let Some(holds) = compare(condition, answer) else {
                return unevaluable(
                    rubric,
                    rule.verdict.as_str(),
                    &condition.answer,
                    &rule.answer_ids(),
                );
            };
            if !holds {
                matched = false;
                break;
            }
        }
        if !matched {
            continue;
        }

        let deciding = rule.answer_ids();
        let confidence = decision_confidence(answers, &deciding);
        let stage = stage_for(rubric, &deciding);
        let floor = rubric.confidence_floor();

        if GATED_VERDICTS.contains(&rule.verdict.as_str()) && confidence < floor {
            // The rule matched, but not confidently enough to act on automatically.
            return Routed {
                verdict: "review".to_string(),
                reason: format!(
                    "{} Downgraded from '{}': confidence {:.2} is below the {} floor of {:.2}.",
                    rule.reason, rule.verdict, confidence, rubric.stakes, floor
                ),
                stage,
                deciding,
                confidence,
            };
        }

        return Routed {
            verdict: rule.verdict.clone(),
            reason: rule.reason.clone(),
            stage,
            deciding,
            confidence,
        };
    }

    // No rule matched and the rubric declared no default.
    Routed {
        verdict: "review".to_string(),
        reason: "No routing rule matched.".to_string(),
        stage: "route".to_string(),
        deciding: Vec::new(),
        confidence: 0.0,
    }
}

fn unevaluable(rubric: &Rubric, verdict: &str, answer_id: &str, rule_answers: &[String]) -> Routed {
    Routed {
        verdict: "escalate".to_string(),
        reason: format!("Could not evaluate '{verdict}' rule: answer '{answer_id}' is missing."),
        stage: stage_for(rubric, rule_answers),
        deciding: Vec::new(),
        confidence: 0.0,
    }
}
