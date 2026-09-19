"""W15-b① 回归:worker 出网代理由配置注入(治 SSL EOF 的假设驱动修)。

派工书 §3.1 断言(全部离线/桩;真拉起证据在 §3.4 报告):
  ① 注入后 env 含 HTTPS_PROXY/HTTP_PROXY/ALL_PROXY,NO_PROXY 含 127.0.0.1;
  ② **密钥仍被剔**:base 里的 WECHAT_APP_SECRET / *_API_KEY 消失;含凭据的
     代理 URL 绝不原样注入(base 里的被黑名单剔,配置里的响亮拒绝);
  ③ 未配置 ⇒ 一个代理键都不出现(不注入假值);
  ④ 池路径:注入后仍 ⊆ 白名单,且 HTTPS_PROXY 在结果里(ALL_PROXY 未被收窄);
  ⑤ `/proc/<pid>/environ` 独立证据:真拉起子进程后代理键确实在其环境里;
  ⑥ 真 call site(launch_v2_content_worker)传给 Popen 的 env 带代理键。

改前对照:helpers 不存在、pool 白名单无 ALL_PROXY ⇒ ①②③④⑤⑥ 全红。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation._safe_io import (
    PROXY_ENV_KEYS,
    WORKER_NO_PROXY,
    apply_worker_proxy,
    pool_worker_environment,
    resolve_worker_proxy,
    scrub_environment,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
PROXY = "http://127.0.0.1:7890"


def _is_allowed(key: str) -> bool:
    from automation._safe_io import POOL_WORKER_ENV_EXACT, POOL_WORKER_ENV_PREFIXES
    return key in POOL_WORKER_ENV_EXACT or key.startswith(POOL_WORKER_ENV_PREFIXES)


class ResolveProxyTests(unittest.TestCase):
    def test_config_wins_then_env_fallback_then_empty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_worker_proxy({"swarm_worker_proxy": PROXY}), PROXY)
            self.assertEqual(resolve_worker_proxy({}), "")
            self.assertEqual(resolve_worker_proxy(None), "")
        with mock.patch.dict(os.environ, {"SWARM_WORKER_PROXY": PROXY}, clear=True):
            self.assertEqual(resolve_worker_proxy({}), PROXY)
            self.assertEqual(resolve_worker_proxy({"swarm_worker_proxy": "  "}), PROXY)

    def test_config_missing_reads_env(self):
        with mock.patch.dict(os.environ, {"SWARM_WORKER_PROXY": "http://e:1"},
                                      clear=True):
            self.assertEqual(resolve_worker_proxy({}), "http://e:1")


class ApplyProxyTests(unittest.TestCase):
    """①②③:注入语义 + 黑名单优先级 + 不注入假值。"""

    def test_1_injects_three_keys_and_no_proxy(self):
        env = apply_worker_proxy({"PATH": "/bin"}, PROXY)
        for key in PROXY_ENV_KEYS:
            self.assertEqual(env[key], PROXY)
        self.assertEqual(env["NO_PROXY"], WORKER_NO_PROXY)
        self.assertIn("127.0.0.1", env["NO_PROXY"])
        self.assertEqual(env["PATH"], "/bin")           # 既有键原样保留

    def test_2_secrets_still_dropped_and_credential_url_never_injected(self):
        scrubbed, dropped = scrub_environment({
            "PATH": "/bin",
            "WECHAT_APP_SECRET": "canary-secret",
            "OPENAI_API_KEY": "canary-key",
            "HTTPS_PROXY": "http://user:password@proxy.internal:3128",  # base 携带凭据
        })
        self.assertNotIn("WECHAT_APP_SECRET", scrubbed)
        self.assertNotIn("OPENAI_API_KEY", scrubbed)
        self.assertIn("WECHAT_APP_SECRET", dropped)
        self.assertIn("OPENAI_API_KEY", dropped)
        # base 里的含凭据代理被第 1 层黑名单剔 ⇒ 未配置注入时它不出现
        self.assertNotIn("HTTPS_PROXY", scrubbed)
        env = apply_worker_proxy(scrubbed, "")
        for key in PROXY_ENV_KEYS:
            self.assertNotIn(key, env)
        self.assertNotIn("password", json.dumps(env))
        # 配置里给含凭据代理 ⇒ 响亮拒绝,绝不原样注入
        with self.assertRaises(ValueError) as ctx:
            apply_worker_proxy({"PATH": "/bin"}, "http://user:password@proxy:1")
        self.assertIn("含凭据", str(ctx.exception))
        self.assertNotIn("password", str(ctx.exception))

    def test_3_unconfigured_injects_nothing(self):
        before = {"PATH": "/bin", "HOME": "/home/pwn"}
        env = apply_worker_proxy(before, "")
        self.assertEqual(env, before)
        self.assertEqual(apply_worker_proxy(before, None), before)
        _, dropped = pool_worker_environment(before)
        for key in PROXY_ENV_KEYS:
            self.assertNotIn(key, env)
        self.assertEqual(dropped, [])


class PoolProxyTests(unittest.TestCase):
    """④ 池路径:先 scrub → 再注入 → 再白名单(注入不被收窄)。"""

    def test_4_pool_path_keeps_injected_proxy_within_whitelist(self):
        base = {
            "PATH": "/bin",
            "HOME": "/home/pwn",
            "WECHAT_APP_SECRET": "canary",
            "FOO_UNKNOWN": "x",
        }
        env, dropped = pool_worker_environment(base, proxy_url=PROXY)
        for key in PROXY_ENV_KEYS:
            self.assertIn(key, env)
        self.assertEqual(env["NO_PROXY"], WORKER_NO_PROXY)
        self.assertIn("HTTPS_PROXY", env)
        for key in env:
            self.assertTrue(_is_allowed(key), f"{key} 不在白名单内")
        self.assertNotIn("WECHAT_APP_SECRET", env)
        self.assertNotIn("FOO_UNKNOWN", env)
        self.assertIn("WECHAT_APP_SECRET", dropped)
        self.assertIn("FOO_UNKNOWN", dropped)

    def test_pool_path_unconfigured_has_no_proxy(self):
        env, dropped = pool_worker_environment({"PATH": "/bin"})
        for key in PROXY_ENV_KEYS:
            self.assertNotIn(key, env)
        self.assertEqual(dropped, [])


class ProcEnvironTests(unittest.TestCase):
    """⑤ 独立证据:/proc/<pid>/environ 里确实有代理键。"""

    def test_5_real_child_process_carries_proxy(self):
        env = apply_worker_proxy(dict(os.environ), PROXY)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            environ_path = Path(f"/proc/{proc.pid}/environ")
            if not environ_path.exists():          # pragma: no cover - non-Linux
                self.skipTest("/proc 不可用")
            raw = environ_path.read_bytes().decode("utf-8", "replace")
            entries = dict(
                item.split("=", 1) for item in raw.split("\0") if "=" in item)
            for key in PROXY_ENV_KEYS:
                self.assertEqual(entries.get(key), PROXY)
            self.assertEqual(entries.get("NO_PROXY"), WORKER_NO_PROXY)
        finally:
            proc.kill()
            proc.wait(timeout=5)


class LaunchCallSiteTests(unittest.TestCase):
    """⑥ 真 call site:launch_v2_content_worker 的 env 带上代理键。"""

    def test_launch_content_worker_env_has_proxy(self):
        from automation import company_router as R

        captured: dict = {}

        class _FakeProc:
            pid = 4242

        def fake_popen(cmd, **kwargs):
            captured.update(kwargs)
            return _FakeProc()

        with tempfile.TemporaryDirectory() as td:
            config = {
                "enabled": True,
                "swarm_repo": SWARM_REPO,
                "swarm_v2_db": str(Path(td) / "swarm_v2.db"),
                "swarm_v2_agent": "content-writer-1",
                "swarm_v2_judge": "content-judge-1",
                "swarm_v2_gray": {"client_source": "company-router"},
                "log_dir": str(Path(td) / "logs"),
                "content_job_dir": str(Path(td) / "content-jobs"),
                "swarm_worker_proxy": PROXY,
            }
            run_id = "company-content-0000000000a1"
            with mock.patch.object(R.subprocess, "Popen", side_effect=fake_popen):
                pid = R.launch_v2_content_worker(config, run_id)
            self.assertEqual(pid, 4242)
            env = captured["env"]
            for key in PROXY_ENV_KEYS:
                self.assertEqual(env[key], PROXY)
            self.assertIn("127.0.0.1", env["NO_PROXY"])
            self.assertEqual(env["COMPANY_ROUTER_BYPASS"], "1")

    def test_launch_content_worker_unconfigured_has_no_proxy(self):
        from automation import company_router as R

        captured: dict = {}

        class _FakeProc:
            pid = 1

        def fake_popen(cmd, **kwargs):
            captured.update(kwargs)
            return _FakeProc()

        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {}, clear=True):
            config = {
                "enabled": True,
                "swarm_repo": SWARM_REPO,
                "swarm_v2_db": str(Path(td) / "swarm_v2.db"),
                "swarm_v2_agent": "content-writer-1",
                "swarm_v2_judge": "content-judge-1",
                "swarm_v2_gray": {"client_source": "company-router"},
                "log_dir": str(Path(td) / "logs"),
                "content_job_dir": str(Path(td) / "content-jobs"),
            }
            with mock.patch.object(R.subprocess, "Popen", side_effect=fake_popen):
                R.launch_v2_content_worker(config, "company-content-0000000000b2")
            for key in PROXY_ENV_KEYS:
                self.assertNotIn(key, captured["env"])


if __name__ == "__main__":
    unittest.main()
