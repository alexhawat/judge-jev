from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell is not installed")


def write_cmd(path: Path, runtime: str, exit_code: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"@echo off\r\necho {runtime}> \"%FAKE_LOG%.runtime\"\r\nexit /b {exit_code}\r\n",
        encoding="utf-8",
    )


def write_tool(path: Path, exit_code: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"@echo off\r\nexit /b {exit_code}\r\n", encoding="utf-8")


def invoke(script: Path, root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    assert PWSH is not None
    return subprocess.run(
        [PWSH, "-NoLogo", "-NoProfile", "-File", str(script), *args],
        cwd=root,
        env=env,
        input="",
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def windows_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "checkout with spaces"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in ("judge-jev.ps1", "setup.ps1"):
        shutil.copy2(REPO / "scripts" / name, scripts / name)
    write_cmd(root / "python/.venv/Scripts/judge-jev.cmd", "python")
    write_cmd(root / "rust/target/release/judge-jev.cmd", "rust")
    tools = tmp_path / "tools"
    write_tool(tools / "uv.cmd", 0)
    write_tool(tools / "cargo.cmd", 0)
    return root, tools


def test_powershell_selection_exit_passthrough_and_setup_rollback(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("cmd launcher fixtures require Windows")
    root, tools = windows_root(tmp_path)
    runtime = root / ".judge-jev/runtime"
    runtime.parent.mkdir()
    runtime.write_text("rust\n")
    env = os.environ.copy()
    env.pop("JUDGE_JEV_RUNTIME", None)
    env["PATH"] = f"{tools}{os.pathsep}{env['PATH']}"
    env["FAKE_LOG"] = str(tmp_path / "invocation")

    result = invoke(root / "scripts/judge-jev.ps1", root, env, "run")
    assert result.returncode == 0
    assert Path(env["FAKE_LOG"] + ".runtime").read_text().strip() == "rust"

    env["JUDGE_JEV_RUNTIME"] = "python"
    result = invoke(root / "scripts/judge-jev.ps1", root, env, "run")
    assert result.returncode == 0
    assert Path(env["FAKE_LOG"] + ".runtime").read_text().strip() == "python"

    env["JUDGE_JEV_RUNTIME"] = "rust"
    result = invoke(root / "scripts/judge-jev.ps1", root, env, "--verbose", "reply")
    assert result.returncode == 0
    assert Path(env["FAKE_LOG"] + ".runtime").read_text().strip() == "python"

    env["JUDGE_JEV_RUNTIME"] = "python"
    for exit_code in (0, 1, 2, 3, 4, 10, 11):
        write_cmd(root / "python/.venv/Scripts/judge-jev.cmd", "python", exit_code)
        result = invoke(root / "scripts/judge-jev.ps1", root, env, "run")
        assert result.returncode == exit_code

    write_tool(tools / "uv.cmd", 7)
    failed = invoke(root / "scripts/setup.ps1", root, env)
    assert failed.returncode == 10
    assert runtime.read_text().strip() == "rust"

    write_tool(tools / "uv.cmd", 0)
    write_cmd(root / "python/.venv/Scripts/judge-jev.cmd", "python", 0)
    succeeded = invoke(root / "scripts/setup.ps1", root, env)
    assert succeeded.returncode == 0
    assert runtime.read_text().strip() == "python"

    env["JUDGE_JEV_RUNTIME"] = "invalid"
    invalid = invoke(root / "scripts/setup.ps1", root, env)
    assert invalid.returncode == 11

    shutil.rmtree(root / ".judge-jev")
    (root / ".judge-jev").write_text("blocks directory creation", encoding="utf-8")
    env["JUDGE_JEV_RUNTIME"] = "python"
    config_failure = invoke(root / "scripts/setup.ps1", root, env)
    assert config_failure.returncode == 10

    # A path that exists but cannot be launched is a controlled operational
    # failure rather than PowerShell's ambient exit 1.
    broken_root, broken_tools = windows_root(tmp_path / "broken")
    executable = broken_root / "python/.venv/Scripts/judge-jev.cmd"
    executable.unlink()
    executable.mkdir()
    broken_env = os.environ.copy()
    broken_env["JUDGE_JEV_RUNTIME"] = "python"
    broken_env["PATH"] = f"{broken_tools}{os.pathsep}{broken_env['PATH']}"
    assert invoke(broken_root / "scripts/judge-jev.ps1", broken_root, broken_env, "run").returncode == 10


def test_powershell_launcher_resolves_python_binary_after_fresh_sync(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("cmd launcher fixtures require Windows")
    root, tools = windows_root(tmp_path)
    target = root / "python/.venv/Scripts/judge-jev.cmd"
    target.unlink()
    seed = tmp_path / "fresh-judge.cmd"
    write_cmd(seed, "python")
    (tools / "uv.cmd").write_text(
        "@echo off\r\n"
        "mkdir \"%FAKE_BOOTSTRAP_DIR%\" 2>nul\r\n"
        "copy /Y \"%FAKE_BOOTSTRAP_SOURCE%\" \"%FAKE_BOOTSTRAP_TARGET%\" >nul\r\n"
        "exit /b %ERRORLEVEL%\r\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["JUDGE_JEV_RUNTIME"] = "python"
    env["PATH"] = f"{tools}{os.pathsep}{env['PATH']}"
    env["FAKE_LOG"] = str(tmp_path / "fresh-invocation")
    env["FAKE_BOOTSTRAP_DIR"] = str(target.parent)
    env["FAKE_BOOTSTRAP_SOURCE"] = str(seed)
    env["FAKE_BOOTSTRAP_TARGET"] = str(target)
    result = invoke(root / "scripts/judge-jev.ps1", root, env, "run")
    assert result.returncode == 0, result.stderr
    assert Path(env["FAKE_LOG"] + ".runtime").read_text().strip() == "python"
