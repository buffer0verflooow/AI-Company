"""W20 无界之墙回归:封顶/轮转/水位门/四处接线/supervisor。

契约(派工书 §3):``python3 -m pytest -q automation/tests/test_w20_output_bound.py``
在 dev-trace-verify 沙箱内真复跑 ⇒ 只允许仓库根可写、断网、完全同步:
所有 scratch 放本目录 ``.w20-scratch/``(仓库根内),测试结束清理;
派生的 writer/边界进程全部走 ``sys.executable``(沙箱内=系统 python3)。
"""
from __future__ import annotations

import inspect
import os
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import automation.log_boundary as lb

SCRATCH = Path(__file__).resolve().parent / ".w20-scratch"

# 无界 writer:每轮 8KB 刷 stdout(+1ms 步进,控制测试写盘总量),写满指定秒数退出。
WRITER = (
    "import sys, time\n"
    "end = time.time() + float(sys.argv[1])\n"
    "while time.time() < end:\n"
    "    sys.stdout.write('x' * 8192)\n"
    "    sys.stdout.flush()\n"
    "    time.sleep(0.001)\n"
)

_MIN_FREE = 1          # 测试内不真触发水位门(磁盘余量充足)
_ENV = {"PATH": os.defpath}


def _scratch(name: str) -> Path:
    d = SCRATCH / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tree_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def _kill_tree(pid: int) -> None:
    try:
        os.killpg(os.getpgid(pid), 15)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _wait_end_marker(work: Path, stem: str, timeout: float = 10.0) -> bool:
    """边界进程消费到 EOF 后会写终态记录 ⇒ 以它作为收尾信号。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for p in sorted(work.glob(f"{stem}*")):
            try:
                if b"log-boundary end" in p.read_bytes():
                    return True
            except OSError:
                pass
        time.sleep(0.2)
    return False


class BoundedCapTest(unittest.TestCase):
    def test_writer_capped_with_rotation_and_markers(self):
        work = _scratch("cap")
        log_path = work / "run.log"
        pid = lb.spawn_bounded(
            [sys.executable, "-c", WRITER, "4"],
            log_path=log_path, cwd=work, env=_ENV,
            max_file_bytes=32768, max_files=3, min_free_bytes=_MIN_FREE)
        self.assertGreater(pid, 0)
        self.assertTrue(_wait_end_marker(work, "run.log"),
                        "终态记录未出现 ⇒ 边界进程没有消费到 EOF")
        _kill_tree(pid)
        # 硬总量封顶:max_file_bytes × (max_files+1) + 单块余量 + 记录行
        total = _tree_size(work)
        slack = lb._READ_CHUNK * 4 + 8192
        self.assertLessEqual(
            total, 32768 * (3 + 1) + slack,
            f"磁盘总占位 {total} 字节超过硬上限 ⇒ 无界之墙失效")
        # 轮转真发生 + 淘汰留痕:start 记录随最旧轮转被淘汰(如实陈述),
        # 当前文件必须保留 rotate/dropped/end 记录 ⇒ "丢了什么"可考。
        self.assertTrue(list(work.glob("run.log.[0-9]*")), "没有任何轮转文件")
        current = log_path.read_bytes()
        self.assertIn(b"log-boundary rotate", current)
        self.assertIn(b"dropped_total=", current)
        self.assertIn(b"log-boundary end", current)

    def test_append_semantics_and_end_record(self):
        work = _scratch("small")
        log_path = work / "run.log"
        log_path.write_bytes(b"pre-existing\n")
        pid = lb.spawn_bounded(
            [sys.executable, "-c", "print('hello')"],
            log_path=log_path, cwd=work, env=_ENV,
            max_file_bytes=1 << 20, max_files=2, min_free_bytes=_MIN_FREE)
        self.assertTrue(_wait_end_marker(work, "run.log"))
        _kill_tree(pid)
        blob = log_path.read_bytes()
        self.assertIn(b"pre-existing", blob)     # append 语义不变
        self.assertIn(b"hello", blob)
        self.assertIn(b"log-boundary end", blob)


class HeadroomGateTest(unittest.TestCase):
    def test_refusal_leaves_zero_side_effects(self):
        work = _scratch("gate")
        log_path = work / "run.log"
        with mock.patch.object(lb, "disk_free_bytes", return_value=1):
            with self.assertRaises(lb.DiskHeadroomError):
                lb.assert_disk_headroom(work, min_free_bytes=1024)
            with self.assertRaises(lb.DiskHeadroomError):
                lb.spawn_bounded([sys.executable, "-c", "print(1)"],
                                 log_path=log_path, cwd=work, env=_ENV,
                                 min_free_bytes=1024)
        self.assertEqual(list(work.iterdir()), [],
                         "水位门拒绝路径残留了文件 ⇒ 非零副作用")
        time.sleep(0.5)   # 若误起了进程,这里会观察到它写出的东西
        self.assertEqual(list(work.iterdir()), [])

    def test_env_override_and_default_pass(self):
        work = _scratch("env")
        with mock.patch.dict(os.environ,
                             {"SWARM_LOG_MIN_FREE_BYTES": str(10 ** 30)}):
            with self.assertRaises(lb.DiskHeadroomError):
                lb.assert_disk_headroom(work)   # 调用时读 env ⇒ 覆盖生效
        self.assertGreater(lb.assert_disk_headroom(work), 0)  # 本机余量充足 ⇒ 放行

    def test_unreadable_disk_raises_loudly(self):
        work = _scratch("ioerr")
        with mock.patch.object(lb, "disk_free_bytes",
                               side_effect=OSError("simulated stat failure")):
            with self.assertRaises(OSError):
                lb.assert_disk_headroom(work)   # 不静默放行


class RouterWiringTest(unittest.TestCase):
    def test_four_v2_submit_sites_wired_to_gate(self):
        import automation.company_router as cr
        src = inspect.getsource(cr)
        self.assertEqual(
            src.count("_disk_headroom_precheck(config)"), 4,
            "v2 四条 submit 线必须各接一次磁盘水位预检")
        self.assertNotIn('log_path.open("a"', src,
                         "仍存在 stdout 直连文件的派工位点")

    def test_precheck_refusal_and_loud_degrade(self):
        import automation.company_router as cr
        cfg = {"log_dir": str(_scratch("precheck"))}
        with mock.patch.object(
                cr.log_boundary, "assert_disk_headroom",
                side_effect=lb.DiskHeadroomError("模拟余量不足")):
            with self.assertRaises(RuntimeError):
                cr._disk_headroom_precheck(cfg)     # 响亮拒绝
        with mock.patch.object(
                cr.log_boundary, "assert_disk_headroom",
                side_effect=OSError("模拟读数不可得")):
            cr._disk_headroom_precheck(cfg)          # 响亮降级:不炸


class SupervisorLaunchTest(unittest.TestCase):
    def test_default_launch_is_bounded(self):
        import automation.swarm_pool_supervisor as sps
        work = _scratch("supervisor")
        log_path = work / "pool.log"
        with mock.patch.dict(os.environ, {"SWARM_LOG_MAX_FILE_BYTES": "32768",
                                          "SWARM_LOG_MAX_FILES": "3"}):
            pid = sps.default_launch([sys.executable, "-c", WRITER, "3"],
                                     log_path=log_path, cwd=work,
                                     env=_ENV)
        self.assertGreater(pid, 0)
        self.assertTrue(_wait_end_marker(work, "pool.log"))
        _kill_tree(pid)
        total = _tree_size(work)
        slack = lb._READ_CHUNK * 4 + 8192
        self.assertLessEqual(total, 32768 * 4 + slack,
                             f"supervisor 派工未受界:总占位 {total} 字节")


def tearDownModule() -> None:
    shutil.rmtree(SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
