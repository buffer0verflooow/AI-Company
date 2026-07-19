from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from automation.tvcr_daily_review import build_evidence_pack, build_prompt, validate_outputs


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


if __name__ == "__main__":
    unittest.main()
