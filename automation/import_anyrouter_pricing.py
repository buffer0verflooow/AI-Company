#!/usr/bin/env python3
"""Import an AnyRouter saved pricing page into the company model-price ledger."""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from ._safe_io import read_text_limited
    from .finance_ledger import connect
except ImportError:  # direct script execution
    from _safe_io import read_text_limited
    from finance_ledger import connect


COMPANY_ROOT = Path("/home/pwn/workspace/company")
DEFAULT_INPUT = Path("/home/pwn/下载/Any Router.html")
DEFAULT_EVIDENCE = COMPANY_ROOT / "finance/sources/anyrouter-pricing-2026-07-15.html"
DEFAULT_DB = COMPANY_ROOT / "finance/finance_ledger.db"
SOURCE_URL = "https://anyrouter.top/pricing"

ROW_RE = re.compile(
    r'<tr[^>]+data-row-key="(?P<slug>[^"]+)"[^>]*>(?P<body>.*?)</tr>', re.DOTALL | re.IGNORECASE
)
TAG_RE = re.compile(r"<[^>]+>")
INPUT_RE = re.compile(r"Prompt\s*\$([0-9.]+)\s*/\s*1M tokens", re.IGNORECASE)
OUTPUT_RE = re.compile(r"Completion\s*\$([0-9.]+)\s*/\s*1M tokens", re.IGNORECASE)
MODEL_RATIO_RE = re.compile(r"Model ratio[：:]\s*([0-9.]+)", re.IGNORECASE)
COMPLETION_RATIO_RE = re.compile(r"Completion ratio[：:]\s*([0-9.]+)", re.IGNORECASE)
GROUP_RATIO_RE = re.compile(r"Group ratio[：:]\s*([0-9.]+)", re.IGNORECASE)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def text_content(fragment: str) -> str:
    return " ".join(html.unescape(TAG_RE.sub(" ", fragment)).split())


def parse_rows(source: Path) -> list[dict[str, object]]:
    content = read_text_limited(source, max_bytes=50 * 1024 * 1024)
    rows: list[dict[str, object]] = []
    for match in ROW_RE.finditer(content):
        body = text_content(match.group("body"))
        input_match = INPUT_RE.search(body)
        output_match = OUTPUT_RE.search(body)
        if not input_match or not output_match:
            continue
        ratios = []
        for label, pattern in (
            ("model_ratio", MODEL_RATIO_RE),
            ("completion_ratio", COMPLETION_RATIO_RE),
            ("group_ratio", GROUP_RATIO_RE),
        ):
            ratio = pattern.search(body)
            if ratio:
                ratios.append(f"{label}={ratio.group(1)}")
        # The pricing page is external HTML: a malformed figure (e.g. "1.2.3")
        # can still match the [0-9.]+ pattern; skip the row rather than abort
        # the whole import.
        try:
            input_price = float(input_match.group(1))
            output_price = float(output_match.group(1))
        except ValueError:
            continue
        rows.append(
            {
                "slug": match.group("slug"),
                "input": input_price,
                "output": output_price,
                "notes": "Pay as you go; default; " + "; ".join(ratios),
            }
        )
    if not rows:
        raise ValueError("No verifiable pricing rows found")
    return rows


def import_rows(source: Path, evidence: Path, db_path: Path) -> int:
    rows = parse_rows(source)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, evidence)
    evidence_hash = digest(evidence)
    collected_at = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc).isoformat()
    db = connect(db_path)
    try:
        for row in rows:
            db.execute(
                """INSERT INTO model_prices
                   (price_id,provider,model,model_slug,endpoint,currency,unit,
                    input_price,output_price,cache_read_price,cache_write_price,
                    context_tokens,max_output_tokens,source_url,evidence_path,
                    evidence_sha256,collected_at,status,notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(provider,model_slug,currency,source_url) DO UPDATE SET
                    input_price=excluded.input_price,output_price=excluded.output_price,
                    evidence_path=excluded.evidence_path,evidence_sha256=excluded.evidence_sha256,
                    collected_at=excluded.collected_at,status=excluded.status,notes=excluded.notes""",
                (
                    str(uuid.uuid4()), "AnyRouter", row["slug"], row["slug"], row["slug"],
                    "USD", "millionTokens", row["input"], row["output"], None, None,
                    None, None, SOURCE_URL, str(evidence.resolve()), evidence_hash,
                    collected_at, "observed", row["notes"],
                ),
            )
        db.commit()
    finally:
        db.close()
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    count = import_rows(args.input, args.evidence, args.db)
    print(f"imported={count} evidence_sha256={digest(args.evidence)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
