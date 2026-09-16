#!/usr/bin/env python3
"""蜂群接入健康检查 — 早发现执行链路断点, 防 2026-08-09 事件重演。

检查项:
  1. 路径有效性: swarm_v2 活库 / swarmctl / executor / _safe_io
  2. v2 schema 指纹: swarm_v2.db 含 v2 专有表 + knowledge_entries
  3. v1 墓碑说明: v1 库位已停用(非 error)
  4. 最近 run 健康: 长期 pending (>=30min 未消费) 的 run 数量
  5. 最近执行痕迹: 24h 内是否有 completed 任务(市场零流量为正常态)

用法:
  python3 swarm_health_check.py            # 检查, 异常 exit 1
  python3 swarm_health_check.py --json     # JSON 输出 (cron 用)

退出码: 0 = 全部正常, 1 = 有断点 (可用于 cron 报警)。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from ._safe_io import sqlite_uri
    from .swarm_db_guard import V2_MARKER_TABLES, SwarmDbUnavailable, check_v2_db
except ImportError:  # direct execution from automation/
    from _safe_io import sqlite_uri
    from swarm_db_guard import (  # type: ignore[no-redef]
        V2_MARKER_TABLES,
        SwarmDbUnavailable,
        check_v2_db,
    )

CONFIG_PATH = Path(__file__).resolve().parent / "router_config.json"

CHECKS: list[dict] = []


def _fail(name: str, detail: str) -> None:
    CHECKS.append({"check": name, "ok": False, "detail": detail})


def _ok(name: str, detail: str = "") -> None:
    CHECKS.append({"check": name, "ok": True, "detail": detail})


def _emit_config_failure() -> int:
    failed = [c for c in CHECKS if not c["ok"]]
    if "--json" in sys.argv:
        print(json.dumps({
            "healthy": False,
            "checks": CHECKS,
            "failed_count": len(failed),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=1))
    else:
        print(f"❌ 配置: {failed[0]['detail']}")
    return 1


def main() -> int:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("config root must be an object")
    except (OSError, ValueError) as exc:
        # A health check must report its own broken configuration cleanly
        # instead of dying with an unhandled traceback.
        _fail("配置", f"无法读取 {CONFIG_PATH}: {exc}")
        return _emit_config_failure()
    # A syntactically valid config that is missing a required key (or holds a
    # non-string value) must be reported the same way: the health check itself
    # is the thing being checked, so it never dies with a raw KeyError/TypeError.
    missing = [
        key for key in ("swarm_repo", "executor", "swarm_v2_db")
        if not isinstance(config.get(key), str) or not config[key].strip()
    ]
    if missing:
        _fail("配置", f"router_config.json 缺少必需字符串键: {', '.join(missing)}")
        return _emit_config_failure()
    swarm_repo = Path(config["swarm_repo"])

    # 1. 组件路径有效性(D-16.3: 改断言 v2 面)
    swarmctl = swarm_repo / "scripts" / "swarmctl.py"
    if swarmctl.is_file():
        _ok("swarmctl.py 存在", str(swarmctl))
        # v2 的执行面由 `swarmctl worker` 承担,与 v1 swarm_runner.py 无关;
        # 用 `--help` 证明解释器能真正加载它,而不是只看到文件在。
        try:
            proc = subprocess.run(
                [sys.executable, str(swarmctl), "--help"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if proc.returncode == 0:
                _ok("swarmctl.py --help", "rc=0")
            else:
                _fail("swarmctl.py --help", f"rc={proc.returncode}: {proc.stderr.strip()[:200]}")
        except (OSError, subprocess.SubprocessError) as exc:
            _fail("swarmctl.py --help", f"无法执行: {exc}")
    else:
        _fail("swarmctl.py 存在", f"缺失: {swarmctl} (v2 worker 依赖)")

    # v1 墓碑说明: 不是 error。v1 库位自 M0.2 起是目录墓碑,读类已 repoint v2,
    # 安全线 v1 入口已加护栏(D-16.2)。
    v1_db = Path(str(config.get("swarm_db", "")))
    if str(config.get("swarm_db", "")).strip() and v1_db.is_file():
        _ok("v1 库位已停用", f"{v1_db} 仍为文件但已不再作为权威活库(安全线 v1 入口有护栏)")
    else:
        _ok("v1 库位已停用", f"{v1_db} 墓碑/缺席;读类已 repoint 到 swarm_v2.db")

    executor = Path(config["executor"])
    _ok("executor 存在", str(executor)) if executor.is_file() else _fail(
        "executor 存在", f"缺失: {executor} (worker 执行器)")

    safe_io = executor.parent / "_safe_io.py"
    _ok("_safe_io.py 存在", str(safe_io)) if safe_io.is_file() else _fail(
        "_safe_io.py 存在", f"缺失: {safe_io} (executor 环境清理依赖)")

    # 2. v2 活库存在 + schema 指纹为 v2
    db_path = Path(config["swarm_v2_db"])
    db = None
    if not db_path.is_file():
        _fail("swarm_v2 活库存在", f"缺失: {db_path}")
    else:
        try:
            check_v2_db(db_path)
        except SwarmDbUnavailable as exc:
            _fail("swarm_v2 schema 指纹", str(exc))
        else:
            _ok("swarm_v2 活库存在", str(db_path))
            _ok("swarm_v2 schema 指纹", "v2 专有表 " + ",".join(V2_MARKER_TABLES) + " + knowledge_entries")
            try:
                # Health check is strictly read-only: a read-write connect would
                # silently materialize a fresh empty DB file if the file vanished
                # between the is_file() check above and the connect (TOCTOU), and
                # the probe never writes.  Read-only mode reports the miss instead.
                db = sqlite3.connect(sqlite_uri(db_path, mode="ro"), uri=True)
            except sqlite3.Error as exc:
                _fail("swarm_v2 活库存在", f"无法打开: {exc}")
                db = None

    # 3. 最近 run 健康: 长期 pending
    if db is not None:
        try:
            rows = db.execute(
                """SELECT run_id, swarm_name, status, created_at
                   FROM swarm_runs WHERE status='running' ORDER BY created_at DESC LIMIT 20"""
            ).fetchall()
            stale = 0
            for rid, _name, _status, created in rows:
                try:
                    # swarm_* timestamps are written by SQLite ``datetime('now')``,
                    # which is UTC.  Compare against UTC now instead of naive
                    # local time: on an Asia/Shanghai host a naive comparison
                    # would age every run by 8 hours and false-alarm the cron.
                    created_dt = datetime.strptime(
                        created, "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    continue
                now_utc = datetime.now(timezone.utc)
                # 只报警 24h 内创建的 run —— 更早的属于历史遗留(如 08-09 断点期),
                # 不算当前链路断点
                if now_utc - created_dt > timedelta(days=1):
                    continue
                if now_utc - created_dt > timedelta(minutes=30):
                    # 检查该 run 的任务是否真的没消费
                    pend = db.execute(
                        "SELECT COUNT(*) FROM agent_tasks WHERE run_id=? AND status IN ('pending','claimed')",
                        (rid,),
                    ).fetchone()[0]
                    done = db.execute(
                        "SELECT COUNT(*) FROM agent_tasks WHERE run_id=? AND status='completed'",
                        (rid,),
                    ).fetchone()[0]
                    if pend > 0 and done == 0:
                        stale += 1
            if stale:
                _fail("最近 run 无断点", f"{stale} 个 run 卡 pending ≥30min (执行链路可能断开)")
            else:
                _ok("最近 run 无断点", "无长期 pending 的 run")

            # 3. 24h 内执行痕迹 —— 参考项。公司内容线为"用户触发路由即起 per-task
            #    worker"(D-16.4),无真实流量时市场为空是**正常态**,不是断点。
            done24 = db.execute(
                """SELECT COUNT(*) FROM agent_tasks
                   WHERE status='completed' AND updated_at >= datetime('now', '-1 day')"""
            ).fetchone()[0]
            if done24:
                _ok("24h 内执行痕迹", f"{done24} 个任务 completed")
            else:
                _ok(
                    "24h 内执行痕迹",
                    "0 条 completed —— 市场尚无真实流量(内容线由用户发任务触发,非断点)",
                )
        except sqlite3.Error as exc:
            _fail("DB 查询", str(exc))
        finally:
            db.close()

    # 输出
    failed = [c for c in CHECKS if not c["ok"]]
    if "--json" in sys.argv:
        print(json.dumps({
            "healthy": not failed,
            "checks": CHECKS,
            "failed_count": len(failed),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False, indent=1))
    else:
        for c in CHECKS:
            mark = "✅" if c["ok"] else "❌"
            print(f"{mark} {c['check']}: {c['detail']}")
        print(f"\n{'✅ 蜂群接入健康' if not failed else f'❌ {len(failed)} 项异常'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
