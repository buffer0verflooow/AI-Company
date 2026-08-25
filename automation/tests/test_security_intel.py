"""Regression tests for the security intel report builder.

CISA's KEV feed is ordered ascending by dateAdded, so the report must sort the
KEV section by date descending before capping the display — otherwise it shows
the oldest (least actionable) entries and silently drops the newest ones.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from automation.security_intel import build_report


def _kev_items(count: int) -> list[dict]:
    # Simulate the ascending dateAdded order of the real CISA KEV feed.
    return [{
        "source": "cisa-kev", "source_title": "CISA KEV", "cat": "漏洞情报",
        "title": f"CVE-2021-{index:04d} | vendor product",
        "url": f"https://nvd.nist.gov/vuln/detail/CVE-2021-{index:04d}",
        "published": f"2021-01-{index + 1:02d}", "summary": "", "authors": "",
    } for index in range(count)]


class KevSectionOrderingTests(unittest.TestCase):
    def test_kev_section_shows_newest_entries_first(self):
        now = datetime.now(timezone.utc)
        report = build_report([("cisa-kev: ok", _kev_items(20))], 0, now)
        section = report.split("## ⚠️ KEV 已利用漏洞")[1].split("## ")[0]
        lines = [line for line in section.splitlines() if line.startswith("- [CVE")]
        self.assertEqual(len(lines), 15)
        # Newest (CVE-2021-0019) first, oldest (CVE-2021-0000) not shown.
        self.assertTrue(lines[0].startswith("- [CVE-2021-0019"))
        self.assertFalse(any("CVE-2021-0000" in line for line in lines))

    def test_kev_header_still_reports_total_count(self):
        now = datetime.now(timezone.utc)
        report = build_report([("cisa-kev: ok", _kev_items(20))], 0, now)
        header = report.split("## ⚠️ KEV 已利用漏洞")[1].splitlines()[0]
        self.assertIn("(20 条", header)


if __name__ == "__main__":
    unittest.main()
