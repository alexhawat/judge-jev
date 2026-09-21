//! Retry policy, mirroring typesafe-sdk 0.7.0.
//!
//! This runtime issued one `minreq::post(...).send()` and bailed on any non-2xx,
//! while the Python runtime got retries for free from the SDK. A 429 or a 503 was
//! transparent there and a hard failure here — same rubric, same input, outcome
//! decided by whichever runtime `.judge-jev/runtime` happened to name. In a hook
//! that gates work, that turns rate limiting into blocked developers.
//!
//! The policy is the SDK's, not an invented one, so the two fail the same way:
//! `max_retries=2`, `backoff_initial=0.5`, `backoff_max=5.0`, `backoff_jitter=0.25`,
//! retry on 408, 429 and 5xx, honour `Retry-After`, retry connection and timeout
//! errors, and a 30s *total* budget per call covering the initial attempt and every
//! delay. `python/src/judge_jev/retry.py` pins the same numbers explicitly rather
//! than inheriting them, so both are tunable through `JUDGE_JEV_MAX_RETRIES` and
//! neither side's defaults are a guess about the other's.
//!
//! Two deliberate differences, both because this runtime's HTTP client is not the
//! SDK's:
//!
//!   * The SDK's 10s default is *per operation* (connect, read, write); `minreq`'s
//!     `with_timeout` covers the whole request. Using 10s here therefore allows an
//!     attempt at most as much time as Python would, never more. The 30s total
//!     budget is tracked explicitly, because nothing in `minreq` tracks it.
//!   * `Retry-After` is honoured in its numeric forms (`retry-after-ms`, then
//!     `retry-after` in seconds). The SDK also accepts an HTTP-date, which needs a
//!     date parser this crate does not carry; a date-valued header falls back to
//!     the ordinary backoff rather than being ignored outright.
//!
//! It lives in its own module rather than inline in the client because #16 wants
//! retry per backend, and a module the client calls is the same work either way.

use anyhow::{anyhow, Result};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tracing::warn;

pub const MAX_RETRIES_ENV: &str = "JUDGE_JEV_MAX_RETRIES";

// typesafe-sdk 0.7.0 defaults.
pub const DEFAULT_MAX_RETRIES: u32 = 2;
const BACKOFF_INITIAL: f64 = 0.5;
const BACKOFF_MAX: f64 = 5.0;
const BACKOFF_JITTER: f64 = 0.25;
/// Total retry budget per call, including the initial attempt and the delays.
const TOTAL_TIMEOUT: f64 = 30.0;
/// The SDK's DEFAULT_TIMEOUT. Per operation there, whole-request here.
pub const PER_OPERATION_TIMEOUT_SECS: u64 = 10;

/// What one attempt produced.
pub enum Outcome<T> {
    Done(T),
    /// Worth another attempt: a 408, a 429, a 5xx, or a transport failure.
    Retry {
        error: anyhow::Error,
        /// Seconds the server asked us to wait, when it said.
        after: Option<f64>,
    },
    /// Not worth another attempt. A malformed request will not fix itself, and
    /// retrying it burns the budget before a real outage can use it.
    Fatal(anyhow::Error),
}

/// Whether a status is worth another attempt. 408 and 429 are the only 4xx that
/// are; the rest are the caller's fault and will fail again identically.
pub fn is_retryable_status(status: i32) -> bool {
    status == 408 || status == 429 || (500..600).contains(&status)
}

/// Retries after the initial attempt, from the environment or the default.
pub fn max_retries() -> Result<u32> {
    let raw = match std::env::var(MAX_RETRIES_ENV) {
        Ok(value) => value,
        Err(_) => return Ok(DEFAULT_MAX_RETRIES),
    };
    if raw.trim().is_empty() {
        return Ok(DEFAULT_MAX_RETRIES);
    }
    raw.parse::<u32>()
        .map_err(|_| anyhow!("{MAX_RETRIES_ENV} must be a non-negative integer, got '{raw}'"))
}

#[derive(Debug, Clone)]
pub struct RetryPolicy {
    pub max_retries: u32,
    pub backoff_initial: f64,
    pub backoff_max: f64,
    pub backoff_jitter: f64,
    pub respect_retry_after: bool,
    /// None disables the total budget.
    pub total_timeout: Option<f64>,
    pub per_operation_timeout_secs: u64,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_retries: DEFAULT_MAX_RETRIES,
            backoff_initial: BACKOFF_INITIAL,
            backoff_max: BACKOFF_MAX,
            backoff_jitter: BACKOFF_JITTER,
            respect_retry_after: true,
            total_timeout: Some(TOTAL_TIMEOUT),
            per_operation_timeout_secs: PER_OPERATION_TIMEOUT_SECS,
        }
    }
}

impl RetryPolicy {
    pub fn from_env() -> Result<Self> {
        Ok(Self {
            max_retries: max_retries()?,
            ..Self::default()
        })
    }

    /// The SDK's backoff: double from `backoff_initial` up to `backoff_max`, then
    /// subtract a random fraction of it, rounded to milliseconds and never longer
    /// than the undithered delay.
    pub fn backoff(&self, attempt: u32, jitter_fraction: f64) -> f64 {
        if self.backoff_initial == 0.0 || self.backoff_max == 0.0 {
            return 0.0;
        }
        let exponent = f64::from(attempt.saturating_sub(1));
        let exponential = if exponent >= self.backoff_max.log2() - self.backoff_initial.log2() {
            self.backoff_max
        } else {
            self.backoff_initial * exponent.exp2()
        };
        let dithered = exponential * (1.0 - jitter_fraction * self.backoff_jitter);
        exponential.min((dithered * 1000.0).round() / 1000.0)
    }

    /// Run `attempt` until it succeeds, gives up, or the budget runs out.
    ///
    /// `attempt` is handed the per-attempt timeout in seconds and the 1-based
    /// attempt number.
    pub fn run<T>(&self, mut attempt: impl FnMut(u64, u32) -> Outcome<T>) -> Result<T> {
        let started = Instant::now();
        let mut last: Option<anyhow::Error> = None;

        for number in 1..=(self.max_retries + 1) {
            match attempt(self.per_operation_timeout_secs, number) {
                Outcome::Done(value) => return Ok(value),
                Outcome::Fatal(error) => return Err(error),
                Outcome::Retry { error, after } => {
                    last = Some(error);
                    if number > self.max_retries {
                        break;
                    }
                    let delay = match (self.respect_retry_after, after) {
                        (true, Some(seconds)) => seconds,
                        _ => self.backoff(number, jitter_fraction()),
                    };
                    // The SDK stops before a delay that would reach the budget,
                    // re-raising the last error rather than sleeping past it.
                    if let Some(budget) = self.total_timeout {
                        if started.elapsed().as_secs_f64() + delay >= budget {
                            break;
                        }
                    }
                    warn!(
                        "attempt {} failed ({}); retrying in {:.3}s",
                        number,
                        last.as_ref().expect("just set"),
                        delay
                    );
                    std::thread::sleep(Duration::from_secs_f64(delay));
                }
            }
        }
        Err(last.unwrap_or_else(|| anyhow!("retry policy ran no attempts")))
    }
}

/// A random fraction in [0, 1) for backoff dither.
///
/// SplitMix64 over the clock rather than a `rand` dependency: this decides how long
/// to sleep, so the bar is "not synchronized across processes", not cryptographic.
fn jitter_fraction() -> f64 {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let mut z = nanos.wrapping_add(0x9E37_79B9_7F4A_7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^= z >> 31;
    // 53 bits, the mantissa, so the result is uniform in [0, 1).
    ((z >> 11) as f64) / ((1u64 << 53) as f64)
}

/// Seconds to wait, from whichever `Retry-After` form the server used.
///
/// `retry-after-ms` is checked first, as the SDK does, because it is the more
/// precise of the two.
pub fn retry_after_seconds(header_ms: Option<&str>, header_seconds: Option<&str>) -> Option<f64> {
    if let Some(raw) = header_ms {
        if let Ok(ms) = raw.trim().parse::<f64>() {
            if ms.is_finite() && ms >= 0.0 {
                return Some(ms / 1000.0);
            }
        }
    }
    if let Some(raw) = header_seconds {
        if let Ok(seconds) = raw.trim().parse::<f64>() {
            if seconds.is_finite() && seconds >= 0.0 {
                return Some(seconds);
            }
        }
    }
    // An HTTP-date lands here and falls back to the ordinary backoff.
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_408_429_and_5xx_are_retried() {
        for status in [408, 429, 500, 502, 503, 504, 599] {
            assert!(is_retryable_status(status), "{status} should retry");
        }
        // A malformed request will not fix itself.
        for status in [400, 401, 403, 404, 409, 422, 200, 201, 301] {
            assert!(!is_retryable_status(status), "{status} should not retry");
        }
    }

    #[test]
    fn backoff_doubles_and_caps() {
        let policy = RetryPolicy::default();
        // Undithered, so the shape is visible: 0.5, 1, 2, 4, then the 5.0 cap.
        assert_eq!(policy.backoff(1, 0.0), 0.5);
        assert_eq!(policy.backoff(2, 0.0), 1.0);
        assert_eq!(policy.backoff(3, 0.0), 2.0);
        assert_eq!(policy.backoff(4, 0.0), 4.0);
        assert_eq!(policy.backoff(5, 0.0), 5.0);
        assert_eq!(policy.backoff(9, 0.0), 5.0);
    }

    #[test]
    fn jitter_only_ever_shortens_a_delay() {
        let policy = RetryPolicy::default();
        // Full jitter takes a quarter off; it must never extend the wait.
        assert_eq!(policy.backoff(1, 1.0), 0.375);
        for fraction in [0.0, 0.25, 0.5, 0.75, 1.0] {
            let delay = policy.backoff(3, fraction);
            assert!((1.5..=2.0).contains(&delay), "{fraction} gave {delay}");
        }
    }

    #[test]
    fn a_fatal_outcome_is_not_retried() {
        let policy = RetryPolicy::default();
        let mut attempts = 0;
        let result: Result<()> = policy.run(|_, _| {
            attempts += 1;
            Outcome::Fatal(anyhow!("400 Bad Request"))
        });
        assert!(result.is_err());
        assert_eq!(attempts, 1);
    }

    #[test]
    fn a_retryable_outcome_is_attempted_max_retries_plus_one_times() {
        let policy = RetryPolicy {
            backoff_initial: 0.0,
            ..RetryPolicy::default()
        };
        let mut attempts = 0;
        let result: Result<()> = policy.run(|_, _| {
            attempts += 1;
            Outcome::Retry {
                error: anyhow!("503"),
                after: None,
            }
        });
        assert!(result.is_err());
        assert_eq!(attempts, 3, "one initial attempt plus max_retries");
        // The last error is what the caller sees, not a wrapper hiding it.
        assert_eq!(result.unwrap_err().to_string(), "503");
    }

    #[test]
    fn success_after_failures_returns_the_value() {
        let policy = RetryPolicy {
            backoff_initial: 0.0,
            ..RetryPolicy::default()
        };
        let mut attempts = 0;
        let value = policy
            .run(|_, _| {
                attempts += 1;
                if attempts < 3 {
                    Outcome::Retry {
                        error: anyhow!("503"),
                        after: None,
                    }
                } else {
                    Outcome::Done("answers")
                }
            })
            .unwrap();
        assert_eq!(value, "answers");
        assert_eq!(attempts, 3);
    }

    #[test]
    fn retries_can_be_disabled() {
        let policy = RetryPolicy {
            max_retries: 0,
            ..RetryPolicy::default()
        };
        let mut attempts = 0;
        let result: Result<()> = policy.run(|_, _| {
            attempts += 1;
            Outcome::Retry {
                error: anyhow!("503"),
                after: None,
            }
        });
        assert!(result.is_err());
        assert_eq!(attempts, 1);
    }

    #[test]
    fn the_total_budget_stops_before_a_delay_that_would_exceed_it() {
        // A budget smaller than the first backoff: one attempt, then give up
        // rather than sleep past it.
        let policy = RetryPolicy {
            total_timeout: Some(0.1),
            ..RetryPolicy::default()
        };
        let mut attempts = 0;
        let started = Instant::now();
        let result: Result<()> = policy.run(|_, _| {
            attempts += 1;
            Outcome::Retry {
                error: anyhow!("503"),
                after: None,
            }
        });
        assert!(result.is_err());
        assert_eq!(attempts, 1);
        assert!(
            started.elapsed() < Duration::from_secs(1),
            "it slept anyway"
        );
    }

    #[test]
    fn retry_after_prefers_milliseconds_and_ignores_a_date() {
        assert_eq!(retry_after_seconds(Some("1500"), Some("9")), Some(1.5));
        assert_eq!(retry_after_seconds(None, Some("2")), Some(2.0));
        assert_eq!(retry_after_seconds(None, Some("  3 ")), Some(3.0));
        assert_eq!(retry_after_seconds(None, None), None);
        assert_eq!(retry_after_seconds(None, Some("-1")), None);
        // An HTTP-date needs a parser this crate does not carry; fall back to
        // backoff rather than pretending to understand it.
        assert_eq!(
            retry_after_seconds(None, Some("Wed, 21 Oct 2015 07:28:00 GMT")),
            None
        );
    }

    #[test]
    fn a_server_asked_delay_is_honoured_over_backoff() {
        let policy = RetryPolicy::default();
        let mut delays = Vec::new();
        let started = Instant::now();
        let result: Result<()> = policy.run(|_, number| {
            delays.push(number);
            Outcome::Retry {
                error: anyhow!("429"),
                after: Some(0.0),
            }
        });
        assert!(result.is_err());
        // Three attempts with a zero Retry-After: the backoff was not used.
        assert_eq!(delays, vec![1, 2, 3]);
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn jitter_fraction_stays_in_range() {
        for _ in 0..100 {
            let value = jitter_fraction();
            assert!((0.0..1.0).contains(&value), "{value} out of range");
        }
    }
}
