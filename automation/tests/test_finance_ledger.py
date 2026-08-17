from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from automation.finance_ledger import (
    add_actual,
    connect,
    report,
    sync_forecast,
    sync_snapshot,
)


class FinanceLedgerTests(unittest.TestCase):
    def test_forecast_never_counts_as_actual_revenue(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ledger.db"
            source = Path(td) / "SUBMISSIONS_INDEX.md"
            source.write_text("**总赏金**: $10,850 - $30,150", encoding="utf-8")
            self.assertTrue(sync_forecast(db, source))
            result = report(db)
            self.assertTrue(result["actual_revenue_is_zero"])
            self.assertEqual(result["forecasts_excluded_from_actual"][0]["min_amount"], 10850.0)

    def test_actual_transaction_requires_and_hashes_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ledger.db"
            with self.assertRaises(ValueError):
                add_actual(
                    db, product_line="security", kind="revenue", category="bounty",
                    amount=100, currency="USD", description="", source_ref="h1-report",
                    evidence_path=Path(td) / "missing.pdf", occurred_at="2026-07-15",
                )
            evidence = Path(td) / "receipt.txt"
            evidence.write_text("provider-confirmed payment receipt", encoding="utf-8")
            add_actual(
                db, product_line="security", kind="revenue", category="bounty",
                amount=100, currency="USD", description="confirmed", source_ref="h1-report",
                evidence_path=evidence, occurred_at="2026-07-15",
            )
            result = report(db)
            self.assertFalse(result["actual_revenue_is_zero"])
            self.assertEqual(result["actual"][0]["amount"], 100.0)

    def test_actual_transaction_rejects_nonfinite_amounts(self):
        with tempfile.TemporaryDirectory() as td:
            evidence = Path(td) / "receipt.txt"
            evidence.write_text("receipt", encoding="utf-8")
            for amount in (float("nan"), float("inf"), float("-inf"), -1.0):
                with self.subTest(amount=amount), self.assertRaises(ValueError):
                    add_actual(
                        Path(td) / "ledger.db", product_line="security", kind="revenue",
                        category="bounty", amount=amount, currency="USD", description="",
                        source_ref="run-1", evidence_path=evidence, occurred_at="2026-07-29",
                    )


class SessionPricingSnapshotTests(unittest.TestCase):
    def _seed_price(self, db_path: Path) -> None:
        db = connect(db_path)
        db.execute(
            """INSERT INTO model_prices
               (price_id,provider,model,model_slug,endpoint,currency,unit,input_price,
                output_price,cache_read_price,cache_write_price,source_url,evidence_path,
                evidence_sha256,collected_at)
               VALUES ('p1','ZenMux','DeepSeek V4 Flash','deepseek/deepseek-v4-flash',
                       'chat','USD','millionTokens',0.14,0.28,0.0028,NULL,
                       'https://example/pricing','/tmp/ev.json','deadbeef','2026-07-15')""",
        )
        db.commit()
        db.close()

    def _hermes_db(self, path: Path) -> None:
        db = sqlite3.connect(path)
        db.execute(
            """CREATE TABLE sessions (
                id TEXT PRIMARY KEY, model TEXT, cost_status TEXT,
                estimated_cost_usd REAL, input_tokens INTEGER, output_tokens INTEGER,
                cache_read_tokens INTEGER, cache_write_tokens INTEGER)"""
        )
        # Priceable deepseek session (1M input, 1M output) -> 0.14 + 0.28 = 0.42 USD.
        db.execute("INSERT INTO sessions VALUES ('s1','deepseek/deepseek-v4-flash','unknown',NULL,1000000,1000000,0,0)")
        # Bare-slug variant resolves via the provider-stripped base name.
        db.execute("INSERT INTO sessions VALUES ('s2','deepseek-v4-flash','estimated',NULL,1000000,0,0,0)")
        # Unknown model has no matching price -> stays unpriced (not $0).
        db.execute("INSERT INTO sessions VALUES ('s3','glm-5.2','unknown',NULL,500000,500000,0,0)")
        # Provider-confirmed session contributes its recorded cost as confirmed.
        db.execute("INSERT INTO sessions VALUES ('s4','deepseek/deepseek-v4-flash','billed',2.5,10,10,0,0)")
        db.commit()
        db.close()

    def test_snapshot_prices_unpriced_sessions_from_model_prices(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger.db"
            hermes = Path(td) / "hermes.db"
            router = Path(td) / "router.db"  # absent -> zero route counts, fine
            self._seed_price(ledger)
            self._hermes_db(hermes)

            sync_snapshot(ledger, router, hermes)
            snap = report(ledger)["latest_usage_snapshot"]

            self.assertEqual(snap["priced_sessions"], 3)      # s1, s2, s4
            self.assertEqual(snap["unpriced_sessions"], 1)    # s3 only
            self.assertAlmostEqual(snap["estimated_cost_usd"], 0.42 + 0.14, places=6)
            self.assertAlmostEqual(snap["confirmed_cost_usd"], 2.5, places=6)
            # actual_cost_usd is the real measured total: confirmed + evidence-priced.
            self.assertAlmostEqual(snap["actual_cost_usd"], 2.5 + 0.56, places=6)

    def test_missing_price_table_keeps_everything_unpriced(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "ledger.db"
            hermes = Path(td) / "hermes.db"
            router = Path(td) / "router.db"
            connect(ledger).close()  # empty model_prices
            self._hermes_db(hermes)

            sync_snapshot(ledger, router, hermes)
            snap = report(ledger)["latest_usage_snapshot"]
            self.assertEqual(snap["estimated_cost_usd"], 0.0)
            self.assertEqual(snap["unpriced_sessions"], 3)    # s1, s2, s3
            self.assertAlmostEqual(snap["confirmed_cost_usd"], 2.5, places=6)


if __name__ == "__main__":
    unittest.main()
