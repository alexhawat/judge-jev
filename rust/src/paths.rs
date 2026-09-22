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
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// Return an external rubric directory when one was explicitly selected or found.
///
/// An explicit JUDGE_JEV_ROOT is returned even when invalid so callers surface the
/// configuration error instead of silently switching to embedded policy.
pub fn external_rubrics_dir() -> Option<PathBuf> {
    if let Ok(root) = env::var("JUDGE_JEV_ROOT") {
        return Some(PathBuf::from(root).join("shared").join("rubrics"));
    }
    if let Ok(cwd) = env::current_dir() {
        if let Some(root) = search_up(&cwd) {
            return Some(root.join("shared").join("rubrics"));
        }
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(dir) = exe.parent() {
            if let Some(root) = search_up(dir) {
                return Some(root.join("shared").join("rubrics"));
            }
        }
    }
    None
}

pub fn rubrics_dir() -> PathBuf {
    external_rubrics_dir().unwrap_or_else(|| repo_root().join("shared").join("rubrics"))
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
