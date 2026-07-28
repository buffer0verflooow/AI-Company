from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from automation.company_result_notifier import (
    _find_cron_output,
    _fit_delivery_message,
    list_terminal_deliveries,
    process_once,
    recover_failed_cron_deliveries,
)
from automation.company_router import RouterState, classify_message, resolve_session_origin
from automation.notification_outbox import pending as pending_outbox
from automation.notification_outbox import enqueue as enqueue_outbox
from automation.notification_outbox import get as get_outbox
from automation.operations_control import business_period, connect as connect_operations, create_review, import_proposals


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

    def test_terminal_delivery_limit_zero_returns_no_records(self):
        with tempfile.TemporaryDirectory() as td:
            fallback = Path(td) / "deliveries.jsonl"
            fallback.write_text('{"identifier":"one"}\n', encoding="utf-8")
            self.assertEqual(list_terminal_deliveries({"delivery_fallback_path": str(fallback)}, 0), [])

    def test_cron_output_rejects_traversal_job_id(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(_find_cron_output({"cron_output_root": td}, "../escape", ""))

    def test_invalid_content_run_path_is_failed_without_filesystem_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = str(root / "router.db")
            state = RouterState(db_path)
            decision = classify_message("写一篇 Agent 工程公众号文章")
            event_id = state.insert(
                "session-invalid", "weixin", "hash-invalid", "写文章", decision,
                origin={"platform": "weixin", "chat_id": "chat"},
            )
            state.update(event_id, run_id="../escape", status="running")
            state.close()
            config = self._config(td, db_path)
            config["content_job_dir"] = str(root / "jobs")
            summary = process_once(
                config,
                deliverer=lambda *_args: (True, ""),
                mirror=lambda *_args: True,
            )
            self.assertEqual(summary["failed"], 1)
            state = RouterState(db_path)
            row = state.db.execute("SELECT status,error FROM route_events WHERE route_event_id=?", (event_id,)).fetchone()
            state.close()
            self.assertEqual(row["status"], "failed")
            self.assertIn("invalid content run path", row["error"])

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

    def test_failed_cron_delivery_is_recovered_by_job_name_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            router_db = str(root / "router.db")
            state = RouterState(router_db)
            state.insert(
                "session-1", "weixin", "hash-cron-recovery", "公司状态",
                classify_message("公司状态"),
                origin={"platform": "weixin", "chat_id": "chat-1", "user_id": "user-1"},
            )
            state.close()
            last_run = "2026-07-26T04:10:51+08:00"
            jobs_path = root / "jobs.json"
            jobs_path.write_text(json.dumps({"jobs": [{
                    "id": "job-1",
                    "name": "company-daily-auto-fix",
                    "last_run_at": last_run,
                    "last_delivery_error": "rate limited",
                }]}), encoding="utf-8")
            output_dir = root / "output" / "job-1"
            output_dir.mkdir(parents=True)
            output = output_dir / "2026-07-26_04-10-51.md"
            output.write_text(
                "# Cron Job\n\n## Response\n---\n\n自动修复完成，测试通过。\n",
                encoding="utf-8",
            )
            stamp = datetime.fromisoformat(last_run).timestamp()
            os.utime(output, (stamp, stamp))
            config = self._config(td, router_db)
            config.update({
                "operations_db": str(root / "operations.db"),
                "router_db": router_db,
                "cron_jobs_path": str(jobs_path),
                "cron_output_root": str(root / "output"),
                "cron_delivery_recovery_jobs": ["company-daily-auto-fix"],
            })
            self.assertEqual(recover_failed_cron_deliveries(config), 1)
            self.assertEqual(recover_failed_cron_deliveries(config), 0)
            rows = pending_outbox(root / "operations.db")
            self.assertEqual(len(rows), 1)
            self.assertIn("自动修复完成", rows[0]["message"])

    def test_outbox_exhaustion_writes_recoverable_dead_letter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            router_db = root / "router.db"
            operations_db = root / "operations.db"
            RouterState(str(router_db)).close()
            enqueue_outbox(
                operations_db,
                dedup_key="failed:1",
                kind="daily_digest",
                source_id="failed:1",
                origin={"platform": "weixin", "chat_id": "chat-1"},
                message="必须保留的日报",
            )
            config = self._config(td, str(router_db))
            config.update({
                "operations_db": str(operations_db),
                "router_db": str(router_db),
                "outbox_max_attempts": 1,
                "delivery_fallback_path": str(root / "dead-letters.jsonl"),
                "cron_jobs_path": str(root / "missing-jobs.json"),
            })
            summary = process_once(
                config,
                deliverer=lambda *_args: (False, "rate limited"),
                mirror=lambda *_args: True,
            )
            self.assertEqual(summary["outbox_dead_letter"], 1)
            self.assertIn("必须保留的日报", (root / "dead-letters.jsonl").read_text(encoding="utf-8"))

    def test_local_tvcr_cron_output_is_queued_as_formatted_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            router_db = str(root / "router.db")
            state = RouterState(router_db)
            state.insert(
                "session-1", "weixin", "hash-tvcr-cron", "公司复盘",
                classify_message("公司复盘"),
                origin={"platform": "weixin", "chat_id": "chat-1", "user_id": "user-1"},
            )
            state.close()
            operations_db = root / "operations.db"
            start, end = business_period(date(2026, 7, 25))
            review_id = create_review(
                operations_db,
                review_day=date(2026, 7, 25),
                period_start=start,
                period_end=end,
                origin={"platform": "weixin", "chat_id": "chat-1", "user_id": "user-1"},
            )
            import_proposals(operations_db, review_id, {
                "executive_summary": "通知链路需要修复。",
                "proposals": [{
                    "priority": "P0", "title": "可靠通知",
                    "problem_statement": "消息丢失", "recommended_action": "使用发件箱",
                }],
            })
            last_run = "2026-07-26T00:33:57+08:00"
            jobs_path = root / "jobs.json"
            jobs_path.write_text(json.dumps({"jobs": [{
                "id": "tvcr-job",
                "name": "company-tvcr-daily-review",
                "deliver": "local",
                "last_run_at": last_run,
                "last_delivery_error": None,
            }]}), encoding="utf-8")
            output_dir = root / "output" / "tvcr-job"
            output_dir.mkdir(parents=True)
            output = output_dir / "2026-07-26_00-33-57.md"
            output.write_text(
                "# Cron Job\n\n---\n\n" + json.dumps({"review_id": review_id}),
                encoding="utf-8",
            )
            stamp = datetime.fromisoformat(last_run).timestamp()
            os.utime(output, (stamp, stamp))
            config = self._config(td, router_db)
            config.update({
                "operations_db": str(operations_db),
                "router_db": router_db,
                "cron_jobs_path": str(jobs_path),
                "cron_output_root": str(root / "output"),
                "cron_delivery_recovery_jobs": ["company-tvcr-daily-review"],
                "tvcr_delivery_default_chars": 1000,
            })
            self.assertEqual(recover_failed_cron_deliveries(config), 1)
            rows = pending_outbox(operations_db)
            self.assertEqual(rows[0]["kind"], "tvcr_cron")
            self.assertEqual(json.loads(rows[0]["metadata_json"])["review_id"], review_id)
            self.assertIn("公司日报｜2026-07-25", rows[0]["message"])
            self.assertIn("通知链路需要修复", rows[0]["message"])

    def test_tvcr_outbox_success_marks_review_and_skips_legacy_delivery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            router_db = str(root / "router.db")
            RouterState(router_db).close()
            operations_db = root / "operations.db"
            start, end = business_period(date(2026, 7, 25))
            review_id = create_review(
                operations_db,
                review_day=date(2026, 7, 25),
                period_start=start,
                period_end=end,
                origin={"platform": "weixin", "chat_id": "chat-1", "user_id": "user-1"},
            )
            import_proposals(operations_db, review_id, {
                "executive_summary": "统一由发件箱投递。",
                "proposals": [{
                    "priority": "P0", "title": "单一发送者",
                    "problem_statement": "重复投递", "recommended_action": "停用旧循环",
                }],
            })
            last_run = "2026-07-26T00:33:57+08:00"
            jobs_path = root / "jobs.json"
            jobs_path.write_text(json.dumps({"jobs": [{
                "id": "tvcr-job", "name": "company-tvcr-daily-review",
                "deliver": "local", "last_run_at": last_run,
            }]}), encoding="utf-8")
            output_dir = root / "output" / "tvcr-job"
            output_dir.mkdir(parents=True)
            output = output_dir / "2026-07-26_00-33-57.md"
            output.write_text(
                "# Cron Job\n\n---\n\n" + json.dumps({"review_id": review_id}),
                encoding="utf-8",
            )
            stamp = datetime.fromisoformat(last_run).timestamp()
            os.utime(output, (stamp, stamp))
            config = self._config(td, router_db)
            config.update({
                "operations_db": str(operations_db),
                "router_db": router_db,
                "cron_jobs_path": str(jobs_path),
                "cron_output_root": str(root / "output"),
                "cron_delivery_recovery_jobs": ["company-tvcr-daily-review"],
                "tvcr_delivery_via_outbox": True,
            })
            delivered = []
            first = process_once(
                config,
                deliverer=lambda _cfg, origin, message: (delivered.append((origin, message)) or True, ""),
                mirror=lambda _origin, _message: True,
            )
            second = process_once(
                config,
                deliverer=lambda _cfg, origin, message: (delivered.append((origin, message)) or True, ""),
                mirror=lambda _origin, _message: True,
            )
            self.assertEqual(first["outbox_delivered"], 1)
            self.assertEqual(first["delivered"], 0)
            self.assertEqual(second["outbox_checked"], 0)
            self.assertEqual(len(delivered), 1)
            db = connect_operations(operations_db)
            review = db.execute(
                "SELECT delivered,delivery_attempts,delivery_error FROM tvcr_reviews WHERE review_id=?",
                (review_id,),
            ).fetchone()
            outbox = db.execute(
                "SELECT notification_id FROM notification_outbox WHERE kind='tvcr_cron'",
            ).fetchone()
            db.close()
            self.assertEqual(review["delivered"], 1)
            self.assertGreaterEqual(review["delivery_attempts"], 1)
            self.assertEqual(review["delivery_error"], "")
            self.assertEqual(get_outbox(operations_db, outbox["notification_id"])["state"], "delivered")

    def test_tvcr_outbox_dead_letter_updates_review_terminal_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            router_db = str(root / "router.db")
            RouterState(router_db).close()
            operations_db = root / "operations.db"
            start, end = business_period(date(2026, 7, 25))
            review_id = create_review(
                operations_db,
                review_day=date(2026, 7, 25),
                period_start=start,
                period_end=end,
                origin={"platform": "weixin", "chat_id": "chat-1", "user_id": "user-1"},
            )
            import_proposals(operations_db, review_id, {
                "executive_summary": "终态也必须可追踪。",
                "proposals": [{
                    "priority": "P0", "title": "死信同步",
                    "problem_statement": "状态分裂", "recommended_action": "同步终态",
                }],
            })
            last_run = "2026-07-26T00:33:57+08:00"
            jobs_path = root / "jobs.json"
            jobs_path.write_text(json.dumps({"jobs": [{
                "id": "tvcr-job", "name": "company-tvcr-daily-review",
                "deliver": "local", "last_run_at": last_run,
            }]}), encoding="utf-8")
            output_dir = root / "output" / "tvcr-job"
            output_dir.mkdir(parents=True)
            output = output_dir / "2026-07-26_00-33-57.md"
            output.write_text(
                "# Cron Job\n\n---\n\n" + json.dumps({"review_id": review_id}),
                encoding="utf-8",
            )
            stamp = datetime.fromisoformat(last_run).timestamp()
            os.utime(output, (stamp, stamp))
            fallback = root / "dead-letters.jsonl"
            config = self._config(td, router_db)
            config.update({
                "operations_db": str(operations_db),
                "router_db": router_db,
                "cron_jobs_path": str(jobs_path),
                "cron_output_root": str(root / "output"),
                "cron_delivery_recovery_jobs": ["company-tvcr-daily-review"],
                "tvcr_delivery_via_outbox": True,
                "outbox_max_attempts": 1,
                "delivery_fallback_path": str(fallback),
            })
            summary = process_once(
                config,
                deliverer=lambda *_args: (False, "rate limited"),
                mirror=lambda *_args: True,
            )
            self.assertEqual(summary["outbox_dead_letter"], 1)
            db = connect_operations(operations_db)
            review = db.execute(
                "SELECT delivered,delivery_attempts,delivery_error FROM tvcr_reviews WHERE review_id=?",
                (review_id,),
            ).fetchone()
            db.close()
            self.assertEqual(review["delivered"], 0)
            self.assertEqual(review["delivery_attempts"], 1)
            self.assertIn("terminal: outbox delivery failed: rate limited", review["delivery_error"])
            self.assertIn("tvcr_cron", fallback.read_text(encoding="utf-8"))

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

    def test_non_allowlisted_content_job_is_terminal_and_recoverable(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "router.db")
            job_root = Path(td) / "content-jobs"
            fallback = Path(td) / "dead-letters.jsonl"
            state = RouterState(db_path)
            decision = classify_message("写一篇 Agent 工程公众号文章")
            event_id = state.insert(
                "session-article-qq", "qq", "hash-article-qq", "写文章", decision,
                origin={"platform": "qq", "chat_id": "chat-article", "user_id": "user-article"},
            )
            state.update(event_id, run_id="article-qq", status="running")
            state.close()
            job_dir = job_root / "article-qq"
            job_dir.mkdir(parents=True)
            (job_dir / "status.json").write_text(json.dumps({
                "status": "completed", "result": "文章完成", "artifacts": [],
            }), encoding="utf-8")
            config = self._config(td, db_path)
            config["content_job_dir"] = str(job_root)
            config["delivery_fallback_path"] = str(fallback)
            calls = []

            summary = process_once(
                config,
                deliverer=lambda *_args: (calls.append(1) or True, ""),
                mirror=lambda _origin, _message: True,
            )

            self.assertEqual(summary["terminal"], 1)
            self.assertEqual(calls, [])
            record = list_terminal_deliveries(config, 1)[0]
            self.assertEqual(record["kind"], "content")
            self.assertEqual(record["identifier"], "article-qq")
            state = RouterState(db_path)
            row = state.db.execute("SELECT * FROM route_events WHERE route_event_id=?", (event_id,)).fetchone()
            self.assertEqual(row["delivery_attempts"], config["max_delivery_attempts"])
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

    def test_non_allowlisted_tvcr_is_terminal_and_recoverable(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = str(Path(td) / "router.db")
            RouterState(db_path).close()
            operations_db = Path(td) / "operations.db"
            fallback = Path(td) / "dead-letters.jsonl"
            start, end = business_period(date(2026, 7, 15))
            review_id = create_review(
                operations_db, review_day=date(2026, 7, 15), period_start=start, period_end=end,
                origin={"platform": "qq", "chat_id": "chat-tvcr", "user_id": "user-tvcr"},
            )
            import_proposals(operations_db, review_id, {
                "executive_summary": "需要经营决策。",
                "proposals": [{
                    "product_line": "company", "priority": "P1", "title": "改进",
                    "problem_statement": "结果需要复核", "recommended_action": "执行内部实验",
                }],
            })
            config = self._config(td, db_path)
            config["operations_db"] = str(operations_db)
            config["delivery_fallback_path"] = str(fallback)
            calls = []

            summary = process_once(
                config,
                deliverer=lambda *_args: (calls.append(1) or True, ""),
                mirror=lambda _origin, _message: True,
            )

            self.assertEqual(summary["terminal"], 1)
            self.assertEqual(calls, [])
            record = list_terminal_deliveries(config, 1)[0]
            self.assertEqual(record["kind"], "tvcr")
            self.assertEqual(record["identifier"], review_id)
            db = connect_operations(operations_db)
            row = db.execute("SELECT * FROM tvcr_reviews WHERE review_id=?", (review_id,)).fetchone()
            self.assertEqual(row["delivery_attempts"], config["max_delivery_attempts"])
            self.assertIn("terminal:", row["delivery_error"])
            db.close()


if __name__ == "__main__":
    unittest.main()
