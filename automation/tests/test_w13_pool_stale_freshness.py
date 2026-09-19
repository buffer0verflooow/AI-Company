r"""W13-b 回归:监督器 liveness 用新鲜度,不再被"幽灵连接"骗过。

背景(缺陷 A):池进程被 SIGTERM 后,`swarm_connections` 里 `pool-worker-*` 行
仍是 `state='open'`(心跳冻结);旧 `_active_worker` 只看 `conn_state=='open'`
⇒ 误判存活 ⇒ 静默不拉起。本批改为 `conn_state=='open' and not stale`。

断言(派工书 §2-b;全部 stub 驱动,不打真模型、不起真常驻进程):
  ① stale-open ⇒ **仍拉起**(核心回归;改前代码必须红)
  ② fresh-open ⇒ 不拉起
  ③ 无连接 + 无进程 ⇒ 拉起
  ④ reap 失败 ⇒ 拉起照做,且记录非空(heartbeat + 日志)
  ⑤ heartbeat/返回值含 `stale_evidence`(agent_id/age_seconds/stale)且日志响亮

scratch 落 `W13_SCRATCH`(默认 /home/pwn/workspace/w13-scratch),不用 /tmp。
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from automation import swarm_pool_supervisor as sup

SCRATCH_ROOT = Path("/home/pwn/workspace/w13-scratch")
SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"


@contextlib.contextmanager
def scratch_dir():
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(SCRATCH_ROOT)) as td:
        yield Path(td)


def _config(td: Path) -> dict:
    return {
        "enabled": True,
        "swarm_repo": SWARM_REPO,
        "swarm_v2_db": str(td / "swarm_v2.db"),
        "state_db": str(td / "runtime" / "company_router.db"),
        "log_dir": str(td / "runtime" / "logs"),
        "swarm_pool_size": 2,
    }


def _worker(slot: int, *, conn_state=None, stale=False, age=None) -> dict:
    return {
        "slot": slot, "agent_id": f"pool-worker-{slot}", "registered": True,
        "roles": [], "conn_id": f"c-{slot:012d}",
        "conn_state": conn_state, "last_heartbeat": None, "close_reason": None,
        "conn_age_seconds": age, "stale": stale, "running_tasks": 0,
    }


class _Runner:
    """`subprocess.run` replacement:pool provision/status/conn reap 均成功。"""

    def __init__(self, *, workers, registered=True):
        self.workers = workers
        self.registered = registered
        self.calls: list[list] = []
        self.reap_calls = 0

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        flat = " ".join(str(c) for c in cmd)
        if "pool provision" in flat:
            self.registered = True
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=json.dumps({"provisioned": [
                    {"agent_id": f"pool-worker-{i + 1}", "roles": []}
                    for i in range(2)]}), stderr="")
        if "pool status" in flat:
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=json.dumps({"size": 2, "workers": self.workers}), stderr="")
        if "conn reap" in flat:
            self.reap_calls += 1
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"reaped": [], "count": 0}), stderr="")
        raise AssertionError(f"unexpected command: {flat}")


class _Launch:
    def __init__(self, *, pid=4321, raise_exc=None):
        self.pid = pid
        self.raise_exc = raise_exc
        self.calls: list[tuple] = []

    def __call__(self, cmd, *, log_path, cwd, env):
        self.calls.append((list(cmd), Path(log_path), Path(cwd), dict(env)))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.pid


def _deps(runner, launch, *, reap=None, pool_procs=(), alive_pids=()):
    # 不用 ``reap=`` 关键字构造:改前 SupervisorDeps 无此字段(新断言须在改前红,
    # 且不能被"构造参数不认识"这种脚手架错误抢先判红)。dataclass 无 __slots__,
    # 属性赋值在改前/改后都成立;改前 supervise 不读它,故红在行为断言上。
    deps = sup.SupervisorDeps(
        runner=runner, launch=launch,
        process_alive=lambda pid: pid in set(alive_pids),
        find_pool_processes=lambda repo: list(pool_procs),
        now=lambda: "2026-09-19T00:00:00+00:00",
    )
    deps.reap = reap
    return deps


class StaleOpenStillLaunchesTests(unittest.TestCase):
    """① 核心回归:stale-open 视为死亡 ⇒ 照常拉起。"""

    def test_stale_open_connection_still_launches(self):
        with scratch_dir() as td:
            config = _config(td)
            workers = [_worker(1, conn_state="open", stale=True, age=200)]
            runner = _Runner(workers=workers, registered=True)
            launch = _Launch(pid=9876)
            deps = _deps(runner, launch, reap=lambda cfg: {"count": 1})
            result = sup.supervise(config, size=2, deps=deps)

            self.assertTrue(result["launched"], "stale-open 不得再被当成存活")
            self.assertFalse(result["alive_before"])
            self.assertEqual(len(launch.calls), 1)
            self.assertEqual(result["pid"], 9876)
            self.assertTrue(result["stale_evidence"])
            self.assertEqual(result["stale_evidence"][0]["agent_id"], "pool-worker-1")
            self.assertEqual(result["stale_evidence"][0]["age_seconds"], 200)
            self.assertTrue(result["stale_evidence"][0]["stale"])
            self.assertEqual(result["reap"], {"ok": True, "count": 1})


class FreshOpenSkipsTests(unittest.TestCase):
    """② fresh-open ⇒ 不拉起。"""

    def test_fresh_open_connection_means_alive(self):
        with scratch_dir() as td:
            config = _config(td)
            workers = [_worker(1, conn_state="open", stale=False, age=3)]
            runner = _Runner(workers=workers, registered=True)
            launch = _Launch()
            result = sup.supervise(config, size=2, deps=_deps(runner, launch))
            self.assertFalse(result["launched"])
            self.assertTrue(result["alive_before"])
            self.assertEqual(launch.calls, [])
            self.assertEqual(result["stale_evidence"], [])
            self.assertIsNone(result["reap"], "无 stale 证据不得调用 reap")


class NoConnNoProcLaunchesTests(unittest.TestCase):
    """③ 无连接 + 无进程 ⇒ 拉起。"""

    def test_no_connection_no_process_launches(self):
        with scratch_dir() as td:
            config = _config(td)
            workers = [_worker(1, conn_state=None), _worker(2, conn_state=None)]
            runner = _Runner(workers=workers, registered=True)
            launch = _Launch(pid=55)
            result = sup.supervise(config, size=2, deps=_deps(runner, launch))
            self.assertTrue(result["launched"])
            self.assertEqual(len(launch.calls), 1)


class StaleEvidenceObservableTests(unittest.TestCase):
    """⑤ heartbeat/返回值含 stale 证据;日志响亮。"""

    def test_heartbeat_and_log_carry_stale_evidence(self):
        with scratch_dir() as td:
            config = _config(td)
            workers = [_worker(2, conn_state="open", stale=True, age=181)]
            runner = _Runner(workers=workers, registered=True)
            launch = _Launch(pid=7)
            result = sup.supervise(
                config, size=2, deps=_deps(runner, launch, reap=lambda cfg: {"count": 1}))

            paths = sup.supervisor_paths(config)
            heartbeat = json.loads(paths["heartbeat"].read_text(encoding="utf-8"))
            self.assertEqual(heartbeat["state"], "launched")
            self.assertEqual(heartbeat["stale_evidence"], result["stale_evidence"])
            self.assertEqual(heartbeat["stale_evidence"][0]["agent_id"], "pool-worker-2")
            self.assertEqual(heartbeat["stale_evidence"][0]["age_seconds"], 181)
            self.assertTrue(heartbeat["stale_evidence"][0]["stale"])
            self.assertIn("reap", heartbeat)

            log_text = paths["supervisor_log"].read_text(encoding="utf-8")
            self.assertIn("stale-open connections detected", log_text)
            self.assertIn("pool-worker-2", log_text)


class ReapFailureDoesNotBlockLaunchTests(unittest.TestCase):
    """④ reap 失败 ⇒ 拉起照做,记录非空。"""

    def test_reap_failure_still_launches_and_is_recorded(self):
        with scratch_dir() as td:
            config = _config(td)
            workers = [_worker(1, conn_state="open", stale=True, age=222)]

            def boom(cfg):
                raise RuntimeError("swarmctl conn reap 不可用")

            runner = _Runner(workers=workers, registered=True)
            launch = _Launch(pid=99)
            result = sup.supervise(
                config, size=2, deps=_deps(runner, launch, reap=boom))

            self.assertTrue(result["launched"], "reap 失败不得阻塞拉起")
            self.assertEqual(len(launch.calls), 1)
            self.assertFalse(result["reap"]["ok"])
            self.assertIn("conn reap 不可用", result["reap"]["error"])

            paths = sup.supervisor_paths(config)
            heartbeat = json.loads(paths["heartbeat"].read_text(encoding="utf-8"))
            self.assertFalse(heartbeat["reap"]["ok"])
            self.assertIn("conn reap 不可用", heartbeat["reap"]["error"])
            log_text = paths["supervisor_log"].read_text(encoding="utf-8")
            self.assertIn("conn reap 失败", log_text)

    def test_default_reap_uses_runner_when_stale(self):
        """未注入 reap 时走 `conn reap` 真 CLI 封装(经 deps.runner)。"""
        with scratch_dir() as td:
            config = _config(td)
            workers = [_worker(1, conn_state="open", stale=True, age=200)]
            runner = _Runner(workers=workers, registered=True)
            launch = _Launch(pid=11)
            result = sup.supervise(config, size=2, deps=_deps(runner, launch))
            self.assertTrue(result["launched"])
            self.assertEqual(runner.reap_calls, 1, "有 stale 证据 ⇒ 应调用 conn reap")
            self.assertTrue(result["reap"]["ok"])


class ActiveWorkerUnitTests(unittest.TestCase):
    """口径单测:stale 字段缺失(旧版)按未 stale 处理(向后兼容)。"""

    def test_active_worker_ignores_stale_and_tolerates_missing_field(self):
        stale = _worker(1, conn_state="open", stale=True, age=200)
        legacy = _worker(2, conn_state="open")  # 无 stale 键(旧 swarmctl)
        legacy.pop("stale")
        self.assertIsNone(sup._active_worker({"workers": [stale]}))
        self.assertEqual(sup._active_worker({"workers": [stale, legacy]}), legacy)
        self.assertEqual(len(sup._stale_evidence({"workers": [stale]})), 1)


if __name__ == "__main__":
    unittest.main()
