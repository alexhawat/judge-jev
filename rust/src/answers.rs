//! Validation at the answer boundary shared by live, mock, and replay paths.

use crate::models::{Answer, Rubric};
use anyhow::{bail, Result};
use std::collections::{HashMap, HashSet};

fn unit(value: f64, where_: &str) -> Result<()> {
    if !value.is_finite() || !(0.0..=1.0).contains(&value) {
        bail!("{where_} must be finite and between 0 and 1");
    }
    Ok(())
}

fn probabilities(values: &HashMap<String, f64>, allowed: &HashSet<String>, where_: &str) -> Result<()> {
    for (key, probability) in values {
        if !allowed.contains(key) {
            let mut expected: Vec<&String> = allowed.iter().collect();
            expected.sort();
            bail!("{where_} has invalid key '{key}'; expected one of {expected:?}");
        }
        unit(*probability, &format!("{where_}.{key}"))?;
    }
    Ok(())
}

pub fn validate_answers(rubric: &Rubric, answers: &HashMap<String, Answer>) -> Result<()> {
    if answers.is_empty() {
        bail!("system_one returned no answers");
    }
    let mut unknown: Vec<&String> = answers
        .keys()
        .filter(|answer_id| !rubric.questions.contains_key(*answer_id))
        .collect();
    unknown.sort();
    if !unknown.is_empty() {
        bail!("answers contain unknown question ids: {unknown:?}");
    }

    for (answer_id, answer) in answers {
        let question = &rubric.questions[answer_id];
        let where_ = format!("answers.{answer_id}");
        if answer.kind() != question.qtype {
            bail!(
                "{where_}.type must be '{}', got '{}'",
                question.qtype,
                answer.kind()
            );
        }
        match answer {
            Answer::Noul { noul } => unit(*noul, &format!("{where_}.noul"))?,
            Answer::Choice {
                choice,
                confidence,
                probabilities: values,
            } => {
                unit(*confidence, &format!("{where_}.confidence"))?;
                let allowed: HashSet<String> = question
                    .criteria
                    .as_mapping()
                    .into_iter()
                    .flatten()
                    .filter_map(|(key, _)| key.as_str().map(str::to_string))
                    .collect();
                if !allowed.contains(choice) {
                    let mut expected: Vec<&String> = allowed.iter().collect();
                    expected.sort();
                    bail!("{where_}.choice must be one of {expected:?}, got '{choice}'");
                }
                probabilities(values, &allowed, &format!("{where_}.probabilities"))?;
            }
            Answer::Score {
                score,
                confidence,
                legend,
                probabilities: values,
            } => {
                unit(*confidence, &format!("{where_}.confidence"))?;
                let levels = question.criteria.as_sequence().map_or(0, Vec::len);
                if !score.is_finite() || *score < 0.0 || *score > (levels.saturating_sub(1)) as f64 {
                    bail!(
                        "{where_}.score must be finite and between 0 and {}",
                        levels.saturating_sub(1)
                    );
                }
                let allowed: HashSet<String> = (0..levels).map(|index| index.to_string()).collect();
                probabilities(values, &allowed, &format!("{where_}.probabilities"))?;
                if let Some(legend) = legend {
                    if legend.keys().any(|key| !allowed.contains(key)) {
                        bail!("{where_}.legend keys must be valid score levels");
                    }
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rubric::load_rubric;

    fn score(value: f64, confidence: f64, key: &str) -> Answer {
        Answer::Score {
            score: value,
            confidence,
            legend: None,
            probabilities: HashMap::from([(key.to_string(), 0.5)]),
        }
    }

    #[test]
    fn fractional_scores_are_valid_but_domains_are_enforced() {
        let rubric = load_rubric("assistant-reply").unwrap();
        let valid = HashMap::from([("score.helpfulness".to_string(), score(1.5, 0.5, "1"))]);
        validate_answers(&rubric, &valid).unwrap();
        for answer in [score(4.1, 0.5, "4"), score(1.5, -0.1, "1"), score(1.5, 0.5, "99")] {
            let bad = HashMap::from([("score.helpfulness".to_string(), answer)]);
            assert!(validate_answers(&rubric, &bad).is_err());
        }
    }

    #[test]
    fn noul_must_be_finite_and_in_unit_interval() {
        let rubric = load_rubric("assistant-reply").unwrap();
        for value in [f64::NAN, f64::INFINITY, -0.1, 1.1] {
            let bad = HashMap::from([("screen.judgeable".to_string(), Answer::Noul { noul: value })]);
            assert!(validate_answers(&rubric, &bad).is_err());
        }
    }
}
