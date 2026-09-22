use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_dir(label: &str) -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let path =
        std::env::temp_dir().join(format!("judge-jev-{label}-{}-{nonce}", std::process::id()));
    fs::create_dir_all(&path).unwrap();
    path
}

fn copy_binary(directory: &Path) -> PathBuf {
    let source = PathBuf::from(env!("CARGO_BIN_EXE_judge-jev"));
    let name = if cfg!(windows) {
        "judge-jev.exe"
    } else {
        "judge-jev"
    };
    let target = directory.join(name);
    fs::copy(source, &target).unwrap();
    target
}

#[test]
fn copied_binary_uses_embedded_rubrics_outside_checkout() {
    let directory = temp_dir("standalone-assets");
    let binary = copy_binary(&directory);
    let output = Command::new(binary)
        .args(["rubric", "list"])
        .current_dir(&directory)
        .env_remove("JUDGE_JEV_ROOT")
        .output()
        .unwrap();
    assert_eq!(
        output.status.code(),
        Some(0),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8(output.stdout).unwrap();
    assert_eq!(
        stdout.lines().collect::<Vec<_>>(),
        ["agent-trajectory", "assistant-reply"]
    );
    fs::remove_dir_all(directory).unwrap();
}

#[test]
fn explicit_invalid_root_does_not_silently_use_embedded_policy() {
    let directory = temp_dir("standalone-explicit-root");
    let binary = copy_binary(&directory);
    let missing = directory.join("missing custom root");
    let output = Command::new(binary)
        .args(["rubric", "show", "--id", "assistant-reply"])
        .current_dir(&directory)
        .env("JUDGE_JEV_ROOT", &missing)
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(10));
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(stderr.contains(&missing.display().to_string()), "{stderr}");
    fs::remove_dir_all(directory).unwrap();
}
