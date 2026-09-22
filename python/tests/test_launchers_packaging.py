from __future__ import annotations

import os
import pty
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[2]


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def isolated_root(tmp_path: Path, *, python_bin: bool = True, rust_bin: bool = True) -> Path:
    root = tmp_path / "checkout with spaces"
    (root / "scripts").mkdir(parents=True)
    for name in ("judge-jev", "setup.sh"):
        shutil.copy2(REPO / "scripts" / name, root / "scripts" / name)
    fake_runtime = (
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$FAKE_RUNTIME_NAME\" > \"$FAKE_LOG.runtime\"\n"
        "printf '%s\\0' \"$@\" > \"$FAKE_LOG.args\"\n"
        "pwd > \"$FAKE_LOG.cwd\"\n"
        "if [[ \"${FAKE_READ_STDIN:-0}\" == 1 ]]; then cat > \"$FAKE_LOG.stdin\"; fi\n"
        "exit \"${FAKE_EXIT:-0}\"\n"
    )
    if python_bin:
        write_executable(
            root / "python/.venv/bin/judge-jev",
            fake_runtime.replace("$FAKE_RUNTIME_NAME", "python"),
        )
    if rust_bin:
        write_executable(
            root / "rust/target/release/judge-jev",
            fake_runtime.replace("$FAKE_RUNTIME_NAME", "rust"),
        )
    return root


def fake_tools(tmp_path: Path, *, uv_exit: int = 0, cargo_exit: int = 0) -> Path:
    tools = tmp_path / "tools"
    write_executable(
        tools / "uv",
        f"#!/usr/bin/env bash\nprintf 'uv %s\\n' \"$*\" >> \"$FAKE_LOG.tools\"\nexit {uv_exit}\n",
    )
    write_executable(
        tools / "cargo",
        f"#!/usr/bin/env bash\nprintf 'cargo %s\\n' \"$*\" >> \"$FAKE_LOG.tools\"\nexit {cargo_exit}\n",
    )
    return tools


def environment(tmp_path: Path, tools: Path, **updates: str) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("JUDGE_JEV_RUNTIME", None)
    env.update(
        PATH=f"{tools}:/usr/bin:/bin",
        FAKE_LOG=str(tmp_path / "invocation"),
        **updates,
    )
    return env


def run_launcher(
    root: Path,
    env: dict[str, str],
    *args: str,
    cwd: Path | None = None,
    stdin: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(root / "scripts/judge-jev"), *args],
        cwd=cwd or root,
        env=env,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def test_runtime_precedence_and_python_only_commands(tmp_path: Path) -> None:
    root = isolated_root(tmp_path)
    tools = fake_tools(tmp_path)
    runtime = root / ".judge-jev/runtime"
    runtime.parent.mkdir()
    runtime.write_text("rust\n")
    env = environment(tmp_path, tools)

    assert run_launcher(root, env, "run").returncode == 0
    assert Path(env["FAKE_LOG"] + ".runtime").read_text() == "rust\n"
    env["JUDGE_JEV_RUNTIME"] = "python"
    assert run_launcher(root, env, "run").returncode == 0
    assert Path(env["FAKE_LOG"] + ".runtime").read_text() == "python\n"
    env["JUDGE_JEV_RUNTIME"] = "rust"
    assert run_launcher(root, env, "doctor").returncode == 0
    assert Path(env["FAKE_LOG"] + ".runtime").read_text() == "python\n"
    runtime.unlink()
    env.pop("JUDGE_JEV_RUNTIME")
    assert run_launcher(root, env, "run").returncode == 0
    assert Path(env["FAKE_LOG"] + ".runtime").read_text() == "python\n"


def test_non_tty_no_args_does_not_prompt_and_tty_uses_python(tmp_path: Path) -> None:
    root = isolated_root(tmp_path)
    tools = fake_tools(tmp_path)
    (root / ".judge-jev").mkdir()
    (root / ".judge-jev/runtime").write_text("rust\n")
    env = environment(tmp_path, tools, FAKE_EXIT="11")
    assert run_launcher(root, env).returncode == 11
    assert Path(env["FAKE_LOG"] + ".runtime").read_text() == "rust\n"

    master, slave = pty.openpty()
    process = subprocess.Popen(
        ["/bin/bash", str(root / "scripts/judge-jev")],
        cwd=root,
        env=env,
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(slave)
    time.sleep(0.1)
    os.close(master)
    process.communicate(timeout=10)
    assert process.returncode == 11
    assert Path(env["FAKE_LOG"] + ".runtime").read_text() == "python\n"


@pytest.mark.parametrize("exit_code", [0, 1, 2, 3, 4, 11])
def test_preserves_runtime_exit_codes(tmp_path: Path, exit_code: int) -> None:
    root = isolated_root(tmp_path)
    tools = fake_tools(tmp_path)
    env = environment(tmp_path, tools, JUDGE_JEV_RUNTIME="python", FAKE_EXIT=str(exit_code))
    assert run_launcher(root, env, "run").returncode == exit_code


def test_preserves_caller_cwd_arguments_spaces_and_stdin(tmp_path: Path) -> None:
    root = isolated_root(tmp_path)
    tools = fake_tools(tmp_path)
    caller = tmp_path / "caller directory"
    caller.mkdir()
    relative = "input file.json"
    env = environment(
        tmp_path,
        tools,
        JUDGE_JEV_RUNTIME="python",
        FAKE_READ_STDIN="1",
    )
    completed = run_launcher(
        root,
        env,
        "run",
        "--input",
        relative,
        cwd=caller,
        stdin='{"through":"stdin"}',
    )
    assert completed.returncode == 0
    assert Path(env["FAKE_LOG"] + ".cwd").read_text().strip() == str(caller)
    assert Path(env["FAKE_LOG"] + ".args").read_bytes().split(b"\0")[:-1] == [
        b"run",
        b"--input",
        relative.encode(),
    ]
    assert Path(env["FAKE_LOG"] + ".stdin").read_text() == '{"through":"stdin"}'


def test_bootstrap_failures_map_to_ten_and_rust_build_runs(tmp_path: Path) -> None:
    missing_python = isolated_root(tmp_path / "python", python_bin=False)
    failed_uv = fake_tools(tmp_path / "python", uv_exit=7)
    env = environment(tmp_path / "python", failed_uv, JUDGE_JEV_RUNTIME="python")
    assert run_launcher(missing_python, env, "run").returncode == 10

    root = isolated_root(tmp_path / "rust")
    failed_cargo = fake_tools(tmp_path / "rust", cargo_exit=7)
    env = environment(tmp_path / "rust", failed_cargo, JUDGE_JEV_RUNTIME="rust")
    assert run_launcher(root, env, "run").returncode == 10

    tools = fake_tools(tmp_path / "ok")
    env = environment(tmp_path / "ok", tools, JUDGE_JEV_RUNTIME="rust")
    assert run_launcher(root, env, "run").returncode == 0
    assert "cargo build --manifest-path" in Path(env["FAKE_LOG"] + ".tools").read_text()


def test_setup_updates_preference_only_after_install_and_smoke(tmp_path: Path) -> None:
    root = isolated_root(tmp_path)
    runtime = root / ".judge-jev/runtime"
    runtime.parent.mkdir()
    runtime.write_text("rust\n")

    failed_tools = fake_tools(tmp_path / "failed", uv_exit=7)
    env = environment(tmp_path / "failed", failed_tools, JUDGE_JEV_RUNTIME="python")
    failed = subprocess.run(
        ["/bin/bash", str(root / "scripts/setup.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert failed.returncode == 10
    assert runtime.read_text() == "rust\n"

    smoke_tools = fake_tools(tmp_path / "smoke-failed")
    env = environment(
        tmp_path / "smoke-failed",
        smoke_tools,
        JUDGE_JEV_RUNTIME="python",
        FAKE_EXIT="1",
    )
    smoke_failed = subprocess.run(
        ["/bin/bash", str(root / "scripts/setup.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert smoke_failed.returncode == 10
    assert runtime.read_text() == "rust\n"

    tools = fake_tools(tmp_path / "success")
    env = environment(tmp_path / "success", tools, JUDGE_JEV_RUNTIME="python")
    succeeded = subprocess.run(
        ["/bin/bash", str(root / "scripts/setup.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert succeeded.returncode == 0
    assert runtime.read_text() == "python\n"

    env.pop("JUDGE_JEV_RUNTIME")
    noninteractive = subprocess.run(
        ["/bin/bash", str(root / "scripts/setup.sh")],
        cwd=root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert noninteractive.returncode == 0
    assert runtime.read_text() == "python\n"


def test_wheel_uses_packaged_assets_outside_checkout(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to build the wheel")
    dist = tmp_path / "dist"
    env = os.environ.copy()
    warm_cache = Path("/tmp/judge-jev-uv-cache")
    if warm_cache.is_dir():
        env.update(UV_CACHE_DIR=str(warm_cache), UV_OFFLINE="1")
    else:
        env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(dist)],
        cwd=REPO / "python",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "judge_jev/assets/rubrics/assistant-reply.yaml" in names
    assert "judge_jev/assets/schemas/judgment-result.schema.json" in names

    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True)
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    installed_env = os.environ.copy()
    installed_env.pop("JUDGE_JEV_ROOT", None)
    installed_env["PYTHONPATH"] = str(Path(yaml.__file__).resolve().parents[1])
    probe = subprocess.run(
        [
            str(python),
            "-c",
            "from judge_jev.paths import rubrics_dir, schemas_dir; "
            "from judge_jev.rubric import load_rubric; "
            "r=rubrics_dir(); s=schemas_dir(); assert load_rubric('assistant-reply').id == 'assistant-reply'; "
            "assert (s/'judgment-result.schema.json').is_file(); "
            "print(r, s)",
        ],
        cwd=outside,
        env=installed_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "site-packages" in probe.stdout

    installed_env["JUDGE_JEV_ROOT"] = str(tmp_path / "missing-explicit-root")
    explicit = subprocess.run(
        [
            str(python),
            "-c",
            "from judge_jev.rubric import load_rubric; load_rubric('assistant-reply')",
        ],
        cwd=outside,
        env=installed_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert explicit.returncode != 0
    assert "Rubric not found: assistant-reply" in explicit.stderr


def test_powershell_mirror_has_noninteractive_and_bootstrap_guards() -> None:
    launcher = (REPO / "scripts/judge-jev.ps1").read_text()
    setup = (REPO / "scripts/setup.ps1").read_text()
    assert "[Console]::IsInputRedirected" in launcher
    assert "Python bootstrap did not install" in launcher
    assert "Rust bootstrap did not build" in launcher
    assert "[Console]::IsInputRedirected" in setup
    assert "[System.IO.File]::Move" in setup
