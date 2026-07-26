from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from automation.notification_outbox import (
    connect,
    enqueue,
    get,
    mark_delivered,
    pending,
    record_failure,
)


class NotificationOutboxTests(unittest.TestCase):
    def test_enqueue_is_idempotent_and_pending_message_can_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            origin = {"platform": "weixin", "chat_id": "chat-1"}
            first = enqueue(
                db_path,
                dedup_key="digest:2026-07-26",
                kind="digest",
                source_id="2026-07-26",
                origin=origin,
                message="旧摘要",
            )
            second = enqueue(
                db_path,
                dedup_key="digest:2026-07-26",
                kind="digest",
                source_id="2026-07-26",
                origin=origin,
                message="新摘要",
            )
            self.assertEqual(first, second)
            self.assertEqual(len(pending(db_path)), 1)
            self.assertEqual(get(db_path, first)["message"], "新摘要")

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


if __name__ == "__main__":
    unittest.main()
