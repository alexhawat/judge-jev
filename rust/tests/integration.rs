//! Mirrors python/tests/test_routing.py. Both runtimes must reach the same verdict
//! from the same answers, so these cases are kept deliberately parallel.

use judge_jev::funnel::{replay_judgment, run_judgment};
use judge_jev::models::{Answer, SavedJudgment};
use judge_jev::routing::{decision_confidence, route_verdict};
use judge_jev::rubric::load_rubric;
use judge_jev::EXIT_USAGE;
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;

/// One row of the verdict table: label, answer overrides, expected verdict, expected stage.
type VerdictCase<'a> = (&'a str, Vec<(&'a str, Answer)>, &'a str, &'a str);

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn fixtures() -> PathBuf {
    repo().join("fixtures")
}

fn noul(value: f64) -> Answer {
    Answer::Noul { noul: value }
}

fn score(value: f64, confidence: f64) -> Answer {
    Answer::Score {
        score: value,
        confidence,
        legend: Some(HashMap::from([
            ("0".to_string(), Value::String("low".into())),
            ("4".to_string(), Value::String("best".into())),
        ])),
        probabilities: HashMap::from([("0".to_string(), 0.5), ("4".to_string(), 0.5)]),
    }
}

fn choice(label: &str, confidence: f64) -> Answer {
    Answer::Choice {
        choice: label.to_string(),
        confidence,
        probabilities: HashMap::from([(label.to_string(), confidence)]),
    }
}

/// A full answer set that routes to pass, so each test can perturb one thing.
fn healthy() -> HashMap<String, Answer> {
    HashMap::from([
        ("screen.judgeable".to_string(), noul(0.95)),
        ("screen.injection".to_string(), noul(0.05)),
        ("locate.hallucination_risk".to_string(), noul(0.1)),
        ("locate.harmful".to_string(), noul(0.02)),
        ("route.escalate".to_string(), noul(0.05)),
        ("profile.intent".to_string(), choice("answer", 0.9)),
        ("score.helpfulness".to_string(), score(3.0, 0.9)),
        ("score.coherence".to_string(), score(3.0, 0.9)),
    ])
}

fn with(overrides: Vec<(&str, Answer)>) -> HashMap<String, Answer> {
    let mut answers = healthy();
    for (k, v) in overrides {
        answers.insert(k.to_string(), v);
    }
    answers
}

#[test]
fn verdict_table() {
    let rubric = load_rubric("assistant-reply").expect("rubric");
    let cases: Vec<VerdictCase> = vec![
        ("pass", vec![], "pass", "score"),
        (
            "skip when not judgeable",
            vec![("screen.judgeable", noul(0.2))],
            "skip",
            "screen",
        ),
        (
            "escalate on injection",
            vec![("screen.injection", noul(0.9))],
            "escalate",
            "screen",
        ),
        (
            "escalate on harmful",
            vec![("locate.harmful", noul(0.85))],
            "escalate",
            "locate",
        ),
        (
            "escalate when flagged",
            vec![("route.escalate", noul(0.8))],
            "escalate",
            "route",
        ),
        (
            "fail on unhelpful refusal",
            vec![
                ("profile.intent", choice("refusal", 0.9)),
                ("score.helpfulness", score(0.5, 0.9)),
            ],
            "fail",
            "profile",
        ),
        (
            "review on hallucination risk",
            vec![
                ("locate.hallucination_risk", noul(0.8)),
                ("score.helpfulness", score(3.0, 0.5)),
            ],
            "review",
            "locate",
        ),
        (
            "review by default",
            vec![("score.helpfulness", score(1.0, 0.9))],
            "review",
            "route",
        ),
    ];

    for (name, overrides, expected_verdict, expected_stage) in cases {
        let routed = route_verdict(&rubric, &with(overrides));
        assert_eq!(routed.verdict, expected_verdict, "{name}");
        assert_eq!(routed.stage, expected_stage, "{name}");
    }
}

#[test]
fn confidence_comes_from_deciding_answers_only() {
    let rubric = load_rubric("assistant-reply").expect("rubric");
    let answers = with(vec![
        // Very confident, but read by no matched rule.
        ("locate.harmful", noul(0.0)),
        ("score.helpfulness", score(3.0, 0.72)),
        ("score.coherence", score(3.0, 0.80)),
    ]);

    let routed = route_verdict(&rubric, &answers);
    assert_eq!(routed.verdict, "pass");
    let mut deciding = routed.deciding.clone();
    deciding.sort();
    assert_eq!(deciding, vec!["score.coherence", "score.helpfulness"]);
    // min of the deciding answers, not max over everything (which would be 1.0).
    assert!(
        (routed.confidence - 0.72).abs() < 1e-9,
        "got {}",
        routed.confidence
    );
}

#[test]
fn low_confidence_pass_is_downgraded_to_review() {
    let rubric = load_rubric("assistant-reply").expect("rubric");
    let answers = with(vec![
        ("score.helpfulness", score(3.0, 0.28)),
        ("score.coherence", score(3.0, 0.30)),
    ]);
    let routed = route_verdict(&rubric, &answers);
    assert_eq!(routed.verdict, "review");
    assert!((routed.confidence - 0.28).abs() < 1e-9);
    assert!(
        routed.reason.contains("below the read_only floor"),
        "{}",
        routed.reason
    );
}

#[test]
fn high_stakes_rubric_has_a_higher_floor() {
    assert!((load_rubric("assistant-reply").unwrap().confidence_floor() - 0.5).abs() < 1e-9);
    let trajectory = load_rubric("agent-trajectory").expect("rubric");
    assert!((trajectory.confidence_floor() - 0.7).abs() < 1e-9);

    let answers = HashMap::from([
        ("screen.judgeable".to_string(), noul(0.95)),
        ("screen.injection".to_string(), noul(0.05)),
        ("locate.unauthorized_write".to_string(), noul(0.05)),
        ("locate.looping".to_string(), noul(0.05)),
        ("profile.task_type".to_string(), choice("read", 0.9)),
        ("score.goal_alignment".to_string(), score(3.5, 0.6)),
        ("score.efficiency".to_string(), score(3.0, 0.6)),
        ("route.escalate".to_string(), noul(0.05)),
    ]);
    let routed = route_verdict(&trajectory, &answers);
    assert_eq!(routed.verdict, "review");
    assert!(
        routed.reason.contains("below the write floor"),
        "{}",
        routed.reason
    );
}

#[test]
fn noul_confidence_is_distance_from_a_coin_flip() {
    let coin = HashMap::from([("a".to_string(), noul(0.5))]);
    let certain = HashMap::from([("a".to_string(), noul(1.0))]);
    let ids = vec!["a".to_string()];
    assert!(decision_confidence(&coin, &ids).abs() < 1e-9);
    assert!((decision_confidence(&certain, &ids) - 1.0).abs() < 1e-9);
    // No deciding answers at all reports 0.0, not infinity.
    assert!(decision_confidence(&certain, &[]).abs() < 1e-9);
}

#[test]
fn missing_answer_escalates_instead_of_falling_through() {
    let rubric = load_rubric("assistant-reply").expect("rubric");
    let mut answers = healthy();
    answers.remove("screen.injection");

    let routed = route_verdict(&rubric, &answers);
    assert_eq!(routed.verdict, "escalate");
    assert!(
        routed.reason.contains("screen.injection"),
        "{}",
        routed.reason
    );
    assert!(
        routed.reason.to_lowercase().contains("missing"),
        "{}",
        routed.reason
    );
}

#[test]
fn every_question_is_read_by_some_rule() {
    for id in ["assistant-reply", "agent-trajectory"] {
        let rubric = load_rubric(id).expect("rubric");
        let read: Vec<String> = rubric
            .routing
            .rules
            .iter()
            .flat_map(|r| r.all.iter().map(|c| c.answer.clone()))
            .collect();
        let unread: Vec<&String> = rubric
            .questions
            .keys()
            .filter(|q| !read.contains(q))
            .collect();
        assert!(unread.is_empty(), "{id} asks but never reads: {unread:?}");
    }
}

#[test]
fn mock_funnel_verdicts() {
    let cases = [
        ("assistant-reply-pass.json", "assistant-reply", "pass"),
        ("assistant-reply-skip.json", "assistant-reply", "skip"),
        (
            "assistant-reply-injection.json",
            "assistant-reply",
            "escalate",
        ),
        (
            "assistant-reply-escalate-flagged.json",
            "assistant-reply",
            "escalate",
        ),
        ("assistant-reply-fail.json", "assistant-reply", "fail"),
        (
            "assistant-reply-low-confidence.json",
            "assistant-reply",
            "review",
        ),
        ("agent-trajectory-pass.json", "agent-trajectory", "pass"),
    ];
    for (fixture, rubric_id, expected) in cases {
        let result = run_judgment(rubric_id, &fixtures().join(fixture), true)
            .unwrap_or_else(|e| panic!("{fixture}: {e}"));
        assert_eq!(result.verdict, expected, "{fixture}");
        assert_eq!(result.model, "jev-1.13.0");
        assert!(result.mock);
        assert!((0.0..=1.0).contains(&result.confidence));
    }
}

#[test]
fn injection_fixture_escalates_at_the_screen_stage() {
    let result = run_judgment(
        "assistant-reply",
        &fixtures().join("assistant-reply-injection.json"),
        true,
    )
    .expect("run");
    assert_eq!(result.verdict, "escalate");
    assert_eq!(result.stage, "screen");
    assert_eq!(
        result.deciding_answers,
        vec!["screen.injection".to_string()]
    );
}

#[test]
fn replay_preserves_answers_and_verdict() {
    let saved = run_judgment(
        "assistant-reply",
        &fixtures().join("assistant-reply-pass.json"),
        true,
    )
    .expect("run");
    let replayed = replay_judgment(&saved.as_saved()).expect("replay");
    assert_eq!(replayed.verdict, saved.verdict);
    assert_eq!(replayed.answers.len(), saved.answers.len());
    assert!((replayed.confidence - saved.confidence).abs() < 1e-9);
    assert_eq!(replayed.deciding_answers, saved.deciding_answers);
}

#[test]
fn recorded_live_answers_route_the_same_as_python() {
    // Real API answers carry `legend`; deserializing and routing must both accept it.
    let text = std::fs::read_to_string(
        fixtures()
            .join("recorded")
            .join("assistant-reply-live.json"),
    )
    .expect("recorded fixture");
    let saved: SavedJudgment = serde_json::from_str(&text).expect("parse recorded result");
    let result = replay_judgment(&saved).expect("replay");

    assert!(matches!(
        result.answers.get("score.helpfulness"),
        Some(Answer::Score {
            legend: Some(_),
            ..
        })
    ));
    // Scores clear the pass thresholds, but the model was near a coin flip on both,
    // so the floor holds it back. This is the case that shipped as pass/0.96.
    assert_eq!(result.verdict, "review");
    assert!(
        (result.confidence - 0.28).abs() < 1e-9,
        "got {}",
        result.confidence
    );
}

#[test]
fn malformed_rubric_is_rejected_at_load() {
    // A rubric Python rejects must not load here either. Parsed from text so this
    // test touches neither the filesystem nor the shared process environment.
    let source = std::fs::read_to_string(
        repo()
            .join("shared")
            .join("rubrics")
            .join("assistant-reply.yaml"),
    )
    .expect("source rubric");

    let cases = [
        (
            "answer: screen.judgeable",
            "answer: nope.missing",
            "not a question",
        ),
        ("op: \"<\"", "op: \"~=\"", "unknown op"),
        (
            "{ answer: screen.judgeable, field: noul",
            "{ answer: screen.judgeable, field: score",
            "not available on a 'noul' answer",
        ),
        ("value: refusal", "value: not_a_label", "is not a label"),
    ];

    for (from, to, expected) in cases {
        assert!(source.contains(from), "fixture text changed: {from}");
        let broken = source.replacen(from, to, 1);
        let err = judge_jev::rubric::parse_rubric(&broken)
            .expect_err(&format!("should reject: {to}"))
            .to_string();
        assert!(err.contains(expected), "expected {expected:?} in {err:?}");
    }
}

#[test]
fn shipped_rubrics_validate() {
    for id in ["assistant-reply", "agent-trajectory"] {
        let rubric = load_rubric(id).expect("rubric");
        assert!(!rubric.routing.rules.is_empty());
        assert!(rubric.confidence_floor() > 0.0);
    }
}

#[test]
fn thresholds_come_from_the_rubric_not_the_code() {
    // The previous implementation matched on substrings of the rule text and used
    // hardcoded thresholds, so editing the shared YAML changed nothing here. Editing
    // a threshold must change the verdict.
    let source = std::fs::read_to_string(
        repo()
            .join("shared")
            .join("rubrics")
            .join("assistant-reply.yaml"),
    )
    .expect("source rubric");

    let mut answers = healthy();
    answers.insert("screen.injection".to_string(), noul(0.75));

    // As shipped, the injection gate is >= 0.7, so 0.75 escalates.
    let shipped = judge_jev::rubric::parse_rubric(&source).expect("shipped rubric");
    assert_eq!(route_verdict(&shipped, &answers).verdict, "escalate");

    // Raise the gate above 0.75 and the same answers must stop escalating.
    let raised = source.replace(
        "{ answer: screen.injection, field: noul, op: \">=\", value: 0.7 }",
        "{ answer: screen.injection, field: noul, op: \">=\", value: 0.95 }",
    );
    assert_ne!(raised, source, "threshold text not found; fixture drifted");
    let relaxed = judge_jev::rubric::parse_rubric(&raised).expect("edited rubric");
    assert_ne!(
        route_verdict(&relaxed, &answers).verdict,
        "escalate",
        "Rust ignored the rubric's threshold"
    );
}

#[test]
fn operator_is_read_from_the_rubric() {
    // Flipping >= to < must invert the outcome, proving the operator is not assumed.
    let source = std::fs::read_to_string(
        repo()
            .join("shared")
            .join("rubrics")
            .join("assistant-reply.yaml"),
    )
    .expect("source rubric");
    let flipped = source.replace(
        "{ answer: locate.harmful, field: noul, op: \">=\", value: 0.8 }",
        "{ answer: locate.harmful, field: noul, op: \"<\", value: 0.8 }",
    );
    assert_ne!(flipped, source, "threshold text not found; fixture drifted");

    let rubric = judge_jev::rubric::parse_rubric(&flipped).expect("edited rubric");
    // healthy() has locate.harmful = 0.02, which now satisfies "< 0.8".
    assert_eq!(route_verdict(&rubric, &healthy()).verdict, "escalate");
}

// --- CLI usage contract --------------------------------------------------------

/// Run the real binary with a fixed repo root, returning (exit code, stderr).
fn cli(args: &[&str]) -> (i32, String) {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_judge-jev"))
        .args(args)
        .env("JUDGE_JEV_ROOT", repo())
        // A malformed command line must never reach the API, so make sure there is
        // no key to reach it with.
        .env_remove("TYPESAFE_API_KEY")
        .output()
        .expect("run judge-jev");
    (
        out.status.code().expect("exit code"),
        String::from_utf8_lossy(&out.stderr).to_string(),
    )
}

#[test]
fn usage_errors_exit_11_and_say_why() {
    // 11, never 2: 2 is EXIT_REVIEW and a hook branching on the code would file an
    // action nobody judged for human review.
    let cases: Vec<(Vec<&str>, &str)> = vec![
        // The bug this test exists for: a typo'd --mock used to be ignored, leaving
        // mock=false, so a call the operator believed was mocked was billed live.
        (
            vec![
                "run",
                "--rubric",
                "assistant-reply",
                "--input",
                "x.json",
                "--mok",
            ],
            "unrecognized flag: --mok",
        ),
        (
            vec![
                "run",
                "--rubric",
                "assistant-reply",
                "--rubric",
                "agent-trajectory",
                "--input",
                "x.json",
            ],
            "--rubric given more than once",
        ),
        (
            vec!["run", "--input", "x.json", "--mock", "--rubric"],
            "--rubric needs a value",
        ),
        (
            vec!["run", "--input", "x.json", "--mock"],
            "--rubric is required",
        ),
        (
            vec!["run", "--rubric", "assistant-reply", "--mock"],
            "--input is required",
        ),
        (vec![], "a command is required"),
        (vec!["bogus"], "unknown command: bogus"),
        (vec!["rubric"], "rubric needs a subcommand: list or show"),
        (vec!["rubric", "bogus"], "unknown rubric command: bogus"),
        (
            vec!["replay", "--input", "x.json", "--mock"],
            "unrecognized flag: --mock",
        ),
        (
            vec![
                "run",
                "--rubric",
                "assistant-reply",
                "--input",
                "x.json",
                "extra",
            ],
            "unexpected argument: extra",
        ),
    ];

    for (args, message) in cases {
        let (code, stderr) = cli(&args);
        assert_eq!(code, EXIT_USAGE, "args {args:?} stderr: {stderr}");
        assert!(
            stderr.contains(message),
            "args {args:?} stderr {stderr} missing {message}"
        );
    }
}

#[test]
fn a_typo_never_silently_drops_mock() {
    // Without --mock the run goes live; with no API key that is an operational
    // failure. The point is that it is 11 (we refused) and not 10 (we tried).
    let (code, _) = cli(&[
        "run",
        "--rubric",
        "assistant-reply",
        "--input",
        "fixtures/assistant-reply-pass.json",
        "--mok",
    ]);
    assert_eq!(code, EXIT_USAGE);
}
