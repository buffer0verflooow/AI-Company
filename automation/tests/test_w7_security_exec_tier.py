"""W7-b 回归:公司侧安全线 worker 启动档位升 exec。

用户批准的 batchW7 派工书 §2:
  ① 安全线启动 argv 逐字含 `--permission exec`(内容线一字不动仍 `write`);
  ② 任务书声明的 `required_capabilities` 需要 exec 时,启动档位必须覆盖 ⇒
     **发布前**显式拒绝不一致(不静默发布后被 worker 零 token 拒跑);
  ③ `agent_runtime_exec` 开关=0 ⇒ exec 档启动在起进程前被拒(三重门之②;
     幂等文案含恢复路径);
  ④ 能力→档位映射 / 开关名 / 档位偏序与 swarm 侧单一来源跨仓对拍。

全部离线:swarmctl 与 Popen 均被 monkeypatch,不接触真实库/身份/进程。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import (
    _V2_CAPABILITY_PERMISSION,
    _V2_EXEC_SWITCH,
    _V2_PERMISSION_ORDER,
    _V2_SECURITY_WORKER_PERMISSION,
    build_v2_content_worker_cmd,
    build_v2_security_worker_cmd,
    capability_permission_gap,
    classify_message,
    launch_v2_security_worker,
    security_job_path,
    security_worker_permission_gap,
    submit_security_v2,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
SECURITY_MESSAGE = "分析本机 APK 逆向报告中的认证逻辑"
SEC_RUN = "company-vuln-abcdef123456"
CONT_RUN = "company-content-abcdef123456"


def _config(td, **extra):
    config = {
        "enabled": True,
        "dispatch_security": False,
        "dispatch_research": False,
        "auto_run_article": True,
        "state_db": str(Path(td) / "router.db"),
        "swarm_repo": SWARM_REPO,
        "swarm_v2_db": str(Path(td) / "swarm_v2.db"),
        "swarm_v2_agent": "content-writer-1",
        "swarm_v2_judge": "content-judge-1",
        "swarm_v2_security_agent": "vuln-executor-1",
        "swarm_v2_security_judge": "vuln-judge-1",
        "swarm_v2_gray": {
            "enabled": True, "run_types": ["vuln"], "task_types": [],
            "ratio_pct": 100, "client_source": "company-router",
        },
        "log_dir": str(Path(td) / "logs"),
        "content_job_dir": str(Path(td) / "content-jobs"),
    }
    config.update(extra)
    return config


def _perm(cmd):
    return cmd[cmd.index("--permission") + 1]


class WorkerTierTests(unittest.TestCase):
    def test_security_worker_permission_is_exec(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            cmd = build_v2_security_worker_cmd(config, SEC_RUN)
            self.assertEqual(_perm(cmd), "exec")
            self.assertEqual(_V2_SECURITY_WORKER_PERMISSION, "exec")
            # 其余 argv 形状不回归
            self.assertEqual(cmd[2], "worker")
            self.assertIn("--agent-runtime", cmd)
            self.assertNotIn("--executor-command", cmd)

    def test_content_worker_permission_unchanged_write(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            cmd = build_v2_content_worker_cmd(config, CONT_RUN)
            self.assertEqual(_perm(cmd), "write")


class CapabilityTierAlignmentTests(unittest.TestCase):
    def test_gap_helper(self):
        self.assertEqual(capability_permission_gap([], "write"), [])
        self.assertEqual(capability_permission_gap(["command"], "exec"), [])
        self.assertEqual(capability_permission_gap(["command", "mcp"], "exec"), [])
        self.assertEqual(capability_permission_gap(["command"], "write"), ["command"])
        self.assertEqual(capability_permission_gap(["command", "mcp"], "read-only"),
                         ["command", "mcp"])
        with self.assertRaises(ValueError):
            capability_permission_gap(["command"], "root")
        with self.assertRaises(ValueError):
            capability_permission_gap(["shell"], "exec")

    def test_security_gap_uses_single_source_permission(self):
        self.assertEqual(security_worker_permission_gap(["command"]), [])
        self.assertEqual(security_worker_permission_gap(["mcp"], "write"), ["mcp"])

    def _submit(self, config, **kwargs):
        decision = classify_message(SECURITY_MESSAGE)
        gray = {"hit": True, "run_type": "vuln", "task_type": "analyze"}
        with patch("automation.company_router.v2_swarm_command",
                   side_effect=[{"run_id": "x"}, {"task_id": "t-1"}]) as v2cli:
            out = submit_security_v2(
                config, decision=decision, message=SECURITY_MESSAGE,
                session_id="sess-1", platform="cli", gray=gray, **kwargs)
        return out, v2cli

    def test_aligned_capabilities_publish_ok(self):
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = self._submit(
                _config(td),
                task_book={"required_capabilities": ["command"]})
            publish = v2cli.call_args_list[1].args
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["required_capabilities"], ["command"])

    def test_mismatch_rejected_before_publish(self):
        # 启动档位仍是 write(能力需要 exec)⇒ 发布前显式拒绝,零 CLI 调用
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            decision = classify_message(SECURITY_MESSAGE)
            gray = {"hit": True, "run_type": "vuln", "task_type": "analyze"}
            with patch("automation.company_router._V2_SECURITY_WORKER_PERMISSION",
                       "write"), \
                    patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError):
                    submit_security_v2(
                        config, decision=decision, message=SECURITY_MESSAGE,
                        session_id="s", platform="cli", gray=gray,
                        task_book={"required_capabilities": ["command"]})
                v2cli.assert_not_called()

    def test_mismatch_via_body_hint_also_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            decision = classify_message(SECURITY_MESSAGE)
            gray = {"hit": True, "run_type": "vuln", "task_type": "analyze"}
            with patch("automation.company_router._V2_SECURITY_WORKER_PERMISSION",
                       "write"), \
                    patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError):
                    submit_security_v2(
                        config, decision=decision, message=SECURITY_MESSAGE,
                        session_id="s", platform="cli", gray=gray,
                        task_book={"runtime_brief": "用 sh.run 计算 sha256"})
                v2cli.assert_not_called()


class ExecSwitchGateTests(unittest.TestCase):
    def _switch_rows(self, enabled):
        return {"policy": "market",
                "switches": [{"name": _V2_EXEC_SWITCH, "enabled": enabled}]}

    def test_switch_off_refuses_launch_zero_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            with patch("automation.company_router.swarm_command",
                       return_value=self._switch_rows(0)) as sc, \
                    patch("automation.company_router.subprocess.Popen") as popen:
                with self.assertRaises(RuntimeError) as cm:
                    launch_v2_security_worker(config, SEC_RUN)
            popen.assert_not_called()
            sc.assert_called_once()
            self.assertFalse(security_job_path(config, SEC_RUN).exists())
            self.assertFalse(Path(config["log_dir"]).exists())
            msg = str(cm.exception)
            self.assertIn(_V2_EXEC_SWITCH, msg)
            self.assertIn("switch on agent_runtime_exec", msg)   # 恢复路径
            self.assertIn("三重门", msg)

    def test_switch_off_message_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            msgs = []
            with patch("automation.company_router.swarm_command",
                       return_value=self._switch_rows(0)), \
                    patch("automation.company_router.subprocess.Popen"):
                for _ in range(2):
                    with self.assertRaises(RuntimeError) as cm:
                        launch_v2_security_worker(config, SEC_RUN)
                    msgs.append(str(cm.exception))
            self.assertEqual(msgs[0], msgs[1])

    def test_switch_unknown_row_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            with patch("automation.company_router.swarm_command",
                       return_value={"switches": [{"name": "other", "enabled": 1}]}), \
                    patch("automation.company_router.subprocess.Popen") as popen:
                with self.assertRaises(RuntimeError):
                    launch_v2_security_worker(config, SEC_RUN)
            popen.assert_not_called()

    def test_switch_on_launches_with_exec(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            with patch("automation.company_router.swarm_command",
                       return_value=self._switch_rows(1)), \
                    patch("automation.company_router.subprocess.Popen") as popen:
                popen.return_value.pid = 4321
                pid = launch_v2_security_worker(config, SEC_RUN)
            self.assertEqual(pid, 4321)
            popen.assert_called_once()
            cmd = popen.call_args.args[0]
            self.assertEqual(_perm(cmd), "exec")
            self.assertTrue(security_job_path(config, SEC_RUN).exists())


class ClosedSetParityTests(unittest.TestCase):
    def test_company_mirrors_swarm_single_source(self):
        repo = Path(SWARM_REPO)
        if not (repo / "src" / "swarm_v2" / "agent_runtime.py").exists():
            self.skipTest("swarm 检出不在本机")
        inserted = str(repo) not in sys.path
        if inserted:
            sys.path.insert(0, str(repo))
        try:
            from src.swarm_v2 import agent_runtime
        finally:
            if inserted and str(repo) in sys.path:
                sys.path.remove(str(repo))
        self.assertEqual(dict(_V2_CAPABILITY_PERMISSION),
                         dict(agent_runtime.CAPABILITY_PERMISSION))
        self.assertEqual(_V2_EXEC_SWITCH, agent_runtime.EXEC_SWITCH)
        self.assertEqual(dict(_V2_PERMISSION_ORDER),
                         dict(agent_runtime._PERMISSION_ORDER))


if __name__ == "__main__":
    unittest.main()
