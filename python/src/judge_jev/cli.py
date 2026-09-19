"""CLI entrypoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

from judge_jev.funnel import replay_judgment, run_judgment
from judge_jev.rubric import list_rubric_ids, show_rubric
from judge_jev.setup_cmd import run_setup

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_REVIEW = 2
EXIT_ESCALATE = 3
EXIT_SKIP = 4


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
    saved = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = replay_judgment(saved)
    print(json.dumps(result.to_dict(), indent=2))
    return _exit_for_verdict(result.verdict)


def cmd_rubric(args: argparse.Namespace) -> int:
    if args.rubric_cmd == "list":
        for rid in list_rubric_ids():
            print(rid)
        return EXIT_OK
    if args.rubric_cmd == "show":
        if not args.id:
            print("rubric show requires --id", file=sys.stderr)
            return EXIT_FAIL
        print(show_rubric(args.id))
        return EXIT_OK
    return EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="judge-jev", description="Jev-native judge CLI")
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
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "setup":
        return run_setup()
    if args.command == "run":
        return cmd_run(args)
    if args.command == "replay":
        return cmd_replay(args)
    if args.command == "rubric":
        return cmd_rubric(args)
    parser.print_help()
    return EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
