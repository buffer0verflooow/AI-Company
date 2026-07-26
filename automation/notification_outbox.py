#!/usr/bin/env python3
"""Durable management-notification outbox.

The company workers can finish successfully even when the messaging adapter is
temporarily unavailable.  This module keeps a small, SQLite-backed outbox so
those notifications can be retried by the one-minute notifier job instead of
being lost with a cron process.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    from ._safe_io import file_lock, locked_append_text
except ImportError:  # direct execution from automation/
    from _safe_io import file_lock, locked_append_text


DEFAULT_MAX_ATTEMPTS = 12
DEFAULT_RETRY_BASE_SECONDS = 60
DEFAULT_RETRY_MAX_SECONDS = 3600


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the operations DB and create the outbox schema idempotently."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db: Optional[sqlite3.Connection] = None
    try:
        with file_lock(db_path):
            db = sqlite3.connect(db_path, timeout=5.0)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=5000")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    notification_id TEXT PRIMARY KEY,
                    dedup_key TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    source_id TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT '',
                    chat_id TEXT NOT NULL DEFAULT '',
                    thread_id TEXT NOT NULL DEFAULT '',
                    user_id TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL DEFAULT '',
                    last_attempt_at TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    delivered_at TEXT NOT NULL DEFAULT '',
                    dead_lettered_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_notification_outbox_ready
                    ON notification_outbox(state, next_attempt_at, created_at);
                """
            )
            db.commit()
        return db
    except BaseException:
        if db is not None:
            db.close()
        raise


def _notification_id(dedup_key: str) -> str:
    digest = hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()[:24]
    return f"NOTICE-{digest}"


def enqueue(
    db_path: Path,
    *,
    dedup_key: str,
    kind: str,
    source_id: str,
    origin: Dict[str, Any],
    message: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Insert a notification once and return its stable ID.

    A pending row may be refreshed with a newer message (for example, a digest
    generated again before its first delivery).  Delivered/dead-lettered rows
    are immutable so a recovery scan cannot resurrect an old notification.
    """

    now = utc_now()
    notification_id = _notification_id(dedup_key)
    db = connect(db_path)
    try:
        db.execute(
            """INSERT INTO notification_outbox
               (notification_id,dedup_key,kind,source_id,platform,chat_id,thread_id,
                user_id,message,metadata_json,state,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?, 'pending', ?,?)
               ON CONFLICT(dedup_key) DO UPDATE SET
                 message=CASE WHEN notification_outbox.state='pending'
                              THEN excluded.message ELSE notification_outbox.message END,
                 platform=CASE WHEN notification_outbox.state='pending'
                               THEN excluded.platform ELSE notification_outbox.platform END,
                 chat_id=CASE WHEN notification_outbox.state='pending'
                              THEN excluded.chat_id ELSE notification_outbox.chat_id END,
                 thread_id=CASE WHEN notification_outbox.state='pending'
                                THEN excluded.thread_id ELSE notification_outbox.thread_id END,
                 user_id=CASE WHEN notification_outbox.state='pending'
                              THEN excluded.user_id ELSE notification_outbox.user_id END,
                 metadata_json=CASE WHEN notification_outbox.state='pending'
                                    THEN excluded.metadata_json ELSE notification_outbox.metadata_json END,
                 updated_at=CASE WHEN notification_outbox.state='pending'
                                 THEN excluded.updated_at ELSE notification_outbox.updated_at END""",
            (
                notification_id,
                dedup_key,
                str(kind or "management"),
                str(source_id or ""),
                str(origin.get("platform") or ""),
                str(origin.get("chat_id") or ""),
                str(origin.get("thread_id") or ""),
                str(origin.get("user_id") or ""),
                str(message or ""),
                json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )
        db.commit()
    finally:
        db.close()
    return notification_id


def pending(db_path: Path, *, limit: int = 20, now: str = "") -> list[Dict[str, Any]]:
    """Return notifications whose retry time has arrived."""

    current = now or utc_now()
    db = connect(db_path)
    try:
        rows = db.execute(
            """SELECT * FROM notification_outbox
               WHERE state='pending'
                 AND (next_attempt_at='' OR next_attempt_at<=?)
               ORDER BY created_at ASC LIMIT ?""",
            (current, max(0, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def get(db_path: Path, notification_id: str) -> Optional[Dict[str, Any]]:
    db = connect(db_path)
    try:
        row = db.execute(
            "SELECT * FROM notification_outbox WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def get_by_dedup_key(db_path: Path, dedup_key: str) -> Optional[Dict[str, Any]]:
    db = connect(db_path)
    try:
        row = db.execute(
            "SELECT * FROM notification_outbox WHERE dedup_key=?",
            (dedup_key,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def mark_delivered(db_path: Path, notification_id: str) -> None:
    now = utc_now()
    db = connect(db_path)
    try:
        db.execute(
            """UPDATE notification_outbox
               SET state='delivered',delivered_at=?,updated_at=?,last_error='',next_attempt_at=''
               WHERE notification_id=? AND state='pending'""",
            (now, now, notification_id),
        )
        db.commit()
    finally:
        db.close()


def _retry_delay_seconds(
    notification_id: str,
    attempts: int,
    base_seconds: int,
    max_seconds: int,
) -> int:
    """Exponential backoff with a small deterministic jitter.

    Deterministic jitter keeps concurrent notifier processes from choosing the
    same retry instant while making tests and incident reconstruction stable.
    """

    base = max(1, int(base_seconds))
    maximum = max(base, int(max_seconds))
    exponential = min(maximum, base * (2 ** min(max(0, attempts - 1), 8)))
    seed = int(hashlib.sha256(f"{notification_id}:{attempts}".encode()).hexdigest()[:6], 16)
    jitter = int(exponential * ((seed % 21 - 10) / 100.0))
    return max(1, min(maximum, exponential + jitter))


def record_failure(
    db_path: Path,
    notification_id: str,
    error: str,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
) -> Dict[str, Any]:
    """Record one failed attempt and return the resulting row/state."""

    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    db = connect(db_path)
    try:
        row = db.execute(
            "SELECT attempts FROM notification_outbox WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        if not row:
            raise KeyError(f"unknown notification: {notification_id}")
        attempts = int(row["attempts"] or 0) + 1
        exhausted = attempts >= max(1, int(max_attempts))
        if exhausted:
            state = "dead_letter"
            next_attempt = ""
            dead_at = now
        else:
            state = "pending"
            delay = _retry_delay_seconds(
                notification_id, attempts, retry_base_seconds, retry_max_seconds,
            )
            next_attempt = (now_dt + timedelta(seconds=delay)).isoformat(timespec="seconds")
            dead_at = ""
        db.execute(
            """UPDATE notification_outbox
               SET state=?,attempts=?,next_attempt_at=?,last_attempt_at=?,last_error=?,
                   updated_at=?,dead_lettered_at=?
               WHERE notification_id=? AND state='pending'""",
            (state, attempts, next_attempt, now, str(error or "delivery failed"), now, dead_at, notification_id),
        )
        db.commit()
        updated = db.execute(
            "SELECT * FROM notification_outbox WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        return dict(updated) if updated else {
            "notification_id": notification_id, "state": state, "attempts": attempts,
        }
    finally:
        db.close()


def force_dead_letter(db_path: Path, notification_id: str, reason: str) -> Optional[Dict[str, Any]]:
    now = utc_now()
    db = connect(db_path)
    try:
        db.execute(
            """UPDATE notification_outbox
               SET state='dead_letter',last_error=?,next_attempt_at='',updated_at=?,dead_lettered_at=?
               WHERE notification_id=? AND state='pending'""",
            (str(reason or "not deliverable"), now, now, notification_id),
        )
        db.commit()
        row = db.execute(
            "SELECT * FROM notification_outbox WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def append_dead_letter(path: Path, row: Dict[str, Any], *, reason: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": utc_now(),
        "kind": row.get("kind", "management"),
        "identifier": row.get("source_id") or row.get("notification_id", ""),
        "notification_id": row.get("notification_id", ""),
        "origin": {
            "platform": row.get("platform", ""),
            "chat_id": row.get("chat_id", ""),
            "thread_id": row.get("thread_id", ""),
            "user_id": row.get("user_id", ""),
        },
        "reason": reason or row.get("last_error", "delivery failed"),
        "message": row.get("message", ""),
        "attempts": int(row.get("attempts") or 0),
    }
    locked_append_text(path, json.dumps(record, ensure_ascii=False) + "\n")


def summary(db_path: Path) -> Dict[str, int]:
    db = connect(db_path)
    try:
        rows = db.execute(
            "SELECT state,COUNT(*) AS count FROM notification_outbox GROUP BY state"
        ).fetchall()
        result = {"pending": 0, "delivered": 0, "dead_letter": 0}
        for row in rows:
            result[str(row["state"])] = int(row["count"])
        return result
    finally:
        db.close()
