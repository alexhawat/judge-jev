//! Built-in assets used when a standalone binary has no adjacent checkout.

pub const ASSISTANT_REPLY: &str = include_str!("../../shared/rubrics/assistant-reply.yaml");
pub const AGENT_TRAJECTORY: &str = include_str!("../../shared/rubrics/agent-trajectory.yaml");

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
