from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from automation.company_daily_digest import build_digest, run
from automation.company_router import RouterState, classify_message
from automation.notification_outbox import pending
from automation.operations_control import (
    business_period,
    connect,
    create_review,
    import_proposals,
    utc_now,
)


class CompanyDailyDigestTests(unittest.TestCase):
    def _setup(self, root: Path) -> Path:
        operations_db = root / "operations.db"
        router_db = root / "router.db"
        market_db = root / "market.db"
        jobs_path = root / "jobs.json"
        start, end = business_period(date(2026, 7, 25))
        review_id = create_review(
            operations_db,
            review_day=date(2026, 7, 25),
            period_start=start,
            period_end=end,
            origin={"platform": "weixin", "chat_id": "chat-1", "user_id": "user-1"},
        )
        import_proposals(operations_db, review_id, {
            "executive_summary": "昨天需要处理通知可靠性问题。",
            "proposals": [{
                "priority": "P0",
                "title": "修复通知",
                "problem_statement": "消息丢失",
                "recommended_action": "建立发件箱",
            }],
        })
        state = RouterState(str(router_db))
        state.insert(
            "session-1", "weixin", "digest-hash", "查看公司日报",
            classify_message("查看公司日报"),
            origin={"platform": "weixin", "chat_id": "chat-1", "user_id": "user-1"},
        )
        state.close()
        db = sqlite3.connect(market_db)
        db.executescript(
            """
            CREATE TABLE market_radar_runs (
              run_id TEXT PRIMARY KEY,status TEXT,completed_at TEXT
            );
            CREATE TABLE market_pulses (
              pulse_id TEXT PRIMARY KEY,run_id TEXT,theme_title TEXT,score REAL,
              independent_sources INTEGER
            );
            """
        )
        db.execute("INSERT INTO market_radar_runs VALUES ('mkt-1','completed','2026-07-26T00:30:00+00:00')")
        db.execute("INSERT INTO market_pulses VALUES ('p1','mkt-1','智能体安全治理',79.5,5)")
        db.commit()
        db.close()
        jobs_path.write_text(json.dumps({"jobs": [{
                "id": "operator-1",
                "name": "company-daily-operator",
                "enabled": False,
                "state": "paused",
                "paused_at": "2026-07-21T21:32:09+08:00",
            }]}), encoding="utf-8")
        config = {
            "timezone": "Asia/Shanghai",
            "operations_db": str(operations_db),
            "state_db": str(router_db),
            "market_signals_db": str(market_db),
            "cron_jobs_path": str(jobs_path),
        }
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        return config_path

    def test_digest_reports_paused_operator_without_running_it(self):
        with tempfile.TemporaryDirectory() as td:
            config_path = self._setup(Path(td))
            config = json.loads(config_path.read_text(encoding="utf-8"))
            message, info = build_digest(
                config,
                now=datetime(2026, 7, 26, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            self.assertIn("经营自动执行：已暂停", message)
            self.assertIn("智能体安全治理", message)
            self.assertIn("待审批提案：1（P0：1）", message)
            self.assertTrue(info["origin_available"])

    def _seed_failed_run(self, operations_db: Path, run_id: str, product_line: str,
                         quality: str, created_at: str) -> None:
        db = connect(operations_db)
        db.execute(
            """INSERT INTO operational_runs (run_id,product_line,source_type,status,
               quality_status,created_at,completed_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (run_id, product_line, "content-job", "failed", quality, created_at, created_at, utc_now()),
        )
        db.commit()
        db.close()

    def test_digest_clusters_recent_failures(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = self._setup(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            operations_db = root / "operations.db"
            recent = "2026-07-25T18:00:00+00:00"  # inside the 24h window
            self._seed_failed_run(operations_db, "f1", "security-exploration", "empty_output", recent)
            self._seed_failed_run(operations_db, "f2", "security-exploration", "empty_output", recent)
            self._seed_failed_run(operations_db, "f3", "article-production", "missing_qa", recent)
            self._seed_failed_run(operations_db, "old", "company", "empty_output", "2026-07-01T00:00:00+00:00")

            message, info = build_digest(
                config,
                now=datetime(2026, 7, 26, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            self.assertIn("失败聚类：3 次失败，2 个类别", message)
            self.assertIn("security-exploration / empty_output：2 次", message)
            clusters = info["failure_clusters"]
            self.assertEqual(clusters[0]["count"], 2)  # ranked most-frequent first
            self.assertNotIn("old", [c.get("run_id") for c in clusters])  # window excludes stale failure

    def test_digest_reports_no_failures_cleanly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = self._setup(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            message, info = build_digest(
                config,
                now=datetime(2026, 7, 26, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            self.assertIn("失败聚类：无失败运行", message)
            self.assertEqual(info["failure_clusters"], [])

    def test_run_enqueues_one_daily_notification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_path = self._setup(root)
            first = run(config_path)
            second = run(config_path)
            self.assertTrue(first["queued"])
            self.assertEqual(first["notification_id"], second["notification_id"])
            rows = pending(root / "operations.db")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "daily_digest")


if __name__ == "__main__":
    unittest.main()
