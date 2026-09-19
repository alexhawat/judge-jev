use anyhow::{anyhow, Result};
use judge_jev::funnel::{replay_judgment, run_judgment};
use judge_jev::models::{JudgmentResult, SavedJudgment};
use judge_jev::rubric::{list_rubric_ids, show_rubric};
use judge_jev::{exit_for_verdict, setup, EXIT_ERROR, EXIT_OK, EXIT_USAGE};
use std::env;
use std::path::PathBuf;
use std::process::ExitCode;
use tracing_subscriber::EnvFilter;

const USAGE: &str = "usage: judge-jev <setup|run|rubric|replay> ...
  setup
  run    --rubric <id> --input <file.json> [--mock]
  replay --input <result.json>
  rubric list | show --id <id>";

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

fn dispatch(mut args: Vec<String>) -> Result<i32> {
    let Some(command) = args.first().cloned() else {
        eprintln!("{USAGE}");
        return Ok(EXIT_USAGE);
    };

    match command.as_str() {
        "setup" => {
            setup::run_setup()?;
            Ok(EXIT_OK)
        }
        "run" => {
            let rubric =
                flag(&mut args, "--rubric").ok_or_else(|| anyhow!("--rubric is required"))?;
            let input = flag(&mut args, "--input").ok_or_else(|| anyhow!("--input is required"))?;
            let mock = args.iter().any(|a| a == "--mock");
            let result = run_judgment(&rubric, &PathBuf::from(input), mock)?;
            print_result(&result)?;
            Ok(exit_for_verdict(&result.verdict))
        }
        "replay" => {
            let input = flag(&mut args, "--input").ok_or_else(|| anyhow!("--input is required"))?;
            let text =
                std::fs::read_to_string(&input).map_err(|e| anyhow!("cannot read {input}: {e}"))?;
            let saved: SavedJudgment = serde_json::from_str(&text)
                .map_err(|e| anyhow!("{input} is not a saved judgment: {e}"))?;
            let result = replay_judgment(&saved)?;
            print_result(&result)?;
            Ok(exit_for_verdict(&result.verdict))
        }
        "rubric" => match args.get(1).map(String::as_str) {
            Some("list") => {
                for id in list_rubric_ids()? {
                    println!("{id}");
                }
                Ok(EXIT_OK)
            }
            Some("show") => {
                let id = flag(&mut args, "--id").ok_or_else(|| anyhow!("--id is required"))?;
                println!("{}", show_rubric(&id)?);
                Ok(EXIT_OK)
            }
            _ => {
                eprintln!("usage: judge-jev rubric list|show --id <id>");
                Ok(EXIT_USAGE)
            }
        },
        other => {
            eprintln!("unknown command: {other}\n{USAGE}");
            Ok(EXIT_USAGE)
        }
    }
}

fn flag(args: &mut Vec<String>, name: &str) -> Option<String> {
    let pos = args.iter().position(|a| a == name)?;
    args.remove(pos);
    if pos < args.len() {
        return Some(args.remove(pos));
    }
    None
}

fn print_result(result: &JudgmentResult) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(result)?);
    Ok(())
}
