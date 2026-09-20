"""CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from loguru import logger

from judge_jev.funnel import replay_judgment, run_judgment
from judge_jev.models import RubricError
from judge_jev.rubric import list_rubric_ids, show_rubric
from judge_jev.setup_cmd import run_setup
from judge_jev.typesafe_client import JudgeJevError

USAGE = """usage: judge-jev <setup|run|rubric|replay> ...
  setup
  run    --rubric <id> --input <file.json> [--mock]
  replay --input <result.json>
  rubric list | show --id <id>"""

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


def cmd_run(args: argparse.Namespace) -> int:
    result = run_judgment(args.rubric, Path(args.input), mock=args.mock)
    print(json.dumps(result.to_dict(), indent=2))
    return _exit_for_verdict(result.verdict)


def cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.input)
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise JudgeJevError(f"Cannot read {path}: {err.strerror or err}") from err
    except json.JSONDecodeError as err:
        raise JudgeJevError(f"{path} is not valid JSON: {err}") from err
    result = replay_judgment(saved)
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

    command, rest = argv[0], argv[1:]
    grammar: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "setup": ((), ()),
        "run": (("--rubric", "--input"), ("--mock",)),
        "replay": (("--input",), ()),
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

    seen: set[str] = set()
    index = 0
    while index < len(rest):
        arg = rest[index]
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

    replay_p = sub.add_parser("replay", help="Re-route from saved JudgmentResult JSON")
    replay_p.add_argument("--input", required=True)

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
