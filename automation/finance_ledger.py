#!/usr/bin/env python3
"""Maintain an evidence-backed company ledger with forecasts kept separate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


COMPANY_ROOT = Path("/home/pwn/workspace/company")
DEFAULT_DB = COMPANY_ROOT / "finance/finance_ledger.db"
DEFAULT_SUBMISSIONS = Path("/home/pwn/workspace/hackerone/SUBMISSIONS_INDEX.md")
DEFAULT_ROUTER_DB = COMPANY_ROOT / "operations/runtime/company_router.db"
DEFAULT_HERMES_DB = Path("/home/pwn/.hermes/state.db")
BOUNTY_RANGE_RE = re.compile(r"总赏金[^$]*\$([\d,]+)\s*-\s*\$([\d,]+)", re.I)
ACTUAL_COST_STATUSES = {"actual", "confirmed", "provider_reported", "billed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db: Optional[sqlite3.Connection] = None
    try:
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(
            """
        CREATE TABLE IF NOT EXISTS actual_transactions (
            transaction_id TEXT PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            product_line TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('revenue','expense')),
            category TEXT NOT NULL,
            amount REAL NOT NULL CHECK(amount >= 0),
            currency TEXT NOT NULL,
            description TEXT DEFAULT '',
            source_ref TEXT NOT NULL,
            evidence_path TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS forecasts (
            forecast_id TEXT PRIMARY KEY,
            product_line TEXT NOT NULL,
            label TEXT NOT NULL,
            min_amount REAL NOT NULL,
            max_amount REAL NOT NULL,
            currency TEXT NOT NULL,
            source_ref TEXT NOT NULL UNIQUE,
            evidence_sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'forecast',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            actual_cost_usd REAL NOT NULL DEFAULT 0,
            unpriced_sessions INTEGER NOT NULL DEFAULT 0,
            completed_security_runs INTEGER NOT NULL DEFAULT 0,
            completed_article_jobs INTEGER NOT NULL DEFAULT 0,
            completed_video_jobs INTEGER NOT NULL DEFAULT 0,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS model_prices (
            price_id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            model_slug TEXT NOT NULL,
            endpoint TEXT,
            currency TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'millionTokens',
            input_price REAL,
            output_price REAL,
            cache_read_price REAL,
            cache_write_price REAL,
            context_tokens INTEGER,
            max_output_tokens INTEGER,
            source_url TEXT NOT NULL,
            evidence_path TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'observed',
            notes TEXT NOT NULL DEFAULT '',
            UNIQUE(provider, model_slug, currency, source_url)
        );
            """
        )
        db.commit()
        return db
    except BaseException:
        if db is not None:
            db.close()
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_actual(
    db_path: Path,
    *,
    product_line: str,
    kind: str,
    category: str,
    amount: float,
    currency: str,
    description: str,
    source_ref: str,
    evidence_path: Path,
    occurred_at: str,
) -> str:
    if kind not in {"revenue", "expense"}:
        raise ValueError("kind must be revenue or expense")
    if amount < 0:
        raise ValueError("amount must be non-negative")
    if not evidence_path.is_file():
        raise ValueError("an evidence file is required for actual transactions")
    transaction_id = str(uuid.uuid4())
    db = connect(db_path)
    try:
        db.execute(
            """INSERT INTO actual_transactions
               (transaction_id,occurred_at,product_line,kind,category,amount,currency,
                description,source_ref,evidence_path,evidence_sha256,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                transaction_id, occurred_at, product_line, kind, category, float(amount),
                currency.upper(), description, source_ref, str(evidence_path.resolve()),
                sha256_file(evidence_path), utc_now(),
            ),
        )
        db.commit()
    finally:
        db.close()
    return transaction_id


def sync_forecast(db_path: Path, submissions: Path) -> bool:
    if not submissions.is_file():
        return False
    content = submissions.read_text(encoding="utf-8")
    match = BOUNTY_RANGE_RE.search(content)
    if not match:
        return False
    minimum = float(match.group(1).replace(",", ""))
    maximum = float(match.group(2).replace(",", ""))
    db = connect(db_path)
    try:
        db.execute(
            """INSERT INTO forecasts
               (forecast_id,product_line,label,min_amount,max_amount,currency,source_ref,
                evidence_sha256,status,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_ref) DO UPDATE SET
                min_amount=excluded.min_amount,max_amount=excluded.max_amount,
                evidence_sha256=excluded.evidence_sha256,updated_at=excluded.updated_at""",
            (
                str(uuid.uuid4()), "security-exploration", "HackerOne findings estimated bounty",
                minimum, maximum, "USD", str(submissions.resolve()), sha256_file(submissions),
                "forecast", utc_now(),
            ),
        )
        db.commit()
        return True
    finally:
        db.close()


def _hermes_cost_snapshot(path: Path) -> tuple[float, int]:
    if not path.is_file():
        return 0.0, 0
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
        if not {"estimated_cost_usd", "cost_status"}.issubset(cols):
            return 0.0, 0
        rows = db.execute("SELECT estimated_cost_usd,cost_status FROM sessions").fetchall()
        actual = sum(float(row["estimated_cost_usd"] or 0) for row in rows if str(row["cost_status"] or "").lower() in ACTUAL_COST_STATUSES)
        unpriced = sum(1 for row in rows if str(row["cost_status"] or "").lower() not in ACTUAL_COST_STATUSES)
        return actual, unpriced
    finally:
        db.close()


def _route_counts(path: Path) -> Dict[str, int]:
    counts = {"security": 0, "article": 0, "video": 0}
    if not path.is_file():
        return counts
    db = sqlite3.connect(path)
    try:
        rows = db.execute(
            """SELECT route,COUNT(*) FROM route_events
               WHERE status='completed' GROUP BY route"""
        ).fetchall()
        for route, count in rows:
            if route in counts:
                counts[route] = int(count)
        return counts
    finally:
        db.close()


def sync_snapshot(db_path: Path, router_db: Path, hermes_db: Path) -> str:
    actual_cost, unpriced = _hermes_cost_snapshot(hermes_db)
    routes = _route_counts(router_db)
    snapshot_id = str(uuid.uuid4())
    db = connect(db_path)
    try:
        db.execute(
            """INSERT INTO usage_snapshots
               (snapshot_id,source,actual_cost_usd,unpriced_sessions,completed_security_runs,
                completed_article_jobs,completed_video_jobs,captured_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                snapshot_id, "hermes+company-router", actual_cost, unpriced,
                routes["security"], routes["article"], routes["video"], utc_now(),
            ),
        )
        db.commit()
    finally:
        db.close()
    return snapshot_id


def report(db_path: Path) -> Dict[str, Any]:
    db = connect(db_path)
    try:
        actual = [dict(row) for row in db.execute(
            """SELECT currency,kind,SUM(amount) amount,COUNT(*) transactions
               FROM actual_transactions GROUP BY currency,kind ORDER BY currency,kind"""
        )]
        forecasts = [dict(row) for row in db.execute(
            "SELECT product_line,label,min_amount,max_amount,currency,status,source_ref FROM forecasts ORDER BY updated_at DESC"
        )]
        snapshot = db.execute("SELECT * FROM usage_snapshots ORDER BY captured_at DESC LIMIT 1").fetchone()
        model_prices = [dict(row) for row in db.execute(
            """SELECT provider,model,model_slug,currency,unit,input_price,output_price,
                      cache_read_price,cache_write_price,collected_at,source_url,status
               FROM model_prices ORDER BY provider,model"""
        )]
        return {
            "actual": actual,
            "actual_revenue_is_zero": not any(row["kind"] == "revenue" and float(row["amount"] or 0) > 0 for row in actual),
            "forecasts_excluded_from_actual": forecasts,
            "latest_usage_snapshot": dict(snapshot) if snapshot else None,
            "model_prices": model_prices,
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence-backed company finance ledger")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--add-actual", action="store_true")
    parser.add_argument("--product-line", default="")
    parser.add_argument("--kind", choices=["revenue", "expense"])
    parser.add_argument("--category", default="")
    parser.add_argument("--amount", type=float)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--description", default="")
    parser.add_argument("--source-ref", default="")
    parser.add_argument("--evidence-file", default="")
    parser.add_argument("--occurred-at", default="")
    args = parser.parse_args()
    db_path = Path(args.db)
    if args.add_actual:
        required = [args.product_line, args.kind, args.category, args.source_ref, args.evidence_file]
        if any(not item for item in required) or args.amount is None:
            parser.error("--add-actual requires product line, kind, category, amount, source ref and evidence file")
        tx = add_actual(
            db_path,
            product_line=args.product_line,
            kind=args.kind,
            category=args.category,
            amount=args.amount,
            currency=args.currency,
            description=args.description,
            source_ref=args.source_ref,
            evidence_path=Path(args.evidence_file),
            occurred_at=args.occurred_at or utc_now(),
        )
        print(json.dumps({"transaction_id": tx}, ensure_ascii=False))
        return 0
    if args.sync:
        print(json.dumps({
            "forecast_synced": sync_forecast(db_path, DEFAULT_SUBMISSIONS),
            "snapshot_id": sync_snapshot(db_path, DEFAULT_ROUTER_DB, DEFAULT_HERMES_DB),
        }, ensure_ascii=False))
        return 0
    print(json.dumps(report(db_path), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
