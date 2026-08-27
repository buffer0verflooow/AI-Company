from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from automation.tvcr_daily_review import (
    _safe_counter,
    build_evidence_pack,
    build_prompt,
    validate_outputs,
)


class TVCRSafeCounterTests(unittest.TestCase):
    """Malformed run rows must not crash the daily TVCR review cron."""

    def test_safe_counter_defaults_and_fallbacks(self):
        self.assertEqual(_safe_counter(None), 0)
        self.assertEqual(_safe_counter(""), 0)
        self.assertEqual(_safe_counter("7"), 7)
        self.assertEqual(_safe_counter(3.9), 3)
        for bad in ("abc", [1], {"a": 1}, float("inf")):
            self.assertEqual(_safe_counter(bad), 0, bad)


class TVCRDailyReviewTests(unittest.TestCase):
    def test_high_tokens_are_signal_not_code_conclusion(self):
        pack = build_evidence_pack(
            [{
                "run_id": "article-1",
                "product_line": "article-production",
                "status": "completed",
                "input_tokens": 150000,
                "output_tokens": 10000,
                "reasoning_tokens": 0,
                "cache_read_tokens": 900000,
                "cache_write_tokens": 0,
                "tool_call_count": 50,
                "outcome_status": "unmeasured",
                "artifacts_json": "[]",
                "evidence_json": "{}",
            }],
            review_day=date(2026, 7, 15),
            period_start="2026-07-14T16:00:00+00:00",
            period_end="2026-07-15T16:00:00+00:00",
            thresholds={"article-production": {"direct_tokens": 120000}},
        )
        self.assertTrue(any(item["kind"] == "resource_threshold_exceeded" for item in pack["signals"]))
        self.assertTrue(any(item["kind"] == "business_outcome_missing" for item in pack["signals"]))
        self.assertIn("不能直接推导为代码问题", pack["signals"][0]["interpretation_rule"])

    def test_prompt_forbids_changes_and_requires_business_options(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            prompt = build_prompt("TVCR-R-1", root / "evidence.json", root / "report.md", root / "proposals.json")
            self.assertTrue(prompt.startswith("[COMPANY_TVCR_INTERNAL]"))
            self.assertIn("不修改任何现有代码", prompt)
            self.assertIn("业务、产品、流程和资源层", prompt)
            self.assertIn("success_metrics", prompt)
            self.assertIn("不超过 1200 个中文字符", prompt)
            self.assertIn("最多 3 个", prompt)

    def test_validator_rejects_unknown_value_as_zero_and_delivery_denial(self):
        evidence = {
            "runs": [{"run_id": "r1", "outcome_status": "unmeasured", "result_delivered": 1}],
        }
        payload = {"proposals": [{
            "title": "x", "success_metrics": [{"metric": "m"}],
            "change_scopes": ["business"], "evidence_run_ids": ["r1"],
        }]}
        errors = validate_outputs(evidence, "产出的实际价值为0，而且没有一件到达用户。", payload)
        self.assertEqual(len(errors), 2)

    def test_validator_rejects_technology_only_proposal(self):
        evidence = {"runs": [{"run_id": "r1", "outcome_status": "measured", "result_delivered": 0}]}
        payload = {"proposals": [{
            "title": "x", "success_metrics": [{"metric": "m"}],
            "change_scopes": ["technology"], "evidence_run_ids": ["r1"],
        }]}
        errors = validate_outputs(evidence, "report", payload)
        self.assertIn("technology-only", errors[0])

    def test_validator_rejects_unknown_cost_as_zero(self):
        evidence = {"runs": [{"run_id": "r1", "outcome_status": "measured", "cost_status": "unknown"}]}
        payload = {"proposals": [{
            "title": "x", "success_metrics": [{"metric": "m"}],
            "change_scopes": ["business"], "evidence_run_ids": ["r1"],
        }]}
        errors = validate_outputs(evidence, "实际模型成本为$0。", payload)
        self.assertIn("unknown model cost", errors[0])

    def test_unmeasured_line_stays_null_not_zero(self):
        # Regression: an all-unmeasured line used to report accepted/published/reach
        # as 0, which read as "business value = 0" and got the review rejected.
        pack = build_evidence_pack(
            [
                {"run_id": "a1", "product_line": "article-production", "status": "completed",
                 "outcome_status": "unmeasured", "artifacts_json": "[]", "evidence_json": "{}"},
                {"run_id": "a2", "product_line": "article-production", "status": "completed",
                 "outcome_status": "unmeasured", "artifacts_json": "[]", "evidence_json": "{}"},
            ],
            review_day=date(2026, 7, 22),
            period_start="2026-07-21T16:00:00+00:00",
            period_end="2026-07-22T16:00:00+00:00",
            thresholds={},
        )
        summary = pack["line_summaries"]["article-production"]
        self.assertIsNone(summary["accepted"])
        self.assertIsNone(summary["published"])
        self.assertIsNone(summary["reach"])
        self.assertEqual(summary["business_outcomes_measured"], 0)

    def test_measured_line_reports_numeric_outcomes(self):
        pack = build_evidence_pack(
            [
                {"run_id": "a1", "product_line": "article-production", "status": "completed",
                 "outcome_status": "measured", "accepted": 1, "published": 1, "reach": 120,
                 "artifacts_json": "[]", "evidence_json": "{}"},
                {"run_id": "a2", "product_line": "article-production", "status": "completed",
                 "outcome_status": "unmeasured", "artifacts_json": "[]", "evidence_json": "{}"},
            ],
            review_day=date(2026, 7, 22),
            period_start="2026-07-21T16:00:00+00:00",
            period_end="2026-07-22T16:00:00+00:00",
            thresholds={},
        )
        summary = pack["line_summaries"]["article-production"]
        self.assertEqual(summary["business_outcomes_measured"], 1)
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["published"], 1)
        self.assertEqual(summary["reach"], 120)

    def test_validator_accepts_prior_proposal_reference(self):
        # A proposal may cite an already-approved prior proposal as evidence
        # (e.g. "confirm it was executed"); that must not be a hard failure.
        evidence = {"runs": [{"run_id": "run-1", "outcome_status": "measured"}]}
        payload = {"proposals": [{
            "title": "确认已批准提案是否执行", "success_metrics": [{"metric": "m"}],
            "change_scopes": ["process"],
            "evidence_run_ids": ["run-1", "TVCR-P-20260715-03", "TVCR-P-20260716-01"],
        }]}
        errors = validate_outputs(
            evidence, "report", payload,
            proposal_ids={"TVCR-P-20260715-03", "TVCR-P-20260716-01"},
        )
        self.assertEqual(errors, [])

    def test_validator_accepts_superseded_proposal_shaped_reference(self):
        # A superseded/pruned proposal is no longer in the id set, but its
        # proposal-shaped id still must not be read as a hallucinated run id.
        evidence = {"runs": [{"run_id": "run-1", "outcome_status": "measured"}]}
        payload = {"proposals": [{
            "title": "x", "success_metrics": [{"metric": "m"}],
            "change_scopes": ["process"], "evidence_run_ids": ["TVCR-P-20251231-09"],
        }]}
        errors = validate_outputs(evidence, "report", payload, proposal_ids=set())
        self.assertEqual(errors, [])

    def test_validator_still_rejects_hallucinated_run_ids(self):
        evidence = {"runs": [{"run_id": "run-1", "outcome_status": "measured"}]}
        payload = {"proposals": [{
            "title": "x", "success_metrics": [{"metric": "m"}],
            "change_scopes": ["process"], "evidence_run_ids": ["run-1", "made-up-run-999"],
        }]}
        errors = validate_outputs(evidence, "report", payload, proposal_ids=set())
        self.assertEqual(len(errors), 1)
        self.assertIn("unknown run ids", errors[0])
        self.assertIn("made-up-run-999", errors[0])

    def test_validator_tolerates_non_list_evidence_ids(self):
        evidence = {"runs": [{"run_id": "run-1", "outcome_status": "measured"}]}
        payload = {"proposals": [{
            "title": "x", "success_metrics": [{"metric": "m"}],
            "change_scopes": ["process"], "evidence_run_ids": None,
        }]}
        errors = validate_outputs(evidence, "report", payload)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
