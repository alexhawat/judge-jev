"""CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from loguru import logger

from judge_jev.funnel import read_input_text, replay_judgment, run_judgment
from judge_jev.logfire_tracing import TracingConfig, configure_tracing
from judge_jev.models import RUNTIME_NAME, RUNTIME_VERSION, RubricError
from judge_jev.rubric import list_rubric_ids, show_rubric
from judge_jev.setup_cmd import run_setup
from judge_jev.typesafe_client import JudgeJevError

USAGE = """usage: judge-jev <setup|run|rubric|replay> ...
  setup
  run    --rubric <id> --input <file.json|-> [--mock] [--tracing] [--tracing-to logfire]
  replay --input <result.json|-> [--allow-version-drift]
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
    except ValueError as err:
        raise JudgeJevError(str(err)) from err
    return configure_tracing(config)


def cmd_run(args: argparse.Namespace) -> int:
    tracing_active = _tracing_active(args)
    result = run_judgment(
        args.rubric,
        Path(args.input),
        mock=args.mock,
        tracing_active=tracing_active,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return _exit_for_verdict(result.verdict)


def cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.input)
    text = read_input_text(path)
    try:
        saved = json.loads(text)
    except json.JSONDecodeError as err:
        raise JudgeJevError(f"input {path} is not valid JSON: {err}") from err
    result = replay_judgment(saved, allow_version_drift=args.allow_version_drift)
    print(json.dumps(result.to_dict(), indent=2))
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
        "run": (("--rubric", "--input"), ("--mock", "--tracing")),
        "replay": (("--input",), ("--allow-version-drift",)),
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
    else:
        return f"unknown command: {command}"

    optional_value_flags = ("--tracing-to",) if command == "run" else ()
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


def build_parser() -> argparse.ArgumentParser:
    parser = UsageParser(prog="judge-jev", description="Jev-native judge CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="Choose and install runtime")

    run_p = sub.add_parser("run", help="Run judgment funnel")
    run_p.add_argument("--rubric", required=True)
    run_p.add_argument("--input", required=True)
    run_p.add_argument("--mock", action="store_true")
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

    rubric_p = sub.add_parser("rubric", help="Rubric commands")
    rubric_sub = rubric_p.add_subparsers(dest="rubric_cmd", required=True)
    rubric_sub.add_parser("list")
    show_p = rubric_sub.add_parser("show")
    show_p.add_argument("--id", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    raw = list(sys.argv[1:] if argv is None else argv)

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
        args = parser.parse_args(raw)
    except SystemExit as err:
        # UsageParser already reported it; carry its status out as main's return
        # value so `main(argv)` is testable without catching SystemExit.
        code = err.code
        return code if isinstance(code, int) else EXIT_USAGE

    handlers = {
        "setup": lambda _a: run_setup(),
        "run": cmd_run,
        "replay": cmd_replay,
        "rubric": cmd_rubric,
    }
    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE

    try:
        return handler(args)
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
