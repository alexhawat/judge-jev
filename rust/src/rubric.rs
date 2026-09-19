use crate::models::Rubric;
use crate::paths::rubrics_dir;
use anyhow::{Context, Result};
use std::fs;

pub fn list_rubric_ids() -> Result<Vec<String>> {
    let dir = rubrics_dir();
    let mut ids = Vec::new();
    for entry in fs::read_dir(&dir).with_context(|| format!("read rubrics dir {}", dir.display()))? {
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
    let text = fs::read_to_string(&path).with_context(|| format!("load rubric {rubric_id}"))?;
    let rubric: Rubric = serde_yaml::from_str(&text)?;
    Ok(rubric)
}

pub fn show_rubric(rubric_id: &str) -> Result<String> {
    let rubric = load_rubric(rubric_id)?;
    let mut lines = vec![
        format!("id: {}", rubric.id),
        format!("version: {}", rubric.version),
        format!("model: {}", rubric.model),
        format!("stakes: {}", rubric.stakes),
        format!("questions: {}", rubric.questions.len()),
    ];
    for (name, q) in &rubric.questions {
        lines.push(format!("  - {} ({}, stage={})", name, q.qtype, q.stage));
    }
    Ok(lines.join("\n"))
}
