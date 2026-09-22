use anyhow::Result;
use judge_jev::funnel::{read_input_text, replay_judgment, run_judgment};
use judge_jev::models::{JudgmentResult, SavedJudgment, RUNTIME_NAME, RUNTIME_VERSION};
use judge_jev::rubric::{list_rubric_ids, show_rubric};
use judge_jev::{exit_for_verdict, setup, EXIT_ERROR, EXIT_OK, EXIT_USAGE};
use std::collections::{HashMap, HashSet};
use std::env;
use std::path::PathBuf;
use std::process::ExitCode;
use tracing_subscriber::EnvFilter;

const USAGE: &str = "usage: judge-jev <setup|run|rubric|replay> ...
  setup
  run    --rubric <id> --input <file.json|-> [--mock] [--tracing] [--tracing-to logfire]
  replay --input <result.json|-> [--allow-version-drift]
  rubric list | show --id <id>
  --version";

fn main() -> ExitCode {
    // Logs go to stderr so stdout carries nothing but the JudgmentResult JSON and
    // `judge-jev run | jq` works. The Python runtime's loguru sink does the same.
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("judge_jev=info")),
        )
        .with_target(false)
        .with_ansi(false)
        .with_writer(std::io::stderr)
        .init();

    let args = env::args().skip(1).collect::<Vec<_>>();
    let code = match dispatch(args) {
        Ok(code) => code,
        Err(err) => {
            // Operational failure: report it plainly and keep it distinct from a
            // verdict of 'fail'.
            eprintln!("judge-jev: {err:#}");
            EXIT_ERROR
        }
    };
    ExitCode::from(code as u8)
}

fn dispatch(args: Vec<String>) -> Result<i32> {
    let Some(command) = args.first().cloned() else {
        return Ok(usage_error("a command is required"));
    };

    // Printing the version is a deliberate, successful exit, so it is answered
    // before the command match and needs no subcommand.
    if command == "--version" && args.len() == 1 {
        println!("judge-jev {RUNTIME_VERSION} ({RUNTIME_NAME})");
        return Ok(EXIT_OK);
    }

    match command.as_str() {
        "setup" => {
            if let Err(msg) = Flags::parse(&args[1..], &[], &[]) {
                return Ok(usage_error(&msg));
            }
            setup::run_setup()?;
            Ok(EXIT_OK)
        }
        "run" => {
            let flags = match Flags::parse(
                &args[1..],
                &["--rubric", "--input", "--tracing-to"],
                &["--mock", "--tracing"],
            ) {
                Ok(flags) => flags,
                Err(msg) => return Ok(usage_error(&msg)),
            };
            let (rubric, input) = match (flags.require("--rubric"), flags.require("--input")) {
                (Ok(rubric), Ok(input)) => (rubric, input),
                (Err(msg), _) | (_, Err(msg)) => return Ok(usage_error(&msg)),
            };
            // Match Python's sink validation, but tracing is a no-op in Rust v1.
            // Like Python, --tracing-to alone does not enable tracing.
            if flags.is_set("--tracing") {
                let sink = flags
                    .values
                    .get("--tracing-to")
                    .map(String::as_str)
                    .filter(|s| !s.is_empty())
                    .unwrap_or("logfire")
                    .trim()
                    .to_lowercase();
                if sink != "logfire" {
                    anyhow::bail!("unsupported tracing sink '{sink}'; only 'logfire' is available");
                }
            }
            let result = run_judgment(&rubric, &PathBuf::from(input), flags.is_set("--mock"))?;
            print_result(&result)?;
            Ok(exit_for_verdict(&result.verdict))
        }
        "replay" => {
            let flags = match Flags::parse(&args[1..], &["--input"], &["--allow-version-drift"]) {
                Ok(flags) => flags,
                Err(msg) => return Ok(usage_error(&msg)),
            };
            let input = match flags.require("--input") {
                Ok(input) => input,
                Err(msg) => return Ok(usage_error(&msg)),
            };
            let text = read_input_text(&PathBuf::from(&input))?;
            let saved: SavedJudgment = serde_json::from_str(&text)
                .map_err(|e| anyhow::anyhow!("input {input} is not a saved judgment: {e}"))?;
            let result = replay_judgment(&saved, flags.is_set("--allow-version-drift"))?;
            print_result(&result)?;
            Ok(exit_for_verdict(&result.verdict))
        }
        "rubric" => match args.get(1).map(String::as_str) {
            Some("list") => {
                if let Err(msg) = Flags::parse(&args[2..], &[], &[]) {
                    return Ok(usage_error(&msg));
                }
                for id in list_rubric_ids()? {
                    println!("{id}");
                }
                Ok(EXIT_OK)
            }
            Some("show") => {
                let flags = match Flags::parse(&args[2..], &["--id"], &[]) {
                    Ok(flags) => flags,
                    Err(msg) => return Ok(usage_error(&msg)),
                };
                let id = match flags.require("--id") {
                    Ok(id) => id,
                    Err(msg) => return Ok(usage_error(&msg)),
                };
                println!("{}", show_rubric(&id)?);
                Ok(EXIT_OK)
            }
            Some(other) => Ok(usage_error(&format!("unknown rubric command: {other}"))),
            None => Ok(usage_error("rubric needs a subcommand: list or show")),
        },
        other => Ok(usage_error(&format!("unknown command: {other}"))),
    }
}

/// Report a malformed command line.
///
/// Usage errors exit 11, never a verdict code and never 10: the judgment did not
/// happen *and* nothing was attempted, so a hook can tell "you called me wrong"
/// apart from "the call was right and the run broke". The message text is shared
/// with the Python runtime verbatim; scripts/check-parity.sh compares both.
fn usage_error(message: &str) -> i32 {
    eprintln!("judge-jev: {message}");
    eprintln!("{USAGE}");
    EXIT_USAGE
}

/// Flags pulled off one command's argv.
///
/// The previous implementation scanned for the flags it wanted and never looked at
/// what was left, so `--mok` was silently dropped — and because `--mock` was then
/// absent, a typo turned a mocked call into a billed live one. Anything unclaimed
/// is now an error.
struct Flags {
    values: HashMap<String, String>,
    set: HashSet<String>,
}

impl Flags {
    fn parse(args: &[String], value_flags: &[&str], bool_flags: &[&str]) -> Result<Self, String> {
        let mut values: HashMap<String, String> = HashMap::new();
        let mut set: HashSet<String> = HashSet::new();
        let mut i = 0;
        while i < args.len() {
            let arg = args[i].as_str();
            if value_flags.contains(&arg) {
                if values.contains_key(arg) {
                    return Err(format!("{arg} given more than once"));
                }
                // `-` is a value (stdin), not the start of another flag.
                match args.get(i + 1) {
                    Some(value) if value == "-" || !value.starts_with('-') => {
                        values.insert(arg.to_string(), value.clone());
                    }
                    _ => return Err(format!("{arg} needs a value")),
                }
                i += 2;
                continue;
            }
            if bool_flags.contains(&arg) {
                if !set.insert(arg.to_string()) {
                    return Err(format!("{arg} given more than once"));
                }
                i += 1;
                continue;
            }
            if arg.starts_with('-') && arg != "-" {
                return Err(format!("unrecognized flag: {arg}"));
            }
            return Err(format!("unexpected argument: {arg}"));
        }
        Ok(Flags { values, set })
    }

    fn require(&self, name: &str) -> Result<String, String> {
        self.values
            .get(name)
            .cloned()
            .ok_or_else(|| format!("{name} is required"))
    }

    fn is_set(&self, name: &str) -> bool {
        self.set.contains(name)
    }
}

fn print_result(result: &JudgmentResult) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(result)?);
    Ok(())
}
