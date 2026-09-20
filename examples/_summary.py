"""Print the JudgmentResult fields a caller acts on. Reads the JSON on stdin.

Kept in Python rather than jq because the repo already requires python3 (the parity
check and hooks/post-judge.sh use it) and jq is not a dependency anywhere.
"""

import json
import sys

result = json.load(sys.stdin)
usage = result.get("usage") or {}

print(f"  verdict           {result['verdict']}")
print(f"  confidence        {result['confidence']:.2f}  (floor {result['confidence_floor']:.2f})")
print(f"  stage             {result['stage']}")
print(f"  deciding answers  {', '.join(result['deciding_answers']) or '-'}")
print(f"  reason            {result['routing_reason']}")
print(f"  model             {result['model']}  (mock={str(result['mock']).lower()})")
print(f"  usage             input={usage.get('input_tokens')} output={usage.get('output_tokens')}")
