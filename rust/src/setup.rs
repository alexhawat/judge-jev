use crate::paths::{ensure_parent, repo_root, runtime_config_path};
use anyhow::{Context, Result};
use std::io::{self, Write};
use std::process::Command;

fn prompt_runtime() -> Result<String> {
    if let Ok(env) = std::env::var("JUDGE_JEV_RUNTIME") {
        let rt = env.trim().to_lowercase();
        if rt == "python" || rt == "rust" {
            return Ok(rt);
        }
    }
    println!("Select judge-jev runtime:");
    println!("  1) python (uv + loguru + typesafe-sdk)");
    println!("  2) rust (cargo + tracing + HTTP client)");
    print!("Enter 1 or 2 [1]: ");
    io::stdout().flush()?;
    let mut line = String::new();
    io::stdin().read_line(&mut line)?;
    let choice = line.trim();
    Ok(if choice == "2" { "rust".into() } else { "python".into() })
}

fn install_python(root: &std::path::Path) -> Result<()> {
    let status = Command::new("uv")
        .args(["sync", "--dev"])
        .current_dir(root.join("python"))
        .status()
        .context("run uv sync")?;
    if !status.success() {
        anyhow::bail!("uv sync failed");
    }
    Ok(())
}

fn install_rust(root: &std::path::Path) -> Result<()> {
    let status = Command::new("cargo")
        .args(["build", "--release"])
        .current_dir(root.join("rust"))
        .status()
        .context("run cargo build")?;
    if !status.success() {
        anyhow::bail!("cargo build failed");
    }
    Ok(())
}

pub fn run_setup() -> Result<()> {
    let root = repo_root();
    let runtime = prompt_runtime()?;
    match runtime.as_str() {
        "python" => install_python(&root)?,
        "rust" => install_rust(&root)?,
        _ => anyhow::bail!("unknown runtime"),
    }
    let cfg = runtime_config_path();
    ensure_parent(&cfg)?;
    std::fs::write(&cfg, format!("{runtime}\n"))?;
    println!("Runtime '{runtime}' configured at {}", cfg.display());
    println!("Set TYPESAFE_API_KEY for live judging, or use --mock in CI.");
    Ok(())
}
