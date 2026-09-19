use std::env;
use std::path::{Path, PathBuf};

fn has_marker(dir: &Path) -> bool {
    dir.join("shared").join("rubrics").is_dir()
}

fn search_up(start: &Path) -> Option<PathBuf> {
    let mut current = Some(start);
    while let Some(dir) = current {
        if has_marker(dir) {
            return Some(dir.to_path_buf());
        }
        current = dir.parent();
    }
    None
}

/// Locate the repo that owns shared/rubrics.
///
/// JUDGE_JEV_ROOT wins when set; the wrapper script always sets it. Otherwise look
/// upward from the working directory and then from the executable's own directory,
/// so an installed binary still finds a checkout it is run from or installed into.
/// CARGO_MANIFEST_DIR is only consulted at compile time, as a fallback for `cargo
/// run` and `cargo test`; it is never set for a released binary.
pub fn repo_root() -> PathBuf {
    if let Ok(root) = env::var("JUDGE_JEV_ROOT") {
        return PathBuf::from(root);
    }
    if let Ok(cwd) = env::current_dir() {
        if let Some(root) = search_up(&cwd) {
            return root;
        }
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            if let Some(root) = search_up(dir) {
                return root;
            }
        }
    }
    if let Some(root) = PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent() {
        if has_marker(root) {
            return root.to_path_buf();
        }
    }
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

pub fn rubrics_dir() -> PathBuf {
    repo_root().join("shared").join("rubrics")
}

pub fn runtime_config_path() -> PathBuf {
    repo_root().join(".judge-jev").join("runtime")
}

pub fn ensure_parent(path: &Path) -> anyhow::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    Ok(())
}
