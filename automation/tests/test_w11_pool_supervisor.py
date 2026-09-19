r"""W11-a 回归:常驻池 worker 监督保活(swarm_pool_supervisor)。

派工书 §1 六条断言(全部 stub 驱动,不打真模型、不起真常驻进程):
  ① 无存活 ⇒ 恰好拉起 1 次 + pidfile/heartbeat 落地;
  ② 已有存活 ⇒ 零拉起;
  ③ 并发两次 ⇒ 只拉起 1 次(flock 生效);
  ④ provision 重复调用幂等(不重复建身份;含副本库真 CLI 对拍);
  ⑤ pool 子命令失败/不可用 ⇒ rc≠0 且原因含"pool";
  ⑥ 拉起失败不得记成成功(退出码≠0 + 失败日志 + 无 pidfile/heartbeat)。

scratch 落 `W11_SCRATCH`(默认 /home/pwn/workspace/w11-scratch),不用 /tmp。
"""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import swarm_pool_supervisor as sup

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
SEED_SCRIPT = Path(SWARM_REPO) / "migrations_v2" / "build_v2.py"
SCRATCH_ROOT = Path("/home/pwn/workspace/w11-scratch")


def _seed_swarm_db(dest: Path) -> None:
    """用例内建确定性空种子库(W13-c:不再拷活库,生产身份不污染计数断言)。"""
    if not SEED_SCRIPT.is_file():
        raise unittest.SkipTest("蜂群建库脚本不在本机")
    proc = subprocess.run(
        [sys.executable, str(SEED_SCRIPT), "--db", str(dest)],
        cwd=SWARM_REPO, capture_output=True, text=True, timeout=240)
    if proc.returncode != 0 or not dest.is_file():
        raise RuntimeError(
            f"种子库构建失败(rc={proc.returncode}):\n"
            f"{proc.stdout[-800:]}\n{proc.stderr[-800:]}")


@contextlib.contextmanager
def scratch_dir():
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(SCRATCH_ROOT)) as td:
        yield Path(td)


def _config(td: Path, *, swarm_repo: str = SWARM_REPO, **extra) -> dict:
    config = {
        "enabled": True,
        "swarm_repo": swarm_repo,
        "swarm_v2_db": str(td / "swarm_v2.db"),
        "state_db": str(td / "runtime" / "company_router.db"),
        "log_dir": str(td / "runtime" / "logs"),
        "swarm_pool_size": 2,
    }
    config.update(extra)
    return config


class StubRunner:
    """Deterministic `subprocess.run` replacement for pool provision/status."""

    def __init__(self, *, size=2, registered=False, open_slots=(), fail_on=None):
        self.size = size
        self.registered = registered
        self.open_slots = set(open_slots)
        self.fail_on = fail_on
        self.calls: list[list] = []
        self.provision_calls = 0
        self.status_calls = 0

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        flat = " ".join(str(c) for c in cmd)
        if self.fail_on and self.fail_on in flat:
            return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="pool boom")
        if "pool provision" in flat:
            self.provision_calls += 1
            self.registered = True
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=json.dumps({"provisioned": [
                    {"agent_id": f"pool-worker-{i + 1}", "roles": []}
                    for i in range(self.size)]}),
                stderr="")
        if "pool status" in flat:
            self.status_calls += 1
            workers = []
            for i in range(self.size):
                slot = i + 1
                workers.append({
                    "slot": slot, "agent_id": f"pool-worker-{slot}",
                    "registered": self.registered, "roles": [],
                    "conn_state": "open" if slot in self.open_slots else None,
                    "last_heartbeat": None, "close_reason": None, "running_tasks": 0,
                })
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=json.dumps({"size": self.size, "workers": workers}),
                stderr="")
        raise AssertionError(f"unexpected command: {flat}")


class StubLaunch:
    def __init__(self, *, pid=1234, raise_exc=None):
        self.pid = pid
        self.raise_exc = raise_exc
        self.calls: list[tuple] = []

    def __call__(self, cmd, *, log_path, cwd, env):
        self.calls.append((list(cmd), Path(log_path), Path(cwd), dict(env)))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.pid


def _deps(runner, launch, *, alive_pids=(), pool_procs=(), now="2026-09-19T00:00:00+00:00"):
    return sup.SupervisorDeps(
        runner=runner, launch=launch,
        process_alive=lambda pid: pid in set(alive_pids),
        find_pool_processes=lambda repo: list(pool_procs),
        now=lambda: now,
    )


class NoAliveLaunchesOnceTests(unittest.TestCase):
    """① 无存活 ⇒ 恰好拉起 1 次 + pidfile/heartbeat 落地。"""

    def test_launches_exactly_once_and_writes_pidfile_and_heartbeat(self):
        with scratch_dir() as td:
            config = _config(td)
            runner = StubRunner(size=2, registered=False)
            launch = StubLaunch(pid=4321)
            deps = _deps(runner, launch)
            result = sup.supervise(config, size=2, deps=deps)

            self.assertTrue(result["ok"])
            self.assertEqual(result["rc"], 0)
            self.assertTrue(result["launched"])
            self.assertFalse(result["alive_before"])
            self.assertEqual(result["pid"], 4321)
            self.assertEqual(len(launch.calls), 1)
            # 初次未注册 ⇒ provision 恰好一次,之后 status 复核
            self.assertEqual(runner.provision_calls, 1)
            self.assertEqual(runner.status_calls, 2)

            paths = sup.supervisor_paths(config)
            self.assertEqual(paths["pidfile"].read_text(encoding="utf-8").strip(), "4321")
            heartbeat = json.loads(paths["heartbeat"].read_text(encoding="utf-8"))
            self.assertEqual(heartbeat["pid"], 4321)
            self.assertIn("ts", heartbeat)
            self.assertIn("conn", heartbeat)
            self.assertEqual(heartbeat["state"], "launched")
            snapshot = json.loads(paths["snapshot"].read_text(encoding="utf-8"))
            self.assertEqual(snapshot["size"], 2)
            # 拉起命令 = swarmctl pool run(size/interval/db 正确)
            cmd = launch.calls[0][0]
            self.assertEqual(cmd[2:4], ["pool", "run"])
            self.assertEqual(cmd[cmd.index("--size") + 1], "2")
            self.assertEqual(cmd[cmd.index("--db") + 1], config["swarm_v2_db"])
            self.assertEqual(launch.calls[0][1], paths["run_log"])
            self.assertTrue(launch.calls[0][2].is_absolute())


class AlreadyAliveSkipsLaunchTests(unittest.TestCase):
    """② 已有存活 ⇒ 零拉起。"""

    def test_open_connection_means_alive_and_no_launch(self):
        with scratch_dir() as td:
            config = _config(td)
            runner = StubRunner(size=2, registered=True, open_slots=(1,))
            launch = StubLaunch()
            result = sup.supervise(config, size=2, deps=_deps(runner, launch))
            self.assertFalse(result["launched"])
            self.assertTrue(result["alive_before"])
            self.assertEqual(launch.calls, [])
            self.assertEqual(runner.provision_calls, 0)  # 已注册 ⇒ 不重复建
            heartbeat = json.loads(
                sup.supervisor_paths(config)["heartbeat"].read_text(encoding="utf-8"))
            self.assertEqual(heartbeat["state"], "alive")
            self.assertEqual(heartbeat["conn"], "open")

    def test_live_pool_process_means_alive_and_no_launch(self):
        with scratch_dir() as td:
            config = _config(td)
            runner = StubRunner(size=2, registered=True)  # no open conn
            launch = StubLaunch()
            deps = _deps(runner, launch, pool_procs=(999,))
            result = sup.supervise(config, size=2, deps=deps)
            self.assertFalse(result["launched"])
            self.assertTrue(result["alive_before"])
            self.assertEqual(result["pid"], 999)
            self.assertEqual(launch.calls, [])


class ConcurrencyLockTests(unittest.TestCase):
    """③ 并发两次 ⇒ 只拉起 1 次(flock 独占)。"""

    def test_concurrent_second_call_is_lock_busy_and_only_one_launch(self):
        with scratch_dir() as td:
            config = _config(td)
            runner = StubRunner(size=2, registered=True)
            started = threading.Event()
            release = threading.Event()
            launch_calls: list[list] = []

            def blocking_launch(cmd, *, log_path, cwd, env):
                launch_calls.append(list(cmd))
                started.set()
                release.wait(5)
                return 777

            deps1 = _deps(runner, blocking_launch)
            deps2 = _deps(runner, StubLaunch())
            results: dict = {}

            def first():
                results["first"] = sup.supervise(config, size=2, deps=deps1)

            thread = threading.Thread(target=first)
            thread.start()
            self.assertTrue(started.wait(5), "first supervisor never reached launch")
            # 第一个仍在拉起中(持锁);第二个应立即 lock-busy,不拉起。
            second = sup.supervise(config, size=2, deps=deps2)
            self.assertTrue(second["ok"])
            self.assertEqual(second["rc"], 0)
            self.assertFalse(second["launched"])
            self.assertIn("lock busy", second["reason"])
            self.assertEqual(len(launch_calls), 1)
            release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertTrue(results["first"]["launched"])
            self.assertEqual(len(launch_calls), 1)


class ProvisionIdempotentTests(unittest.TestCase):
    """④ provision 重复调用幂等(不重复建身份)。"""

    def test_supervise_skips_provision_when_all_registered(self):
        with scratch_dir() as td:
            config = _config(td)
            runner = StubRunner(size=2, registered=True)
            for _ in range(3):
                sup.supervise(config, size=2, deps=_deps(runner, StubLaunch()))
            self.assertEqual(runner.provision_calls, 0)

    def test_real_swarmctl_provision_twice_does_not_add_rows(self):
        with scratch_dir() as td:
            db = td / "swarm_v2.db"
            _seed_swarm_db(db)          # 空种子库:不含任何生产 pool-worker-* 身份
            config = _config(td, swarm_v2_db=str(db))

            def count():
                con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    profiles = con.execute(
                        "SELECT COUNT(*) FROM agent_profiles WHERE agent_id LIKE 'pool-worker-%'"
                    ).fetchone()[0]
                    roles = con.execute(
                        "SELECT COUNT(*) FROM agent_roles WHERE agent_id LIKE 'pool-worker-%'"
                    ).fetchone()[0]
                finally:
                    con.close()
                return profiles, roles

            roles_json = json.dumps({"pool-worker-1": ["researcher"],
                                     "pool-worker-2": ["analyst"]})
            sup.provision_pool(config, size=2, roles_json=roles_json)
            first = count()
            sup.provision_pool(config, size=2, roles_json=roles_json)
            second = count()
            self.assertEqual(first, (2, 2))
            self.assertEqual(second, first)  # 重复调用不增行


class PoolFailureLoudTests(unittest.TestCase):
    """⑤ pool 子命令失败/不可用 ⇒ rc≠0 且原因含"pool"。"""

    def test_status_failure_raises_with_pool_reason(self):
        with scratch_dir() as td:
            config = _config(td)
            runner = StubRunner(size=2, registered=True, fail_on="pool status")
            with self.assertRaises(sup.PoolSupervisorError) as ctx:
                sup.supervise(config, size=2, deps=_deps(runner, StubLaunch()))
            self.assertIn("pool", str(ctx.exception))
            self.assertIn("rc=2", str(ctx.exception))

    def test_cli_missing_swarmctl_exits_nonzero_with_pool_reason(self):
        with scratch_dir() as td:
            config = _config(td, swarm_repo=str(td / "no-such-repo"))
            config_path = td / "router_config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(err):
                rc = sup.main(["--config", str(config_path)])
            self.assertNotEqual(rc, 0)
            self.assertEqual(rc, 2)
            self.assertIn("pool", err.getvalue())

    def test_cli_status_failure_exits_nonzero(self):
        with scratch_dir() as td:
            config = _config(td, swarm_repo=str(td / "no-such-repo"))
            config_path = td / "router_config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                rc = sup.main(["--config", str(config_path), "--status", "--json"])
            self.assertNotEqual(rc, 0)
            self.assertIn("pool", err.getvalue())


class LaunchFailureNotSuccessTests(unittest.TestCase):
    """⑥ 拉起失败不得记成成功(rc≠0 + 日志 + 无 pidfile/heartbeat)。"""

    def test_launch_exception_is_not_recorded_as_success(self):
        with scratch_dir() as td:
            config = _config(td)
            runner = StubRunner(size=2, registered=True)
            launch = StubLaunch(raise_exc=OSError("exec format error"))
            result = sup.supervise(config, size=2, deps=_deps(runner, launch))
            self.assertFalse(result["ok"])
            self.assertNotEqual(result["rc"], 0)
            self.assertFalse(result["launched"])
            self.assertIn("pool 拉起失败", result["reason"])
            self.assertIn("exec format error", result["reason"])
            paths = sup.supervisor_paths(config)
            self.assertFalse(paths["pidfile"].exists())
            self.assertFalse(paths["heartbeat"].exists())
            self.assertIn("exec format error",
                          paths["supervisor_log"].read_text(encoding="utf-8"))

    def test_invalid_pid_is_not_recorded_as_success(self):
        with scratch_dir() as td:
            config = _config(td)
            runner = StubRunner(size=2, registered=True)
            launch = StubLaunch(pid=0)
            result = sup.supervise(config, size=2, deps=_deps(runner, launch))
            self.assertFalse(result["ok"])
            self.assertNotEqual(result["rc"], 0)
            self.assertIn("pool 拉起失败", result["reason"])
            self.assertFalse(sup.supervisor_paths(config)["pidfile"].exists())


class ProcessProbeTests(unittest.TestCase):
    """路径型 /proc 匹配:命中 pool run;不命中自身/其它命令(字符类防自命中)。"""

    def _fake_proc(self, root: Path, pid: int, cmdline: list[str]) -> None:
        d = root / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "cmdline").write_bytes(b"\x00".join(c.encode() for c in cmdline) + b"\x00")

    def test_matches_pool_run_only(self):
        with scratch_dir() as td:
            proc = td / "proc"
            self._fake_proc(proc, 111, [sys.executable, f"{SWARM_REPO}/scripts/swarmctl.py",
                                        "pool", "run", "--size", "2"])
            self._fake_proc(proc, 222, [sys.executable, f"{SWARM_REPO}/scripts/swarmctl.py",
                                        "pool", "status", "--size", "2"])
            self._fake_proc(proc, 333, [sys.executable,
                                        "/home/pwn/workspace/company/automation/swarm_pool_supervisor.py",
                                        "pool", "run"])  # supervisor 自身:不含 swarmctl.py
            self.assertEqual(sup.find_pool_processes(Path(SWARM_REPO), proc_root=proc), [111])

    def test_health_snapshot_reads_files(self):
        with scratch_dir() as td:
            config = _config(td)
            paths = sup.supervisor_paths(config)
            paths["snapshot"].parent.mkdir(parents=True, exist_ok=True)
            paths["snapshot"].write_text('{"size": 2, "workers": []}', encoding="utf-8")
            paths["heartbeat"].write_text('{"pid": 5, "ts": "t", "conn": null}',
                                          encoding="utf-8")
            snap = sup.pool_health_snapshot(config)
            self.assertEqual(snap["status_snapshot"]["size"], 2)
            self.assertEqual(snap["heartbeat"]["pid"], 5)
            # 缺失即 None,不编造
            snap2 = sup.pool_health_snapshot(_config(td / "other"))
            self.assertIsNone(snap2["status_snapshot"])


if __name__ == "__main__":
    unittest.main()
