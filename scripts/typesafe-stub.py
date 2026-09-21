#!/usr/bin/env python3
"""A local stand-in for `POST /v1/systemone`, used by scripts/check-parity.sh.

Two things the parity harness cannot check against the real API, and could not
check at all before this existed:

  * **What each runtime puts on the wire.** Comparing parsed `JudgmentResult`s
    proves the two agree on the verdict, not on the request that produced it. The
    stub records the exact bytes of the `state` field, so a divergence in key
    order or separators is visible instead of being normalized away by a parser.
  * **What each runtime does with a failure.** A scripted status sequence
    (`--statuses 503,503,200`) makes transient failure reproducible offline, which
    is how retry behaviour gets tested without a flaky network.

Answers are synthesized from the questions in the request, so a response is always
well-formed for the rubric that asked, and identical for both runtimes: they are
derived from the question name and type only, never from anything that could
differ between the two clients.

    python3 scripts/typesafe-stub.py --record /tmp/req.jsonl --statuses 503,503,200

Prints `PORT <n>` on stdout once it is listening, then serves until killed.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SYSTEM_ONE_PATH = "/v1/systemone"
STUB_MODEL = "jev-1.13.0"


def raw_field(body: str, key: str) -> str | None:
    """The exact source text of a top-level JSON field, as it arrived.

    Re-serializing a parsed value would hide precisely the differences this stub
    exists to expose (key order, separators, escaping), so the value is sliced out
    of the original text instead.
    """
    needle = json.dumps(key) + ":"
    start = body.find(needle)
    if start < 0:
        # Tolerate a space after the colon, which a non-compact encoder emits.
        needle = json.dumps(key) + ": "
        start = body.find(needle)
        if start < 0:
            return None
    value_at = start + len(needle)
    while value_at < len(body) and body[value_at] in " \t\n\r":
        value_at += 1
    try:
        _, end = json.JSONDecoder().raw_decode(body, value_at)
    except ValueError:
        return None
    return body[value_at:end]


def answer_for(name: str, spec: dict) -> dict:
    """A deterministic answer for one question.

    Every value is a function of the question's name and type alone. Criteria are
    read only for the labels an answer must be phrased in, and only through
    accessors that tolerate either client's serialization.
    """
    qtype = spec.get("type")
    criteria = spec.get("criteria")
    if qtype == "choice":
        labels = sorted(criteria) if isinstance(criteria, dict) else ["a", "b"]
        # A label both runtimes will route the same way; the escalate/fail rules key
        # off scores and nouls, not this.
        pick = labels[0]
        return {
            "type": "choice",
            "choice": pick,
            "confidence": 0.9,
            "probabilities": {label: (0.9 if label == pick else 0.1) for label in labels},
        }
    if qtype == "score":
        levels = criteria if isinstance(criteria, list) and criteria else ["low", "high"]
        count = len(levels)
        return {
            "type": "score",
            "score": 3.0,
            "confidence": 0.9,
            "legend": {str(i): str(level) for i, level in enumerate(levels)},
            "probabilities": {str(i): 1.0 / count for i in range(count)},
        }
    # Noul, and anything a future rubric adds. `judgeable` has to be high or every
    # stubbed run short-circuits to `skip` at the screen stage and exercises none of
    # the funnel; everything else is low, so the run lands on the ordinary pass path
    # rather than an escalate rule.
    return {"type": "noul", "noul": 0.9 if "judgeable" in name else 0.1}


class Handler(BaseHTTPRequestHandler):
    # Set by main().
    record_path: str | None = None
    statuses: list[int] = [200]
    lock = threading.Lock()
    seen = 0

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:
        """Silence the default stderr access log; the recording is the output."""

    def _status_for_this_request(self) -> tuple[int, int]:
        with Handler.lock:
            index = Handler.seen
            Handler.seen += 1
        # The last entry repeats, so `--statuses 503,503,200` means "fail twice,
        # then succeed for good".
        return Handler.statuses[min(index, len(Handler.statuses) - 1)], index

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling.
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        body_text = raw.decode("utf-8", errors="replace")
        status, index = self._status_for_this_request()

        if Handler.record_path:
            try:
                parsed = json.loads(body_text)
            except ValueError:
                parsed = None
            entry = {
                "n": index,
                "path": self.path,
                "status": status,
                "body_bytes": len(raw),
                # The exact source text of each field, not a re-encoding of it.
                "state_raw": raw_field(body_text, "state"),
                "model": (parsed or {}).get("model"),
                "question_names": sorted((parsed or {}).get("questions") or {}),
            }
            with Handler.lock, open(Handler.record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")

        if self.path.rstrip("/") != SYSTEM_ONE_PATH:
            self._respond(404, {"error": {"message": f"no such path: {self.path}"}})
            return

        if status != 200:
            self._respond(status, {"error": {"message": f"stubbed status {status}"}})
            return

        try:
            questions = json.loads(body_text).get("questions") or {}
        except ValueError:
            self._respond(400, {"error": {"message": "request body is not JSON"}})
            return

        self._respond(
            200,
            {
                "model": STUB_MODEL,
                "usage": {"input_tokens": 100, "output_tokens": 40},
                "answers": {
                    name: answer_for(name, spec if isinstance(spec, dict) else {})
                    for name, spec in questions.items()
                },
            },
        )

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-typesafe-request-id", "stub-request-id")
        # No retry-after: the SDK honours it, and a stub that sets it would be
        # testing the header path instead of the backoff path.
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--record", help="append one JSON line per request to this file")
    parser.add_argument(
        "--statuses",
        default="200",
        help="comma-separated status sequence; the last entry repeats",
    )
    args = parser.parse_args()

    Handler.record_path = args.record
    Handler.statuses = [int(s) for s in args.statuses.split(",") if s.strip()] or [200]

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"PORT {server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
