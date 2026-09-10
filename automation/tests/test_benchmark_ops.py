"""benchmark_ops 纯函数单测(不触网)。"""

import os
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from automation.benchmark_ops import (
    _api,
    classify_submit,
    file_hash,
    find_row,
    first_diff_offset,
    submit_plan,
    summarize,
)

ROW_AVAIL = {
    "unique_code": "g-01",
    "is_completed": False,
    "container_status": "available",
    "total_score": 600,
}
ROW_STOPPED = {
    "unique_code": "g-02",
    "is_completed": False,
    "container_status": "stopped",
    "total_score": 700,
}
ROW_DONE = {
    "unique_code": "g-03",
    "is_completed": True,
    "container_status": "stopped",
    "total_score": 800,
}


class SubmitPlanTests(unittest.TestCase):
    def test_available_submits(self):
        plan = submit_plan(ROW_AVAIL, ["g-01", "g-09"])
        self.assertEqual(plan["action"], "submit")

    def test_stopped_needs_start(self):
        plan = submit_plan(ROW_STOPPED, ["g-09"])
        self.assertEqual(plan["action"], "need_start")

    def test_max_active_blocks(self):
        plan = submit_plan(ROW_STOPPED, ["g-09", "g-10", "g-11"])
        self.assertEqual(plan["action"], "max_active")

    def test_not_found(self):
        plan = submit_plan(None, [])
        self.assertEqual(plan["action"], "not_found")

    def test_already_complete(self):
        plan = submit_plan(ROW_DONE, [])
        self.assertEqual(plan["action"], "already_complete")


class SummaryTests(unittest.TestCase):
    def test_summarize_counts(self):
        done, total, score = summarize([ROW_AVAIL, ROW_STOPPED, ROW_DONE])
        self.assertEqual((done, total, score), (1, 3, 800))

    def test_find_row(self):
        rows = [ROW_AVAIL, ROW_DONE]
        found = find_row(rows, "g-03")
        self.assertIsNotNone(found)
        assert found is not None  # 类型收窄
        self.assertEqual(found["unique_code"], "g-03")
        self.assertIsNone(find_row(rows, "g-99"))


class ClassifyTests(unittest.TestCase):
    def test_correct_true(self):
        out = classify_submit(
            {"correct": True, "awarded": 500, "cumulative_score": 500,
             "correct_flag_count": 1, "total_flag_count": 1}
        )
        self.assertIn("correct:true", out)
        self.assertIn("awarded=500", out)

    def test_correct_false_includes_env_hint(self):
        out = classify_submit(
            {"correct": False, "awarded": 0, "correct_flag_count": 0,
             "total_flag_count": 1}
        )
        self.assertIn("correct:false", out)
        self.assertIn("禁止直接归因", out)  # 环境先查,再归因内容

    def test_duplicate(self):
        out = classify_submit({"http": 409, "code": "duplicate"})
        self.assertIn("duplicate", out)


class ApiBodyParsingTests(unittest.TestCase):
    def test_http_error_with_array_body_does_not_crash(self):
        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("http://x", 400, "bad", {}, None)

            def read(self):
                return b"[1, 2]"

        with patch("automation.benchmark_ops.urllib.request.urlopen", side_effect=FakeHTTPError()):
            resp = _api("GET", "", "token")
        self.assertEqual(resp["http"], 400)
        self.assertIn("message", resp)

    def test_non_json_success_body_does_not_crash(self):
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"<html>not json</html>"

        with patch("automation.benchmark_ops.urllib.request.urlopen", return_value=FakeResponse()):
            resp = _api("GET", "", "token")
        self.assertEqual(resp["http"], 200)
        self.assertIn("message", resp)


class DiffTests(unittest.TestCase):
    def _write(self, d, name, content):
        p = os.path.join(d, name)
        with open(p, "wb") as fh:
            fh.write(content)
        return p

    def test_identical_files(self):
        with tempfile.TemporaryDirectory() as d:
            a = self._write(d, "a.bin", b"hello world" * 100)
            b = self._write(d, "b.bin", b"hello world" * 100)
            ha, sa, _ = file_hash(a)
            hb, sb, _ = file_hash(b)
            self.assertEqual(ha, hb)
            self.assertEqual(sa, sb)
            self.assertIsNone(first_diff_offset(a, b))

    def test_first_diff_offset(self):
        with tempfile.TemporaryDirectory() as d:
            a = self._write(d, "a.bin", b"A" * 4096 + b"B" * 100)
            b = self._write(d, "b.bin", b"A" * 4096 + b"C" * 100)
            self.assertEqual(first_diff_offset(a, b), 4096)

    def test_length_difference(self):
        with tempfile.TemporaryDirectory() as d:
            a = self._write(d, "a.bin", b"X" * 64)
            b = self._write(d, "b.bin", b"X" * 32)
            self.assertIsNotNone(first_diff_offset(a, b))

    def test_hash_reports_size(self):
        with tempfile.TemporaryDirectory() as d:
            a = self._write(d, "a.bin", b"abc")
            digest, size, _ = file_hash(a)
            self.assertEqual(size, 3)
            self.assertEqual(
                digest,
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )


if __name__ == "__main__":
    unittest.main()
