"""D-28 回归:安全线 / research 线 v2 提交分支(灰度命中前 fail-closed)。

覆盖派工书 §2 W1-b 要求 D:
  1. run_type 映射(security→vuln / research→ops)+ task_type 映射;
  2. 安全线专用身份缺失 ⇒ 不命中(回落 fail-closed);
  3. v2 提交异常 ⇒ 记录回退原因且任务不丢(仍 fail-closed);
  4. ``focus_params`` 形状(含 ``vuln_verify``;无编造 ``exec_criteria``);
  5. worker 命令走内建运行时,不含 ``--executor-command``;
  6. ``dispatch_*=false`` ⇒ 行为与 HEAD 逐字一致(deferred);
  7. ``dispatch_*=true`` 且灰度未命中 ⇒ 仍 fail-closed(不假装能跑);
  8. 灰度命中 + 身份齐 ⇒ 走 v2 路径(全部桩化,不起真实进程)。

全部离线:CLI / worker 子进程都被 monkeypatch,不接触真实 swarm 库、run 记录、
身份注册或 job 目录;``dispatch_security`` / ``dispatch_research`` 保持 false 的事实
不在测试中改写生产配置(测试用临时 config 构造显式灰度命中)。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from automation.company_router import (
    V1_EXECUTION_SURFACE_RETIRED,
    RouterState,
    build_v2_security_worker_cmd,
    classify_message,
    handle_hook,
    security_job_path,
    submit_security_v2,
    v2_gray_decision,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
#: route=security / intent=analyze / action=dispatch_swarm(本机, 免授权门)
SECURITY_MESSAGE = "分析本机 APK 逆向报告中的认证逻辑"
#: route=research / intent=research / action=dispatch_swarm
RESEARCH_MESSAGE = "调研一下竞品 X 的技术方案"


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
        "run_types": ["vuln", "ops"],
        "task_types": [],
        "ratio_pct": 100,
        "client_source": "company-router",
    }
    block.update(overrides)
    return _gray(**block)


def _config(td, *, dispatch_security=False, dispatch_research=False, gray=None,
            security_agent="", security_judge="", agent="content-writer-1",
            judge="content-judge-1", **extra):
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
        "swarm_v2_agent": agent,
        "swarm_v2_judge": judge,
        "swarm_v2_security_agent": security_agent,
        "swarm_v2_security_judge": security_judge,
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


def _payload(session="security-session", message=SECURITY_MESSAGE):
    return {"session_id": session, "extra": {"user_message": message, "platform": "cli"}}


def _event_row(config):
    state = RouterState(config["state_db"])
    try:
        return state.db.execute(
            "SELECT action,status,error,run_id,request_id,runner_pid FROM route_events"
        ).fetchone()
    finally:
        state.close()


def _gray_of(config, message=SECURITY_MESSAGE):
    """Evaluate the security-line gate the way dispatch_swarm does."""
    decision = classify_message(message)
    with patch("automation.company_router._load_v2_company_router", return_value=None):
        return v2_gray_decision(
            config, decision, message,
            agent=config.get("swarm_v2_security_agent", ""),
            judge=config.get("swarm_v2_security_judge", ""),
        )


class RunTypeMappingTests(unittest.TestCase):
    def test_security_route_maps_to_vuln_run_type(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), security_agent="sec-exec-1",
                             security_judge="sec-judge-1")
            decision = classify_message(SECURITY_MESSAGE)
            self.assertEqual((decision.route, decision.intent), ("security", "analyze"))
            gray = _gray_of(config)
            self.assertTrue(gray["hit"])
            self.assertEqual(gray["run_type"], "vuln")
            self.assertEqual(gray["task_type"], "analyze")

    def test_research_route_maps_to_ops_run_type(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), security_agent="sec-exec-1",
                             security_judge="sec-judge-1")
            decision = classify_message(RESEARCH_MESSAGE)
            self.assertEqual((decision.route, decision.intent), ("research", "research"))
            gray = _gray_of(config, RESEARCH_MESSAGE)
            self.assertTrue(gray["hit"])
            self.assertEqual(gray["run_type"], "ops")
            self.assertEqual(gray["task_type"], "research")

    def test_content_identity_keys_do_not_gate_the_security_line(self):
        # The security line must key off its own identity config; the content
        # identities are irrelevant (and may legitimately be empty).
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), security_agent="sec-exec-1",
                             security_judge="sec-judge-1", agent="", judge="")
            gray = _gray_of(config)
            self.assertTrue(gray["hit"])


class IdentityGateTests(unittest.TestCase):
    def test_missing_security_agent_not_hit(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), security_agent="",
                             security_judge="sec-judge-1")
            gray = _gray_of(config)
            self.assertFalse(gray["hit"])
            self.assertIn("agent", gray["reason"])

    def test_missing_security_judge_not_hit(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), security_agent="sec-exec-1",
                             security_judge="")
            gray = _gray_of(config)
            self.assertFalse(gray["hit"])
            self.assertIn("judge", gray["reason"])

    def test_self_judge_not_hit(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), security_agent="same",
                             security_judge="same")
            gray = _gray_of(config)
            self.assertFalse(gray["hit"])
            self.assertEqual(gray["reason"], "v2_self_judge_forbidden")


class DispatchSwarmLifecycleTests(unittest.TestCase):
    def _run(self, config, message=SECURITY_MESSAGE, session="sw-security"):
        with patch("automation.company_router._load_v2_company_router",
                   return_value=None), \
                patch("automation.company_router.v2_swarm_command") as v2cli, \
                patch("automation.company_router.launch_v2_security_worker",
                      return_value=999) as v2w:
            result = handle_hook(_payload(session=session, message=message), config)
        return result, v2cli, v2w

    def test_dispatch_security_false_is_deferred_verbatim(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_security=False, gray=_hit_gray(),
                             security_agent="sec-exec-1", security_judge="sec-judge-1")
            result, v2cli, v2w = self._run(config)
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn("security 自动分发已禁用", result["context"])
            row = _event_row(config)
            self.assertEqual(row["action"], "dispatch_swarm")
            self.assertEqual(row["status"], "deferred")
            self.assertEqual(row["error"], "product line dispatch disabled")
            self.assertEqual(row["run_id"], "")

    def test_dispatch_research_false_is_deferred_verbatim(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_research=False, gray=_hit_gray(),
                             security_agent="sec-exec-1", security_judge="sec-judge-1")
            result, v2cli, v2w = self._run(
                config, message=RESEARCH_MESSAGE, session="sw-research-off")
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn("research 自动分发已禁用", result["context"])
            row = _event_row(config)
            self.assertEqual(row["status"], "deferred")
            self.assertEqual(row["error"], "product line dispatch disabled")
            self.assertEqual(row["run_id"], "")

    def test_dispatch_true_but_gray_miss_still_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            # gray enabled but run_types only content ⇒ security/vuln not gray
            config = _config(td, dispatch_security=True,
                             gray=_hit_gray(run_types=["content"]),
                             security_agent="sec-exec-1", security_judge="sec-judge-1")
            result, v2cli, v2w = self._run(config)
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn(V1_EXECUTION_SURFACE_RETIRED, result["context"])
            row = _event_row(config)
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["error"], V1_EXECUTION_SURFACE_RETIRED)
            self.assertEqual(row["run_id"], "")

    def test_dispatch_true_but_identity_missing_still_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_security=True, gray=_hit_gray(),
                             security_agent="", security_judge="sec-judge-1")
            result, v2cli, v2w = self._run(config)
            v2cli.assert_not_called()
            v2w.assert_not_called()
            self.assertIn(V1_EXECUTION_SURFACE_RETIRED, result["context"])
            self.assertIn("v2_agent_not_configured", result["context"])
            row = _event_row(config)
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["error"], V1_EXECUTION_SURFACE_RETIRED)

    def test_gray_hit_dispatches_v2_worker(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_security=True, gray=_hit_gray(),
                             security_agent="sec-exec-1", security_judge="sec-judge-1")
            create_result = {"run_id": "created", "status": "created"}
            publish_result = {"task_id": "t-vuln-1"}
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command",
                          side_effect=[create_result, publish_result]) as v2cli, \
                    patch("automation.company_router.launch_v2_security_worker",
                          return_value=777) as v2w, \
                    patch("automation.company_router.launch_content_job") as v1:
                result = handle_hook(_payload(session="sec-hit"), config)

            v1.assert_not_called()
            self.assertEqual(v2cli.call_count, 2)
            create = v2cli.call_args_list[0].args
            self.assertEqual(create[1:4], ("v2", "run", "create"))
            self.assertEqual(create[create.index("--run-type") + 1], "vuln")
            self.assertEqual(create[create.index("--intent") + 1], "analyze")
            self.assertEqual(create[create.index("--by") + 1], "sec-exec-1")
            self.assertEqual(create[create.index("--target-type") + 1], "unknown")
            self.assertEqual(create[create.index("--target") + 1], "company-internal")
            run_id = create[create.index("--run-id") + 1]
            self.assertTrue(run_id.startswith("company-vuln-"))
            self.assertNotIn("--role-counts", create)

            publish = v2cli.call_args_list[1].args
            self.assertEqual(publish[1:3], ("market", "publish"))
            self.assertEqual(publish[publish.index("--run-type") + 1], "vuln")
            self.assertEqual(publish[publish.index("--task-type") + 1], "analyze")
            self.assertEqual(publish[publish.index("--publisher") + 1], "client")
            self.assertEqual(publish[publish.index("--client-source") + 1], "company-router")
            self.assertEqual(publish[publish.index("--task-id") + 1], run_id)
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["vuln_verify"]["mode"], "binding-record")
            self.assertNotIn("exec_criteria", focus)
            self.assertIn("v2 灰度命中", result["context"])
            v2w.assert_called_once_with(config, run_id)

            row = _event_row(config)
            self.assertEqual(row["action"], "dispatch_swarm")
            self.assertEqual(row["run_id"], run_id)
            self.assertEqual(row["request_id"], "t-vuln-1")
            self.assertEqual(row["runner_pid"], 777)
            self.assertEqual(row["status"], "running")

    def test_research_gray_hit_uses_ops_run_type(self):
        with tempfile.TemporaryDirectory() as td:
            # W11-b:research 路由改走专属提交口(submit_research_v2)与专属身份/launch。
            # 断言面与迁移前一致(ops run_type / company-ops- run_id / worker 起用本 run);
            # 仅把桩目标从安全线切到 research 线并补 research 专用身份键。
            config = _config(td, dispatch_research=True,
                             gray=_hit_gray(run_types=["ops"]),
                             security_agent="sec-exec-1", security_judge="sec-judge-1",
                             swarm_v2_research_agent="res-exec-1",
                             swarm_v2_research_judge="res-judge-1")
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command",
                          side_effect=[{"run_id": "x"}, {"task_id": "t-ops-1"}]) as v2cli, \
                    patch("automation.company_router.launch_v2_research_worker",
                          return_value=555) as v2w:
                handle_hook(_payload(session="res-hit", message=RESEARCH_MESSAGE), config)
            create = v2cli.call_args_list[0].args
            self.assertEqual(create[create.index("--run-type") + 1], "ops")
            run_id = create[create.index("--run-id") + 1]
            self.assertTrue(run_id.startswith("company-ops-"))
            v2w.assert_called_once_with(config, run_id)
            row = _event_row(config)
            self.assertEqual(row["status"], "running")
            self.assertEqual(row["runner_pid"], 555)

    def test_v2_exception_falls_back_to_fail_closed_and_task_not_lost(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, dispatch_security=True, gray=_hit_gray(),
                             security_agent="sec-exec-1", security_judge="sec-judge-1")
            with patch("automation.company_router._load_v2_company_router",
                       return_value=None), \
                    patch("automation.company_router.v2_swarm_command",
                          side_effect=RuntimeError("v2 cli exploded")) as v2cli, \
                    patch("automation.company_router.launch_v2_security_worker") as v2w:
                result = handle_hook(_payload(session="sec-fb"), config)
            v2cli.assert_called_once()
            v2w.assert_not_called()
            self.assertIn(V1_EXECUTION_SURFACE_RETIRED, result["context"])
            self.assertIn("v2 security submit failed", result["context"])
            self.assertIn("v2 cli exploded", result["context"])
            row = _event_row(config)
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["error"], V1_EXECUTION_SURFACE_RETIRED)
            self.assertEqual(row["run_id"], "")


class FocusParamsTests(unittest.TestCase):
    def test_focus_declares_binding_record_and_never_fabricates_exec_criteria(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, gray=_hit_gray(), security_agent="sec-exec-1",
                             security_judge="sec-judge-1")
            decision = classify_message(SECURITY_MESSAGE)
            gray = {"hit": True, "run_type": "vuln", "task_type": "analyze"}
            with patch("automation.company_router.v2_swarm_command",
                       side_effect=[{"run_id": "x"}, {"task_id": "t-1"}]) as v2cli:
                submit_security_v2(
                    config, decision=decision, message=SECURITY_MESSAGE,
                    session_id="sess-1", platform="cli", gray=gray)
            publish = v2cli.call_args_list[1].args
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["vuln_verify"]["mode"], "binding-record")
            self.assertEqual(focus["vuln_verify"]["provider"], "p5-exec-verify")
            self.assertEqual(focus["company_task"], SECURITY_MESSAGE)
            self.assertEqual(focus["company_route"], "security")
            self.assertEqual(focus["company_session_id"], "sess-1")
            self.assertEqual(focus["client_source"], "company-router")
            # 红旗:任何 {"argv": [...], "expect_exit": 0} 硬编码都不允许
            blob = json.dumps(focus, ensure_ascii=False)
            self.assertNotIn("exec_criteria", focus)
            self.assertNotIn('"argv"', blob)
            self.assertNotIn('"expect_exit"', blob)

    def test_submit_rejects_non_security_route(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            decision = classify_message(RESEARCH_MESSAGE)
            decision = replace(decision, route="article")
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError):
                    submit_security_v2(
                        config, decision=decision, message="x",
                        session_id="s", platform="cli", gray={})
            v2cli.assert_not_called()


class WorkerCmdTests(unittest.TestCase):
    RUN_ID = "company-vuln-abcdef123456"

    def test_uses_builtin_runtime_and_security_identities(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td, security_agent="sec-exec-1", security_judge="sec-judge-1")
            cmd = build_v2_security_worker_cmd(config, self.RUN_ID)
            self.assertEqual(cmd[2], "worker")
            self.assertEqual(cmd[cmd.index("--db") + 1], config["swarm_v2_db"])
            # security-specific identities, NOT the content ones
            self.assertEqual(cmd[cmd.index("--agent") + 1], "sec-exec-1")
            self.assertEqual(cmd[cmd.index("--judge-by") + 1], "sec-judge-1")
            # D-22/D-25: built-in runtime, never the retired external executor
            self.assertIn("--agent-runtime", cmd)
            # W7-b:安全线启动档位升 exec(命令面/MCP 面能力需 exec;内容线仍 write)
            self.assertEqual(cmd[cmd.index("--permission") + 1], "exec")
            self.assertNotIn("--executor-command", cmd)
            self.assertNotIn(config["content_executor"], cmd)
            self.assertNotIn("--role-counts", cmd)
            self.assertEqual(cmd[cmd.index("--repo-root") + 1],
                             str(security_job_path(config, self.RUN_ID)))
            self.assertEqual(cmd[cmd.index("--max-tasks") + 1], "1")

    def test_default_security_job_root_is_content_sibling(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            path = security_job_path(config, self.RUN_ID)
            self.assertEqual(path.parent.name, "security-jobs")
            self.assertEqual(path.parent.parent, Path(config["content_job_dir"]).parent)

    def test_security_job_path_rejects_escaping_run_ids(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            for bad in ("", "../evil", "a/b", "-leading", "x" * 200):
                with self.assertRaises(ValueError, msg=bad):
                    security_job_path(config, bad)


if __name__ == "__main__":
    unittest.main()
