#!/usr/bin/env python3
"""Build a read-only daily company digest and enqueue it for delivery.

This is intentionally separate from ``company_operator.py``.  Pausing
autonomous execution must not also silence the management readout.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from ._safe_io import read_text_limited, sqlite_uri
    from .company_router import load_config
    from .notification_outbox import enqueue
    from .notification_outbox import summary as outbox_summary
    from .operations_control import latest_origin, utc_now
except ImportError:  # direct execution from automation/
    from _safe_io import read_text_limited, sqlite_uri
    from company_router import load_config
    from notification_outbox import enqueue
    from notification_outbox import summary as outbox_summary
    from operations_control import latest_origin, utc_now


COMPANY_ROOT = Path("/home/pwn/workspace/company")
DEFAULT_CONFIG = COMPANY_ROOT / "automation/router_config.json"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(read_text_limited(path, max_bytes=5 * 1024 * 1024))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None


def _db_rows(path: Path, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    if not path.is_file():
        return []
    try:
        db = sqlite3.connect(sqlite_uri(path, mode="ro"), uri=True)
    except sqlite3.Error:
        return []
    db.row_factory = sqlite3.Row
    try:
        return db.execute(query, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()


def _safe_counter(value: Any) -> int:
    """Coerce a DB/JSON counter to int, degrading to 0 on malformed values.

    The digest is a read-only cron that must keep reporting even when a
    sibling subsystem wrote a corrupt row.
    """
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _safe_float(value: Any) -> float:
    """Coerce a DB/JSON score to float, degrading to 0.0 on malformed values."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _operator_status(config: dict[str, Any]) -> str:
    jobs_path = Path(str(config.get("cron_jobs_path") or "/home/pwn/.hermes/cron/jobs.json"))
    payload = _read_json(jobs_path)
    if isinstance(payload, dict):
        payload = payload.get("jobs")
    jobs = payload if isinstance(payload, list) else []
    for job in jobs:
        if not isinstance(job, dict) or job.get("name") != "company-daily-operator":
            continue
        if job.get("enabled") is False or job.get("state") == "paused":
            paused = str(job.get("paused_at") or "")
            return f"已暂停（{paused or '手动'}）"
        return "运行中"
    return "未注册"


def _review_summary(operations_db: Path) -> tuple[str, str]:
    reviews = _db_rows(
        operations_db,
        """SELECT review_id,review_date,status,executive_summary,delivered
           FROM tvcr_reviews ORDER BY review_date DESC,created_at DESC LIMIT 1""",
    )
    if not reviews:
        return "无", "尚无经营复盘"
    review = reviews[0]
    summary = " ".join(str(review["executive_summary"] or "").split())
    if len(summary) > 260:
        summary = summary[:259].rstrip() + "…"
    return (
        f"{review['review_date']} / {review['status']} / {'已送达' if review['delivered'] else '待送达'}",
        summary or "复盘已生成，但没有摘要。",
    )


def _proposal_summary(operations_db: Path, now: datetime) -> tuple[int, int, int]:
    rows = _db_rows(
        operations_db,
        """SELECT priority,created_at FROM improvement_proposals
           WHERE status='pending_approval'""",
    )
    cutoff = (now - timedelta(hours=72)).astimezone(timezone.utc)
    stale = 0
    for row in rows:
        try:
            created = datetime.fromisoformat(str(row["created_at"] or ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created <= cutoff:
                stale += 1
        except ValueError:
            continue
    return len(rows), sum(1 for row in rows if str(row["priority"] or "") == "P0"), stale


def _run_summary(operations_db: Path, now: datetime) -> tuple[int, int, int, int]:
    start = (now - timedelta(hours=24)).astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = _db_rows(
        operations_db,
        """SELECT status,outcome_status FROM operational_runs
           WHERE COALESCE(completed_at,created_at)>=?""",
        (start,),
    )
    completed = sum(1 for row in rows if str(row["status"]) == "completed")
    measured = sum(1 for row in rows if str(row["outcome_status"] or "unmeasured") != "unmeasured")
    stale_cutoff = (now - timedelta(hours=24)).astimezone(timezone.utc).isoformat(timespec="seconds")
    stale_rows = _db_rows(
        operations_db,
        """SELECT COUNT(*) AS count FROM operational_runs
           WHERE status='completed' AND outcome_status='unmeasured'
             AND completed_at<>'' AND completed_at<=?""",
        (stale_cutoff,),
    )
    stale = int(stale_rows[0]["count"] or 0) if stale_rows else 0
    return len(rows), completed, measured, stale


def _market_summary(market_db: Path) -> list[str]:
    runs = _db_rows(
        market_db,
        """SELECT run_id,completed_at FROM market_radar_runs
           WHERE status='completed' ORDER BY completed_at DESC LIMIT 1""",
    )
    if not runs:
        return ["市场雷达：暂无完成记录"]
    run_id = str(runs[0]["run_id"])
    pulses = _db_rows(
        market_db,
        """SELECT theme_title,score,independent_sources FROM market_pulses
           WHERE run_id=? ORDER BY score DESC LIMIT 3""",
        (run_id,),
    )
    if not pulses:
        return [f"市场雷达：{run_id} 已完成，无合格脉冲"]
    lines = [f"市场雷达：{run_id}，Top {len(pulses)}"]
    lines.extend(
        f"- {row['theme_title']}（{_safe_float(row['score']):.1f} 分，{_safe_counter(row['independent_sources'])} 个独立来源）"
        for row in pulses
    )
    return lines


def _failure_clusters(
    operations_db: Path, now: datetime, *, window_hours: int = 24, top: int = 3
) -> tuple[list[str], list[dict[str, Any]]]:
    """Group recent failed runs by ``product_line/quality_status``.

    A flat failure count hides whether one root cause (e.g. empty security
    output) dominates.  Clustering on the two dimensions we already record turns
    the readout into an actionable "fix this class first" signal.  Returns the
    rendered digest lines plus a structured payload for metadata.
    """
    start = (now - timedelta(hours=window_hours)).astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = _db_rows(
        operations_db,
        """SELECT product_line,quality_status,outcome_notes,request_text
           FROM operational_runs
           WHERE status='failed' AND COALESCE(completed_at,created_at)>=?""",
        (start,),
    )
    if not rows:
        return ["近 24 小时失败聚类：无失败运行"], []

    clusters: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        product = str(row["product_line"] or "unknown")
        quality = str(row["quality_status"] or "unmeasured")
        key = (product, quality)
        bucket = clusters.setdefault(key, {"count": 0, "sample": ""})
        bucket["count"] += 1
        if not bucket["sample"]:
            sample = " ".join(str(row["outcome_notes"] or row["request_text"] or "").split())
            bucket["sample"] = sample[:60]

    ranked = sorted(clusters.items(), key=lambda item: item[1]["count"], reverse=True)
    payload = [
        {"product_line": product, "quality_status": quality, "count": data["count"], "sample": data["sample"]}
        for (product, quality), data in ranked
    ]
    lines = [f"近 24 小时失败聚类：{len(rows)} 次失败，{len(clusters)} 个类别"]
    for entry in payload[:top]:
        suffix = f"（例：{entry['sample']}）" if entry["sample"] else ""
        lines.append(
            f"- {entry['product_line']} / {entry['quality_status']}：{entry['count']} 次{suffix}"
        )
    return lines, payload


def build_digest(config: dict[str, Any], *, now: datetime | None = None) -> tuple[str, dict[str, Any]]:
    zone = ZoneInfo(str(config.get("timezone") or DEFAULT_TIMEZONE))
    current = now.astimezone(zone) if now else datetime.now(zone)
    operations_db = Path(str(config.get("operations_db") or COMPANY_ROOT / "operations/runtime/operations_control.db"))
    router_db = Path(str(config.get("state_db") or COMPANY_ROOT / "operations/runtime/company_router.db"))
    market_db = Path(str(config.get("market_signals_db") or COMPANY_ROOT / "marketing/market_signals.db"))
    review_state, review_text = _review_summary(operations_db)
    pending, p0, stale_proposals = _proposal_summary(operations_db, current)
    total_runs, completed_runs, measured_runs, stale_outcomes = _run_summary(operations_db, current)
    failure_lines, failure_clusters = _failure_clusters(operations_db, current)
    origin = latest_origin(router_db)
    outbox = outbox_summary(operations_db)
    lines = [
        f"公司日报｜{current.date().isoformat()}",
        "",
        f"经营自动执行：{_operator_status(config)}（只读日报独立运行）",
        f"最近复盘：{review_state}",
        f"复盘结论：{review_text}",
        f"待审批提案：{pending}（P0：{p0}）",
        f"超过 72 小时未决策：{stale_proposals} 条（系统不会代替用户批准）",
        f"近 24 小时运行：{total_runs} 次，完成 {completed_runs} 次，已计量结果 {measured_runs} 次",
        f"超过 24 小时未补录结果：{stale_outcomes} 次",
        "",
        *failure_lines,
        "",
        *_market_summary(market_db),
        "",
        f"通知队列：待发 {outbox.get('pending', 0)}，死信 {outbox.get('dead_letter', 0)}",
    ]
    message = "\n".join(lines).strip()
    metadata = {
        "date": current.date().isoformat(),
        "generated_at": utc_now(),
        "origin_available": bool(origin.get("platform") and origin.get("chat_id")),
        "review_state": review_state,
        "pending_proposals": pending,
        "p0_proposals": p0,
        "stale_proposals": stale_proposals,
        "runs_24h": total_runs,
        "completed_runs_24h": completed_runs,
        "measured_runs_24h": measured_runs,
        "stale_outcomes": stale_outcomes,
        "failure_clusters": failure_clusters,
    }
    return message, {"origin": origin, **metadata}


def run(config_path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    config = load_config(config_path)
    message, info = build_digest(config)
    origin = info["origin"]
    configured_db = str(config.get("operations_db") or "").strip()
    if not configured_db:
        return {"queued": False, "reason": "operations_db not configured", **info}
    db_path = Path(configured_db)
    if not origin.get("platform") or not origin.get("chat_id"):
        return {"queued": False, "reason": "no management delivery target", "message": message, **info}
    notification_id = enqueue(
        db_path,
        dedup_key=f"daily-digest:{info['date']}",
        kind="daily_digest",
        source_id=info["date"],
        origin=origin,
        message=message,
        metadata=info,
    )
    return {"queued": True, "notification_id": notification_id, **info}


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue a read-only company management digest")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    result = run(Path(args.config))
    # The Cron job is local-only; the one-minute notifier owns external send.
    print(json.dumps({key: value for key, value in result.items() if key != "message"}, ensure_ascii=False))
    return 0 if result.get("queued") else 1


if __name__ == "__main__":
    raise SystemExit(main())
