#!/usr/bin/env python3
"""Route company conversations to the correct product line.

The primary integration is a Hermes ``pre_llm_call`` shell hook. Hermes sends
one JSON payload on stdin; this program emits either ``{}`` or a
``{"context": "..."}`` response. Security work is submitted to the existing
swarm client API. Other product lines are annotated for the main company agent
until their own execution adapters are enabled.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    from ._safe_io import file_lock, locked_atomic_write_text, quote_identifier, sqlite_uri
except ImportError:  # direct ``python automation/company_router.py`` invocation
    from _safe_io import file_lock, locked_atomic_write_text, quote_identifier, sqlite_uri


HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "router_config.json"
INTERNAL_WORKER_PREFIX = "[COMPANY_WORKER_INTERNAL]"
TVCR_INTERNAL_PREFIX = "[COMPANY_TVCR_INTERNAL]"
OPERATOR_INTERNAL_PREFIX = "[COMPANY_OPERATOR_INTERNAL]"
INTERNAL_MESSAGE_PREFIXES = (INTERNAL_WORKER_PREFIX, TVCR_INTERNAL_PREFIX, OPERATOR_INTERNAL_PREFIX)
INTERNAL_MESSAGE_PREFIX_RE = re.compile(
    rf"^(?:\s*(?:{'|'.join(re.escape(prefix) for prefix in INTERNAL_MESSAGE_PREFIXES)})\s*)+",
    re.I,
)

EXPLICIT_NEW_SWARM_RE = re.compile(
    r"(?:new|fresh|another)\s+swarm|(?:新的?|新建|另开|再开|重新(?:提交|分发|启动))[^，。；\n]{0,8}(?:蜂群|swarm)",
    re.I,
)
SKILL_REVIEW_MARKER = "update the skill library"
HERMES_STATE_DB = Path("/home/pwn/.hermes/state.db")
PRE_EVAL_MIN_PRIOR_MESSAGES = 3  # user+assistant messages before this dispatch
PRE_EVAL_MIN_PRIOR_USER_MESSAGES = 1  # the current message doesn't count


SECURITY_TERMS = {
    "安全", "漏洞", "赏金", "hackerone", "bug bounty", "渗透", "红队",
    "recon", "exploit", "cve", "apk", "逆向", "攻击面", "扫描", "蜂群",
    "swarm", "poc", "idor", "xss", "sqli", "ssrf", "jwt", "cors",
}
ARTICLE_TERMS = {"文章", "公众号", "写稿", "排版", "选题", "草稿箱", "润色", "发布文章"}
VIDEO_TERMS = {"视频", "pixelle", "b站", "分镜", "配音", "tts", "字幕", "剪辑"}
COMPANY_TERMS = {"公司", "战略", "财务", "销售", "运营", "产品", "流程", "知识库", "仪表盘"}
MANAGEMENT_TERMS = {"状态", "进度", "流程", "路由", "架构", "能力", "管理", "如何", "怎么", "是否", "当前"}
COMPANY_EXECUTION_TERMS = {
    "开始", "执行", "修改", "实现", "开发", "完善", "更新", "新增", "接入",
    "搭建", "创建", "生成", "整理", "迁移", "重构", "验证", "落地", "推进",
    "调研", "排查", "制定", "编写", "补充", "删除",
    "implement", "build", "update", "create", "refactor",
}

ACTIVE_SECURITY_TERMS = {
    "扫描", "探测", "枚举", "爆破", "利用", "攻击", "绕过", "验证漏洞",
    "recon", "scan", "exploit", "brute", "probe", "fuzz",
}
AUTHORIZATION_TERMS = {
    "已授权", "明确授权", "授权范围", "in scope", "in-scope", "scope内",
    "hackerone项目", "hackerone program", "赏金项目", "自有系统", "本地靶场",
}  # retained for reference only — NOT trusted for authorization (see classify_message)
EXTERNAL_ACTION_TERMS = {
    "发布", "推送", "提交hackerone", "发送", "删除", "付款", "转账", "上线",
}
# Only strip an external-action term when the negator is directly attached
# ("不发布", "禁止推送"). A wide window used to swallow "不要忘记发布" and let a
# real publish slip through; a near-miss now stays flagged (fail toward approval).
NEGATED_EXTERNAL_ACTION_RE = re.compile(
    r"(?:不|不要|无需|禁止|不得|暂不|先不|仅生成|只生成)[^，。；\n]{0,1}"
    r"(?:发布|推送|提交hackerone|发送|删除|付款|转账|上线)",
    re.I,
)

DOMAIN_RE = re.compile(r"(?<![@\w-])(?:https?://)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)(?::\d+)?", re.I)
IP_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
APK_RE = re.compile(r"(?:^|\s)(/[^\s]+\.apk|[^\s]+\.apk)(?:$|\s)", re.I)


@dataclass(frozen=True)
class RouteDecision:
    route: str
    confidence: float
    action: str
    reason: str
    intent: str = "custom"
    target_type: str = "unknown"
    target: str = ""
    profile: str = "balanced"
    authorization_required: bool = False
    external_action: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(path: Path = DEFAULT_CONFIG) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["config_path"] = str(path)
    return data


def resolve_session_origin(index_path: str, session_id: str) -> Dict[str, str]:
    """Resolve a Hermes agent session ID back to its messaging destination."""
    if not index_path or not session_id:
        return {}
    try:
        payload = json.loads(Path(index_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    for entry in payload.values():
        if not isinstance(entry, dict) or str(entry.get("session_id") or "") != session_id:
            continue
        origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
        chat_id = str(origin.get("chat_id") or "")
        platform = str(origin.get("platform") or entry.get("platform") or "")
        if not chat_id or not platform:
            return {}
        return {
            "platform": platform,
            "chat_id": chat_id,
            "thread_id": str(origin.get("thread_id") or ""),
            "user_id": str(origin.get("user_id") or ""),
        }
    return {}


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _score(text: str, terms: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if term.lower() in lowered)


def _has_external_action(text: str) -> bool:
    cleaned = NEGATED_EXTERNAL_ACTION_RE.sub("", text)
    return _contains_any(cleaned, EXTERNAL_ACTION_TERMS)


def _internal_metadata_value(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if not isinstance(value, str):
        return False
    return value.strip().lower() in {
        "1", "true", "yes", "internal", "internal_call", "internal-routing",
        "internal_routing", "routing",
    }


def _is_internal_hermes_hook(payload: Dict[str, Any], extra: Dict[str, Any]) -> bool:
    """Trust internal prefixes only when hook provenance explicitly says so."""
    source_present = "source" in payload or "source" in extra
    source = payload.get("source") if "source" in payload else extra.get("source")
    if source_present and str(source or "").strip().lower() != "hook":
        return False

    metadata: list[Dict[str, Any]] = []
    for container in (payload, extra):
        for key in ("hook_metadata", "metadata"):
            value = container.get(key)
            if isinstance(value, dict):
                metadata.append(value)

    flag_keys = {
        "internal", "is_internal", "internal_call", "is_internal_call",
        "internal_routing_event", "is_internal_routing_event", "router_internal",
    }
    type_keys = {"call_type", "event_type", "routing_event", "origin"}
    internal_marker_present = bool(metadata)
    for container in (payload, extra, *metadata):
        internal_marker_present = internal_marker_present or any(
            key in container for key in flag_keys | type_keys
        )
        if any(_internal_metadata_value(container.get(key)) for key in flag_keys):
            return True
        if any(_internal_metadata_value(container.get(key)) for key in type_keys):
            return True

    # Hermes workers are launched with ``--source tool``. The shell hook
    # inherits that trusted process-local source even on Hermes versions that
    # do not yet include source metadata in the JSON payload.
    if (
        not source_present
        and str(payload.get("hook_event_name") or "").lower() == "pre_llm_call"
        and not internal_marker_present
        and os.getenv("HERMES_SESSION_SOURCE", "").strip().lower() == "tool"
    ):
        return True

    # Preserve compatibility for legacy in-process worker calls that predate
    # the shell-hook envelope. Real Hermes hook payloads include an event name.
    return (
        not source_present
        and "hook_event_name" not in payload
        and not internal_marker_present
        and str(payload.get("session_id") or "") == "worker"
    )


def _strip_internal_message_prefixes(message: str) -> str:
    return INTERNAL_MESSAGE_PREFIX_RE.sub("", message or "", count=1).lstrip()


def extract_target(message: str) -> list[tuple[str, str]]:
    candidates: list[tuple[int, int, int, str, str]] = []

    for match in APK_RE.finditer(message or ""):
        candidates.append((match.start(1), match.end(1), 0, "apk", match.group(1)))

    for match in IP_RE.finditer(message or ""):
        raw = match.group(0)
        try:
            ipaddress.ip_address(raw)
            candidates.append((match.start(), match.end(), 0, "ip", raw))
        except ValueError:
            continue

    for match in DOMAIN_RE.finditer(message or ""):
        candidates.append((match.start(1), match.end(1), 1, "domain", match.group(1).lower()))

    targets: list[tuple[str, str]] = []
    accepted_spans: list[tuple[int, int]] = []
    seen: set[tuple[str, str]] = set()
    for start, end, _priority, target_type, target in sorted(candidates):
        if any(start < accepted_end and end > accepted_start for accepted_start, accepted_end in accepted_spans):
            continue
        accepted_spans.append((start, end))
        key = (target_type, target.lower())
        if key in seen:
            continue
        targets.append((target_type, target))
        seen.add(key)
    return targets


def _normalize_target(target_type: str, target: str) -> tuple[str, str]:
    normalized_type = str(target_type or "unknown").strip().lower()
    normalized_target = str(target or "").strip().lower()
    if normalized_type == "domain":
        normalized_target = normalized_target.rstrip(".")
    elif normalized_type == "ip":
        try:
            normalized_target = str(ipaddress.ip_address(normalized_target))
        except ValueError:
            pass
    return normalized_type, normalized_target


def _build_dedup_key(session_id: str, targets: Iterable[tuple[str, str]], intent: str) -> str:
    normalized_targets = sorted({
        _normalize_target(target_type, target)
        for target_type, target in targets
        if str(target or "").strip()
    })
    target_key = ",".join(f"{target_type}:{target}" for target_type, target in normalized_targets)
    return f"{session_id}|{target_key or 'no-target'}|{str(intent or 'custom').strip().lower()}"


def _is_internal_target(target_type: str, target: str) -> bool:
    """Local/own targets that need no external scope authorization."""
    if target_type == "apk":
        return True
    value = (target or "").lower()
    if value.startswith("/home/pwn/workspace/") or value.startswith("localhost"):
        return True
    if target_type == "ip":
        try:
            addr = ipaddress.ip_address(target)
            return addr.is_private or addr.is_loopback
        except ValueError:
            return False
    return False


def classify_message(message: str, authorized_targets: Iterable[str] = ()) -> RouteDecision:
    text = " ".join((message or "").split())
    lowered = text.lower()

    explicit = ""
    for prefix, route in (
        ("/research", "security"), ("/security", "security"),
        ("研究：", "security"), ("安全：", "security"),
        ("/article", "article"), ("文章：", "article"),
        ("/video", "video"), ("视频：", "video"),
        ("/company", "company"), ("公司：", "company"),
    ):
        if lowered.startswith(prefix.lower()):
            explicit = route
            break

    scores = {
        "security": _score(text, SECURITY_TERMS),
        "article": _score(text, ARTICLE_TERMS),
        "video": _score(text, VIDEO_TERMS),
        "company": _score(text, COMPANY_TERMS),
    }
    route = explicit or max(scores, key=scores.get)
    top = scores[route]
    if not explicit and top == 0:
        route = "company"
    elif not explicit and scores["company"] > 0 and _contains_any(text, MANAGEMENT_TERMS):
        route = "company"

    confidence = 0.99 if explicit else min(0.95, 0.45 + 0.12 * top)
    external_action = _has_external_action(text)

    if route != "security":
        action = {
            "article": "dispatch_article",
            "video": "dispatch_video",
        }.get(route, "main_agent")
        if route == "company" and _contains_any(text, COMPANY_EXECUTION_TERMS):
            action = "dispatch_company"
        if external_action:
            action = "approval_required"
        return RouteDecision(
            route=route,
            confidence=confidence,
            action=action,
            reason=f"matched {route} product-line vocabulary",
            external_action=external_action,
        )

    targets = extract_target(text)
    target_type, target = targets[0] if targets else ("unknown", "")
    if any(term in lowered for term in ("生成报告", "写报告", "整理报告", "输出报告", "出报告", "write report", "writeup")):
        intent = "report"
    elif any(term in lowered for term in ("利用", "exploit", "poc")):
        intent = "exploit"
    elif _contains_any(text, ACTIVE_SECURITY_TERMS):
        intent = "recon"
    else:
        intent = "analyze"

    # exploit (incl. poc) and recon are active testing; report/analyze are passive.
    active = intent in {"exploit", "recon"}
    # Authorization comes from a trusted scope allowlist (config), NEVER from
    # in-band user text like "已授权". Internal/local targets need no scope.
    allow = {str(item).strip().lower() for item in authorized_targets if str(item).strip()}
    unauthorized_targets = [
        (candidate_type, candidate)
        for candidate_type, candidate in targets
        if candidate.lower() not in allow and not _is_internal_target(candidate_type, candidate)
    ]
    authorization_required = bool(active and unauthorized_targets)

    profile = "breadth" if intent == "recon" else "depth" if intent in {"analyze", "exploit"} else "balanced"
    action = "approval_required" if authorization_required or external_action else "dispatch_swarm"
    reason = "security task requires explicit scope authorization" if authorization_required else "security task routed to research swarm"
    return RouteDecision(
        route="security",
        confidence=confidence,
        action=action,
        reason=reason,
        intent=intent,
        target_type=target_type,
        target=target,
        profile=profile,
        authorization_required=authorization_required,
        external_action=external_action,
    )


class RouterState:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db: Optional[sqlite3.Connection] = None
        try:
            # Schema creation/migrations are writes too; serialize first
            # startup so concurrent hooks cannot race on ALTER TABLE/indices.
            with file_lock(self.path):
                self.db = sqlite3.connect(self.path, timeout=5.0)
                self.db.row_factory = sqlite3.Row
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA busy_timeout=5000")
                self.db.executescript(
                    """
            CREATE TABLE IF NOT EXISTS route_events (
                route_event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                platform TEXT DEFAULT '',
                message_hash TEXT NOT NULL,
                message_excerpt TEXT DEFAULT '',
                route TEXT NOT NULL,
                action TEXT NOT NULL,
                dedup_key TEXT DEFAULT '',
                decision_json TEXT NOT NULL,
                run_id TEXT DEFAULT '',
                request_id TEXT DEFAULT '',
                runner_pid INTEGER,
                status TEXT DEFAULT 'routed',
                result_delivered INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, message_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_route_events_session
            ON route_events(session_id, created_at DESC);
            """
                )
                columns = {row[1] for row in self.db.execute("PRAGMA table_info(route_events)")}
                migrations = {
                    "delivery_platform": "TEXT DEFAULT ''",
                    "delivery_chat_id": "TEXT DEFAULT ''",
                    "delivery_thread_id": "TEXT DEFAULT ''",
                    "delivery_user_id": "TEXT DEFAULT ''",
                    "proactive_delivered": "INTEGER DEFAULT 0",
                    "delivery_attempts": "INTEGER DEFAULT 0",
                    "delivery_error": "TEXT DEFAULT ''",
                    "last_delivery_at": "TEXT DEFAULT ''",
                    "runner_restarts": "INTEGER DEFAULT 0",
                    "quality_status": "TEXT DEFAULT ''",
                    "dedup_key": "TEXT DEFAULT ''",
                }
                for column, definition in migrations.items():
                    if column not in columns:
                        safe_column = quote_identifier(column, allowed=migrations)
                        self.db.execute(
                            f"ALTER TABLE route_events ADD COLUMN {safe_column} {definition}"
                        )
                self.db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_route_events_dedup ON route_events(dedup_key, created_at DESC)"
                )
                self.db.commit()
        except BaseException:
            if self.db is not None:
                self.db.close()
                self.db = None
            raise

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def existing(self, session_id: str, message_hash: str) -> Optional[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM route_events WHERE session_id=? AND message_hash=?",
            (session_id, message_hash),
        ).fetchone()

    def active_for_session(self, session_id: str, action: str = "dispatch_swarm") -> list[sqlite3.Row]:
        return list(self.db.execute(
            """SELECT * FROM route_events
               WHERE session_id=? AND action=? AND run_id<>''
                 AND status IN ('submitted','running','completed')
               ORDER BY created_at DESC LIMIT 8""",
            (session_id, action),
        ))

    def recent_for_session(
        self,
        session_id: str,
        action: str,
        since: datetime,
        *,
        completed_only: bool = False,
        message_marker: str = "",
        dedup_key: str = "",
    ) -> Optional[sqlite3.Row]:
        conditions = ["session_id=?", "action=?", "run_id<>''", "created_at>=?"]
        params: list[Any] = [session_id, action, since.isoformat(timespec="seconds")]
        if completed_only:
            conditions.append("status='completed'")
        else:
            conditions.append("status IN ('submitted','running','completed')")
        if message_marker:
            conditions.append("LOWER(message_excerpt) LIKE ?")
            params.append(f"%{message_marker.lower()}%")
        if dedup_key:
            conditions.append("(dedup_key=? OR dedup_key='')")
            params.append(dedup_key)
        rows = self.db.execute(
            f"SELECT * FROM route_events WHERE {' AND '.join(conditions)} "
            "ORDER BY created_at DESC",
            params,
        ).fetchall()
        if not dedup_key:
            return rows[0] if rows else None
        for row in rows:
            stored_key = str(row["dedup_key"] or "")
            if stored_key == dedup_key:
                return row
            if stored_key:
                continue
            try:
                stored_decision = json.loads(row["decision_json"])
            except (TypeError, json.JSONDecodeError):
                stored_decision = {}
            stored_targets = extract_target(str(row["message_excerpt"] or ""))
            if not stored_targets and stored_decision.get("target"):
                stored_targets = [(
                    str(stored_decision.get("target_type") or "unknown"),
                    str(stored_decision.get("target") or ""),
                )]
            candidate_key = _build_dedup_key(
                str(row["session_id"] or ""),
                stored_targets,
                str(stored_decision.get("intent") or "custom"),
            )
            if candidate_key == dedup_key:
                return row
        return None

    def insert(
        self,
        session_id: str,
        platform: str,
        message_hash: str,
        message: str,
        decision: RouteDecision,
        origin: Optional[Dict[str, str]] = None,
        dedup_key: str = "",
    ) -> str:
        event_id = str(uuid.uuid4())
        now = utc_now()
        origin = origin or {}
        if not dedup_key:
            targets = extract_target(message)
            if not targets and decision.target:
                targets = [(decision.target_type, decision.target)]
            dedup_key = _build_dedup_key(session_id, targets, decision.intent)
        values = (
            event_id, session_id, platform, message_hash, message[:500], decision.route,
            decision.action, dedup_key, json.dumps(asdict(decision), ensure_ascii=False),
            str(origin.get("platform") or ""), str(origin.get("chat_id") or ""),
            str(origin.get("thread_id") or ""), str(origin.get("user_id") or ""),
            now, now,
        )
        with file_lock(self.path):
            try:
                self.db.execute(
                    """INSERT INTO route_events
                       (route_event_id,session_id,platform,message_hash,message_excerpt,route,action,dedup_key,
                        decision_json,delivery_platform,delivery_chat_id,delivery_thread_id,
                        delivery_user_id,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    values,
                )
                self.db.commit()
            except sqlite3.IntegrityError:
                # A concurrent hook may have inserted the same session/hash.
                # Reuse that event so only one downstream run is created.
                existing = self.db.execute(
                    "SELECT route_event_id FROM route_events WHERE session_id=? AND message_hash=?",
                    (session_id, message_hash),
                ).fetchone()
                self.db.rollback()
                if existing:
                    return str(existing["route_event_id"])
                raise
        return event_id

    def update(self, event_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        allowed = {
            "run_id", "request_id", "runner_pid", "status", "result_delivered",
            "error", "delivery_platform", "delivery_chat_id", "delivery_thread_id",
            "delivery_user_id", "proactive_delivered", "delivery_attempts",
            "delivery_error", "last_delivery_at", "runner_restarts",
            "quality_status", "updated_at",
        }
        if set(fields) - allowed:
            raise ValueError("unsupported route state field")
        assignments = ", ".join(
            f"{quote_identifier(key, allowed=allowed)}=?" for key in fields
        )
        with file_lock(self.path):
            self.db.execute(
                f"UPDATE route_events SET {assignments} WHERE route_event_id=?",
                (*fields.values(), event_id),
            )
            self.db.commit()

    def pending_notifications(self, max_attempts: int = 10) -> list[sqlite3.Row]:
        return list(self.db.execute(
            """SELECT * FROM route_events
               WHERE action='dispatch_swarm' AND run_id<>''
                 AND proactive_delivered=0 AND result_delivered=0
                 AND delivery_attempts<?
               ORDER BY created_at ASC LIMIT 50""",
            (max_attempts,),
        ))

    def pending_content_notifications(self, max_attempts: int = 10) -> list[sqlite3.Row]:
        return list(self.db.execute(
            """SELECT * FROM route_events
               WHERE action IN ('dispatch_article','dispatch_video','dispatch_company') AND run_id<>''
                 AND proactive_delivered=0 AND result_delivered=0
                 AND delivery_attempts<?
               ORDER BY created_at ASC LIMIT 50""",
            (max_attempts,),
        ))


def _parse_json_output(output: str) -> Dict[str, Any]:
    text = (output or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise RuntimeError(f"command did not return JSON: {text[-500:]}")


def swarm_command(config: Dict[str, Any], *args: str, timeout: int = 30) -> Dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(config["swarm_repo"]) / "swarmctl.py"),
        "--db", config["swarm_db"],
        *args,
        "--json",
    ]
    proc = subprocess.run(cmd, cwd=config["swarm_repo"], capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"swarmctl exited {proc.returncode}")
    return _parse_json_output(proc.stdout)


def submit_security(config: Dict[str, Any], session_id: str, platform: str, message: str, decision: RouteDecision) -> Dict[str, Any]:
    metadata = json.dumps(
        {
            "company_product_line": "security-exploration",
            "company_session_id": session_id,
            "company_platform": platform,
            "router_version": 1,
            "authorization_marker_present": not decision.authorization_required,
        },
        ensure_ascii=False,
    )
    args = [
        "task", "submit",
        "--source", "company-router",
        "--task", message,
        "--intent", decision.intent,
        "--target-type", decision.target_type,
        "--profile", decision.profile,
        "--name", f"company-{decision.intent}-{session_id[-8:] or 'session'}",
        "--metadata", metadata,
    ]
    if decision.target:
        args.extend(["--target", decision.target])
    return swarm_command(config, *args)


def runner_role_counts(intent: str) -> str:
    return {
        "recon": "scanner=2,analyst=1,reporter=1",
        "exploit": "analyst=1,exploiter=1,reporter=1",
        "report": "reporter=1",
        "analyze": "analyst=1,reporter=1",
    }.get(intent, "analyst=1,reporter=1")


def launch_runner(config: Dict[str, Any], run_id: str, intent: str) -> int:
    log_dir = Path(config["log_dir"])
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"swarm-{run_id}.log"
    cmd = [
        sys.executable,
        str(Path(config["swarm_repo"]) / "swarm_runner.py"),
        "--db", config["swarm_db"],
        "--run-id", run_id,
        "--executor-command", config["executor"],
        "--role-counts", runner_role_counts(intent),
        "--max-rounds", "30",
        "--idle-rounds", "2",
        "--json",
    ]
    log_fh = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=config["swarm_repo"],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=dict(os.environ),
        )
    except BaseException:
        log_fh.close()
        raise
    log_fh.close()
    return proc.pid


def content_job_path(config: Dict[str, Any], run_id: str) -> Path:
    return Path(config["content_job_dir"]) / run_id


def launch_content_job(
    config: Dict[str, Any],
    run_id: str,
    *,
    route: str = "",
    message: str = "",
    session_id: str = "",
    platform: str = "",
) -> int:
    job_dir = content_job_path(config, run_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    request_path = job_dir / "request.json"
    if route and message:
        locked_atomic_write_text(
            request_path,
            json.dumps({
                "run_id": run_id,
                "route": route,
                "message": message,
                "session_id": session_id,
                "platform": platform,
                "created_at": utc_now(),
            }, ensure_ascii=False, indent=2),
        )
    if not request_path.exists():
        raise RuntimeError(f"content job request missing: {request_path}")

    log_path = job_dir / "executor.log"
    log_fh = log_path.open("a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [sys.executable, config["content_executor"], "--job-dir", str(job_dir)],
            cwd=str(HERE.parent),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=dict(os.environ),
        )
    except BaseException:
        log_fh.close()
        raise
    log_fh.close()
    return proc.pid


def select_company_result(result: Dict[str, Any]) -> str:
    """Prefer the latest completed worker result over reporter-first ordering.

    The swarm client intentionally favors reporter tasks for generic callers,
    but company routing must account for a later analyst correcting an earlier
    synthesis. Diff previews are self-reports from an isolated worker and are
    lower priority than a direct evidence-backed conclusion.
    """
    candidates = []
    for task in result.get("task_results") or []:
        if task.get("status") != "completed":
            continue
        summary = task.get("result_summary") or {}
        content = ""
        if isinstance(summary, dict):
            for key in ("content", "summary", "result", "output"):
                if summary.get(key):
                    content = str(summary[key])
                    break
        if not content:
            continue
        is_diff_preview = content.lstrip().startswith("┊ review diff")
        candidates.append((str(task.get("ended_at") or ""), not is_diff_preview, content))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]
    return str(result.get("result") or result.get("summary") or "")


def refresh_session_runs(config: Dict[str, Any], state: RouterState, session_id: str) -> list[str]:
    updates: list[str] = []
    for row in state.active_for_session(session_id):
        run_id = row["run_id"]
        try:
            result = swarm_command(config, "task", "result", "--run-id", run_id, timeout=15)
        except Exception as exc:
            updates.append(f"- 蜂群 {run_id[:8]} 状态查询失败：{exc}")
            continue
        status = result.get("status", "unknown")
        state.update(row["route_event_id"], status=status)
        if status == "completed" and not row["result_delivered"]:
            limit = int(config.get("result_context_chars", 6000))
            content = select_company_result(result)[:limit]
            updates.append(f"- 蜂群 {run_id[:8]} 已完成。结果：\n{content}")
            state.update(row["route_event_id"], result_delivered=1)
            # Classify output quality for security runs
            if not row.get("quality_status"):
                try:
                    from .operations_control import _classify_security_findings
                except ImportError:
                    from operations_control import _classify_security_findings
                try:
                    quality = _classify_security_findings(
                        run_id,
                        swarm_db=Path(config.get("swarm_db", "")),
                        log_dir=Path(config.get("log_dir", "")),
                    )
                    state.update(row["route_event_id"], quality_status=quality)
                except Exception:
                    # Best-effort quality classification — must not block result notification
                    pass
        elif status in {"running", "submitted"}:
            task_counts = result.get("tasks") or {}
            updates.append(f"- 蜂群 {run_id[:8]} 正在运行，任务状态：{json.dumps(task_counts, ensure_ascii=False)}")
        elif status in {"failed", "cancelled"}:
            updates.append(f"- 蜂群 {run_id[:8]} 状态为 {status}，需要主 Agent 检查日志。")
    return updates


def refresh_session_content_jobs(config: Dict[str, Any], state: RouterState, session_id: str) -> list[str]:
    updates: list[str] = []
    for action, label in (
        ("dispatch_article", "文章产线"),
        ("dispatch_video", "视频产线"),
        ("dispatch_company", "公司执行 Worker"),
    ):
        for row in state.active_for_session(session_id, action=action):
            status_path = content_job_path(config, row["run_id"]) / "status.json"
            if not status_path.exists():
                updates.append(f"- {label}任务 {row['run_id'][:8]} 正在启动。")
                continue
            try:
                payload = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                updates.append(f"- {label}任务 {row['run_id'][:8]} 状态读取失败：{exc}")
                continue
            status = str(payload.get("status") or "unknown")
            state.update(row["route_event_id"], status=status)
            if status == "completed" and not row["result_delivered"]:
                result = str(payload.get("result") or "任务已完成。")
                artifacts = payload.get("artifacts") or []
                artifact_text = "、".join(str(item) for item in artifacts)
                suffix = f"\n产物：{artifact_text}" if artifact_text else ""
                updates.append(f"- {label}任务 {row['run_id'][:8]} 已完成：\n{result[:4000]}{suffix}")
                state.update(row["route_event_id"], result_delivered=1)
            elif status == "running":
                updates.append(f"- {label}任务 {row['run_id'][:8]} 正在运行。")
            elif status == "needs_approval":
                updates.append(f"- {label}任务 {row['run_id'][:8]} 已完成内部准备，等待人工审批：{payload.get('result') or '请查看审批请求。'}")
                state.update(row["route_event_id"], result_delivered=1)
            elif status in {"failed", "cancelled"}:
                updates.append(f"- {label}任务 {row['run_id'][:8]} 状态为 {status}：{payload.get('error') or '请检查日志。'}")
    return updates


def build_context(
    decision: RouteDecision,
    *,
    run: Optional[Dict[str, Any]] = None,
    existing_run_id: str = "",
    existing_status: str = "",
    status_updates: Optional[list[str]] = None,
) -> str:
    lines = [
        "[公司 Router 私有上下文]",
        f"产品线路由：{decision.route}；动作：{decision.action}；置信度：{decision.confidence:.2f}。",
    ]
    if status_updates:
        lines.append("本会话已有后台任务更新：")
        lines.extend(status_updates)

    if decision.action == "approval_required":
        if decision.authorization_required:
            lines.append(
                "该请求涉及主动安全测试，但消息中缺少足够的授权/Scope 证明。"
                "不要执行或自动分发；请主 Agent 向用户确认授权目标和范围。"
            )
        else:
            lines.append(
                "该请求涉及发布、上传、付款、删除或其他外部动作，必须人工审批。"
                "可以说明待审批内容，但不要自动执行或分发外部动作。"
            )
    elif decision.action == "dispatch_swarm":
        run_id = (run or {}).get("run_id") or existing_run_id
        if run_id:
            run_status = str((run or {}).get("status") or existing_status)
            completed = run_status == "completed" or any("已完成" in update for update in (status_updates or []))
            if completed:
                lines.append(
                    f"该任务对应的安全蜂群 run_id={run_id} 已完成。"
                    "请主 Agent 使用本轮注入结果或会话中此前已交付的结果回答，不要重新提交。"
                )
            else:
                lines.append(f"任务已自动提交至安全蜂群，run_id={run_id}。不要在主 Agent 中重复执行同一任务。")
                lines.append("请向用户简短确认已分发；后续回合 Router 会注入运行状态或最终结果。")
        else:
            lines.append("任务应提交至安全蜂群，但当前未获得 run_id；主 Agent 应报告路由失败。")
    elif decision.action in {"dispatch_article", "dispatch_video", "dispatch_company"}:
        run_id = (run or {}).get("run_id") or existing_run_id
        run_status = str((run or {}).get("status") or existing_status)
        destination = {
            "dispatch_article": "文章产线",
            "dispatch_video": "视频产线",
            "dispatch_company": "公司执行 Worker",
        }[decision.action]
        if run_id and run_status == "completed":
            lines.append(f"{destination}任务 run_id={run_id} 已完成。请使用本轮或此前注入的产物回答，不要重复提交。")
        elif run_id:
            lines.append(f"任务已自动分发至{destination}，run_id={run_id}。不要在主 Agent 中重复执行。")
            lines.append("外部动作和不可逆操作仍需人工审批；后台完成后会主动回传原会话。")
        else:
            lines.append(f"任务应提交至{destination}，但当前未获得 run_id；主 Agent 应报告路由失败。")
    else:
        lines.append("该请求由公司主 Agent 处理，并以公司 Wiki 为事实来源。")
    return "\n".join(lines)


def handle_tvcr_decision(message: str, config: Dict[str, Any], actor: str) -> Optional[str]:
    """Apply an explicit user decision to a pending operating proposal."""
    operations_db = str(config.get("operations_db") or "").strip()
    if not operations_db:
        return None
    try:
        try:
            from .operations_control import apply_user_decision
        except ImportError:
            from operations_control import apply_user_decision
        result = apply_user_decision(Path(operations_db), message, actor=actor)
    except Exception as exc:
        return f"[TVCR 审批上下文]\n审批处理失败：{exc}。不要实施任何修改，请向用户报告失败。"
    if result is None:
        return None
    if not result.get("ok"):
        return f"[TVCR 审批上下文]\n{result.get('message') or '审批未生效。'}不要实施任何修改。"
    if result.get("decision") == "rejected":
        return (
            "[TVCR 审批上下文]\n"
            f"用户已拒绝经营提案 {result['proposal_id']}（{result['proposal_title']}）。"
            "不要实施该提案；向用户确认已记录决定即可。"
        )
    return "\n".join([
        "[TVCR 审批上下文]",
        f"用户已批准经营提案 {result['proposal_id']}（{result['proposal_title']}）。",
        f"已创建运营实验 {result['experiment_id']}，当前状态为 planned。",
        f"批准的经营动作：{result['recommended_action']}",
        f"可能涉及层级：{json.dumps(result.get('change_scopes') or [], ensure_ascii=False)}",
        f"成功指标：{json.dumps(result.get('success_metrics') or [], ensure_ascii=False)}",
        "主 Agent 应开始落实运营实验：先处理业务/产品/流程/资源决策，再判断 Prompt、配置或代码是否需要改变。",
        f"实施前运行：python3 /home/pwn/workspace/company/automation/operations_control.py experiment {result['experiment_id']} running",
        f"完成安全验证后运行：python3 /home/pwn/workspace/company/automation/operations_control.py experiment {result['experiment_id']} evaluating",
        "实施前将实验标记 running；完成安全验证后标记 evaluating。不得把“已批准经营实验”解释为任意外部发布或付款授权。",
    ])


def _session_has_meaningful_content(
    session_id: str,
    *,
    hermes_db_path: Path = HERMES_STATE_DB,
    min_user_messages: int = PRE_EVAL_MIN_PRIOR_USER_MESSAGES,
    min_total_messages: int = PRE_EVAL_MIN_PRIOR_MESSAGES,
) -> bool:
    """Check whether a session has enough prior conversation to justify analysis.

    Returns True only if the session contains at least *min_user_messages*
    user messages AND *min_total_messages* user+assistant messages beyond
    the current dispatch request itself.
    """
    if not hermes_db_path.is_file():
        return False
    try:
        db = sqlite3.connect(hermes_db_path)
        db.row_factory = sqlite3.Row
        try:
            user_count = db.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE session_id=? AND role='user'",
                (session_id,),
            ).fetchone()
            total_count = db.execute(
                "SELECT COUNT(*) AS c FROM messages "
                "WHERE session_id=? AND role IN ('user','assistant')",
                (session_id,),
            ).fetchone()
            if not user_count or not total_count:
                return False
            # The current dispatch message may already be stored; be conservative
            # and require at least 1 extra user message (meaning at least 2 total).
            return int(user_count["c"]) >= (min_user_messages + 1) and int(total_count["c"]) >= min_total_messages
        finally:
            db.close()
    except sqlite3.Error:
        return False


def _pre_evaluate_task(
    decision: RouteDecision,
    message: str,
    session_id: str,
    config: Dict[str, Any],
) -> str:
    """Determines if a task should proceed, be skipped, or require approval BEFORE dispatch.

    Returns one of: 'proceed', 'skip', 'needs_approval'
    """
    pre_eval_enabled = bool(config.get("pre_evaluation_enabled", True))
    if not pre_eval_enabled:
        return "proceed"

    # Status notifications: messages that are purely reporting status/progress,
    # NOT requesting security analysis. These should never dispatch swarm.
    lowered = message.lower()
    status_markers = ("已发表", "已发布", "已完成", "已推送", "已更新",
                      "以下文章", "以下是",
                      "[async delegation", "[importan")
    if decision.action == "dispatch_swarm":
        if any(marker in lowered for marker in status_markers):
            return "skip"

    # Conversation vs. task disambiguation for security-classified messages.
    # A message that was keyword-scored as security but reads like a conversation
    # (long text, meta-discourse, past-tense references) should not dispatch swarm.
    # Real security tasks are short, imperative, and reference a specific target.
    # Conversational signals — skip if strong indicators present
    # Common to all actions
    conversation_markers = (
        "我认为", "我觉得", "你可以", "能不能", "是不是", "应该是",
        "我认为是", "废弃", "如果", "可以寻找", "我来", "我想",
    )
    if decision.action in {"dispatch_swarm", "dispatch_company"}:
        conv_hits = sum(1 for m in conversation_markers if m in lowered)
        if conv_hits >= 2:
            return "skip"
        # Long meandering text (3+ sentences without a clear target) is likely chat
        sentence_count = len(re.split(r'[。！？\\n]', message))
        words = len(message.split())
        if sentence_count >= 3 and words >= 15 and conv_hits >= 1:
            return "skip"

    # Company-specific disambiguation: "给我一个方案", "建议", "你觉得怎么样"
    # are requests for the main agent to design/propose, not execution commands.
    if decision.action == "dispatch_company":
        company_conv_markers = (
            "给我", "给我一个", "给我一份", "给我列出", "给我总结", "给我说",
            "你觉得", "你有什么建议", "有什么想法", "方案", "建议",
        )
        if any(m in lowered for m in company_conv_markers):
            return "skip"

    # Confidence gate: baseline confidence (0.45, no terms matched) should
    # never trigger an auto-dispatch — the classification was a fallback.
    # Fall back to main_agent so the conversation continues naturally.
    if decision.confidence < 0.5 and decision.action in {"dispatch_company", "dispatch_article", "dispatch_video"}:
        return "skip"

    # Skill review: check for actual conversation content to review
    if decision.action == "dispatch_company" and SKILL_REVIEW_MARKER in message.lower():
        min_messages = int(config.get("pre_eval_skill_review_min_messages", PRE_EVAL_MIN_PRIOR_MESSAGES))
        min_user = int(config.get("pre_eval_skill_review_min_user_messages", PRE_EVAL_MIN_PRIOR_USER_MESSAGES))
        hermes_db = Path(str(config.get("hermes_state_db", HERMES_STATE_DB)) or HERMES_STATE_DB)
        if not _session_has_meaningful_content(
            session_id,
            hermes_db_path=hermes_db,
            min_user_messages=min_user,
            min_total_messages=min_messages,
        ):
            return "skip"

    return "proceed"


def handle_hook(payload: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, str]:
    if not config.get("enabled", True):
        return {}

    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    message = str(extra.get("user_message") or "").strip()
    if not message:
        return {}
    if os.getenv("COMPANY_ROUTER_BYPASS") == "1":
        return {}
    if INTERNAL_MESSAGE_PREFIX_RE.match(message):
        if _is_internal_hermes_hook(payload, extra):
            return {}
        message = _strip_internal_message_prefixes(message)
        if not message:
            return {}
    session_id = str(payload.get("session_id") or "unknown-session")
    platform = str(extra.get("platform") or "unknown")
    decision_context = handle_tvcr_decision(message, config, actor=f"{platform}:{session_id}")
    if decision_context is not None:
        return {"context": decision_context}
    message_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    state = RouterState(config["state_db"])
    updates = refresh_session_runs(config, state, session_id)
    updates.extend(refresh_session_content_jobs(config, state, session_id))

    existing = state.existing(session_id, message_hash)
    if existing:
        decision = RouteDecision(**json.loads(existing["decision_json"]))
        return {"context": build_context(
            decision,
            existing_run_id=existing["run_id"],
            existing_status=existing["status"],
            status_updates=updates,
        )}

    targets = extract_target(message)
    decision = classify_message(message, config.get("authorized_targets") or [])
    dedup_key = _build_dedup_key(session_id, targets, decision.intent)
    if decision.action == "dispatch_swarm" and not EXPLICIT_NEW_SWARM_RE.search(message):
        dedup_minutes = int(config.get("swarm_dedup_window_minutes", 10))
        recent = state.recent_for_session(
            session_id,
            decision.action,
            datetime.now(timezone.utc) - timedelta(minutes=dedup_minutes),
            dedup_key=dedup_key,
        )
        if recent:
            state.update(
                recent["route_event_id"],
                delivery_attempts=int(recent["delivery_attempts"] or 0) + 1,
            )
            return {"context": build_context(
                RouteDecision(**json.loads(recent["decision_json"])),
                existing_run_id=recent["run_id"],
                existing_status=recent["status"],
                status_updates=updates,
            )}

    is_skill_review = decision.action == "dispatch_company" and SKILL_REVIEW_MARKER in message.lower()
    if is_skill_review:
        cooldown_hours = int(config.get("content_job_skill_review_cooldown_hours", 4))
        recent = state.recent_for_session(
            session_id,
            decision.action,
            datetime.now(timezone.utc) - timedelta(hours=cooldown_hours),
            completed_only=True,
            message_marker=SKILL_REVIEW_MARKER,
        )
        if recent:
            state.update(
                recent["route_event_id"],
                delivery_attempts=int(recent["delivery_attempts"] or 0) + 1,
            )
            return {"context": build_context(
                RouteDecision(**json.loads(recent["decision_json"])),
                existing_run_id=recent["run_id"],
                existing_status=recent["status"],
                status_updates=updates,
            )}

    origin = resolve_session_origin(str(config.get("gateway_sessions_index") or ""), session_id)
    event_id = state.insert(
        session_id,
        platform,
        message_hash,
        message,
        decision,
        origin=origin,
        dedup_key=dedup_key,
    )
    run: Optional[Dict[str, Any]] = None

    # Pre-evaluation gate: check task value BEFORE dispatch
    pre_eval = _pre_evaluate_task(decision, message, session_id, config)
    if pre_eval == "skip":
        state.update(event_id, status="skipped", error="pre-evaluation: no meaningful work to perform")
        return {"context": build_context(
            decision,
            status_updates=updates + [
                f"- [预评估] 任务已跳过：当前会话无足够的对话历史可供分析。任务未派发，无 Token 消耗。"
            ],
        )}
    elif pre_eval == "needs_approval":
        state.update(event_id, status="waiting_approval")
        return {"context": build_context(
            RouteDecision(**{**asdict(decision), "action": "approval_required",
                           "reason": "pre-evaluation: task requires user review before execution"}),
            status_updates=updates + [
                "- [预评估] 此任务被标记为需要用户审批，已暂停派发。"
            ],
        )}

    if decision.action == "dispatch_swarm" and config.get("dispatch_security", True):
        active = [row for row in state.active_for_session(session_id) if row["status"] in {"submitted", "running"}]
        if len(active) >= int(config.get("max_active_runs_per_session", 2)):
            state.update(event_id, status="deferred", error="active run limit reached")
            return {"context": build_context(
                RouteDecision(**{**asdict(decision), "action": "main_agent", "reason": "active run limit reached"}),
                status_updates=updates + ["- 已达到本会话并发蜂群上限，新任务暂未提交。"],
            )}
        try:
            run = submit_security(config, session_id, platform, message, decision)
            fields: Dict[str, Any] = {
                "run_id": str(run.get("run_id") or ""),
                "request_id": str(run.get("request_id") or ""),
                "status": "submitted",
            }
            if config.get("auto_run_security", True) and fields["run_id"]:
                fields["runner_pid"] = launch_runner(config, fields["run_id"], decision.intent)
                fields["status"] = "running"
            state.update(event_id, **fields)
        except Exception as exc:
            state.update(event_id, status="failed", error=str(exc))
            updates.append(f"- 自动提交失败：{exc}")
    elif decision.action in {"dispatch_article", "dispatch_video", "dispatch_company"}:
        enabled_key = {
            "dispatch_article": "auto_run_article",
            "dispatch_video": "auto_run_video",
            "dispatch_company": "auto_run_company",
        }[decision.action]
        if config.get(enabled_key, True):
            active = state.active_for_session(session_id, action=decision.action)
            running = [row for row in active if row["status"] in {"submitted", "running"}]
            if len(running) >= int(config.get("max_active_content_jobs_per_session", 2)):
                state.update(event_id, status="deferred", error="active content job limit reached")
                updates.append("- 已达到本会话内容产线并发上限，新任务暂未提交。")
            else:
                try:
                    run_id = str(uuid.uuid4())
                    pid = launch_content_job(
                        config,
                        run_id,
                        route=decision.route,
                        message=message,
                        session_id=session_id,
                        platform=platform,
                    )
                    run = {"run_id": run_id, "status": "running"}
                    state.update(event_id, run_id=run_id, runner_pid=pid, status="running")
                except Exception as exc:
                    state.update(event_id, status="failed", error=str(exc))
                    updates.append(f"- 内容产线自动提交失败：{exc}")

    return {"context": build_context(decision, run=run, status_updates=updates)}


def parse_hook_stdin() -> Dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("hook payload must be a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Company product-line router")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--hook", action="store_true", help="Read Hermes hook JSON from stdin")
    parser.add_argument("--message", default="", help="Classify one message")
    parser.add_argument("--session-id", default="cli-test")
    parser.add_argument("--platform", default="cli")
    parser.add_argument("--dispatch", action="store_true", help="Submit eligible security message")
    args = parser.parse_args()
    config = load_config(Path(args.config))

    if args.hook:
        print(json.dumps(handle_hook(parse_hook_stdin(), config), ensure_ascii=False))
        return 0

    decision = classify_message(args.message, config.get("authorized_targets") or [])
    if not args.dispatch:
        print(json.dumps(asdict(decision), ensure_ascii=False, indent=2))
        return 0

    payload = {
        "session_id": args.session_id,
        "extra": {"user_message": args.message, "platform": args.platform},
    }
    print(json.dumps(handle_hook(payload, config), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
