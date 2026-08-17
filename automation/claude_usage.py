#!/usr/bin/env python3
"""Read measured usage directly from Claude Code native session JSONL files."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

try:
    from ._safe_io import stream_contains
except ImportError:  # direct script/module execution from automation/
    from _safe_io import stream_contains


DEFAULT_CLAUDE_PROJECTS = Path.home() / ".claude/projects"


def _session_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    return root.glob("*/*.jsonl")


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


def read_claude_session(path: Path) -> dict[str, Any]:
    """Return deduplicated counters and metadata for one Claude Code session."""
    result: dict[str, Any] = {
        "id": "",
        "model": "",
        "model_provider": "anthropic",
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
    seen_messages: set[str] = set()
    seen_tools: set[str] = set()
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
                if not result["started_at"]:
                    result["started_at"] = timestamp
                result["completed_at"] = timestamp
            result["id"] = str(event.get("sessionId") or result["id"])
            result["cwd"] = str(event.get("cwd") or result["cwd"])
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            result["model"] = str(message.get("model") or result["model"])
            message_id = str(message.get("id") or "")
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            if message_id and usage and message_id not in seen_messages:
                seen_messages.add(message_id)
                result["input_tokens"] += _counter(usage.get("input_tokens"))
                result["output_tokens"] += _counter(usage.get("output_tokens"))
                result["cache_read_tokens"] += _counter(usage.get("cache_read_input_tokens"))
                result["cache_write_tokens"] += _counter(usage.get("cache_creation_input_tokens"))
            content = message.get("content") if isinstance(message.get("content"), list) else []
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id = str(block.get("id") or "")
                if tool_id and tool_id not in seen_tools:
                    seen_tools.add(tool_id)
                    result["tool_call_count"] += 1
    return result


def find_claude_usage(reference: str, root: Path = DEFAULT_CLAUDE_PROJECTS) -> dict[str, Any]:
    """Find the newest Claude Code session whose transcript contains a run reference."""
    if not reference or not root.is_dir():
        return {}
    needle = reference.encode()
    newest: Path | None = None
    newest_mtime = -1
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
        return read_claude_session(newest)
    except OSError:
        return {}
