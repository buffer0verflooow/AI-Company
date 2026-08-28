from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.notification_outbox import (
    _safe_counter,
    append_dead_letter,
    enqueue,
    get,
    mark_delivered,
    pending,
    record_failure,
)


class OutboxSafeCounterTests(unittest.TestCase):
    """Malformed outbox rows must not crash the delivery batch."""

    def test_safe_counter_defaults_and_fallbacks(self):
        self.assertEqual(_safe_counter(None), 0)
        self.assertEqual(_safe_counter(""), 0)
        self.assertEqual(_safe_counter("7"), 7)
        self.assertEqual(_safe_counter(3.9), 3)
        for bad in ("abc", [1], {"a": 1}, float("inf")):
            self.assertEqual(_safe_counter(bad), 0, bad)


class NotificationOutboxTests(unittest.TestCase):
    def test_enqueue_is_idempotent_and_pending_message_can_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            origin = {"platform": "weixin", "chat_id": "chat-1"}
            first = enqueue(
                db_path,
                dedup_key="digest:2026-07-26",
                kind="cron_recovery",
                source_id="cron-job",
                origin=origin,
                message="旧摘要",
                metadata={"legacy": True},
            )
            second = enqueue(
                db_path,
                dedup_key="digest:2026-07-26",
                kind="tvcr_cron",
                source_id="TVCR-R-20260726",
                origin=origin,
                message="新摘要",
                metadata={"review_id": "TVCR-R-20260726"},
            )
            self.assertEqual(first, second)
            self.assertEqual(len(pending(db_path)), 1)
            refreshed = get(db_path, first)
            self.assertEqual(refreshed["message"], "新摘要")
            self.assertEqual(refreshed["kind"], "tvcr_cron")
            self.assertEqual(refreshed["source_id"], "TVCR-R-20260726")
            self.assertIn("TVCR-R-20260726", refreshed["metadata_json"])

    def test_failed_attempts_backoff_then_dead_letter(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            notification_id = enqueue(
                db_path,
                dedup_key="cron:1",
                kind="cron_recovery",
                source_id="1",
                origin={"platform": "weixin", "chat_id": "chat-1"},
                message="报告",
            )
            first = record_failure(db_path, notification_id, "rate limited", max_attempts=2)
            self.assertEqual(first["state"], "pending")
            self.assertEqual(first["attempts"], 1)
            far_future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            self.assertEqual(len(pending(db_path, now=far_future)), 1)
            second = record_failure(db_path, notification_id, "still limited", max_attempts=2)
            self.assertEqual(second["state"], "dead_letter")
            self.assertEqual(pending(db_path, now=far_future), [])

    def test_delivered_rows_are_not_retried(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            notification_id = enqueue(
                db_path,
                dedup_key="cron:2",
                kind="cron_recovery",
                source_id="2",
                origin={"platform": "weixin", "chat_id": "chat-1"},
                message="报告",
            )
            mark_delivered(db_path, notification_id)
            self.assertEqual(pending(db_path), [])
            self.assertEqual(get(db_path, notification_id)["state"], "delivered")

    def test_dead_letter_record_tolerates_corrupt_attempts(self):
        # A corrupt attempts counter must not crash the dead-letter record.
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "dead-letter.jsonl"
            append_dead_letter(
                ledger,
                {
                    "kind": "tvcr_cron",
                    "source_id": "TVCR-R-1",
                    "notification_id": "n-1",
                    "platform": "weixin",
                    "chat_id": "chat-1",
                    "last_error": "boom",
                    "message": "m",
                    "attempts": "not-a-number",
                },
                reason="boom",
            )
            line = ledger.read_text(encoding="utf-8").strip()
            self.assertIn('"attempts": 0', line)


if __name__ == "__main__":
    unittest.main()
