//! The one serialization both runtimes build a request from.
//!
//! `shared/rubrics/` is only shared if the two runtimes send the same thing. They
//! did not: `serde_json::Map` is a `BTreeMap` in this build — there is no
//! `preserve_order` feature on the dependency, and `indexmap` arrives only through
//! `serde_yaml` — so this runtime sorted the filtered state while the Python
//! runtime kept it in `state_filter` order. The same fixture went over the wire as
//! `{"context":…,"prompt":…,"reply":…}` here and `{"prompt":…,"reply":…,"context":…}`
//! there, and nested objects diverged the same way: `{input,output,tool}` against
//! `{tool,input,output}`. Nothing caught it, because the parity harness compared
//! parsed `JudgmentResult`s, and two different requests that reach the same verdict
//! look identical to a JSON parser.
//!
//! The canonical form, defined once in `python/src/judge_jev/canonical.py` and
//! implemented identically here:
//!
//!   * **Object keys sorted**, recursively, by Unicode code point. Sorting is the
//!     only ordering rule both runtimes can implement, since this one cannot
//!     recover the order a document was written in. It applies at every depth,
//!     which is where the divergence was.
//!   * **No insignificant whitespace**: `,` and `:` separate, nothing pads.
//!   * **Non-ASCII characters emitted literally.** Escaping them would cost bytes,
//!     and bytes are tokens.
//!   * **Numbers**: integers as digits; everything else with the shortest digit
//!     string that round-trips, written plainly when the decimal point lands in
//!     `-4 < decpt <= 16` and as `d[.ddd]e±XX` otherwise. That is what `repr()`
//!     gives a Python float, so the other runtime gets this from `json.dumps` for
//!     free. `serde_json` does not: it writes `1e-5` as `0.00001` and `1e-7` as
//!     `1e-7` where Python writes `1e-05` and `1e-07`, which is why the formatting
//!     below is done by hand rather than delegated.
//!   * **NaN and Infinity are rejected.** JSON cannot spell them, and the Python
//!     parser accepts them where this runtime's does not.
//!
//! The canonical text is also what goes over the wire: `state` is sent as the text
//! itself rather than as an object for each runtime's HTTP client to re-encode.
//! The API documents `state` as text, a JSON object, or an array, and sending the
//! text is the only way byte-identity survives two encoders neither runtime owns.
//!
//! `serde_json` is built with `arbitrary_precision`, so integer literals outside
//! i64/u64 remain exact decimal text. This matches Python's unbounded JSON ints.

use anyhow::{bail, Result};
use serde_json::{Map, Value};

/// Serialize `value` in the canonical form.
///
/// Fails on a non-finite number, which JSON cannot represent.
pub fn canonical_json(value: &Value) -> Result<String> {
    let mut out = String::new();
    write_value(value, &mut out)?;
    Ok(out)
}

/// Byte length of the canonical form.
///
/// Bytes, not characters: Python counts characters with `len()` and the two
/// runtimes have to agree on the number.
pub fn canonical_size(value: &Value) -> Result<usize> {
    Ok(canonical_json(value)?.len())
}

/// Filtered state, alongside the exact bytes it will be sent as.
///
/// Both halves are needed downstream: the live client sends `text`, while the mock
/// engine reads `value` to decide what a question would be answered. Keeping them
/// together means they cannot drift out of step.
#[derive(Debug, Clone)]
pub struct CanonicalState {
    pub value: Value,
    pub text: String,
}

impl CanonicalState {
    pub fn of(value: Value) -> Result<Self> {
        let text = canonical_json(&value)?;
        Ok(Self { value, text })
    }

    pub fn size(&self) -> usize {
        self.text.len()
    }
}

fn write_value(value: &Value, out: &mut String) -> Result<()> {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(n) => out.push_str(&format_number(n)?),
        Value::String(s) => write_string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write_value(item, out)?;
            }
            out.push(']');
        }
        Value::Object(map) => write_object(map, out)?,
    }
    Ok(())
}

fn write_object(map: &Map<String, Value>, out: &mut String) -> Result<()> {
    // `Map` is already a `BTreeMap` here, but sorting explicitly keeps the
    // canonical form a property of this module rather than of a dependency's
    // feature flags — which is exactly how the two runtimes drifted apart.
    // `str`'s ordering is by UTF-8 bytes, which for UTF-8 is code point order, so
    // this matches Python's `sort_keys=True`.
    let mut keys: Vec<&String> = map.keys().collect();
    keys.sort();
    out.push('{');
    for (i, key) in keys.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        write_string(key, out);
        out.push(':');
        write_value(&map[*key], out)?;
    }
    out.push('}');
    Ok(())
}

/// Escape exactly as Python's `json.dumps(..., ensure_ascii=False)` does: the two
/// quote/backslash escapes, the five short control escapes, `\u00xx` in lowercase
/// hex for the remaining C0 controls, and every other character literally.
fn write_string(s: &str, out: &mut String) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

fn format_number(n: &serde_json::Number) -> Result<String> {
    if let Some(i) = n.as_i64() {
        return Ok(i.to_string());
    }
    if let Some(u) = n.as_u64() {
        return Ok(u.to_string());
    }
    let exact = n.to_string();
    if !exact.contains(['.', 'e', 'E']) {
        return Ok(exact);
    }
    let Some(f) = n.as_f64() else {
        bail!("number {n} cannot be represented canonically");
    };
    format_f64(f)
}

/// Format a float the way Python's `repr` does.
///
/// Rust's `{:e}` gives the same shortest round-tripping digits Python computes; all
/// that differs is where the two choose to put the decimal point and how they pad
/// the exponent. So the digits are taken from `{:e}` and re-presented under
/// Python's rule: plain notation while the decimal point sits in `-4 < decpt <= 16`,
/// exponential otherwise, with the exponent always signed and at least two digits.
fn format_f64(f: f64) -> Result<String> {
    if !f.is_finite() {
        bail!("{f} cannot be represented in JSON");
    }
    let sign = if f.is_sign_negative() { "-" } else { "" };
    let magnitude = f.abs();
    if magnitude == 0.0 {
        // Python renders both zeroes with a fractional part, sign and all.
        return Ok(format!("{sign}0.0"));
    }

    // e.g. "2.5e0", "1e30", "3.0000000000000004e-1".
    let exp_form = format!("{magnitude:e}");
    let (mantissa, exponent) = exp_form
        .split_once('e')
        .expect("Rust's LowerExp always emits an exponent");
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let exponent: i32 = exponent.parse()?;
    // Position of the decimal point relative to the start of `digits`.
    let decpt = exponent + 1;

    if -4 < decpt && decpt <= 16 {
        let body = if decpt <= 0 {
            format!("0.{}{}", "0".repeat((-decpt) as usize), digits)
        } else if (decpt as usize) >= digits.len() {
            // An integral value still carries a fractional part: `3.0`, not `3`.
            format!("{}{}.0", digits, "0".repeat(decpt as usize - digits.len()))
        } else {
            let (whole, fraction) = digits.split_at(decpt as usize);
            format!("{whole}.{fraction}")
        };
        return Ok(format!("{sign}{body}"));
    }

    let (lead, rest) = digits.split_at(1);
    let mantissa = if rest.is_empty() {
        lead.to_string()
    } else {
        format!("{lead}.{rest}")
    };
    let e = decpt - 1;
    let e_sign = if e < 0 { '-' } else { '+' };
    Ok(format!("{sign}{mantissa}e{e_sign}{:02}", e.abs()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sorts_keys_at_every_depth() {
        let value = json!({"b": 1, "a": {"z": 1, "y": 2}, "c": [{"n": 1, "m": 2}]});
        assert_eq!(
            canonical_json(&value).unwrap(),
            r#"{"a":{"y":2,"z":1},"b":1,"c":[{"m":2,"n":1}]}"#
        );
    }

    #[test]
    fn uses_compact_separators() {
        let value = json!({"a": [1, 2], "b": {"c": "d"}});
        assert_eq!(
            canonical_json(&value).unwrap(),
            r#"{"a":[1,2],"b":{"c":"d"}}"#
        );
    }

    #[test]
    fn keeps_non_ascii_literal_and_escapes_controls() {
        let value = json!({"k": "é\u{1f}\n\"\\"});
        // Non-ASCII literal (it costs three bytes escaped and two raw), controls as
        // lowercase \u00xx, short escapes where Python uses them.
        assert_eq!(
            canonical_json(&value).unwrap(),
            "{\"k\":\"é\\u001f\\n\\\"\\\\\"}"
        );
    }

    /// The table this rule exists for: every value here is what Python's
    /// `json.dumps` produces, and four of them are values `serde_json` writes
    /// differently. `python/tests/test_canonical.py` asserts the same table.
    #[test]
    fn formats_numbers_the_way_python_does() {
        let cases = [
            ("0", "0"),
            ("1", "1"),
            ("-1", "-1"),
            ("3.0", "3.0"),
            ("100.0", "100.0"),
            ("2.5", "2.5"),
            ("0.1", "0.1"),
            ("-0.0", "-0.0"),
            ("0.30000000000000004", "0.30000000000000004"),
            ("1e-4", "0.0001"),
            // serde_json writes this one as 0.00001.
            ("1e-5", "1e-05"),
            // serde_json writes this one as 1e-7.
            ("1e-7", "1e-07"),
            ("1e15", "1000000000000000.0"),
            ("1e16", "1e+16"),
            ("1e20", "1e+20"),
            ("1e21", "1e+21"),
            ("1e30", "1e+30"),
            ("5e-324", "5e-324"),
            ("1.7976931348623157e308", "1.7976931348623157e+308"),
        ];
        for (input, want) in cases {
            let value: Value = serde_json::from_str(input).unwrap();
            assert_eq!(canonical_json(&value).unwrap(), want, "input {input}");
        }
    }

    #[test]
    fn size_is_bytes_not_characters() {
        // Two characters, three bytes: the estimate has to count what was sent.
        let value = json!("é");
        assert_eq!(canonical_size(&value).unwrap(), 4);
    }

    #[test]
    fn canonical_state_carries_both_halves() {
        let state = CanonicalState::of(json!({"b": 1, "a": 2})).unwrap();
        assert_eq!(state.text, r#"{"a":2,"b":1}"#);
        assert_eq!(state.value["b"], json!(1));
        assert_eq!(state.size(), 13);
    }

    #[test]
    fn arbitrary_size_integers_remain_exact() {
        for raw in [
            "-9223372036854775809",
            "9223372036854775808",
            "18446744073709551615",
            "18446744073709551616",
            "184467440737095516170000000000000001",
        ] {
            let value: Value = serde_json::from_str(raw).unwrap();
            assert_eq!(canonical_json(&value).unwrap(), raw);
        }
    }
}
