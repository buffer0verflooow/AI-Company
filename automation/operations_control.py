#!/usr/bin/env python3
"""Company operating ledger and TVCR governance state machine.

This module deliberately separates business decisions from implementation.
Technical runs are evidence.  A TVCR proposal must be approved before it can
become an operating experiment, and approval does not imply that code is the
right implementation mechanism.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

try:
    from ._safe_io import file_lock, quote_identifier
except ImportError:  # direct ``python automation/operations_control.py`` invocation
    from _safe_io import file_lock, quote_identifier

try:
    from . import pricing
    from .codex_usage import DEFAULT_CODEX_SESSIONS, find_codex_usage
    from .claude_usage import DEFAULT_CLAUDE_PROJECTS, find_claude_usage
except ImportError:  # direct ``python automation/operations_control.py`` invocation
    import pricing  # type: ignore[no-redef]
    from codex_usage import DEFAULT_CODEX_SESSIONS, find_codex_usage
    from claude_usage import DEFAULT_CLAUDE_PROJECTS, find_claude_usage


COMPANY_ROOT = Path("/home/pwn/workspace/company")
DEFAULT_DB = COMPANY_ROOT / "operations/runtime/operations_control.db"
DEFAULT_ROUTER_DB = COMPANY_ROOT / "operations/runtime/company_router.db"
DEFAULT_CONTENT_JOBS = COMPANY_ROOT / "operations/runtime/content-jobs"
DEFAULT_HERMES_DB = Path("/home/pwn/.hermes/state.db")
DEFAULT_FINANCE_DB = COMPANY_ROOT / "finance/finance_ledger.db"
DEFAULT_ARTICLE_PERF_DB = COMPANY_ROOT / "marketing/article_performance.db"
DEFAULT_REVIEW_ROOT = COMPANY_ROOT / "operations/runtime/tvcr-reviews"
DEFAULT_SWARM_DB = Path("/home/pwn/workspace/research/swarm-knowledge/swarm_knowledge.db")
DEFAULT_LOG_DIR = COMPANY_ROOT / "operations/runtime/logs"
DEFAULT_TIMEZONE = "Asia/Shanghai"

APPROVE_RE = re.compile(r"(?<!不)(?:批准|同意|确认执行|采纳|通过)", re.I)
REJECT_RE = re.compile(r"(?:拒绝|不批准|不采纳|驳回|暂不执行)", re.I)
PROPOSAL_ID_RE = re.compile(r"TVCR-P-\d{8}-\d{2}", re.I)
ITEM_NO_RE = re.compile(r"第?\s*(\d{1,2})\s*(?:项|条)")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db: Optional[sqlite3.Connection] = None
    try:
        # Protect schema creation/migrations; normal transactions still use
        # SQLite's WAL/busy-timeout for concurrent readers and writers.
        with file_lock(path):
            db = sqlite3.connect(path, timeout=5.0)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=5000")
            db.executescript(
                """
        CREATE TABLE IF NOT EXISTS operational_runs (
            run_id TEXT PRIMARY KEY,
            route_event_id TEXT DEFAULT '',
            product_line TEXT NOT NULL,
            source_type TEXT NOT NULL,
            request_text TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'unknown',
            origin_platform TEXT DEFAULT '',
            origin_chat_id TEXT DEFAULT '',
            result_delivered INTEGER NOT NULL DEFAULT 0,
            proactive_delivered INTEGER NOT NULL DEFAULT 0,
            started_at TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            duration_seconds REAL,
            worker_session_id TEXT DEFAULT '',
            model TEXT DEFAULT '',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            tool_call_count INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL,
            actual_cost_usd REAL,
            cost_status TEXT DEFAULT 'unknown',
            estimated_cost_native REAL,
            estimated_cost_currency TEXT NOT NULL DEFAULT '',
            artifacts_json TEXT NOT NULL DEFAULT '[]',
            output_bytes INTEGER NOT NULL DEFAULT 0,
            quality_status TEXT DEFAULT 'unmeasured',
            outcome_status TEXT NOT NULL DEFAULT 'unmeasured',
            accepted INTEGER,
            published INTEGER,
            value_score REAL,
            human_minutes REAL,
            reach INTEGER,
            revenue_amount REAL,
            revenue_currency TEXT DEFAULT '',
            outcome_notes TEXT DEFAULT '',
            evidence_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_operational_runs_period
        ON operational_runs(product_line, completed_at);

        CREATE TABLE IF NOT EXISTS tvcr_reviews (
            review_id TEXT PRIMARY KEY,
            review_date TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            product_line TEXT NOT NULL DEFAULT 'company',
            status TEXT NOT NULL DEFAULT 'collecting',
            evidence_path TEXT DEFAULT '',
            report_path TEXT DEFAULT '',
            executive_summary TEXT DEFAULT '',
            error TEXT DEFAULT '',
            delivery_platform TEXT DEFAULT '',
            delivery_chat_id TEXT DEFAULT '',
            delivery_thread_id TEXT DEFAULT '',
            delivery_user_id TEXT DEFAULT '',
            delivered INTEGER NOT NULL DEFAULT 0,
            delivery_attempts INTEGER NOT NULL DEFAULT 0,
            delivery_error TEXT DEFAULT '',
            last_delivery_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(period_start, period_end, product_line)
        );

        CREATE TABLE IF NOT EXISTS improvement_proposals (
            proposal_id TEXT PRIMARY KEY,
            review_id TEXT NOT NULL REFERENCES tvcr_reviews(review_id),
            item_no INTEGER NOT NULL,
            product_line TEXT NOT NULL DEFAULT 'company',
            priority TEXT NOT NULL DEFAULT 'P2',
            title TEXT NOT NULL,
            problem_statement TEXT NOT NULL,
            business_impact TEXT DEFAULT '',
            root_cause_hypotheses_json TEXT NOT NULL DEFAULT '[]',
            options_json TEXT NOT NULL DEFAULT '[]',
            recommended_action TEXT NOT NULL,
            change_scopes_json TEXT NOT NULL DEFAULT '[]',
            expected_value TEXT DEFAULT '',
            expected_cost TEXT DEFAULT '',
            risk TEXT DEFAULT '',
            success_metrics_json TEXT NOT NULL DEFAULT '[]',
            evidence_run_ids_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'pending_approval',
            decided_by TEXT DEFAULT '',
            decided_at TEXT DEFAULT '',
            decision_note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(review_id, item_no)
        );
        CREATE INDEX IF NOT EXISTS idx_improvement_proposals_status
        ON improvement_proposals(status, created_at);

        CREATE TABLE IF NOT EXISTS operating_experiments (
            experiment_id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL UNIQUE REFERENCES improvement_proposals(proposal_id),
            product_line TEXT NOT NULL,
            name TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            baseline_json TEXT NOT NULL DEFAULT '{}',
            targets_json TEXT NOT NULL DEFAULT '[]',
            implementation_plan_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'planned',
            owner TEXT DEFAULT 'company-main-agent',
            started_at TEXT DEFAULT '',
            evaluation_due_at TEXT DEFAULT '',
            ended_at TEXT DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            conclusion TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
            )
            proposal_columns = {row[1] for row in db.execute("PRAGMA table_info(improvement_proposals)")}
            if "product_line" not in proposal_columns:
                db.execute("ALTER TABLE improvement_proposals ADD COLUMN product_line TEXT NOT NULL DEFAULT 'company'")
            if "escalated_at" not in proposal_columns:
                db.execute("ALTER TABLE improvement_proposals ADD COLUMN escalated_at TEXT NOT NULL DEFAULT ''")
            run_columns = {row[1] for row in db.execute("PRAGMA table_info(operational_runs)")}
            migrations = {
                "result_delivered": "INTEGER NOT NULL DEFAULT 0",
                "proactive_delivered": "INTEGER NOT NULL DEFAULT 0",
                "estimated_cost_native": "REAL",
                "estimated_cost_currency": "TEXT NOT NULL DEFAULT ''",
            }
            for column, definition in migrations.items():
                if column not in run_columns:
                    safe_column = quote_identifier(column, allowed=migrations)
                    db.execute(f"ALTER TABLE operational_runs ADD COLUMN {safe_column} {definition}")
            db.commit()
        return db
    except BaseException:
        if db is not None:
            db.close()
        raise


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sql_assignments(fields: Dict[str, Any], allowed: set[str]) -> str:
    """Return a parameterized ``SET`` clause for a trusted column allow-list."""

    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unsupported SQL columns: {sorted(unknown)!r}")
    return ",".join(f"{quote_identifier(key, allowed=allowed)}=?" for key in fields)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def business_period(day: date, timezone_name: str = DEFAULT_TIMEZONE) -> tuple[str, str]:
    zone = ZoneInfo(timezone_name)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc).isoformat(timespec="seconds"), end.astimezone(timezone.utc).isoformat(timespec="seconds")


def previous_business_day(timezone_name: str = DEFAULT_TIMEZONE) -> date:
    return datetime.now(ZoneInfo(timezone_name)).date() - timedelta(days=1)


def _find_worker_session(hermes_db: Path, job_dir: Path) -> Dict[str, Any]:
    if not hermes_db.is_file():
        return {}
    db = sqlite3.connect(hermes_db)
    db.row_factory = sqlite3.Row
    try:
        session = db.execute(
            """SELECT s.* FROM messages m JOIN sessions s ON s.id=m.session_id
               WHERE m.role='user' AND m.content LIKE ?
               ORDER BY m.timestamp DESC LIMIT 1""",
            (f"%产物目录：{job_dir}%",),
        ).fetchone()
        return dict(session) if session else {}
    except sqlite3.Error:
        return {}
    finally:
        db.close()


def _classify_security_findings(
    run_id: str,
    swarm_db: Path = DEFAULT_SWARM_DB,
    log_dir: Path = DEFAULT_LOG_DIR,
    min_finding_tokens: int = 200,
    no_finding_patterns: Optional[list[str]] = None,
) -> str:
    """Classify a security run's output quality without modifying existing records.

    Examines the swarm runner log and swarm DB task result summaries for
    indicators of actual findings vs. wasted-token scenarios (no findings,
    target unreachable, timeouts, empty output).

    Args:
        run_id: The swarm run UUID.
        swarm_db: Path to the swarm knowledge SQLite database.
        log_dir: Directory containing swarm-{run_id}.log runner output.
        min_finding_tokens: Minimum output length (in characters, used as a
            proxy for tokens) before a "no findings" verdict is trusted.
        no_finding_patterns: List of substrings that indicate the run produced
            no actionable findings. If None, uses the built-in default list.

    Returns:
        One of:
            "actionable"       — output contains evidence of actual findings.
            "no_business_value" — output matches no-finding patterns (wasted
                                  tokens) but has meaningful length.
            "empty_output"     — no output, trivially short, or unreadable.
    """
    if no_finding_patterns is None:
        no_finding_patterns = [
            "no findings", "no vulnerabilities", "target unreachable",
            "无漏洞", "无法访问", "未发现漏洞", "没有发现漏洞",
            "没有发现安全问题", "0 findings", "connection refused",
            "connection timeout", "connection reset", "404 not found",
            "403 forbidden", "access denied", "empty result",
            "nothing found", "no issues found", "no results",
            "请求超时", "连接超时", "连接失败", "请求失败",
        ]

    # ── 1. Collect all available output text ──────────────────────────
    fragments: list[str] = []

    # Runner log
    log_file = log_dir / f"swarm-{run_id}.log"
    if log_file.is_file():
        try:
            fragments.append(log_file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass

    # Swarm DB — agent_tasks result_summary
    if swarm_db.is_file():
        try:
            conn = sqlite3.connect(str(swarm_db))
            conn.row_factory = sqlite3.Row
            try:
                for row in conn.execute(
                    "SELECT task_type, result_summary FROM agent_tasks "
                    "WHERE run_id=? AND status='completed' "
                    "AND result_summary IS NOT NULL AND result_summary!='{}'",
                    (run_id,),
                ):
                    summary = row["result_summary"]
                    if summary:
                        fragments.append(summary)
                # Also grab swarm_runs.conversation_summary
                row = conn.execute(
                    "SELECT conversation_summary FROM swarm_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if row and row[0]:
                    fragments.append(row[0])
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    combined = "\n".join(fragments)

    # ── 2. Trivially empty → "empty_output" ──────────────────────────
    if not combined.strip():
        return "empty_output"

    # ── 3. Check for evidence of actual findings — actionable ─────────
    #       Scan before no-finding patterns so genuine findings override
    #       "no findings" boilerplate.  The _finding_is_noise helper
    #       prevents false positives like "漏洞" inside "未发现漏洞".
    finding_re = re.compile(
        r"(?:漏洞|vulnerabilit|exploit|poc|风险|不安全|泄漏|泄露|"
        r"注入|越权|绕过|弱口令|敏感信息|信息泄露|未授权|"
        r"xss|csrf|ssrf|sqli|idor|rce|命令执行|文件包含|"
        r"权限提升|敏感文件|备份文件|目录遍历|cors|jwt|"
        r"broken\\s*(?:access\\s*)?control|misconfig|暴露)",
        re.I,
    )

    def _finding_is_noise(finding_span: tuple[int, int]) -> bool:
        """Return True if the finding span lies entirely inside a no-finding pattern."""
        for pat in no_finding_patterns:  # type: ignore[union-attr]
            for noise_m in re.finditer(re.escape(pat), combined, re.I):
                if noise_m.start() <= finding_span[0] and finding_span[1] <= noise_m.end():
                    return True
        return False

    finding_match = finding_re.search(combined)
    if finding_match and not _finding_is_noise(finding_match.span()):
        return "actionable"

    # ── 4. Check for no-finding patterns ──────────────────────────────
    lowered = combined.lower()
    no_finding_hits = sum(
        1 for pat in no_finding_patterns if pat.lower() in lowered
    )
    if no_finding_hits > 0:
        if len(combined) < min_finding_tokens:
            return "empty_output"
        return "no_business_value"

    # ── 5. Finding regex matched but only as noise? Check again ───────
    #       If we got here, any finding_re match was inside a no-finding
    #       pattern.  Treat same as a no-finding-pattern hit.
    if finding_match is not None:
        if len(combined) < min_finding_tokens:
            return "empty_output"
        return "no_business_value"

    # ── 6. Default: if the runner log shows completed tasks with no
    #       finding signal, count as no_business_value.
    try:
        for line in reversed(combined.strip().splitlines()):
            obj = json.loads(line)
            if isinstance(obj, dict) and "task_counts" in obj:
                completed = obj["task_counts"].get("completed", 0)
                if completed > 0:
                    return "no_business_value"
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    return "empty_output"


def _quality_status(route: str, job_dir: Path) -> str:
    if route != "article":
        return "not_assessed"
    qa = job_dir / "qa-report.md"
    if not qa.is_file():
        return "missing_qa"
    try:
        text = qa.read_text(encoding="utf-8")
    except OSError:
        return "qa_unreadable"
    if all(gate in text for gate in ("Gate 1", "Gate 2", "Gate 3")) and "通过" in text:
        return "qa_reported_pass"
    return "qa_report_present"


def _upsert_run(db: sqlite3.Connection, values: Dict[str, Any]) -> None:
    columns = list(values)
    safe_columns = [quote_identifier(key) for key in columns]
    placeholders = ",".join("?" for _ in columns)
    updates = ",".join(
        f"{quote_identifier(key)}=excluded.{quote_identifier(key)}"
        for key in columns if key not in {"run_id", "created_at"}
    )
    db.execute(
        f"INSERT INTO operational_runs ({','.join(safe_columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(run_id) DO UPDATE SET {updates}",
        [values[column] for column in columns],
    )


# Usage fields sourced from the shared worker session, not from the individual
# run.  When several runs reuse one Codex/Claude session each of them records the
# session's *whole* usage, so a naive sum counts that one session N times.
_SESSION_INT_FIELDS = (
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "tool_call_count",
)
_SESSION_FLOAT_FIELDS = ("estimated_cost_usd", "estimated_cost_native", "actual_cost_usd")


def _apportion_shared_sessions(rows: List[Dict[str, Any]]) -> None:
    """Split one worker session's tokens/cost evenly across the runs it served.

    Every run in a shared session initially carries the session's full usage.
    This mutates the rows in place so each run holds an equal share and the
    per-session sum stays whole (integer remainders are handed out one-by-one;
    the last run absorbs float rounding).  Runs with an empty ``worker_session_id``
    or a session used by a single run are left untouched.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        session_id = str(row.get("worker_session_id") or "")
        if session_id:
            groups.setdefault(session_id, []).append(row)
    for session_id, members in groups.items():
        n = len(members)
        if n < 2:
            continue
        for field in _SESSION_INT_FIELDS:
            total = int(members[0].get(field) or 0)
            base, remainder = divmod(total, n)
            for index, row in enumerate(members):
                row[field] = base + (1 if index < remainder else 0)
        for field in _SESSION_FLOAT_FIELDS:
            total = members[0].get(field)
            if total is None:
                continue
            total = float(total)
            per_run = round(total / n, 6)
            allocated = 0.0
            for index, row in enumerate(members):
                if index < n - 1:
                    row[field] = per_run
                    allocated += per_run
                else:
                    row[field] = round(total - allocated, 6)
        for row in members:
            try:
                evidence = json.loads(row.get("evidence_json") or "{}")
                if not isinstance(evidence, dict):
                    evidence = {}
            except json.JSONDecodeError:
                evidence = {}
            evidence["session_apportioned"] = {
                "worker_session_id": session_id, "runs_sharing": n,
            }
            row["evidence_json"] = _json(evidence)


def sync_operational_runs(
    db_path: Path = DEFAULT_DB,
    router_db: Path = DEFAULT_ROUTER_DB,
    content_jobs: Path = DEFAULT_CONTENT_JOBS,
    hermes_db: Path = DEFAULT_HERMES_DB,
    codex_sessions: Path = DEFAULT_CODEX_SESSIONS,
    claude_projects: Path = DEFAULT_CLAUDE_PROJECTS,
    swarm_db: Path = DEFAULT_SWARM_DB,
    log_dir: Path = DEFAULT_LOG_DIR,
    finance_db: Path = DEFAULT_FINANCE_DB,
) -> Dict[str, int]:
    """Import technical run evidence without inventing business outcomes."""
    price_table = pricing.load_price_table(finance_db)
    router_rows: Dict[str, Dict[str, Any]] = {}
    if router_db.is_file():
        source = sqlite3.connect(router_db)
        source.row_factory = sqlite3.Row
        try:
            for row in source.execute("SELECT * FROM route_events WHERE run_id<>''"):
                router_rows[str(row["run_id"])] = dict(row)
        finally:
            source.close()

    job_dirs: Dict[str, Path] = {}
    if content_jobs.is_dir():
        for path in content_jobs.iterdir():
            if path.is_dir() and (path / "request.json").is_file():
                job_dirs[path.name] = path

    all_run_ids = set(router_rows) | set(job_dirs)
    db = connect(db_path)
    counts = {"synced": 0, "content": 0, "security": 0}
    pending_rows: List[Dict[str, Any]] = []
    try:
        now = utc_now()
        for run_id in sorted(all_run_ids):
            router = router_rows.get(run_id, {})
            job_dir = job_dirs.get(run_id)
            request = _read_json(job_dir / "request.json") if job_dir else {}
            status = _read_json(job_dir / "status.json") if job_dir else {}
            route = str(request.get("route") or router.get("route") or "unknown")
            product_line = {
                "article": "article-production",
                "video": "video-production",
                "security": "security-exploration",
            }.get(route, route or "unknown")
            artifacts: list[str] = []
            output_bytes = 0
            if job_dir:
                raw_artifacts = status.get("artifacts") if isinstance(status.get("artifacts"), list) else []
                if raw_artifacts:
                    artifacts = [str(item) for item in raw_artifacts]
                else:
                    artifacts = [str(path) for path in sorted(job_dir.iterdir()) if path.is_file() and path.name not in {"request.json", "status.json", "executor.log"}]
                for item in artifacts:
                    path = Path(item)
                    if not path.is_absolute():
                        path = job_dir / path
                    try:
                        output_bytes += path.stat().st_size
                    except OSError:
                        pass

            worker = _find_worker_session(hermes_db, job_dir) if job_dir else {}
            usage_source = "hermes" if worker else ""
            if not worker:
                reference = str(job_dir) if job_dir else run_id
                native_candidates = [
                    ("codex-session-jsonl", find_codex_usage(reference, codex_sessions)),
                    ("claude-session-jsonl", find_claude_usage(reference, claude_projects)),
                ]
                native_candidates = [(source, item) for source, item in native_candidates if item]
                if native_candidates:
                    usage_source, worker = max(
                        native_candidates,
                        key=lambda pair: Path(str(pair[1]["source_path"])).stat().st_mtime_ns,
                    )
            started_at = str(status.get("started_at") or request.get("created_at") or router.get("created_at") or "")
            completed_at = str(status.get("completed_at") or (router.get("updated_at") if str(status.get("status") or router.get("status")) in {"completed", "needs_approval", "failed", "cancelled"} else "") or "")
            started_dt = _parse_dt(started_at)
            completed_dt = _parse_dt(completed_at)
            duration = (completed_dt - started_dt).total_seconds() if started_dt and completed_dt else None
            evidence = {
                "router_db": str(router_db) if router else "",
                "job_dir": str(job_dir) if job_dir else "",
                "status_path": str(job_dir / "status.json") if job_dir else "",
                "hermes_session_db": str(hermes_db) if usage_source == "hermes" else "",
                "usage_source": usage_source,
                "codex_session_path": str(worker.get("source_path") or ""),
            }
            values = {
                "run_id": run_id,
                "route_event_id": str(router.get("route_event_id") or ""),
                "product_line": product_line,
                "source_type": "content-job" if job_dir else "router-run",
                "request_text": str(request.get("message") or router.get("message_excerpt") or "")[:4000],
                "status": str(status.get("status") or router.get("status") or "unknown"),
                "origin_platform": str(router.get("delivery_platform") or request.get("platform") or router.get("platform") or ""),
                "origin_chat_id": str(router.get("delivery_chat_id") or ""),
                "result_delivered": int(router.get("result_delivered") or 0),
                "proactive_delivered": int(router.get("proactive_delivered") or 0),
                "started_at": started_at,
                "completed_at": completed_at,
                "duration_seconds": duration,
                "worker_session_id": str(worker.get("id") or status.get("worker_session_id") or ""),
                "model": str(worker.get("model") or ""),
                "input_tokens": int(worker.get("input_tokens") or 0),
                "output_tokens": int(worker.get("output_tokens") or 0),
                "cache_read_tokens": int(worker.get("cache_read_tokens") or 0),
                "cache_write_tokens": int(worker.get("cache_write_tokens") or 0),
                "reasoning_tokens": int(worker.get("reasoning_tokens") or 0),
                "tool_call_count": int(worker.get("tool_call_count") or 0),
                "estimated_cost_usd": worker.get("estimated_cost_usd"),
                "actual_cost_usd": worker.get("actual_cost_usd"),
                "cost_status": str(worker.get("cost_status") or "unknown"),
                "artifacts_json": _json(artifacts),
                "output_bytes": output_bytes,
                "quality_status": _classify_security_findings(
                    run_id, swarm_db, log_dir,
                ) if route == "security" else (
                    _quality_status(route, job_dir) if job_dir else "unmeasured"
                ),
                "evidence_json": _json(evidence),
                "created_at": now,
                "updated_at": now,
            }
            if values["input_tokens"] == 0 and values["output_tokens"] == 0 and output_bytes == 0:
                # Keep the execution outcome from the source of truth (for
                # example ``failed``).  Empty output is a quality signal, not
                # a replacement for the run's lifecycle status.
                values["quality_status"] = "empty_output"
            # Join measured tokens to evidence-backed prices.  Never overwrites a
            # provider-confirmed cost; an unmatched model stays explicitly
            # ``unpriced`` (not $0); non-USD keeps its native amount, no FX guess.
            price_update = pricing.price_run_update(
                values["model"], values, price_table,
                existing_cost_status=values["cost_status"],
            )
            if price_update is not None:
                evidence["pricing"] = price_update.pop("_pricing")
                values["estimated_cost_usd"] = price_update["estimated_cost_usd"]
                values["estimated_cost_native"] = price_update["estimated_cost_native"]
                values["estimated_cost_currency"] = price_update["estimated_cost_currency"]
                values["cost_status"] = price_update["cost_status"]
                values["evidence_json"] = _json(evidence)
            else:
                values["estimated_cost_native"] = None
                values["estimated_cost_currency"] = ""
            pending_rows.append(values)
            counts["synced"] += 1
            counts["content" if job_dir else "security"] += 1
        # Dedupe shared worker sessions before persisting so a session reused by
        # several runs is not counted once per run.
        _apportion_shared_sessions(pending_rows)
        for values in pending_rows:
            _upsert_run(db, values)
        db.commit()
    finally:
        db.close()
    return counts


def reprice_runs(
    db_path: Path = DEFAULT_DB,
    finance_db: Path = DEFAULT_FINANCE_DB,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Recompute estimated cost for every stored run from its measured tokens.

    Backfills the price-join onto history so existing runs stop reporting $0.
    Provider-confirmed costs are left untouched.  Returns before/after cost
    rollups and the number of rows changed.
    """
    price_table = pricing.load_price_table(finance_db)
    db = connect(db_path)
    try:
        before = pricing.cost_rollup([dict(r) for r in db.execute("SELECT * FROM operational_runs")])
        rows = db.execute(
            """SELECT run_id, model, input_tokens, output_tokens, cache_read_tokens,
                      cache_write_tokens, cost_status, evidence_json FROM operational_runs"""
        ).fetchall()
        now = utc_now()
        changed = 0
        for row in rows:
            update = pricing.price_run_update(
                row["model"], dict(row), price_table,
                existing_cost_status=row["cost_status"],
            )
            if update is None:
                continue
            try:
                evidence = json.loads(row["evidence_json"] or "{}")
                if not isinstance(evidence, dict):
                    evidence = {}
            except json.JSONDecodeError:
                evidence = {}
            evidence["pricing"] = update.pop("_pricing")
            if not dry_run:
                db.execute(
                    """UPDATE operational_runs SET estimated_cost_usd=?,estimated_cost_native=?,
                       estimated_cost_currency=?,cost_status=?,evidence_json=?,updated_at=? WHERE run_id=?""",
                    (
                        update["estimated_cost_usd"], update["estimated_cost_native"],
                        update["estimated_cost_currency"], update["cost_status"],
                        _json(evidence), now, row["run_id"],
                    ),
                )
            changed += 1
        if not dry_run:
            db.commit()
        after = pricing.cost_rollup([dict(r) for r in db.execute("SELECT * FROM operational_runs")])
        return {"dry_run": dry_run, "runs_repriced": changed, "before": before, "after": after}
    finally:
        db.close()


def cost_report(db_path: Path = DEFAULT_DB) -> Dict[str, Any]:
    """Honest cost rollup over all runs, plus a per-model breakdown."""
    db = connect(db_path)
    try:
        runs = [dict(r) for r in db.execute("SELECT * FROM operational_runs")]
    finally:
        db.close()
    by_model: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        model = str(run.get("model") or "(empty)")
        bucket = by_model.setdefault(model, {"runs": 0, "estimated_cost_usd": 0.0, "cost_status": set()})
        bucket["runs"] += 1
        if run.get("estimated_cost_usd") is not None:
            bucket["estimated_cost_usd"] += float(run["estimated_cost_usd"])
        bucket["cost_status"].add(str(run.get("cost_status") or "unknown"))
    for bucket in by_model.values():
        bucket["estimated_cost_usd"] = round(bucket["estimated_cost_usd"], 6)
        bucket["cost_status"] = sorted(bucket["cost_status"])
    return {"rollup": pricing.cost_rollup(runs), "by_model": by_model}


# Draft variants a content run may leave behind, most-final first.  Humanising
# and formatting can each rewrite the H1, so the published title may live in any
# of them.
_TITLE_ARTIFACT_NAMES = ("draft-humanized.md", "draft-formatted.md", "draft.md")


def _normalize_title(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _run_article_titles(run: Dict[str, Any]) -> list[str]:
    """Every candidate published title of a content run, most-final draft first.

    For each draft artifact (humanised / formatted / plain) two candidates are
    taken: the frontmatter ``title:`` (the exact key ``wechat_push.py`` publishes
    under) and the first ``# `` H1.  Humanising and formatting can each rewrite
    the H1, and the account is published under the frontmatter title, so trying
    both (still by *exact* normalized match) recovers reach the plain-draft H1
    alone would miss.  Nothing is fuzzy-matched or invented.
    """
    try:
        artifacts = json.loads(run.get("artifacts_json") or "[]")
    except json.JSONDecodeError:
        return []
    by_name = {Path(str(a)).name: str(a) for a in artifacts if isinstance(a, str)}
    titles: list[str] = []
    seen: set[str] = set()

    def _add(title: str) -> None:
        key = _normalize_title(title)
        if title and key and key not in seen:
            seen.add(key)
            titles.append(title)

    for name in _TITLE_ARTIFACT_NAMES:
        path = by_name.get(name)
        if not path or not Path(path).is_file():
            continue
        try:
            lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        in_frontmatter = False
        h1 = ""
        fence_seen = 0
        for line in lines[:60]:
            stripped = line.strip()
            if stripped == "---" and fence_seen < 2:
                in_frontmatter = not in_frontmatter
                fence_seen += 1
                continue
            if in_frontmatter and line.startswith("title:"):
                _add(line.split(":", 1)[1].strip())
            elif not in_frontmatter and not h1 and line.startswith("# "):
                h1 = line[2:].strip()
        if h1:
            _add(h1)
    return titles


def _run_article_title(run: Dict[str, Any]) -> str:
    """Best-effort single published title of a content run (first candidate)."""
    titles = _run_article_titles(run)
    return titles[0] if titles else ""


def _load_article_reach(article_perf_db: Path) -> Dict[str, Dict[str, Any]]:
    """Map exact-normalized published title -> reach/publish evidence (read-only).

    Reads both measured tables in ``article_performance.db``:
      * ``article_metrics`` — the per-article snapshot (reads, published_at, platform).
      * ``article_source_metrics`` — per-channel reads; the aggregate ('全部'/total)
        channel is a second, independent publish+reach witness used only for titles
        that ``article_metrics`` does not already carry.
    Only exact normalized titles are indexed; nothing is fuzzy-matched or invented.
    """
    out: Dict[str, Dict[str, Any]] = {}
    try:
        db = sqlite3.connect(f"file:{article_perf_db}?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "article_metrics" in tables:
            cols = {row[1] for row in db.execute("PRAGMA table_info(article_metrics)")}
            if {"title", "reads"}.issubset(cols):
                for row in db.execute("SELECT article_id,title,reads,published_at,platform FROM article_metrics"):
                    key = _normalize_title(row["title"])
                    if key and key not in out:
                        out[key] = {
                            "article_id": str(row["article_id"]),
                            "reads": int(row["reads"] or 0),
                            "published_at": str(row["published_at"] or ""),
                            "platform": str(row["platform"] or ""),
                            "source": "article_metrics",
                        }
        if "article_source_metrics" in tables:
            cols = {row[1] for row in db.execute("PRAGMA table_info(article_source_metrics)")}
            if {"title", "reads", "channel"}.issubset(cols):
                totals: Dict[str, Dict[str, Any]] = {}
                for row in db.execute("SELECT title,channel,reads,published_at FROM article_source_metrics"):
                    key = _normalize_title(row["title"])
                    channel = str(row["channel"] or "").strip()
                    # Skip the exported header row ("内容标题 / 传播渠道 / 发表日期").
                    if not key or channel == "传播渠道" or key == _normalize_title("内容标题"):
                        continue
                    reads = int(row["reads"] or 0)
                    bucket = totals.setdefault(key, {"reads": 0, "published_at": "", "has_total": False})
                    if channel in {"全部", "all", "total", "合计"}:
                        bucket["reads"] = reads
                        bucket["has_total"] = True
                    elif not bucket["has_total"]:
                        bucket["reads"] = max(int(bucket["reads"]), reads)
                    if not bucket["published_at"]:
                        bucket["published_at"] = str(row["published_at"] or "")
                for key, bucket in totals.items():
                    if key not in out:
                        out[key] = {
                            "article_id": "",
                            "reads": int(bucket["reads"]),
                            "published_at": str(bucket["published_at"]),
                            "platform": "",
                            "source": "article_source_metrics",
                        }
    except sqlite3.Error:
        return {}
    finally:
        db.close()
    return out


def _load_revenue_transactions(finance_db: Path) -> list[Dict[str, Any]]:
    """Revenue transactions to attribute to a run via an explicit source_ref key."""
    try:
        db = sqlite3.connect(f"file:{finance_db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    db.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(actual_transactions)")}
        if not {"kind", "source_ref", "amount"}.issubset(cols):
            return []
        return [dict(row) for row in db.execute("SELECT * FROM actual_transactions WHERE kind='revenue'")]
    except sqlite3.Error:
        return []
    finally:
        db.close()


def backfill_outcomes(
    db_path: Path = DEFAULT_DB,
    *,
    finance_db: Path = DEFAULT_FINANCE_DB,
    article_perf_db: Path = DEFAULT_ARTICLE_PERF_DB,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Close the outcome loop from *evidence*, not guesses.

    Attributes a run to a business outcome only via a strong key: a finance
    revenue transaction whose ``source_ref`` names the run, or an exact
    normalized title match against measured article reach (from either
    ``article_metrics`` or the aggregate ``article_source_metrics`` channel).
    Runs with no such evidence stay ``unmeasured`` (never invented).  Never
    overwrites a run that already has a recorded outcome.

    A title that matches a *published* measured article (one carrying a real
    publish date) is recorded as ``published=1`` and ``accepted=1``: publishing
    to the official account is an explicit human adoption act, distinct from a
    result merely delivered to chat.  ``reach`` is the measured read count.
    """
    reach = _load_article_reach(article_perf_db)
    revenue = _load_revenue_transactions(finance_db)
    now = utc_now()
    matched: list[Dict[str, Any]] = []
    by_source: Dict[str, int] = {}
    unresolved = 0
    db = connect(db_path)
    try:
        runs = [dict(row) for row in db.execute(
            "SELECT * FROM operational_runs WHERE outcome_status='unmeasured'"
        )]
        for run in runs:
            run_id = str(run["run_id"])
            fields: Optional[Dict[str, Any]] = None
            source = ""
            for tx in revenue:
                if run_id and run_id in str(tx.get("source_ref") or ""):
                    fields = {
                        "outcome_status": "measured", "accepted": 1, "published": 1,
                        "revenue_amount": float(tx.get("amount") or 0),
                        "revenue_currency": str(tx.get("currency") or ""),
                        "outcome_notes": f"auto-backfill: finance revenue tx {tx.get('transaction_id')}",
                    }
                    source = "finance-revenue"
                    break
            if fields is None and str(run.get("product_line") or "").startswith("article"):
                hit: Optional[Dict[str, Any]] = None
                matched_title = ""
                for title in _run_article_titles(run):
                    candidate = reach.get(_normalize_title(title))
                    if candidate:
                        hit, matched_title = candidate, title
                        break
                if hit:
                    published_at = str(hit.get("published_at") or "").strip()
                    fields = {
                        "outcome_status": "measured", "published": 1, "reach": hit["reads"],
                        "outcome_notes": (
                            f"auto-backfill: {hit.get('source') or 'article_metrics'} "
                            f"{hit.get('article_id') or ''} title='{matched_title}' reads={hit['reads']}"
                        ).replace("  ", " ").strip(),
                    }
                    # Publication to the official account is an explicit adoption
                    # act; a mere reach row with no publish date is not.
                    if published_at:
                        fields["accepted"] = 1
                    source = "article-reach"
            if fields is None:
                unresolved += 1
                continue
            fields["updated_at"] = now
            if not dry_run:
                sql = ",".join(f"{key}=?" for key in fields)
                db.execute(f"UPDATE operational_runs SET {sql} WHERE run_id=?", (*fields.values(), run_id))
            by_source[source] = by_source.get(source, 0) + 1
            matched.append({"run_id": run_id, "source": source,
                            "reach": fields.get("reach"), "revenue_amount": fields.get("revenue_amount")})
        if not dry_run:
            db.commit()
    finally:
        db.close()
    return {"dry_run": dry_run, "backfilled": len(matched), "by_source": by_source,
            "matches": matched, "still_unmeasured": unresolved}


def runs_for_period(db_path: Path, period_start: str, period_end: str) -> list[Dict[str, Any]]:
    db = connect(db_path)
    try:
        rows = db.execute(
            """SELECT * FROM operational_runs
               WHERE COALESCE(NULLIF(completed_at,''), started_at)>=?
                 AND COALESCE(NULLIF(completed_at,''), started_at)<?
               ORDER BY product_line, started_at""",
            (period_start, period_end),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def record_outcome(db_path: Path, run_id: str, **fields: Any) -> None:
    allowed = {
        "outcome_status", "accepted", "published", "value_score", "human_minutes",
        "reach", "revenue_amount", "revenue_currency", "outcome_notes",
    }
    if set(fields) - allowed:
        raise ValueError("unsupported business outcome field")
    if not fields:
        return
    fields["updated_at"] = utc_now()
    db = connect(db_path)
    try:
        sql = ",".join(f"{key}=?" for key in fields)
        cur = db.execute(f"UPDATE operational_runs SET {sql} WHERE run_id=?", (*fields.values(), run_id))
        if cur.rowcount != 1:
            raise ValueError(f"unknown operational run: {run_id}")
        db.commit()
    finally:
        db.close()


def create_review(
    db_path: Path,
    *,
    review_day: date,
    period_start: str,
    period_end: str,
    product_line: str = "company",
    evidence_path: str = "",
    origin: Optional[Dict[str, str]] = None,
) -> str:
    review_id = f"TVCR-R-{review_day.strftime('%Y%m%d')}"
    now = utc_now()
    origin = origin or {}
    db = connect(db_path)
    try:
        db.execute(
            """INSERT INTO tvcr_reviews
               (review_id,review_date,period_start,period_end,product_line,status,evidence_path,
                delivery_platform,delivery_chat_id,delivery_thread_id,delivery_user_id,created_at,updated_at)
               VALUES (?,?,?,?,?,'collecting',?,?,?,?,?,?,?)
               ON CONFLICT(period_start,period_end,product_line) DO UPDATE SET
                 evidence_path=excluded.evidence_path,
                 delivery_platform=CASE WHEN excluded.delivery_platform<>'' THEN excluded.delivery_platform ELSE tvcr_reviews.delivery_platform END,
                 delivery_chat_id=CASE WHEN excluded.delivery_chat_id<>'' THEN excluded.delivery_chat_id ELSE tvcr_reviews.delivery_chat_id END,
                 delivery_thread_id=CASE WHEN excluded.delivery_thread_id<>'' THEN excluded.delivery_thread_id ELSE tvcr_reviews.delivery_thread_id END,
                 delivery_user_id=CASE WHEN excluded.delivery_user_id<>'' THEN excluded.delivery_user_id ELSE tvcr_reviews.delivery_user_id END,
                 updated_at=excluded.updated_at""",
            (
                review_id, review_day.isoformat(), period_start, period_end, product_line,
                evidence_path, str(origin.get("platform") or ""), str(origin.get("chat_id") or ""),
                str(origin.get("thread_id") or ""), str(origin.get("user_id") or ""), now, now,
            ),
        )
        row = db.execute(
            "SELECT review_id FROM tvcr_reviews WHERE period_start=? AND period_end=? AND product_line=?",
            (period_start, period_end, product_line),
        ).fetchone()
        db.commit()
        return str(row["review_id"])
    finally:
        db.close()


def update_review(db_path: Path, review_id: str, **fields: Any) -> None:
    allowed = {
        "status", "evidence_path", "report_path", "executive_summary", "error",
        "delivery_platform", "delivery_chat_id", "delivery_thread_id", "delivery_user_id",
        "delivered", "delivery_attempts", "delivery_error", "last_delivery_at",
    }
    if set(fields) - allowed:
        raise ValueError("unsupported review field")
    fields["updated_at"] = utc_now()
    db = connect(db_path)
    try:
        sql = ",".join(f"{key}=?" for key in fields)
        db.execute(f"UPDATE tvcr_reviews SET {sql} WHERE review_id=?", (*fields.values(), review_id))
        db.commit()
    finally:
        db.close()


def import_proposals(db_path: Path, review_id: str, payload: Dict[str, Any]) -> list[str]:
    proposals = payload.get("proposals")
    if not isinstance(proposals, list):
        raise ValueError("proposals.json must contain a proposals array")
    db = connect(db_path)
    ids: list[str] = []
    try:
        review = db.execute("SELECT review_date FROM tvcr_reviews WHERE review_id=?", (review_id,)).fetchone()
        if not review:
            raise ValueError(f"unknown review: {review_id}")
        stamp = str(review["review_date"]).replace("-", "")
        now = utc_now()
        for index, item in enumerate(proposals, 1):
            if not isinstance(item, dict):
                raise ValueError(f"proposal {index} must be an object")
            required = ["title", "problem_statement", "recommended_action"]
            if any(not str(item.get(key) or "").strip() for key in required):
                raise ValueError(f"proposal {index} is missing required business fields")
            proposal_id = f"TVCR-P-{stamp}-{index:02d}"
            ids.append(proposal_id)
            db.execute(
                """INSERT INTO improvement_proposals
                   (proposal_id,review_id,item_no,product_line,priority,title,problem_statement,business_impact,
                    root_cause_hypotheses_json,options_json,recommended_action,change_scopes_json,
                    expected_value,expected_cost,risk,success_metrics_json,evidence_run_ids_json,
                    status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending_approval',?,?)
                   ON CONFLICT(review_id,item_no) DO UPDATE SET
                    product_line=excluded.product_line,priority=excluded.priority,title=excluded.title,
                    problem_statement=excluded.problem_statement,business_impact=excluded.business_impact,
                    root_cause_hypotheses_json=excluded.root_cause_hypotheses_json,
                    options_json=excluded.options_json,recommended_action=excluded.recommended_action,
                    change_scopes_json=excluded.change_scopes_json,expected_value=excluded.expected_value,
                    expected_cost=excluded.expected_cost,risk=excluded.risk,
                    success_metrics_json=excluded.success_metrics_json,
                    evidence_run_ids_json=excluded.evidence_run_ids_json,updated_at=excluded.updated_at""",
                (
                    proposal_id, review_id, index, str(item.get("product_line") or "company"),
                    str(item.get("priority") or "P2"),
                    str(item["title"]).strip(), str(item["problem_statement"]).strip(),
                    str(item.get("business_impact") or ""), _json(item.get("root_cause_hypotheses") or []),
                    _json(item.get("options") or []), str(item["recommended_action"]).strip(),
                    _json(item.get("change_scopes") or []), str(item.get("expected_value") or ""),
                    str(item.get("expected_cost") or ""), str(item.get("risk") or ""),
                    _json(item.get("success_metrics") or []), _json(item.get("evidence_run_ids") or []),
                    now, now,
                ),
            )
        summary = str(payload.get("executive_summary") or "").strip()
        status = "pending_approval" if ids else "no_action"
        db.execute(
            "UPDATE tvcr_reviews SET status=?,executive_summary=?,updated_at=? WHERE review_id=?",
            (status, summary, now, review_id),
        )
        db.commit()
    finally:
        db.close()
    return ids


def pending_review_deliveries(db_path: Path, max_attempts: int = 10) -> list[Dict[str, Any]]:
    db = connect(db_path)
    try:
        rows = db.execute(
            """SELECT * FROM tvcr_reviews
               WHERE status IN ('pending_approval','no_action','failed') AND delivered=0
                 AND delivery_attempts<?
               ORDER BY period_end ASC LIMIT 20""",
            (max_attempts,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def proposals_for_review(db_path: Path, review_id: str) -> list[Dict[str, Any]]:
    db = connect(db_path)
    try:
        return [dict(row) for row in db.execute(
            "SELECT * FROM improvement_proposals WHERE review_id=? ORDER BY item_no", (review_id,)
        )]
    finally:
        db.close()


def _compact_message_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit > 0 and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip("，。；;,. ") + "…"
    return text


def format_review_message(db_path: Path, review_id: str, limit: int = 1000) -> str:
    db = connect(db_path)
    try:
        review = db.execute("SELECT * FROM tvcr_reviews WHERE review_id=?", (review_id,)).fetchone()
        if not review:
            raise ValueError(f"unknown review: {review_id}")
        proposals = db.execute(
            "SELECT * FROM improvement_proposals WHERE review_id=? ORDER BY item_no", (review_id,)
        ).fetchall()
        review_date = str(review["review_date"] or "").strip()
        title = f"公司日报｜{review_date}" if review_date else "公司日报"
        if review["status"] == "failed":
            error = _compact_message_text(review["error"] or "请检查运行日志。", 180)
            text = f"{title}\n状态：生成失败\n原因：{error}"
        elif not proposals:
            summary = _compact_message_text(
                review["executive_summary"] or "本周期没有可执行的经营改进事项。", 100
            )
            text = f"{title}\n结论：{summary}\n待决策：无"
        else:
            lines = [
                title,
                f"结论：{_compact_message_text(review['executive_summary'] or '发现需要经营决策的事项。', 100)}",
                f"待决策：{len(proposals)} 项",
            ]
            for proposal in proposals:
                title_text = _compact_message_text(proposal["title"], 28)
                recommendation = _compact_message_text(proposal["recommended_action"], 72)
                lines.extend([
                    f"{proposal['item_no']}. [{proposal['priority']}] {title_text}",
                    f"   {recommendation}",
                ])
            lines.append("回复“批准第1项”或“拒绝第1项”。")
            text = "\n".join(lines)
        if limit > 0 and len(text) > limit:
            suffix = "\n[详情已存档]"
            return text[: max(0, limit - len(suffix))].rstrip() + suffix
        return text
    finally:
        db.close()


def _latest_pending_review(db: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return db.execute(
        """SELECT * FROM tvcr_reviews WHERE status IN ('pending_approval','partially_approved')
           ORDER BY review_date DESC LIMIT 1"""
    ).fetchone()


def _resolve_proposal(db: sqlite3.Connection, message: str) -> Optional[sqlite3.Row]:
    match = PROPOSAL_ID_RE.search(message)
    if match:
        return db.execute(
            "SELECT * FROM improvement_proposals WHERE proposal_id=?",
            (match.group(0).upper(),),
        ).fetchone()
    item = ITEM_NO_RE.search(message)
    review = _latest_pending_review(db)
    if item and review:
        return db.execute(
            "SELECT * FROM improvement_proposals WHERE review_id=? AND item_no=?",
            (review["review_id"], int(item.group(1))),
        ).fetchone()
    if review:
        pending = db.execute(
            "SELECT * FROM improvement_proposals WHERE review_id=? AND status='pending_approval' ORDER BY item_no",
            (review["review_id"],),
        ).fetchall()
        if len(pending) == 1:
            return pending[0]
    return None


def _experiment_baseline(db: sqlite3.Connection) -> Dict[str, Any]:
    """Snapshot the metrics an experiment will later be judged against.

    Captured at approval time so weekly evaluation can compute a real delta
    instead of guessing — ``baseline_json`` was previously always ``'{}'``.
    """
    runs = [dict(row) for row in db.execute("SELECT * FROM operational_runs")]
    measured = sum(1 for run in runs if str(run.get("outcome_status") or "unmeasured") != "unmeasured")
    return {
        "captured_at": utc_now(),
        "cost_rollup": pricing.cost_rollup(runs),
        "runs_total": len(runs),
        "runs_measured": measured,
        "runs_unmeasured": len(runs) - measured,
    }


def _create_experiment_for_proposal(db: sqlite3.Connection, proposal: sqlite3.Row, now: str) -> str:
    """Turn an approved proposal into a planned operating experiment.

    Shared by manual approval and policy auto-approval so both paths capture the
    same baseline and implementation contract.
    """
    experiment_id = f"OPS-EXP-{proposal['proposal_id'][7:]}"
    db.execute(
        """INSERT OR IGNORE INTO operating_experiments
           (experiment_id,proposal_id,product_line,name,hypothesis,baseline_json,targets_json,
            implementation_plan_json,status,created_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,'planned',?,?)""",
        (
            experiment_id, proposal["proposal_id"], str(proposal["product_line"] or "company"),
            proposal["title"], proposal["problem_statement"], _json(_experiment_baseline(db)),
            proposal["success_metrics_json"],
            _json({
                "recommended_action": proposal["recommended_action"],
                "change_scopes": json.loads(proposal["change_scopes_json"] or "[]"),
                "rule": "先落实经营方案，再决定是否需要代码变更",
            }), now, now,
        ),
    )
    return experiment_id


def _recompute_review_status(db: sqlite3.Connection, review_id: str, now: str) -> str:
    remaining = db.execute(
        "SELECT COUNT(*) FROM improvement_proposals WHERE review_id=? AND status='pending_approval'",
        (review_id,),
    ).fetchone()[0]
    approved = db.execute(
        "SELECT COUNT(*) FROM improvement_proposals WHERE review_id=? AND status='approved'",
        (review_id,),
    ).fetchone()[0]
    review_status = "partially_approved" if remaining else "approved" if approved else "closed"
    db.execute("UPDATE tvcr_reviews SET status=?,updated_at=? WHERE review_id=?", (review_status, now, review_id))
    return review_status


def _approve_proposal(db: sqlite3.Connection, proposal: sqlite3.Row, *, actor: str, note: str, now: str) -> str:
    """Approve one proposal and spawn its experiment (no commit — caller owns tx)."""
    db.execute(
        """UPDATE improvement_proposals SET status='approved',decided_by=?,decided_at=?,decision_note=?,updated_at=?
           WHERE proposal_id=?""",
        (actor, now, note, now, proposal["proposal_id"]),
    )
    experiment_id = _create_experiment_for_proposal(db, proposal, now)
    _recompute_review_status(db, proposal["review_id"], now)
    return experiment_id


# Risk vocabulary for policy auto-approval.  A proposal is only eligible when it
# states a low risk (or none) and carries no high/medium markers — anything
# ambiguous stays with the user.
_LOW_RISK_MARKERS = ("低", "low", "minimal", "minor", "轻微", "trivial")
_HIGH_RISK_MARKERS = ("高", "中", "high", "medium", "严重", "critical", "不可逆", "irreversible", "危险")


def _is_low_risk(risk: Any) -> bool:
    text = str(risk or "").strip().lower()
    if any(marker in text for marker in _HIGH_RISK_MARKERS):
        return False
    if not text:
        return True
    return any(marker in text for marker in _LOW_RISK_MARKERS)


def _proposal_change_scopes(row: sqlite3.Row) -> set[str]:
    try:
        return {str(scope).strip().lower() for scope in json.loads(row["change_scopes_json"] or "[]") if str(scope).strip()}
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()


def approved_change_scopes(db: sqlite3.Connection) -> set[str]:
    """Union of change_scopes across every already-approved proposal.

    This is the pre-authorised envelope: a new low-risk proposal that stays
    inside it introduces no scope the user has not already sanctioned.
    """
    scopes: set[str] = set()
    for row in db.execute("SELECT change_scopes_json FROM improvement_proposals WHERE status='approved'"):
        scopes |= _proposal_change_scopes(row)
    return scopes


def auto_approve_proposals(
    db_path: Path = DEFAULT_DB,
    *,
    actor: str = "policy-auto-approve",
    now: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Policy-approve P2/low-risk proposals whose change_scopes are already sanctioned.

    Deliberately narrow: only P2 priority, only a stated-low risk, and only when
    every declared change scope already appears in an approved proposal.  P0/P1
    or scope-expanding items always wait for an explicit human decision.
    """
    db = connect(db_path)
    try:
        stamp = now or utc_now()
        baseline = approved_change_scopes(db)
        candidates = db.execute(
            "SELECT * FROM improvement_proposals WHERE status='pending_approval' AND priority='P2' ORDER BY review_id,item_no"
        ).fetchall()
        approved_ids: list[str] = []
        skipped: list[Dict[str, str]] = []
        for row in candidates:
            scopes = _proposal_change_scopes(row)
            if not _is_low_risk(row["risk"]):
                reason = "risk is not stated-low"
            elif not scopes:
                reason = "no change_scopes declared"
            elif not baseline:
                reason = "no approved scope baseline yet"
            elif not scopes.issubset(baseline):
                reason = f"scopes outside approved baseline: {sorted(scopes - baseline)}"
            else:
                reason = ""
            if reason:
                skipped.append({"proposal_id": row["proposal_id"], "reason": reason})
                continue
            if not dry_run:
                _approve_proposal(
                    db, row,
                    actor=actor,
                    note=f"策略化自动批准：P2/低风险，change_scopes {sorted(scopes)} 落在已批准范围内",
                    now=stamp,
                )
            approved_ids.append(row["proposal_id"])
        if not dry_run:
            db.commit()
        return {
            "approved": approved_ids,
            "skipped": skipped,
            "approved_scope_baseline": sorted(baseline),
            "dry_run": dry_run,
        }
    finally:
        db.close()


def escalate_stale_proposals(
    db_path: Path = DEFAULT_DB,
    *,
    now: Optional[str] = None,
    p0_sla_hours: int = 72,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Enforce the proposal SLA: expire superseded backlog, escalate stale P0s.

    * A pending proposal whose review is older than the latest review for the
      same product line is auto-expired to ``superseded`` — a newer review has
      replaced its evidence.
    * A pending P0 in the current review that has waited longer than the SLA is
      stamped ``escalated_at`` (status unchanged so the user can still decide).
    """
    stamp = now or utc_now()
    try:
        current = datetime.fromisoformat(stamp)
    except ValueError:
        current = datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = current - timedelta(hours=p0_sla_hours)
    db = connect(db_path)
    try:
        pending = db.execute(
            """SELECT p.*, r.review_date AS review_date FROM improvement_proposals p
               JOIN tvcr_reviews r ON r.review_id=p.review_id
               WHERE p.status='pending_approval'"""
        ).fetchall()
        latest_date: Dict[str, str] = {}
        for row in db.execute(
            """SELECT p.product_line AS pl, MAX(r.review_date) AS md FROM improvement_proposals p
               JOIN tvcr_reviews r ON r.review_id=p.review_id GROUP BY p.product_line"""
        ):
            latest_date[str(row["pl"])] = str(row["md"] or "")
        superseded: list[str] = []
        escalated: list[str] = []
        for row in pending:
            product_line = str(row["product_line"] or "company")
            if str(row["review_date"] or "") < latest_date.get(product_line, ""):
                superseded.append(row["proposal_id"])
                if not dry_run:
                    db.execute(
                        """UPDATE improvement_proposals
                           SET status='superseded',decided_by='sla-auto-expire',decided_at=?,
                               decision_note='更晚的复盘已取代本提案，SLA 自动过期。',updated_at=?
                           WHERE proposal_id=?""",
                        (stamp, stamp, row["proposal_id"]),
                    )
                continue
            if str(row["priority"] or "") == "P0" and not str(row["escalated_at"] or "").strip():
                try:
                    created = datetime.fromisoformat(str(row["created_at"] or ""))
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if created <= cutoff:
                    escalated.append(row["proposal_id"])
                    if not dry_run:
                        db.execute(
                            "UPDATE improvement_proposals SET escalated_at=?,updated_at=? WHERE proposal_id=?",
                            (stamp, stamp, row["proposal_id"]),
                        )
        if not dry_run:
            db.commit()
        return {"superseded": superseded, "escalated": escalated, "p0_sla_hours": p0_sla_hours, "dry_run": dry_run}
    finally:
        db.close()


def apply_user_decision(
    db_path: Path,
    message: str,
    *,
    actor: str,
    note: str = "",
) -> Optional[Dict[str, Any]]:
    decision = "rejected" if REJECT_RE.search(message) else "approved" if APPROVE_RE.search(message) else ""
    if not decision or not (PROPOSAL_ID_RE.search(message) or ITEM_NO_RE.search(message) or "提案" in message):
        return None
    db = connect(db_path)
    try:
        proposal = _resolve_proposal(db, message)
        if not proposal:
            return {"ok": False, "message": "没有找到对应的待审批 TVCR 提案，请提供提案 ID 或编号。"}
        if proposal["status"] != "pending_approval":
            return {"ok": False, "message": f"提案 {proposal['proposal_id']} 当前状态为 {proposal['status']}，不能重复决策。"}
        now = utc_now()
        db.execute(
            """UPDATE improvement_proposals SET status=?,decided_by=?,decided_at=?,decision_note=?,updated_at=?
               WHERE proposal_id=?""",
            (decision, actor, now, note or message[:1000], now, proposal["proposal_id"]),
        )
        experiment_id = ""
        if decision == "approved":
            experiment_id = _create_experiment_for_proposal(db, proposal, now)
        _recompute_review_status(db, proposal["review_id"], now)
        db.commit()
        return {
            "ok": True,
            "decision": decision,
            "proposal_id": proposal["proposal_id"],
            "proposal_title": proposal["title"],
            "recommended_action": proposal["recommended_action"],
            "change_scopes": json.loads(proposal["change_scopes_json"] or "[]"),
            "success_metrics": json.loads(proposal["success_metrics_json"] or "[]"),
            "experiment_id": experiment_id,
        }
    finally:
        db.close()


EXPERIMENT_STATUSES = {"planned", "running", "evaluating", "succeeded", "failed", "stopped", "rolled_back"}
# Legal transitions.  Blocks nonsense like planned->succeeded (claiming a win
# with no experiment) or succeeded->running (reopening a closed result), while
# allowing retry (failed->planned) and re-runs (evaluating->running).
EXPERIMENT_TRANSITIONS = {
    "planned": {"running", "stopped"},
    "running": {"evaluating", "failed", "stopped"},
    "evaluating": {"succeeded", "failed", "stopped", "rolled_back", "running"},
    "succeeded": {"rolled_back"},
    "failed": {"planned", "running"},
    "stopped": {"planned"},
    "rolled_back": set(),
}
# Weekly cadence from tvcr-governance.md ("每周评估运营实验").
EVALUATION_WINDOW_HOURS = 168


def _iso_plus_hours(base: Optional[datetime], hours: float) -> str:
    return ((base or datetime.now(timezone.utc)) + timedelta(hours=hours)).isoformat(timespec="seconds")


def update_experiment(
    db_path: Path,
    experiment_id: str,
    *,
    status: str,
    result: Optional[Dict[str, Any]] = None,
    conclusion: str = "",
    evaluation_window_hours: float = EVALUATION_WINDOW_HOURS,
    force: bool = False,
) -> None:
    if status not in EXPERIMENT_STATUSES:
        raise ValueError(f"unsupported experiment status: {status}")
    now = utc_now()
    db = connect(db_path)
    try:
        current = db.execute(
            "SELECT status, started_at FROM operating_experiments WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()
        if current is None:
            raise ValueError(f"unknown experiment: {experiment_id}")
        cur_status = str(current["status"])
        if status != cur_status and not force and status not in EXPERIMENT_TRANSITIONS.get(cur_status, set()):
            raise ValueError(f"illegal experiment transition: {cur_status} -> {status}")
        fields: Dict[str, Any] = {"status": status, "updated_at": now}
        if status == "running":
            if not current["started_at"]:  # don't rewrite the original start on re-entry
                fields["started_at"] = now
            fields["evaluation_due_at"] = _iso_plus_hours(_parse_dt(current["started_at"]), evaluation_window_hours)
        if status in {"succeeded", "failed", "stopped", "rolled_back"}:
            fields["ended_at"] = now
        if result is not None:
            fields["result_json"] = _json(result)
        if conclusion:
            fields["conclusion"] = conclusion
        sql = ",".join(f"{key}=?" for key in fields)
        cur = db.execute(f"UPDATE operating_experiments SET {sql} WHERE experiment_id=?", (*fields.values(), experiment_id))
        if cur.rowcount != 1:
            raise ValueError(f"unknown experiment: {experiment_id}")
        db.commit()
    finally:
        db.close()


def reap_experiments(
    db_path: Path = DEFAULT_DB,
    *,
    evaluation_window_hours: float = EVALUATION_WINDOW_HOURS,
) -> Dict[str, Any]:
    """Recover experiments from silent limbo.

    Old ``running`` rows were written with no ``evaluation_due_at`` and would
    never be evaluated.  This backfills a due date, advances anything past due
    to ``evaluating`` (so a human/agent actually assesses it), and surfaces
    ``planned`` experiments that were never kicked off.  Idempotent.
    """
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    advanced: list[str] = []
    backfilled: list[str] = []
    stale_planned: list[str] = []
    db = connect(db_path)
    try:
        rows = db.execute(
            "SELECT experiment_id,status,started_at,evaluation_due_at,created_at FROM operating_experiments"
        ).fetchall()
        for row in rows:
            exp_id = str(row["experiment_id"])
            if row["status"] == "running":
                due = str(row["evaluation_due_at"] or "")
                if not due:
                    due = _iso_plus_hours(_parse_dt(row["started_at"]) or now_dt, evaluation_window_hours)
                    db.execute(
                        "UPDATE operating_experiments SET evaluation_due_at=?,updated_at=? WHERE experiment_id=?",
                        (due, now, exp_id),
                    )
                    backfilled.append(exp_id)
                due_dt = _parse_dt(due)
                if due_dt and due_dt <= now_dt:
                    db.execute(
                        "UPDATE operating_experiments SET status='evaluating',updated_at=? WHERE experiment_id=?",
                        (now, exp_id),
                    )
                    advanced.append(exp_id)
            elif row["status"] == "planned":
                created = _parse_dt(row["created_at"])
                if created and (now_dt - created) >= timedelta(hours=evaluation_window_hours):
                    stale_planned.append(exp_id)
        db.commit()
    finally:
        db.close()
    return {"advanced_to_evaluating": advanced, "due_backfilled": backfilled, "stale_planned": stale_planned}


DEFAULT_STALE_RUN_MINUTES = 120


def _age_minutes(reference: str, *, now: Optional[datetime] = None) -> float:
    dt = _parse_dt(reference)
    if dt is None:
        return 0.0
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 60.0)


def _runner_pid_alive(pid: Any, run_id: str) -> bool:
    """True only if *pid* is a live worker process for *run_id*.

    Mirrors the notifier's liveness probe: a recycled PID that now belongs to an
    unrelated process must not keep a dead run pinned to ``running``.
    """
    try:
        pid_int = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        cmdline = Path(f"/proc/{pid_int}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return run_id in cmdline and ("content_hermes_executor.py" in cmdline or "swarm_runner.py" in cmdline)


def reap_stale_runs(
    db_path: Path = DEFAULT_DB,
    *,
    router_db: Path = DEFAULT_ROUTER_DB,
    stale_minutes: float = DEFAULT_STALE_RUN_MINUTES,
    now: Optional[datetime] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Terminalize operational runs stuck in ``running``/``submitted``.

    A worker can die before writing ``status.json`` (e.g. an executor crash on
    startup), leaving both the router event and the derived operational run
    pinned to ``running`` forever.  The result notifier restarts such runs a
    bounded number of times but never records a terminal outcome once restarts
    are exhausted, so cost/outcome reporting keeps counting them as in-flight.

    This reaps any run older than *stale_minutes* whose worker PID is no longer
    alive: the operational run becomes ``failed`` and the backing router event is
    terminalized too, so ``sync_operational_runs`` cannot resurrect it.  Live
    workers are always left untouched.  Idempotent.
    """
    now_dt = now or datetime.now(timezone.utc)
    now_iso = now_dt.isoformat(timespec="seconds")

    # Runner PIDs live in the router DB; join them back to each run.
    runner_pids: Dict[str, Any] = {}
    if router_db.is_file():
        source = sqlite3.connect(router_db)
        source.row_factory = sqlite3.Row
        try:
            for row in source.execute("SELECT run_id, runner_pid FROM route_events WHERE run_id<>''"):
                runner_pids[str(row["run_id"])] = row["runner_pid"]
        finally:
            source.close()

    reaped: list[str] = []
    skipped_alive: list[str] = []
    db = connect(db_path)
    try:
        rows = db.execute(
            "SELECT run_id, started_at, created_at, input_tokens, output_tokens, "
            "output_bytes, outcome_notes, evidence_json FROM operational_runs "
            "WHERE status IN ('running','submitted')"
        ).fetchall()
        for row in rows:
            run_id = str(row["run_id"])
            reference = str(row["started_at"] or row["created_at"] or "")
            if _age_minutes(reference, now=now_dt) < stale_minutes:
                continue
            if _runner_pid_alive(runner_pids.get(run_id), run_id):
                skipped_alive.append(run_id)
                continue
            reaped.append(run_id)
            if dry_run:
                continue
            empty = (
                int(row["input_tokens"] or 0) == 0
                and int(row["output_tokens"] or 0) == 0
                and int(row["output_bytes"] or 0) == 0
            )
            quality = "empty_output" if empty else "unmeasured"
            note = f"stale run reaped at {now_iso}: worker not alive after >={stale_minutes:.0f}m running"
            existing_note = str(row["outcome_notes"] or "").strip()
            outcome_notes = f"{existing_note}\n{note}".strip() if existing_note else note
            try:
                evidence = json.loads(row["evidence_json"] or "{}")
                if not isinstance(evidence, dict):
                    evidence = {}
            except (TypeError, json.JSONDecodeError):
                evidence = {}
            evidence["stale_reaped"] = {"at": now_iso, "stale_minutes": stale_minutes}
            db.execute(
                "UPDATE operational_runs SET status='failed', quality_status=?, "
                "completed_at=CASE WHEN completed_at IS NULL OR completed_at='' THEN ? ELSE completed_at END, "
                "outcome_notes=?, evidence_json=?, updated_at=? WHERE run_id=?",
                (quality, now_iso, outcome_notes, _json(evidence), now_iso, run_id),
            )
        db.commit()
    finally:
        db.close()

    # Terminalize the backing router events so neither the next sync pass nor the
    # notifier re-derives ``running`` or attempts another restart.
    if reaped and not dry_run and router_db.is_file():
        with file_lock(router_db):
            sink = sqlite3.connect(router_db, timeout=5.0)
            try:
                sink.execute("PRAGMA busy_timeout=5000")
                for run_id in reaped:
                    sink.execute(
                        "UPDATE route_events SET status='failed', "
                        "error=CASE WHEN error IS NULL OR error='' THEN ? ELSE error END, "
                        "updated_at=? WHERE run_id=? AND status IN ('running','submitted')",
                        (f"stale run reaped at {now_iso}", now_iso, run_id),
                    )
                sink.commit()
            finally:
                sink.close()

    return {"reaped": reaped, "skipped_alive": skipped_alive, "stale_minutes": stale_minutes}


def latest_origin(router_db: Path = DEFAULT_ROUTER_DB) -> Dict[str, str]:
    if not router_db.is_file():
        return {}
    db = sqlite3.connect(router_db)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute(
            """SELECT delivery_platform,delivery_chat_id,delivery_thread_id,delivery_user_id
               FROM route_events WHERE delivery_platform<>'' AND delivery_chat_id<>''
               ORDER BY updated_at DESC LIMIT 1"""
        ).fetchone()
        if not row:
            return {}
        return {
            "platform": str(row["delivery_platform"] or ""),
            "chat_id": str(row["delivery_chat_id"] or ""),
            "thread_id": str(row["delivery_thread_id"] or ""),
            "user_id": str(row["delivery_user_id"] or ""),
        }
    finally:
        db.close()


def _optional_bool(value: str) -> Optional[int]:
    if not value:
        return None
    return 1 if value.lower() in {"1", "true", "yes", "y", "是"} else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Company operating ledger and TVCR governance")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync-runs")
    reprice = sub.add_parser("reprice")
    reprice.add_argument("--dry-run", action="store_true")
    reprice.add_argument("--finance-db", default=str(DEFAULT_FINANCE_DB))
    sub.add_parser("cost-report")
    backfill = sub.add_parser("backfill-outcomes")
    backfill.add_argument("--dry-run", action="store_true")
    runs = sub.add_parser("runs")
    runs.add_argument("--date", default="")

    outcome = sub.add_parser("record-outcome")
    outcome.add_argument("run_id")
    outcome.add_argument("--status", default="measured")
    outcome.add_argument("--accepted", default="")
    outcome.add_argument("--published", default="")
    outcome.add_argument("--value-score", type=float)
    outcome.add_argument("--human-minutes", type=float)
    outcome.add_argument("--reach", type=int)
    outcome.add_argument("--revenue", type=float)
    outcome.add_argument("--currency", default="")
    outcome.add_argument("--notes", default="")

    proposals = sub.add_parser("proposals")
    proposals.add_argument("--review-id", default="")
    proposals.add_argument("--status", default="pending_approval")

    decide = sub.add_parser("decide")
    decide.add_argument("message")
    decide.add_argument("--actor", default="user")

    experiment = sub.add_parser("experiment")
    experiment.add_argument("experiment_id")
    experiment.add_argument("status")
    experiment.add_argument("--result-json", default="")
    experiment.add_argument("--conclusion", default="")
    experiment.add_argument("--force", action="store_true", help="bypass transition guard (admin/repair)")

    sub.add_parser("reap-experiments")

    reap_runs = sub.add_parser("reap-stale-runs")
    reap_runs.add_argument("--stale-minutes", type=float, default=DEFAULT_STALE_RUN_MINUTES)
    reap_runs.add_argument("--dry-run", action="store_true")

    auto_approve = sub.add_parser("auto-approve")
    auto_approve.add_argument("--actor", default="policy-auto-approve")
    auto_approve.add_argument("--dry-run", action="store_true")

    escalate = sub.add_parser("escalate-sla")
    escalate.add_argument("--p0-sla-hours", type=int, default=72)
    escalate.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    db_path = Path(args.db)
    if args.command == "sync-runs":
        print(_json(sync_operational_runs(db_path=db_path)))
        return 0
    if args.command == "reprice":
        print(json.dumps(reprice_runs(db_path, Path(args.finance_db), dry_run=args.dry_run), ensure_ascii=False, indent=2))
        return 0
    if args.command == "cost-report":
        print(json.dumps(cost_report(db_path), ensure_ascii=False, indent=2))
        return 0
    if args.command == "backfill-outcomes":
        print(json.dumps(backfill_outcomes(db_path, dry_run=args.dry_run), ensure_ascii=False, indent=2))
        return 0
    if args.command == "runs":
        day = date.fromisoformat(args.date) if args.date else previous_business_day()
        start, end = business_period(day)
        print(json.dumps(runs_for_period(db_path, start, end), ensure_ascii=False, indent=2))
        return 0
    if args.command == "record-outcome":
        fields = {
            "outcome_status": args.status,
            "accepted": _optional_bool(args.accepted),
            "published": _optional_bool(args.published),
            "value_score": args.value_score,
            "human_minutes": args.human_minutes,
            "reach": args.reach,
            "revenue_amount": args.revenue,
            "revenue_currency": args.currency,
            "outcome_notes": args.notes,
        }
        record_outcome(db_path, args.run_id, **{key: value for key, value in fields.items() if value is not None and value != ""})
        print(_json({"updated": args.run_id}))
        return 0
    if args.command == "proposals":
        db = connect(db_path)
        try:
            sql = "SELECT * FROM improvement_proposals WHERE 1=1"
            params: list[Any] = []
            if args.review_id:
                sql += " AND review_id=?"
                params.append(args.review_id)
            if args.status:
                sql += " AND status=?"
                params.append(args.status)
            sql += " ORDER BY created_at DESC,item_no"
            print(json.dumps([dict(row) for row in db.execute(sql, params)], ensure_ascii=False, indent=2))
        finally:
            db.close()
        return 0
    if args.command == "decide":
        print(json.dumps(apply_user_decision(db_path, args.message, actor=args.actor), ensure_ascii=False, indent=2))
        return 0
    if args.command == "experiment":
        result = json.loads(args.result_json) if args.result_json else None
        update_experiment(db_path, args.experiment_id, status=args.status, result=result, conclusion=args.conclusion, force=args.force)
        print(_json({"updated": args.experiment_id, "status": args.status}))
        return 0
    if args.command == "reap-experiments":
        print(json.dumps(reap_experiments(db_path), ensure_ascii=False, indent=2))
        return 0
    if args.command == "reap-stale-runs":
        print(json.dumps(
            reap_stale_runs(db_path, stale_minutes=args.stale_minutes, dry_run=args.dry_run),
            ensure_ascii=False, indent=2,
        ))
        return 0
    if args.command == "auto-approve":
        print(json.dumps(auto_approve_proposals(db_path, actor=args.actor, dry_run=args.dry_run), ensure_ascii=False, indent=2))
        return 0
    if args.command == "escalate-sla":
        print(json.dumps(escalate_stale_proposals(db_path, p0_sla_hours=args.p0_sla_hours, dry_run=args.dry_run), ensure_ascii=False, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
