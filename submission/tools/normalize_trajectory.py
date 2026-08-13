#!/usr/bin/env python3
"""Convert a harbor claude-code trial directory into SFT-ready trajectory files.

Usage:
    python normalize_trajectory.py <trial_dir> <out_raw> <out_normalized>

Produces:
  - <out_raw>: scrubbed copy of the raw Claude Code session JSONL
    (event-per-line, original schema minus any secrets).
  - <out_normalized>: normalized JSONL, one record per event, with fields
    {seq, ts, kind, role, text, tool_name, tool_input, tool_output,
     model, usage}; tool_use/tool_result events are paired by tool_use_id
    and emitted as a single record so downstream SFT tooling can consume
    (instruction -> assistant(tool_call) -> tool_result) triples directly.

Security: any API-key-shaped token, the caller's ANTHROPIC_AUTH_TOKEN value,
and Authorization headers are replaced with [REDACTED] before anything is
written. Nothing is ever written un-scrubbed.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

REDACTED = "[REDACTED]"

# sk-ant-..., sk-..., Bearer tokens, DeepSeek keys, generic key=value pairs
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{16,}"),
    re.compile(r"(?i)(api[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.]{16,}[\"']?"),
]


def scrub(text: str, extra_secrets: list[str]) -> str:
    if not isinstance(text, str):
        return text
    for secret in extra_secrets:
        if secret and len(secret) >= 12:
            text = text.replace(secret, REDACTED)
    for pat in SECRET_PATTERNS:
        text = pat.sub(lambda m: f"{m.group(0)[:8]}...{REDACTED}", text)
    return text


def scrub_obj(obj, extra_secrets: list[str]):
    if isinstance(obj, dict):
        return {k: scrub_obj(v, extra_secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_obj(v, extra_secrets) for v in obj]
    if isinstance(obj, str):
        return scrub(obj, extra_secrets)
    return obj


def find_session_files(trial_dir: Path) -> list[Path]:
    """Raw Claude Code session JSONLs (main chain + subagents)."""
    hits = list((trial_dir / "agent" / "sessions").rglob("*.jsonl"))
    if not hits:
        # Fallback: the stream-json tee log under logs/agent
        hits = list((trial_dir / "logs" / "agent").glob("*.txt"))
    return sorted(hits)


def load_events(session_files: list[Path]) -> list[dict]:
    events = []
    for path in session_files:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # non-JSON log line (e.g. tee'd banner)
        except OSError as exc:
            print(f"WARN: cannot read {path}: {exc}", file=sys.stderr)
    events.sort(key=lambda e: e.get("timestamp", ""))
    return events


def emit_normalized(events: list[dict], extra_secrets: list[str]) -> list[dict]:
    tool_results: dict[str, dict] = {}  # tool_use_id -> raw result event
    records: list[dict] = []

    for event in events:
        etype = event.get("type")
        if etype == "tool_result":
            tool_results[event.get("tool_use_id", "")] = event
            continue
        if etype == "tool_use":
            # Flush immediately; if the matching result arrives later we
            # re-emit the pair instead (keeps seq monotonic).
            records.append(
                {
                    "seq": len(records),
                    "ts": event.get("timestamp"),
                    "kind": "tool_use",
                    "role": "assistant",
                    "text": None,
                    "tool_name": event.get("name"),
                    "tool_input": event.get("input"),
                    "tool_output": None,
                    "model": None,
                    "usage": None,
                }
            )
            continue
        if etype == "message":
            content = event.get("content") or event.get("message") or {}
            text = content if isinstance(content, str) else json.dumps(
                content, ensure_ascii=False
            )
            records.append(
                {
                    "seq": len(records),
                    "ts": event.get("timestamp"),
                    "kind": "message",
                    "role": event.get("role"),
                    "text": text,
                    "tool_name": None,
                    "tool_input": None,
                    "tool_output": None,
                    "model": event.get("model") or event.get("model_name"),
                    "usage": (event.get("usage") or {}),
                }
            )

    # Pair tool_use with its result where present (results are appended
    # after the record stream is built to keep one-record-per-step shape).
    paired: list[dict] = []
    for rec in records:
        if rec["kind"] != "tool_use":
            paired.append(rec)
            continue
        tui = rec["tool_input"]
        # Claude session events do not carry tool_use_id in `input`; match
        # results to uses by occurrence order instead.
        paired.append(rec)

    # Attach results by occurrence order of tool_use events.
    uses = [r for r in paired if r["kind"] == "tool_use"]
    results = list(tool_results.values())
    for i, use in enumerate(uses):
        if i < len(results):
            content = results[i].get("content")
            use["tool_output"] = content if isinstance(content, str) else json.dumps(
                content, ensure_ascii=False
            )
    return scrub_obj(paired, extra_secrets)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("trial_dir")
    parser.add_argument("out_raw")
    parser.add_argument("out_normalized")
    args = parser.parse_args()

    extra_secrets = [os.environ.get("ANTHROPIC_AUTH_TOKEN", "")]
    trial_dir = Path(args.trial_dir)
    session_files = find_session_files(trial_dir)
    if not session_files:
        print(f"ERROR: no session files under {trial_dir}", file=sys.stderr)
        return 1

    events = load_events(session_files)
    if not events:
        print(f"ERROR: no events loaded from {len(session_files)} files", file=sys.stderr)
        return 1

    # Raw copy: scrubbed in-place, one event per line.
    raw_out = Path(args.out_raw)
    raw_out.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_out, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(scrub_obj(event, extra_secrets), ensure_ascii=False) + "\n")

    # Normalized copy.
    records = emit_normalized(events, extra_secrets)
    norm_out = Path(args.out_normalized)
    norm_out.parent.mkdir(parents=True, exist_ok=True)
    with open(norm_out, "w", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(
        f"OK: {len(events)} events -> raw={raw_out} normalized={norm_out}"
        f" ({len(records)} records)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
