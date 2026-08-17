#!/usr/bin/env python3
"""Estimate model token cost by joining measured usage to evidence-backed prices.

The company already measures tokens precisely (``operational_runs``) and stores
provider prices precisely (``finance_ledger.model_prices``), but nothing ever
multiplied the two.  As a result the dominant production path recorded
``$0.00`` cost, which silently biased every downstream ROI judgement toward
"free / profitable".

This module is a small, pure price-join.  It never invents an FX rate: a cost
is reported in USD only when the matched price row is already denominated in
USD; otherwise the native amount + currency are kept so no cost silently
collapses to zero.  A run that cannot be priced stays explicitly ``unpriced``
(never ``$0``), preserving the ledger's "unknown is not zero" discipline.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

try:
    from ._safe_io import sqlite_uri
except ImportError:  # direct ``python automation/pricing.py`` invocation
    from _safe_io import sqlite_uri


COMPANY_ROOT = Path("/home/pwn/workspace/company")
DEFAULT_FINANCE_DB = COMPANY_ROOT / "finance/finance_ledger.db"

# Statuses that mean a real, provider-confirmed cost is already recorded.  A
# computed estimate must never overwrite one of these.
ACTUAL_COST_STATUSES = {"actual", "confirmed", "provider_reported", "billed"}

PER_MILLION = 1_000_000

# (usage-token field on the run, price column on the price row)
_COST_COMPONENTS = (
    ("input_tokens", "input_price"),
    ("output_tokens", "output_price"),
    ("cache_read_tokens", "cache_read_price"),
    ("cache_write_tokens", "cache_write_price"),
)


def _norm(model: Any) -> str:
    return str(model or "").strip().lower()


def _base(slug: str) -> str:
    """Provider-independent base name: ``deepseek/deepseek-v4-pro`` -> ``deepseek-v4-pro``."""
    return slug.rsplit("/", 1)[-1] if slug else ""


def _counter(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _nonnegative_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def load_price_table(finance_db: Path = DEFAULT_FINANCE_DB) -> dict[str, Any]:
    """Load evidence-backed prices read-only.

    Returns a lookup with ``by_slug`` (exact, lowercased ``model_slug``) and
    ``by_base`` (provider-stripped name -> candidate rows).  Missing DB / table
    yields an empty table so callers degrade to "unpriced" rather than crash.
    """
    by_slug: dict[str, list[dict[str, Any]]] = {}
    by_base: dict[str, list[dict[str, Any]]] = {}
    try:
        db = sqlite3.connect(sqlite_uri(finance_db, mode="ro"), uri=True)
    except sqlite3.Error:
        return {"by_slug": by_slug, "by_base": by_base}
    db.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in db.execute("PRAGMA table_info(model_prices)")}
        if not {"model_slug", "currency", "input_price", "output_price"}.issubset(cols):
            return {"by_slug": by_slug, "by_base": by_base}
        for row in db.execute(
            """SELECT provider,model_slug,currency,unit,
                      input_price,output_price,cache_read_price,cache_write_price
               FROM model_prices"""
        ):
            info = {
                "provider": str(row["provider"] or ""),
                "model_slug": str(row["model_slug"] or ""),
                "currency": str(row["currency"] or "").upper(),
                "unit": str(row["unit"] or "millionTokens"),
                "input_price": row["input_price"],
                "output_price": row["output_price"],
                "cache_read_price": row["cache_read_price"],
                "cache_write_price": row["cache_write_price"],
            }
            slug = _norm(info["model_slug"])
            if not slug:
                continue
            by_slug.setdefault(slug, []).append(info)
            by_base.setdefault(_base(slug), []).append(info)
    except sqlite3.Error:
        return {"by_slug": {}, "by_base": {}}
    finally:
        db.close()
    return {"by_slug": by_slug, "by_base": by_base}


def _pick(candidates: list[dict[str, Any]], model_norm: str) -> dict[str, Any] | None:
    """Deterministic winner among price rows sharing a base name.

    Prefer USD (so figures stay comparable in a mixed-provider deployment),
    then an exact full-slug match, then the first row.  The choice is always
    recorded as an *estimate* with its provenance, so an operator can correct a
    mispriced provider via the outcome path.
    """
    if not candidates:
        return None
    usd = [item for item in candidates if item["currency"] == "USD"]
    pool = usd or candidates
    for item in pool:
        if _norm(item["model_slug"]) == model_norm:
            return item
    return pool[0]


def match_price(model: Any, table: dict[str, Any]) -> dict[str, Any] | None:
    model_norm = _norm(model)
    if not model_norm:
        return None
    base = _base(model_norm)
    # Consider every row sharing the base name (covers both ``deepseek-v4-pro``
    # and ``deepseek/deepseek-v4-pro``) so USD preference can win over an exact
    # non-USD match.
    candidates = list(table.get("by_base", {}).get(base, []))
    if not candidates:
        candidates = list(table.get("by_slug", {}).get(model_norm, []))
    return _pick(candidates, model_norm)


def estimate_cost(model: Any, tokens: dict[str, Any], table: dict[str, Any]) -> dict[str, Any]:
    """Estimate the cost of one run from its measured tokens.

    Never fabricates: unmatched -> ``unpriced``; matched non-USD -> native
    amount kept, ``estimated_cost_usd`` left ``None`` (no FX guess); price
    components that are absent for the model are listed in
    ``unpriced_components`` rather than assumed free-but-hidden.
    """
    token_counts = {field: _counter(tokens.get(field)) for field, _ in _COST_COMPONENTS}
    total_tokens = sum(token_counts.values())
    result: dict[str, Any] = {
        "estimated_cost_usd": None,
        "estimated_cost_native": None,
        "estimated_cost_currency": "",
        "cost_status": "unknown",
        "matched_slug": "",
        "matched_provider": "",
        "priced_components": [],
        "unpriced_components": [],
    }
    if not _norm(model) or total_tokens == 0:
        return result
    price = match_price(model, table)
    if price is None:
        result["cost_status"] = "unpriced"
        result["unpriced_components"] = [field for field, count in token_counts.items() if count]
        return result

    native = 0.0
    priced: list[str] = []
    unpriced: list[str] = []
    for field, price_col in _COST_COMPONENTS:
        count = token_counts[field]
        if not count:
            continue
        rate = _nonnegative_float(price.get(price_col))
        if rate is None:
            unpriced.append(field)
            continue
        native += count * rate / PER_MILLION
        priced.append(field)

    currency = price["currency"]
    native_amount = round(native, 6) if priced else None
    result.update({
        "estimated_cost_native": native_amount,
        "estimated_cost_currency": currency,
        # USD only when the price itself is USD — no invented FX rate.
        "estimated_cost_usd": native_amount if currency == "USD" else None,
        "cost_status": "estimated" if priced else "unpriced",
        "matched_slug": price["model_slug"],
        "matched_provider": price["provider"],
        "priced_components": priced,
        "unpriced_components": unpriced,
    })
    return result


def price_run_update(
    model: Any,
    tokens: dict[str, Any],
    table: dict[str, Any],
    *,
    existing_cost_status: Any = "",
) -> dict[str, Any] | None:
    """Return column updates for a run, or ``None`` to leave it untouched.

    A confirmed/actual cost is authoritative and never replaced by an estimate.
    """
    if _norm(existing_cost_status) in ACTUAL_COST_STATUSES:
        return None
    est = estimate_cost(model, tokens, table)
    return {
        "estimated_cost_usd": est["estimated_cost_usd"],
        "estimated_cost_native": est["estimated_cost_native"],
        "estimated_cost_currency": est["estimated_cost_currency"],
        "cost_status": est["cost_status"],
        "_pricing": {
            "matched_slug": est["matched_slug"],
            "matched_provider": est["matched_provider"],
            "priced_components": est["priced_components"],
            "unpriced_components": est["unpriced_components"],
        },
    }


def cost_rollup(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate cost honestly: confirmed USD, estimated USD, native non-USD, and
    the still-unpriced token volume — so no consumer can sum a basis that omits
    the unpriced portion.
    """
    confirmed_usd = 0.0
    estimated_usd = 0.0
    estimated_native: dict[str, float] = {}
    unpriced_runs = 0
    unpriced_tokens = 0
    priced_runs = 0
    for run in runs:
        status = _norm(run.get("cost_status") or "unknown")
        tokens = sum(_counter(run.get(field)) for field, _ in _COST_COMPONENTS)
        if status in ACTUAL_COST_STATUSES:
            amount = _nonnegative_float(run.get("actual_cost_usd"))
            if amount is None:
                unpriced_runs += 1
                unpriced_tokens += tokens
            else:
                confirmed_usd += amount
                priced_runs += 1
        elif status == "estimated":
            usd = _nonnegative_float(run.get("estimated_cost_usd"))
            native = _nonnegative_float(run.get("estimated_cost_native"))
            currency = str(run.get("estimated_cost_currency") or "").upper()
            if usd is not None:
                estimated_usd += usd
                priced_runs += 1
            elif native is not None and currency:
                estimated_native[currency] = round(estimated_native.get(currency, 0.0) + native, 6)
                priced_runs += 1
            else:
                unpriced_runs += 1
                unpriced_tokens += tokens
        else:
            unpriced_runs += 1
            unpriced_tokens += tokens
    return {
        "confirmed_cost_usd": round(confirmed_usd, 6),
        "estimated_cost_usd": round(estimated_usd, 6),
        "estimated_cost_native": estimated_native,
        "priced_runs": priced_runs,
        "unpriced_runs": unpriced_runs,
        "unpriced_token_volume": unpriced_tokens,
    }
