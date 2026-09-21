"""The one serialization both runtimes build a request from.

`shared/rubrics/` is only shared if the two runtimes send the same thing. They did
not: this runtime's `filter_state` returns a dict in `state_filter` order while the
Rust runtime's `serde_json::Map` is a `BTreeMap`, so the same fixture went over the
wire as `{"prompt":…,"reply":…,"context":…}` here and `{"context":…,"prompt":…,
"reply":…}` there — and nested objects diverged the same way, `{tool,input,output}`
against `{input,output,tool}`. Nothing caught it, because the parity harness
compared parsed `JudgmentResult`s: two different requests that happen to produce
the same verdict look identical to a JSON parser.

So the canonical form is defined here, in one place, and both runtimes build every
request through it:

  * **Object keys sorted**, recursively, by Unicode code point. Sorting is the only
    ordering rule both runtimes can implement, because this build of `serde_json`
    has no `preserve_order` feature and cannot keep the order a document was
    written in. It applies at every depth, since that is where the divergence was.
  * **No insignificant whitespace**: `,` and `:` separate, nothing pads.
  * **Non-ASCII characters emitted literally.** Escaping them would cost bytes, and
    bytes are tokens.
  * **Numbers**: integers as digits; other numbers with the shortest digit string
    that round-trips, written plainly when the decimal point lands in
    `-4 < decpt <= 16` and as `d[.ddd]e±XX` (exponent signed, at least two digits)
    otherwise. That is `repr()` for a Python float, which is why this module can
    lean on `json.dumps`; `rust/src/canonical.rs` implements the same rule by hand,
    because `serde_json` writes `1e-5` as `0.00001` and `1e-7` as `1e-7`.
  * **NaN and Infinity are rejected.** JSON has no spelling for them, and
    `json.loads` accepts them where the Rust parser does not.

The canonical text is also what goes over the wire: `state` is sent as the text
itself, not as an object for each runtime's HTTP client to re-encode however it
likes. The API documents `state` as text, a JSON object, or an array, and sending
the text is the only way byte-identity survives two encoders neither runtime owns.
It is likewise the string the token estimate is measured over, so the number in the
log is the number of bytes that were actually sent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize *value* in the canonical form described above.

    Raises ValueError on a non-finite number, which JSON cannot represent and the
    Rust runtime's parser rejects outright.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_size(value: Any) -> int:
    """Byte length of the canonical form.

    Bytes, not characters: `len()` counts characters here and bytes in Rust, and
    the two runtimes have to agree on the number.
    """
    return len(canonical_json(value).encode("utf-8"))


@dataclass(frozen=True)
class CanonicalState:
    """Filtered state, alongside the exact bytes it will be sent as.

    Both halves are needed downstream: the live client sends `text`, while the mock
    engine reads `value` to decide what a question would be answered. Keeping them
    together means they cannot drift out of step.
    """

    value: dict[str, Any]
    text: str

    @classmethod
    def of(cls, value: dict[str, Any]) -> CanonicalState:
        return cls(value=value, text=canonical_json(value))

    @property
    def size(self) -> int:
        return len(self.text.encode("utf-8"))
