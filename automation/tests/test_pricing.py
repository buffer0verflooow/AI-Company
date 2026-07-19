from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from automation.pricing import (
    cost_rollup,
    estimate_cost,
    load_price_table,
    match_price,
    price_run_update,
)


def _finance_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE model_prices (
            price_id TEXT PRIMARY KEY, provider TEXT, model TEXT, model_slug TEXT,
            endpoint TEXT, currency TEXT, unit TEXT DEFAULT 'millionTokens',
            input_price REAL, output_price REAL, cache_read_price REAL, cache_write_price REAL,
            source_url TEXT, evidence_path TEXT, evidence_sha256 TEXT, collected_at TEXT,
            status TEXT, notes TEXT
        );
        """
    )
    rows = [
        # provider, model_slug, currency, in, out, cache_read, cache_write
        ("zen", "deepseek/deepseek-v4-pro", "USD", 0.435, 0.87, 0.003625, None),
        ("ohmygpt", "deepseek-v4-pro", "CNY", 3.0, 6.0, None, None),
        ("anyrouter", "gpt-5.6-sol", "USD", 2.0, 12.0, None, None),
        ("ohmygpt", "deepseek-chat", "CNY", 1.0, 2.0, None, None),
    ]
    for i, (provider, slug, cur, ip, op, cr, cw) in enumerate(rows):
        db.execute(
            "INSERT INTO model_prices (price_id,provider,model,model_slug,currency,unit,"
            "input_price,output_price,cache_read_price,cache_write_price,source_url,"
            "evidence_path,evidence_sha256,collected_at,status,notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(i), provider, slug, slug, cur, "millionTokens", ip, op, cr, cw,
             "u", "e", "h", "t", "observed", ""),
        )
    db.commit()
    db.close()


class PricingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.finance_db = Path(self._tmp.name) / "finance.db"
        _finance_db(self.finance_db)
        self.table = load_price_table(self.finance_db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_exact_usd_slug_is_priced(self):
        est = estimate_cost(
            "deepseek/deepseek-v4-pro",
            {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_tokens": 1_000_000},
            self.table,
        )
        self.assertEqual(est["cost_status"], "estimated")
        self.assertEqual(est["estimated_cost_currency"], "USD")
        # 0.435 + 0.87 + 0.003625
        self.assertAlmostEqual(est["estimated_cost_usd"], 1.308625, places=6)
        self.assertIn("cache_read_tokens", est["priced_components"])
        self.assertEqual(est["unpriced_components"], [])

    def test_bare_name_prefers_usd_over_cny(self):
        # "deepseek-v4-pro" exists as both ZenMux USD (deepseek/deepseek-v4-pro)
        # and OhMyGPT CNY (deepseek-v4-pro); USD must win for comparability.
        price = match_price("deepseek-v4-pro", self.table)
        self.assertEqual(price["currency"], "USD")
        self.assertEqual(price["provider"], "zen")
        est = estimate_cost("deepseek-v4-pro", {"input_tokens": 1_000_000}, self.table)
        self.assertAlmostEqual(est["estimated_cost_usd"], 0.435, places=6)

    def test_unmatched_model_is_unpriced_not_zero(self):
        est = estimate_cost("mystery-model", {"input_tokens": 100}, self.table)
        self.assertEqual(est["cost_status"], "unpriced")
        self.assertIsNone(est["estimated_cost_usd"])
        self.assertEqual(est["unpriced_components"], ["input_tokens"])

    def test_no_tokens_is_unknown(self):
        est = estimate_cost("deepseek/deepseek-v4-pro", {"input_tokens": 0}, self.table)
        self.assertEqual(est["cost_status"], "unknown")
        self.assertIsNone(est["estimated_cost_usd"])

    def test_absent_price_component_is_flagged_not_assumed_free(self):
        est = estimate_cost(
            "gpt-5.6-sol",
            {"input_tokens": 1_000_000, "cache_read_tokens": 1_000_000},
            self.table,
        )
        self.assertAlmostEqual(est["estimated_cost_usd"], 2.0, places=6)
        self.assertEqual(est["priced_components"], ["input_tokens"])
        self.assertEqual(est["unpriced_components"], ["cache_read_tokens"])

    def test_non_usd_keeps_native_and_no_usd_guess(self):
        est = estimate_cost("deepseek-chat", {"input_tokens": 1_000_000}, self.table)
        self.assertEqual(est["cost_status"], "estimated")
        self.assertEqual(est["estimated_cost_currency"], "CNY")
        self.assertAlmostEqual(est["estimated_cost_native"], 1.0, places=6)
        self.assertIsNone(est["estimated_cost_usd"])  # never invent an FX rate

    def test_actual_cost_is_never_overwritten(self):
        self.assertIsNone(price_run_update(
            "deepseek/deepseek-v4-pro", {"input_tokens": 1_000_000},
            self.table, existing_cost_status="billed",
        ))
        upd = price_run_update(
            "deepseek/deepseek-v4-pro", {"input_tokens": 1_000_000},
            self.table, existing_cost_status="unknown",
        )
        self.assertEqual(upd["cost_status"], "estimated")

    def test_missing_finance_db_degrades_to_unpriced(self):
        table = load_price_table(Path("/nonexistent/finance.db"))
        est = estimate_cost("deepseek/deepseek-v4-pro", {"input_tokens": 100}, table)
        self.assertEqual(est["cost_status"], "unpriced")

    def test_cost_rollup_separates_confirmed_estimated_unpriced(self):
        runs = [
            {"cost_status": "estimated", "estimated_cost_usd": 1.3, "input_tokens": 100},
            {"cost_status": "billed", "actual_cost_usd": 2.0, "input_tokens": 100},
            {"cost_status": "estimated", "estimated_cost_usd": None,
             "estimated_cost_native": 5.0, "estimated_cost_currency": "CNY", "input_tokens": 100},
            {"cost_status": "unknown", "input_tokens": 100},
        ]
        roll = cost_rollup(runs)
        self.assertAlmostEqual(roll["confirmed_cost_usd"], 2.0, places=6)
        self.assertAlmostEqual(roll["estimated_cost_usd"], 1.3, places=6)
        self.assertEqual(roll["estimated_cost_native"], {"CNY": 5.0})
        self.assertEqual(roll["unpriced_runs"], 1)
        self.assertEqual(roll["unpriced_token_volume"], 100)
        self.assertEqual(roll["priced_runs"], 3)


if __name__ == "__main__":
    unittest.main()
