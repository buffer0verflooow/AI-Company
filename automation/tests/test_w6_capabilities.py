"""W6/G3 回归:安全线任务书的能力声明(required_capabilities)。

事实:`write` 档工具面只有 fs.*;要求"跑命令/用 MCP 工具"的任务在 `write` 档
worker 上会 12 轮空转后判负,发布方无从得知档位能力边界。裁定:任务书要求命令面/
MCP 面 ⇒ **必须同时声明** `required_capabilities`(闭集),随 `focus_params` 下发;
v2 worker 认领后执行前确定性校验,不覆盖 ⇒ 零 token 拒跑并写明该用哪个档位。

不变量:
  1. 显式声明 ⇒ 原样随 `focus_params.required_capabilities` 下发(去重、保序);
  2. 正文提及 `sh.run`/`mcp.call`/`mcp.list` ⇒ 派生对应能力(要求用工具 = 声明能力);
  3. 闭集外/形状错 ⇒ 发布前 ValueError,零 CLI 调用(不静默丢弃);
  4. 未声明 ⇒ 现状逐字不变(focus 内无 `required_capabilities`);
  5. 闭集单一来源 = swarm 侧 `agent_runtime.CAPABILITIES`(跨仓对拍)。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import (
    _V2_CAPABILITIES,
    classify_message,
    security_required_capabilities,
    submit_security_v2,
    validate_security_required_capabilities,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
SECURITY_MESSAGE = "分析本机 APK 逆向报告中的认证逻辑"


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


def _submit(config, message=SECURITY_MESSAGE, **kwargs):
    decision = classify_message(message)
    gray = {"hit": True, "run_type": "vuln", "task_type": "analyze"}
    with patch("automation.company_router.v2_swarm_command",
               side_effect=[{"run_id": "x"}, {"task_id": "t-1"}]) as v2cli:
        out = submit_security_v2(
            config, decision=decision, message=message, session_id="sess-1",
            platform="cli", gray=gray, **kwargs)
    return out, v2cli


def _focus_of(v2cli):
    publish = v2cli.call_args_list[1].args
    return json.loads(publish[publish.index("--focus") + 1])


class DeclaredCapabilityTests(unittest.TestCase):
    def test_declared_capabilities_shipped_verbatim_dedup(self):
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(
                _config(td),
                task_book={"required_capabilities": ["command", "mcp", "command"]})
            focus = _focus_of(v2cli)
            self.assertEqual(focus["required_capabilities"], ["command", "mcp"])

    def test_tool_hint_derives_mcp_capability(self):
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(
                _config(td),
                task_book={"runtime_brief": "用 mcp.call 调 apk_info 拿包名"})
            focus = _focus_of(v2cli)
            self.assertEqual(focus["required_capabilities"], ["mcp"])

    def test_tool_hint_derives_command_capability(self):
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(
                _config(td),
                task_book={"runtime_brief": "用 sh.run 计算 APK 的 sha256"})
            focus = _focus_of(v2cli)
            self.assertEqual(focus["required_capabilities"], ["command"])

    def test_declared_and_derived_merge(self):
        caps = security_required_capabilities(
            SECURITY_MESSAGE,
            task_book={"required_capabilities": ["command"],
                       "runtime_brief": "用 mcp.call 调工具"})
        self.assertEqual(caps, ["command", "mcp"])

    def test_fenced_json_declaration_is_honoured(self):
        block = ("```json\n"
                 + json.dumps({"required_capabilities": ["mcp"],
                               "runtime_brief": "离线分析"}, ensure_ascii=False)
                 + "\n```")
        message = f"{SECURITY_MESSAGE}\n\n{block}\n"
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(_config(td), message=message)
            focus = _focus_of(v2cli)
            self.assertEqual(focus["required_capabilities"], ["mcp"])


class BadCapabilityTests(unittest.TestCase):
    def _assert_rejected(self, raw):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            decision = classify_message(SECURITY_MESSAGE)
            gray = {"hit": True, "run_type": "vuln", "task_type": "analyze"}
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError, msg=raw):
                    submit_security_v2(
                        config, decision=decision, message=SECURITY_MESSAGE,
                        session_id="s", platform="cli", gray=gray,
                        task_book={"required_capabilities": raw})
                v2cli.assert_not_called()

    def test_unknown_capability_rejected(self):
        self._assert_rejected(["shell"])

    def test_bad_shapes_rejected(self):
        for raw in ("command", [], [1], ["command", ""], {"command": True}):
            self._assert_rejected(raw)

    def test_validate_helper_direct(self):
        self.assertEqual(validate_security_required_capabilities(["mcp"]), ["mcp"])
        with self.assertRaises(ValueError):
            validate_security_required_capabilities(["rm"])


class UnchangedTests(unittest.TestCase):
    def test_no_capabilities_keeps_focus_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(_config(td))
            focus = _focus_of(v2cli)
            self.assertNotIn("required_capabilities", focus)
            self.assertEqual(focus["vuln_verify"]["mode"], "binding-record")


class ClosedSetParityTests(unittest.TestCase):
    def test_closed_set_matches_swarm_single_source(self):
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
        self.assertEqual(set(_V2_CAPABILITIES), set(agent_runtime.CAPABILITIES))


if __name__ == "__main__":
    unittest.main()
