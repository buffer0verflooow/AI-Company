#!/usr/bin/env python3
"""Poll company-routed Swarm runs and deliver terminal results to their origin."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

try:
    from .company_router import (
        DEFAULT_CONFIG,
        RouteDecision,
        RouterState,
        content_job_path,
        launch_content_job,
        launch_runner,
        load_config,
        resolve_session_origin,
        select_company_result,
        swarm_command,
        utc_now,
    )
except ImportError:  # Direct execution from automation/.
    from company_router import (
        DEFAULT_CONFIG,
        RouteDecision,
        RouterState,
        content_job_path,
        launch_content_job,
        launch_runner,
        load_config,
        resolve_session_origin,
        select_company_result,
        swarm_command,
        utc_now,
    )


DeliveryFn = Callable[[Dict[str, Any], Dict[str, str], str], Tuple[bool, str]]
MirrorFn = Callable[[Dict[str, str], str], bool]


def _operations_api():
    try:
        from .operations_control import format_review_message, pending_review_deliveries, update_review
    except ImportError:
        from operations_control import format_review_message, pending_review_deliveries, update_review
    return format_review_message, pending_review_deliveries, update_review


def _ensure_hermes_imports(config: Dict[str, Any]) -> None:
    repo = str(config.get("hermes_repo") or "").strip()
    if repo and repo not in sys.path:
        sys.path.insert(0, repo)


def deliver_message(config: Dict[str, Any], origin: Dict[str, str], message: str) -> Tuple[bool, str]:
    """Use Hermes' supported standalone sender for a confirmed delivery."""
    _ensure_hermes_imports(config)
    try:
        from gateway.config import Platform, load_gateway_config
        from tools.send_message_tool import _send_to_platform

        platform = Platform(origin["platform"].lower())
        gateway_config = load_gateway_config()
        pconfig = gateway_config.platforms.get(platform)
        if not pconfig or not pconfig.enabled:
            return False, f"platform {origin['platform']} is not enabled"
        result = asyncio.run(_send_to_platform(
            platform,
            pconfig,
            origin["chat_id"],
            message,
            thread_id=origin.get("thread_id") or None,
        ))
    except Exception as exc:
        return False, f"{exc.__class__.__name__}: {exc}"

    if isinstance(result, dict):
        if result.get("error"):
            return False, str(result["error"])
        if result.get("success") is True:
            return True, ""
    return False, f"unconfirmed delivery result: {result!r}"


def mirror_message(config: Dict[str, Any], origin: Dict[str, str], message: str) -> bool:
    """Append a delivered notification to the matching gateway transcript."""
    _ensure_hermes_imports(config)
    try:
        from gateway.mirror import mirror_to_session

        return bool(mirror_to_session(
            origin["platform"],
            origin["chat_id"],
            f"[公司 Research 完成通知]\n{message}",
            source_label="company-router",
            thread_id=origin.get("thread_id") or None,
            user_id=origin.get("user_id") or None,
            role="user",
        ))
    except Exception:
        return False


def mirror_tvcr_message(config: Dict[str, Any], origin: Dict[str, str], message: str) -> bool:
    """Mirror a delivered operating review into the management conversation."""
    _ensure_hermes_imports(config)
    try:
        from gateway.mirror import mirror_to_session

        return bool(mirror_to_session(
            origin["platform"],
            origin["chat_id"],
            f"[公司 TVCR 经营复盘]\n{message}",
            source_label="company-tvcr",
            thread_id=origin.get("thread_id") or None,
            user_id=origin.get("user_id") or None,
            role="user",
        ))
    except Exception:
        return False


def runner_is_alive(pid: Optional[int], run_id: str) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return "swarm_runner.py" in cmdline and run_id in cmdline


def content_runner_is_alive(pid: Optional[int], run_id: str) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return "content_hermes_executor.py" in cmdline and run_id in cmdline


def _age_minutes(created_at: str) -> float:
    try:
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 60.0)
    except (TypeError, ValueError):
        return 0.0


def _delivery_retry_ready(config: Dict[str, Any], row: Any) -> bool:
    attempts = int(row["delivery_attempts"] or 0)
    last = str(row["last_delivery_at"] or "")
    if attempts <= 0 or not last:
        return True
    try:
        last_at = datetime.fromisoformat(last)
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    base = max(1, int(config.get("delivery_retry_base_seconds", 60)))
    maximum = max(base, int(config.get("delivery_retry_max_seconds", 900)))
    delay = min(maximum, base * (2 ** min(attempts - 1, 8)))
    return (datetime.now(timezone.utc) - last_at).total_seconds() >= delay


def _origin_for_row(config: Dict[str, Any], row: Any) -> Dict[str, str]:
    stored = {
        "platform": str(row["delivery_platform"] or ""),
        "chat_id": str(row["delivery_chat_id"] or ""),
        "thread_id": str(row["delivery_thread_id"] or ""),
        "user_id": str(row["delivery_user_id"] or ""),
    }
    if stored["platform"] and stored["chat_id"]:
        return stored
    return resolve_session_origin(
        str(config.get("gateway_sessions_index") or ""),
        str(row["session_id"] or ""),
    )


def _format_terminal_message(config: Dict[str, Any], row: Any, result: Dict[str, Any]) -> str:
    run_id = str(row["run_id"])
    status = str(result.get("status") or row["status"] or "unknown")
    if status == "completed":
        limit = int(config.get("proactive_result_chars", 6000))
        content = select_company_result(result)[:limit].strip() or "任务已完成，但未返回可展示的结果正文。"
        return f"Research 安全探索任务已完成\nRun: {run_id}\n\n{content}"
    detail = str(result.get("error") or result.get("summary") or "请检查公司运行日志。")
    return f"Research 安全探索任务状态异常：{status}\nRun: {run_id}\n\n{detail}"


def _format_content_message(row: Any, payload: Dict[str, Any]) -> str:
    route = {
        "dispatch_article": "文章产线",
        "dispatch_video": "视频产线",
        "dispatch_company": "公司执行",
    }.get(row["action"], "公司任务")
    run_id = str(row["run_id"])
    status = str(payload.get("status") or row["status"] or "unknown")
    artifacts = payload.get("artifacts") or []
    artifact_text = "\n".join(f"- {item}" for item in artifacts)
    if status == "completed":
        result = str(payload.get("result") or "任务已完成。")[-1200:]
        suffix = f"\n\n产物：\n{artifact_text}" if artifact_text else ""
        return f"{route}任务已完成\nRun: {run_id}\n\n{result}{suffix}"
    if status == "needs_approval":
        result = str(payload.get("result") or "内部准备已完成，需要人工审批。")[-1200:]
        suffix = f"\n\n产物：\n{artifact_text}" if artifact_text else ""
        return f"{route}任务等待审批\nRun: {run_id}\n\n{result}{suffix}"
    error = str(payload.get("error") or "请检查任务日志。")
    suffix = f"\n\n已生成产物：\n{artifact_text}" if artifact_text else ""
    return f"{route}任务状态异常：{status}\nRun: {run_id}\n\n{error}{suffix}"


def _fit_delivery_message(config: Dict[str, Any], origin: Dict[str, str], message: str) -> str:
    limits = config.get("proactive_delivery_chars_by_platform") or {}
    limit = int(limits.get(origin.get("platform", ""), config.get("proactive_delivery_default_chars", 3000)))
    if limit <= 0 or len(message) <= limit:
        return message
    suffix = "\n\n[通知已截断；完整结果与产物保存在公司运行目录。]"
    return message[:max(0, limit - len(suffix))].rstrip() + suffix


def process_once(
    config: Dict[str, Any],
    *,
    deliverer: DeliveryFn = deliver_message,
    mirror: Optional[MirrorFn] = None,
) -> Dict[str, int]:
    """Reconcile run state, recover dead runners, and deliver terminal results."""
    summary = {"checked": 0, "running": 0, "restarted": 0, "delivered": 0, "failed": 0, "waiting_target": 0}
    if not config.get("proactive_delivery", True):
        return summary

    state = RouterState(config["state_db"])
    try:
        rows = state.pending_notifications(int(config.get("max_delivery_attempts", 10)))
        for row in rows:
            summary["checked"] += 1
            event_id = str(row["route_event_id"])
            run_id = str(row["run_id"])
            try:
                result = swarm_command(config, "task", "result", "--run-id", run_id, timeout=20)
            except Exception as exc:
                state.update(event_id, error=f"run status query failed: {exc}")
                summary["failed"] += 1
                continue

            status = str(result.get("status") or "unknown")
            state.update(event_id, status=status)
            if status in {"submitted", "running"}:
                summary["running"] += 1
                stale = _age_minutes(str(row["created_at"] or "")) >= float(config.get("stale_run_minutes", 15))
                restarts = int(row["runner_restarts"] or 0)
                if (
                    stale
                    and not runner_is_alive(row["runner_pid"], run_id)
                    and restarts < int(config.get("max_runner_restarts", 2))
                ):
                    decision = RouteDecision(**json.loads(row["decision_json"]))
                    try:
                        pid = launch_runner(config, run_id, decision.intent)
                        state.update(event_id, runner_pid=pid, runner_restarts=restarts + 1, error="")
                        summary["restarted"] += 1
                    except Exception as exc:
                        state.update(event_id, error=f"runner recovery failed: {exc}")
                        summary["failed"] += 1
                continue

            if status not in {"completed", "needs_approval", "failed", "cancelled"}:
                continue
            if not _delivery_retry_ready(config, row):
                continue

            origin = _origin_for_row(config, row)
            attempts = int(row["delivery_attempts"] or 0) + 1
            if not origin:
                state.update(
                    event_id,
                    delivery_attempts=attempts,
                    delivery_error="could not resolve original conversation",
                    last_delivery_at=utc_now(),
                )
                summary["waiting_target"] += 1
                continue

            allowed = {str(item).lower() for item in config.get("proactive_delivery_platforms", [])}
            if allowed and origin["platform"].lower() not in allowed:
                state.update(
                    event_id,
                    delivery_attempts=attempts,
                    delivery_error=f"delivery platform not allowlisted: {origin['platform']}",
                    last_delivery_at=utc_now(),
                )
                summary["failed"] += 1
                continue

            state.update(
                event_id,
                delivery_platform=origin["platform"],
                delivery_chat_id=origin["chat_id"],
                delivery_thread_id=origin.get("thread_id", ""),
                delivery_user_id=origin.get("user_id", ""),
            )
            message = _format_terminal_message(config, row, result)
            message = _fit_delivery_message(config, origin, message)
            ok, error = deliverer(config, origin, message)
            if ok:
                state.update(
                    event_id,
                    proactive_delivered=1,
                    result_delivered=1,
                    delivery_attempts=attempts,
                    delivery_error="",
                    last_delivery_at=utc_now(),
                )
                if mirror is None:
                    mirror_message(config, origin, message)
                else:
                    mirror(origin, message)
                summary["delivered"] += 1
            else:
                state.update(
                    event_id,
                    delivery_attempts=attempts,
                    delivery_error=error,
                    last_delivery_at=utc_now(),
                )
                summary["failed"] += 1
        content_rows = state.pending_content_notifications(int(config.get("max_delivery_attempts", 10)))
        for row in content_rows:
            summary["checked"] += 1
            event_id = str(row["route_event_id"])
            run_id = str(row["run_id"])
            status_path = content_job_path(config, run_id) / "status.json"
            payload: Dict[str, Any] = {}
            if status_path.exists():
                try:
                    payload = json.loads(status_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    state.update(event_id, error=f"content status query failed: {exc}")
                    summary["failed"] += 1
                    continue
            status = str(payload.get("status") or row["status"] or "running")
            state.update(event_id, status=status)
            if status in {"submitted", "running"}:
                summary["running"] += 1
                stale = _age_minutes(str(row["created_at"] or "")) >= float(config.get("stale_run_minutes", 15))
                restarts = int(row["runner_restarts"] or 0)
                if (
                    stale
                    and not content_runner_is_alive(row["runner_pid"], run_id)
                    and restarts < int(config.get("max_runner_restarts", 2))
                ):
                    try:
                        pid = launch_content_job(config, run_id)
                        state.update(event_id, runner_pid=pid, runner_restarts=restarts + 1, error="")
                        summary["restarted"] += 1
                    except Exception as exc:
                        state.update(event_id, error=f"content runner recovery failed: {exc}")
                        summary["failed"] += 1
                continue
            if status not in {"completed", "needs_approval", "failed", "cancelled"}:
                continue
            if not _delivery_retry_ready(config, row):
                continue

            origin = _origin_for_row(config, row)
            attempts = int(row["delivery_attempts"] or 0) + 1
            if not origin:
                state.update(
                    event_id,
                    delivery_attempts=attempts,
                    delivery_error="could not resolve original conversation",
                    last_delivery_at=utc_now(),
                )
                summary["waiting_target"] += 1
                continue
            allowed = {str(item).lower() for item in config.get("proactive_delivery_platforms", [])}
            if allowed and origin["platform"].lower() not in allowed:
                state.update(
                    event_id,
                    delivery_attempts=attempts,
                    delivery_error=f"delivery platform not allowlisted: {origin['platform']}",
                    last_delivery_at=utc_now(),
                )
                summary["failed"] += 1
                continue
            state.update(
                event_id,
                delivery_platform=origin["platform"],
                delivery_chat_id=origin["chat_id"],
                delivery_thread_id=origin.get("thread_id", ""),
                delivery_user_id=origin.get("user_id", ""),
            )
            message = _format_content_message(row, payload)
            message = _fit_delivery_message(config, origin, message)
            ok, error = deliverer(config, origin, message)
            if ok:
                state.update(
                    event_id,
                    proactive_delivered=1,
                    result_delivered=1,
                    delivery_attempts=attempts,
                    delivery_error="",
                    last_delivery_at=utc_now(),
                )
                if mirror is None:
                    mirror_message(config, origin, message)
                else:
                    mirror(origin, message)
                summary["delivered"] += 1
            else:
                state.update(
                    event_id,
                    delivery_attempts=attempts,
                    delivery_error=error,
                    last_delivery_at=utc_now(),
                )
                summary["failed"] += 1
    finally:
        state.close()

    operations_db = str(config.get("operations_db") or "").strip()
    if operations_db:
        format_review_message, pending_review_deliveries, update_review = _operations_api()
        review_rows = pending_review_deliveries(
            Path(operations_db), int(config.get("max_delivery_attempts", 10))
        )
        for row in review_rows:
            summary["checked"] += 1
            if not _delivery_retry_ready(config, row):
                continue
            review_id = str(row["review_id"])
            attempts = int(row["delivery_attempts"] or 0) + 1
            origin = {
                "platform": str(row["delivery_platform"] or ""),
                "chat_id": str(row["delivery_chat_id"] or ""),
                "thread_id": str(row["delivery_thread_id"] or ""),
                "user_id": str(row["delivery_user_id"] or ""),
            }
            if not origin["platform"] or not origin["chat_id"]:
                update_review(
                    Path(operations_db), review_id,
                    delivery_attempts=attempts,
                    delivery_error="TVCR review has no delivery target",
                    last_delivery_at=utc_now(),
                )
                summary["waiting_target"] += 1
                continue
            allowed = {str(item).lower() for item in config.get("proactive_delivery_platforms", [])}
            if allowed and origin["platform"].lower() not in allowed:
                update_review(
                    Path(operations_db), review_id,
                    delivery_attempts=attempts,
                    delivery_error=f"delivery platform not allowlisted: {origin['platform']}",
                    last_delivery_at=utc_now(),
                )
                summary["failed"] += 1
                continue
            message = format_review_message(
                Path(operations_db), review_id,
                limit=int((config.get("tvcr_delivery_chars_by_platform") or {}).get(
                    origin["platform"], config.get("tvcr_delivery_default_chars", 1000)
                )),
            )
            ok, error = deliverer(config, origin, message)
            if ok:
                update_review(
                    Path(operations_db), review_id,
                    delivered=1,
                    delivery_attempts=attempts,
                    delivery_error="",
                    last_delivery_at=utc_now(),
                )
                if mirror is None:
                    mirror_tvcr_message(config, origin, message)
                else:
                    mirror(origin, message)
                summary["delivered"] += 1
            else:
                update_review(
                    Path(operations_db), review_id,
                    delivery_attempts=attempts,
                    delivery_error=error,
                    last_delivery_at=utc_now(),
                )
                summary["failed"] += 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Deliver completed company Swarm results")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = process_once(load_config(Path(args.config)))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
