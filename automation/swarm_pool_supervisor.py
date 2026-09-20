#!/usr/bin/env python3
r"""常驻池 worker 监督与保活(W11-a)。

事实(2026-09-19):蜂群常驻机制已存在(`swarmctl pool {provision,status,run}`),
缺的是**监督** —— `systemctl --user` 无 unit、crontab 无;公司只在派发时起一次性
worker ⇒ 公司不派发则任务躺市场无人接。本脚本是那个"看门人",供 Hermes cron
每 5 分钟调用一次(幂等、单实例、响亮失败):

  1. 供给池身份(幂等):`pool provision --size N [--roles-json …]`;已注册 ⇒ 跳过。
  2. 存活判定:`pool status --json`(**连接态**) + 进程存活检查(**/proc 路径型
     匹配**,不用裸 `pgrep -f`;正则用字符类 `swarmct[l]\.py` / `poo[l]` / `ru[n]`
     以免模式命中自身命令行)。
  3. 拉起:无存活 ⇒ detached 起 `pool run`(`start_new_session=True`,
     stdout/stderr 落日志),写 pidfile + heartbeat(时间戳 + pid + conn)。
  4. 单实例:`flock` 独占锁;并发调用只有一个拉起,其余立即返回 0(不重复拉起)。
  5. 退出码语义(响亮):拉起成功=0;已存活=0;锁被占=0;`pool` 子命令不可用 /
     status/provision 失败 / 拉起失败 ⇒ **非 0 + 原因原文**(绝不 rc=0 静默)。
  6. 可观测:`pool status` 快照 + heartbeat 落 JSON,供健康检查(含 W10 读数面)
     直接读取;`--status --json` 与 :func:`pool_health_snapshot` 是读接口。

**本批不真起常驻进程**:测试全部注入 stub;生产注册由主代理决定(见报告 cron
定义)。本脚本也**不改任何活库开关**(`pool run` 自身的 `pool_resident` 门由运维
裁决,本脚本不代开)。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

try:  # pragma: no cover - import style depends on how it is launched
    import fcntl
except ImportError:  # pragma: no cover - Windows is not the deployment platform
    fcntl = None  # type: ignore[assignment]

try:
    from ._safe_io import (atomic_write_text, pool_worker_environment,
                           resolve_worker_proxy)
except ImportError:  # direct execution from automation/
    from _safe_io import (atomic_write_text, pool_worker_environment,  # type: ignore[no-redef]
                          resolve_worker_proxy)

try:  # W20 无界之墙:worker 落盘输出的硬边界(水位门 + 有界轮转)
    from automation import log_boundary
except ImportError:  # direct execution from automation/
    import log_boundary  # type: ignore[no-redef]

CONFIG_PATH = Path(__file__).resolve().parent / "router_config.json"
HERE = Path(__file__).resolve().parent

DEFAULT_POOL_SIZE = 3
DEFAULT_INTERVAL_SECONDS = 90.0

#: 拉起命令路径型匹配(字符类防模式自命中;仅用于 /proc 扫描,不落 pgrep)。
#: 形如 `python …/swarmctl.py pool run --size … --db …`。
_POOL_RUN_RE = re.compile(r"swarmct[l]\.py[\s\x00]+poo[l]\b[\s\x00]+ru[n]\b")
#: 单条命令硬超时(秒)
DEFAULT_TIMEOUT = 30


class PoolSupervisorError(RuntimeError):
    """响亮失败(CLI 捕获后非 0 退出;绝不静默 rc=0)。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


# ---------------------------------------------------------------------------
# 路径与配置
# ---------------------------------------------------------------------------

def runtime_dir(config: dict[str, Any]) -> Path:
    state_db = str(config.get("state_db") or "").strip()
    if state_db:
        return Path(state_db).parent
    return HERE.parent / "operations" / "runtime"


def supervisor_paths(config: dict[str, Any]) -> dict[str, Path]:
    """Runtime file locations (config-overridable; default next to state_db)."""
    runtime = runtime_dir(config)

    def _p(key: str, default: Path) -> Path:
        raw = str(config.get(key) or "").strip()
        return Path(raw) if raw else default

    pidfile = _p("swarm_pool_pidfile", runtime / "swarm-pool.pid")
    log_dir = _p("log_dir", runtime / "logs")
    return {
        "pidfile": pidfile,
        "lock": _p("swarm_pool_lock", Path(str(pidfile) + ".lock")),
        "heartbeat": _p("swarm_pool_heartbeat", runtime / "swarm-pool.heartbeat.json"),
        "snapshot": _p("swarm_pool_status_snapshot", runtime / "swarm-pool-status.json"),
        "log_dir": log_dir,
        "run_log": log_dir / "swarm-pool-run.log",
        "supervisor_log": log_dir / "swarm-pool-supervisor.log",
    }


def _swarmctl_base(config: dict[str, Any]) -> list:
    repo = str(config.get("swarm_repo") or "").strip()
    if not repo:
        raise PoolSupervisorError("pool 子命令不可用: router_config 缺 swarm_repo")
    swarmctl = Path(repo) / "scripts" / "swarmctl.py"
    if not swarmctl.is_file():
        raise PoolSupervisorError(f"pool 子命令不可用: 缺少 {swarmctl}")
    return [sys.executable, str(swarmctl)]


def _db_of(config: dict[str, Any]) -> str:
    db = str(config.get("swarm_v2_db") or "").strip()
    if not db:
        raise PoolSupervisorError("pool 子命令不可用: router_config 缺 swarm_v2_db")
    return db


def _parse_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise PoolSupervisorError(f"pool 子命令输出非 JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# CLI 封装(全部经注入的 runner,便于离线测试)
# ---------------------------------------------------------------------------

def _run_swarmctl(config: dict[str, Any], args: list, *, runner: Callable,
                  timeout: int = DEFAULT_TIMEOUT) -> Any:
    cmd = _swarmctl_base(config) + args
    try:
        proc = runner(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
            cwd=str(config.get("swarm_repo") or "."),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PoolSupervisorError(f"pool 子命令不可执行: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise PoolSupervisorError(
            f"pool 子命令失败 rc={proc.returncode}: {detail[:400]}")
    return _parse_json(proc.stdout)


def pool_status(config: dict[str, Any], *, size: int, runner: Callable = subprocess.run,
                timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """`swarmctl pool status --json`(只读)。失败 ⇒ PoolSupervisorError(含 pool)。"""
    out = _run_swarmctl(
        config, ["pool", "status", "--size", str(size), "--db", _db_of(config), "--json"],
        runner=runner, timeout=timeout)
    if not isinstance(out, dict) or not isinstance(out.get("workers"), list):
        raise PoolSupervisorError(f"pool status 返回非预期 JSON: {str(out)[:200]}")
    return out


def provision_pool(config: dict[str, Any], *, size: int, roles_json: str = "{}",
                   runner: Callable = subprocess.run,
                   timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """`swarmctl pool provision --json`(幂等;G33 闭集角色)。"""
    args = ["pool", "provision", "--size", str(size), "--roles-json", roles_json,
            "--db", _db_of(config), "--json"]
    out = _run_swarmctl(config, args, runner=runner, timeout=timeout)
    if not isinstance(out, dict) or not isinstance(out.get("provisioned"), list):
        raise PoolSupervisorError(f"pool provision 返回非预期 JSON: {str(out)[:200]}")
    return out


# ---------------------------------------------------------------------------
# 进程存活(/proc 路径型扫描;seed 用字符类,不裸 pgrep)
# ---------------------------------------------------------------------------

def _process_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def find_pool_processes(swarm_repo: Path, *, proc_root: Path = Path("/proc")) -> list[int]:
    """Return pids whose cmdline is `<repo>/scripts/swarmctl.py pool run …`.

    路径型匹配:cmdline 必须同时含 swarmctl.py、`pool`、`run`(用字符类正则
    `swarmct[l]\\.py` / `poo[l]` / `ru[n]`,故本函数自己的命令行不会被命中;
    也不使用裸 `pgrep -f`)。
    """
    repo = str(Path(swarm_repo))
    found: list[int] = []
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        flat = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
        if repo not in flat:
            continue
        if _POOL_RUN_RE.search(flat):
            found.append(int(entry.name))
    return sorted(found)


def read_pidfile(path: Path) -> Optional[int]:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(text)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


# ---------------------------------------------------------------------------
# 锁(flock 独占;并发只有一个能拉起)
# ---------------------------------------------------------------------------

def acquire_lock(lock_path: Path):
    """Try an exclusive non-blocking flock; return the open fd, or None if busy."""
    if fcntl is None:  # pragma: no cover - non-POSIX
        raise PoolSupervisorError("pool supervisor 需要 fcntl/flock(本平台不支持)")
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def release_lock(fd) -> None:
    if fd is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# 拉起(默认实现;测试注入 stub)
# ---------------------------------------------------------------------------

def build_pool_run_cmd(config: dict[str, Any], *, size: int, interval: float) -> list:
    """`swarmctl pool run` 前台常驻命令(纯函数;可测)。"""
    return _swarmctl_base(config) + [
        "pool", "run", "--size", str(size), "--interval", str(interval),
        "--db", _db_of(config),
    ]


def default_launch(cmd: list, *, log_path: Path, cwd: Path,
                   env: Optional[dict] = None) -> int:
    """Detached 起常驻进程;stdout/stderr 落 log_path(失败上抛,绝不假装成功)。

    ``env`` 缺省 = :func:`_safe_io.pool_worker_environment` 白名单结果
    (W12 收敛:不再继承 ``os.environ``;环境里没有的键不会注入默认值)。
    """
    if env is None:
        env, _ = pool_worker_environment(proxy_url=resolve_worker_proxy())
    # W20:stdout/stderr 改走有界边界(管道→detached 边界进程,轮转封顶),
    # 余量不足时零副作用拒绝;其余 Popen 语义(start_new_session/close_fds/
    # 失败上抛)与原派工逐字一致。
    return int(log_boundary.spawn_bounded(
        list(cmd),
        log_path=Path(log_path),
        cwd=Path(cwd),
        env=env,
    ))


def _append_log(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line.rstrip("\n") + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 观测面(快照 + heartbeat,供健康检查读取)
# ---------------------------------------------------------------------------

def _write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(Path(path), json.dumps(payload, ensure_ascii=False, indent=2,
                                             sort_keys=True) + "\n")


def read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _active_worker(status: dict[str, Any]) -> Optional[dict[str, Any]]:
    """存活口径(W13-b) = `conn_state=='open'` **且** `not stale`。

    缺陷 A:池进程被 SIGTERM 后连接行仍是 open(心跳冻结),旧口径只看
    `conn_state` 便误判存活。stale-open ⇒ 视为死亡,照常拉起。
    向后兼容:pool status 未带 `stale` 字段(旧版)时按未 stale 处理。
    """
    for worker in status.get("workers") or []:
        if isinstance(worker, dict) and worker.get("conn_state") == "open" \
                and not worker.get("stale"):
            return worker
    return None


def _stale_workers(status: dict[str, Any]) -> list[dict[str, Any]]:
    """stale-open 连接(= 幽灵连接:进程已死但连接行未收尸)。"""
    out: list[dict[str, Any]] = []
    for worker in status.get("workers") or []:
        if isinstance(worker, dict) and worker.get("conn_state") == "open" \
                and worker.get("stale"):
            out.append(worker)
    return out


def _stale_evidence(status: dict[str, Any]) -> list[dict[str, Any]]:
    """可观测的 stale 证据(heartbeat/返回值/日志逐条带出,不许静默)。"""
    return [{"agent_id": w.get("agent_id"), "conn_id": w.get("conn_id"),
             "age_seconds": w.get("conn_age_seconds"), "stale": True}
            for w in _stale_workers(status)]


def reap_stale_connections(config: dict[str, Any], *, runner: Callable = subprocess.run,
                           timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """`swarmctl conn reap --json`(W13-a 收尸口;调用方负责响亮记录失败)。"""
    out = _run_swarmctl(
        config, ["conn", "reap", "--db", _db_of(config), "--json"],
        runner=runner, timeout=timeout)
    if not isinstance(out, dict):
        raise PoolSupervisorError(f"conn reap 返回非预期 JSON: {str(out)[:200]}")
    return out


def _try_reap_stale(config: dict[str, Any], paths: dict[str, Path], deps: "SupervisorDeps",
                    stale_evidence: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """有 stale 证据才收尸;命令不存在/失败 ⇒ 响亮记录,**绝不阻塞拉起**。

    无 stale 证据时返回 None(既不调用命令、也不写心跳噪声)。
    """
    if not stale_evidence:
        return None
    try:
        if deps.reap is not None:
            out = deps.reap(config)
        else:
            out = reap_stale_connections(config, runner=deps.runner)
        count = out.get("count") if isinstance(out, dict) else None
        _append_log(paths["supervisor_log"],
                    f"{deps.now()} conn reap ok: reaped={count}"
                    f" stale={[e['agent_id'] for e in stale_evidence]}")
        return {"ok": True, "count": count}
    except Exception as exc:  # noqa: BLE001 -- 收尸失败不得阻塞拉起,但必须响亮
        detail = f"{type(exc).__name__}: {exc}"
        _append_log(paths["supervisor_log"],
                    f"{deps.now()} conn reap 失败(不阻塞拉起): {detail}")
        return {"ok": False, "error": detail}


def pool_health_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """健康检查读接口:池状态快照 + heartbeat(文件缺失即显式 None,不编造)。"""
    paths = supervisor_paths(config)
    return {
        "status_snapshot": read_json(paths["snapshot"]),
        "heartbeat": read_json(paths["heartbeat"]),
        "pidfile_pid": read_pidfile(paths["pidfile"]),
        "paths": {key: str(value) for key, value in paths.items()},
    }


@dataclass
class SupervisorDeps:
    """注入点(测试用 stub;默认 = 真实 CLI / Popen / /proc)。"""
    runner: Callable = subprocess.run
    launch: Callable = default_launch
    process_alive: Callable = _process_alive
    find_pool_processes: Callable = find_pool_processes
    now: Callable = utc_now
    #: W13-b 收尸注入点(config -> dict);None ⇒ 走 `conn reap` 真 CLI。
    reap: Optional[Callable] = None
    extra: dict = field(default_factory=dict)


def supervise(config: dict[str, Any], *, size: Optional[int] = None,
              roles_json: Optional[str] = None, interval: Optional[float] = None,
              deps: Optional[SupervisorDeps] = None) -> dict[str, Any]:
    """One supervision pass: provision → liveness → launch-if-dead.

    Returns a JSON-serializable result; ``ok=False`` + ``rc`` carries the loud
    failure (CLI maps it to a non-zero exit).  Never launches when the flock is
    held by a concurrent invocation.
    """
    deps = deps or SupervisorDeps()
    size = _int(size if size is not None else config.get("swarm_pool_size"),
                DEFAULT_POOL_SIZE)
    roles_json = roles_json if roles_json is not None \
        else str(config.get("swarm_pool_roles_json") or "{}")
    interval = float(interval if interval is not None
                     else config.get("swarm_pool_interval", DEFAULT_INTERVAL_SECONDS))
    paths = supervisor_paths(config)
    repo = Path(str(config.get("swarm_repo") or "."))

    result: dict[str, Any] = {
        "ok": True, "rc": 0, "launched": False, "alive_before": False,
        "pid": None, "reason": "", "size": size,
        "stale_evidence": [], "reap": None,
        "snapshot_path": str(paths["snapshot"]),
        "heartbeat_path": str(paths["heartbeat"]),
    }

    lock_fd = acquire_lock(paths["lock"])
    if lock_fd is None:
        # 并发调用:另一实例已在跑 —— 不重复拉起,退出 0(非错误)。
        result["reason"] = "pool supervisor lock busy: 另一实例正在保活"
        return result
    try:
        # 0. 拉起环境收敛(W12)+ 代理注入(W15-b):先黑名单 → 再按配置注入
        #    代理 → 再白名单;被剔键名可观测(只记键名)。
        worker_env, env_dropped = pool_worker_environment(
            proxy_url=resolve_worker_proxy(config))
        result["env_dropped_count"] = len(env_dropped)
        result["env_dropped_sample"] = env_dropped[:5]
        _append_log(paths["supervisor_log"],
                    f"{deps.now()} pool worker env whitelist: "
                    f"dropped={len(env_dropped)} sample={env_dropped[:5]}")

        # 1. 供给池身份(幂等):全部已注册 ⇒ 跳过,不重复建身份。
        status = pool_status(config, size=size, runner=deps.runner)
        if not all(bool(w.get("registered")) for w in status.get("workers") or []):
            provision_pool(config, size=size, roles_json=roles_json, runner=deps.runner)
            status = pool_status(config, size=size, runner=deps.runner)
        _write_json(paths["snapshot"], status)

        # 1b. 新鲜度证据(W13-b):stale-open = 幽灵连接;必须可观测、不许静默。
        stale_evidence = _stale_evidence(status)
        result["stale_evidence"] = stale_evidence
        if stale_evidence:
            _append_log(paths["supervisor_log"],
                        f"{deps.now()} stale-open connections detected:"
                        f" {stale_evidence}")

        # 2. 存活判定:新鲜连接态(open 且 not stale) 或 /proc 里活的
        #    `pool run` 进程 或 pidfile 指向的进程仍活。
        active = _active_worker(status)
        pid_from_file = read_pidfile(paths["pidfile"])
        pid_alive = bool(pid_from_file and deps.process_alive(pid_from_file))
        procs = deps.find_pool_processes(repo)
        alive = bool(active) or pid_alive or bool(procs)
        result["alive_before"] = alive
        result["procs"] = procs
        if alive:
            result["pid"] = pid_from_file or (procs[0] if procs else None)
            result["reason"] = "pool worker already alive: 不重复拉起"
            result["reap"] = _try_reap_stale(config, paths, deps, stale_evidence)
            _write_json(paths["heartbeat"], {
                "ts": deps.now(), "pid": result["pid"],
                "conn": active.get("conn_state") if active else None,
                "slot": active.get("slot") if active else None,
                "size": size, "state": "alive",
                "stale_evidence": stale_evidence,
                "reap": result["reap"],
                "env_dropped_count": len(env_dropped),
                "env_dropped_sample": env_dropped[:5],
            })
            return result

        # 3. 拉起(无存活)。拉起失败 ⇒ 非 0 + 原因 + 日志,绝不记成功。
        cmd = build_pool_run_cmd(config, size=size, interval=interval)
        try:
            pid = deps.launch(cmd, log_path=paths["run_log"],
                              cwd=repo, env=worker_env)
        except Exception as exc:  # noqa: BLE001 -- 拉起失败必须响亮
            reason = f"pool 拉起失败: {type(exc).__name__}: {exc}"
            _append_log(paths["supervisor_log"], f"{deps.now()} {reason}")
            result.update(ok=False, rc=3, reason=reason)
            return result
        if not isinstance(pid, int) or pid <= 0:
            reason = f"pool 拉起失败: 非法 pid {pid!r}"
            _append_log(paths["supervisor_log"], f"{deps.now()} {reason}")
            result.update(ok=False, rc=3, reason=reason)
            return result

        # 3b. 拉起成功后收尸 stale-open(失败不阻塞;已在 _try_reap_stale 响亮记录)。
        result["reap"] = _try_reap_stale(config, paths, deps, stale_evidence)

        # 4. pidfile + heartbeat(时间戳 + pid + conn + stale 证据/收尸结果)
        atomic_write_text(paths["pidfile"], f"{pid}\n")
        _write_json(paths["heartbeat"], {
            "ts": deps.now(), "pid": pid, "conn": None,
            "slot": None, "size": size, "state": "launched",
            "cmd": cmd,
            "stale_evidence": stale_evidence,
            "reap": result["reap"],
            "env_dropped_count": len(env_dropped),
            "env_dropped_sample": env_dropped[:5],
        })
        result.update(launched=True, pid=pid, reason="pool worker launched")
        _append_log(paths["supervisor_log"],
                    f"{deps.now()} launched pool run pid={pid} size={size}")
        return result
    finally:
        release_lock(lock_fd)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_config(path: str) -> dict[str, Any]:
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PoolSupervisorError(f"无法读取配置 {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise PoolSupervisorError(f"配置根须为对象: {path}")
    return config


def _status_only(config: dict[str, Any], deps: SupervisorDeps, size: int) -> dict[str, Any]:
    status = pool_status(config, size=size, runner=deps.runner)
    paths = supervisor_paths(config)
    _write_json(paths["snapshot"], status)
    return {
        "ok": True, "rc": 0, "size": size,
        "status": status,
        "heartbeat": read_json(paths["heartbeat"]),
        "paths": {key: str(value) for key, value in paths.items()},
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="swarm_pool_supervisor.py",
        description="常驻池 worker 监督保活(W11-a;幂等/单实例/响亮失败)")
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--size", type=int, default=None)
    parser.add_argument("--roles-json", default=None)
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--status", action="store_true",
                        help="只读观测:打印 pool status 快照 + heartbeat,不拉起")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = _load_config(args.config)
        deps = SupervisorDeps()
        size = _int(args.size if args.size is not None else config.get("swarm_pool_size"),
                    DEFAULT_POOL_SIZE)
        if args.status:
            result = _status_only(config, deps, size)
        else:
            result = supervise(config, size=args.size, roles_json=args.roles_json,
                               interval=args.interval, deps=deps)
    except PoolSupervisorError as exc:
        print(f"pool supervisor 失败: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{'OK' if result.get('ok') else 'FAIL'}: "
              f"launched={result.get('launched')} alive={result.get('alive_before')} "
              f"pid={result.get('pid')} reason={result.get('reason')}")
    return 0 if result.get("ok") else int(result.get("rc") or 1)


if __name__ == "__main__":
    sys.exit(main())
