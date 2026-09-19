#!/usr/bin/env python3
"""C-7 公司侧 v2 治理只读读数(判定 / 声誉 / 预算 / 审计)。

事实:公司侧对 v2 的读取只有 `task result` 与健康检查的库可用性判断;判定分布 /
声誉 / 预算 / 审计对公司不可见。蜂群侧读数命令已存在
(`swarmctl metrics {task,decision,run,system}`、`reputation verify`、`bus tail`、
`switch list`、`conn list`)。本模块只**消费**它们,归一化成一份快照 dict。

**硬纪律**:
  - **只读**:读蜂群库一律 `mode=ro`(`_ro`),SQL 全是 SELECT;读数命令只走
    蜂群既有只读入口(`metrics`/`reputation verify`/`bus tail`)。本模块不写
    任何蜂群库;快照落盘是公司侧 JSON 文件(`persist_snapshot`,单独调用)。
  - **不可用即响亮**:任一类读数命令失败 / 库读失败 ⇒ 该类
    `{"available": False, "reason": "..."}`,人类面输出 `不可用(<原因>)`;
    **禁止**静默省略或填 0 冒充。
  - **不改蜂群判定语义**:纯消费者。

快照文件:`swarm-governance-YYYY-MM-DD.json`(目录默认
`<state_db 同级>/governance-snapshots/`);delta 由 `compute_delta` 逐数值字段计算。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

try:  # package import (``automation.swarm_governance_readout``)
    from . import finance_ledger
    from ._safe_io import sqlite_uri
except ImportError:  # direct ``python automation/swarm_governance_readout.py``
    import finance_ledger  # type: ignore[no-redef]
    from _safe_io import sqlite_uri  # type: ignore[no-redef]


CATEGORIES: tuple[str, ...] = ("decision", "reputation", "budget", "audit")
UNAVAILABLE_MARK = "不可用"
DEFAULT_SNAPSHOT_DIRNAME = "governance-snapshots"
BUS_TAIL_LIMIT = 500

#: 读数命令 runner 签名:`runner(args: list[str]) -> dict`(失败抛异常)。
Runner = Callable[[list[str]], dict[str, Any]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _ro(path: Path) -> Iterator[sqlite3.Connection]:
    """只读连接(结束即关闭;绝不写库)。"""
    con = sqlite3.connect(sqlite_uri(Path(path), mode="ro"), uri=True)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


class ReadoutError(RuntimeError):
    """C-7 读数领域错误(缺路径等;CLI 干净报错)。"""


# ---------------------------------------------------------------------------
# 默认 runner(swarmctl;只读命令)
# ---------------------------------------------------------------------------

def _swarm_python(config: dict[str, Any]) -> str:
    explicit = str(config.get("swarm_python") or "").strip()
    if explicit:
        return explicit
    repo = str(config.get("swarm_repo") or "").strip()
    candidate = Path(repo) / ".venv" / "bin" / "python"
    return str(candidate) if candidate.is_file() else sys.executable


def default_runner(config: dict[str, Any], *, timeout: int = 30) -> Runner:
    """构造默认 runner:`swarmctl --db <v2> <args…>`(输出必须为 JSON 对象)。"""
    repo = str(config.get("swarm_repo") or "").strip()
    swarm_db = str(config.get("swarm_v2_db") or config.get("swarm_db") or "").strip()
    swarmctl = str(Path(repo) / "scripts" / "swarmctl.py") if repo else ""
    python = _swarm_python(config)

    def run(args: list[str]) -> dict[str, Any]:
        if not swarmctl or not Path(swarmctl).is_file():
            raise ReadoutError(f"swarmctl 缺失: {swarmctl!r}")
        if not swarm_db:
            raise ReadoutError("swarm_v2 活库未配置")
        cmd = [python, swarmctl, "--db", swarm_db, *args]
        proc = subprocess.run(cmd, cwd=repo or None, capture_output=True,
                              text=True, timeout=timeout, check=False)
        if proc.returncode != 0:
            raise ReadoutError(proc.stderr.strip() or proc.stdout.strip()
                               or f"swarmctl rc={proc.returncode}")
        text = (proc.stdout or "").strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReadoutError(f"swarmctl 输出非 JSON: {text[-200:]!r}") from exc
        if not isinstance(value, dict):
            raise ReadoutError("swarmctl 输出不是 JSON 对象")
        return value

    return run


def _swarm_db_of(config: dict[str, Any]) -> Path:
    raw = str(config.get("swarm_v2_db") or config.get("swarm_db") or "").strip()
    if not raw:
        raise ReadoutError("swarm_v2 活库未配置")
    return Path(raw)


# ---------------------------------------------------------------------------
# 四类读数(每类失败 ⇒ available=False + reason,绝不填 0 冒充)
# ---------------------------------------------------------------------------

def _json_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_decision(runner: Runner, swarm_db: Path) -> dict[str, Any]:
    """判定:`metrics decision` + `judge_decision`/`exec_verify` 审计派生计数。"""
    try:
        metrics = runner(["metrics", "decision"])
        with _ro(swarm_db) as con:
            judge_rows = con.execute(
                "SELECT payload_json FROM audit_events WHERE event_type='judge_decision'"
                " ORDER BY id").fetchall()
            criteria_rows = con.execute(
                "SELECT payload_json FROM audit_events WHERE event_type='exec_verify'"
                " ORDER BY id").fetchall()
    except Exception as exc:  # noqa: BLE001 -- 读数不可用要响亮,不静默
        return {"available": False, "reason": f"{exc.__class__.__name__}: {exc}"}
    pass_count = fail_count = 0
    for row in judge_rows:
        payload = _json_payload(row[0])
        if payload.get("accepted") is True:
            pass_count += 1
        elif payload.get("accepted") is False:
            fail_count += 1
    criteria = {"passed": 0, "failed": 0, "refused": 0, "timeout": 0}
    for row in criteria_rows:
        payload = _json_payload(row[0])
        for key in criteria:
            value = payload.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            criteria[key] += int(value)
    return {
        "available": True,
        "metrics_decision": metrics,
        "judge_decisions": len(judge_rows),
        "pass": pass_count,
        "fail": fail_count,
        "refused": criteria["refused"],
        "timeout": criteria["timeout"],
        "criteria": criteria,
    }


def read_reputation(runner: Runner, swarm_db: Path) -> dict[str, Any]:
    """声誉:`reputation verify`(只读)+ agent 维度 pass_rate/weighted 与最近事件。"""
    try:
        verify = runner(["reputation", "verify", "--json"])
        with _ro(swarm_db) as con:
            event_rows = con.execute(
                "SELECT agent_id, delta, reason, task_id, created_at"
                " FROM reputation_events ORDER BY id").fetchall()
            judge_rows = con.execute(
                "SELECT payload_json FROM audit_events WHERE event_type='judge_decision'"
                " ORDER BY id").fetchall()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{exc.__class__.__name__}: {exc}"}
    recent: dict[str, list[dict[str, Any]]] = {}
    for row in event_rows:
        recent.setdefault(str(row["agent_id"]), []).append({
            "delta": row["delta"], "reason": row["reason"],
            "task_id": row["task_id"], "created_at": row["created_at"],
        })
    latest: dict[str, dict[str, Any]] = {}
    for row in judge_rows:
        payload = _json_payload(row[0])
        rep = payload.get("reputation")
        if isinstance(rep, dict) and rep.get("agent_id"):
            latest[str(rep["agent_id"])] = rep
    agents: list[dict[str, Any]] = []
    for agent_id in sorted(set(latest) | set(recent)):
        rep = latest.get(agent_id, {})
        agents.append({
            "agent_id": agent_id,
            "pass_rate": rep.get("pass_rate"),
            "weighted_pass": rep.get("weighted_pass"),
            "weighted_fail": rep.get("weighted_fail"),
            "events": rep.get("events"),
            "reputation_cached": rep.get("reputation_cached"),
            "recent_events": recent.get(agent_id, [])[-5:],
        })
    return {
        "available": True,
        "verify": verify,
        "drift": len(verify.get("drift") or []),
        "ok": bool(verify.get("ok")),
        "agents": agents,
        "agent_count": len(agents),
    }


def latest_run_id(swarm_db: Path) -> Optional[str]:
    try:
        with _ro(swarm_db) as con:
            row = con.execute(
                "SELECT run_id FROM swarm_runs ORDER BY created_at DESC, run_id LIMIT 1"
            ).fetchone()
            return str(row[0]) if row is not None else None
    except sqlite3.Error as exc:
        raise ReadoutError(f"读取 swarm_runs 失败: {exc}") from exc


def read_budget(runner: Runner, swarm_db: Path, run_id: Optional[str] = None) -> dict[str, Any]:
    """预算:token 实测 vs estimated、越顶计数、run 级预算使用率。"""
    try:
        with _ro(swarm_db) as con:
            agg = con.execute(
                "SELECT COUNT(*) AS tasks,"
                " COALESCE(SUM(token_cost), 0) AS measured,"
                " COALESCE(SUM(estimated_tokens), 0) AS estimated,"
                " SUM(CASE WHEN token_cost IS NULL THEN 1 ELSE 0 END) AS unmeasured"
                " FROM agent_tasks").fetchone()
            trace_rows = con.execute(
                "SELECT payload_json FROM audit_events WHERE event_type='agent_trace_close'"
                " ORDER BY id").fetchall()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{exc.__class__.__name__}: {exc}"}
    if run_id is None:
        try:
            run_id = latest_run_id(swarm_db)
        except ReadoutError as exc:
            return {"available": False, "reason": str(exc)}
    if not run_id:
        return {"available": False, "reason": "库内无 swarm_runs(run 级预算不可读)"}
    try:
        run_metrics = runner(["metrics", "run", str(run_id)])
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{exc.__class__.__name__}: {exc}"}
    stops: dict[str, int] = {}
    for row in trace_rows:
        payload = _json_payload(row[0])
        reason = str(payload.get("stop_reason") or "")
        if reason:
            stops[reason] = stops.get(reason, 0) + 1
    budget_exec = run_metrics.get("budget_execution")
    budget_exec = budget_exec if isinstance(budget_exec, dict) else {}
    return {
        "available": True,
        "run_id": str(run_id),
        "metrics_run": run_metrics,
        "tasks": int(agg["tasks"] or 0),
        "measured_tokens": int(agg["measured"] or 0),
        "estimated_tokens": int(agg["estimated"] or 0),
        "unmeasured_tasks": int(agg["unmeasured"] or 0),
        "budget_exceeded": int(stops.get("budget_exceeded", 0)),
        "max_turns_exceeded": int(stops.get("max_turns_exceeded", 0)),
        "run_token_budget": budget_exec.get("token_budget"),
        "run_tokens_spent": budget_exec.get("tokens_spent"),
        "run_budget_utilization": budget_exec.get("execution_ratio"),
    }


def read_audit(runner: Runner, swarm_db: Path) -> dict[str, Any]:
    """审计:`audit_events` 按 event_type 计数(含 `ops_backfill` 接线事件)。"""
    try:
        tail = runner(["bus", "tail", "--since", "0", "--limit", str(BUS_TAIL_LIMIT), "--json"])
        with _ro(swarm_db) as con:
            rows = con.execute(
                "SELECT event_type, COUNT(*) AS n FROM audit_events"
                " GROUP BY event_type ORDER BY event_type").fetchall()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{exc.__class__.__name__}: {exc}"}
    counts = {str(r["event_type"]): int(r["n"]) for r in rows}
    return {
        "available": True,
        "event_counts": counts,
        "total": sum(counts.values()),
        "event_types": len(counts),
        "ops_backfill": counts.get("ops_backfill", 0),
        "bus_tail_events": len(tail.get("events") or []),
    }


def build_readout(config: dict[str, Any], *, run_id: Optional[str] = None,
                  runner: Optional[Runner] = None,
                  now: Optional[str] = None) -> dict[str, Any]:
    """四类读数归一化快照(只读;每类含 available 标记)。"""
    swarm_db = _swarm_db_of(config)
    runner = runner or default_runner(config)
    readout: dict[str, Any] = {
        "captured_at": now or utc_now(),
        "swarm_db": str(swarm_db),
        "swarm_repo": str(config.get("swarm_repo") or ""),
        "run_id": run_id,
        "categories": {},
    }
    builders = {
        "decision": lambda: read_decision(runner, swarm_db),
        "reputation": lambda: read_reputation(runner, swarm_db),
        "budget": lambda: read_budget(runner, swarm_db, run_id),
        "audit": lambda: read_audit(runner, swarm_db),
    }
    for name in CATEGORIES:
        try:
            readout["categories"][name] = builders[name]()
        except Exception as exc:  # noqa: BLE001 -- 任何一类不得拖垮整份快照
            readout["categories"][name] = {
                "available": False, "reason": f"{exc.__class__.__name__}: {exc}"}
    readout["available_categories"] = sorted(
        name for name in CATEGORIES
        if readout["categories"][name].get("available"))
    readout["unavailable_categories"] = sorted(
        name for name in CATEGORIES
        if not readout["categories"][name].get("available"))
    return readout


# ---------------------------------------------------------------------------
# 快照持久化 + 趋势 delta(公司侧文件;不碰蜂群库)
# ---------------------------------------------------------------------------

def snapshot_dir(config: dict[str, Any]) -> Path:
    explicit = str(config.get("swarm_governance_snapshot_dir") or "").strip()
    if explicit:
        return Path(explicit)
    state_db = str(config.get("state_db") or "").strip()
    base = Path(state_db).parent if state_db else Path(finance_ledger.COMPANY_ROOT)
    return base / DEFAULT_SNAPSHOT_DIRNAME


def snapshot_filename(readout: dict[str, Any], *, day: Optional[str] = None) -> str:
    day_value = day or str(readout.get("captured_at") or "")[:10] or "unknown"
    return f"swarm-governance-{day_value}.json"


def persist_snapshot(readout: dict[str, Any], *, config: Optional[dict[str, Any]] = None,
                     directory: Optional[Path] = None,
                     day: Optional[str] = None) -> Path:
    """把快照落成 `swarm-governance-YYYY-MM-DD.json`(公司侧文件,不是蜂群库)。"""
    target = Path(directory) if directory is not None else snapshot_dir(config or {})
    target.mkdir(parents=True, exist_ok=True)
    path = target / snapshot_filename(readout, day=day)
    path.write_text(json.dumps(readout, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")
    return path


def load_snapshot(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReadoutError(f"快照不是 JSON 对象: {path}")
    return value


def list_snapshots(directory: Path) -> list[Path]:
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(root.glob("swarm-governance-*.json"))


def compute_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """逐数值字段算 delta(趋势对比;非数值字段不进 delta)。"""
    deltas: dict[str, dict[str, Any]] = {}

    def walk(path: str, before: Any, after: Any) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in sorted(set(before) | set(after)):
                walk(f"{path}.{key}" if path else str(key), before.get(key), after.get(key))
        elif (isinstance(before, (int, float)) and not isinstance(before, bool)
              and isinstance(after, (int, float)) and not isinstance(after, bool)):
            deltas[path] = {"prev": before, "curr": after, "delta": round(after - before, 6)}

    walk("", previous.get("categories", {}), current.get("categories", {}))
    return deltas


# ---------------------------------------------------------------------------
# 人类可读一屏(数字开头;不可用即响亮)
# ---------------------------------------------------------------------------

def format_category(name: str, category: dict[str, Any]) -> str:
    """人类可读一屏:标签后**先给数字**,再接明细(不可用即显式标注)。"""
    if not category.get("available"):
        return f"治理-{name}: {UNAVAILABLE_MARK}({category.get('reason') or '未知原因'})"
    if name == "decision":
        total = int(category.get("judge_decisions") or 0)
        return (f"治理-判定: {total} 判定 | pass={category['pass']} fail={category['fail']} "
                f"refused={category['refused']} timeout={category['timeout']} "
                f"criteria={category['criteria']['passed']}/"
                f"{category['criteria']['failed']}/"
                f"{category['criteria']['refused']}/{category['criteria']['timeout']}")
    if name == "reputation":
        return (f"治理-声誉: {category['agent_count']} agent | "
                f"drift={category['drift']} ok={category['ok']}")
    if name == "budget":
        return (f"治理-预算: {category['tasks']} 任务 | run={category['run_id']} "
                f"measured={category['measured_tokens']} "
                f"estimated={category['estimated_tokens']} "
                f"budget_exceeded={category['budget_exceeded']} "
                f"max_turns_exceeded={category['max_turns_exceeded']} "
                f"util={category['run_budget_utilization']}")
    if name == "audit":
        return (f"治理-审计: {category['total']} 事件 | "
                f"types={category['event_types']} "
                f"ops_backfill={category['ops_backfill']}")
    return f"治理-{name}: {json.dumps(category, ensure_ascii=False)}"


def format_human(readout: dict[str, Any]) -> list[str]:
    lines = [format_category(name, readout["categories"][name]) for name in CATEGORIES]
    if readout.get("unavailable_categories"):
        lines.append("治理-不可用类: " + ",".join(readout["unavailable_categories"]))
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python automation/swarm_governance_readout.py",
        description="C-7 公司侧 v2 治理只读读数(判定/声誉/预算/审计;不可用即响亮)")
    parser.add_argument("--config",
                        default=str(Path(__file__).resolve().parent / "router_config.json"))
    parser.add_argument("--run-id", default="", help="预算读数的 run(默认库内最新)")
    parser.add_argument("--persist", action="store_true", help="同时落同日快照")
    parser.add_argument("--delta", action="store_true",
                        help="对比最近两份快照并输出数值 delta")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ReadoutError("config 根必须是对象")
        if args.delta:
            snaps = list_snapshots(snapshot_dir(config))
            if len(snaps) < 2:
                raise ReadoutError(f"快照不足两份({len(snaps)}),无法算 delta")
            out = {"previous": str(snaps[-2]), "current": str(snaps[-1]),
                   "delta": compute_delta(load_snapshot(snaps[-2]), load_snapshot(snaps[-1]))}
        else:
            readout = build_readout(config, run_id=args.run_id or None)
            if args.persist:
                out = {"snapshot": str(persist_snapshot(readout, config=config)),
                       "readout": readout}
            else:
                out = readout
    except (ReadoutError, OSError, ValueError, KeyError) as exc:
        print(f"swarm governance readout 失败: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    elif args.delta:
        for key, item in sorted(out["delta"].items()):
            print(f"{key}: {item['prev']} -> {item['curr']} (Δ{item['delta']})")
    else:
        for line in format_human(out):
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
