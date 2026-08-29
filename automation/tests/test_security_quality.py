"""Tests for _classify_security_findings in operations_control.py."""

import json

# Add parent to path
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from automation.operations_control import _classify_security_findings


class TestClassifySecurityFindings(unittest.TestCase):

    def _make_log(self, log_dir: Path, run_id: str, content: str) -> Path:
        log = log_dir / f"swarm-{run_id}.log"
        log.write_text(content)
        return log

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.log_dir = self.tmp / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = "test-run-001"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── empty_output cases ──────────────────────────────────────────────

    def test_empty_log_and_no_db_returns_empty_output(self):
        """No log file, no swarm DB → empty_output."""
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
        )
        self.assertEqual(result, "empty_output")

    def test_trivial_empty_log_returns_empty_output(self):
        """Log file exists but is blank → empty_output."""
        self._make_log(self.log_dir, self.run_id, "   \n   ")
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
        )
        self.assertEqual(result, "empty_output")

    def test_short_no_findings_returns_empty_output(self):
        """Short output with 'no findings' pattern → empty_output."""
        self._make_log(
            self.log_dir,
            self.run_id,
            "scan completed. no findings. " + ("a" * 50),
        )
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
            min_finding_tokens=200,
        )
        self.assertEqual(result, "empty_output")

    # ── no_business_value cases ──────────────────────────────────────────

    def test_long_no_findings_returns_no_business_value(self):
        """Long output (>min_finding_tokens) with 'no findings' → no_business_value."""
        body = "scan completed. no findings.\n" + ("details " * 60)
        self._make_log(self.log_dir, self.run_id, body)
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
            min_finding_tokens=200,
        )
        self.assertEqual(result, "no_business_value")

    def test_target_unreachable_returns_no_business_value(self):
        """Output mentioning 'target unreachable' → no_business_value."""
        body = "connection refused - target unreachable\n" + ("retry " * 50)
        self._make_log(self.log_dir, self.run_id, body)
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
            min_finding_tokens=150,
        )
        self.assertEqual(result, "no_business_value")

    def test_chinese_no_vuln_returns_no_business_value(self):
        """Chinese '未发现漏洞' with sufficient length → no_business_value."""
        body = "扫描完成，未发现漏洞。\n" + "详细分析报告 " * 40
        self._make_log(self.log_dir, self.run_id, body)
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
            min_finding_tokens=200,
        )
        self.assertEqual(result, "no_business_value")

    # ── actionable cases ────────────────────────────────────────────────

    def test_xss_finding_returns_actionable(self):
        """Output containing 'XSS' → actionable."""
        self._make_log(self.log_dir, self.run_id, "found reflected XSS in param q\n" + ("x" * 300))
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
        )
        self.assertEqual(result, "actionable")

    def test_sqli_finding_returns_actionable(self):
        """Output containing 'SQL注入' → actionable."""
        self._make_log(
            self.log_dir,
            self.run_id,
            "POST /login 存在 SQL 注入漏洞\n" + ("y" * 300),
        )
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
        )
        self.assertEqual(result, "actionable")

    def test_vulnerability_keyword_returns_actionable(self):
        """Output containing 'vulnerability' → actionable."""
        self._make_log(
            self.log_dir,
            self.run_id,
            "one high-severity vulnerability found\n" + ("z" * 300),
        )
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
        )
        self.assertEqual(result, "actionable")

    # ── finding beats no-finding ───────────────────────────────────────

    def test_finding_overrides_no_finding_pattern(self):
        """Both 'XSS' and 'no findings' → actionable wins."""
        self._make_log(
            self.log_dir,
            self.run_id,
            "scanned endpoints. no findings from port scan, but XSS found in login\n" + ("a" * 300),
        )
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
        )
        self.assertEqual(result, "actionable")

    # ── default: task_counts but no signal ──────────────────────────────

    def test_completed_tasks_no_signal_returns_no_business_value(self):
        """Log with task_counts but no finding/no-finding pattern → no_business_value."""
        body = "analysis report\n" + ("detail " * 40) + "\n"
        body += json.dumps({"task_counts": {"completed": 3, "failed": 0}})
        self._make_log(self.log_dir, self.run_id, body)
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
            min_finding_tokens=200,
        )
        self.assertEqual(result, "no_business_value")

    def test_corrupt_task_counts_does_not_crash(self):
        """Malformed task_counts in arbitrary log lines must not crash the sync."""
        body = "scanning...\n" + ("detail " * 40) + "\n"
        body += json.dumps({"task_counts": "not-a-dict"}) + "\n"
        body += json.dumps({"task_counts": {"completed": "abc"}}) + "\n"
        body += json.dumps({"task_counts": {"completed": [1, 2]}})
        self._make_log(self.log_dir, self.run_id, body)
        result = _classify_security_findings(
            self.run_id,
            swarm_db=self.tmp / "nonexistent.db",
            log_dir=self.log_dir,
            min_finding_tokens=200,
        )
        self.assertEqual(result, "empty_output")


if __name__ == "__main__":
    unittest.main()
