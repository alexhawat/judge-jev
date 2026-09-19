use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Usage {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub input_tokens: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output_tokens: Option<u64>,
}

impl Default for Usage {
    fn default() -> Self {
        Self {
            input_tokens: None,
            output_tokens: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JudgmentResult {
    pub rubric_id: String,
    pub verdict: String,
    pub confidence: f64,
    pub stage: String,
    pub model: String,
    pub usage: Usage,
    pub answers: HashMap<String, Answer>,
    pub routing_reason: String,
    pub mock: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
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
        probabilities: HashMap<String, f64>,
    },
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
    pub state_filter: Vec<String>,
    pub questions: HashMap<String, QuestionSpec>,
    pub routing: RoutingSpec,
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
    pub when: String,
    #[serde(default)]
    pub reason: String,
}
