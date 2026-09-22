from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from judge_jev.cli import EXIT_USAGE, main
from judge_jev.history import delete_all, list_entries, save_result
from judge_jev.guided import GuidedUsageError, _multiline, _public_launcher, doctor_report


def test_no_args_non_tty_is_usage_without_prompt(capsys) -> None:
    assert main([]) == EXIT_USAGE
    captured = capsys.readouterr()
    assert "a command is required" in captured.err
    assert captured.out == ""


def test_multiline_keyboard_cancel_is_clean(monkeypatch, capsys) -> None:
    monkeypatch.setattr("builtins.input", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(GuidedUsageError, match="reply entry cancelled"):
        _multiline("reply")
    assert "Finish with" in capsys.readouterr().err


def test_keyboard_cancel_never_submits_partial_live_reply(monkeypatch, capsys) -> None:
    responses = iter(["partially typed prompt", KeyboardInterrupt()])

    def typed_then_cancel() -> str:
        value = next(responses)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("judge_jev.guided.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", typed_then_cancel)
    assert main(["reply"]) == EXIT_USAGE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "entry cancelled" in captured.err


def test_public_launcher_selects_powershell_on_windows(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("judge_jev.guided.os.name", "nt")
    assert _public_launcher(tmp_path) == tmp_path / "scripts" / "judge-jev.ps1"


def test_reply_mock_human_and_json(capsys) -> None:
    argv = ["reply", "--prompt", "What is 2+2?", "--reply", "4", "--mock"]
    assert main(argv) == 0
    human = capsys.readouterr()
    assert "DEMO — CANNED ANSWERS — PASS" in human.out
    assert "offline canned demo" in human.out
    assert "canned answers prove mechanics" in human.out
    assert "Try a real judgment" in human.out
    assert "INFO" not in human.err
    assert "WARNING" not in human.err

    assert main([*argv, "--format", "json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["verdict"] == "pass"
    assert payload["mock"] is True
    assert payload["rubric_hash"]


def test_reply_required_values_are_usage_errors(capsys) -> None:
    assert main(["reply", "--prompt", "hello", "--mock"]) == EXIT_USAGE
    assert "reply needs a non-empty --reply/--reply-file" in capsys.readouterr().err


def test_reply_editor_uses_direct_argv_and_json_document(tmp_path: Path, monkeypatch, capsys) -> None:
    editor = tmp_path / "editor helper.py"
    editor.write_text(
        "import json, pathlib, sys\nprint('editor status')\n"
        "pathlib.Path(sys.argv[-1]).write_text(json.dumps({"
        "'prompt': 'p', 'reply': 'r', 'context': 'c'}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    marker = tmp_path / "must-not-exist"
    quote = subprocess.list2cmdline if os.name == "nt" else shlex.join
    monkeypatch.setenv(
        "EDITOR", quote([sys.executable, str(editor), ";", "touch", str(marker)])
    )
    # The semicolon and following words are ordinary arguments, never interpreted
    # by a shell, so the helper still edits the final path without creating marker.
    assert main(["reply", "--editor", "--mock"]) == 0
    assert not marker.exists()
    capsys.readouterr()

    monkeypatch.setenv("EDITOR", quote([sys.executable, str(editor)]))
    assert main(["reply", "--editor", "--mock", "--format", "json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["verdict"] == "pass"
    assert "editor status" not in captured.out


def test_input_template_and_validation_are_offline(tmp_path: Path, capsys) -> None:
    assert main(["input", "template", "--rubric", "assistant-reply"]) == 0
    template = json.loads(capsys.readouterr().out)
    assert set(template) == {"prompt", "reply", "context"}

    path = tmp_path / "input with spaces.json"
    path.write_text(json.dumps({"prompt": "hello", "reply": "hi"}), encoding="utf-8")
    assert main(["input", "validate", "--rubric", "assistant-reply", "--input", str(path), "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is True
    assert report["network_used"] is False

    missing = tmp_path / "missing.json"
    missing.write_text(json.dumps({"prompt": "hello"}), encoding="utf-8")
    assert main(["input", "validate", "--rubric", "assistant-reply", "--input", str(missing)]) == EXIT_USAGE
    assert "non-empty reply" in capsys.readouterr().err

    malformed = tmp_path / "malformed.json"
    malformed.write_text(json.dumps({"prompt": "hello", "reply": ["wrong"]}), encoding="utf-8")
    assert main(["input", "validate", "--rubric", "assistant-reply", "--input", str(malformed)]) == 10
    assert "must be a string" in capsys.readouterr().err


def test_history_is_opt_in_result_only_and_explicitly_deleted(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("JUDGE_JEV_HISTORY_DIR", str(tmp_path / "private history"))
    secret_prompt = "private prompt that must not be stored"
    secret_reply = "private reply that must not be stored"
    assert main([
        "reply",
        "--prompt",
        secret_prompt,
        "--reply",
        secret_reply,
        "--mock",
        "--save",
        "--format",
        "json",
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    history_id = result["history_id"]
    saved_path = next((tmp_path / "private history").glob("*.json"))
    stored = saved_path.read_text(encoding="utf-8")
    assert secret_prompt not in stored
    assert secret_reply not in stored
    if os.name == "posix":
        assert saved_path.stat().st_mode & 0o777 == 0o600

    assert main(["explain", "--history-id", history_id, "--format", "json"]) == 0
    explanation = json.loads(capsys.readouterr().out)
    assert explanation["matched_rule_id"].startswith("rule:")
    assert explanation["rubric_hash"] == result["rubric_hash"]

    assert main(["history", "delete", "--all"]) == EXIT_USAGE
    assert "requires --confirm-all" in capsys.readouterr().err
    assert main(["history", "delete", "--all", "--confirm-all", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"deleted": 1}


def test_doctor_is_offline_and_key_presence_only(monkeypatch, capsys) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "never-print-this-secret")
    assert main(["doctor", "--format", "json"]) == 0
    captured = capsys.readouterr()
    assert "never-print-this-secret" not in captured.out + captured.err
    report = json.loads(captured.out)
    assert report["typesafe_api_key_present"] is True
    assert report["network_used"] is False
    assert report["offline_smoke"]["passed"] is True


def test_doctor_forces_launcher_dependency_checks_offline(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/setup.sh").write_text("#!/bin/sh\n")
    fixture = tmp_path / "hooks/claude-code/fixtures/stop-event.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("{}")
    (tmp_path / "hooks/claude-code/claude_code_hook.py").write_text("# fixture\n")
    captured: dict[str, str] = {}

    def fake_run(*_args, **kwargs):
        captured.update(kwargs["env"])
        payload = {"result": {"verdict": "review"}, "host_response": {"decision": "block"}}
        return subprocess.CompletedProcess([], 0, json.dumps(payload), "")

    monkeypatch.setattr("judge_jev.guided.repo_root", lambda: tmp_path)
    monkeypatch.setattr("judge_jev.guided.subprocess.run", fake_run)
    report = doctor_report()
    assert report["offline_smoke"]["passed"] is True
    assert captured["UV_OFFLINE"] == "1"
    assert captured["CARGO_NET_OFFLINE"] == "true"


def test_replay_human_output_says_no_new_api_call(tmp_path: Path, capsys) -> None:
    assert main([
        "reply", "--prompt", "p", "--reply", "r", "--mock", "--format", "json"
    ]) == 0
    saved = json.loads(capsys.readouterr().out)
    path = tmp_path / "saved.json"
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert main(["replay", "--input", str(path), "--format", "human"]) == 0
    output = capsys.readouterr().out
    assert "offline replay/view; no API call was made" in output
    assert "Original evidence             canned mock answers" in output
    assert "live API judgment" not in output


def test_history_retention_and_delete_ignore_unowned_or_malformed_files(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "history"
    root.mkdir()
    monkeypatch.setenv("JUDGE_JEV_HISTORY_DIR", str(root))
    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    plausible = root / "20260922T120000000000Z-deadbeef.json"
    plausible.write_text(
        json.dumps({"history_id": "../outside", "saved_at": "now", "result": {}}),
        encoding="utf-8",
    )
    arbitrary = root / "user-document.json"
    arbitrary.write_text("{}", encoding="utf-8")
    malformed = root / "20260922T120000000001Z-deadbeef.json"
    malformed.write_text(json.dumps({"history_id": malformed.stem, "result": []}), encoding="utf-8")
    if hasattr(os, "symlink"):
        try:
            os.symlink(outside, root / "20260922T120000000002Z-deadbeef.json")
        except OSError:
            pass

    monkeypatch.setattr("judge_jev.history.RETENTION_LIMIT", 0)
    save_result({"verdict": "pass", "rubric_id": "assistant-reply", "mock": True})
    assert outside.read_text(encoding="utf-8") == "keep"
    assert plausible.exists()
    assert arbitrary.exists()
    assert malformed.exists()
    assert list_entries() == []
    assert delete_all() == 0
    assert outside.exists() and arbitrary.exists() and plausible.exists() and malformed.exists()
