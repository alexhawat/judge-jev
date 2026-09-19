use judge_jev::funnel::{replay_judgment, run_judgment};
use judge_jev::models::JudgmentResult;
use judge_jev::rubric::{list_rubric_ids, show_rubric};
use judge_jev::{exit_for_verdict, setup};
use std::env;
use std::path::PathBuf;
use std::process::ExitCode;
use tracing_subscriber::EnvFilter;

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("judge_jev=info".parse().unwrap()))
        .with_target(false)
        .init();

    let mut args = env::args().skip(1).collect::<Vec<_>>();
    if args.is_empty() {
        eprintln!("usage: judge-jev <setup|run|rubric|replay> ...");
        return ExitCode::from(1);
    }

    let code = match args[0].as_str() {
        "setup" => match setup::run_setup() {
            Ok(()) => judge_jev::EXIT_OK,
            Err(err) => {
                eprintln!("{err}");
                1
            }
        },
        "run" => {
            let rubric = flag(&mut args, "--rubric").expect("--rubric required");
            let input = flag(&mut args, "--input").expect("--input required");
            let mock = args.iter().any(|a| a == "--mock");
            match run_judgment(&rubric, &PathBuf::from(input), mock) {
                Ok(result) => {
                    print_result(&result);
                    exit_for_verdict(&result.verdict)
                }
                Err(err) => {
                    eprintln!("{err}");
                    1
                }
            }
        }
        "replay" => {
            let input = flag(&mut args, "--input").expect("--input required");
            match std::fs::read_to_string(&input)
                .map_err(anyhow::Error::from)
                .and_then(|text| {
                    let saved: JudgmentResult = serde_json::from_str(&text)?;
                    replay_judgment(&saved)
                }) {
                Ok(result) => {
                    print_result(&result);
                    exit_for_verdict(&result.verdict)
                }
                Err(err) => {
                    eprintln!("{err}");
                    1
                }
            }
        }
        "rubric" => {
            if args.get(1).map(String::as_str) == Some("list") {
                match list_rubric_ids() {
                    Ok(ids) => {
                        for id in ids {
                            println!("{id}");
                        }
                        judge_jev::EXIT_OK
                    }
                    Err(err) => {
                        eprintln!("{err}");
                        1
                    }
                }
            } else if args.get(1).map(String::as_str) == Some("show") {
                let id = flag(&mut args, "--id").expect("--id required");
                match show_rubric(&id) {
                    Ok(text) => {
                        println!("{text}");
                        judge_jev::EXIT_OK
                    }
                    Err(err) => {
                        eprintln!("{err}");
                        1
                    }
                }
            } else {
                eprintln!("usage: judge-jev rubric list|show --id <id>");
                1
            }
        }
        other => {
            eprintln!("unknown command: {other}");
            1
        }
    };
    ExitCode::from(code as u8)
}

fn flag(args: &mut Vec<String>, name: &str) -> Option<String> {
    if let Some(pos) = args.iter().position(|a| a == name) {
        args.remove(pos);
        if pos < args.len() {
            return Some(args.remove(pos));
        }
    }
    None
}

fn print_result(result: &JudgmentResult) {
    println!("{}", serde_json::to_string_pretty(result).expect("serialize result"));
}
