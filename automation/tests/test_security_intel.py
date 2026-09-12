"""Regression tests for the security intel report builder.

CISA's KEV feed is ordered ascending by dateAdded, so the report must sort the
KEV section by date descending before capping the display — otherwise it shows
the oldest (least actionable) entries and silently drops the newest ones.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from automation.security_intel import build_report, parse_cisa_kev


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

    def test_kev_entry_with_missing_or_non_string_date_does_not_crash_report(self):
        # ``published`` is the raw external dateAdded and may be absent/null or
        # numeric; the report must still render the KEV section.
        now = datetime.now(timezone.utc)
        items = _kev_items(0)
        items.append({
            "source": "cisa-kev", "source_title": "CISA KEV", "cat": "漏洞情报",
            "title": "CVE-2026-1 | vendor product",
            "url": "https://nvd.nist.gov/vuln/detail/CVE-2026-1",
            "published": None, "summary": "", "authors": "",
        })
        items.append({
            "source": "cisa-kev", "source_title": "CISA KEV", "cat": "漏洞情报",
            "title": "CVE-2026-2 | vendor product",
            "url": "https://nvd.nist.gov/vuln/detail/CVE-2026-2",
            "published": 20260811, "summary": "", "authors": "",
        })
        report = build_report([("cisa-kev: ok", items)], 0, now)
        self.assertIn("KEV 已利用漏洞", report)
        self.assertIn("CVE-2026-1", report)


class KevParserRobustnessTests(unittest.TestCase):
    """Malformed-but-valid JSON from the external KEV feed must degrade to no
    items instead of raising out of the parser."""

    def _src(self) -> dict:
        return {"id": "cisa-kev", "title": "CISA KEV", "cat": "漏洞情报", "max": 15}

    def test_non_object_root_returns_empty(self):
        now = datetime.now(timezone.utc)
        for body in ('[]', '"text"', '3', 'null', 'true'):
            with self.subTest(body=body):
                self.assertEqual(parse_cisa_kev(body, self._src(), now), [])

    def test_non_list_vulnerabilities_returns_empty(self):
        now = datetime.now(timezone.utc)
        for body in ('{"vulnerabilities": null}', '{"vulnerabilities": {}}',
                     '{"vulnerabilities": "nope"}', '{}'):
            with self.subTest(body=body):
                self.assertEqual(parse_cisa_kev(body, self._src(), now), [])

    def test_non_object_entries_are_skipped(self):
        now = datetime.now(timezone.utc)
        body = '{"vulnerabilities": [1, "bad", null, {"cveID": "CVE-2026-1"}]}'
        items = parse_cisa_kev(body, self._src(), now)
        self.assertEqual(len(items), 1)
        self.assertIn("CVE-2026-1", items[0]["title"])

    def test_invalid_json_returns_empty(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(parse_cisa_kev("<html>not json</html>", self._src(), now), [])


if __name__ == "__main__":
    unittest.main()
