"""`_v2_subprocess_env` 盐兜底单测(2026-09-16 启用配套)。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import _v2_subprocess_env

SALT = "a" * 64


class V2SaltFallbackTests(unittest.TestCase):
    def test_process_env_wins(self):
        """进程已有盐 ⇒ 返回 None(不覆盖 launcher 提供的环境)。"""
        with patch.dict(os.environ, {"SWARM_CLIENT_SALT": SALT}):
            self.assertIsNone(_v2_subprocess_env(["swarmctl"]))

    def test_fallback_reads_env_file(self):
        """进程无盐 + ~/.company-env 有 ⇒ 注入该值(systemd/cron 兜底)。"""
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".company-env").write_text(
                "# 注释\nSWARM_CLIENT_SALT=" + SALT + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"HOME": home}, clear=False):
                os.environ.pop("SWARM_CLIENT_SALT", None)
                env = _v2_subprocess_env(["swarmctl"])
            self.assertIsNotNone(env)
            self.assertEqual(env["SWARM_CLIENT_SALT"], SALT)

    def test_no_salt_anywhere_returns_none(self):
        """两处都无盐 ⇒ None(子进程沿用环境;v2 侧照旧 fail-closed,不伪造盐)。"""
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"HOME": home}, clear=False):
                os.environ.pop("SWARM_CLIENT_SALT", None)
                self.assertIsNone(_v2_subprocess_env(["swarmctl"]))

    def test_empty_value_in_file_ignored(self):
        """文件里盐为空 ⇒ 视为没有(不注入空值绕过 fail-closed)。"""
        with tempfile.TemporaryDirectory() as home:
            (Path(home) / ".company-env").write_text("SWARM_CLIENT_SALT=\n", encoding="utf-8")
            with patch.dict(os.environ, {"HOME": home}, clear=False):
                os.environ.pop("SWARM_CLIENT_SALT", None)
                self.assertIsNone(_v2_subprocess_env(["swarmctl"]))


if __name__ == "__main__":
    unittest.main()
