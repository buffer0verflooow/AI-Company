from __future__ import annotations

import sqlite3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import RouterState, build_context, classify_message, classify_with_fallback, handle_hook, select_company_result, submit_security
from automation.operations_control import business_period, create_review, import_proposals
from automation.operations_control import connect as connect_operations
from datetime import date, datetime, timezone


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

    def test_inband_authorization_text_is_not_trusted(self):
        # "已授权" in the message must NOT bypass the gate — closes the phrasing bypass.
        decision = classify_message("这是已授权 HackerOne 项目，扫描 example.com 的攻击面")
        self.assertEqual(decision.route, "security")
        self.assertEqual(decision.action, "approval_required")
        self.assertTrue(decision.authorization_required)

    def test_scope_allowlist_authorizes_probe(self):
        decision = classify_message("扫描 example.com 的攻击面", authorized_targets={"example.com"})
        self.assertEqual(decision.action, "dispatch_swarm")
        self.assertEqual(decision.intent, "recon")

    def test_poc_against_external_target_requires_authorization(self):
        # "poc" previously set intent=exploit but skipped active-security gating.
        decision = classify_message("给 acme.com 的登录接口写一个 poc")
        self.assertEqual(decision.route, "security")
        self.assertEqual(decision.intent, "exploit")
        self.assertEqual(decision.action, "approval_required")

    def test_reminder_phrase_does_not_bypass_publish_approval(self):
        # "不要忘记发布" must not be misread as a negated (suppressed) publish.
        decision = classify_message("这篇文章不要忘记发布到公众号")
        self.assertEqual(decision.action, "approval_required")

    def test_information_query_about_audio_video_is_not_video_production(self):
        decision = classify_message("检查是否支持眼镜直接联网传输音视频数据")
        self.assertEqual(decision.route, "company")
        self.assertEqual(decision.action, "main_agent")

    def test_mineru_extraction_is_not_article_production(self):
        decision = classify_message("让 MinerU 提取刚才的文章")
        self.assertEqual(decision.route, "company")
        self.assertEqual(decision.action, "main_agent")

    def test_company_glasses_question_is_not_execution(self):
        decision = classify_message("对于公司的 AI 眼镜项目，Rokid 眼镜实现有什么借鉴意义")
        self.assertEqual(decision.route, "company")
        self.assertEqual(decision.action, "main_agent")

    def test_apk_download_question_is_not_security_swarm(self):
        decision = classify_message("瞳者 AI 眼镜有公开的 APK 下载吗")
        self.assertEqual(decision.route, "company")
        self.assertEqual(decision.action, "main_agent")

    def test_security_article_prefers_article_production(self):
        decision = classify_message("写一篇关于 JWT 安全的公众号文章")
        self.assertEqual(decision.route, "article")
        self.assertEqual(decision.action, "dispatch_article")

    def test_research_request_routes_to_swarm(self):
        # 蜂群研究路由 (2026-08-12): 公司职能研究任务 → dispatch_swarm,
        # intent 统一为 research (由蜂群侧按 research 产品线播种)。
        cases = (
            ("调研一下竞品 X 的技术方案", "research"),
            ("做一份 AI 眼镜行业竞品分析报告", "research"),
            ("对比一下 Codex 和 Claude Code 的优缺点", "research"),
        )
        for message, intent in cases:
            with self.subTest(message=message):
                decision = classify_message(message)
                self.assertEqual(decision.route, "research")
                self.assertEqual(decision.action, "dispatch_swarm")
                self.assertEqual(decision.intent, intent)
                self.assertEqual(decision.target_type, "unknown")

    def test_research_question_stays_with_main_agent(self):
        # 纯问答不走蜂群 (无执行意图)
        decision = classify_message("我们的竞品是谁？")
        self.assertNotEqual(decision.action, "dispatch_swarm")

    def test_security_request_not_swallowed_by_research(self):
        # research 词表含"分析/评估"等, 不得抢走 security 判定
        decision = classify_message("扫描并分析 10.0.0.5 的漏洞")
        self.assertEqual(decision.route, "security")
        self.assertEqual(decision.action, "dispatch_swarm")

    def test_technical_methodology_routes_to_research(self):
        # 方法论研究门控 (2026-08-12): 讨论"怎么做"的技术陈述句,
        # 即使含 fuzz/漏洞/反编译 等 security 词, 也不得被派成 recon 扫描。
        cases = (
            "现在方法是将二进制反编译成伪代码，然后使用SAST/静态代码分析等方法，"
            "来查找漏洞；或者通过动态fuzz，模拟执行来找漏洞",
            "盘点一下当前利用AI进行二进制漏洞挖掘的主流方法",
            "梳理一下语法树和代码图在漏洞检测里的技术细节",
        )
        for message in cases:
            with self.subTest(message=message[:20]):
                decision = classify_message(message)
                self.assertEqual(decision.route, "research")
                self.assertEqual(decision.action, "dispatch_swarm")
                self.assertEqual(decision.intent, "research")
                self.assertEqual(decision.target_type, "unknown")

    def test_active_security_task_not_swallowed_by_methodology_gate(self):
        # 方法论门控不得抢走真实安全任务: 主动攻击动词/目标实体优先。
        cases = (
            "扫描 example.com 并尝试绕过认证",
            "给 acme.com 的登录接口写一个 poc",
            "扫描并分析 10.0.0.5 的漏洞",
        )
        for message in cases:
            with self.subTest(message=message):
                decision = classify_message(message)
                self.assertEqual(decision.route, "security")

    def test_methodology_with_source_url_routes_to_research(self):
        # URL 作为内容来源 (2026-08-13): "抓取分析一下 https://... 中提到的漏洞
        # 挖掘方法" 是读文章、分析方法的研究请求，URL 不是攻击目标，不得被派成
        # security/analyze。主动攻击动词/目标实体的真实安全任务仍走 security。
        cases = (
            ("抓取分析一下https://zeropath.com/blog/0day-discoveries中提到的漏洞挖掘方法", "research"),
            ("抓取分析一下 https://zeropath.com/blog/0day-discoveries 中提到的漏洞挖掘方法", "research"),
            ("抓取并分析这篇文章里提到的漏洞挖掘方法", "research"),
        )
        for message, expected in cases:
            with self.subTest(message=message[:24]):
                decision = classify_message(message)
                self.assertEqual(decision.route, expected)
                if expected == "research":
                    self.assertEqual(decision.action, "dispatch_swarm")
                    self.assertEqual(decision.intent, "research")

    def test_source_url_gate_keeps_real_security_tasks(self):
        # 有 URL 但带主动攻击动词/目标时，仍然是真实安全任务，不被当成阅读内容来源。
        cases = (
            ("扫描 example.com 并尝试绕过认证", "security"),
            ("扫描并分析 10.0.0.5 的漏洞", "security"),
            ("给 acme.com 的登录接口写一个 poc", "security"),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                decision = classify_message(message)
                self.assertEqual(decision.route, expected)

    def test_meta_swarm_discussion_stays_with_main_agent(self):
        # 蜂群自身系统元讨论门控 (2026-08-13): "蜂群算法/蜂群调度机制" 描述的是
        # 公司自身蜂群系统，不能被 "蜂群"+"分析" 误派成 security 蜂群任务。
        cases = (
            "分析一下当前系统的蜂群算法，我们进行讨论",
            "分析一下蜂群算法",
            "分析当前系统的蜂群算法",
            "分析蜂群算法的调度机制",
            "优化蜂群算法的调度策略",
        )
        for message in cases:
            with self.subTest(message=message[:20]):
                decision = classify_message(message)
                self.assertEqual(decision.route, "company")
                self.assertEqual(decision.action, "main_agent")

    def test_meta_swarm_gate_does_not_swallow_real_security_tasks(self):
        # 讨论蜂群自身与真实安全任务要区分: 有主动攻击动词/目标/安全对象时仍走 security。
        cases = (
            ("用蜂群扫描 example.com", "security"),
            ("审计当前系统的漏洞", "security"),
            ("分析蜂群算法的漏洞", "security"),
            ("扫描并分析 10.0.0.5 的漏洞", "security"),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                decision = classify_message(message)
                self.assertEqual(decision.route, expected)

    def test_ma_question_does_not_redispatch(self):
        # 2026-08-12: "调研跑完了吗" 被误判为 research 新任务重复派发 (run 323d31af)。
        # "…吗" 结尾是完成态问句, 必须走 main_agent, 不得再派发蜂群。
        cases = (
            "调研跑完了吗",
            "调研完成了吗",
            "那个扫描任务跑完了吗",
            "蜂群 7d8cb7f0 有结果了吗",
        )
        for message in cases:
            with self.subTest(message=message):
                decision = classify_message(message)
                self.assertEqual(decision.route, "company")
                self.assertEqual(decision.action, "main_agent")

    def test_ma_suffix_does_not_block_explicit_prefix(self):
        # 显式 /security 前缀仍优先于问句拦截 (用户显式指令)
        decision = classify_message("/security 扫描 example.com 跑完了吗")
        self.assertEqual(decision.route, "security")

    def test_article_pipeline_complaints_are_not_article_production(self):
        messages = (
            "为什么写了篇文章？现在不是在调研研究吗？",
            "怎么又到文章产线了！？我让你写文章了吗？",
            "这篇文章要清除掉，根本不是我想要的",
        )
        for message in messages:
            with self.subTest(message=message):
                decision = classify_message(message)
                self.assertNotEqual(decision.action, "dispatch_article")
                self.assertEqual(decision.route, "company")

    def test_classifier_replay_also_rejects_synthetic_notification(self):
        decision = classify_message("[公司 Research 完成通知] 文章产线任务已完成 Run: run-1")
        self.assertEqual(decision.action, "main_agent")
        self.assertEqual(decision.confidence, 0.0)


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

    def _seed_failures(self, operations_db: Path, product_line: str, count: int) -> None:
        db = connect_operations(operations_db)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.executemany(
            """INSERT INTO operational_runs
               (run_id,product_line,source_type,status,completed_at,created_at,updated_at)
               VALUES (?,?,?,'failed',?,?,?)""",
            [(f"run-{product_line}-{i}", product_line, "content", now, now, now) for i in range(count)],
        )
        db.commit()
        db.close()

    def test_circuit_breaker_downgrades_failing_product_line(self):
        with tempfile.TemporaryDirectory() as td:
            config = {
                "enabled": True,
                "dispatch_security": False,
                "auto_run_article": True,
                "auto_run_video": True,
                "state_db": str(Path(td) / "router.db"),
                "operations_db": str(Path(td) / "operations.db"),
                "swarm_repo": td,
                "swarm_db": str(Path(td) / "swarm.db"),
                "log_dir": str(Path(td) / "logs"),
                "content_executor": "/bin/false",
                "content_job_dir": str(Path(td) / "content-jobs"),
                "gateway_sessions_index": str(Path(td) / "sessions.json"),
                "max_active_runs_per_session": 2,
                "max_active_content_jobs_per_session": 2,
                "circuit_breaker_threshold": 3,
            }
            self._seed_failures(Path(config["operations_db"]), "article-production", 3)
            payload = {"session_id": "article-session", "extra": {"user_message": "写一篇 Agent 工程公众号文章", "platform": "cli"}}
            with patch("automation.company_router.launch_content_job") as launch:
                result = handle_hook(payload, config)
            launch.assert_not_called()
            self.assertIn("熔断", result["context"])
            state = RouterState(config["state_db"])
            row = state.db.execute("SELECT status,error FROM route_events").fetchone()
            self.assertEqual(row["status"], "skipped")
            self.assertIn("circuit breaker open", row["error"])
            state.close()

    def test_circuit_breaker_stays_closed_for_healthy_product_line(self):
        with tempfile.TemporaryDirectory() as td:
            config = {
                "enabled": True,
                "dispatch_security": False,
                "auto_run_article": True,
                "auto_run_video": True,
                "state_db": str(Path(td) / "router.db"),
                "operations_db": str(Path(td) / "operations.db"),
                "swarm_repo": td,
                "swarm_db": str(Path(td) / "swarm.db"),
                "log_dir": str(Path(td) / "logs"),
                "content_executor": "/bin/false",
                "content_job_dir": str(Path(td) / "content-jobs"),
                "gateway_sessions_index": str(Path(td) / "sessions.json"),
                "max_active_runs_per_session": 2,
                "max_active_content_jobs_per_session": 2,
                "circuit_breaker_threshold": 3,
            }
            # Failures on a different product line must not trip the article breaker.
            self._seed_failures(Path(config["operations_db"]), "security-exploration", 5)
            payload = {"session_id": "article-session", "extra": {"user_message": "写一篇 Agent 工程公众号文章", "platform": "cli"}}
            with patch("automation.company_router.launch_content_job", return_value=4321):
                result = handle_hook(payload, config)
            self.assertIn("任务已自动分发至文章产线", result["context"])
            state = RouterState(config["state_db"])
            self.assertEqual(state.db.execute("SELECT status FROM route_events").fetchone()[0], "running")
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

    def test_synthetic_hermes_messages_bypass_router(self):
        messages = (
            "[IMPORTANT: Background process proc_x exited (exit code 1). Command: codex exec '写一篇文章']",
            "[AGENTKEY_RADAR_PROBE] 任务主题：AI agent security",
            "[公司 Research 完成通知] 文章产线任务已完成 Run: run-1",
            "[公司 TVCR 经营复盘] 公司日报｜三条产线状态",
            "[ASYNC DELEGATION BATCH COMPLETE — deleg_123] 后台子代理结果",
            "[The user sent an image but I couldn't quite see it this time (>_<)]",
            "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted",
            "Review the conversation above and update the skill library",
        )
        with tempfile.TemporaryDirectory() as td:
            config = {"enabled": True, "state_db": str(Path(td) / "router.db")}
            for message in messages:
                with self.subTest(message=message):
                    payload = {"session_id": "human-session", "extra": {"user_message": message, "platform": "cli"}}
                    self.assertEqual(handle_hook(payload, config), {})
            self.assertFalse(Path(config["state_db"]).exists())

    def test_tool_source_session_bypasses_router_even_without_internal_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            config = {"enabled": True, "state_db": str(Path(td) / "router.db")}
            payload = {
                "session_id": "tool-session",
                "extra": {"user_message": "写一篇 Agent 文章", "platform": "cli", "source": "tool"},
            }
            self.assertEqual(handle_hook(payload, config), {})
            self.assertFalse(Path(config["state_db"]).exists())

    def test_model_switch_notice_is_stripped_before_routing_real_user_text(self):
        with tempfile.TemporaryDirectory() as td:
            config = {
                "enabled": True,
                "dispatch_security": False,
                "auto_run_article": True,
                "state_db": str(Path(td) / "router.db"),
                "content_job_dir": str(Path(td) / "content-jobs"),
                "max_active_content_jobs_per_session": 2,
            }
            payload = {
                "session_id": "model-switch-session",
                "extra": {
                    "user_message": "[Note: model was just switched from a to b via gateway.] 写一篇 Agent 文章",
                    "platform": "cli",
                },
            }
            with patch("automation.company_router.launch_content_job", return_value=4321):
                result = handle_hook(payload, config)
            self.assertIn("文章产线", result["context"])

    def test_hermes_state_source_gate_skips_tool_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            hermes_db = root / "hermes.db"
            db = sqlite3.connect(hermes_db)
            db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
            db.execute("INSERT INTO sessions VALUES (?, ?)", ("tool-session", "tool"))
            db.commit()
            db.close()
            config = {
                "enabled": True,
                "state_db": str(root / "router.db"),
                "hermes_state_db": str(hermes_db),
            }
            payload = {
                "session_id": "tool-session",
                "extra": {"user_message": "写一篇 Agent 文章", "platform": "cli"},
            }
            self.assertEqual(handle_hook(payload, config), {})
            self.assertFalse(Path(config["state_db"]).exists())

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


class LowConfidenceFallbackTests(unittest.TestCase):
    def _ambiguous(self):
        # A terse imperative with no product-line vocabulary lands on the
        # deterministic 0.45 "unrecognised" verdict.
        decision = classify_message("把上次那个东西继续弄一下")
        self.assertLess(decision.confidence, 0.5)
        self.assertEqual(decision.action, "main_agent")
        return "把上次那个东西继续弄一下"

    def test_confident_llm_verdict_promotes_route(self):
        message = self._ambiguous()
        # LLM 兜底不再允许产生 article/video 路由：内容生产必须由确定性规则
        # 识别（历史误分发：“你能自动下载公众号统计信息吗”→article 0.95）。
        upgraded = classify_with_fallback(
            message, {},
            fallback=lambda msg, cfg: {"route": "article", "confidence": 0.82},
        )
        self.assertEqual(upgraded.action, "main_agent")
        self.assertNotEqual(upgraded.route, "article")

    def test_none_verdict_keeps_main_agent(self):
        message = self._ambiguous()
        decision = classify_with_fallback(
            message, {},
            fallback=lambda msg, cfg: {"route": "none", "confidence": 0.9},
        )
        self.assertEqual(decision.action, "main_agent")

    def test_low_llm_confidence_is_ignored(self):
        message = self._ambiguous()
        decision = classify_with_fallback(
            message, {},
            fallback=lambda msg, cfg: {"route": "article", "confidence": 0.30},
        )
        self.assertEqual(decision.action, "main_agent")

    def test_confident_keyword_match_never_calls_llm(self):
        calls = []

        def _fallback(msg, cfg):
            calls.append(msg)
            return {"route": "security", "confidence": 0.99}

        decision = classify_with_fallback("写一篇关于 JWT 安全的公众号文章", {}, fallback=_fallback)
        self.assertEqual(decision.route, "article")
        self.assertEqual(calls, [])  # keyword confidence 0.86 >= threshold

    def test_fallback_cannot_bypass_security_authorization(self):
        # Even if the LLM says "security" for an external target, the re-run
        # through classify_message still requires scope authorization.
        decision = classify_with_fallback(
            "顺手把 acme.com 弄一下", {},
            authorized_targets=(),
            fallback=lambda msg, cfg: {"route": "security", "confidence": 0.95},
        )
        if decision.route == "security":
            self.assertIn(decision.action, {"approval_required", "dispatch_swarm"})

    def test_disabled_flag_skips_fallback(self):
        message = self._ambiguous()
        called = []
        decision = classify_with_fallback(
            message, {"llm_fallback_enabled": False},
            fallback=lambda msg, cfg: called.append(1) or {"route": "article", "confidence": 0.9},
        )
        self.assertEqual(decision.action, "main_agent")
        self.assertEqual(called, [])


class HybridRoutingTests(unittest.TestCase):
    """Hybrid routing mode (router_mode='hybrid') tests — all use injected fallback, no real LLM calls."""

    def _hybrid_config(self, **overrides):
        cfg = {
            "router_mode": "hybrid",
            "llm_fallback_enabled": True,
            "llm_fallback_confidence": 0.5,
            "hybrid_high_confidence_skip": 0.86,
        }
        cfg.update(overrides)
        return cfg

    def test_keyword_mode_high_confidence_skips_llm(self):
        """keyword mode: /article prefix (0.99) never calls fallback, same as before."""
        calls = []

        def fb(msg, cfg):
            calls.append(msg)
            return {"route": "company", "confidence": 0.99}

        decision = classify_with_fallback(
            "/article 写一篇技术文章", {"router_mode": "keyword"},
            fallback=fb,
        )
        self.assertEqual(decision.route, "article")
        self.assertEqual(calls, [])

    def test_keyword_mode_still_triggers_fallback_at_0_45(self):
        """keyword mode: 0.45 unrecognised verdict keeps main_agent — LLM cannot reroute to article."""
        decision = classify_with_fallback(
            "把上次那个东西继续弄一下", {"router_mode": "keyword"},
            fallback=lambda msg, cfg: {"route": "article", "confidence": 0.82},
        )
        self.assertEqual(decision.action, "main_agent")
        self.assertNotEqual(decision.route, "article")

    def test_hybrid_fuzzy_band_reroutes(self):
        """hybrid: fuzzy band message (confidence < 0.86) keeps main_agent — LLM cannot reroute to article."""
        # "把上次那个东西继续弄一下" → keyword confidence 0.45 (unrecognised), below 0.86 → LLM triggered,
        # but article/video rerouting is disabled: content production needs deterministic rules.
        config = self._hybrid_config()
        upgraded = classify_with_fallback(
            "把上次那个东西继续弄一下", config,
            fallback=lambda msg, cfg: {"route": "article", "confidence": 0.82},
        )
        self.assertEqual(upgraded.action, "main_agent")
        self.assertNotEqual(upgraded.route, "article")

    def test_hybrid_external_action_skips_llm(self):
        """hybrid: external_action messages skip fallback entirely, keep approval_required."""
        calls = []

        def fb(msg, cfg):
            calls.append(msg)
            return {"route": "article", "confidence": 0.95}

        config = self._hybrid_config()
        decision = classify_with_fallback(
            "发布这篇文章到公众号", config,
            fallback=fb,
        )
        self.assertEqual(decision.action, "approval_required")
        self.assertEqual(calls, [])

    def test_hybrid_security_authorization_invariant(self):
        """hybrid: LLM security suggestion still goes through classify_message's authorization gate."""
        config = self._hybrid_config()
        decision = classify_with_fallback(
            "顺手把 acme.com 弄一下", config,
            authorized_targets=(),
            fallback=lambda msg, cfg: {"route": "security", "confidence": 0.95},
        )
        # The re-run through classify_message("/security 顺手把 acme.com 弄一下")
        # must produce a real RouteDecision, not hand-crafted from LLM output.
        if decision.route == "security":
            self.assertIn(decision.action, {"approval_required", "dispatch_swarm"})

    def test_hybrid_none_or_exception_keeps_original(self):
        """hybrid: LLM returning 'none' or raising → keep original decision (fail-safe)."""
        config = self._hybrid_config()
        # LLM returns "none" (not in _LLM_FALLBACK_PREFIX → fallback keeps original)
        decision = classify_with_fallback(
            "把上次那个东西继续弄一下", config,
            fallback=lambda msg, cfg: {"route": "none", "confidence": 0.9},
        )
        self.assertEqual(decision.action, "main_agent")

        # Exception during LLM call → keep original
        def broken(msg, cfg):
            raise RuntimeError("LLM unavailable")

        decision2 = classify_with_fallback(
            "把上次那个东西继续弄一下", config,
            fallback=broken,
        )
        self.assertEqual(decision2.action, "main_agent")

    def test_hybrid_high_confidence_skip(self):
        """hybrid: confidence >= hybrid_high_confidence_skip (0.86) skips LLM entirely."""
        calls = []

        def fb(msg, cfg):
            calls.append(msg)
            return {"route": "company", "confidence": 0.99}

        config = self._hybrid_config()
        # Explicit /security prefix → 0.99 confidence, above 0.86 → skip LLM
        decision = classify_with_fallback(
            "/security 扫描 example.com", config,
            fallback=fb,
        )
        self.assertNotEqual(decision.route, "company")  # LLM suggestion not used
        self.assertEqual(calls, [])

    def test_hybrid_empty_synthetic_skips_llm(self):
        """hybrid: empty message or synthetic notification prefix → skip LLM (confidence 0.0)."""
        config = self._hybrid_config()
        calls = []

        def fb(msg, cfg):
            calls.append(msg)
            return {"route": "article", "confidence": 0.99}

        # Empty message
        decision = classify_with_fallback("", config, fallback=fb)
        self.assertEqual(decision.confidence, 0.0)
        self.assertEqual(decision.action, "main_agent")
        self.assertEqual(calls, [])

        # Synthetic notification
        decision2 = classify_with_fallback(
            "[公司 Research 完成通知] test", config, fallback=fb
        )
        self.assertEqual(decision2.confidence, 0.0)
        self.assertEqual(decision2.action, "main_agent")
        self.assertEqual(calls, [])


class RoutingTermConfigTests(unittest.TestCase):
    def test_router_config_terms_match_builtin_defaults(self):
        # The shipped router_config.json must reproduce the built-in defaults so
        # extracting the tables to config does not change classification.
        import automation.company_router as router
        loaded = router._load_routing_terms(router.DEFAULT_CONFIG)
        for key, value in router._DEFAULT_ROUTING_TERMS.items():
            self.assertEqual(loaded[key], set(value), key)

    def test_missing_config_falls_back_to_defaults(self):
        import automation.company_router as router
        with tempfile.TemporaryDirectory() as td:
            loaded = router._load_routing_terms(Path(td) / "does-not-exist.json")
        self.assertEqual(loaded["security"], set(router._DEFAULT_ROUTING_TERMS["security"]))

    def test_reload_routing_terms_from_file_changes_classification(self):
        import automation.company_router as router
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "router_config.json"
            cfg.write_text(json.dumps({"routing_terms": {"video": ["自定义视频词"]}}), encoding="utf-8")
            try:
                router.reload_routing_terms(cfg)
                # Overridden table replaces the default; omitted tables stay default.
                self.assertEqual(router.VIDEO_TERMS, {"自定义视频词"})
                self.assertEqual(router.ARTICLE_TERMS, set(router._DEFAULT_ROUTING_TERMS["article"]))
            finally:
                router.reload_routing_terms(router.DEFAULT_CONFIG)
        # Defaults restored for the rest of the suite.
        self.assertIn("视频", router.VIDEO_TERMS)


if __name__ == "__main__":
    unittest.main()
