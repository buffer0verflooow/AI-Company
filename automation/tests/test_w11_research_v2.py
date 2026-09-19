"""W11-b 回归:research 路由从退役 v1 迁到 v2 专属提交口(submit_research_v2)。

派工书 §2 四条断言(全部离线,桩驱动,不接触真实库/身份/进程):
  ① 闸关(dispatch_research=false)⇒ 现状拒绝语义保留,文案含"已迁 v2"与开闸命令;
  ② 闸开(副本 config)⇒ 提交 v2 市场(run_type=ops + 闸派生字段)+ 起 worker argv 正确;
  ③ 三条线互不串档:run_type / --permission / 身份 / --repo-root 各用各的;
  ④ dispatch_security 仍 false、security 行为逐字不变(V1 文案 + argv 金样本)。

改前对照:迁移前 research 走 submit_security_v2/launch_v2_security_worker;
本文件锁定迁移后的专属提交口,同时保留既有 test_swarm_security_v2.py 的断言面。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import (
    RESEARCH_GATE_OPEN_COMMAND,
    RESEARCH_LINE_MIGRATED_TO_V2,
    RESEARCH_V2_LINE_UNAVAILABLE,
    V1_EXECUTION_SURFACE_RETIRED,
    RouterState,
    build_v2_content_worker_cmd,
    build_v2_research_worker_cmd,
    build_v2_security_worker_cmd,
    classify_message,
    handle_hook,
    research_job_path,
    security_job_path,
    submit_content_v2,
    submit_research_v2,
    submit_security_v2,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
RESEARCH_MESSAGE = "调研一下竞品 X 的技术方案"
SECURITY_MESSAGE = "分析本机 APK 逆向报告中的认证逻辑"
CONTENT_MESSAGE = "写一篇 Agent 工程公众号文章"


def _gray(**overrides):
    block = {
        "enabled": False,
        "run_types": [],
        "task_types": [],
        "ratio_pct": 0,
        "client_source": "",
    }
    block.update(overrides)
    return block


def _hit_gray(**overrides):
    block = {
        "enabled": True,
        "run_types": ["content", "vuln", "ops"],
        "task_types": [],
        "ratio_pct": 100,
        "client_source": "company-router",
    }
    block.update(overrides)
    return _gray(**block)


def _config(td, *, dispatch_security=False, dispatch_research=False, gray=None,
            content_agent="content-writer-1", content_judge="content-judge-1",
            security_agent="", security_judge="",
            research_agent="", research_judge="", **extra):
    config = {
        "enabled": True,
        "dispatch_security": dispatch_security,
        "dispatch_research": dispatch_research,
        "auto_run_security": False,
        "auto_run_article": True,
        "auto_run_video": True,
        "auto_run_company": True,
        "state_db": str(Path(td) / "router.db"),
        "swarm_repo": SWARM_REPO,
        "swarm_v2_db": str(Path(td) / "swarm_v2.db"),
        "swarm_v2_agent": content_agent,
        "swarm_v2_judge": content_judge,
        "swarm_v2_security_agent": security_agent,
        "swarm_v2_security_judge": security_judge,
        "swarm_v2_research_agent": research_agent,
        "swarm_v2_research_judge": research_judge,
        "swarm_v2_gray": _gray() if gray is None else gray,
        "log_dir": str(Path(td) / "logs"),
        "content_executor": str(Path(td) / "content_executor.py"),
        "content_job_dir": str(Path(td) / "content-jobs"),
        "gateway_sessions_index": str(Path(td) / "sessions.json"),
        "max_active_runs_per_session": 2,
        "max_active_content_jobs_per_session": 2,
    }
    config.update(extra)
    return config


def _payload(session, message):
    return {"session_id": session, "extra": {"user_message": message, "platform": "cli"}}


def _event_row(config):
    state = RouterState(config["state_db"])
    try:
        return state.db.execute(
            "SELECT action,status,error,run_id,request_id,runner_pid FROM route_events"
        ).fetchone()
    finally:
        state.close()


class GateClosedTests(unittest.TestCase):
    """① 闸关 ⇒ 现状拒绝语义保留,文案含"已迁 v2"与开闸命令。"""

    def test_dispatch_research_false_defers_with_v2_migration_note(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_research=False, gray=_hit_gray(),
                             research_agent="res-exec-1", research_judge="res-judge-1")
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command") as v2cli, \
                    patch("automation.company_router.launch_v2_research_worker") as v2w:
                result = handle_hook(_payload("res-off", RESEARCH_MESSAGE), config)
            # 现状语义保留:deferred + 同一 error 码 + 无任何提交/拉起
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn("research 自动分发已禁用 (dispatch_research=false)", result["context"])
            row = _event_row(config)
            self.assertEqual(row["action"], "dispatch_swarm")
            self.assertEqual(row["status"], "deferred")
            self.assertEqual(row["error"], "product line dispatch disabled")
            self.assertEqual(row["run_id"], "")
            # 新口径:已迁 v2 + 开闸命令原文
            self.assertIn("已迁 v2", result["context"])
            self.assertIn("开闸命令", result["context"])
            self.assertIn(RESEARCH_GATE_OPEN_COMMAND, result["context"])
            self.assertIn("dispatch_research", result["context"])
            # 不再是 v1 退役口径
            self.assertNotIn(V1_EXECUTION_SURFACE_RETIRED, result["context"])

    def test_submit_research_v2_refuses_loudly_when_gate_closed(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_research=False,
                             research_agent="res-exec-1", research_judge="res-judge-1")
            decision = classify_message(RESEARCH_MESSAGE)
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(RuntimeError) as ctx:
                    submit_research_v2(
                        config, decision=decision, message=RESEARCH_MESSAGE,
                        session_id="s", platform="cli",
                        gray={"hit": True, "reason": "ratio_hit"})
            v2cli.assert_not_called()
            self.assertIn(RESEARCH_LINE_MIGRATED_TO_V2, str(ctx.exception))
            self.assertIn("开闸命令", str(ctx.exception))


class GateOpenTests(unittest.TestCase):
    """② 闸开(副本 config)⇒ 提交 v2 市场 + 起 worker(argv 正确)。"""

    def _decision(self):
        return classify_message(RESEARCH_MESSAGE)

    def test_gate_open_submits_ops_run_and_launches_research_worker(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_research=True, gray=_hit_gray(),
                             research_agent="res-exec-1", research_judge="res-judge-1")
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command",
                          side_effect=[{"run_id": "created"}, {"task_id": "t-ops-9"}]) as v2cli, \
                    patch("automation.company_router.launch_v2_research_worker",
                          return_value=321) as v2w:
                result = handle_hook(_payload("res-on", RESEARCH_MESSAGE), config)

            self.assertEqual(v2cli.call_count, 2)
            create = v2cli.call_args_list[0].args
            self.assertEqual(create[1:4], ("v2", "run", "create"))
            self.assertEqual(create[create.index("--run-type") + 1], "ops")
            self.assertEqual(create[create.index("--intent") + 1], "research")
            self.assertEqual(create[create.index("--by") + 1], "res-exec-1")
            run_id = create[create.index("--run-id") + 1]
            self.assertTrue(run_id.startswith("company-ops-"))

            publish = v2cli.call_args_list[1].args
            self.assertEqual(publish[1:3], ("market", "publish"))
            self.assertEqual(publish[publish.index("--run-type") + 1], "ops")
            self.assertEqual(publish[publish.index("--task-type") + 1], "research")
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["company_route"], "research")
            self.assertEqual(focus["task_intent"], "research")
            self.assertEqual(focus["company_task"], RESEARCH_MESSAGE)
            self.assertEqual(focus["client_source"], "company-router")
            self.assertIn("vuln_verify", focus)
            self.assertIn("v2 灰度命中", result["context"])
            v2w.assert_called_once_with(config, run_id)
            row = _event_row(config)
            self.assertEqual(row["action"], "dispatch_swarm")
            self.assertEqual(row["run_id"], run_id)
            self.assertEqual(row["request_id"], "t-ops-9")
            self.assertEqual(row["runner_pid"], 321)
            self.assertEqual(row["status"], "running")

    def test_submit_research_v2_carries_task_book_derived_fields(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_research=True,
                             research_agent="res-exec-1", research_judge="res-judge-1")
            task_book = {
                "runtime_brief": "调研竞品 X,输出 report.md",
                "required_capabilities": ["command"],
                "exec_criteria": [
                    {"argv": ["grep", "-F", "-q", "competitor=X", "report.md"],
                     "expect_exit": 0},
                ],
            }
            with patch("automation.company_router.v2_swarm_command",
                       side_effect=[{"run_id": "x"}, {"task_id": "t-1"}]) as v2cli:
                out = submit_research_v2(
                    config, decision=self._decision(), message=RESEARCH_MESSAGE,
                    session_id="sess-1", platform="cli",
                    gray={"hit": True, "reason": "ratio_hit"}, task_book=task_book)
            self.assertEqual(out["_v2_run_type"], "ops")
            self.assertEqual(out["_v2_task_type"], "research")
            publish = v2cli.call_args_list[1].args
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["runtime_brief"], "调研竞品 X,输出 report.md")
            self.assertEqual(focus["required_capabilities"], ["command"])
            # 判据原样透传(校验器补 timeout 缺省,不编造 argv/expect_exit)
            self.assertEqual(len(focus["exec_criteria"]), 1)
            self.assertEqual(focus["exec_criteria"][0]["argv"],
                             task_book["exec_criteria"][0]["argv"])
            self.assertEqual(focus["exec_criteria"][0]["expect_exit"], 0)
            self.assertEqual(focus["exec_criteria"][0]["timeout"], 30)
            self.assertEqual(focus["vuln_verify"]["mode"], "exec-criteria")
            self.assertIn("deliverable_requirement", focus)

    def test_submit_research_v2_rejects_non_research_route(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_research=True,
                             research_agent="res-exec-1", research_judge="res-judge-1")
            decision = self._decision()
            from dataclasses import replace  # noqa: PLC0415 -- 局部一次性使用
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError):
                    submit_research_v2(
                        config, decision=replace(decision, route="article"),
                        message="x", session_id="s", platform="cli",
                        gray={"hit": True})
            v2cli.assert_not_called()


class ThreeLineLockTests(unittest.TestCase):
    """③ 三条线互不串档:run_type / --permission / 身份 / --repo-root。"""

    def _all_lines_config(self, td):
        return _config(
            td, dispatch_security=True, dispatch_research=True, gray=_hit_gray(),
            content_agent="content-writer-1", content_judge="content-judge-1",
            security_agent="sec-exec-1", security_judge="sec-judge-1",
            research_agent="res-exec-1", research_judge="res-judge-1")

    def test_worker_cmds_do_not_mix_identity_permission_or_root(self):
        with tempfile.TemporaryDirectory() as td:
            config = self._all_lines_config(td)
            content_id = "company-content-aaaaaaaaaaaa"
            security_id = "company-vuln-bbbbbbbbbbbb"
            research_id = "company-ops-cccccccccccc"
            content = build_v2_content_worker_cmd(config, content_id)
            security = build_v2_security_worker_cmd(config, security_id)
            research = build_v2_research_worker_cmd(config, research_id)

            # --permission:content=write,security/research=exec
            self.assertEqual(content[content.index("--permission") + 1], "write")
            self.assertEqual(security[security.index("--permission") + 1], "exec")
            self.assertEqual(research[research.index("--permission") + 1], "exec")
            # 身份各用各的
            self.assertEqual(content[content.index("--agent") + 1], "content-writer-1")
            self.assertEqual(security[security.index("--agent") + 1], "sec-exec-1")
            self.assertEqual(research[research.index("--agent") + 1], "res-exec-1")
            self.assertEqual(content[content.index("--judge-by") + 1], "content-judge-1")
            self.assertEqual(security[security.index("--judge-by") + 1], "sec-judge-1")
            self.assertEqual(research[research.index("--judge-by") + 1], "res-judge-1")
            # --repo-root 三条线各不相同(各自产物根)
            roots = {
                content[content.index("--repo-root") + 1],
                security[security.index("--repo-root") + 1],
                research[research.index("--repo-root") + 1],
            }
            self.assertEqual(len(roots), 3)
            self.assertEqual(Path(content[content.index("--repo-root") + 1]).parent.name,
                             "content-jobs")
            self.assertEqual(Path(security[security.index("--repo-root") + 1]).parent.name,
                             "security-jobs")
            self.assertEqual(Path(research[research.index("--repo-root") + 1]).parent.name,
                             "research-jobs")
            self.assertEqual(research[research.index("--repo-root") + 1],
                             str(research_job_path(config, research_id)))
            # 交叉身份绝不出现在别线 argv
            for foreign in ("sec-exec-1", "res-exec-1", "sec-judge-1", "res-judge-1"):
                self.assertNotIn(foreign, content)
            for foreign in ("content-writer-1", "res-exec-1", "content-judge-1",
                            "res-judge-1"):
                self.assertNotIn(foreign, security)
            for foreign in ("content-writer-1", "sec-exec-1", "content-judge-1",
                            "sec-judge-1"):
                self.assertNotIn(foreign, research)

    def test_submit_ports_keep_their_own_run_types(self):
        with tempfile.TemporaryDirectory() as td:
            config = self._all_lines_config(td)
            content_decision = classify_message(CONTENT_MESSAGE)
            security_decision = classify_message(SECURITY_MESSAGE)
            research_decision = classify_message(RESEARCH_MESSAGE)
            with patch("automation.company_router.v2_swarm_command",
                       side_effect=[{"run_id": "a"}, {"task_id": "ta"},
                                    {"run_id": "b"}, {"task_id": "tb"},
                                    {"run_id": "c"}, {"task_id": "tc"}]):
                out_c = submit_content_v2(
                    config, decision=content_decision, message=CONTENT_MESSAGE,
                    session_id="s", platform="cli", gray={"hit": True})
                out_s = submit_security_v2(
                    config, decision=security_decision, message=SECURITY_MESSAGE,
                    session_id="s", platform="cli", gray={"hit": True})
                out_r = submit_research_v2(
                    config, decision=research_decision, message=RESEARCH_MESSAGE,
                    session_id="s", platform="cli", gray={"hit": True})
            self.assertEqual(out_c["_v2_run_type"], "content")
            self.assertEqual(out_s["_v2_run_type"], "vuln")
            self.assertEqual(out_r["_v2_run_type"], "ops")
            self.assertTrue(out_c["run_id"].startswith("company-content-"))
            self.assertTrue(out_s["run_id"].startswith("company-vuln-"))
            self.assertTrue(out_r["run_id"].startswith("company-ops-"))


class SecurityUnchangedTests(unittest.TestCase):
    """④ dispatch_security 仍 false、security 行为逐字不变。"""

    def test_security_gate_off_is_deferred_verbatim(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_security=False, gray=_hit_gray(),
                             security_agent="sec-exec-1", security_judge="sec-judge-1")
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command") as v2cli, \
                    patch("automation.company_router.launch_v2_security_worker") as v2w:
                result = handle_hook(_payload("sec-off", SECURITY_MESSAGE), config)
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn(
                "- security 自动分发已禁用 (dispatch_security=false)，已交由主 Agent。",
                result["context"])
            self.assertNotIn("已迁 v2", result["context"])
            row = _event_row(config)
            self.assertEqual(row["status"], "deferred")
            self.assertEqual(row["error"], "product line dispatch disabled")

    def test_security_worker_cmd_golden_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_security=True,
                             security_agent="sec-exec-1", security_judge="sec-judge-1")
            run_id = "company-vuln-000000000001"
            cmd = build_v2_security_worker_cmd(config, run_id)
            sys_exec = cmd[0]
            self.assertEqual(cmd, [
                sys_exec,
                str(Path(SWARM_REPO) / "scripts" / "swarmctl.py"),
                "worker",
                "--db", config["swarm_v2_db"],
                "--agent", "sec-exec-1",
                "--judge-by", "sec-judge-1",
                "--agent-runtime",
                "--permission", "exec",
                "--repo-root", str(security_job_path(config, run_id)),
                "--max-turns", "12",
                "--max-tokens-budget", "100000",
                "--poll-interval", "5.0",
                "--max-tasks", "1",
            ])

    def test_research_unavailable_message_not_v1_retired(self):
        with tempfile.TemporaryDirectory() as td:
            # 闸开 + 灰度未命中(身份缺)⇒ research 专属 fail-closed 文案,非 V1 退役。
            config = _config(td, dispatch_research=True,
                             gray=_hit_gray(run_types=["ops"]),
                             research_agent="", research_judge="res-judge-1")
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command") as v2cli, \
                    patch("automation.company_router.launch_v2_research_worker") as v2w:
                result = handle_hook(_payload("res-miss", RESEARCH_MESSAGE), config)
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn(RESEARCH_V2_LINE_UNAVAILABLE, result["context"])
            self.assertNotIn(V1_EXECUTION_SURFACE_RETIRED, result["context"])
            row = _event_row(config)
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["error"], RESEARCH_V2_LINE_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
