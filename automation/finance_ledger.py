#!/usr/bin/env python3
"""Maintain an evidence-backed company ledger with forecasts kept separate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import pricing
    from ._safe_io import read_text_limited, sqlite_uri
except ImportError:  # direct ``python automation/finance_ledger.py`` invocation
    import pricing  # type: ignore[no-redef]
    from _safe_io import read_text_limited, sqlite_uri


COMPANY_ROOT = Path("/home/pwn/workspace/company")
DEFAULT_DB = COMPANY_ROOT / "finance/finance_ledger.db"
DEFAULT_SUBMISSIONS = Path("/home/pwn/workspace/hackerone/SUBMISSIONS_INDEX.md")
DEFAULT_ROUTER_DB = COMPANY_ROOT / "operations/runtime/company_router.db"
DEFAULT_HERMES_DB = Path("/home/pwn/.hermes/state.db")
BOUNTY_RANGE_RE = re.compile(r"总赏金[^$]*\$([\d,]+)\s*-\s*\$([\d,]+)", re.IGNORECASE)
ACTUAL_COST_STATUSES = {"actual", "confirmed", "provider_reported", "billed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_float(value: Any) -> float:
    """Coerce a DB/JSON amount to float, degrading to 0.0 on malformed values.

    The ledger aggregates rows written by sibling subsystems; a corrupt
    amount must not crash the finance report cron.
    """
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


# Columns added after usage_snapshots first shipped.  The names are hard-coded
# literals (never user input), so a plain ALTER is safe.
_USAGE_SNAPSHOT_MIGRATIONS = {
    "confirmed_cost_usd": "REAL NOT NULL DEFAULT 0",
    "estimated_cost_usd": "REAL NOT NULL DEFAULT 0",
    "estimated_cost_native": "TEXT NOT NULL DEFAULT '{}'",
    "priced_sessions": "INTEGER NOT NULL DEFAULT 0",
}


def _migrate_usage_snapshots(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(usage_snapshots)")}
    for column, definition in _USAGE_SNAPSHOT_MIGRATIONS.items():
        if column not in columns:
            db.execute(f"ALTER TABLE usage_snapshots ADD COLUMN {column} {definition}")



def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db: sqlite3.Connection | None = None
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
        _migrate_usage_snapshots(db)
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
    try:
        amount_value = float(amount)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("amount must be a finite non-negative number") from exc
    if not math.isfinite(amount_value) or amount_value < 0:
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
                transaction_id, occurred_at, product_line, kind, category, amount_value,
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
    content = read_text_limited(submissions, max_bytes=10 * 1024 * 1024)
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


_SESSION_TOKEN_COLUMNS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
)


def _hermes_cost_snapshot(
    path: Path, price_table: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Join measured Hermes session tokens to evidence-backed model prices.

    Sessions the provider has already billed (``cost_status`` in
    ``ACTUAL_COST_STATUSES``) contribute their recorded cost as *confirmed*.
    Every other session is priced from ``model_prices`` via
    ``pricing.estimate_cost`` so its real token cost is counted instead of
    silently collapsing to ``$0``.  A session whose model has no matching price
    row (or only non-USD prices) is kept honest: it stays ``unpriced`` (or is
    tracked as a native-currency amount) rather than assumed free — "unknown is
    not zero", and no FX rate is ever invented.
    """
    result: dict[str, Any] = {
        "confirmed_cost_usd": 0.0,
        "estimated_cost_usd": 0.0,
        "estimated_cost_native": {},
        "priced_sessions": 0,
        "unpriced_sessions": 0,
    }
    if not path.is_file():
        return result
    if price_table is None:
        price_table = pricing.load_price_table()
    try:
        db = sqlite3.connect(sqlite_uri(path, mode="ro"), uri=True)
    except sqlite3.Error:
        return result
    db.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
        if not {"estimated_cost_usd", "cost_status"}.issubset(cols):
            return result
        token_cols = [column for column in _SESSION_TOKEN_COLUMNS if column in cols]
        has_model = "model" in cols
        select_cols = ["estimated_cost_usd", "cost_status", *token_cols]
        if has_model:
            select_cols.append("model")
        native = result["estimated_cost_native"]
        # Column names come from fixed constants, filtered against the live
        # table's PRAGMA columns -- never from user/configuration input.
        for row in db.execute(f"SELECT {','.join(select_cols)} FROM sessions"):  # nosec B608 -- fixed column whitelist
            if str(row["cost_status"] or "").lower() in ACTUAL_COST_STATUSES:
                try:
                    confirmed = float(row["estimated_cost_usd"])
                except (TypeError, ValueError, OverflowError):
                    confirmed = float("nan")
                if math.isfinite(confirmed) and confirmed >= 0:
                    result["confirmed_cost_usd"] += confirmed
                    result["priced_sessions"] += 1
                else:
                    result["unpriced_sessions"] += 1
                continue
            tokens = {column: row[column] for column in token_cols}
            est = pricing.estimate_cost(row["model"] if has_model else "", tokens, price_table)
            if est["cost_status"] != "estimated":
                result["unpriced_sessions"] += 1
                continue
            result["priced_sessions"] += 1
            usd = est["estimated_cost_usd"]
            if usd is not None:
                result["estimated_cost_usd"] += float(usd)
            else:
                amount = est["estimated_cost_native"]
                currency = str(est["estimated_cost_currency"] or "").upper()
                if amount is not None and currency:
                    native[currency] = round(native.get(currency, 0.0) + float(amount), 6)
        result["confirmed_cost_usd"] = round(result["confirmed_cost_usd"], 6)
        result["estimated_cost_usd"] = round(result["estimated_cost_usd"], 6)
        return result
    finally:
        db.close()


def _route_counts(path: Path) -> dict[str, int]:
    counts = {"security": 0, "article": 0, "video": 0}
    if not path.is_file():
        return counts
    try:
        db = sqlite3.connect(sqlite_uri(path, mode="ro"), uri=True)
    except sqlite3.Error:
        return counts
    try:
        rows = db.execute(
            """SELECT route,COUNT(*) FROM route_events
               WHERE status='completed' GROUP BY route"""
        ).fetchall()
        for route, count in rows:
            if route in counts:
                counts[route] = int(count)
        return counts
    except sqlite3.Error:
        # An older/partial router DB without the route_events table must not
        # abort the whole --sync cron job; degrade to zeroed counts like the
        # sibling _hermes_cost_snapshot does for its schema mismatch.
        return counts
    finally:
        db.close()


def sync_snapshot(db_path: Path, router_db: Path, hermes_db: Path) -> str:
    costs = _hermes_cost_snapshot(hermes_db, pricing.load_price_table(db_path))
    routes = _route_counts(router_db)
    # ``actual_cost_usd`` carries the real measured USD cost: provider-confirmed
    # billing plus tokens priced against evidence-backed ``model_prices`` (USD
    # rows only — native-currency amounts stay in ``estimated_cost_native`` and
    # are never converted with an invented FX rate).  The confirmed/estimated
    # split is kept in dedicated columns so the mix stays auditable.
    actual_cost = round(costs["confirmed_cost_usd"] + costs["estimated_cost_usd"], 6)
    snapshot_id = str(uuid.uuid4())
    db = connect(db_path)
    try:
        db.execute(
            """INSERT INTO usage_snapshots
               (snapshot_id,source,actual_cost_usd,unpriced_sessions,completed_security_runs,
                completed_article_jobs,completed_video_jobs,captured_at,
                confirmed_cost_usd,estimated_cost_usd,estimated_cost_native,priced_sessions)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                snapshot_id, "hermes+company-router", actual_cost, costs["unpriced_sessions"],
                routes["security"], routes["article"], routes["video"], utc_now(),
                costs["confirmed_cost_usd"], costs["estimated_cost_usd"],
                json.dumps(costs["estimated_cost_native"], ensure_ascii=False),
                costs["priced_sessions"],
            ),
        )
        db.commit()
    finally:
        db.close()
    return snapshot_id


def report(db_path: Path) -> dict[str, Any]:
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
            "actual_revenue_is_zero": not any(row["kind"] == "revenue" and _safe_float(row["amount"]) > 0 for row in actual),
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
