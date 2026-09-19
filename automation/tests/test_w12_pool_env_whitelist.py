r"""W12 回归:常驻池 worker 拉起环境收敛(白名单 deny-by-default)。

用户 2026-09-19 裁定:W11 继承全量 ``os.environ`` 的常驻池拉起面必须收敛。
派工书 §3 断言(全部离线;不打真模型、不起真常驻进程):

  ① 收敛:未知键 + 密钥类键(FOO_SECRET / AWS_* / HERMES_* / GITHUB_* …)全剔,
     且键名出现在 ``dropped``;
  ② 必需面保留:PATH / HOME / TMPDIR / SWARM_LLM_PROVIDER / SWARM_CLIENT_SALT /
     无凭据 HTTPS_PROXY;
  ③ 两层顺序:白名单前缀内的**密钥**仍被第 1 层黑名单剔除
     (SWARM_API_SECRET / DEEPSEEK_TOKEN_X / DEEPSEEK_API_KEY);
  ④ 不注入假值:base 缺 TMPDIR ⇒ 结果无 TMPDIR;
  ⑤ spy:``supervise()`` 传给 ``launch`` 的 env ≠ ``os.environ`` 拷贝,且键集 ⊆ 白名单;
  ⑥ heartbeat/supervisor 日志落被剔**键名**(值不落)。

变异反证:把 ``pool_worker_environment`` 换成 ``dict(os.environ)`` ⇒ ①③⑤⑥
多条用例失败(实测 9 例中 7 红);把 ``supervise`` 改回
``env=dict(os.environ)`` ⇒ ⑤ 失败。

scratch 落 ``W12_SCRATCH``(默认 /home/pwn/workspace/w12-scratch),不用 /tmp。
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import swarm_pool_supervisor as sup
from automation._safe_io import (
    POOL_WORKER_ENV_EXACT,
    POOL_WORKER_ENV_PREFIXES,
    pool_worker_environment,
)

SCRATCH_ROOT = Path("/home/pwn/workspace/w12-scratch")

#: 白名单外的一批"活环境中真实存在/典型泄漏面"键名(Base 里给值)。
NON_WHITELISTED_SAMPLE = (
    "FOO_SECRET",
    "SOME_APP_CONFIG",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "HERMES_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "WECHAT_APP_ID",
    "WEIXIN_TOKEN",
    "ZENMUX_API_KEY",
    "TERMINAL_CWD",
    "SOME_APP_CONFIGURATION",
)


def _is_allowed(key: str) -> bool:
    return key in POOL_WORKER_ENV_EXACT or key.startswith(POOL_WORKER_ENV_PREFIXES)


@contextlib.contextmanager
def scratch_dir():
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(SCRATCH_ROOT)) as td:
        yield Path(td)


def _config(td: Path) -> dict:
    return {
        "enabled": True,
        "swarm_repo": "/home/pwn/workspace/research/swarm-knowledge",
        "swarm_v2_db": str(td / "swarm_v2.db"),
        "state_db": str(td / "runtime" / "company_router.db"),
        "log_dir": str(td / "runtime" / "logs"),
        "swarm_pool_size": 1,
    }


class _StubRunner:
    """`subprocess.run` replacement:pool provision/status 均成功。"""

    def __init__(self, *, size=1, registered=True):
        self.size = size
        self.registered = registered
        self.calls: list[list] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        flat = " ".join(str(c) for c in cmd)
        if "pool provision" in flat:
            self.registered = True
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=json.dumps({"provisioned": [
                    {"agent_id": f"pool-worker-{i + 1}", "roles": []}
                    for i in range(self.size)]}),
                stderr="")
        if "pool status" in flat:
            workers = [{
                "slot": i + 1, "agent_id": f"pool-worker-{i + 1}",
                "registered": self.registered, "roles": [],
                "conn_state": None, "last_heartbeat": None,
                "close_reason": None, "running_tasks": 0,
            } for i in range(self.size)]
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=json.dumps({"size": self.size, "workers": workers}),
                stderr="")
        raise AssertionError(f"unexpected command: {flat}")


class _StubLaunch:
    def __init__(self, *, pid=4321):
        self.pid = pid
        self.calls: list[tuple] = []

    def __call__(self, cmd, *, log_path, cwd, env):
        self.calls.append((list(cmd), Path(log_path), Path(cwd), dict(env)))
        return self.pid


def _deps(runner, launch):
    return sup.SupervisorDeps(
        runner=runner, launch=launch,
        process_alive=lambda pid: False,
        find_pool_processes=lambda repo: [],
        now=lambda: "2026-09-19T00:00:00+00:00",
    )


class PoolWorkerEnvironmentTests(unittest.TestCase):
    """①②③④:纯函数层面的白名单语义。"""

    def test_1_convergence_drops_unknown_and_secret_keys(self):
        base = {key: f"value-{i}" for i, key in enumerate(NON_WHITELISTED_SAMPLE)}
        base["PATH"] = "/usr/bin"
        env, dropped = pool_worker_environment(base)
        for key in NON_WHITELISTED_SAMPLE:
            self.assertNotIn(key, env, f"{key} 不该出现在收敛后的环境")
            self.assertIn(key, dropped, f"{key} 应从 dropped 可见")
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_2_required_face_is_retained(self):
        base = {
            "PATH": "/usr/bin",
            "HOME": "/home/pwn",
            "TMPDIR": "/home/pwn/workspace/.tmpdir",
            "SWARM_LLM_PROVIDER": "deepseek-official",
            "SWARM_CLIENT_SALT": "salt-canary",
            "HTTPS_PROXY": "http://proxy.internal:3128",  # 无凭据形态
            "HTTP_PROXY": "http://proxy.internal:3128",   # 前缀命中(白名单)
        }
        env, dropped = pool_worker_environment(base)
        for key in ("PATH", "HOME", "TMPDIR", "SWARM_LLM_PROVIDER",
                    "SWARM_CLIENT_SALT", "HTTPS_PROXY", "HTTP_PROXY"):
            self.assertIn(key, env, f"必需键 {key} 应保留")
            self.assertNotIn(key, dropped)

    def test_3_layer1_blacklist_wins_over_allowed_prefix(self):
        base = {
            "SWARM_API_SECRET": "s",
            "SWARM_CLIENT_SALT": "keep-me",
            "DEEPSEEK_TOKEN_X": "t",
            "DEEPSEEK_API_KEY": "k",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com/v1",
            "AUTH_TOKEN": "a",
            "HTTPS_PROXY": "http://user:password@proxy.internal:3128",  # 值含凭据
        }
        env, dropped = pool_worker_environment(base)
        # 白名单前缀内的密钥类键仍被第 1 层剔除。
        for key in ("SWARM_API_SECRET", "DEEPSEEK_TOKEN_X", "DEEPSEEK_API_KEY",
                    "AUTH_TOKEN", "HTTPS_PROXY"):
            self.assertNotIn(key, env)
            self.assertIn(key, dropped)
        # 非密钥的同前缀键保留。
        self.assertIn("SWARM_CLIENT_SALT", env)
        self.assertIn("DEEPSEEK_BASE_URL", env)

    def test_4_no_fake_values_injected(self):
        env, dropped = pool_worker_environment({"PATH": "/usr/bin"})
        self.assertNotIn("TMPDIR", env)
        self.assertNotIn("HOME", env)
        self.assertEqual(dropped, [])
        empty_env, empty_dropped = pool_worker_environment({})
        self.assertEqual(empty_env, {})
        self.assertEqual(empty_dropped, [])

    def test_5_dropped_is_sorted_unique_key_names_without_values(self):
        canary = "CANARY-VALUE-MUST-NEVER-APPEAR-42"
        base = {
            "FOO_SECRET": canary,
            "BAR_SECRET": canary,
            "PATH": "/usr/bin",
        }
        env, dropped = pool_worker_environment(base)
        self.assertEqual(dropped, sorted(set(dropped)))
        self.assertEqual(dropped, ["BAR_SECRET", "FOO_SECRET"])
        self.assertNotIn(canary, dropped)

    def test_6_every_returned_key_is_on_the_whitelist(self):
        base = {key: "v" for key in NON_WHITELISTED_SAMPLE}
        base.update({
            "PATH": "v", "HOME": "v", "SWARM_LLM_PROVIDER": "v",
            "SWARM_CLIENT_SALT": "v", "COMPANY_ROUTER_BYPASS": "1",
            "DEEPSEEK_BASE_URL": "v", "NO_PROXY": "localhost",
        })
        env, _ = pool_worker_environment(base)
        for key in env:
            self.assertTrue(_is_allowed(key), f"{key} 不在白名单内却未剔除")


class SuperviseWhitelistSpyTests(unittest.TestCase):
    """⑤⑥:supervise 拉起面 + 可观测面。"""

    def test_supervise_passes_whitelist_env_not_os_environ(self):
        canary = "SECRET-CANARY-VALUE-777"
        base = {
            "PATH": "/usr/bin",
            "SWARM_LLM_PROVIDER": "deepseek-official",
            "TMPDIR": "/home/pwn/workspace/.tmpdir",
            "FOO_SECRET": canary,
            "TERMINAL_CWD": "/home/pwn/workspace",
        }
        with scratch_dir() as td:
            config = _config(td)
            runner = _StubRunner(size=1, registered=True)
            launch = _StubLaunch(pid=4321)
            with mock.patch.dict(os.environ, base, clear=True):
                result = sup.supervise(config, size=1, deps=_deps(runner, launch))

            self.assertTrue(result["ok"])
            self.assertEqual(len(launch.calls), 1)
            passed_env = launch.calls[0][3]

            # ⑤ env ≠ os.environ 拷贝,且键集 ⊆ 白名单。
            self.assertNotEqual(passed_env, dict(base))
            self.assertEqual(set(passed_env), {"PATH", "SWARM_LLM_PROVIDER", "TMPDIR"})
            for key in passed_env:
                self.assertTrue(_is_allowed(key))
            self.assertNotIn("FOO_SECRET", passed_env)
            self.assertNotIn("TERMINAL_CWD", passed_env)

            # ⑥ heartbeat 落被剔数量 + 样本键名(只键名)。
            paths = sup.supervisor_paths(config)
            heartbeat = json.loads(paths["heartbeat"].read_text(encoding="utf-8"))
            self.assertEqual(heartbeat["state"], "launched")
            self.assertEqual(heartbeat["env_dropped_count"], 2)
            self.assertEqual(heartbeat["env_dropped_sample"],
                             ["FOO_SECRET", "TERMINAL_CWD"])
            heartbeat_text = paths["heartbeat"].read_text(encoding="utf-8")
            log_text = paths["supervisor_log"].read_text(encoding="utf-8")
            self.assertNotIn(canary, heartbeat_text)
            self.assertNotIn(canary, log_text)
            # 日志一行可观测。
            self.assertIn("env whitelist", log_text)
            self.assertIn("FOO_SECRET", log_text)
            self.assertEqual(result["env_dropped_count"], 2)
            self.assertEqual(result["env_dropped_sample"],
                             ["FOO_SECRET", "TERMINAL_CWD"])

    def test_alive_heartbeat_also_records_dropped(self):
        base = {"PATH": "/usr/bin", "FOO_SECRET": "x"}
        with scratch_dir() as td:
            config = _config(td)
            runner = _StubRunner(size=1, registered=True)
            deps = sup.SupervisorDeps(
                runner=runner, launch=_StubLaunch(pid=1),
                process_alive=lambda pid: True,   # pidfile/进程存活
                find_pool_processes=lambda repo: [],
                now=lambda: "2026-09-19T00:00:00+00:00",
            )
            paths = sup.supervisor_paths(config)
            paths["pidfile"].parent.mkdir(parents=True, exist_ok=True)
            paths["pidfile"].write_text("999\n", encoding="utf-8")
            with mock.patch.dict(os.environ, base, clear=True):
                result = sup.supervise(config, size=1, deps=deps)
            self.assertFalse(result["launched"])
            heartbeat = json.loads(paths["heartbeat"].read_text(encoding="utf-8"))
            self.assertEqual(heartbeat["state"], "alive")
            self.assertEqual(heartbeat["env_dropped_count"], 1)
            self.assertEqual(heartbeat["env_dropped_sample"], ["FOO_SECRET"])

    def test_default_launch_env_defaults_to_whitelist(self):
        captured: dict = {}

        class _FakeProc:
            pid = 4242

        def fake_popen(cmd, **kwargs):
            captured.update(kwargs)
            return _FakeProc()

        with scratch_dir() as td:
            base = {"PATH": "/usr/bin", "FOO_SECRET": "x"}
            with mock.patch.dict(os.environ, base, clear=True), \
                    mock.patch.object(sup.subprocess, "Popen", side_effect=fake_popen):
                pid = sup.default_launch(["swarmctl"], log_path=td / "run.log", cwd=td)
            self.assertEqual(pid, 4242)
            self.assertEqual(set(captured["env"]), {"PATH"})


if __name__ == "__main__":
    unittest.main()
