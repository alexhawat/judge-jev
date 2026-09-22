pub mod answers;
pub mod budget;
pub mod canonical;
pub mod funnel;
pub mod gates;
pub mod models;
pub mod paths;
pub mod retry;
pub mod routing;
pub mod rubric;
pub mod setup;
pub mod state_filter;
pub mod typesafe;

// Verdict codes. These are a contract: hooks and CI branch on them.
pub const EXIT_OK: i32 = 0;
pub const EXIT_FAIL: i32 = 1;
pub const EXIT_REVIEW: i32 = 2;
pub const EXIT_ESCALATE: i32 = 3;
pub const EXIT_SKIP: i32 = 4;

// Operational failure: the judgment did not happen. Kept well clear of the verdict
// codes so a crash can never be mistaken for a verdict of 'fail'.
pub const EXIT_ERROR: i32 = 10;
pub const EXIT_USAGE: i32 = 11;

pub fn exit_for_verdict(verdict: &str) -> i32 {
    match verdict {
        "pass" => EXIT_OK,
        "fail" => EXIT_FAIL,
        "review" => EXIT_REVIEW,
        "escalate" => EXIT_ESCALATE,
        "skip" => EXIT_SKIP,
        _ => EXIT_REVIEW,
    }
}
