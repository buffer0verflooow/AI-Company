#!/usr/bin/env python3
"""Read measured usage directly from Codex native session JSONL files."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from ._safe_io import stream_contains
except ImportError:  # direct script/module execution from automation/
    from _safe_io import stream_contains


DEFAULT_CODEX_SESSIONS = Path.home() / ".codex/sessions"


def _session_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    return root.glob("*/*/*/rollout-*.jsonl")


def _counter(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def read_codex_session(path: Path) -> dict[str, Any]:
    """Return final measured counters and metadata for one Codex session."""
    result: dict[str, Any] = {
        "id": "",
        "model": "",
        "model_provider": "",
        "cwd": "",
        "started_at": "",
        "completed_at": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "tool_call_count": 0,
        "source_path": str(path),
    }
    final_usage: dict[str, Any] = {}
    with path.open(encoding="utf-8", errors="replace") as stream:
        for raw in stream:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            timestamp = str(event.get("timestamp") or "")
            if timestamp:
                result["completed_at"] = timestamp
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if event.get("type") == "session_meta":
                result["id"] = str(payload.get("session_id") or payload.get("id") or "")
                result["model_provider"] = str(payload.get("model_provider") or "")
                result["cwd"] = str(payload.get("cwd") or "")
                result["started_at"] = str(payload.get("timestamp") or timestamp)
            elif event.get("type") == "turn_context":
                result["model"] = str(payload.get("model") or result["model"])
            elif event.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                usage = info.get("total_token_usage")
                if isinstance(usage, dict):
                    final_usage = usage
            elif event.get("type") == "response_item" and payload.get("type") in {
                "function_call", "custom_tool_call", "local_shell_call", "web_search_call",
            }:
                result["tool_call_count"] += 1

    total_input = _counter(final_usage.get("input_tokens"))
    cached_input = _counter(final_usage.get("cached_input_tokens"))
    result["input_tokens"] = max(0, total_input - cached_input)
    result["cache_read_tokens"] = cached_input
    result["output_tokens"] = _counter(final_usage.get("output_tokens"))
    result["reasoning_tokens"] = _counter(final_usage.get("reasoning_output_tokens"))
    return result


def find_codex_usage(reference: str, root: Path = DEFAULT_CODEX_SESSIONS) -> dict[str, Any]:
    """Find the newest Codex session whose transcript contains a run reference."""
    if not reference or not root.is_dir():
        return {}
    newest: Path | None = None
    newest_mtime = -1
    needle = reference.encode()
    for path in _session_files(root):
        try:
            if stream_contains(path, needle):
                modified = _mtime_ns(path)
                if modified > newest_mtime:
                    newest = path
                    newest_mtime = modified
        except OSError:
            continue
    if newest is None:
        return {}
    try:
        return read_codex_session(newest)
    except OSError:
        return {}
