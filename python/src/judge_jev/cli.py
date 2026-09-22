"""CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

from loguru import logger

from judge_jev.funnel import read_input_text, replay_judgment, run_judgment
from judge_jev.guided import (
    GuidedUsageError,
    cmd_doctor,
    cmd_explain,
    cmd_history,
    cmd_init,
    cmd_input,
    cmd_reply,
    cmd_trajectory,
    emit_result,
)
from judge_jev.logfire_tracing import TracingConfig, configure_tracing
from judge_jev.models import RUNTIME_NAME, RUNTIME_VERSION, RubricError
from judge_jev.rubric import list_rubric_ids, show_rubric
from judge_jev.setup_cmd import run_setup
from judge_jev.typesafe_client import JudgeJevError

USAGE = """usage: judge-jev <command> ...
  setup
  init | doctor
  reply  [--prompt TEXT|--prompt-file FILE] [--reply TEXT|--reply-file FILE] [--mock]
  trajectory --input <file.json|-> [--mock]
  input template --rubric <id> | input validate --rubric <id> --input <file.json|->
  run    --rubric <id> --input <file.json|-> [--mock] [--format json|human] [--save]
  replay --input <result.json|-> [--allow-version-drift] [--format json|human] [--save]
  explain --input <result.json|-> | --history-id <id>
  history list | show | replay | delete
  rubric list | show --id <id>
  --version"""

VERSION_LINE = f"judge-jev {RUNTIME_VERSION} ({RUNTIME_NAME})"

# Verdict codes. These are a contract: hooks and CI branch on them.
EXIT_OK = 0
EXIT_FAIL = 1
EXIT_REVIEW = 2
EXIT_ESCALATE = 3
EXIT_SKIP = 4

# Operational failure: the judgment did not happen. Kept well clear of the verdict
# codes so a crash can never be mistaken for a verdict of 'fail'.
EXIT_ERROR = 10
EXIT_USAGE = 11


def _exit_for_verdict(verdict: str) -> int:
    return {
        "pass": EXIT_OK,
        "fail": EXIT_FAIL,
        "review": EXIT_REVIEW,
        "escalate": EXIT_ESCALATE,
        "skip": EXIT_SKIP,
    }.get(verdict, EXIT_REVIEW)


def _tracing_active(args: argparse.Namespace) -> bool:
    if not getattr(args, "tracing", False):
        return False
    try:
        config = TracingConfig.from_cli(tracing=True, tracing_to=getattr(args, "tracing_to", None))
        return configure_tracing(config)
    except ValueError as err:
        raise JudgeJevError(str(err)) from err


def cmd_run(args: argparse.Namespace) -> int:
    tracing_active = _tracing_active(args)
    result = run_judgment(
        args.rubric,
        Path(args.input),
        mock=args.mock,
        tracing_active=tracing_active,
    )
    emit_result(result.to_dict(), args.format, args.save)
    return _exit_for_verdict(result.verdict)


def cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.input)
    text = read_input_text(path)
    try:
        saved = json.loads(text)
    except json.JSONDecodeError as err:
        raise JudgeJevError(f"input {path} is not valid JSON: {err}") from err
    result = replay_judgment(saved, allow_version_drift=args.allow_version_drift)
    emit_result(result.to_dict(), args.format, args.save, action="replay")
    return _exit_for_verdict(result.verdict)


def cmd_rubric(args: argparse.Namespace) -> int:
    if args.rubric_cmd == "list":
        for rid in list_rubric_ids():
            print(rid)
        return EXIT_OK
    if args.rubric_cmd == "show":
        print(show_rubric(args.id))
        return EXIT_OK
    return EXIT_USAGE


class UsageParser(argparse.ArgumentParser):
    """An ArgumentParser whose every error path exits 11.

    argparse exits 2 of its own accord, and 2 is EXIT_REVIEW. A hook that reads the
    exit code -- which is the whole point of the code contract -- would file an
    action nobody judged into the human review queue on a typo like `--mok`. Only a
    successful, deliberate exit (`--help`) is allowed to keep its own status.
    """

    def error(self, message: str) -> NoReturn:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"judge-jev: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:  # type: ignore[override]
        if message:
            stream = sys.stdout if status == 0 else sys.stderr
            print(message, end="", file=stream)
        raise SystemExit(EXIT_OK if status == 0 else EXIT_USAGE)


def _precheck(argv: list[str]) -> str | None:
    """Validate argv the way the Rust runtime does, returning an error message.

    argparse and the Rust parser disagree on wording (and argparse accepts a
    repeated flag, silently keeping the last), so the cases a caller actually
    fat-fingers are checked here first and worded once. Both runtimes emit these
    strings verbatim; scripts/check-parity.sh compares them.
    """
    if not argv:
        return "a command is required"

    # A lone --version is a deliberate, successful exit, not a command.
    if argv == ["--version"]:
        return None

    command, rest = argv[0], argv[1:]
    grammar: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "setup": ((), ()),
        "run": (("--rubric", "--input"), ("--mock", "--tracing", "--save")),
        "replay": (("--input",), ("--allow-version-drift", "--save")),
    }

    if command == "rubric":
        sub = rest[0] if rest else None
        if sub is None:
            return "rubric needs a subcommand: list or show"
        if sub == "list":
            value_flags, bool_flags, rest = (), (), rest[1:]
        elif sub == "show":
            value_flags, bool_flags, rest = ("--id",), (), rest[1:]
        else:
            return f"unknown rubric command: {sub}"
    elif command in grammar:
        value_flags, bool_flags = grammar[command]
    elif command in {"init", "doctor", "reply", "trajectory", "input", "explain", "history"}:
        # argparse owns the richer human-command grammar. The shared precheck above
        # remains intentionally small because it is mirrored byte-for-byte in Rust.
        return None
    else:
        return f"unknown command: {command}"

    optional_value_flags = ("--tracing-to", "--format") if command == "run" else (("--format",) if command == "replay" else ())
    seen: set[str] = set()
    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg in optional_value_flags:
            if arg in seen:
                return f"{arg} given more than once"
            seen.add(arg)
            following = rest[index + 1] if index + 1 < len(rest) else None
            if following is None or (following.startswith("-") and following != "-"):
                return f"{arg} needs a value"
            index += 2
            continue
        if arg in value_flags:
            if arg in seen:
                return f"{arg} given more than once"
            seen.add(arg)
            following = rest[index + 1] if index + 1 < len(rest) else None
            # `-` is a value (stdin), not the start of another flag.
            if following is None or (following.startswith("-") and following != "-"):
                return f"{arg} needs a value"
            index += 2
            continue
        if arg in bool_flags:
            if arg in seen:
                return f"{arg} given more than once"
            seen.add(arg)
            index += 1
            continue
        if arg.startswith("-") and arg != "-":
            return f"unrecognized flag: {arg}"
        return f"unexpected argument: {arg}"

    missing = [flag for flag in value_flags if flag not in seen]
    if missing:
        return f"{missing[0]} is required"
    return None


def _format_arg(parser: argparse.ArgumentParser, *, default: str) -> None:
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default=default,
        help=f"output format (default: {default})",
    )


def _save_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--save",
        action="store_true",
        help="save the result only to private local history (raw inputs are never saved)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = UsageParser(
        prog="judge-jev",
        description="Judge replies and agent trajectories with explicit, inspectable rubrics.",
        epilog=(
            "Exit codes: 0 pass, 1 fail, 2 review, 3 escalate, 4 skip, "
            "10 operational/no judgment, 11 usage. Try an offline demo with: "
            "judge-jev reply --prompt 'What is 2+2?' --reply '4' --mock"
        ),
    )
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--verbose", action="store_true", help="show runtime progress on stderr")
    verbosity.add_argument("--debug", action="store_true", help="show debug diagnostics on stderr")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="choose and install a runtime")
    init_p = sub.add_parser("init", help="inspect setup and print the easiest first command")
    _format_arg(init_p, default="human")
    doctor_p = sub.add_parser("doctor", help="check runtime, rubrics, integrations, and an offline smoke test")
    _format_arg(doctor_p, default="human")

    run_p = sub.add_parser("run", help="Run judgment funnel")
    run_p.add_argument("--rubric", required=True)
    run_p.add_argument("--input", required=True)
    run_p.add_argument("--mock", action="store_true")
    _format_arg(run_p, default="json")
    _save_arg(run_p)
    run_p.add_argument(
        "--tracing",
        action="store_true",
        help="Enable optional Logfire tracing (Python runtime only; requires [tracing] extra and JUDGE_JEV_LOGFIRE_TOKEN)",
    )
    run_p.add_argument(
        "--tracing-to",
        default=None,
        help="Tracing sink (default: logfire when --tracing is set)",
    )

    replay_p = sub.add_parser("replay", help="Re-route from saved JudgmentResult JSON")
    replay_p.add_argument("--input", required=True)
    replay_p.add_argument(
        "--allow-version-drift",
        action="store_true",
        help="Route saved answers against a rubric version they were not judged under",
    )
    _format_arg(replay_p, default="json")
    _save_arg(replay_p)

    reply_p = sub.add_parser("reply", help="judge an assistant reply")
    reply_prompt = reply_p.add_mutually_exclusive_group()
    reply_prompt.add_argument("--prompt")
    reply_prompt.add_argument("--prompt-file")
    reply_text = reply_p.add_mutually_exclusive_group()
    reply_text.add_argument("--reply")
    reply_text.add_argument("--reply-file")
    reply_context = reply_p.add_mutually_exclusive_group()
    reply_context.add_argument("--context")
    reply_context.add_argument("--context-file")
    reply_p.add_argument(
        "--editor",
        action="store_true",
        help="edit prompt/reply/context as JSON using VISUAL or EDITOR (executed without a shell)",
    )
    reply_p.add_argument("--mock", action="store_true", help="use deterministic canned answers; no API call")
    _format_arg(reply_p, default="human")
    _save_arg(reply_p)

    trajectory_p = sub.add_parser("trajectory", help="judge an agent trajectory JSON document")
    trajectory_p.add_argument("--input", help="JSON file or - for stdin")
    trajectory_p.add_argument("--mock", action="store_true", help="use deterministic canned answers; no API call")
    _format_arg(trajectory_p, default="human")
    _save_arg(trajectory_p)

    input_p = sub.add_parser("input", help="create or validate input without calling the API")
    input_sub = input_p.add_subparsers(dest="input_cmd", required=True)
    template_p = input_sub.add_parser("template", help="print a starter JSON document")
    template_p.add_argument("--rubric", required=True, choices=("assistant-reply", "agent-trajectory"))
    validate_p = input_sub.add_parser("validate", help="validate and project a JSON input offline")
    validate_p.add_argument("--rubric", required=True)
    validate_p.add_argument("--input", required=True, help="JSON file or - for stdin")
    _format_arg(validate_p, default="human")

    explain_p = sub.add_parser("explain", help="explain routing from a saved result without an API call")
    explain_source = explain_p.add_mutually_exclusive_group(required=True)
    explain_source.add_argument("--input", help="saved result JSON file or - for stdin")
    explain_source.add_argument("--history-id")
    _format_arg(explain_p, default="human")

    history_p = sub.add_parser("history", help="manage opt-in, result-only local history")
    history_sub = history_p.add_subparsers(dest="history_cmd", required=True)
    history_list = history_sub.add_parser("list", help="list saved results")
    _format_arg(history_list, default="human")
    history_show = history_sub.add_parser("show", help="show one saved result")
    history_show.add_argument("--id", required=True)
    _format_arg(history_show, default="human")
    history_replay = history_sub.add_parser("replay", help="re-route one saved result offline")
    history_replay.add_argument("--id", required=True)
    history_replay.add_argument("--allow-version-drift", action="store_true")
    _format_arg(history_replay, default="human")
    _save_arg(history_replay)
    history_delete = history_sub.add_parser("delete", help="delete one saved result, or all with explicit confirmation")
    history_delete.add_argument("--id")
    history_delete.add_argument("--all", action="store_true")
    history_delete.add_argument("--confirm-all", action="store_true")
    _format_arg(history_delete, default="human")

    rubric_p = sub.add_parser("rubric", help="Rubric commands")
    rubric_sub = rubric_p.add_subparsers(dest="rubric_cmd", required=True)
    rubric_sub.add_parser("list")
    show_p = rubric_sub.add_parser("show")
    show_p.add_argument("--id", required=True)

    return parser


def _interactive_menu() -> list[str] | None:
    print("Judge Jev\n")
    print("  1. Judge a reply with the live API (needs TYPESAFE_API_KEY)")
    print("  2. Try a reply with canned demo answers (offline)")
    print("  3. Judge a trajectory with the live API (needs TYPESAFE_API_KEY)")
    print("  4. Try a trajectory with canned demo answers (offline)")
    print("  5. Check setup")
    print("  6. Show help")
    print("  q. Quit")
    try:
        choice = input("\nChoose: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return None
    selected = {
        "1": ["reply"],
        "2": ["reply", "--mock"],
        "3": ["trajectory"],
        "4": ["trajectory", "--mock"],
        "5": ["doctor"],
        "6": ["--help"],
        "q": [],
        "quit": [],
    }.get(choice, ["--help"])
    if selected in (["reply"], ["trajectory"]) and not os.environ.get("TYPESAFE_API_KEY"):
        print("\nLive judging needs TYPESAFE_API_KEY. Set it in your environment, then choose this option again.")
        print("The key value is never printed or saved. Choose an offline demo to try the workflow now.")
        return None
    return selected


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    verbose = raw.count("--verbose")
    debug = raw.count("--debug")
    if verbose + debug > 1:
        print("judge-jev: choose at most one of --verbose or --debug", file=sys.stderr)
        return EXIT_USAGE
    raw = [arg for arg in raw if arg not in {"--verbose", "--debug"}]

    if not raw and sys.stdin.isatty() and sys.stdout.isatty():
        selected = _interactive_menu()
        if selected is None or not selected:
            return EXIT_OK
        raw = selected

    logger.remove()
    human_command = bool(raw) and (
        raw[0] in {"init", "doctor", "reply", "trajectory", "input", "explain", "history"}
        or ("--format" in raw and "human" in raw)
    )
    level = "DEBUG" if debug else ("INFO" if verbose or not human_command else "ERROR")
    logger.add(sys.stderr, level=level)

    # Printing the version is a deliberate, successful exit. It is answered before
    # the parser so `judge-jev --version` needs no subcommand, matching the Rust
    # runtime and the convention every CLI follows.
    if raw == ["--version"]:
        print(VERSION_LINE)
        return EXIT_OK

    # `--help` is a successful, deliberate exit and keeps status 0; everything else
    # malformed is a usage error, which is 11.
    if not any(flag in raw for flag in ("-h", "--help")):
        message = _precheck(raw)
        if message is not None:
            print(f"judge-jev: {message}", file=sys.stderr)
            print(USAGE, file=sys.stderr)
            return EXIT_USAGE

    parser = build_parser()
    try:
        parser_raw = (["--debug"] if debug else ["--verbose"] if verbose else []) + raw
        args = parser.parse_args(parser_raw)
    except SystemExit as err:
        # UsageParser already reported it; carry its status out as main's return
        # value so `main(argv)` is testable without catching SystemExit.
        code = err.code
        return code if isinstance(code, int) else EXIT_USAGE

    handlers = {
        "setup": lambda _a: run_setup(),
        "init": cmd_init,
        "doctor": cmd_doctor,
        "run": cmd_run,
        "replay": cmd_replay,
        "reply": cmd_reply,
        "trajectory": cmd_trajectory,
        "input": cmd_input,
        "explain": cmd_explain,
        "history": cmd_history,
        "rubric": cmd_rubric,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE

    try:
        return handler(args)
    except GuidedUsageError as err:
        print(f"judge-jev: {err}", file=sys.stderr)
        return EXIT_USAGE
    except (JudgeJevError, RubricError, FileNotFoundError) as err:
        # Expected operational failures: report them plainly, no traceback.
        print(f"judge-jev: {err}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as err:  # noqa: BLE001 - last resort so a crash never looks like a verdict.
        logger.opt(exception=True).debug("unhandled error")
        print(f"judge-jev: unexpected error: {type(err).__name__}: {err}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
