from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.finance_ledger import add_actual, report, sync_forecast


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


if __name__ == "__main__":
    unittest.main()
