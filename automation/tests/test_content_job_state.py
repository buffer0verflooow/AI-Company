"""Regression tests for the content-job lifecycle state machine.

The executor only writes status.json/events.jsonl/progress.json, so jobs
created after the state machine shipped have no lifecycle.json.  read_lifecycle
must derive the machine's initial state from status.json (like
backfill_job_states.py) instead of pinning every fresh job to "pending" where
the documented review → published/archived flow is unreachable.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.content_job_state import read_lifecycle, transition


def _make_job(root: Path, status: str | None = None) -> Path:
    job_dir = root / "job"
    job_dir.mkdir(parents=True, exist_ok=True)
    if status is not None:
        (job_dir / "status.json").write_text(
            json.dumps({"status": status}), encoding="utf-8"
        )
    return job_dir


class ReadLifecycleDerivationTests(unittest.TestCase):
    def test_completed_status_derives_review(self):
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td), "completed")
            self.assertEqual(read_lifecycle(job)["state"], "review")

    def test_needs_approval_status_derives_review(self):
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td), "needs_approval")
            self.assertEqual(read_lifecycle(job)["state"], "review")

    def test_failed_status_derives_terminated(self):
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td), "failed")
            self.assertEqual(read_lifecycle(job)["state"], "terminated")

    def test_running_status_derives_running(self):
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td), "running")
            self.assertEqual(read_lifecycle(job)["state"], "running")

    def test_missing_status_file_derives_pending(self):
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td))
            self.assertEqual(read_lifecycle(job)["state"], "pending")

    def test_existing_lifecycle_takes_precedence(self):
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td), "completed")
            (job / "lifecycle.json").write_text(
                json.dumps({"state": "archived", "history": []}), encoding="utf-8"
            )
            self.assertEqual(read_lifecycle(job)["state"], "archived")


class TransitionFlowTests(unittest.TestCase):
    def test_completed_job_can_be_published(self):
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td), "completed")
            self.assertEqual(transition(job, "published", "pushed draft"), 0)
            self.assertEqual(read_lifecycle(job)["state"], "published")

    def test_corrupt_history_does_not_crash_transition(self):
        # A lifecycle.json with a valid state but a non-list history (corrupt
        # write, hand edit) must not raise AttributeError on transition.
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td), "completed")
            (job / "lifecycle.json").write_text(
                json.dumps({"state": "review", "history": None}), encoding="utf-8"
            )
            self.assertEqual(transition(job, "published", "pushed draft"), 0)
            lc = read_lifecycle(job)
            self.assertEqual(lc["state"], "published")
            self.assertIsInstance(lc["history"], list)

    def test_completed_job_can_be_archived(self):
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td), "completed")
            self.assertEqual(transition(job, "archived", "closed"), 0)
            self.assertEqual(read_lifecycle(job)["state"], "archived")

    def test_failed_job_cannot_be_published(self):
        with tempfile.TemporaryDirectory() as td:
            job = _make_job(Path(td), "failed")
            self.assertNotEqual(transition(job, "published"), 0)
            self.assertEqual(read_lifecycle(job)["state"], "terminated")


if __name__ == "__main__":
    unittest.main()
