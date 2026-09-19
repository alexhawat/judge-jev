use std::env;
use std::path::{Path, PathBuf};

pub fn repo_root() -> PathBuf {
    if let Ok(root) = env::var("JUDGE_JEV_ROOT") {
        return PathBuf::from(root);
    }
    if let Ok(manifest) = env::var("CARGO_MANIFEST_DIR") {
        let manifest_path = PathBuf::from(manifest);
        if let Some(root) = manifest_path.parent() {
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
