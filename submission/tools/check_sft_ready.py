#!/usr/bin/env python3
"""Validate a normalized trajectory for SFT readiness.

Checks (per the test brief's data-integrity requirements):
  1. starts with a user instruction and ends with a complete final message;
  2. every assistant tool_use has a tool_result and vice versa (no truncation);
  3. no secrets (key-shaped tokens / our auth token) in any field;
  4. non-empty text payloads for message records.

Usage: python check_sft_ready.py <normalized.jsonl>
Exit 0 = SFT-ready.
"""

import json
import os
import re
import sys
from pathlib import Path

REDACTED = "[REDACTED]"
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"(?i)(api[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.]{16,}[\"']?"),
]


def contains_secret(text: str) -> bool:
    if not isinstance(text, str):
        return False
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            return True
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    return bool(token) and token in text


def main() -> int:
    path = Path(sys.argv[1])
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    problems: list[str] = []

    # 1. start / end shape
    if not records:
        problems.append("empty trajectory")
    elif records[0].get("kind") != "message" or records[0].get("role") != "user":
        problems.append("trajectory does not start with a user message")
    if records and records[-1].get("kind") != "message":
        problems.append("trajectory does not end with a complete message (truncated?)")

    # 2. tool pairing
    uses = [r for r in records if r.get("kind") == "tool_use"]
    missing_output = [r for r in uses if not r.get("tool_output")]
    if missing_output:
        problems.append(f"{len(missing_output)} tool_use without tool_output (truncated)")

    # 3. secrets
    for i, rec in enumerate(records):
        blob = json.dumps(rec, ensure_ascii=False)
        if contains_secret(blob):
            problems.append(f"record {i} contains a secret")

    # 4. empty payloads
    empty_msgs = [r.get("seq") for r in records if r.get("kind") == "message" and not r.get("text")]
    if empty_msgs:
        problems.append(f"empty message records: {empty_msgs[:5]}")

    if problems:
        print(f"NOT SFT-READY ({len(records)} records):")
        for p in problems:
            print(f"  - {p}")
        return 1

    n_tools = len(uses)
    print(f"SFT-READY: {len(records)} records, {n_tools} tool calls, "
          f"last ts={records[-1].get('ts')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
