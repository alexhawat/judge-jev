from __future__ import annotations

import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "hooks" / "claude-code" / "claude_code_hook.py"
FIXTURES = ROOT / "hooks" / "claude-code" / "fixtures"
SPEC = importlib.util.spec_from_file_location("claude_code_hook", MODULE_PATH)
assert SPEC and SPEC.loader
hook = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = hook
SPEC.loader.exec_module(hook)


def event(transcript: Path, **overrides: object) -> dict[str, object]:
    value = json.loads((FIXTURES / "stop-event.json").read_text())
    value["transcript_path"] = str(transcript.resolve())
    value.update(overrides)
    return value


def fake_judge(tmp_path: Path, code: int, verdict: str = "pass") -> Path:
    result = {
        "rubric_id": "agent-trajectory", "rubric_version": "3.0.0",
        "rubric_hash": "sha256:" + "a" * 64,
        "source_rubric_version": "3.0.0", "source_rubric_hash": "sha256:" + "a" * 64,
        "verdict": verdict, "confidence": 0.8, "stage": "route", "model": "jev-1.13.0",
        "usage": {"input_tokens": 1, "output_tokens": 1}, "answers": {},
        "routing_reason": "fixture reason", "mock": True, "deciding_answers": [],
        "confidence_floor": 0.7, "runtime": {"name": "python", "version": "0.1.0"},
        "state_projection": {"paths": [], "hash": "sha256:x", "projected_keys": []},
        "deterministic_gates": [],
    }
    script = tmp_path / f"fake-judge-{code}"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\nsys.stdin.read()\n"
        f"print(json.dumps({result!r}))\n"
        f"raise SystemExit({code})\n"
    )
    script.chmod(0o755)
    return script


def fake_raw_judge(tmp_path: Path, code: int, stdout: str) -> Path:
    script = tmp_path / f"fake-raw-{code}-{len(list(tmp_path.iterdir()))}"
    script.write_text("#!/bin/sh\ncat >/dev/null\nprintf '%s' '" + stdout + "'\nexit " + str(code) + "\n")
    script.chmod(0o755)
    return script


def test_stop_event_normalizes_real_stdin_shape() -> None:
    normalized = hook.normalize_event(event(FIXTURES / "transcript-stop.jsonl"))
    assert normalized == {
        "goal": "Inspect the README and report its first heading.",
        "steps": [
            {
                "tool": "Read",
                "input": {"file_path": "README.md"},
                "tool_use_id": "toolu_read",
                "output": "# judge-jev\n\nJev-native judge kit.",
            }
        ],
        "final_output": "The first heading is judge-jev.",
    }


@pytest.mark.parametrize("code,verdict,blocked", [(0, "pass", False), (4, "skip", False), (1, "fail", True), (2, "review", True), (3, "escalate", True)])
def test_event_to_judge_to_host_response(tmp_path: Path, code: int, verdict: str, blocked: bool) -> None:
    response, result = hook.handle(
        event(FIXTURES / "transcript-stop.jsonl"),
        fake_judge(tmp_path, code, verdict),
    )
    assert result["verdict"] == verdict
    assert (response.get("decision") == "block") is blocked


def test_recursion_guard_never_calls_judge(tmp_path: Path) -> None:
    response, result = hook.handle(
        event(FIXTURES / "transcript-stop.jsonl", stop_hook_active=True),
        tmp_path / "does-not-exist",
    )
    assert response == {}
    assert result is None


def test_failure_policy_is_explicit() -> None:
    assert hook.host_response(10, None, stop_hook_active=False, failure_policy="open") == {}
    assert hook.host_response(10, None, stop_hook_active=False, failure_policy="closed")["decision"] == "block"


@pytest.mark.parametrize(
    ("code", "stdout"),
    [(0, ""), (0, "not-json"), (0, '{"verdict":"pass"}'), (0, '{"verdict":"fail"}'), (1, '{"verdict":"pass"}')],
)
def test_malformed_or_mismatched_judge_output_obeys_failure_policy(tmp_path: Path, code: int, stdout: str) -> None:
    judge = fake_raw_judge(tmp_path, code, stdout)
    open_response, _ = hook.handle(event(FIXTURES / "transcript-stop.jsonl"), judge, failure_policy="open")
    closed_response, _ = hook.handle(event(FIXTURES / "transcript-stop.jsonl"), judge, failure_policy="closed")
    assert open_response == {}
    assert closed_response["decision"] == "block"


@pytest.mark.parametrize("session_id", ["../escape", "bad/session", "", None])
def test_session_id_cannot_be_used_for_path_traversal(session_id: object) -> None:
    with pytest.raises(hook.HookError, match="session_id"):
        hook.normalize_event(event(FIXTURES / "transcript-stop.jsonl", session_id=session_id))


def test_sessions_normalize_concurrently_without_shared_state(tmp_path: Path) -> None:
    paths = []
    for index in range(12):
        path = tmp_path / f"session-{index}.jsonl"
        path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": f"goal {index}"}}) + "\n")
        paths.append(path)

    with ThreadPoolExecutor(max_workers=6) as pool:
        normalized = list(pool.map(lambda pair: hook.normalize_event(event(pair[1], session_id=f"session-{pair[0]}")), enumerate(paths)))
    assert [item["goal"] for item in normalized] == [f"goal {index}" for index in range(12)]


def test_installer_preview_round_trip_preserves_other_settings(tmp_path: Path) -> None:
    settings = {
        "permissions": {"allow": ["Read"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "/other/hook"}]}]},
    }
    handler = hook.owned_handler((ROOT / "scripts" / "judge-jev").resolve())
    installed = hook.merge_settings(settings, handler, uninstall=False)
    assert installed["permissions"] == settings["permissions"]
    assert installed["hooks"]["Stop"][0] == settings["hooks"]["Stop"][0]
    assert hook.OWNER in installed["hooks"]["Stop"][1]["hooks"][0]["args"]
    assert Path(installed["hooks"]["Stop"][1]["hooks"][0]["command"]).is_absolute()
    assert all(Path(value).is_absolute() for value in installed["hooks"]["Stop"][1]["hooks"][0]["args"] if value.startswith("/"))

    uninstalled = hook.merge_settings(installed, handler, uninstall=True)
    assert uninstalled == settings


def test_atomic_write_creates_backup(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"existing": true}\n')
    backup = hook.write_settings(path, {"existing": True, "added": 1})
    assert backup is not None and json.loads(backup.read_text()) == {"existing": True}
    assert json.loads(path.read_text()) == {"existing": True, "added": 1}
    second_backup = hook.write_settings(path, {"existing": True, "added": 2})
    assert second_backup is not None and second_backup != backup
    assert backup.exists() and second_backup.exists()
    assert json.loads(second_backup.read_text()) == {"existing": True, "added": 1}
