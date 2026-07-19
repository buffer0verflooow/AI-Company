from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import RouterState, build_context, classify_message, handle_hook, select_company_result, submit_security
from automation.operations_control import business_period, create_review, import_proposals
from datetime import date


class ClassificationTests(unittest.TestCase):
    def test_company_execution_is_delegated(self):
        decision = classify_message("完善公司财务和项目管理流程")
        self.assertEqual(decision.route, "company")
        self.assertEqual(decision.action, "dispatch_company")

    def test_short_company_question_stays_with_main_agent(self):
        decision = classify_message("公司当前的项目状态怎么样？")
        self.assertEqual(decision.route, "company")
        self.assertEqual(decision.action, "main_agent")

    def test_contextual_start_modification_is_delegated(self):
        decision = classify_message("开始修改")
        self.assertEqual(decision.route, "company")
        self.assertEqual(decision.action, "dispatch_company")

    def test_article_route(self):
        decision = classify_message("把这份公开报告改成公众号文章并排版")
        self.assertEqual(decision.route, "article")
        self.assertEqual(decision.action, "dispatch_article")

    def test_video_route(self):
        decision = classify_message("用 Pixelle 给这篇文章生成视频分镜")
        self.assertEqual(decision.route, "video")
        self.assertEqual(decision.action, "dispatch_video")

    def test_article_pipeline_status_is_company_management(self):
        decision = classify_message("查看公司文章产线当前状态和流程")
        self.assertEqual(decision.route, "company")
        self.assertEqual(decision.action, "main_agent")

    def test_article_publish_requires_approval(self):
        decision = classify_message("把这篇文章发布到公众号")
        self.assertEqual(decision.route, "article")
        self.assertEqual(decision.action, "approval_required")

    def test_negated_article_publish_allows_draft_production(self):
        decision = classify_message("文章：写内部技术文章草稿，不发布、不推送草稿箱")
        self.assertEqual(decision.route, "article")
        self.assertEqual(decision.action, "dispatch_article")

    def test_local_security_analysis_dispatches(self):
        decision = classify_message("分析本机 APK 逆向报告中的认证逻辑")
        self.assertEqual(decision.route, "security")
        self.assertEqual(decision.intent, "analyze")
        self.assertEqual(decision.action, "dispatch_swarm")

    def test_external_probe_requires_authorization(self):
        decision = classify_message("扫描 example.com 并尝试绕过认证")
        self.assertEqual(decision.route, "security")
        self.assertEqual(decision.action, "approval_required")
        self.assertTrue(decision.authorization_required)

    def test_authorized_probe_dispatches(self):
        decision = classify_message("这是已授权 HackerOne 项目，扫描 example.com 的攻击面")
        self.assertEqual(decision.route, "security")
        self.assertEqual(decision.action, "dispatch_swarm")
        self.assertEqual(decision.intent, "recon")


class HookTests(unittest.TestCase):
    def test_article_message_is_submitted_to_content_executor(self):
        with tempfile.TemporaryDirectory() as td:
            config = {
                "enabled": True,
                "dispatch_security": False,
                "auto_run_security": False,
                "auto_run_article": True,
                "auto_run_video": True,
                "state_db": str(Path(td) / "router.db"),
                "swarm_repo": td,
                "swarm_db": str(Path(td) / "swarm.db"),
                "log_dir": str(Path(td) / "logs"),
                "executor": "/bin/false",
                "content_executor": "/bin/false",
                "content_job_dir": str(Path(td) / "content-jobs"),
                "gateway_sessions_index": str(Path(td) / "sessions.json"),
                "max_active_runs_per_session": 2,
                "max_active_content_jobs_per_session": 2,
            }
            payload = {"session_id": "article-session", "extra": {"user_message": "写一篇 Agent 工程公众号文章", "platform": "cli"}}
            with patch("automation.company_router.launch_content_job", return_value=4321):
                result = handle_hook(payload, config)
            self.assertIn("任务已自动分发至文章产线", result["context"])
            state = RouterState(config["state_db"])
            row = state.db.execute("SELECT action,run_id,runner_pid,status FROM route_events").fetchone()
            self.assertEqual(row["action"], "dispatch_article")
            self.assertTrue(row["run_id"])
            self.assertEqual(row["runner_pid"], 4321)
            self.assertEqual(row["status"], "running")
            state.close()

    def test_company_execution_is_submitted_to_worker(self):
        with tempfile.TemporaryDirectory() as td:
            config = {
                "enabled": True,
                "dispatch_security": False,
                "auto_run_company": True,
                "state_db": str(Path(td) / "router.db"),
                "swarm_repo": td,
                "swarm_db": str(Path(td) / "swarm.db"),
                "log_dir": str(Path(td) / "logs"),
                "content_executor": "/bin/false",
                "content_job_dir": str(Path(td) / "company-jobs"),
                "gateway_sessions_index": str(Path(td) / "sessions.json"),
                "max_active_runs_per_session": 2,
                "max_active_content_jobs_per_session": 2,
            }
            payload = {"session_id": "company-session", "extra": {"user_message": "修改公司任务路由并运行测试", "platform": "cli"}}
            with patch("automation.company_router.launch_content_job", return_value=8765):
                result = handle_hook(payload, config)
            self.assertIn("公司执行 Worker", result["context"])
            state = RouterState(config["state_db"])
            row = state.db.execute("SELECT action,run_id,runner_pid,status FROM route_events").fetchone()
            self.assertEqual(row["action"], "dispatch_company")
            self.assertTrue(row["run_id"])
            self.assertEqual(row["runner_pid"], 8765)
            self.assertEqual(row["status"], "running")
            state.close()

    def test_internal_worker_message_bypasses_router_without_global_env(self):
        with tempfile.TemporaryDirectory() as td:
            config = {
                "enabled": True,
                "state_db": str(Path(td) / "router.db"),
                "swarm_repo": td,
                "swarm_db": str(Path(td) / "swarm.db"),
                "content_job_dir": str(Path(td) / "content-jobs"),
            }
            payload = {
                "session_id": "worker",
                "extra": {"user_message": "[COMPANY_WORKER_INTERNAL]\n写文章", "platform": "cli"},
            }
            self.assertEqual(handle_hook(payload, config), {})
            self.assertFalse(Path(config["state_db"]).exists())

    def test_tvcr_worker_message_bypasses_router(self):
        config = {"enabled": True, "state_db": "/should/not/be/created"}
        payload = {"session_id": "worker", "extra": {"user_message": "[COMPANY_TVCR_INTERNAL]\n复盘", "platform": "tool"}}
        self.assertEqual(handle_hook(payload, config), {})

    def test_company_operator_worker_bypasses_router(self):
        config = {"enabled": True, "state_db": "/should/not/be/created"}
        payload = {"session_id": "worker", "extra": {"user_message": "[COMPANY_OPERATOR_INTERNAL]\n主动经营", "platform": "tool"}}
        self.assertEqual(handle_hook(payload, config), {})

    def test_tvcr_approval_creates_experiment_context(self):
        with tempfile.TemporaryDirectory() as td:
            operations_db = Path(td) / "operations.db"
            start, end = business_period(date(2026, 7, 15))
            review_id = create_review(operations_db, review_day=date(2026, 7, 15), period_start=start, period_end=end)
            proposal_id = import_proposals(operations_db, review_id, {
                "executive_summary": "test",
                "proposals": [{
                    "product_line": "article-production", "title": "流程实验",
                    "problem_statement": "成本高但价值未知", "recommended_action": "先做5篇实验",
                    "change_scopes": ["process"], "success_metrics": [{"metric": "token", "target": "-30%"}],
                }],
            })[0]
            config = {"enabled": True, "operations_db": str(operations_db), "state_db": str(Path(td) / "router.db")}
            payload = {"session_id": "user-session", "extra": {"user_message": f"批准 {proposal_id}", "platform": "weixin"}}
            result = handle_hook(payload, config)
            self.assertIn("已创建运营实验", result["context"])
            self.assertIn("先处理业务/产品/流程/资源决策", result["context"])

    def test_completed_existing_run_is_not_reported_as_merely_submitted(self):
        decision = classify_message("分析本机 APK 逆向报告中的认证逻辑")
        context = build_context(
            decision,
            existing_run_id="run-completed",
            existing_status="completed",
        )
        self.assertIn("已完成", context)
        self.assertNotIn("任务已自动提交至安全蜂群", context)

    def test_active_run_lookup_is_scoped_to_session(self):
        with tempfile.TemporaryDirectory() as td:
            state = RouterState(str(Path(td) / "router.db"))
            decision = classify_message("分析本机 APK 逆向报告中的认证逻辑")

            wanted_id = state.insert("wanted", "cli", "hash-1", "安全分析", decision)
            state.update(wanted_id, run_id="run-wanted", status="running")
            other_id = state.insert("other", "cli", "hash-2", "安全分析", decision)
            state.update(other_id, run_id="run-other", status="running")

            rows = state.active_for_session("wanted")
            self.assertEqual([row["run_id"] for row in rows], ["run-wanted"])
            state.close()

    def test_already_delivered_legacy_result_is_not_retried_proactively(self):
        with tempfile.TemporaryDirectory() as td:
            state = RouterState(str(Path(td) / "router.db"))
            decision = classify_message("分析本机 APK 逆向报告中的认证逻辑")
            event_id = state.insert("legacy", "test", "hash-legacy", "安全分析", decision)
            state.update(
                event_id,
                run_id="run-legacy",
                status="completed",
                result_delivered=1,
            )
            self.assertEqual(state.pending_notifications(), [])
            state.close()

    def test_hook_is_idempotent_for_non_security_message(self):
        with tempfile.TemporaryDirectory() as td:
            config = {
                "enabled": True,
                "dispatch_security": False,
                "auto_run_security": False,
                "state_db": str(Path(td) / "router.db"),
                "swarm_repo": td,
                "swarm_db": str(Path(td) / "swarm.db"),
                "log_dir": str(Path(td) / "logs"),
                "executor": "/bin/false",
                "max_active_runs_per_session": 2,
            }
            payload = {"session_id": "s1", "extra": {"user_message": "查看公司项目状态", "platform": "cli"}}
            first = handle_hook(payload, config)
            second = handle_hook(payload, config)
            self.assertIn("公司主 Agent", first["context"])
            self.assertEqual(first, second)
            state = RouterState(config["state_db"])
            count = state.db.execute("SELECT COUNT(*) FROM route_events").fetchone()[0]
            self.assertEqual(count, 1)
            state.close()

    def test_approval_gate_never_submits(self):
        with tempfile.TemporaryDirectory() as td:
            config = {
                "enabled": True,
                "dispatch_security": True,
                "auto_run_security": False,
                "state_db": str(Path(td) / "router.db"),
                "swarm_repo": td,
                "swarm_db": str(Path(td) / "swarm.db"),
                "log_dir": str(Path(td) / "logs"),
                "executor": "/bin/false",
                "max_active_runs_per_session": 2,
            }
            payload = {"session_id": "s2", "extra": {"user_message": "扫描 example.com", "platform": "cli"}}
            result = handle_hook(payload, config)
            self.assertIn("不要执行或自动分发", result["context"])
            state = RouterState(config["state_db"])
            row = state.db.execute("SELECT run_id, action FROM route_events").fetchone()
            self.assertEqual(row["run_id"], "")
            self.assertEqual(row["action"], "approval_required")
            state.close()


class SwarmIntegrationTests(unittest.TestCase):
    def test_submit_uses_real_swarm_client_contract(self):
        swarm_repo = Path("/home/pwn/workspace/research/swarm-knowledge")
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "swarm.db"
            import sys
            sys.path.insert(0, str(swarm_repo))
            try:
                from src import SwarmDB
                db = SwarmDB(str(db_path))
                self.assertTrue(db.init())
                db.close()
            finally:
                sys.path.remove(str(swarm_repo))

            config = {"swarm_repo": str(swarm_repo), "swarm_db": str(db_path)}
            message = "分析本机 APK 逆向报告中的认证逻辑"
            decision = classify_message(message)
            result = submit_security(config, "integration-session", "test", message, decision)
            self.assertTrue(result["run_id"])
            self.assertGreaterEqual(len(result["seeded_tasks"]), 1)

            conn = sqlite3.connect(db_path)
            stored = conn.execute("SELECT intent, config FROM swarm_runs WHERE run_id=?", (result["run_id"],)).fetchone()
            self.assertEqual(stored[0], "analyze")
            self.assertIn("security-exploration", stored[1])
            conn.close()

    def test_latest_corrective_result_wins_over_reporter_diff(self):
        result = {
            "result": "reporter fallback",
            "task_results": [
                {
                    "status": "completed",
                    "ended_at": "2026-07-15 03:35:31",
                    "result_summary": {"content": "┊ review diff\nclaimed file output"},
                },
                {
                    "status": "completed",
                    "ended_at": "2026-07-15 03:40:48",
                    "result_summary": {"content": "校正后的证据结论"},
                },
            ],
        }
        self.assertEqual(select_company_result(result), "校正后的证据结论")


if __name__ == "__main__":
    unittest.main()
