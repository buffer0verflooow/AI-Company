#!/usr/bin/env python3
"""蜂群接入健康检查 — 早发现执行链路断点, 防 2026-08-09 事件重演。

检查项:
  1. 路径有效性: swarm_runner / swarmctl / executor / _safe_io / DB
  2. 最近 run 健康: 长期 pending (>=30min 未消费) 的 run 数量
  3. 最近执行痕迹: 24h 内是否有 completed 任务

用法:
  python3 swarm_health_check.py            # 检查, 异常 exit 1
  python3 swarm_health_check.py --json     # JSON 输出 (cron 用)

退出码: 0 = 全部正常, 1 = 有断点 (可用于 cron 报警)。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "router_config.json"

CHECKS: list[dict] = []


def _fail(name: str, detail: str) -> None:
    CHECKS.append({"check": name, "ok": False, "detail": detail})


def _ok(name: str, detail: str = "") -> None:
    CHECKS.append({"check": name, "ok": True, "detail": detail})


def main() -> int:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # A health check must report its own broken configuration cleanly
        # instead of dying with an unhandled traceback.
        _fail("配置", f"无法读取 {CONFIG_PATH}: {exc}")
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
    swarm_repo = Path(config["swarm_repo"])

    # 1. 路径有效性
    runner = swarm_repo / "scripts" / "swarm_runner.py"
    _ok("swarm_runner.py 存在", str(runner)) if runner.is_file() else _fail(
        "swarm_runner.py 存在", f"缺失: {runner} (launch_runner 依赖, 8e00a60 移到 scripts/)")

    swarmctl = swarm_repo / "scripts" / "swarmctl.py"
    _ok("swarmctl.py 存在", str(swarmctl)) if swarmctl.is_file() else _fail(
        "swarmctl.py 存在", f"缺失: {swarmctl} (Router swarm_command 依赖)")

    executor = Path(config["executor"])
    _ok("executor 存在", str(executor)) if executor.is_file() else _fail(
        "executor 存在", f"缺失: {executor} (worker 执行器)")

    safe_io = executor.parent / "_safe_io.py"
    _ok("_safe_io.py 存在", str(safe_io)) if safe_io.is_file() else _fail(
        "_safe_io.py 存在", f"缺失: {safe_io} (executor 环境清理依赖)")

    db_path = Path(config["swarm_db"])
    if not db_path.is_file():
        _fail("swarm DB 存在", f"缺失: {db_path}")
        db = None
    else:
        _ok("swarm DB 存在", str(db_path))
        try:
            db = sqlite3.connect(str(db_path))
        except sqlite3.Error as exc:
            _fail("swarm DB 存在", f"无法打开: {exc}")
            db = None

    # 2. 最近 run 健康: 长期 pending
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

            # 3. 24h 内执行痕迹
            done24 = db.execute(
                """SELECT COUNT(*) FROM agent_tasks
                   WHERE status='completed' AND updated_at >= datetime('now', '-1 day')"""
            ).fetchone()[0]
            if done24:
                _ok("24h 内执行痕迹", f"{done24} 个任务 completed")
            else:
                _fail("24h 内执行痕迹", "24h 内无 completed 任务 (蜂群可能闲置或链路断开)")
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
