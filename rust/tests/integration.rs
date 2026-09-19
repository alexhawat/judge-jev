use judge_jev::funnel::{replay_judgment, run_judgment};
use judge_jev::routing::route_verdict;
use judge_jev::rubric::load_rubric;
use std::collections::HashMap;
use std::path::PathBuf;

fn fixtures() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("fixtures")
}

#[test]
fn assistant_reply_mock_pass() {
    let result = run_judgment(
        "assistant-reply",
        &fixtures().join("assistant-reply-pass.json"),
        true,
    )
    .expect("run");
    assert_eq!(result.rubric_id, "assistant-reply");
    assert!(result.mock);
    assert_eq!(result.model, "jev-1.13.0");
}

#[test]
fn injection_escalates() {
    let result = run_judgment(
        "assistant-reply",
        &fixtures().join("assistant-reply-injection.json"),
        true,
    )
    .expect("run");
    assert_eq!(result.verdict, "escalate");
    assert!(result.routing_reason.to_lowercase().contains("injection"));
}

#[test]
fn agent_trajectory_mock() {
    let result = run_judgment(
        "agent-trajectory",
        &fixtures().join("agent-trajectory-pass.json"),
        true,
    )
    .expect("run");
    assert_eq!(result.rubric_id, "agent-trajectory");
}

#[test]
fn replay_matches() {
    let saved = run_judgment(
        "assistant-reply",
        &fixtures().join("assistant-reply-pass.json"),
        true,
    )
    .expect("run");
    let replayed = replay_judgment(&saved).expect("replay");
    assert_eq!(replayed.verdict, saved.verdict);
    assert_eq!(replayed.answers.len(), saved.answers.len());
}

#[test]
fn route_skip_not_judgeable() {
    let rubric = load_rubric("assistant-reply").expect("rubric");
    let mut answers = HashMap::new();
    answers.insert(
        "screen.judgeable".to_string(),
        judge_jev::models::Answer::Noul { noul: 0.2 },
    );
    let (verdict, _reason, stage) = route_verdict(&rubric, &answers);
    assert_eq!(verdict, "skip");
    assert_eq!(stage, "screen");
}
