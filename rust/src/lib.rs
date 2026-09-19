pub mod funnel;
pub mod models;
pub mod paths;
pub mod routing;
pub mod rubric;
pub mod setup;
pub mod typesafe;

pub const EXIT_OK: i32 = 0;
pub const EXIT_FAIL: i32 = 1;
pub const EXIT_REVIEW: i32 = 2;
pub const EXIT_ESCALATE: i32 = 3;
pub const EXIT_SKIP: i32 = 4;

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
