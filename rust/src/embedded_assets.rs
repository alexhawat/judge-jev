//! Built-in assets used when a standalone binary has no adjacent checkout.

pub const ASSISTANT_REPLY: &str = include_str!("../assets/rubrics/assistant-reply.yaml");
pub const AGENT_TRAJECTORY: &str = include_str!("../assets/rubrics/agent-trajectory.yaml");
pub const JUDGMENT_RESULT_SCHEMA: &str =
    include_str!("../assets/schemas/judgment-result.schema.json");
pub const RUBRIC_SCHEMA: &str = include_str!("../assets/schemas/rubric.schema.json");

pub fn rubric(id: &str) -> Option<&'static str> {
    match id {
        "assistant-reply" => Some(ASSISTANT_REPLY),
        "agent-trajectory" => Some(AGENT_TRAJECTORY),
        _ => None,
    }
}

pub fn rubric_ids() -> Vec<String> {
    vec!["agent-trajectory".into(), "assistant-reply".into()]
}

pub fn schema(name: &str) -> Option<&'static str> {
    match name {
        "judgment-result.schema.json" => Some(JUDGMENT_RESULT_SCHEMA),
        "rubric.schema.json" => Some(RUBRIC_SCHEMA),
        _ => None,
    }
}
