#!/usr/bin/env python3
"""Poll company-routed Swarm runs and deliver terminal results to their origin."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import re
import sqlite3
import sys
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

try:
    from ._safe_io import (
        locked_append_text,
        read_text_limited,
        read_text_limited_nofollow,
        sqlite_uri,
    )
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
    from .notification_outbox import (
        append_dead_letter as append_outbox_dead_letter,
    )
    from .notification_outbox import (
        enqueue as enqueue_outbox,
    )
    from .notification_outbox import (
        force_dead_letter as force_outbox_dead_letter,
    )
    from .notification_outbox import (
        get_by_dedup_key as get_outbox_by_dedup_key,
    )
    from .notification_outbox import (
        mark_delivered as mark_outbox_delivered,
    )
    from .notification_outbox import (
        pending as pending_outbox,
    )
    from .notification_outbox import (
        record_failure as record_outbox_failure,
    )
except ImportError:  # Direct execution from automation/.
    from _safe_io import (
        locked_append_text,
        read_text_limited,
        read_text_limited_nofollow,
        sqlite_uri,
    )
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
    from notification_outbox import (
        append_dead_letter as append_outbox_dead_letter,
    )
    from notification_outbox import (
        enqueue as enqueue_outbox,
    )
    from notification_outbox import (
        force_dead_letter as force_outbox_dead_letter,
    )
    from notification_outbox import (
        get_by_dedup_key as get_outbox_by_dedup_key,
    )
    from notification_outbox import (
        mark_delivered as mark_outbox_delivered,
    )
    from notification_outbox import (
        pending as pending_outbox,
    )
    from notification_outbox import (
        record_failure as record_outbox_failure,
    )


DeliveryFn = Callable[[dict[str, Any], dict[str, str], str], tuple[bool, str]]
MirrorFn = Callable[[dict[str, str], str], bool]
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
MAX_CRON_OUTPUT_BYTES = 5 * 1024 * 1024


def _operations_api():
    try:
        from .operations_control import (
            format_review_message,
            pending_review_deliveries,
            update_review,
        )
    except ImportError:
        from operations_control import (
            format_review_message,
            pending_review_deliveries,
            update_review,
        )
    return format_review_message, pending_review_deliveries, update_review


def _ensure_hermes_imports(config: dict[str, Any]) -> None:
    repo = str(config.get("hermes_repo") or "").strip()
    if repo and repo not in sys.path:
        sys.path.insert(0, repo)


def _int_config(config: dict[str, Any], key: str, default: int) -> int:
    """Coerce a config value to int, falling back on malformed values.

    The notifier reads the same hand-edited router config as the hook; a
    single non-numeric value must not crash the one-minute delivery tick.
    """
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default


def _float_config(config: dict[str, Any], key: str, default: float) -> float:
    """Coerce a config value to float, falling back on malformed values."""
    try:
        value = float(config.get(key, default))
    except (TypeError, ValueError, OverflowError):
        return default
    return value if math.isfinite(value) else default


def _safe_counter(value: Any) -> int:
    """Coerce a DB/JSON counter to int, degrading to 0 on malformed values.

    State rows are written by the router and sibling subsystems; a corrupt
    attempt/restart counter must not crash the one-minute delivery tick.
    """
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _positive_limit(value: Any, default: int) -> int:
    """Coerce a delivery character limit to a positive int with fallback.

    The per-platform limit maps in the hand-edited config are nested JSON; a
    single non-numeric or non-positive value must not crash the delivery tick.
    """
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if number > 0 else default


def deliver_message(config: dict[str, Any], origin: dict[str, str], message: str) -> tuple[bool, str]:
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
    except Exception as exc:  # noqa: BLE001 -- a broken sender plugin must not stop the tick
        return False, f"{exc.__class__.__name__}: {exc}"

    if isinstance(result, dict):
        if result.get("error"):
            return False, str(result["error"])
        if result.get("success") is True:
            return True, ""
    return False, f"unconfirmed delivery result: {result!r}"


def mirror_message(config: dict[str, Any], origin: dict[str, str], message: str) -> bool:
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
    except Exception as exc:
        # Mirroring is best-effort, but a silent swallow hides real bugs (e.g. a
        # missing origin key or a failure inside mirror_to_session) that are
        # otherwise impossible to diagnose.
        LOGGER.warning("mirror_message failed: %s", exc, exc_info=True)
        return False


def mirror_tvcr_message(config: dict[str, Any], origin: dict[str, str], message: str) -> bool:
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
    except Exception as exc:
        LOGGER.warning("mirror_tvcr_message failed: %s", exc, exc_info=True)
        return False


def runner_is_alive(pid: int | None, run_id: str) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return "swarm_runner.py" in cmdline and run_id in cmdline


def content_runner_is_alive(pid: int | None, run_id: str) -> bool:
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


def _beat_heartbeat(state: RouterState, event_id: str, alive: bool) -> None:
    """Refresh the run heartbeat on behalf of a live runner (1-minute cron)."""
    if alive:
        state.update(event_id, last_heartbeat=utc_now())


def _mark_suspected_dead_if_stale(
    state: RouterState,
    config: dict[str, Any],
    row: Any,
    event_id: str,
    *,
    alive: bool,
    restarts: int,
    max_restarts: int,
) -> bool:
    """Flag a run whose heartbeat has gone silent past the restart budget.

    Detection only: the suspected_dead status makes the failing run visible to the
    daily readout and stops us restarting it forever, which is the degradation the
    heartbeat is meant to trigger.
    """
    if alive or restarts < max_restarts:
        return False
    reference = str(row["last_heartbeat"] or row["created_at"] or "")
    timeout = _float_config(config, "heartbeat_timeout_minutes", 15)
    if _age_minutes(reference) < timeout:
        return False
    state.update(
        event_id,
        status="suspected_dead",
        error="no heartbeat; runner presumed dead after restart budget exhausted",
    )
    return True


def _delivery_retry_ready(config: dict[str, Any], row: Any) -> bool:
    attempts = _safe_counter(row["delivery_attempts"])
    last = str(row["last_delivery_at"] or "")
    if attempts <= 0 or not last:
        return True
    try:
        last_at = datetime.fromisoformat(last)
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    base = max(1, _int_config(config, "delivery_retry_base_seconds", 60))
    maximum = max(base, _int_config(config, "delivery_retry_max_seconds", 900))
    delay = min(maximum, base * (2 ** min(attempts - 1, 8)))
    return (datetime.now(timezone.utc) - last_at).total_seconds() >= delay


def _delivery_fallback_path(config: dict[str, Any]) -> Path:
    configured = str(config.get("delivery_fallback_path") or "").strip()
    return Path(configured) if configured else Path(config["state_db"]).parent / "delivery-dead-letters.jsonl"


def record_terminal_delivery(
    config: dict[str, Any],
    *,
    kind: str,
    identifier: str,
    origin: dict[str, str],
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


def list_terminal_deliveries(config: dict[str, Any], limit: int = 50) -> list[dict[str, Any]]:
    """Return the newest locally surfaced terminal notifications."""
    path = _delivery_fallback_path(config)
    if not path.is_file():
        return []
    if limit <= 0:
        return []
    records: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        return []
    return list(reversed(records))


def _terminal_reason(config: dict[str, Any], origin: dict[str, str]) -> str:
    allowed = {str(item).lower() for item in config.get("proactive_delivery_platforms", [])}
    if allowed and origin.get("platform", "").lower() not in allowed:
        return f"delivery platform not allowlisted: {origin.get('platform', '')}"
    return ""


def _origin_for_row(config: dict[str, Any], row: Any) -> dict[str, str]:
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


def _format_terminal_message(config: dict[str, Any], row: Any, result: dict[str, Any]) -> str:
    run_id = str(row["run_id"])
    status = str(result.get("status") or row["status"] or "unknown")
    if status == "completed":
        limit = _int_config(config, "proactive_result_chars", 6000)
        content = select_company_result(result)[:limit].strip() or "任务已完成，但未返回可展示的结果正文。"
        return f"Research 安全探索任务已完成\nRun: {run_id}\n\n{content}"
    detail = str(result.get("error") or result.get("summary") or "请检查公司运行日志。")
    return f"Research 安全探索任务状态异常：{status}\nRun: {run_id}\n\n{detail}"


def _format_content_message(row: Any, payload: dict[str, Any]) -> str:
    route = {
        "dispatch_article": "文章产线",
        "dispatch_video": "视频产线",
        "dispatch_company": "公司执行",
    }.get(row["action"], "公司任务")
    run_id = str(row["run_id"])
    status = str(payload.get("status") or row["status"] or "unknown")
    # A worker-written status.json may carry a non-list "artifacts" (corrupt or
    # legacy payload); coerce to [] so one bad row cannot kill the whole tick.
    raw_artifacts = payload.get("artifacts")
    artifacts = raw_artifacts if isinstance(raw_artifacts, list) else []
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


def _fit_delivery_message(config: dict[str, Any], origin: dict[str, str], message: str) -> str:
    limits = config.get("proactive_delivery_chars_by_platform") or {}
    default_limit = _positive_limit(config.get("proactive_delivery_default_chars", 3000), 3000)
    limit = _positive_limit(limits.get(origin.get("platform", "")), default_limit)
    if limit <= 0 or len(message) <= limit:
        return message
    suffix = "\n\n[通知已截断；完整结果与产物保存在公司运行目录。]"
    return message[:max(0, limit - len(suffix))].rstrip() + suffix


def mirror_management_message(config: dict[str, Any], origin: dict[str, str], message: str) -> bool:
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
    except Exception as exc:
        LOGGER.warning("mirror_management_message failed: %s", exc, exc_info=True)
        return False


def _operations_db_path(config: dict[str, Any]) -> Path | None:
    value = str(config.get("operations_db") or "").strip()
    return Path(value) if value else None


def _management_origin(config: dict[str, Any]) -> dict[str, str]:
    """Resolve the latest known management conversation without guessing."""
    try:
        from .operations_control import latest_origin
    except ImportError:
        from operations_control import latest_origin
    router_db = Path(str(config.get("router_db") or config.get("state_db") or ""))
    try:
        return latest_origin(router_db)
    except (OSError, ValueError, sqlite3.Error):
        return {}


def _cron_output_root(config: dict[str, Any]) -> Path:
    configured = str(config.get("cron_output_root") or "").strip()
    if configured:
        return Path(configured)
    return Path("/home/pwn/.hermes/cron/output")


def _read_cron_jobs(config: dict[str, Any]) -> list[dict[str, Any]]:
    configured = str(config.get("cron_jobs_path") or "").strip()
    path = Path(configured) if configured else Path("/home/pwn/.hermes/cron/jobs.json")
    try:
        payload = json.loads(read_text_limited(path, max_bytes=5 * 1024 * 1024))
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("jobs")
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _cron_run_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _find_cron_output(config: dict[str, Any], job_id: str, last_run_at: str) -> Path | None:
    if not SAFE_ID_RE.fullmatch(str(job_id or "")):
        return None
    output_root = _cron_output_root(config).resolve()
    root = output_root / job_id
    if root.is_symlink():
        return None
    try:
        root.resolve(strict=False).relative_to(output_root)
    except (OSError, ValueError):
        return None
    if not root.is_dir():
        return None
    run_at = _cron_run_datetime(last_run_at)
    candidates_with_mtime: list[tuple[float, Path]] = []
    for item in root.glob("*.md"):
        if item.is_symlink() or not item.is_file():
            continue
        try:
            item.resolve().relative_to(root.resolve())
            candidates_with_mtime.append((item.stat().st_mtime, item))
        except (OSError, ValueError):
            continue
    candidates = [item for _, item in sorted(candidates_with_mtime, key=lambda pair: pair[0], reverse=True)]
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
        text = read_text_limited(path, max_bytes=MAX_CRON_OUTPUT_BYTES, errors="replace")
    except (OSError, ValueError):
        return ""
    # Agent-driven jobs save the prompt and final answer under this marker.
    marker = "\n## Response\n"
    if marker in text:
        text = text.rsplit(marker, 1)[1].lstrip()
        text = text.removeprefix("---")
    else:
        # no-agent scripts generally contain a short header followed by JSON.
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            text = parts[1]
    return text.strip()


def _extract_cron_json(path: Path) -> dict[str, Any]:
    """Parse a no-agent Cron script's JSON response, if present."""
    response = _extract_cron_response(path)
    try:
        value = json.loads(response)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _cron_recovery_payload(
    config: dict[str, Any],
    job: dict[str, Any],
    output: Path,
    fallback_origin: dict[str, str],
) -> tuple[dict[str, str], str, str, dict[str, Any]]:
    """Return (origin, message, kind, metadata) for a saved Cron output."""
    name = str(job.get("name") or job.get("id") or "cron")
    # TVCR's no-agent output contains a stable review ID.  Format the concise
    # governance message from the operations DB instead of forwarding raw JSON.
    if name == "company-tvcr-daily-review":
        payload = _extract_cron_json(output)
        review_id = str(payload.get("review_id") or "")
        operations_db = _operations_db_path(config)
        if review_id and operations_db is not None:
            try:
                from .operations_control import format_review_message
            except ImportError:
                from operations_control import format_review_message
            row = None
            db = None
            try:
                db = sqlite3.connect(sqlite_uri(operations_db, mode="ro"), uri=True)
                db.row_factory = sqlite3.Row
                row = db.execute(
                    "SELECT delivery_platform,delivery_chat_id,delivery_thread_id,delivery_user_id "
                    "FROM tvcr_reviews WHERE review_id=?",
                    (review_id,),
                ).fetchone()
            except sqlite3.Error:
                row = None
            finally:
                if db is not None:
                    db.close()
            origin = dict(fallback_origin)
            if row:
                stored = {
                    "platform": str(row["delivery_platform"] or ""),
                    "chat_id": str(row["delivery_chat_id"] or ""),
                    "thread_id": str(row["delivery_thread_id"] or ""),
                    "user_id": str(row["delivery_user_id"] or ""),
                }
                if stored["platform"] and stored["chat_id"]:
                    origin = stored
            limit = _positive_limit(
                (config.get("tvcr_delivery_chars_by_platform") or {}).get(
                    origin.get("platform", ""),
                ),
                _positive_limit(config.get("tvcr_delivery_default_chars", 1000), 1000),
            )
            try:
                return (
                    origin,
                    format_review_message(operations_db, review_id, limit=limit),
                    "tvcr_cron",
                    {"review_id": review_id},
                )
            except (OSError, sqlite3.Error, ValueError):
                # A missing/unreadable operations DB (or a review that is not
                # yet recorded there) must not abort the whole notifier tick:
                # fall back to forwarding the raw saved cron output.
                pass
    response = _extract_cron_response(output)
    return (
        fallback_origin,
        f"【补发】{name}\n运行时间：{job.get('last_run_at', '')}\n\n{response}",
        "cron_recovery",
        {},
    )


def _outbox_metadata(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata_json")
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sync_tvcr_review_from_outbox(config: dict[str, Any], row: dict[str, Any]) -> None:
    """Project a TVCR outbox state back into its review delivery fields."""
    if str(row.get("kind") or "") != "tvcr_cron":
        return
    review_id = str(_outbox_metadata(row).get("review_id") or "")
    db_path = _operations_db_path(config)
    if not review_id or db_path is None:
        return
    db = None
    try:
        db = sqlite3.connect(sqlite_uri(db_path, mode="ro"), uri=True)
        current = db.execute(
            "SELECT delivered,delivery_attempts FROM tvcr_reviews WHERE review_id=?",
            (review_id,),
        ).fetchone()
    except sqlite3.Error:
        current = None
    finally:
        if db is not None:
            db.close()
    if current is None:
        return

    state = str(row.get("state") or "")
    outbox_attempts = _safe_counter(row.get("attempts"))
    attempts = max(_safe_counter(current[1]), outbox_attempts)
    fields: dict[str, Any]
    if state == "delivered":
        fields = {
            "delivered": 1,
            "delivery_attempts": max(1, attempts),
            "delivery_error": "",
            "last_delivery_at": str(row.get("delivered_at") or row.get("updated_at") or utc_now()),
        }
    elif state == "dead_letter":
        if _safe_counter(current[0]):
            return
        reason = str(row.get("last_error") or "delivery failed")
        fields = {
            "delivered": 0,
            "delivery_attempts": max(1, attempts),
            "delivery_error": (
                f"terminal: outbox delivery failed: {reason}; "
                f"notification_id={row.get('notification_id', '')}; "
                f"fallback={_delivery_fallback_path(config)}"
            ),
            "last_delivery_at": str(
                row.get("dead_lettered_at") or row.get("last_attempt_at")
                or row.get("updated_at") or utc_now()
            ),
        }
    elif state == "pending" and outbox_attempts > 0:
        if _safe_counter(current[0]):
            return
        fields = {
            "delivered": 0,
            "delivery_attempts": attempts,
            "delivery_error": str(row.get("last_error") or "delivery failed"),
            "last_delivery_at": str(row.get("last_attempt_at") or row.get("updated_at") or utc_now()),
        }
    else:
        return

    try:
        _, _, update_review = _operations_api()
        update_review(db_path, review_id, **fields)
    except (OSError, sqlite3.Error, ValueError) as exc:
        # The durable outbox remains the source of truth and the next recovery
        # scan will retry this projection without resending a delivered row —
        # but a persistent projection failure must stay visible to operators.
        LOGGER.warning(
            "tvcr outbox projection failed for review %s: %s",
            review_id, exc, exc_info=True,
        )
        return


def recover_failed_cron_deliveries(config: dict[str, Any]) -> int:
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
    queued = 0
    for job in _read_cron_jobs(config):
        job_id = str(job.get("id") or "")
        name = str(job.get("name") or job_id)
        is_allowed = job_id in allowed or name in allowed
        local_recovery = str(job.get("deliver") or "").lower() == "local"
        if not is_allowed or (not local_recovery and not job.get("last_delivery_error")):
            continue
        last_run = str(job.get("last_run_at") or "")
        if not last_run:
            continue
        output = _find_cron_output(config, job_id, last_run)
        if output is None:
            continue
        origin_for_job, message, kind, source_metadata = _cron_recovery_payload(
            config, job, output, origin,
        )
        if not message.strip() or not origin_for_job.get("platform") or not origin_for_job.get("chat_id"):
            continue
        message = _fit_delivery_message(config, origin_for_job, message)
        dedup_key = f"cron-delivery:{job_id}:{last_run}"
        existing = get_outbox_by_dedup_key(db_path, dedup_key)
        enqueue_outbox(
            db_path,
            dedup_key=dedup_key,
            kind=kind,
            source_id=f"{job_id}:{last_run}",
            origin=origin_for_job,
            message=message,
            metadata={
                "job_id": job_id,
                "job_name": name,
                "output_path": str(output),
                "delivery_error": str(job.get("last_delivery_error") or ""),
                **source_metadata,
            },
        )
        current = get_outbox_by_dedup_key(db_path, dedup_key)
        if current is not None and kind == "tvcr_cron":
            # Also heals rows created before TVCR metadata projection existed.
            sync_row = dict(current)
            sync_row["kind"] = kind
            metadata = _outbox_metadata(sync_row)
            metadata.update(source_metadata)
            sync_row["metadata_json"] = metadata
            _sync_tvcr_review_from_outbox(config, sync_row)
        if existing is None:
            queued += 1
    return queued


def process_outbox(
    config: dict[str, Any],
    *,
    deliverer: DeliveryFn,
    mirror: MirrorFn | None = None,
) -> dict[str, int]:
    """Deliver ready outbox rows with bounded retry and dead-lettering."""
    result = {"checked": 0, "delivered": 0, "failed": 0, "dead_letter": 0}
    db_path = _operations_db_path(config)
    if db_path is None:
        return result
    fallback = _delivery_fallback_path(config)
    rows = pending_outbox(
        db_path,
        limit=_int_config(config, "outbox_batch_size", 20),
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
            _sync_tvcr_review_from_outbox(config, dead)
            result["dead_letter"] += 1
            continue
        try:
            ok, error = deliverer(config, origin, message)
        except Exception as exc:  # noqa: BLE001 -- a broken sender plugin must not stop the batch
            ok, error = False, f"{exc.__class__.__name__}: {exc}"
        if ok:
            mark_outbox_delivered(db_path, notification_id)
            delivered_row = get_outbox_by_dedup_key(db_path, str(row.get("dedup_key") or "")) or row
            _sync_tvcr_review_from_outbox(config, delivered_row)
            if mirror is None:
                if str(row.get("kind") or "") == "tvcr_cron":
                    mirror_tvcr_message(config, origin, message)
                else:
                    mirror_management_message(config, origin, message)
            else:
                mirror(origin, message)
            result["delivered"] += 1
            continue
        updated = record_outbox_failure(
            db_path,
            notification_id,
            error,
            max_attempts=_int_config(config, "outbox_max_attempts", 12),
            retry_base_seconds=_int_config(config, "delivery_retry_base_seconds", 60),
            retry_max_seconds=_int_config(config, "delivery_retry_max_seconds", 3600),
        )
        if updated.get("state") == "dead_letter":
            append_outbox_dead_letter(fallback, updated, reason=str(error or "delivery failed"))
            _sync_tvcr_review_from_outbox(config, updated)
            result["dead_letter"] += 1
        else:
            _sync_tvcr_review_from_outbox(config, updated)
            result["failed"] += 1
    return result


def _record_retry_exhaustion(
    config: dict[str, Any],
    *,
    kind: str,
    identifier: str,
    origin: dict[str, str],
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
    config: dict[str, Any],
    *,
    deliverer: DeliveryFn = deliver_message,
    mirror: MirrorFn | None = None,
) -> dict[str, int]:
    """Reconcile run state, recover dead runners, and deliver terminal results."""
    summary = {
        "checked": 0, "running": 0, "restarted": 0, "delivered": 0,
        "failed": 0, "waiting_target": 0, "terminal": 0, "suspected_dead": 0,
        "outbox_enqueued": 0, "outbox_checked": 0,
        "outbox_delivered": 0, "outbox_failed": 0, "outbox_dead_letter": 0,
    }
    if not config.get("proactive_delivery", True):
        return summary

    # Import failed Hermes-origin deliveries before polling normal worker
    # results.  The outbox sender is deliberately single-owner (this cron job)
    # so a live-adapter failure cannot race a second recovery sender.  A locked
    # or unavailable operations DB must not abort the whole one-minute tick.
    if _operations_db_path(config) is not None:
        try:
            summary["outbox_enqueued"] = recover_failed_cron_deliveries(config)
            outbox_result = process_outbox(config, deliverer=deliverer, mirror=mirror)
            summary["outbox_checked"] = outbox_result["checked"]
            summary["outbox_delivered"] = outbox_result["delivered"]
            summary["outbox_failed"] = outbox_result["failed"]
            summary["outbox_dead_letter"] = outbox_result["dead_letter"]
        except (OSError, sqlite3.Error, ValueError) as exc:
            LOGGER.warning("outbox recovery/processing failed: %s", exc, exc_info=True)

    state = RouterState(config["state_db"])
    try:
        rows = state.pending_notifications(_int_config(config, "max_delivery_attempts", 10))
        for row in rows:
            summary["checked"] += 1
            event_id = str(row["route_event_id"])
            run_id = str(row["run_id"])
            try:
                result = swarm_command(config, "task", "result", "--run-id", run_id, timeout=20)
            except Exception as exc:  # noqa: BLE001 -- a failing status query must not re-poll forever
                # A persistently failing status query (e.g. the run was removed
                # from the swarm DB) must not re-poll forever: advance the
                # attempt counter so the row eventually dead-letters.
                state.update(
                    event_id,
                    error=f"run status query failed: {exc}",
                    delivery_attempts=_safe_counter(row["delivery_attempts"]) + 1,
                    last_delivery_at=utc_now(),
                )
                summary["failed"] += 1
                continue

            status = str(result.get("status") or "unknown")
            state.update(event_id, status=status)
            if status in {"submitted", "running"}:
                summary["running"] += 1
                alive = runner_is_alive(row["runner_pid"], run_id)
                _beat_heartbeat(state, event_id, alive)
                stale = _age_minutes(str(row["created_at"] or "")) >= _float_config(config, "stale_run_minutes", 15)
                restarts = _safe_counter(row["runner_restarts"])
                max_restarts = _int_config(config, "max_runner_restarts", 2)
                if stale and not alive and restarts < max_restarts:
                    try:
                        raw_decision = json.loads(row["decision_json"])
                        if not isinstance(raw_decision, dict):
                            raise TypeError("decision_json root must be an object")
                        decision = RouteDecision(**raw_decision)
                        pid = launch_runner(config, run_id, decision.intent)
                        state.update(event_id, runner_pid=pid, runner_restarts=restarts + 1, last_heartbeat=utc_now(), error="")
                        summary["restarted"] += 1
                    except Exception as exc:  # noqa: BLE001 -- runner recovery failure must not abort the sweep
                        state.update(event_id, error=f"runner recovery failed: {exc}")
                        summary["failed"] += 1
                elif _mark_suspected_dead_if_stale(
                    state, config, row, event_id,
                    alive=alive, restarts=restarts, max_restarts=max_restarts,
                ):
                    summary["suspected_dead"] += 1
                continue

            if status not in {"completed", "needs_approval", "failed", "cancelled"}:
                # Unknown/unhandled status (corrupt payload, deleted run) must
                # not be re-polled forever; record it and advance attempts so
                # the row can dead-letter after max_delivery_attempts.
                state.update(
                    event_id,
                    error=f"unhandled swarm run status: {status}",
                    delivery_attempts=_safe_counter(row["delivery_attempts"]) + 1,
                    last_delivery_at=utc_now(),
                )
                summary["failed"] += 1
                continue
            if not _delivery_retry_ready(config, row):
                continue

            origin = _origin_for_row(config, row)
            attempts = _safe_counter(row["delivery_attempts"]) + 1
            if not origin:
                missing_target = "could not resolve original conversation"
                if attempts >= _int_config(config, "max_delivery_attempts", 10):
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
                    delivery_attempts=_int_config(config, "max_delivery_attempts", 10),
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
            try:
                ok, error = deliverer(config, origin, message)
            except Exception as exc:  # noqa: BLE001 -- sender plugins must not stop the tick
                ok, error = False, f"{exc.__class__.__name__}: {exc}"
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
            elif attempts >= _int_config(config, "max_delivery_attempts", 10):
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
        content_rows = state.pending_content_notifications(_int_config(config, "max_delivery_attempts", 10))
        for row in content_rows:
            summary["checked"] += 1
            event_id = str(row["route_event_id"])
            run_id = str(row["run_id"])
            try:
                status_path = content_job_path(config, run_id) / "status.json"
            except ValueError as exc:
                state.update(event_id, status="failed", error=f"invalid content run path: {exc}")
                summary["failed"] += 1
                continue
            payload: dict[str, Any] = {}
            if status_path.exists():
                try:
                    # O_NOFOLLOW makes the symlink rejection atomic: the
                    # content-jobs directory is written by an untrusted worker,
                    # and a swap between an is_symlink() check and a following
                    # open would leak file content into a delivered message.
                    value = json.loads(
                        read_text_limited_nofollow(status_path, max_bytes=2 * 1024 * 1024)
                    )
                    if not isinstance(value, dict):
                        raise TypeError("content status root must be an object")
                    payload = value
                except (OSError, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    # A persistently unreadable/corrupt status.json must not be
                    # re-polled forever; advance the attempt counter so the row
                    # can dead-letter after max_delivery_attempts.
                    state.update(
                        event_id,
                        error=f"content status query failed: {exc}",
                        delivery_attempts=_safe_counter(row["delivery_attempts"]) + 1,
                        last_delivery_at=utc_now(),
                    )
                    summary["failed"] += 1
                    continue
            status = str(payload.get("status") or row["status"] or "running")
            state.update(event_id, status=status)
            if status in {"submitted", "running"}:
                summary["running"] += 1
                alive = content_runner_is_alive(row["runner_pid"], run_id)
                _beat_heartbeat(state, event_id, alive)
                stale = _age_minutes(str(row["created_at"] or "")) >= _float_config(config, "stale_run_minutes", 15)
                restarts = _safe_counter(row["runner_restarts"])
                max_restarts = _int_config(config, "max_runner_restarts", 2)
                if stale and not alive and restarts < max_restarts:
                    try:
                        pid = launch_content_job(config, run_id)
                        state.update(event_id, runner_pid=pid, runner_restarts=restarts + 1, last_heartbeat=utc_now(), error="")
                        summary["restarted"] += 1
                    except Exception as exc:  # noqa: BLE001 -- runner recovery failure must not abort the sweep
                        state.update(event_id, error=f"content runner recovery failed: {exc}")
                        summary["failed"] += 1
                elif _mark_suspected_dead_if_stale(
                    state, config, row, event_id,
                    alive=alive, restarts=restarts, max_restarts=max_restarts,
                ):
                    summary["suspected_dead"] += 1
                continue
            if status not in {"completed", "needs_approval", "failed", "cancelled"}:
                # Unhandled statuses (e.g. review/qa from the job state machine)
                # must not be re-polled forever; record and advance attempts so
                # the row can dead-letter after max_delivery_attempts.
                state.update(
                    event_id,
                    error=f"unhandled content job status: {status}",
                    delivery_attempts=_safe_counter(row["delivery_attempts"]) + 1,
                    last_delivery_at=utc_now(),
                )
                summary["failed"] += 1
                continue
            if not _delivery_retry_ready(config, row):
                continue

            origin = _origin_for_row(config, row)
            attempts = _safe_counter(row["delivery_attempts"]) + 1
            if not origin:
                missing_target = "could not resolve original conversation"
                if attempts >= _int_config(config, "max_delivery_attempts", 10):
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
                    delivery_attempts=_int_config(config, "max_delivery_attempts", 10),
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
            try:
                ok, error = deliverer(config, origin, message)
            except Exception as exc:  # noqa: BLE001 -- sender plugins must not stop the tick
                ok, error = False, f"{exc.__class__.__name__}: {exc}"
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
            elif attempts >= _int_config(config, "max_delivery_attempts", 10):
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
    if operations_db and not config.get("tvcr_delivery_via_outbox", False):
        try:
            format_review_message, pending_review_deliveries, update_review = _operations_api()
            review_rows = pending_review_deliveries(
                Path(operations_db), _int_config(config, "max_delivery_attempts", 10)
            )
            for row in review_rows:
                summary["checked"] += 1
                if not _delivery_retry_ready(config, row):
                    continue
                review_id = str(row["review_id"])
                attempts = _safe_counter(row["delivery_attempts"]) + 1
                origin = {
                    "platform": str(row["delivery_platform"] or ""),
                    "chat_id": str(row["delivery_chat_id"] or ""),
                    "thread_id": str(row["delivery_thread_id"] or ""),
                    "user_id": str(row["delivery_user_id"] or ""),
                }
                if not origin["platform"] or not origin["chat_id"]:
                    missing_target = "TVCR review has no delivery target"
                    if attempts >= _int_config(config, "max_delivery_attempts", 10):
                        missing_message = format_review_message(
                            Path(operations_db), review_id,
                            limit=_int_config(config, "tvcr_delivery_default_chars", 1000),
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
                    limit=_positive_limit(
                        (config.get("tvcr_delivery_chars_by_platform") or {}).get(origin["platform"]),
                        _positive_limit(config.get("tvcr_delivery_default_chars", 1000), 1000),
                    ),
                )
                terminal_reason = _terminal_reason(config, origin)
                if terminal_reason:
                    fallback = record_terminal_delivery(
                        config, kind="tvcr", identifier=review_id, origin=origin,
                        message=message, reason=terminal_reason,
                    )
                    update_review(
                        Path(operations_db), review_id,
                        delivery_attempts=_int_config(config, "max_delivery_attempts", 10),
                        delivery_error=f"terminal: {terminal_reason}; fallback={fallback}",
                        last_delivery_at=utc_now(),
                    )
                    summary["terminal"] += 1
                    continue
                try:
                    ok, error = deliverer(config, origin, message)
                except Exception as exc:  # noqa: BLE001 -- sender plugins must not stop the tick
                    ok, error = False, f"{exc.__class__.__name__}: {exc}"
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
                elif attempts >= _int_config(config, "max_delivery_attempts", 10):
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
        except (OSError, sqlite3.Error, ValueError) as exc:
            # A locked/unavailable operations DB must not abort the whole
            # one-minute delivery tick; skip this cycle and retry next time.
            LOGGER.warning("tvcr review delivery skipped: %s", exc, exc_info=True)
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
