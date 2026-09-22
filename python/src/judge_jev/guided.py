"""Beginner-facing commands layered over the machine judgment service."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from judge_jev.budget import check_budget
from judge_jev.canonical import CanonicalState, strict_json_loads
from judge_jev.funnel import (
    load_input,
    read_input_text,
    replay_judgment,
    run_judgment,
    validate_shipped_input_shape,
)
from judge_jev.history import delete_all, delete_entry, history_dir, list_entries, load_entry, save_result
from judge_jev.human import explain_result, format_explanation, format_result
from judge_jev.models import JudgeJevError
from judge_jev.paths import repo_root, rubrics_dir, runtime_config_path
from judge_jev.rubric import list_rubric_ids, load_rubric
from judge_jev.state_filter import filter_state


class GuidedUsageError(ValueError):
    pass


def emit_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


def emit_result(result: dict[str, Any], format_: str, save: bool, *, action: str = "judgment") -> None:
    history_id = save_result(result) if save else None
    if format_ == "json":
        payload = dict(result)
        if history_id:
            payload["history_id"] = history_id
        emit_json(payload)
    else:
        print(format_result(result, history_id=history_id, action=action))


def _read_text(path: str | None) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as err:
        raise JudgeJevError(f"cannot read text file {path}: {err}") from err


def _multiline(label: str) -> str:
    print(f"Enter {label}. Finish with a line containing only .done:", file=sys.stderr)
    lines: list[str] = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line == ".done":
            break
        lines.append(line)
    return "\n".join(lines)


def _split_editor_command(command: str) -> list[str]:
    """Split EDITOR with the platform's command-line quoting rules."""
    if os.name != "nt":
        return shlex.split(command)
    # Windows does not use POSIX backslash escaping. CommandLineToArgvW is the
    # native inverse of subprocess.list2cmdline and handles quoted paths.
    import ctypes  # noqa: PLC0415 - unavailable API is isolated to Windows.

    count = ctypes.c_int()
    parser = ctypes.windll.shell32.CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv_ptr = parser(command, ctypes.byref(count))
    if not argv_ptr:
        raise ValueError("Windows could not parse the editor command")
    try:
        return [argv_ptr[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(argv_ptr, ctypes.c_void_p))


def _edit_reply() -> tuple[Any, Any, Any]:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        raise GuidedUsageError("--editor needs VISUAL or EDITOR to name an editor")
    fd, raw_path = tempfile.mkstemp(prefix="judge-jev-reply-", suffix=".json")
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"prompt": "", "reply": "", "context": ""}, stream, indent=2)
            stream.write("\n")
        try:
            argv = [*_split_editor_command(editor), str(path)]
        except ValueError as err:
            raise GuidedUsageError(f"invalid editor command: {err}") from err
        if not argv:
            raise GuidedUsageError("VISUAL or EDITOR is empty")
        try:
            # Editor diagnostics must not corrupt JSON mode's one-value stdout.
            # In a real terminal, send stdout to that terminal's stderr so a
            # full-screen editor keeps terminal control. Test/captured streams do
            # not expose a file descriptor, so capture and forward in that case.
            try:
                sys.stderr.fileno()
                editor_stdout: Any = sys.stderr
            except (AttributeError, OSError):
                editor_stdout = subprocess.PIPE
            completed = subprocess.run(  # noqa: S603 - direct argv; no shell.
                argv, check=False, stdout=editor_stdout, text=True
            )
        except KeyboardInterrupt as err:
            raise GuidedUsageError("editor cancelled") from err
        except OSError as err:
            raise JudgeJevError(f"cannot start editor {argv[0]!r}: {err}") from err
        if isinstance(completed.stdout, str) and completed.stdout:
            # Keep an editor's status text visible without adding bytes before a
            # JSON result on stdout. Interactive editors continue to use the
            # controlling terminal through stdin/stderr.
            print(completed.stdout, file=sys.stderr, end="")
        if completed.returncode != 0:
            raise JudgeJevError(f"editor exited {completed.returncode}")
        try:
            payload = strict_json_loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as err:
            raise GuidedUsageError(f"editor reply document is not valid JSON: {err}") from err
        if not isinstance(payload, dict):
            raise GuidedUsageError("editor reply document must be a JSON object")
        return payload.get("prompt"), payload.get("reply"), payload.get("context")
    finally:
        path.unlink(missing_ok=True)


def _run_object(rubric_id: str, payload: dict[str, Any], *, mock: bool) -> dict[str, Any]:
    fd, raw_path = tempfile.mkstemp(prefix="judge-jev-input-", suffix=".json")
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
        return run_judgment(rubric_id, path, mock=mock).to_dict()
    finally:
        path.unlink(missing_ok=True)


def cmd_reply(args: Any) -> int:
    if args.editor:
        if any(
            value is not None
            for value in (args.prompt, args.prompt_file, args.reply, args.reply_file, args.context, args.context_file)
        ):
            raise GuidedUsageError("--editor cannot be combined with prompt, reply, or context flags")
        prompt, reply, context = _edit_reply()
    else:
        prompt = args.prompt if args.prompt is not None else _read_text(args.prompt_file)
        reply = args.reply if args.reply is not None else _read_text(args.reply_file)
        context = args.context if args.context is not None else _read_text(args.context_file)
    interactive = sys.stdin.isatty() and args.format == "human"
    if prompt is None and interactive:
        prompt = _multiline("the original prompt")
    if reply is None and interactive:
        reply = _multiline("the reply to judge")
    if not isinstance(prompt, str) or not prompt.strip():
        raise GuidedUsageError("reply needs a non-empty --prompt/--prompt-file")
    if not isinstance(reply, str) or not reply.strip():
        raise GuidedUsageError("reply needs a non-empty --reply/--reply-file")
    payload = {"prompt": prompt, "reply": reply}
    if context is not None:
        payload["context"] = context
    result = _run_object("assistant-reply", payload, mock=args.mock)
    emit_result(result, args.format, args.save)
    return verdict_exit(result["verdict"])


def cmd_trajectory(args: Any) -> int:
    input_path = args.input
    if input_path is None and sys.stdin.isatty() and args.format == "human":
        try:
            input_path = input("Trajectory JSON file (use `judge-jev input template --rubric agent-trajectory` to create one): ").strip()
        except (EOFError, KeyboardInterrupt):
            input_path = ""
    if not input_path:
        raise GuidedUsageError("trajectory needs --input <file.json|->")
    payload = load_input(Path(input_path))
    if not isinstance(payload.get("goal"), str) or not payload["goal"].strip():
        raise GuidedUsageError("trajectory input needs a non-empty goal")
    if not isinstance(payload.get("steps"), list):
        raise GuidedUsageError("trajectory input needs a steps array")
    if not isinstance(payload.get("final_output"), str) or not payload["final_output"].strip():
        raise GuidedUsageError("trajectory input needs a non-empty final_output")
    result = _run_object("agent-trajectory", payload, mock=args.mock)
    emit_result(result, args.format, args.save)
    return verdict_exit(result["verdict"])


def template_for(rubric_id: str) -> dict[str, Any]:
    if rubric_id == "assistant-reply":
        return {
            "prompt": "Required: the user request or question.",
            "reply": "Required: the assistant reply to judge.",
            "context": "Optional: trusted supporting context; omit when unavailable.",
        }
    if rubric_id == "agent-trajectory":
        return {
            "goal": "Required: the task the agent was asked to complete.",
            "steps": [{"tool": "read", "input": "path", "output": "observed result"}],
            "final_output": "Required: the agent's final response or outcome.",
        }
    raise GuidedUsageError(f"unknown rubric: {rubric_id}")


def cmd_input(args: Any) -> int:
    if args.input_cmd == "template":
        emit_json(template_for(args.rubric))
        return 0
    raw = load_input(Path(args.input))
    rubric = load_rubric(args.rubric)
    validate_shipped_input_shape(rubric.id, raw)
    if rubric.id == "assistant-reply":
        for field in ("prompt", "reply"):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                raise GuidedUsageError(
                    f"assistant-reply input needs a non-empty {field}"
                )
    elif rubric.id == "agent-trajectory":
        for field in ("goal", "final_output"):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                raise GuidedUsageError(
                    f"agent-trajectory input needs a non-empty {field}"
                )
        if not isinstance(raw.get("steps"), list):
            raise GuidedUsageError("agent-trajectory input needs a steps array")
    filtered = filter_state(raw, rubric.state_filter)
    state = CanonicalState.of(filtered)
    check_budget(state, rubric)
    report = {
        "valid": True,
        "rubric_id": rubric.id,
        "projected_keys": sorted(filtered),
        "canonical_bytes": state.size,
        "network_used": False,
    }
    if args.format == "json":
        emit_json(report)
    else:
        print(f"Valid for {rubric.id}; {len(filtered)} projected keys, {state.size} canonical bytes. No network call was made.")
    return 0


def _runtime_status() -> tuple[str, str]:
    env = os.environ.get("JUDGE_JEV_RUNTIME", "").strip()
    if env:
        return env, "JUDGE_JEV_RUNTIME environment override"
    path = runtime_config_path()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip(), str(path)
    return "python", "default (no environment override or saved preference)"


def doctor_report() -> dict[str, Any]:
    runtime, why = _runtime_status()
    smoke_error = None
    root = repo_root()
    checkout = (root / "scripts" / "setup.sh").is_file()
    claude_fixture = root / "hooks" / "claude-code" / "fixtures" / "stop-event.json"
    integration: dict[str, Any] | None = None
    try:
        if checkout and claude_fixture.is_file():
            environment = os.environ.copy()
            environment["JUDGE_JEV_RUNTIME"] = runtime
            environment.setdefault(
                "UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "judge-jev-uv-cache")
            )
            completed = subprocess.run(  # noqa: S603 - fixed repository scripts.
                [sys.executable, str(root / "hooks/claude-code/claude_code_hook.py"), "doctor", "--event", str(claude_fixture), "--judge", str(root / "scripts/judge-jev")],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                env=environment,
            )
            integration = strict_json_loads(completed.stdout)
            if completed.returncode != 0 or not isinstance(integration, dict):
                raise JudgeJevError(completed.stderr.strip() or "Claude fixture doctor failed")
            smoke_verdict = integration["result"]["verdict"]
        else:
            smoke = _run_object(
                "assistant-reply",
                {"prompt": "Say hello.", "reply": "Hello.", "context": "Offline setup smoke."},
                mock=True,
            )
            smoke_verdict = smoke["verdict"]
    except Exception as err:  # noqa: BLE001 - doctor reports, it does not hide the failure.
        smoke_verdict = None
        smoke_error = f"{type(err).__name__}: {err}"
    fixes = []
    if checkout:
        fixes.append("Run scripts/setup.sh (or scripts/setup.ps1) if a selected dependency is missing.")
    elif smoke_error is not None:
        fixes.append("Reinstall or upgrade the judge-jev Python package, then run doctor again.")
    if not os.environ.get("TYPESAFE_API_KEY"):
        fixes.append("Export TYPESAFE_API_KEY only when you are ready for live API judging.")
    return {
        "selected_runtime": runtime,
        "runtime_selected_by": why,
        "recommended_human_frontend": "python",
        "dependencies": {"uv": shutil.which("uv") is not None, "cargo": shutil.which("cargo") is not None},
        "rubrics_dir": str(rubrics_dir()),
        "rubrics": list_rubric_ids(),
        "source_checkout": checkout,
        "typesafe_api_key_present": bool(os.environ.get("TYPESAFE_API_KEY")),
        "tracing": {
            "sdk_available": importlib.util.find_spec("logfire") is not None,
            "token_present": bool(os.environ.get("JUDGE_JEV_LOGFIRE_TOKEN")),
            "network_tested": False,
        },
        "offline_smoke": {"passed": smoke_error is None, "verdict": smoke_verdict, "error": smoke_error},
        "integration_smoke": {
            "claude_code_recorded_fixture": integration is not None and smoke_error is None,
            "selected_runtime_exercised": runtime if integration is not None else "python-in-process",
            "host_response": integration.get("host_response") if integration else None,
        },
        "claude_code_fixture": str(claude_fixture) if claude_fixture.is_file() else None,
        "network_used": False,
        "fixes": fixes,
    }


def cmd_doctor(args: Any) -> int:
    report = doctor_report()
    if args.format == "json":
        emit_json(report)
    else:
        print(f"Runtime: {report['selected_runtime']} — {report['runtime_selected_by']}")
        print(f"Rubrics: {', '.join(report['rubrics'])} ({report['rubrics_dir']})")
        print(f"TypeSafe key present: {str(report['typesafe_api_key_present']).lower()} (value never displayed)")
        print(f"Offline smoke: {'passed' if report['offline_smoke']['passed'] else 'failed'}")
        print("No network check was performed.")
        for fix in report["fixes"]:
            print(f"Next: {fix}")
    return 0 if report["offline_smoke"]["passed"] else 10


def cmd_init(args: Any) -> int:
    code = cmd_doctor(args)
    if args.format == "human" and code == 0:
        print("\nReady for an offline demo:")
        print("  judge-jev reply --prompt 'What is 2+2?' --reply '4' --mock")
        print("Live judging uses the API only when you omit --mock and have set TYPESAFE_API_KEY.")
    return code


def _read_saved(args: Any) -> dict[str, Any]:
    if getattr(args, "history_id", None):
        return load_entry(args.history_id)["result"]
    if not getattr(args, "input", None):
        raise GuidedUsageError("provide --input <result.json|-> or --history-id <id>")
    try:
        saved = strict_json_loads(read_input_text(Path(args.input)))
    except (json.JSONDecodeError, ValueError) as err:
        raise JudgeJevError(f"saved result is not valid JSON: {err}") from err
    if not isinstance(saved, dict):
        raise JudgeJevError("saved result must be a JSON object")
    return saved


def cmd_explain(args: Any) -> int:
    saved = _read_saved(args)
    rubric = load_rubric(str(saved.get("rubric_id", "")))
    explanation = explain_result(saved, rubric)
    if args.format == "json":
        emit_json(explanation)
    else:
        print(format_explanation(explanation))
    return 0


def cmd_history(args: Any) -> int:
    if args.history_cmd == "list":
        entries = list_entries()
        if args.format == "json":
            emit_json({"history_dir": str(history_dir()), "retention_limit": 100, "entries": entries})
        elif not entries:
            print("No saved results. Add --save to a run, replay, reply, or trajectory command.")
        else:
            for entry in entries:
                print(f"{entry['history_id']}  {entry['verdict']}  {entry['rubric_id']}  mock={str(entry['mock']).lower()}")
        return 0
    if args.history_cmd == "show":
        payload = load_entry(args.id)
        if args.format == "json":
            emit_json(payload)
        else:
            print(
                format_result(
                    payload["result"], history_id=payload["history_id"], action="history"
                )
            )
        return 0
    if args.history_cmd == "replay":
        saved = load_entry(args.id)["result"]
        result = replay_judgment(saved, allow_version_drift=args.allow_version_drift).to_dict()
        emit_result(result, args.format, args.save, action="replay")
        return verdict_exit(result["verdict"])
    if args.history_cmd == "delete":
        if args.all:
            if not args.confirm_all:
                raise GuidedUsageError("history delete --all requires --confirm-all")
            count = delete_all()
            message = {"deleted": count}
        else:
            if not args.id:
                raise GuidedUsageError("history delete needs an id or --all --confirm-all")
            delete_entry(args.id)
            message = {"deleted": 1, "history_id": args.id}
        emit_json(message) if args.format == "json" else print(f"Deleted {message['deleted']} saved result(s).")
        return 0
    raise GuidedUsageError("history needs list, show, replay, or delete")


def verdict_exit(verdict: str) -> int:
    return {"pass": 0, "fail": 1, "review": 2, "escalate": 3, "skip": 4}.get(verdict, 10)
