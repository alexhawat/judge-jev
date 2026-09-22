#!/usr/bin/env python3
"""Claude Code Stop integration and settings installer for judge-jev.

The hook is deliberately stateless: each Stop event names its own transcript and
is normalized in one process. Concurrent sessions therefore cannot overwrite one
another, and a supplied session id is never used to form a filesystem path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

OWNER = "judge-jev/claude-stop/v1"
SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
VERDICT_CODES = {0: "pass", 1: "fail", 2: "review", 3: "escalate", 4: "skip"}


class HookError(RuntimeError):
    pass


def _text_blocks(content: Any) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    texts: list[str] = []
    uses: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif kind == "tool_use":
                uses.append(block)
            elif kind == "tool_result":
                results.append(block)
    return texts, uses, results


def _result_text(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts, _, _ = _text_blocks(value)
        return "\n".join(texts) if texts else value
    return value


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    if event.get("hook_event_name") != "Stop":
        raise HookError("expected a Claude Code Stop event")
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not SESSION_RE.fullmatch(session_id) or ".." in session_id:
        raise HookError("session_id must contain only letters, numbers, '.', '_' or '-' and no '..'")
    final_output = event.get("last_assistant_message")
    if not isinstance(final_output, str):
        raise HookError("Stop event is missing last_assistant_message")

    raw_path = event.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise HookError("Stop event is missing transcript_path")
    transcript = Path(raw_path).expanduser()
    if not transcript.is_absolute():
        raise HookError("transcript_path must be absolute")
    try:
        lines = transcript.read_text(encoding="utf-8").splitlines()
    except OSError as err:
        raise HookError(f"cannot read transcript_path: {err}") from err

    goal = ""
    steps: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as err:
            raise HookError(f"transcript line {number} is not valid JSON: {err.msg}") from err
        if not isinstance(record, dict):
            continue
        message = record.get("message") if isinstance(record.get("message"), dict) else record
        role = message.get("role") or record.get("type")
        texts, uses, results = _text_blocks(message.get("content"))
        if role == "user" and texts:
            # Stop judges the just-completed user turn. Reset at a new real prompt;
            # tool_result-only user records carry no text block and do not reset it.
            goal = "\n".join(texts).strip()
            steps = []
            by_id = {}
        for use in uses:
            step = {
                "tool": str(use.get("name", "unknown")),
                "input": use.get("input", {}),
            }
            tool_id = use.get("id")
            if isinstance(tool_id, str):
                step["tool_use_id"] = tool_id
                by_id[tool_id] = step
            steps.append(step)
        for result in results:
            tool_id = result.get("tool_use_id")
            if isinstance(tool_id, str) and tool_id in by_id:
                by_id[tool_id]["output"] = _result_text(result.get("content"))
                if result.get("is_error") is True:
                    by_id[tool_id]["error"] = True

    if not goal:
        raise HookError("transcript contains no user goal")
    return {"goal": goal, "steps": steps, "final_output": final_output}


def run_judge(judge: Path, normalized: dict[str, Any], *, mock: bool = False) -> tuple[int, dict[str, Any] | None, str]:
    judge = judge.expanduser().resolve()
    if not judge.is_file():
        raise HookError(f"judge executable does not exist: {judge}")
    command = [str(judge), "run", "--rubric", "agent-trajectory", "--input", "-"]
    if mock:
        command.append("--mock")
    completed = subprocess.run(
        command,
        input=json.dumps(normalized, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    result: dict[str, Any] | None = None
    if completed.stdout.strip():
        try:
            parsed = json.loads(completed.stdout)
            if isinstance(parsed, dict):
                result = parsed
        except json.JSONDecodeError:
            pass
    return completed.returncode, result, completed.stderr.strip()


def host_response(code: int, result: dict[str, Any] | None, *, stop_hook_active: bool, failure_policy: str) -> dict[str, Any]:
    if stop_hook_active:
        return {}
    expected = VERDICT_CODES.get(code)
    valid_verdict = isinstance(result, dict) and result.get("verdict") == expected
    if valid_verdict and code in (0, 4):
        return {}
    if valid_verdict and code in (1, 2, 3):
        verdict = expected.upper()
        reason = str(result.get("routing_reason") or "judge-jev requested another checkpoint")
        return {
            "decision": "block",
            "reason": f"judge-jev {verdict}: {reason} Review the completed work, address the finding, then finish again.",
        }
    if failure_policy == "closed":
        return {
            "decision": "block",
            "reason": "judge-jev could not evaluate this checkpoint. Resolve the integration error or ask the user to review before finishing.",
        }
    return {}


def handle(event: dict[str, Any], judge: Path, *, mock: bool = False, failure_policy: str = "open") -> tuple[dict[str, Any], dict[str, Any] | None]:
    if event.get("stop_hook_active") is True:
        return {}, None
    normalized = normalize_event(event)
    code, result, stderr = run_judge(judge, normalized, mock=mock)
    response = host_response(code, result, stop_hook_active=False, failure_policy=failure_policy)
    if code not in VERDICT_CODES and stderr:
        print(f"judge-jev Stop hook: {stderr}", file=sys.stderr)
    return response, result


def owned_handler(judge: Path) -> dict[str, Any]:
    return {
        "type": "command",
        "command": str(Path(sys.executable).resolve()),
        "args": [str(Path(__file__).resolve()), "hook", "--judge", str(judge.resolve()), "--owner", OWNER],
        "timeout": 120,
    }


def _is_owned(handler: Any) -> bool:
    return isinstance(handler, dict) and OWNER in (handler.get("args") or [])


def merge_settings(settings: dict[str, Any], handler: dict[str, Any], *, uninstall: bool) -> dict[str, Any]:
    merged = json.loads(json.dumps(settings))
    hooks = merged.setdefault("hooks", {})
    stop_groups = hooks.setdefault("Stop", [])
    if not isinstance(stop_groups, list):
        raise HookError("settings hooks.Stop must be an array")

    cleaned: list[Any] = []
    for group in stop_groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            cleaned.append(group)
            continue
        remaining = [item for item in group["hooks"] if not _is_owned(item)]
        if remaining:
            copy = dict(group)
            copy["hooks"] = remaining
            cleaned.append(copy)
    if not uninstall:
        cleaned.append({"hooks": [handler]})
    if cleaned:
        hooks["Stop"] = cleaned
    else:
        hooks.pop("Stop", None)
        if not hooks:
            merged.pop("hooks", None)
    return merged


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise HookError(f"cannot read settings {path}: {err}") from err
    if not isinstance(value, dict):
        raise HookError("settings root must be a JSON object")
    return value


def write_settings(path: Path, value: dict[str, Any]) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if path.exists():
        backup_fd, backup_name = tempfile.mkstemp(
            prefix=f"{path.name}.judge-jev.", suffix=".bak", dir=path.parent
        )
        os.close(backup_fd)
        backup = Path(backup_name)
        shutil.copy2(path, backup)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return backup


def settings_command(args: argparse.Namespace, *, uninstall: bool) -> int:
    settings_path = Path(args.settings).expanduser()
    current = _read_settings(settings_path)
    judge = Path(args.judge).expanduser().resolve()
    updated = merge_settings(current, owned_handler(judge), uninstall=uninstall)
    preview = {"settings": str(settings_path), "action": "uninstall" if uninstall else "install", "before": current, "after": updated}
    if args.dry_run:
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        return 0
    backup = write_settings(settings_path, updated) if current != updated else None
    print(json.dumps({"settings": str(settings_path), "backup": str(backup) if backup else None, "changed": current != updated}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claude Code Stop integration for judge-jev")
    sub = parser.add_subparsers(dest="command", required=True)
    hook = sub.add_parser("hook", help="read one Stop event from stdin")
    hook.add_argument("--judge", required=True)
    hook.add_argument("--owner", default=OWNER, help=argparse.SUPPRESS)
    hook.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)
    hook.add_argument("--failure-policy", choices=("open", "closed"), default=os.environ.get("JUDGE_JEV_HOOK_FAILURE", "open"))
    for name in ("install", "uninstall", "preview"):
        command = sub.add_parser(name)
        command.add_argument("--settings", default="~/.claude/settings.json")
        command.add_argument("--judge", required=True)
        command.add_argument("--dry-run", action="store_true", default=name == "preview")
    doctor = sub.add_parser("doctor", help="normalize a recorded Stop event and run the offline judge")
    doctor.add_argument("--event", required=True)
    doctor.add_argument("--judge", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in ("install", "preview"):
            return settings_command(args, uninstall=False)
        if args.command == "uninstall":
            return settings_command(args, uninstall=True)
        if args.command == "doctor":
            event = json.loads(Path(args.event).read_text(encoding="utf-8"))
            if event.get("transcript_path") == "TRANSCRIPT_PATH_REPLACED_BY_TEST_OR_DOCTOR":
                event["transcript_path"] = str((Path(args.event).resolve().parent / "transcript-stop.jsonl"))
            normalized = normalize_event(event)
            code, result, stderr = run_judge(Path(args.judge), normalized, mock=True)
            print(json.dumps({"normalized": normalized, "judge_exit": code, "result": result, "stderr": stderr}, ensure_ascii=False))
            return 0 if code in VERDICT_CODES and result is not None else 1
        event = json.load(sys.stdin)
        response, _ = handle(event, Path(args.judge), mock=args.mock, failure_policy=args.failure_policy)
        print(json.dumps(response, ensure_ascii=False))
        return 0
    except (HookError, json.JSONDecodeError, OSError, subprocess.SubprocessError) as err:
        print(f"judge-jev Stop hook: {err}", file=sys.stderr)
        if args.command == "hook" and getattr(args, "failure_policy", "open") == "closed":
            print(json.dumps(host_response(10, None, stop_hook_active=False, failure_policy="closed")))
            return 0
        return 0 if args.command == "hook" else 1


if __name__ == "__main__":
    raise SystemExit(main())
