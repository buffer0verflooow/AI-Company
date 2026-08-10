"""蜂群-公司集成测试: 防重构回归 (2026-08-10 断点教训).

背景: 08-09 17:15 起 dispatch_swarm 全部失败 —— swarm_runner.py 从
swarm-knowledge 根目录移到 scripts/ 后, company_router.launch_runner
引用未同步 (can't open file), 3 个安全 run 卡 pending。
本测试守护蜂群接入链路的路径有效性, 任何重构移动文件都会立即红灯。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # company/
sys.path.insert(0, str(REPO_ROOT))

from automation.company_router import build_runner_cmd, load_config, runner_role_counts  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent / "router_config.json"


class SwarmIntegrationPathTests(unittest.TestCase):
    """链路路径有效性 —— 本次断点的直接回归测试"""

    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cls.swarm_repo = Path(cls.config["swarm_repo"])
        cls.db_path = Path(cls.config["swarm_db"])

    def test_runner_script_exists(self):
        """swarm_runner.py 必须存在于 scripts/ (8e00a60 重构后位置)"""
        self.assertTrue(
            (self.swarm_repo / "scripts" / "swarm_runner.py").is_file(),
            "swarm_runner.py 不在 swarm-knowledge/scripts/ —— launch_runner 会失败",
        )

    def test_build_runner_cmd_points_to_existing_script(self):
        """build_runner_cmd 构造的命令指向真实文件"""
        cmd = build_runner_cmd(self.config, "test-run-1234", "analyze")
        script_path = Path(cmd[1])
        self.assertTrue(script_path.is_file(), f"cmd 引用的 runner 不存在: {script_path}")
        # 参数完整性
        self.assertIn("--run-id", cmd)
        self.assertIn("--executor-command", cmd)
        self.assertIn(self.config["executor"], cmd)
        self.assertIn("--db", cmd)
        self.assertIn(self.config["swarm_db"], cmd)

    def test_executor_exists(self):
        """swarm_hermes_executor.py (worker 执行器) 必须存在"""
        self.assertTrue(
            (self.swarm_repo / "automation" / "swarm_hermes_executor.py").is_file()
            or Path(self.config["executor"]).is_file(),
            f"executor 不存在: {self.config['executor']}",
        )

    def test_safe_io_exists(self):
        """_safe_io.py (环境清理, executor 依赖) 必须存在"""
        self.assertTrue(
            Path(self.config["executor"]).parent.joinpath("_safe_io.py").is_file(),
            "_safe_io.py 不存在 (executor 导入依赖)",
        )

    def test_swarmctl_exists(self):
        """swarmctl.py (Router 的 swarm_command 依赖) 必须存在"""
        self.assertTrue(
            (self.swarm_repo / "scripts" / "swarmctl.py").is_file(),
            "swarmctl.py 不在 swarm-knowledge/scripts/",
        )

    def test_swarm_db_exists(self):
        """swarm_knowledge.db (任务市场/知识库) 必须存在"""
        self.assertTrue(
            self.db_path.is_file(),
            f"swarm DB 不存在: {self.db_path}",
        )

    def test_runner_role_counts_are_valid(self):
        """role_counts 字符串必须能被 runner 解析 (key=value 对)"""
        for intent in ["recon", "exploit", "report", "analyze", "custom", "research"]:
            rc = runner_role_counts(intent)
            for part in rc.split(","):
                key, _, val = part.partition("=")
                self.assertTrue(key.strip(), f"空角色名: {rc}")
                self.assertTrue(val.strip().isdigit(), f"非法数量: {rc}")


class SwarmIntegrationSmokeTests(unittest.TestCase):
    """轻量冒烟: runner 可启动 (不执行真实任务)"""

    @classmethod
    def setUpClass(cls):
        cls.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_runner_script_is_importable_syntax(self):
        """runner 脚本至少能通过语法检查 (py_compile)"""
        runner = Path(self.config["swarm_repo"]) / "scripts" / "swarm_runner.py"
        import py_compile

        try:
            py_compile.compile(str(runner), doraise=True)
            ok = True
        except py_compile.PyCompileError as exc:
            ok = False
            self.fail(f"swarm_runner.py 语法错误: {exc}")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
