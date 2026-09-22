use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Funnel stages, earliest first. A rule's stage is the earliest stage among the
/// questions it reads.
pub const STAGE_ORDER: [&str; 5] = ["screen", "profile", "locate", "score", "route"];

pub const VERDICTS: [&str; 5] = ["pass", "fail", "review", "escalate", "skip"];

/// Verdicts that assert an automatic conclusion and are therefore gated by the
/// rubric's confidence floor. review/escalate/skip already defer to a human.
pub const GATED_VERDICTS: [&str; 2] = ["pass", "fail"];

pub fn stage_index(stage: &str) -> Option<usize> {
    STAGE_ORDER.iter().position(|s| *s == stage)
}

/// Token counts, `null` when the API reported none.
///
/// The fields are emitted even when absent. They used to be skipped, which made a
/// replayed result differ from Python's on shape alone — `{}` against
/// `{"input_tokens": null, "output_tokens": null}` — for any saved judgment that
/// carried no usage. `scripts/check-parity.sh` could not see it, because it only
/// compared successful mock runs and the mock always fills both.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Usage {
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct GateOutcome {
    pub gate_id: String,
    pub outcome: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StateProjection {
    pub paths: Vec<String>,
    pub hash: String,
    pub projected_keys: Vec<String>,
}

impl Default for StateProjection {
    fn default() -> Self {
        StateProjection {
            paths: Vec::new(),
            hash: "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
                .to_string(),
            projected_keys: Vec::new(),
        }
    }
}

/// Which build produced a result.
///
/// Recorded because a verdict is only auditable against the code that reached it:
/// `scripts/check-parity.sh` exists precisely because the two runtimes can drift,
/// and a saved result that cannot say which one ran it cannot be checked against
/// the other.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Runtime {
    pub name: String,
    pub version: String,
}

pub const RUNTIME_NAME: &str = "rust";
pub const RUNTIME_VERSION: &str = env!("CARGO_PKG_VERSION");

impl Default for Runtime {
    fn default() -> Self {
        Runtime {
            name: RUNTIME_NAME.to_string(),
            version: RUNTIME_VERSION.to_string(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JudgmentResult {
    pub rubric_id: String,
    /// The rubric version that produced this verdict. Without it `replay` re-routes
    /// saved answers against whatever the rubric says today and reports the new
    /// verdict as though it were the original judgment.
    pub rubric_version: String,
    pub verdict: String,
    pub confidence: f64,
    pub stage: String,
    pub model: String,
    pub usage: Usage,
    pub answers: HashMap<String, Answer>,
    pub routing_reason: String,
    pub mock: bool,
    /// Answer IDs the matched rule read. These, and only these, determine `confidence`.
    #[serde(default)]
    pub deciding_answers: Vec<String>,
    /// The floor `confidence` was checked against, from confidence_floors[stakes].
    #[serde(default)]
    pub confidence_floor: f64,
    /// Emitted even when absent, for the same reason as `Usage`'s fields.
    pub request_id: Option<String>,
    #[serde(default)]
    pub runtime: Runtime,
    #[serde(default)]
    pub state_projection: StateProjection,
    #[serde(default)]
    pub deterministic_gates: Vec<GateOutcome>,
}

/// Replay input: a previously saved judgment. Only `rubric_id` and `answers` are
/// needed to re-route, so everything else is optional — matching what the Python
/// runtime accepts, so a payload one runtime replays the other replays too.
#[derive(Debug, Clone, Deserialize)]
pub struct SavedJudgment {
    pub rubric_id: String,
    /// Absent on any result saved before provenance existed, which `replay` treats
    /// as drift: a result that cannot say what judged it cannot be re-derived.
    #[serde(default)]
    pub rubric_version: Option<String>,
    pub answers: HashMap<String, Answer>,
    #[serde(default)]
    pub model: Option<String>,
    #[serde(default)]
    pub usage: Usage,
    #[serde(default)]
    pub mock: bool,
    #[serde(default)]
    pub request_id: Option<String>,
    #[serde(default)]
    pub state_projection: StateProjection,
    #[serde(default)]
    pub deterministic_gates: Vec<GateOutcome>,
}

impl JudgmentResult {
    /// View this result as replay input.
    pub fn as_saved(&self) -> SavedJudgment {
        SavedJudgment {
            rubric_id: self.rubric_id.clone(),
            rubric_version: Some(self.rubric_version.clone()),
            answers: self.answers.clone(),
            model: Some(self.model.clone()),
            usage: self.usage.clone(),
            mock: self.mock,
            request_id: self.request_id.clone(),
            state_projection: self.state_projection.clone(),
            deterministic_gates: self.deterministic_gates.clone(),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type")]
pub enum Answer {
    #[serde(rename = "noul")]
    Noul { noul: f64 },
    #[serde(rename = "choice")]
    Choice {
        choice: String,
        confidence: f64,
        probabilities: HashMap<String, f64>,
    },
    #[serde(rename = "score")]
    Score {
        score: f64,
        confidence: f64,
        /// Score levels keyed by level number. The API always returns this; it is
        /// optional here only so older saved results still replay.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        legend: Option<HashMap<String, serde_json::Value>>,
        probabilities: HashMap<String, f64>,
    },
}

impl<'de> Deserialize<'de> for Answer {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        use serde::de::Error;
        let value = serde_json::Value::deserialize(deserializer)?;
        let object = value
            .as_object()
            .ok_or_else(|| D::Error::custom("answer must be an object"))?;
        let kind = object
            .get("type")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| D::Error::custom("answer.type must be a string"))?;
        let number = |field: &str| {
            object
                .get(field)
                .and_then(serde_json::Value::as_f64)
                .ok_or_else(|| D::Error::custom(format!("answer.{field} must be a number")))
        };
        let probabilities = || -> Result<HashMap<String, f64>, D::Error> {
            object
                .get("probabilities")
                .and_then(serde_json::Value::as_object)
                .ok_or_else(|| D::Error::custom("answer.probabilities must be an object"))?
                .iter()
                .map(|(key, value)| {
                    value
                        .as_f64()
                        .map(|number| (key.clone(), number))
                        .ok_or_else(|| D::Error::custom("answer probability must be a number"))
                })
                .collect()
        };
        match kind {
            "noul" => Ok(Answer::Noul {
                noul: number("noul")?,
            }),
            "choice" => Ok(Answer::Choice {
                choice: object
                    .get("choice")
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| D::Error::custom("answer.choice must be a string"))?
                    .to_string(),
                confidence: number("confidence")?,
                probabilities: probabilities()?,
            }),
            "score" => {
                let legend = object.get("legend").map(|raw| {
                    raw.as_object()
                        .ok_or_else(|| D::Error::custom("answer.legend must be an object"))
                        .map(|mapping| {
                            mapping
                                .iter()
                                .map(|(key, value)| (key.clone(), value.clone()))
                                .collect()
                        })
                }).transpose()?;
                Ok(Answer::Score {
                    score: number("score")?,
                    confidence: number("confidence")?,
                    legend,
                    probabilities: probabilities()?,
                })
            }
            other => Err(D::Error::custom(format!("unknown answer type '{other}'"))),
        }
    }
}

/// A single field read off an answer by a routing condition.
#[derive(Debug, Clone, PartialEq)]
pub enum FieldValue {
    Num(f64),
    Text(String),
}

impl Answer {
    /// The answer's type discriminator, matching the wire form.
    pub fn kind(&self) -> &'static str {
        match self {
            Answer::Noul { .. } => "noul",
            Answer::Choice { .. } => "choice",
            Answer::Score { .. } => "score",
        }
    }

    /// Read one field, or None when this answer type does not expose it.
    pub fn field(&self, name: &str) -> Option<FieldValue> {
        match (self, name) {
            (Answer::Noul { noul }, "noul") => Some(FieldValue::Num(*noul)),
            (Answer::Choice { choice, .. }, "choice") => Some(FieldValue::Text(choice.clone())),
            (Answer::Choice { confidence, .. }, "confidence") => Some(FieldValue::Num(*confidence)),
            (Answer::Score { score, .. }, "score") => Some(FieldValue::Num(*score)),
            (Answer::Score { confidence, .. }, "confidence") => Some(FieldValue::Num(*confidence)),
            _ => None,
        }
    }

    /// How certain this answer is, on 0-1.
    ///
    /// Choice and Score carry `confidence` directly. Noul has none, so the distance
    /// from 0.5 stands in for it: 0.5 is a coin flip, 0 and 1 are certain.
    pub fn confidence(&self) -> f64 {
        match self {
            Answer::Noul { noul } => (noul - 0.5).abs() * 2.0,
            Answer::Choice { confidence, .. } => *confidence,
            Answer::Score { confidence, .. } => *confidence,
        }
    }
}

/// Which fields each answer type exposes to a condition.
pub fn fields_for_type(qtype: &str) -> &'static [&'static str] {
    match qtype {
        "noul" => &["noul"],
        "choice" => &["choice", "confidence"],
        "score" => &["score", "confidence"],
        _ => &[],
    }
}

/// Fields compared as text rather than numbers.
pub fn is_text_field(field: &str) -> bool {
    field == "choice"
}

pub const OPS: [&str; 6] = ["<", "<=", ">", ">=", "==", "!="];

/// One `state_filter` entry: a path, and whether its absence is fatal.
///
/// Written either as a bare path or as `{ path: …, required: true }`. The mapping
/// form was chosen over a sigil (`ticket.subject!`) because it reads as data rather
/// than punctuation and matches the inline-mapping style the routing rules already
/// use.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StatePath {
    pub path: String,
    pub required: bool,
}

impl<'de> Deserialize<'de> for StatePath {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Full {
            path: String,
            #[serde(default)]
            required: bool,
        }

        #[derive(Deserialize)]
        #[serde(untagged)]
        enum Spec {
            Bare(String),
            Full(Full),
        }

        Ok(match Spec::deserialize(deserializer)? {
            Spec::Bare(path) => StatePath {
                path,
                required: false,
            },
            Spec::Full(full) => StatePath {
                path: full.path,
                required: full.required,
            },
        })
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Rubric {
    pub id: String,
    pub version: String,
    #[serde(default)]
    pub description: String,
    pub model: String,
    pub stakes: String,
    #[serde(default)]
    pub confidence_floors: HashMap<String, f64>,
    #[serde(default)]
    pub state_filter: Vec<StatePath>,
    pub questions: HashMap<String, QuestionSpec>,
    pub routing: RoutingSpec,
}

impl Rubric {
    /// The floor automatic verdicts must clear, from this rubric's stakes.
    pub fn confidence_floor(&self) -> f64 {
        self.confidence_floors
            .get(&self.stakes)
            .copied()
            .unwrap_or(0.0)
    }

    pub fn stage_of(&self, answer_id: &str) -> Option<&str> {
        self.questions.get(answer_id).map(|q| q.stage.as_str())
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct QuestionSpec {
    #[serde(rename = "type")]
    pub qtype: String,
    pub stage: String,
    #[serde(default)]
    pub instructions: Option<String>,
    #[serde(default)]
    pub criteria: serde_yaml::Value,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RoutingSpec {
    pub rules: Vec<RoutingRule>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RoutingRule {
    pub verdict: String,
    #[serde(default)]
    pub reason: String,
    /// Conditions ANDed together, evaluated in order and short-circuiting on the
    /// first false. Empty for the catch-all rule.
    #[serde(default)]
    pub all: Vec<Condition>,
    #[serde(default)]
    pub default: bool,
}

impl RoutingRule {
    /// Answer IDs this rule reads, in first-seen order and deduplicated.
    pub fn answer_ids(&self) -> Vec<String> {
        let mut seen: Vec<String> = Vec::new();
        for condition in &self.all {
            if !seen.contains(&condition.answer) {
                seen.push(condition.answer.clone());
            }
        }
        seen
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Condition {
    pub answer: String,
    pub field: String,
    pub op: String,
    pub value: ConditionValue,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum ConditionValue {
    Number(f64),
    Text(String),
}

impl ConditionValue {
    pub fn as_number(&self) -> Option<f64> {
        match self {
            ConditionValue::Number(n) => Some(*n),
            ConditionValue::Text(t) => t.parse::<f64>().ok(),
        }
    }

    pub fn as_text(&self) -> String {
        match self {
            // `{:?}` keeps the trailing .0 on whole numbers, so `rubric show` renders
            // thresholds the same way the Python runtime does.
            ConditionValue::Number(n) => format!("{n:?}"),
            ConditionValue::Text(t) => t.clone(),
        }
    }
}
