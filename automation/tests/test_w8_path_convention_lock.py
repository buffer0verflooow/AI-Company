"""W8-b 跨仓锁:路径口径统一(用户裁定;FINDINGS-4-5 D2)。

事实(live run 4):`apk` MCP 服务只认绝对路径,传相对路径 ⇒
`ValueError: invalid apk path: glasses-debug.apk`;而 G1 之后 `sh.run` 的相对
路径基准 = 任务产物根 ⇒ 两个工具面口径相反,错误文案不可自纠。

裁定:`apk` MCP 接受绝对路径或相对产物根的路径(与 `sh.run` 同口径),约定写进
**工具描述**(不只写在任务书),错误文案给出期望形状与示例。

本测试 = 公司侧跨仓锁(与 `test_w6_capabilities.ClosedSetParityTests` 同法):
  1. swarm `sh.run`/`mcp.call` 工具描述含路径约定(锁文本,防回归);
  2. swarm `apk` MCP 服务确实按产物根解析相对路径,根外仍拒且文案可操作;
  3. 绝对路径逐字兼容(不要求落在产物根内)。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
_AGENT_RUNTIME = Path(SWARM_REPO) / "src" / "swarm_v2" / "agent_runtime.py"
_APK_SERVER = Path(SWARM_REPO) / "scripts" / "apk_tools_server.py"


def _load_agent_runtime():
    inserted = str(SWARM_REPO) not in sys.path
    if inserted:
        sys.path.insert(0, SWARM_REPO)
    try:
        from src.swarm_v2 import agent_runtime
    finally:
        if inserted and SWARM_REPO in sys.path:
            sys.path.remove(SWARM_REPO)
    return agent_runtime


def _load_apk_server():
    spec = importlib.util.spec_from_file_location("w8_apk_lock_server", _APK_SERVER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class PathConventionLockTests(unittest.TestCase):
    def setUp(self):
        if not _AGENT_RUNTIME.exists() or not _APK_SERVER.exists():
            self.skipTest("swarm 检出不在本机")

    def test_swarm_tool_descriptions_state_path_convention(self):
        ar = _load_agent_runtime()
        self.assertIn(ar.PATH_CONVENTION_SH_RUN,
                      ar.TOOL_REGISTRY["sh.run"].summary)
        self.assertIn(ar.PATH_CONVENTION_MCP,
                      ar.TOOL_REGISTRY["mcp.call"].summary)

    def test_apk_server_resolves_relative_to_product_root(self):
        mod = _load_apk_server()
        self.assertIn("绝对路径", mod.APK_PATH_CONVENTION)
        self.assertIn("产物根", mod.APK_PATH_CONVENTION)
        self.assertIn("glasses-debug.apk", mod.APK_PATH_CONVENTION)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            apk = root / "demo.apk"
            with zipfile.ZipFile(apk, "w") as zf:
                zf.writestr("classes.dex", b"dex-bytes")
            old = os.environ.get("SWARM_REPO_ROOT")
            os.environ["SWARM_REPO_ROOT"] = str(root)
            try:
                self.assertEqual(mod._check_apk("demo.apk"), apk.resolve())
                # 绝对路径逐字兼容(不要求落在产物根内)
                self.assertEqual(mod._check_apk(str(apk)), apk.resolve())
                with self.assertRaises(ValueError) as ctx:
                    mod._check_apk("../demo.apk")
                msg = str(ctx.exception)
                self.assertIn("产物根", msg)
                self.assertIn("绝对路径", msg)
                self.assertIn("invalid apk path", msg)
            finally:
                if old is None:
                    os.environ.pop("SWARM_REPO_ROOT", None)
                else:
                    os.environ["SWARM_REPO_ROOT"] = old


if __name__ == "__main__":
    unittest.main()
