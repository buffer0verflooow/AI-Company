#!/usr/bin/env python3
"""content-job 状态机工具 (human-in-the-loop transitions).

Extends content_hermes_executor.py's terminal states with the post-worker
lifecycle stages. The executor writes events for pending/running/qa and ends
in state=review (worker completed). This tool advances the job through the
human-in-the-loop stages and records every transition in events.jsonl.

States: pending → running → qa → review → published / archived
                    ↘ retrying / terminated (failure paths)

Usage:
  python3 content_job_state.py <job_dir> show
  python3 content_job_state.py <job_dir> review [detail]
  python3 content_job_state.py <job_dir> publish [detail]   # draft pushed
  python3 content_job_state.py <job_dir> archive [detail]   # closed w/o publish
  python3 content_job_state.py <job_dir> retry [reason]     # re-run planned
  python3 content_job_state.py <job_dir> terminate [reason] # no re-run

Every transition appends one line to <job_dir>/events.jsonl (append-only)
and updates a machine-readable <job_dir>/lifecycle.json:
  {"state": "...", "history": [{"state","ts","event","detail"}, ...]}
"""
from __future__ import annotations

import argparse
import json
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ._safe_io import atomic_write_text, locked_append_text, read_text_limited
except ImportError:  # direct script execution
    from _safe_io import atomic_write_text, locked_append_text, read_text_limited

VALID_STATES = ("pending", "running", "qa", "review", "published", "archived",
                "retrying", "terminated")

# allowed transitions: current -> {next states}
TRANSITIONS: dict[str, set] = {
    "pending": {"running", "terminated"},
    "running": {"qa", "review", "retrying", "terminated"},
    "qa": {"review", "retrying", "terminated"},
    "review": {"published", "archived", "retrying"},
    "retrying": {"running", "terminated"},
    "published": {"archived"},
    "archived": set(),
    "terminated": set(),
}

# CLI action -> state name mapping (actions are verbs, states are nouns)
ACTION_TO_STATE = {
    "review": "review",
    "publish": "published",
    "archive": "archived",
    "retry": "retrying",
    "terminate": "terminated",
}

# state -> event name for the log
STATE_EVENT = {
    "review": "to_review",
    "published": "published",
    "archived": "archived",
    "retrying": "retrying",
    "terminated": "terminated",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_lifecycle(job_dir: Path) -> dict[str, Any]:
    path = job_dir / "lifecycle.json"
    try:
        data = json.loads(read_text_limited(path, max_bytes=1024 * 1024))
        if isinstance(data, dict) and data.get("state") in VALID_STATES:
            return data
    except (OSError, ValueError):
        pass
    return {"state": "pending", "history": []}


def write_lifecycle(job_dir: Path, data: dict[str, Any]) -> None:
    atomic_write_text(
        job_dir / "lifecycle.json",
        json.dumps(data, ensure_ascii=False, indent=2),
    )


def log_event(job_dir: Path, state: str, event: str, detail: str) -> None:
    record = {"ts": utc_now(), "state": state, "event": event}
    if detail:
        record["detail"] = detail
    with suppress(OSError):
        locked_append_text(job_dir / "events.jsonl", json.dumps(record, ensure_ascii=False))


def transition(job_dir: Path, target: str, detail: str = "") -> int:
    lc = read_lifecycle(job_dir)
    current = lc["state"]
    allowed = TRANSITIONS.get(current, set())
    if target not in allowed:
        print(f"ERROR: {current} → {target} not allowed (allowed: {sorted(allowed) or 'none'})")
        return 1

    lc["state"] = target
    lc.setdefault("history", []).append({
        "state": target,
        "ts": utc_now(),
        "event": STATE_EVENT.get(target, target),
        "detail": detail or "",
    })
    write_lifecycle(job_dir, lc)
    log_event(job_dir, target, STATE_EVENT.get(target, target), detail)
    print(f"OK: {current} → {target}" + (f" ({detail})" if detail else ""))
    return 0


def show(job_dir: Path) -> int:
    lc = read_lifecycle(job_dir)
    print(f"state: {lc['state']}")
    print(f"history ({len(lc['history'])}):")
    for h in lc["history"]:
        d = f" — {h['detail']}" if h.get("detail") else ""
        print(f"  {h['ts']}  {h['state']:<10} {h['event']}{d}")
    ev_path = job_dir / "events.jsonl"
    if ev_path.exists():
        try:
            with open(ev_path, encoding="utf-8", errors="replace") as stream:
                n = sum(1 for _ in stream)
        except OSError:
            n = 0
        print(f"events.jsonl lines: {n}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="content-job lifecycle state machine")
    parser.add_argument("job_dir", help="path to the content job directory")
    parser.add_argument("action", choices=["show", "review", "publish", "archive", "retry", "terminate"])
    parser.add_argument("detail", nargs="?", default="", help="optional detail / reason")
    args = parser.parse_args()

    job_dir = Path(args.job_dir).resolve()
    if not job_dir.is_dir():
        print(f"ERROR: not a directory: {job_dir}")
        return 1

    if args.action == "show":
        return show(job_dir)
    target = ACTION_TO_STATE[args.action]
    return transition(job_dir, target, args.detail)


if __name__ == "__main__":
    raise SystemExit(main())
