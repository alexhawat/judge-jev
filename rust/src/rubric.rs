//! Rubric loading and validation from shared YAML.
//!
//! Validation mirrors `judge_jev/rubric.py`: a rubric one runtime rejects must be
//! rejected by the other, or the "shared rubric" guarantee is not a guarantee.

use crate::models::{
    fields_for_type, is_text_field, stage_index, RoutingRule, Rubric, OPS, STAGE_ORDER, VERDICTS,
};
use crate::paths::rubrics_dir;
use crate::state_filter::parse_path;
use anyhow::{bail, Context, Result};
use std::fs;

const QUESTION_TYPES: [&str; 3] = ["noul", "choice", "score"];

pub fn list_rubric_ids() -> Result<Vec<String>> {
    let dir = rubrics_dir();
    let mut ids = Vec::new();
    for entry in
        fs::read_dir(&dir).with_context(|| format!("read rubrics dir {}", dir.display()))?
    {
        let entry = entry?;
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) == Some("yaml") {
            if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
                ids.push(stem.to_string());
            }
        }
    }
    ids.sort();
    Ok(ids)
}

pub fn load_rubric(rubric_id: &str) -> Result<Rubric> {
    let path = rubrics_dir().join(format!("{rubric_id}.yaml"));
    let text = fs::read_to_string(&path)
        .with_context(|| format!("rubric not found: {rubric_id} ({})", path.display()))?;
    parse_rubric(&text).with_context(|| format!("invalid rubric {rubric_id}"))
}

/// Parse and validate a rubric from YAML text. Exposed so validation can be tested
/// without touching the filesystem or the process environment.
pub fn parse_rubric(text: &str) -> Result<Rubric> {
    let rubric: Rubric = serde_yaml::from_str(text).context("parse rubric YAML")?;
    validate(&rubric)?;
    Ok(rubric)
}

fn validate(rubric: &Rubric) -> Result<()> {
    validate_questions(rubric)?;

    for entry in &rubric.state_filter {
        parse_path(&entry.path).with_context(|| format!("invalid state_filter path '{}'", entry.path))?;
    }

    for (name, floor) in &rubric.confidence_floors {
        if !floor.is_finite() || !(0.0..=1.0).contains(floor) {
            bail!("confidence_floors.{name} must be finite and between 0 and 1");
        }
    }

    if !rubric.confidence_floors.contains_key(&rubric.stakes) {
        let mut known: Vec<&String> = rubric.confidence_floors.keys().collect();
        known.sort();
        bail!(
            "stakes '{}' has no entry in confidence_floors ({:?}); automatic verdicts would be ungated",
            rubric.stakes,
            known
        );
    }

    validate_rules(rubric)
}

fn validate_questions(rubric: &Rubric) -> Result<()> {
    if rubric.questions.is_empty() {
        bail!("questions must be a non-empty mapping");
    }
    for (name, spec) in &rubric.questions {
        if !QUESTION_TYPES.contains(&spec.qtype.as_str()) {
            bail!(
                "questions.{name}: type must be one of {QUESTION_TYPES:?}, got '{}'",
                spec.qtype
            );
        }
        if !STAGE_ORDER.contains(&spec.stage.as_str()) {
            bail!(
                "questions.{name}: stage must be one of {STAGE_ORDER:?}, got '{}'",
                spec.stage
            );
        }
        match spec.qtype.as_str() {
            "choice" if spec.criteria.as_mapping().is_none_or(|criteria| criteria.is_empty()) => {
                bail!("questions.{name}: a choice question needs a non-empty criteria mapping")
            }
            "score" => match spec.criteria.as_sequence() {
                Some(levels) if !levels.is_empty() => {}
                _ => bail!("questions.{name}: a score question needs a non-empty criteria list"),
            },
            _ => {}
        }
    }
    Ok(())
}

fn validate_rules(rubric: &Rubric) -> Result<()> {
    let rules = &rubric.routing.rules;
    if rules.is_empty() {
        bail!("routing.rules must be a non-empty list");
    }

    for (index, rule) in rules.iter().enumerate() {
        let where_ = format!("routing.rules[{index}]");
        if !VERDICTS.contains(&rule.verdict.as_str()) {
            bail!(
                "{where_}: verdict must be one of {VERDICTS:?}, got '{}'",
                rule.verdict
            );
        }
        if rule.default && !rule.all.is_empty() {
            bail!("{where_}: a default rule cannot also declare conditions");
        }
        if !rule.default && rule.all.is_empty() {
            bail!("{where_}: rule needs either 'all' conditions or 'default: true'");
        }
        for (i, condition) in rule.all.iter().enumerate() {
            validate_condition(rubric, rule, condition, &format!("{where_}.all[{i}]"))?;
        }
    }

    let defaults: Vec<usize> = rules
        .iter()
        .enumerate()
        .filter(|(_, r)| r.default)
        .map(|(i, _)| i)
        .collect();
    if defaults.len() > 1 {
        bail!(
            "routing.rules declares {} default rules; at most one is allowed",
            defaults.len()
        );
    }
    if let Some(&first) = defaults.first() {
        if first != rules.len() - 1 {
            bail!("the default rule must be last; rules after it can never match");
        }
    }
    Ok(())
}

fn validate_condition(
    rubric: &Rubric,
    _rule: &RoutingRule,
    condition: &crate::models::Condition,
    where_: &str,
) -> Result<()> {
    let Some(question) = rubric.questions.get(&condition.answer) else {
        bail!(
            "{where_}: references answer '{}', which is not a question in this rubric",
            condition.answer
        );
    };
    if !OPS.contains(&condition.op.as_str()) {
        bail!(
            "{where_}: unknown op '{}'; expected one of {OPS:?}",
            condition.op
        );
    }

    let allowed = fields_for_type(&question.qtype);
    if !allowed.contains(&condition.field.as_str()) {
        bail!(
            "{where_}: field '{}' is not available on a '{}' answer; expected one of {allowed:?}",
            condition.field,
            question.qtype
        );
    }

    if is_text_field(&condition.field) {
        if condition.op != "==" && condition.op != "!=" {
            bail!(
                "{where_}: field '{}' supports only == and !=",
                condition.field
            );
        }
        // A choice can only ever be one of its declared labels.
        if let Some(labels) = question.criteria.as_mapping() {
            let wanted = condition.value.as_text();
            let known: Vec<String> = labels
                .keys()
                .filter_map(|k| k.as_str().map(str::to_string))
                .collect();
            if !known.contains(&wanted) {
                let mut sorted = known;
                sorted.sort();
                bail!(
                    "{where_}: value '{wanted}' is not a label of '{}'; expected one of {sorted:?}",
                    condition.answer
                );
            }
        }
    } else {
        let Some(value) = condition.value.as_number() else {
            bail!(
                "{where_}: field '{}' needs a numeric value, got '{}'",
                condition.field,
                condition.value.as_text()
            );
        };
        if !value.is_finite() {
            bail!("{where_}: numeric value must be finite");
        }
        if (condition.field == "noul" || condition.field == "confidence")
            && !(0.0..=1.0).contains(&value)
        {
            bail!("{where_}: {} threshold must be between 0 and 1", condition.field);
        }
        if condition.field == "score" {
            let highest = question
                .criteria
                .as_sequence()
                .map(|levels| levels.len().saturating_sub(1))
                .unwrap_or(0);
            if value < 0.0 || value > highest as f64 {
                bail!("{where_}: score threshold must be between 0 and {highest}");
            }
        }
    }
    Ok(())
}

pub fn show_rubric(rubric_id: &str) -> Result<String> {
    let rubric = load_rubric(rubric_id)?;
    let mut lines = vec![
        format!("id: {}", rubric.id),
        format!("version: {}", rubric.version),
        format!("model: {}", rubric.model),
        format!(
            "stakes: {} (confidence floor {:.2})",
            rubric.stakes,
            rubric.confidence_floor()
        ),
        format!("questions: {}", rubric.questions.len()),
    ];
    // Funnel order, so the listing reads the way the funnel runs and both runtimes
    // print the same thing regardless of mapping order.
    let mut names: Vec<&String> = rubric.questions.keys().collect();
    names.sort_by_key(|name| {
        let q = &rubric.questions[*name];
        (stage_index(&q.stage).unwrap_or(usize::MAX), (*name).clone())
    });
    for name in names {
        let q = &rubric.questions[name];
        lines.push(format!("  - {} ({}, stage={})", name, q.qtype, q.stage));
    }
    lines.push(format!("rules: {}", rubric.routing.rules.len()));
    for rule in &rubric.routing.rules {
        if rule.default {
            lines.push(format!("  - {} (default)", rule.verdict));
        } else {
            let clauses: Vec<String> = rule
                .all
                .iter()
                .map(|c| format!("{}.{} {} {}", c.answer, c.field, c.op, c.value.as_text()))
                .collect();
            lines.push(format!(
                "  - {} when {}",
                rule.verdict,
                clauses.join(" and ")
            ));
        }
    }
    Ok(lines.join("\n"))
}
