from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_result_notifier import _fit_delivery_message, list_terminal_deliveries, process_once
from automation.company_router import RouterState, classify_message, resolve_session_origin
from automation.operations_control import business_period, connect as connect_operations, create_review, import_proposals
from datetime import date


class OriginResolutionTests(unittest.TestCase):
    def test_resolves_gateway_session_origin(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sessions.json"
            path.write_text(json.dumps({
                "agent:main:weixin:dm:chat-1": {
                    "session_id": "session-1",
                    "platform": "weixin",
                    "origin": {
                        "platform": "weixin",
                        "chat_id": "chat-1",
                        "thread_id": None,
                        "user_id": "user-1",
                    },
                },
            }), encoding="utf-8")
            self.assertEqual(resolve_session_origin(str(path), "session-1"), {
                "platform": "weixin",
                "chat_id": "chat-1",
                "thread_id": "",
                "user_id": "user-1",
            })


class NotifierTests(unittest.TestCase):
    def test_weixin_delivery_is_kept_to_one_compact_message(self):
        message = "x" * 5000
        fitted = _fit_delivery_message(
            {"proactive_delivery_chars_by_platform": {"weixin": 1800}},
            {"platform": "weixin", "chat_id": "chat"},
            message,
        )
        self.assertLessEqual(len(fitted), 1800)
        self.assertIn("通知已截断", fitted)

    def _setup_event(self, td: str):
        db_path = str(Path(td) / "router.db")
        state = RouterState(db_path)
        decision = classify_message("分析本机 APK 逆向报告中的认证逻辑")
        event_id = state.insert(
            "session-1", "weixin", "hash-1", "安全分析", decision,
            origin={"platform": "weixin", "chat_id": "chat-1", "user_id": "user-1"},
        )
        state.update(event_id, run_id="run-1", status="running", runner_pid=999999)
        state.close()
        return db_path, event_id

    def _config(self, td: str, db_path: str):
        return {
            "state_db": db_path,
            "swarm_repo": td,
            "swarm_db": str(Path(td) / "swarm.db"),
            "proactive_delivery": True,
            "proactive_delivery_platforms": ["weixin"],
            "max_delivery_attempts": 3,
            "proactive_result_chars": 6000,
            "stale_run_minutes": 999999,
            "max_runner_restarts": 0,
        }

    def test_completed_run_is_delivered_once_and_mirrored(self):
        with tempfile.TemporaryDirectory() as td:
            db_path, event_id = self._setup_event(td)
            delivered = []
            mirrored = []
            result = {
                "status": "completed",
                "task_results": [{
                    "status": "completed",
                    "ended_at": "2026-07-15 04:00:00",
                    "result_summary": {"content": "证据化最终结论"},
                }],
            }
            with patch("automation.company_result_notifier.swarm_command", return_value=result):
                summary = process_once(
                    self._config(td, db_path),
                    deliverer=lambda _cfg, origin, message: (delivered.append((origin, message)) or True, ""),
                    mirror=lambda origin, message: bool(mirrored.append((origin, message)) or True),
                )
            self.assertEqual(summary["delivered"], 1)
            self.assertIn("证据化最终结论", delivered[0][1])
            self.assertEqual(len(mirrored), 1)
            state = RouterState(db_path)
            row = state.db.execute("SELECT * FROM route_events WHERE route_event_id=?", (event_id,)).fetchone()
            self.assertEqual(row["proactive_delivered"], 1)
            self.assertEqual(row["result_delivered"], 1)
            state.close()

    def test_failed_delivery_remains_retryable(self):
        with tempfile.TemporaryDirectory() as td:
            db_path, event_id = self._setup_event(td)
            calls = []
            with patch("automation.company_result_notifier.swarm_command", return_value={"status": "completed", "result": "done"}):
                summary = process_once(
                    self._config(td, db_path),
                    deliverer=lambda _cfg, _origin, _message: (calls.append(1) or False, "network down"),
                    mirror=lambda _origin, _message: True,
                )
                second = process_once(
                    self._config(td, db_path),
                    deliverer=lambda _cfg, _origin, _message: (calls.append(1) or False, "network down"),
                    mirror=lambda _origin, _message: True,
                )
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(second["failed"], 0)
            self.assertEqual(len(calls), 1)
            state = RouterState(db_path)
            row = state.db.execute("SELECT * FROM route_events WHERE route_event_id=?", (event_id,)).fetchone()
            self.assertEqual(row["proactive_delivered"], 0)
            self.assertEqual(row["delivery_attempts"], 1)
            self.assertEqual(row["delivery_error"], "network down")
            state.close()

    def test_non_allowlisted_platform_becomes_terminal_with_local_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "router.db")
            fallback = Path(td) / "dead-letters.jsonl"
            state = RouterState(db_path)
            decision = classify_message("分析本机 APK 认证逻辑")
            event_id = state.insert(
                "session-qq", "qq", "hash-qq", "安全分析", decision,
                origin={"platform": "qq", "chat_id": "chat-qq", "user_id": "user-qq"},
            )
            state.update(event_id, run_id="run-qq", status="running")
            state.close()
            config = self._config(td, db_path)
            config["delivery_fallback_path"] = str(fallback)
            calls = []
            with patch("automation.company_result_notifier.swarm_command", return_value={"status": "completed", "result": "done"}):
                first = process_once(
                    config,
                    deliverer=lambda *_args: (calls.append(1) or True, ""),
                    mirror=lambda _origin, _message: True,
                )
                second = process_once(
                    config,
                    deliverer=lambda *_args: (calls.append(1) or True, ""),
                    mirror=lambda _origin, _message: True,
                )
            self.assertEqual(first["terminal"], 1)
            self.assertEqual(second["checked"], 0)
            self.assertEqual(calls, [])
            records = [json.loads(line) for line in fallback.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["identifier"], "run-qq")
            self.assertIn("done", records[0]["message"])
            self.assertEqual(list_terminal_deliveries(config, 1)[0]["identifier"], "run-qq")
            state = RouterState(db_path)
            row = state.db.execute("SELECT * FROM route_events WHERE route_event_id=?", (event_id,)).fetchone()
            self.assertEqual(row["delivery_attempts"], config["max_delivery_attempts"])
            self.assertIn("terminal:", row["delivery_error"])
            state.close()

    def test_completed_article_job_is_delivered(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "router.db")
            job_root = Path(td) / "content-jobs"
            state = RouterState(db_path)
            decision = classify_message("写一篇 Agent 工程公众号文章")
            event_id = state.insert(
                "session-article", "weixin", "hash-article", "写文章", decision,
                origin={"platform": "weixin", "chat_id": "chat-article", "user_id": "user-article"},
            )
            run_id = "article-run"
            state.update(event_id, run_id=run_id, status="running")
            state.close()
            job_dir = job_root / run_id
            job_dir.mkdir(parents=True)
            draft = job_dir / "draft.md"
            draft.write_text("# Article", encoding="utf-8")
            (job_dir / "status.json").write_text(json.dumps({
                "status": "completed",
                "result": "三道质量门通过",
                "artifacts": [str(draft)],
            }), encoding="utf-8")
            config = self._config(td, db_path)
            config["content_job_dir"] = str(job_root)
            delivered = []
            summary = process_once(
                config,
                deliverer=lambda _cfg, origin, message: (delivered.append((origin, message)) or True, ""),
                mirror=lambda _origin, _message: True,
            )
            self.assertEqual(summary["delivered"], 1)
            self.assertIn("文章产线任务已完成", delivered[0][1])
            self.assertIn(str(draft), delivered[0][1])
            state = RouterState(db_path)
            row = state.db.execute("SELECT * FROM route_events WHERE route_event_id=?", (event_id,)).fetchone()
            self.assertEqual(row["proactive_delivered"], 1)
            state.close()

    def test_completed_company_job_is_delivered(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "router.db")
            job_root = Path(td) / "company-jobs"
            state = RouterState(db_path)
            decision = classify_message("修改公司任务路由并运行测试")
            event_id = state.insert(
                "session-company", "weixin", "hash-company", "修改路由", decision,
                origin={"platform": "weixin", "chat_id": "chat-company", "user_id": "user-company"},
            )
            run_id = "company-run"
            state.update(event_id, run_id=run_id, status="running")
            state.close()
            job_dir = job_root / run_id
            job_dir.mkdir(parents=True)
            report = job_dir / "task-report.md"
            report.write_text("# Done", encoding="utf-8")
            (job_dir / "status.json").write_text(json.dumps({
                "status": "completed",
                "result": "路由修改和测试已完成",
                "artifacts": [str(report)],
            }), encoding="utf-8")
            config = self._config(td, db_path)
            config["content_job_dir"] = str(job_root)
            delivered = []
            summary = process_once(
                config,
                deliverer=lambda _cfg, origin, message: (delivered.append((origin, message)) or True, ""),
                mirror=lambda _origin, _message: True,
            )
            self.assertEqual(summary["delivered"], 1)
            self.assertIn("公司执行任务已完成", delivered[0][1])
            self.assertIn(str(report), delivered[0][1])

    def test_company_job_waiting_approval_is_delivered_as_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "router.db")
            job_root = Path(td) / "company-jobs"
            state = RouterState(db_path)
            decision = classify_message("修改公司流程并运行测试")
            event_id = state.insert(
                "session-approval", "weixin", "hash-approval", "准备外部发布", decision,
                origin={"platform": "weixin", "chat_id": "chat-approval", "user_id": "user-approval"},
            )
            state.update(event_id, run_id="approval-run", status="running")
            state.close()
            job_dir = job_root / "approval-run"
            job_dir.mkdir(parents=True)
            approval = job_dir / "approval-request.md"
            approval.write_text("# Approval", encoding="utf-8")
            (job_dir / "status.json").write_text(json.dumps({
                "status": "needs_approval",
                "result": "内部准备完成，请批准外部发布",
                "artifacts": [str(approval)],
            }), encoding="utf-8")
            config = self._config(td, db_path)
            config["content_job_dir"] = str(job_root)
            delivered = []
            summary = process_once(
                config,
                deliverer=lambda _cfg, origin, message: (delivered.append((origin, message)) or True, ""),
                mirror=lambda _origin, _message: True,
            )
            self.assertEqual(summary["delivered"], 1)
            self.assertIn("公司执行任务等待审批", delivered[0][1])

    def test_pending_tvcr_review_is_delivered(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "router.db")
            RouterState(db_path).close()
            operations_db = Path(td) / "operations.db"
            start, end = business_period(date(2026, 7, 15))
            review_id = create_review(
                operations_db, review_day=date(2026, 7, 15), period_start=start, period_end=end,
                origin={"platform": "weixin", "chat_id": "chat-tvcr", "user_id": "user-tvcr"},
            )
            import_proposals(operations_db, review_id, {
                "executive_summary": "需要经营决策。",
                "proposals": [{
                    "product_line": "article-production", "priority": "P1", "title": "文章分级",
                    "problem_statement": "投入上升但结果未知", "recommended_action": "先做分级实验",
                }],
            })
            config = self._config(td, db_path)
            config["operations_db"] = str(operations_db)
            delivered = []
            summary = process_once(
                config,
                deliverer=lambda _cfg, origin, message: (delivered.append((origin, message)) or True, ""),
                mirror=lambda _origin, _message: True,
            )
            self.assertEqual(summary["delivered"], 1)
            self.assertIn("公司日报｜2026-07-15", delivered[0][1])
            self.assertIn("结论：需要经营决策。", delivered[0][1])
            self.assertLessEqual(len(delivered[0][1]), 1000)
            db = connect_operations(operations_db)
            self.assertEqual(db.execute("SELECT delivered FROM tvcr_reviews WHERE review_id=?", (review_id,)).fetchone()[0], 1)
            db.close()


if __name__ == "__main__":
    unittest.main()
