#!/usr/bin/env python3
"""Poll company-routed Swarm runs and deliver terminal results to their origin."""

from __future__ import annotations

import argparse
import asyncio
import json
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
    from ._safe_io import locked_append_text
    from .notification_outbox import (
        append_dead_letter as append_outbox_dead_letter,
        enqueue as enqueue_outbox,
        force_dead_letter as force_outbox_dead_letter,
        get_by_dedup_key as get_outbox_by_dedup_key,
        mark_delivered as mark_outbox_delivered,
        pending as pending_outbox,
        record_failure as record_outbox_failure,
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
    from _safe_io import locked_append_text
    from notification_outbox import (
        append_dead_letter as append_outbox_dead_letter,
        enqueue as enqueue_outbox,
        force_dead_letter as force_outbox_dead_letter,
        get_by_dedup_key as get_outbox_by_dedup_key,
        mark_delivered as mark_outbox_delivered,
        pending as pending_outbox,
        record_failure as record_outbox_failure,
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


def _delivery_fallback_path(config: Dict[str, Any]) -> Path:
    configured = str(config.get("delivery_fallback_path") or "").strip()
    return Path(configured) if configured else Path(config["state_db"]).parent / "delivery-dead-letters.jsonl"


def record_terminal_delivery(
    config: Dict[str, Any],
    *,
    kind: str,
    identifier: str,
    origin: Dict[str, str],
    message: str,
    reason: str,
) -> str:
    """Persist an undeliverable terminal notification for management recovery."""
    path = _delivery_fallback_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "recorded_at": utc_now(),
        "kind": kind,
        "identifier": identifier,
        "origin": origin,
        "reason": reason,
        "message": message,
    }
    locked_append_text(path, json.dumps(record, ensure_ascii=False) + "\n")
    return str(path)


def list_terminal_deliveries(config: Dict[str, Any], limit: int = 50) -> list[Dict[str, Any]]:
    """Return the newest locally surfaced terminal notifications."""
    path = _delivery_fallback_path(config)
    if not path.is_file():
        return []
    records: list[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records[-max(0, limit):][::-1]


def _terminal_reason(config: Dict[str, Any], origin: Dict[str, str]) -> str:
    allowed = {str(item).lower() for item in config.get("proactive_delivery_platforms", [])}
    if allowed and origin.get("platform", "").lower() not in allowed:
        return f"delivery platform not allowlisted: {origin.get('platform', '')}"
    return ""


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


def mirror_management_message(config: Dict[str, Any], origin: Dict[str, str], message: str) -> bool:
    """Mirror a recovered management notification into the chat transcript."""
    _ensure_hermes_imports(config)
    try:
        from gateway.mirror import mirror_to_session

        return bool(mirror_to_session(
            origin["platform"],
            origin["chat_id"],
            f"[公司主动通知]\n{message}",
            source_label="company-notification-outbox",
            thread_id=origin.get("thread_id") or None,
            user_id=origin.get("user_id") or None,
            role="user",
        ))
    except Exception:
        return False


def _operations_db_path(config: Dict[str, Any]) -> Optional[Path]:
    value = str(config.get("operations_db") or "").strip()
    return Path(value) if value else None


def _management_origin(config: Dict[str, Any]) -> Dict[str, str]:
    """Resolve the latest known management conversation without guessing."""
    try:
        from .operations_control import latest_origin
    except ImportError:
        from operations_control import latest_origin
    router_db = Path(str(config.get("router_db") or config.get("state_db") or ""))
    try:
        return latest_origin(router_db)
    except (OSError, ValueError):
        return {}


def _cron_output_root(config: Dict[str, Any]) -> Path:
    configured = str(config.get("cron_output_root") or "").strip()
    if configured:
        return Path(configured)
    return Path("/home/pwn/.hermes/cron/output")


def _read_cron_jobs(config: Dict[str, Any]) -> list[Dict[str, Any]]:
    configured = str(config.get("cron_jobs_path") or "").strip()
    path = Path(configured) if configured else Path("/home/pwn/.hermes/cron/jobs.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("jobs")
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _cron_run_datetime(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _find_cron_output(config: Dict[str, Any], job_id: str, last_run_at: str) -> Optional[Path]:
    root = _cron_output_root(config) / job_id
    if not root.is_dir():
        return None
    run_at = _cron_run_datetime(last_run_at)
    candidates = sorted(root.glob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    if run_at is None:
        return candidates[0]
    target = run_at.timestamp()
    for candidate in candidates:
        try:
            if abs(candidate.stat().st_mtime - target) <= 15 * 60:
                return candidate
        except OSError:
            continue
    return None


def _extract_cron_response(path: Path) -> str:
    """Extract the report portion from Hermes' saved cron transcript."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # Agent-driven jobs save the prompt and final answer under this marker.
    marker = "\n## Response\n"
    if marker in text:
        text = text.rsplit(marker, 1)[1]
        if text.startswith("---"):
            text = text[3:]
    else:
        # no-agent scripts generally contain a short header followed by JSON.
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            text = parts[1]
    return text.strip()


def recover_failed_cron_deliveries(config: Dict[str, Any]) -> int:
    """Queue failed origin deliveries from selected Cron jobs.

    Hermes records a Cron agent as successful even when the final message
    cannot be sent.  The saved output is still durable, so import it once into
    the company outbox and let the normal notifier own retries/dead letters.
    """
    db_path = _operations_db_path(config)
    if db_path is None:
        return 0
    allow = config.get("cron_delivery_recovery_jobs") or ["company-daily-auto-fix"]
    allowed = {str(item) for item in allow}
    origin = _management_origin(config)
    if not origin.get("platform") or not origin.get("chat_id"):
        return 0
    queued = 0
    for job in _read_cron_jobs(config):
        job_id = str(job.get("id") or "")
        name = str(job.get("name") or job_id)
        if (job_id not in allowed and name not in allowed) or not job.get("last_delivery_error"):
            continue
        last_run = str(job.get("last_run_at") or "")
        if not last_run:
            continue
        output = _find_cron_output(config, job_id, last_run)
        if output is None:
            continue
        response = _extract_cron_response(output)
        if not response:
            continue
        message = f"【补发】{name}\n运行时间：{last_run}\n\n{response}"
        message = _fit_delivery_message(config, origin, message)
        dedup_key = f"cron-delivery:{job_id}:{last_run}"
        if get_outbox_by_dedup_key(db_path, dedup_key) is not None:
            continue
        enqueue_outbox(
            db_path,
            dedup_key=dedup_key,
            kind="cron_recovery",
            source_id=f"{job_id}:{last_run}",
            origin=origin,
            message=message,
            metadata={
                "job_id": job_id,
                "job_name": name,
                "output_path": str(output),
                "delivery_error": str(job.get("last_delivery_error") or ""),
            },
        )
        queued += 1
    return queued


def process_outbox(
    config: Dict[str, Any],
    *,
    deliverer: DeliveryFn,
    mirror: Optional[MirrorFn] = None,
) -> Dict[str, int]:
    """Deliver ready outbox rows with bounded retry and dead-lettering."""
    result = {"checked": 0, "delivered": 0, "failed": 0, "dead_letter": 0}
    db_path = _operations_db_path(config)
    if db_path is None:
        return result
    fallback = _delivery_fallback_path(config)
    rows = pending_outbox(
        db_path,
        limit=int(config.get("outbox_batch_size", 20)),
    )
    for row in rows:
        result["checked"] += 1
        notification_id = str(row["notification_id"])
        origin = {
            "platform": str(row.get("platform") or ""),
            "chat_id": str(row.get("chat_id") or ""),
            "thread_id": str(row.get("thread_id") or ""),
            "user_id": str(row.get("user_id") or ""),
        }
        message = _fit_delivery_message(config, origin, str(row.get("message") or ""))
        terminal_reason = ""
        if not origin["platform"] or not origin["chat_id"]:
            terminal_reason = "outbox notification has no delivery target"
        else:
            terminal_reason = _terminal_reason(config, origin)
        if terminal_reason:
            dead = force_outbox_dead_letter(db_path, notification_id, terminal_reason) or row
            append_outbox_dead_letter(fallback, dead, reason=terminal_reason)
            result["dead_letter"] += 1
            continue
        try:
            ok, error = deliverer(config, origin, message)
        except Exception as exc:  # sender plugins must not stop the batch
            ok, error = False, f"{exc.__class__.__name__}: {exc}"
        if ok:
            mark_outbox_delivered(db_path, notification_id)
            if mirror is None:
                mirror_management_message(config, origin, message)
            else:
                mirror(origin, message)
            result["delivered"] += 1
            continue
        updated = record_outbox_failure(
            db_path,
            notification_id,
            error,
            max_attempts=int(config.get("outbox_max_attempts", 12)),
            retry_base_seconds=int(config.get("delivery_retry_base_seconds", 60)),
            retry_max_seconds=int(config.get("delivery_retry_max_seconds", 3600)),
        )
        if updated.get("state") == "dead_letter":
            append_outbox_dead_letter(fallback, updated, reason=str(error or "delivery failed"))
            result["dead_letter"] += 1
        else:
            result["failed"] += 1
    return result


def _record_retry_exhaustion(
    config: Dict[str, Any],
    *,
    kind: str,
    identifier: str,
    origin: Dict[str, str],
    message: str,
    error: str,
) -> str:
    """Move a repeatedly failing legacy row to the same recoverable dead letter."""
    fallback = record_terminal_delivery(
        config,
        kind=kind,
        identifier=identifier,
        origin=origin,
        message=message,
        reason=f"retry exhausted: {error}",
    )
    return f"terminal: retry exhausted: {error}; fallback={fallback}"


def process_once(
    config: Dict[str, Any],
    *,
    deliverer: DeliveryFn = deliver_message,
    mirror: Optional[MirrorFn] = None,
) -> Dict[str, int]:
    """Reconcile run state, recover dead runners, and deliver terminal results."""
    summary = {
        "checked": 0, "running": 0, "restarted": 0, "delivered": 0,
        "failed": 0, "waiting_target": 0, "terminal": 0,
        "outbox_enqueued": 0, "outbox_checked": 0,
        "outbox_delivered": 0, "outbox_failed": 0, "outbox_dead_letter": 0,
    }
    if not config.get("proactive_delivery", True):
        return summary

    # Import failed Hermes-origin deliveries before polling normal worker
    # results.  The outbox sender is deliberately single-owner (this cron job)
    # so a live-adapter failure cannot race a second recovery sender.
    if _operations_db_path(config) is not None:
        summary["outbox_enqueued"] = recover_failed_cron_deliveries(config)
        outbox_result = process_outbox(config, deliverer=deliverer, mirror=mirror)
        summary["outbox_checked"] = outbox_result["checked"]
        summary["outbox_delivered"] = outbox_result["delivered"]
        summary["outbox_failed"] = outbox_result["failed"]
        summary["outbox_dead_letter"] = outbox_result["dead_letter"]

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
                missing_target = "could not resolve original conversation"
                if attempts >= int(config.get("max_delivery_attempts", 10)):
                    terminal_error = _record_retry_exhaustion(
                        config,
                        kind="swarm",
                        identifier=run_id,
                        origin=origin,
                        message=f"Research Run {run_id} 已完成，但无法解析原始会话。",
                        error=missing_target,
                    )
                    state.update(
                        event_id,
                        delivery_attempts=attempts,
                        delivery_error=terminal_error,
                        last_delivery_at=utc_now(),
                    )
                    summary["terminal"] += 1
                else:
                    state.update(
                        event_id,
                        delivery_attempts=attempts,
                        delivery_error=missing_target,
                        last_delivery_at=utc_now(),
                    )
                    summary["waiting_target"] += 1
                continue

            message = _format_terminal_message(config, row, result)
            message = _fit_delivery_message(config, origin, message)
            terminal_reason = _terminal_reason(config, origin)
            if terminal_reason:
                fallback = record_terminal_delivery(
                    config, kind="swarm", identifier=run_id, origin=origin,
                    message=message, reason=terminal_reason,
                )
                state.update(
                    event_id,
                    delivery_attempts=int(config.get("max_delivery_attempts", 10)),
                    delivery_error=f"terminal: {terminal_reason}; fallback={fallback}",
                    last_delivery_at=utc_now(),
                )
                summary["terminal"] += 1
                continue

            state.update(
                event_id,
                delivery_platform=origin["platform"],
                delivery_chat_id=origin["chat_id"],
                delivery_thread_id=origin.get("thread_id", ""),
                delivery_user_id=origin.get("user_id", ""),
            )
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
                if attempts >= int(config.get("max_delivery_attempts", 10)):
                    terminal_error = _record_retry_exhaustion(
                        config,
                        kind="swarm",
                        identifier=run_id,
                        origin=origin,
                        message=message,
                        error=error,
                    )
                    state.update(
                        event_id,
                        delivery_attempts=attempts,
                        delivery_error=terminal_error,
                        last_delivery_at=utc_now(),
                    )
                    summary["terminal"] += 1
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
                missing_target = "could not resolve original conversation"
                if attempts >= int(config.get("max_delivery_attempts", 10)):
                    terminal_error = _record_retry_exhaustion(
                        config,
                        kind="content",
                        identifier=run_id,
                        origin=origin,
                        message=f"公司 Run {run_id} 已完成，但无法解析原始会话。",
                        error=missing_target,
                    )
                    state.update(
                        event_id,
                        delivery_attempts=attempts,
                        delivery_error=terminal_error,
                        last_delivery_at=utc_now(),
                    )
                    summary["terminal"] += 1
                else:
                    state.update(
                        event_id,
                        delivery_attempts=attempts,
                        delivery_error=missing_target,
                        last_delivery_at=utc_now(),
                    )
                    summary["waiting_target"] += 1
                continue
            message = _format_content_message(row, payload)
            message = _fit_delivery_message(config, origin, message)
            terminal_reason = _terminal_reason(config, origin)
            if terminal_reason:
                fallback = record_terminal_delivery(
                    config, kind="content", identifier=run_id, origin=origin,
                    message=message, reason=terminal_reason,
                )
                state.update(
                    event_id,
                    delivery_attempts=int(config.get("max_delivery_attempts", 10)),
                    delivery_error=f"terminal: {terminal_reason}; fallback={fallback}",
                    last_delivery_at=utc_now(),
                )
                summary["terminal"] += 1
                continue
            state.update(
                event_id,
                delivery_platform=origin["platform"],
                delivery_chat_id=origin["chat_id"],
                delivery_thread_id=origin.get("thread_id", ""),
                delivery_user_id=origin.get("user_id", ""),
            )
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
                if attempts >= int(config.get("max_delivery_attempts", 10)):
                    terminal_error = _record_retry_exhaustion(
                        config,
                        kind="content",
                        identifier=run_id,
                        origin=origin,
                        message=message,
                        error=error,
                    )
                    state.update(
                        event_id,
                        delivery_attempts=attempts,
                        delivery_error=terminal_error,
                        last_delivery_at=utc_now(),
                    )
                    summary["terminal"] += 1
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
                missing_target = "TVCR review has no delivery target"
                if attempts >= int(config.get("max_delivery_attempts", 10)):
                    missing_message = format_review_message(
                        Path(operations_db), review_id,
                        limit=int(config.get("tvcr_delivery_default_chars", 1000)),
                    )
                    terminal_error = _record_retry_exhaustion(
                        config,
                        kind="tvcr",
                        identifier=review_id,
                        origin=origin,
                        message=missing_message,
                        error=missing_target,
                    )
                    update_review(
                        Path(operations_db), review_id,
                        delivery_attempts=attempts,
                        delivery_error=terminal_error,
                        last_delivery_at=utc_now(),
                    )
                    summary["terminal"] += 1
                else:
                    update_review(
                        Path(operations_db), review_id,
                        delivery_attempts=attempts,
                        delivery_error=missing_target,
                        last_delivery_at=utc_now(),
                    )
                    summary["waiting_target"] += 1
                continue
            message = format_review_message(
                Path(operations_db), review_id,
                limit=int((config.get("tvcr_delivery_chars_by_platform") or {}).get(
                    origin["platform"], config.get("tvcr_delivery_default_chars", 1000)
                )),
            )
            terminal_reason = _terminal_reason(config, origin)
            if terminal_reason:
                fallback = record_terminal_delivery(
                    config, kind="tvcr", identifier=review_id, origin=origin,
                    message=message, reason=terminal_reason,
                )
                update_review(
                    Path(operations_db), review_id,
                    delivery_attempts=int(config.get("max_delivery_attempts", 10)),
                    delivery_error=f"terminal: {terminal_reason}; fallback={fallback}",
                    last_delivery_at=utc_now(),
                )
                summary["terminal"] += 1
                continue
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
                if attempts >= int(config.get("max_delivery_attempts", 10)):
                    terminal_error = _record_retry_exhaustion(
                        config,
                        kind="tvcr",
                        identifier=review_id,
                        origin=origin,
                        message=message,
                        error=error,
                    )
                    update_review(
                        Path(operations_db), review_id,
                        delivery_attempts=attempts,
                        delivery_error=terminal_error,
                        last_delivery_at=utc_now(),
                    )
                    summary["terminal"] += 1
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
    parser.add_argument("--list-terminal", action="store_true")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    config = load_config(Path(args.config))
    if args.list_terminal:
        print(json.dumps(list_terminal_deliveries(config, args.limit), ensure_ascii=False, indent=2))
        return 0
    summary = process_once(config)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
