#!/usr/bin/env python3
"""Gate Swarm knowledge before any reviewed material enters the company Wiki."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable


COMPANY_ROOT = Path("/home/pwn/workspace/company")
DEFAULT_SWARM_DB = Path("/home/pwn/workspace/research/swarm-knowledge/swarm_knowledge.db")
DEFAULT_GATE_DB = COMPANY_ROOT / "operations/runtime/knowledge_promotion.db"
DEFAULT_WIKI_DIR = COMPANY_ROOT / "wiki/promoted"

DOMAIN_RE = re.compile(r"(?<![@\w-])(?:https?://)?([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)", re.I)
IP_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
SECRET_RE = re.compile(r"(?:api[_ -]?key|secret|token|password|dsn)\s*[:=]\s*[^\s,;]+", re.I)
PATH_RE = re.compile(r"/(?:home|root|opt|var|tmp)/[^\s`\])]+", re.I)
ENDPOINT_RE = re.compile(r"(?<!\w)/(?:api|auth|oauth|admin|internal|v\d+)(?:/[A-Za-z0-9_.-]+)+", re.I)
SENSITIVE_TERMS = {
    "poc", "exploit", "未披露", "0day", "zero-day", "bypass", "接管", "takeover",
    "hackerone", "in scope", "payload", "hardcoded key", "hardcoded-key",
    "internal network", "内网", "smb", "eternalblue", "cve-", "csrf", "ssrf",
    "auth flow", "token issuance", "websocket endpoint", "high-value target",
}
DISCLOSURE_TAGS = {"public", "published", "disclosed", "fixed-and-public", "公开", "已披露"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_gate(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS promotion_candidates (
            candidate_id TEXT PRIMARY KEY,
            knowledge_id TEXT NOT NULL UNIQUE,
            title TEXT DEFAULT '',
            sanitized_preview TEXT DEFAULT '',
            validation_status TEXT NOT NULL,
            sensitivity_status TEXT NOT NULL,
            disclosure_status TEXT NOT NULL,
            human_approval_status TEXT NOT NULL DEFAULT 'pending',
            status TEXT NOT NULL,
            reasons_json TEXT NOT NULL DEFAULT '[]',
            reviewed_content TEXT DEFAULT '',
            content_sha256 TEXT DEFAULT '',
            approved_by TEXT DEFAULT '',
            approved_at TEXT DEFAULT '',
            promoted_path TEXT DEFAULT '',
            promoted_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_promotion_status
        ON promotion_candidates(status, updated_at DESC);
        """
    )
    db.commit()
    return db


def _tags(value: Any) -> set[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        parsed = []
    return {str(item).strip().lower() for item in parsed if str(item).strip()}


def _trust(value: Any) -> Dict[str, float]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        parsed = {}
    return {key: float(parsed.get(key, 0.0) or 0.0) for key in ("logic_soundness", "base_confidence", "cross_validation")}


def sensitivity_hits(text: str) -> list[str]:
    hits: list[str] = []
    lowered = text.lower()
    if DOMAIN_RE.search(text):
        hits.append("domain_or_url")
    if EMAIL_RE.search(text):
        hits.append("email")
    if SECRET_RE.search(text):
        hits.append("credential_or_dsn")
    if PATH_RE.search(text):
        hits.append("local_path")
    if ENDPOINT_RE.search(text):
        hits.append("internal_endpoint")
    for raw in IP_RE.findall(text):
        try:
            ipaddress.ip_address(raw)
            hits.append("ip_address")
            break
        except ValueError:
            pass
    if any(term in lowered for term in SENSITIVE_TERMS):
        hits.append("security_exploit_detail")
    return sorted(set(hits))


def sanitize_preview(text: str, limit: int = 600) -> str:
    value = SECRET_RE.sub("[REDACTED_CREDENTIAL]", text)
    value = EMAIL_RE.sub("[REDACTED_EMAIL]", value)
    value = DOMAIN_RE.sub("[REDACTED_DOMAIN]", value)
    value = IP_RE.sub("[REDACTED_IP]", value)
    value = PATH_RE.sub("[REDACTED_PATH]", value)
    value = " ".join(value.split())
    return value[:limit]


def assess(entry: sqlite3.Row) -> Dict[str, Any]:
    combined = "\n".join((str(entry["title"] or ""), str(entry["content"] or ""), str(entry["tags"] or "")))
    trust = _trust(entry["trust_vector"])
    validated = (
        str(entry["status"] or "") == "active"
        and int(entry["level"] or 0) >= 2
        and bool(entry["last_validated_at"])
        and trust["base_confidence"] >= 0.7
        and trust["cross_validation"] >= 0.8
    )
    hits = sensitivity_hits(combined)
    if str(entry["knowledge_type"] or "").lower() == "vulnerability":
        hits.append("vulnerability_record")
    if str(entry["knowledge_intent"] or "").lower() == "attack":
        hits.append("attack_intent")
    hits = sorted(set(hits))
    tags = _tags(entry["tags"])
    disclosure = "public" if tags & DISCLOSURE_TAGS else "unknown"
    reasons: list[str] = []
    if not validated:
        reasons.append("knowledge has not passed validation threshold")
    if hits:
        reasons.append("sensitive indicators: " + ", ".join(hits))
    if disclosure != "public":
        reasons.append("public disclosure/fix status is not proven")
    if hits:
        status = "blocked_sensitive"
    elif not validated:
        status = "needs_validation"
    elif disclosure != "public":
        status = "needs_disclosure"
    else:
        status = "pending_approval"
    return {
        "validation_status": "passed" if validated else "failed",
        "sensitivity_status": "blocked" if hits else "passed",
        "disclosure_status": disclosure,
        "status": status,
        "reasons": reasons,
        "preview": sanitize_preview(combined),
    }


def scan(swarm_db: Path, gate_db: Path) -> Dict[str, int]:
    source = sqlite3.connect(swarm_db)
    source.row_factory = sqlite3.Row
    gate = connect_gate(gate_db)
    counts: Dict[str, int] = {}
    now = utc_now()
    try:
        entries = source.execute(
            """SELECT id,level,knowledge_type,content,title,domain,knowledge_intent,
                      trust_vector,status,tags,last_validated_at
               FROM knowledge_entries WHERE status='active'"""
        ).fetchall()
        for entry in entries:
            result = assess(entry)
            existing = gate.execute(
                "SELECT candidate_id,human_approval_status,status FROM promotion_candidates WHERE knowledge_id=?",
                (entry["id"],),
            ).fetchone()
            if existing and existing["human_approval_status"] == "approved":
                counts["approved_unchanged"] = counts.get("approved_unchanged", 0) + 1
                continue
            candidate_id = existing["candidate_id"] if existing else str(uuid.uuid4())
            gate.execute(
                """INSERT INTO promotion_candidates
                   (candidate_id,knowledge_id,title,sanitized_preview,validation_status,
                    sensitivity_status,disclosure_status,human_approval_status,status,
                    reasons_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,'pending',?,?,?,?)
                   ON CONFLICT(knowledge_id) DO UPDATE SET
                    title=excluded.title,sanitized_preview=excluded.sanitized_preview,
                    validation_status=excluded.validation_status,
                    sensitivity_status=excluded.sensitivity_status,
                    disclosure_status=excluded.disclosure_status,status=excluded.status,
                    reasons_json=excluded.reasons_json,updated_at=excluded.updated_at""",
                (
                    candidate_id, entry["id"], sanitize_preview(str(entry["title"] or ""), 180),
                    result["preview"], result["validation_status"], result["sensitivity_status"],
                    result["disclosure_status"], result["status"],
                    json.dumps(result["reasons"], ensure_ascii=False), now, now,
                ),
            )
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        gate.commit()
    finally:
        source.close()
        gate.close()
    return counts


def approve(
    gate_db: Path,
    candidate_id: str,
    reviewer: str,
    reviewed_file: Path,
    disclosure_status: str,
) -> None:
    content = reviewed_file.read_text(encoding="utf-8").strip()
    if disclosure_status != "public":
        raise ValueError("company Wiki promotion requires proven public disclosure/fix status")
    hits = sensitivity_hits(content)
    if hits:
        raise ValueError("reviewed content still contains sensitive indicators: " + ", ".join(hits))
    db = connect_gate(gate_db)
    try:
        row = db.execute("SELECT * FROM promotion_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if not row:
            raise ValueError("candidate not found")
        if row["status"] == "promoted":
            raise ValueError("candidate is already promoted")
        now = utc_now()
        db.execute(
            """UPDATE promotion_candidates SET human_approval_status='approved',status='approved',
               validation_status='reviewed',sensitivity_status='reviewed_passed',disclosure_status='public',
               reviewed_content=?,content_sha256=?,approved_by=?,approved_at=?,updated_at=?
               WHERE candidate_id=?""",
            (content, hashlib.sha256(content.encode()).hexdigest(), reviewer, now, now, candidate_id),
        )
        db.commit()
    finally:
        db.close()


def promote(gate_db: Path, candidate_id: str, wiki_dir: Path) -> Path:
    db = connect_gate(gate_db)
    try:
        row = db.execute("SELECT * FROM promotion_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if not row:
            raise ValueError("candidate not found")
        if row["status"] != "approved" or not row["reviewed_content"]:
            raise ValueError("candidate requires explicit human approval and reviewed content")
        wiki_dir.mkdir(parents=True, exist_ok=True)
        path = wiki_dir / f"knowledge-{candidate_id[:8]}.md"
        body = (
            "---\n"
            "tags: [knowledge-promotion, reviewed]\n"
            f"source_knowledge_id: {row['knowledge_id']}\n"
            f"approved_by: {row['approved_by']}\n"
            f"approved_at: {row['approved_at']}\n"
            "---\n\n"
            f"{row['reviewed_content'].strip()}\n"
        )
        path.write_text(body, encoding="utf-8")
        now = utc_now()
        db.execute(
            "UPDATE promotion_candidates SET status='promoted',promoted_path=?,promoted_at=?,updated_at=? WHERE candidate_id=?",
            (str(path), now, now, candidate_id),
        )
        db.commit()
        return path
    finally:
        db.close()


def list_candidates(gate_db: Path, statuses: Iterable[str] = ()) -> list[Dict[str, Any]]:
    db = connect_gate(gate_db)
    try:
        status_list = [item for item in statuses if item]
        if status_list:
            marks = ",".join("?" for _ in status_list)
            rows = db.execute(
                f"SELECT * FROM promotion_candidates WHERE status IN ({marks}) ORDER BY updated_at DESC",
                status_list,
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM promotion_candidates ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Company knowledge promotion gateway")
    parser.add_argument("--swarm-db", default=str(DEFAULT_SWARM_DB))
    parser.add_argument("--gate-db", default=str(DEFAULT_GATE_DB))
    parser.add_argument("--wiki-dir", default=str(DEFAULT_WIKI_DIR))
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--status", action="append", default=[])
    parser.add_argument("--approve", default="")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--reviewed-file", default="")
    parser.add_argument("--disclosure-status", default="")
    parser.add_argument("--promote", default="")
    args = parser.parse_args()
    gate_db = Path(args.gate_db)
    if args.approve:
        if not args.reviewer or not args.reviewed_file or not args.disclosure_status:
            parser.error("--approve requires --reviewer, --reviewed-file and --disclosure-status public")
        approve(gate_db, args.approve, args.reviewer, Path(args.reviewed_file), args.disclosure_status)
        print(json.dumps({"approved": args.approve}, ensure_ascii=False))
        return 0
    if args.promote:
        path = promote(gate_db, args.promote, Path(args.wiki_dir))
        print(json.dumps({"promoted": args.promote, "path": str(path)}, ensure_ascii=False))
        return 0
    if args.list:
        print(json.dumps(list_candidates(gate_db, args.status), ensure_ascii=False, indent=2))
        return 0
    counts = scan(Path(args.swarm_db), gate_db)
    print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
