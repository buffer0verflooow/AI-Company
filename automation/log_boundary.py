#!/usr/bin/env python3
"""worker stdout/stderr 的无界之墙(W20,2026-09-20 22:14 磁盘事故后立)。

事故:派工层 6 处 ``log_fh = log_path.open("a") + Popen(stdout=log_fh, …)``
无上限、无轮转、无水位门——batchW17 一次跑飞 5 秒写出 10.4GB,根分区写满,
整机不可用约 10 分钟。执行面内部早有纪律(agent_runtime 64KB 截断 /
RLIMIT_FSIZE / 60s kill),⇒ 纪律在里层,无界在外层。

机制(被派工进程自己无法绕过,子进程再 fork 孙进程仍然成立):
  worker 的 stdout/stderr 不再直连文件,而是一根管道;管道读端由一个
  **detached 的边界进程**(``python3 automation/log_boundary.py --fd N …``)
  独占消费,按 ``max_file_bytes × max_files`` 轮转落盘,总量硬封顶。
  - 对 worker 而言 fd1/fd2 **就是**这根管道——写多快都只进边界进程的
    64KB 读循环,磁盘占用由边界进程决定;孙进程继承同一 fd,除非它显式
    重开别的文件,否则不存在绕出面。
  - 截断不丢证据:每次淘汰最旧轮转都**在当前文件里留下淘汰记录**
    (文件名+字节数+时间戳);崩溃现场 = 当前文件尾部,永远保留;
    EOF 时写终态记录(总字节/淘汰字节)。数据一旦被淘汰即不存在,
    记录如实陈述"已淘汰 N 字节",不伪造完整假象。
  - 边界进程从不杀 worker(只丢字节)⇒ 失败语义零改动:worker 该是什么
    终态还是什么终态,不存在"被边界掐死却假装成功"的路径。
  - 启动前磁盘水位门(:func:`assert_disk_headroom`):余量不足 ⇒
    :class:`DiskHeadroomError`,**零副作用**(未建目录、未起进程、未写库)。

上限默认值可用环境变量覆盖(读值发生在每次调用时,便于演示与调整):
  ``SWARM_LOG_MAX_FILE_BYTES``  单文件上限(默认 64MiB)
  ``SWARM_LOG_MAX_FILES``       轮转保留份数(默认 4,含当前 ⇒ 总量 ≤ 5×单文件+单块余量)
  ``SWARM_LOG_MIN_FREE_BYTES``  水位门下限(默认 20GiB)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_FILES = 4
DEFAULT_MIN_FREE_BYTES = 20 * 1024 * 1024 * 1024

_READ_CHUNK = 65536


class DiskHeadroomError(RuntimeError):
    """磁盘余量低于水位线 ⇒ 零副作用拒绝(不创建任何 run/task/文件/进程)。"""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def resolve_max_file_bytes(override: int | None = None) -> int:
    return _env_int("SWARM_LOG_MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES) \
        if override is None else int(override)


def resolve_max_files(override: int | None = None) -> int:
    return _env_int("SWARM_LOG_MAX_FILES", DEFAULT_MAX_FILES) \
        if override is None else int(override)


def resolve_min_free_bytes(override: int | None = None) -> int:
    return _env_int("SWARM_LOG_MIN_FREE_BYTES", DEFAULT_MIN_FREE_BYTES) \
        if override is None else int(override)


def disk_free_bytes(directory: Path) -> int:
    """只读:目录所在文件系统的可用字节数(读数失败原样上抛 OSError)。"""
    return shutil.disk_usage(str(directory)).free


def assert_disk_headroom(directory: Path | str, *,
                         min_free_bytes: int | None = None) -> int:
    """磁盘水位门:可用字节 < 下限 ⇒ DiskHeadroomError(零副作用)。

    下限口径:
    - 显式传入 ``min_free_bytes`` 或设置 ``SWARM_LOG_MIN_FREE_BYTES``
      ⇒ 绝对字节数,跨文件系统一律生效;
    - 都没给 ⇒ **自适应**:`min(20GiB, 该文件系统总量的 5%)`——大数
      据盘(983G)取 20GiB,小文件系统(如 tmpfs)按比例缩,避免水位门
      把整个小盘永久锁死。
    只读 stat,不创建任何东西;读数失败(OSError)原样上抛,由调用方
    决定降级还是失败——本模块不替调用方静默放行。
    """
    directory = Path(directory)
    explicit = min_free_bytes is not None or \
        bool((os.environ.get("SWARM_LOG_MIN_FREE_BYTES") or "").strip())
    limit = resolve_min_free_bytes(min_free_bytes)
    free = disk_free_bytes(directory)
    if not explicit:
        try:   # 自适应:从未显式给下限时,按所在文件系统总量缩放(小盘不锁死)
            limit = min(limit, shutil.disk_usage(str(directory)).total // 20)
        except OSError:
            pass
    if free < limit:
        raise DiskHeadroomError(
            f"W20 磁盘水位门拒启:仅 {free} 字节可用 < 下限 {limit}"
            f"(target={directory};零副作用:未创建 run/task/审计,"
            f"未建目录,未起进程)")
    return free


def follow_bounded(fd: int, path: Path | str, *,
                   max_file_bytes: int, max_files: int) -> dict:
    """边界进程主循环:从 fd 读到 EOF,按上限轮转落盘。返回处置摘要。

    轮转语义:path 写满 ⇒ 重命名为 ``path.1``(更旧的顺延 ``path.2``…);
    保留份数超 max_files ⇒ 淘汰最旧并在**新当前文件**里留下淘汰记录。
    当前文件永远持有最近字节(崩溃现场),淘汰记录保证"丢了什么"可考。
    单块写入最多超限一个读块(_READ_CHUNK)——硬总量 ≤
    ``max_file_bytes × (max_files + 1) + 2 × _READ_CHUNK + 记录行``。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if max_file_bytes <= 0 or max_files <= 0:
        raise ValueError("max_file_bytes/max_files 必须为正")
    rotations: list[Path] = []          # 旧 → 新
    current = open(path, "ab", buffering=0)
    size = path.stat().st_size
    header = (f"=== log-boundary start {_now_iso()} "
              f"max_file_bytes={max_file_bytes} max_files={max_files} "
              f"pid={os.getpid()} ===\n").encode()
    current.write(header)
    size += len(header)
    total = size
    dropped = 0
    rotations_done = 0

    def _rotate() -> None:
        nonlocal current, size, dropped, rotations_done
        current.close()
        rotated = path.with_name(f"{path.name}.{rotations_done + 1}")
        rotations_done += 1
        os.replace(path, rotated)
        rotations.append(rotated)
        evicted: Path | None = None
        while len(rotations) > max_files:
            old = rotations.pop(0)
            evicted = old
            try:
                dropped += old.stat().st_size
            except OSError:
                pass
            try:
                old.unlink()
            except FileNotFoundError:
                pass
        current = open(path, "ab", buffering=0)
        size = 0
        note = (f"=== log-boundary rotate {_now_iso()} "
                f"evicted={evicted.name if evicted is not None else '-'} "
                f"dropped_total={dropped} ===\n").encode()
        current.write(note)
        size += len(note)

    try:
        while True:
            chunk = os.read(fd, _READ_CHUNK)
            if not chunk:
                break
            if size >= max_file_bytes:
                _rotate()
            current.write(chunk)
            size += len(chunk)
            total += len(chunk)
    finally:
        end = (f"=== log-boundary end {_now_iso()} total_bytes={total} "
               f"rotations={rotations_done} dropped_bytes={dropped} ===\n").encode()
        try:
            current.write(end)
        except (OSError, ValueError):
            pass
        current.close()
        os.close(fd)
    return {"total_bytes": total, "rotations": rotations_done,
            "dropped_bytes": dropped}


def spawn_bounded(cmd: list[str], *, log_path: Path | str, cwd: Path | str,
                  env: dict | None, max_file_bytes: int | None = None,
                  max_files: int | None = None,
                  min_free_bytes: int | None = None) -> int:
    """有界派工:水位门 → 管道 → detached 边界进程 → worker。

    除"stdout 落点从文件换成管道"外,与旧 `open("a")+Popen` 派工逐字同
    语义:stdin=DEVNULL、stderr=STDOUT、start_new_session=True、
    close_fds=True、失败上抛(绝不假装成功)。返回 worker pid。
    """
    mfb = resolve_max_file_bytes(max_file_bytes)
    mf = resolve_max_files(max_files)
    log_path = Path(log_path)
    assert_disk_headroom(log_path.parent, min_free_bytes=min_free_bytes)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    r, w = os.pipe()
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()),
             "--fd", str(r), "--path", str(log_path),
             "--max-file-bytes", str(mfb), "--max-files", str(mf)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
            close_fds=True, pass_fds=(r,))
    except BaseException:
        os.close(r)
        os.close(w)
        raise
    os.close(r)          # 读端只属于边界进程
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=w,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
    except BaseException:
        os.close(w)
        raise
    os.close(w)          # 父进程关写端 ⇒ worker 全部退出后边界进程见到 EOF
    return int(proc.pid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="log-boundary 边界进程:消费管道读端,有界轮转落盘")
    parser.add_argument("--fd", type=int, required=True,
                        help="要消费的管道读端 fd(由 spawn_bounded 传入)")
    parser.add_argument("--path", required=True, help="日志主文件路径")
    parser.add_argument("--max-file-bytes", type=int, default=None)
    parser.add_argument("--max-files", type=int, default=None)
    args = parser.parse_args(argv)
    summary = follow_bounded(
        args.fd, Path(args.path),
        max_file_bytes=resolve_max_file_bytes(args.max_file_bytes),
        max_files=resolve_max_files(args.max_files))
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
