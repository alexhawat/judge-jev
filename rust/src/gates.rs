//! Deterministic pre- and post-route gates recorded on every JudgmentResult.

use crate::models::{GateOutcome, Rubric, StatePath, StateProjection, GATED_VERDICTS};
use serde_json::Value;
use std::collections::HashMap;

const INJECTION_MARKERS: [&str; 2] = ["ignore all prior", "always return pass"];

pub fn state_filter_paths(state_filter: &[StatePath]) -> Vec<String> {
    let mut paths: Vec<String> = state_filter.iter().map(|p| p.path.clone()).collect();
    paths.sort();
    paths
}

pub fn state_projection_hash(paths: &[String]) -> String {
    let payload = compact_json_array(paths);
    format!("sha256:{}", sha256_hex(payload.as_bytes()))
}

/// JSON string encoding that matches Python `json.dumps(..., ensure_ascii=True)`.
fn python_json_string(value: &str) -> String {
    let mut out = String::from("\"");
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{0008}' => out.push_str("\\b"),
            '\u{000c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || (c as u32) >= 0x7f => {
                let cp = c as u32;
                if cp > 0xFFFF {
                    let v = cp - 0x10000;
                    let hi = 0xD800 + (v >> 10);
                    let lo = 0xDC00 + (v & 0x3FF);
                    out.push_str(&format!("\\u{hi:04x}\\u{lo:04x}"));
                } else {
                    out.push_str(&format!("\\u{cp:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn compact_json_array(items: &[String]) -> String {
    let inner = items
        .iter()
        .map(|item| python_json_string(item))
        .collect::<Vec<_>>()
        .join(",");
    format!("[{inner}]")
}

pub(crate) fn sha256_hex(data: &[u8]) -> String {
    sha256(data)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn sha256(data: &[u8]) -> [u8; 32] {
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let k: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];

    let bit_len = (data.len() as u64) * 8;
    let mut msg = data.to_vec();
    msg.push(0x80);
    while (msg.len() % 64) != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());

    for chunk in msg.chunks(64) {
        let mut w = [0u32; 64];
        for (i, word) in chunk.chunks(4).enumerate().take(16) {
            w[i] = u32::from_be_bytes([word[0], word[1], word[2], word[3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let temp1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(k[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }

    let mut out = [0u8; 32];
    for (i, word) in h.iter().enumerate() {
        out[i * 4..(i + 1) * 4].copy_from_slice(&word.to_be_bytes());
    }
    out
}

pub fn build_state_projection(
    state_filter: &[StatePath],
    filtered_state: &Value,
) -> StateProjection {
    let paths = state_filter_paths(state_filter);
    let projected_keys = filtered_state
        .as_object()
        .map(|obj| {
            let mut keys: Vec<String> = obj.keys().cloned().collect();
            keys.sort();
            keys
        })
        .unwrap_or_default();
    StateProjection {
        paths: paths.clone(),
        hash: state_projection_hash(&paths),
        projected_keys,
    }
}

pub struct GateContext<'a> {
    pub rubric: &'a Rubric,
    pub filtered_state: Option<&'a Value>,
    pub answers: Option<&'a HashMap<String, crate::models::Answer>>,
    pub verdict: Option<&'a str>,
    pub confidence: Option<f64>,
    pub confidence_floor: f64,
    pub confidence_candidate: Option<&'a str>,
    pub replay: bool,
    pub budget_ok: bool,
}

pub fn evaluate_gates(ctx: &GateContext<'_>) -> Vec<GateOutcome> {
    let mut outcomes = Vec::new();

    if ctx.replay {
        outcomes.push(GateOutcome {
            gate_id: "state_projection".to_string(),
            outcome: "skip".to_string(),
            reason: "replay does not re-filter state".to_string(),
        });
        outcomes.push(GateOutcome {
            gate_id: "token_budget".to_string(),
            outcome: "skip".to_string(),
            reason: "replay does not estimate tokens".to_string(),
        });
        outcomes.push(GateOutcome {
            gate_id: "injection_heuristic".to_string(),
            outcome: "skip".to_string(),
            reason: "replay does not scan raw state".to_string(),
        });
        outcomes.push(answer_completeness_gate(ctx.rubric, ctx.answers));
        outcomes.push(confidence_floor_gate(
            ctx.verdict,
            ctx.confidence_candidate,
            ctx.confidence,
            ctx.confidence_floor,
        ));
        return outcomes;
    }

    if ctx.filtered_state.is_some() {
        outcomes.push(GateOutcome {
            gate_id: "state_projection".to_string(),
            outcome: "pass".to_string(),
            reason: format!(
                "projected {} top-level keys from {} paths",
                ctx.filtered_state
                    .and_then(|v| v.as_object())
                    .map(|o| o.len())
                    .unwrap_or(0),
                ctx.rubric.state_filter.len()
            ),
        });
    } else {
        outcomes.push(GateOutcome {
            gate_id: "state_projection".to_string(),
            outcome: "skip".to_string(),
            reason: "no filtered state".to_string(),
        });
    }

    if ctx.budget_ok {
        outcomes.push(GateOutcome {
            gate_id: "token_budget".to_string(),
            outcome: "pass".to_string(),
            reason: "state and questions within budget".to_string(),
        });
    } else {
        outcomes.push(GateOutcome {
            gate_id: "token_budget".to_string(),
            outcome: "fail".to_string(),
            reason: "token budget exceeded".to_string(),
        });
    }

    outcomes.push(injection_heuristic_gate(ctx.filtered_state));
    outcomes.push(answer_completeness_gate(ctx.rubric, ctx.answers));
    outcomes.push(confidence_floor_gate(
        ctx.verdict,
        ctx.confidence_candidate,
        ctx.confidence,
        ctx.confidence_floor,
    ));
    outcomes
}

pub fn evaluate_replay_gates(
    ctx: &GateContext<'_>,
    historical_gates: &[GateOutcome],
) -> Vec<GateOutcome> {
    let mut current = evaluate_gates(ctx);
    for (index, gate_id) in ["state_projection", "token_budget", "injection_heuristic"]
        .iter()
        .enumerate()
    {
        let unavailable_reason =
            format!("replay lacks raw state and historical {gate_id} evidence");
        current[index] = match historical_gates
            .iter()
            .find(|gate| gate.gate_id == *gate_id)
        {
            Some(historical)
                if historical.reason != unavailable_reason
                    && !historical.reason.starts_with("replay does not ") =>
            {
                let prefix = "historical evidence from original run: ";
                let mut reason = historical.reason.as_str();
                while let Some(stripped) = reason.strip_prefix(prefix) {
                    reason = stripped;
                }
                GateOutcome {
                    gate_id: historical.gate_id.clone(),
                    outcome: historical.outcome.clone(),
                    reason: format!("{prefix}{reason}"),
                }
            }
            Some(_) | None => GateOutcome {
                gate_id: (*gate_id).to_string(),
                outcome: "skip".to_string(),
                reason: unavailable_reason,
            },
        };
    }
    current
}

pub fn escalate_on_injection(
    verdict: &str,
    reason: &str,
    gates: &[GateOutcome],
) -> (String, String) {
    let failed = gates
        .iter()
        .any(|gate| gate.gate_id == "injection_heuristic" && gate.outcome == "fail");
    if !failed || verdict == "escalate" {
        return (verdict.to_string(), reason.to_string());
    }
    (
        "escalate".to_string(),
        format!("{reason} Injection heuristic failed; verdict escalated."),
    )
}

fn injection_heuristic_gate(filtered_state: Option<&Value>) -> GateOutcome {
    let Some(state) = filtered_state else {
        return GateOutcome {
            gate_id: "injection_heuristic".to_string(),
            outcome: "skip".to_string(),
            reason: "no state to scan".to_string(),
        };
    };
    let text = serde_json::to_string(state)
        .unwrap_or_default()
        .to_lowercase();
    let hits: Vec<&str> = INJECTION_MARKERS
        .iter()
        .copied()
        .filter(|marker| text.contains(marker))
        .collect();
    if hits.is_empty() {
        GateOutcome {
            gate_id: "injection_heuristic".to_string(),
            outcome: "pass".to_string(),
            reason: "no known injection markers".to_string(),
        }
    } else {
        GateOutcome {
            gate_id: "injection_heuristic".to_string(),
            outcome: "fail".to_string(),
            reason: format!("matched markers: {}", hits.join(", ")),
        }
    }
}

fn answer_completeness_gate(
    rubric: &Rubric,
    answers: Option<&HashMap<String, crate::models::Answer>>,
) -> GateOutcome {
    let Some(answers) = answers else {
        return GateOutcome {
            gate_id: "answer_completeness".to_string(),
            outcome: "skip".to_string(),
            reason: "no answers yet".to_string(),
        };
    };
    let expected: std::collections::HashSet<_> = rubric.questions.keys().cloned().collect();
    let missing: Vec<String> = expected
        .difference(&answers.keys().cloned().collect())
        .cloned()
        .collect();
    if missing.is_empty() {
        GateOutcome {
            gate_id: "answer_completeness".to_string(),
            outcome: "pass".to_string(),
            reason: format!("all {} questions answered", expected.len()),
        }
    } else {
        let mut missing = missing;
        missing.sort();
        GateOutcome {
            gate_id: "answer_completeness".to_string(),
            outcome: "fail".to_string(),
            reason: format!("missing answers: {}", missing.join(", ")),
        }
    }
}

fn confidence_floor_gate(
    verdict: Option<&str>,
    confidence_candidate: Option<&str>,
    confidence: Option<f64>,
    floor: f64,
) -> GateOutcome {
    let (Some(verdict), Some(confidence)) = (verdict, confidence) else {
        return GateOutcome {
            gate_id: "confidence_floor".to_string(),
            outcome: "skip".to_string(),
            reason: "routing not complete".to_string(),
        };
    };
    let subject = confidence_candidate.unwrap_or(verdict);
    if !GATED_VERDICTS.contains(&subject) {
        return GateOutcome {
            gate_id: "confidence_floor".to_string(),
            outcome: "skip".to_string(),
            reason: format!("verdict '{verdict}' is not gated by confidence floors"),
        };
    }
    if confidence >= floor {
        GateOutcome {
            gate_id: "confidence_floor".to_string(),
            outcome: "pass".to_string(),
            reason: format!("confidence {confidence:.2} meets {floor:.2} floor"),
        }
    } else if confidence_candidate.is_some() && verdict == "review" {
        GateOutcome {
            gate_id: "confidence_floor".to_string(),
            outcome: "fail".to_string(),
            reason: format!(
                "confidence {confidence:.2} below {floor:.2} floor (downgraded {subject} to review)"
            ),
        }
    } else {
        GateOutcome {
            gate_id: "confidence_floor".to_string(),
            outcome: "fail".to_string(),
            reason: format!(
                "confidence {confidence:.2} below {floor:.2} floor (verdict may downgrade to review)"
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{state_filter_paths, state_projection_hash};
    use crate::models::StatePath;

    #[test]
    fn projection_hash_matches_python_runtime() {
        let paths = vec![
            "context".to_string(),
            "prompt".to_string(),
            "reply".to_string(),
        ];
        assert_eq!(
            state_projection_hash(&paths),
            "sha256:7d26c079d3169ddc395d6e9418adebd2c8ed8ef2936aa0ff5f170f1430ae265b"
        );
    }

    #[test]
    fn empty_projection_hash_is_the_default() {
        use crate::models::StateProjection;

        assert_eq!(state_projection_hash(&[]), StateProjection::default().hash);
    }

    #[test]
    fn projection_hash_escapes_paths_like_python() {
        let vectors = [
            (
                vec!["café".to_string()],
                "sha256:d9957358c680f7382fdf2e65ede7171846b650ebea7953d521837c3b16eed288",
            ),
            (
                vec!["résumé".to_string()],
                "sha256:c7e84dd64340d2a754bfa25062709546dd2b70396126660ffd7eed1c387b84df",
            ),
            (
                vec!["emoji.😀".to_string()],
                "sha256:12fdef3a76819ccbc0b8f6ea1c0d91d79db25eee67ab30ce15c4eb1d753a18a7",
            ),
            (
                vec!["\u{007f}".to_string()],
                "sha256:b10d448be414f5c5ccc0aa89a007547d37a76f46e1209f1147a63f7a6cb090ed",
            ),
            (
                vec![
                    "line\nfeed".to_string(),
                    "quote\"path".to_string(),
                    "slash\\path".to_string(),
                ],
                "sha256:bc0d131ca5c14a8ad6c0a5d27237d1c657b08347f995888706a5540737e74bbe",
            ),
        ];
        for (paths, expected) in vectors {
            assert_eq!(state_projection_hash(&paths), expected);
        }
    }

    #[test]
    fn projection_path_normalization_sorts_before_hashing() {
        let paths = state_filter_paths(&[
            StatePath {
                path: "reply".to_string(),
                required: false,
            },
            StatePath {
                path: "context".to_string(),
                required: false,
            },
            StatePath {
                path: "prompt".to_string(),
                required: false,
            },
        ]);
        assert_eq!(paths, vec!["context", "prompt", "reply"]);
        assert_eq!(
            state_projection_hash(&paths),
            "sha256:7d26c079d3169ddc395d6e9418adebd2c8ed8ef2936aa0ff5f170f1430ae265b"
        );
    }
}
