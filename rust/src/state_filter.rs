//! Selecting the state a rubric actually judges.
//!
//! `state_filter` used to be whole top-level keys and nothing else, so a key was
//! all-or-nothing: no way to send `ticket.subject` without `ticket.body`, or to
//! keep `steps[].tool` while dropping `steps[].output`. Everything sent is billed
//! and counts against the token budget, so the only lever a caller had was
//! restructuring the input before calling — which means every caller writes its own
//! pre-processor, and the rubric's declared `state_filter` stops describing what is
//! actually judged.
//!
//! `python/src/judge_jev/state_filter.py` carries the full rationale and the
//! wording of the syntax; this is its mirror, and the two must agree byte for byte
//! on the filtered state. In short:
//!
//!   * `a` selects a top-level key, exactly as before.
//!   * `a.b` selects a nested key.
//!   * `a[]` maps over a list; `a[].b` projects a field from each element.
//!   * The filtered state keeps the original shape, so a path written in
//!     `instructions` still resolves against what the model is sent.
//!   * A missing path warns and is dropped; `{ path: …, required: true }` makes it
//!     fail the run instead.
//!
//! In YAML, `[` and `]` are flow indicators, so a path is written bare in a block
//! sequence and in block mapping form, but must be quoted inside an inline mapping:
//! `{ path: "steps[].tool", required: true }`.
//!
//! "Missing" means the walk cannot proceed: a key is absent, or a segment is
//! applied to the wrong kind of value. An element that lacks a projected field is
//! not missing — the list is there, so it keeps its length and that element
//! contributes `{}`. Preserving the length matters: dropping elements would
//! silently rewrite a trajectory that questions like `locate.looping` are counting.

use crate::models::StatePath;
use crate::typesafe::MOCK_ANSWERS_KEY;
use anyhow::{bail, Result};
use serde_json::{Map, Value};
use tracing::warn;

/// One step of a path: a key, or `[]`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Segment {
    Key(String),
    Each,
}

/// Split a path into segments. The Python runtime parses identically.
pub fn parse_path(path: &str) -> Result<Vec<Segment>> {
    if path.is_empty() {
        bail!("a state_filter path cannot be empty");
    }
    let mut segments = Vec::new();
    for raw in path.split('.') {
        let each = raw.ends_with("[]");
        let name = if each { &raw[..raw.len() - 2] } else { raw };
        if name.is_empty() {
            bail!("state_filter path '{path}' has an empty segment");
        }
        if name.contains('[') || name.contains(']') {
            bail!("state_filter path '{path}': a segment is a key, optionally followed by '[]'");
        }
        segments.push(Segment::Key(name.to_string()));
        if each {
            segments.push(Segment::Each);
        }
    }
    Ok(segments)
}

/// The part of `value` that `segments` selects, keeping the original shape.
///
/// `None` means the path does not resolve here.
fn project(value: &Value, segments: &[Segment]) -> Option<Value> {
    let Some((head, rest)) = segments.split_first() else {
        return Some(value.clone());
    };

    match head {
        Segment::Each => {
            let items = value.as_array()?;
            if rest.is_empty() {
                return Some(Value::Array(items.clone()));
            }
            // An element that does not carry the projected field contributes an
            // empty object rather than vanishing, so the list keeps its length.
            let projected = items
                .iter()
                .map(|item| project(item, rest).unwrap_or_else(|| Value::Object(Map::new())))
                .collect();
            Some(Value::Array(projected))
        }
        Segment::Key(name) => {
            let child = project(value.as_object()?.get(name)?, rest)?;
            let mut out = Map::new();
            out.insert(name.clone(), child);
            Some(Value::Object(out))
        }
    }
}

/// Fold one path's projection into the result built so far.
fn merge(existing: &mut Value, addition: Value) {
    match (existing, addition) {
        (Value::Object(into), Value::Object(from)) => {
            for (key, value) in from {
                match into.get_mut(&key) {
                    Some(slot) => merge(slot, value),
                    None => {
                        into.insert(key, value);
                    }
                }
            }
        }
        (Value::Array(into), Value::Array(from)) => {
            for (index, value) in from.into_iter().enumerate() {
                match into.get_mut(index) {
                    Some(slot) => merge(slot, value),
                    None => into.push(value),
                }
            }
        }
        // Nothing to fold: a broader selection of the same key already covers this.
        _ => {}
    }
}

/// Keep only what a rubric declares, dropping the mock override block.
pub fn filter_state(raw: &Value, paths: &[StatePath]) -> Result<Value> {
    let Some(obj) = raw.as_object() else {
        bail!("input must be a JSON object, got {}", json_kind(raw));
    };
    if paths.is_empty() {
        let mut kept = obj.clone();
        kept.remove(MOCK_ANSWERS_KEY);
        return Ok(Value::Object(kept));
    }

    let mut source = obj.clone();
    source.remove(MOCK_ANSWERS_KEY);
    let source = Value::Object(source);

    let mut filtered = Value::Object(Map::new());
    let mut missing: Vec<&str> = Vec::new();
    let mut missing_required: Vec<&str> = Vec::new();
    let mut resolved: Vec<(Vec<Segment>, Value)> = Vec::new();
    for entry in paths {
        if entry.path == MOCK_ANSWERS_KEY {
            continue;
        }
        let segments = parse_path(&entry.path)?;
        match project(&source, &segments) {
            Some(projected) => resolved.push((segments, projected)),
            None if entry.required => missing_required.push(&entry.path),
            None => missing.push(&entry.path),
        }
    }

    if !missing_required.is_empty() {
        bail!(
            "required state_filter paths are missing from the input: {}",
            missing_required.join(", ")
        );
    }
    if !missing.is_empty() {
        // Questions referencing these paths will be judging absent data.
        warn!(
            "state_filter paths missing from input: {}",
            missing.join(", ")
        );
    }
    resolved.sort_by_key(|(segments, _)| segments.len());
    let mut selected: Vec<Vec<Segment>> = Vec::new();
    for (segments, projected) in resolved {
        if selected.iter().any(|parent| segments.starts_with(parent)) {
            continue;
        }
        merge(&mut filtered, projected);
        selected.push(segments);
    }
    Ok(filtered)
}

fn json_kind(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Every case here has a twin in `python/tests/test_state_filter.py`, asserting
    /// the same selection on the same input. The two runtimes share no code, so the
    /// duplication is what makes "identical semantics" checkable.
    fn trajectory() -> Value {
        json!({
            "goal": "find the readme",
            "steps": [
                {"tool": "glob", "input": "**/README.md", "output": "README.md"},
                {"tool": "read", "input": "README.md", "output": "# judge-jev"}
            ],
            "final_output": "done",
            "_mock_answers": {"screen.judgeable": {"type": "noul", "noul": 0.9}}
        })
    }

    fn paths(specs: &[&str]) -> Vec<StatePath> {
        specs
            .iter()
            .map(|s| StatePath {
                path: (*s).to_string(),
                required: false,
            })
            .collect()
    }

    #[test]
    fn a_plain_key_still_selects_the_whole_value() {
        let filtered = filter_state(&trajectory(), &paths(&["goal"])).unwrap();
        assert_eq!(filtered, json!({"goal": "find the readme"}));
    }

    #[test]
    fn nested_key_leaves_its_siblings_behind() {
        let raw = json!({"ticket": {"subject": "card declined", "body": "xxxx"}});
        let filtered = filter_state(&raw, &paths(&["ticket.subject"])).unwrap();
        assert_eq!(filtered, json!({"ticket": {"subject": "card declined"}}));
    }

    #[test]
    fn projection_keeps_the_original_shape() {
        let filtered = filter_state(&trajectory(), &paths(&["steps[].tool"])).unwrap();
        assert_eq!(
            filtered,
            json!({"steps": [{"tool": "glob"}, {"tool": "read"}]})
        );
    }

    #[test]
    fn two_projections_of_one_list_merge() {
        let filtered = filter_state(
            &trajectory(),
            &paths(&["goal", "steps[].tool", "steps[].input"]),
        )
        .unwrap();
        assert_eq!(
            filtered,
            json!({
                "goal": "find the readme",
                "steps": [
                    {"tool": "glob", "input": "**/README.md"},
                    {"tool": "read", "input": "README.md"}
                ]
            })
        );
    }

    #[test]
    fn the_expensive_field_is_the_one_left_out() {
        let filtered = filter_state(&trajectory(), &paths(&["steps[].tool"])).unwrap();
        assert!(!crate::canonical::canonical_json(&filtered)
            .unwrap()
            .contains("output"));
    }

    #[test]
    fn a_whole_list_can_still_be_selected() {
        let by_each = filter_state(&trajectory(), &paths(&["steps[]"])).unwrap();
        let by_key = filter_state(&trajectory(), &paths(&["steps"])).unwrap();
        assert_eq!(by_each, by_key);
    }

    #[test]
    fn an_element_missing_the_field_keeps_the_lists_length() {
        let raw = json!({"steps": [{"tool": "glob"}, {"note": "no tool here"}, {"tool": "read"}]});
        let filtered = filter_state(&raw, &paths(&["steps[].tool"])).unwrap();
        assert_eq!(
            filtered,
            json!({"steps": [{"tool": "glob"}, {}, {"tool": "read"}]})
        );
    }

    #[test]
    fn a_missing_path_is_dropped_and_the_run_continues() {
        let filtered =
            filter_state(&json!({"prompt": "hi"}), &paths(&["prompt", "reply"])).unwrap();
        assert_eq!(filtered, json!({"prompt": "hi"}));
    }

    #[test]
    fn a_segment_applied_to_the_wrong_kind_of_value_is_missing() {
        let filtered = filter_state(
            &json!({"steps": {"not": "a list"}}),
            &paths(&["steps[].tool"]),
        )
        .unwrap();
        assert_eq!(filtered, json!({}));
        let filtered =
            filter_state(&json!({"goal": "a string"}), &paths(&["goal.nested"])).unwrap();
        assert_eq!(filtered, json!({}));
    }

    #[test]
    fn a_required_path_fails_the_run_instead_of_warning() {
        let declared = vec![
            StatePath {
                path: "prompt".into(),
                required: false,
            },
            StatePath {
                path: "reply".into(),
                required: true,
            },
        ];
        let err = filter_state(&json!({"prompt": "hi"}), &declared).unwrap_err();
        assert_eq!(
            err.to_string(),
            "required state_filter paths are missing from the input: reply"
        );
    }

    #[test]
    fn mock_answers_are_never_selectable() {
        let filtered = filter_state(&trajectory(), &paths(&["_mock_answers"])).unwrap();
        assert_eq!(filtered, json!({}));
        let everything = filter_state(&trajectory(), &[]).unwrap();
        assert!(everything.get("_mock_answers").is_none());
    }

    #[test]
    fn no_paths_means_everything_but_the_mock_block() {
        let filtered = filter_state(&trajectory(), &[]).unwrap();
        let mut keys: Vec<&String> = filtered.as_object().unwrap().keys().collect();
        keys.sort();
        assert_eq!(keys, vec!["final_output", "goal", "steps"]);
    }

    #[test]
    fn json_null_is_selected_and_satisfies_required_path() {
        let declared = vec![StatePath {
            path: "a".into(),
            required: true,
        }];
        assert_eq!(
            filter_state(&json!({"a": null}), &declared).unwrap(),
            json!({"a": null})
        );
    }

    #[test]
    fn parent_selection_wins_over_descendant_regardless_of_order() {
        let raw = json!({"a": [1, {"x": 2, "keep": 3}, null]});
        for declared in [paths(&["a", "a[].x"]), paths(&["a[].x", "a"])] {
            assert_eq!(filter_state(&raw, &declared).unwrap(), raw);
        }
    }

    #[test]
    fn non_object_input_is_rejected_at_filter_boundary() {
        for value in [json!(null), json!([]), json!("text"), json!(1), json!(true)] {
            assert!(filter_state(&value, &[]).is_err());
        }
    }

    #[test]
    fn malformed_paths_are_rejected() {
        for path in ["", "a..b", ".a", "a.", "a[b]", "a[][]", "a]b"] {
            assert!(
                parse_path(path).is_err(),
                "expected {path:?} to be rejected"
            );
        }
    }

    #[test]
    fn state_filter_entries_accept_both_forms() {
        // Block form, where `[]` needs no quoting. A bare path and a mapping, which
        // is the shape a rubric actually writes.
        let parsed: Vec<StatePath> =
            serde_yaml::from_str("- goal\n- path: steps[].tool\n  required: true\n").unwrap();
        assert_eq!(
            parsed,
            vec![
                StatePath {
                    path: "goal".into(),
                    required: false
                },
                StatePath {
                    path: "steps[].tool".into(),
                    required: true
                },
            ]
        );
    }

    #[test]
    fn a_flow_mapping_needs_the_path_quoted() {
        // `[` and `]` are flow indicators, so `{ path: steps[].tool }` is a YAML
        // parse error rather than a judge-jev one. Pinned here because a rubric
        // author writing the inline style will hit it, and the error they get comes
        // from the YAML parser with no mention of state_filter.
        assert!(serde_yaml::from_str::<Vec<StatePath>>("- { path: steps[].tool }\n").is_err());
        let quoted: Vec<StatePath> =
            serde_yaml::from_str("- { path: \"steps[].tool\", required: true }\n").unwrap();
        assert_eq!(quoted[0].path, "steps[].tool");
        assert!(quoted[0].required);
    }

    #[test]
    fn malformed_state_filter_entries_are_rejected() {
        for yaml in [
            "- { required: true }\n",
            "- { path: goal, requried: true }\n",
            "- 7\n",
        ] {
            assert!(
                serde_yaml::from_str::<Vec<StatePath>>(yaml).is_err(),
                "expected {yaml:?} to be rejected"
            );
        }
    }
}
