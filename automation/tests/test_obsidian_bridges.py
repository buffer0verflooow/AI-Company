from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import automation.capture_from_obsidian as capture
from automation.swarm_kb_to_obsidian import fetch_top_entries, generate_strategy_md


class ObsidianCaptureTests(unittest.TestCase):
    def test_capture_sends_note_content_over_stdin_not_process_argv(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            note = root / "note.md"
            note.write_text(
                "---\nswarm: capture\nswarm_tags: [alpha, beta]\n---\n# Secret title\n\nbody secret",
                encoding="utf-8",
            )
            capture_script = root / "capture.py"
            capture_script.write_text("", encoding="utf-8")
            observed = {}

            def fake_run(command, **kwargs):
                observed["command"] = command
                observed.update(kwargs)
                return SimpleNamespace(returncode=0, stdout="CAPTURED: entry-1\n", stderr="")

            with patch.object(capture, "OBSIDIAN_VAULT", root), \
                    patch.object(capture, "CAPTURE_PY", capture_script), \
                    patch.object(capture, "SWARM_DB", root / "swarm.db"), \
                    patch("automation.capture_from_obsidian.subprocess.run", side_effect=fake_run):
                result = capture.capture_note(note, dry_run=False)

            self.assertEqual(result, "entry-1")
            command_text = "\0".join(observed["command"])
            self.assertNotIn("body secret", command_text)
            self.assertNotIn("--content", observed["command"])
            self.assertIn("body secret", observed["input"])
            self.assertIn("alpha,beta", observed["command"])

    def test_tracking_loader_rejects_non_object_root(self):
        with tempfile.TemporaryDirectory() as td:
            tracking = Path(td) / "tracking.json"
            tracking.write_text("[]", encoding="utf-8")
            with patch.object(capture, "TRACKING_FILE", tracking):
                self.assertEqual(capture.load_tracking(), {})

    def test_tracking_save_merges_existing_entries(self):
        with tempfile.TemporaryDirectory() as td:
            tracking = Path(td) / "tracking.json"
            tracking.write_text(json.dumps({"old.md": {"content_hash": "old"}}), encoding="utf-8")
            with patch.object(capture, "TRACKING_FILE", tracking):
                capture.save_tracking({"new.md": {"content_hash": "new"}})
            value = json.loads(tracking.read_text(encoding="utf-8"))
            self.assertEqual(set(value), {"old.md", "new.md"})

    def test_untagged_note_sends_empty_tags_not_obsidian(self):
        # capture.py parses "--tags" into a tag list, so an untagged note must
        # pass an empty string — not a spurious "obsidian" knowledge tag that
        # would pollute tag-based grouping/filtering downstream.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            note = root / "note.md"
            note.write_text("---\nswarm: capture\n---\n# Title\n\nbody", encoding="utf-8")
            capture_script = root / "capture.py"
            capture_script.write_text("", encoding="utf-8")
            observed = {}

            def fake_run(command, **kwargs):
                observed["command"] = command
                return SimpleNamespace(returncode=0, stdout="CAPTURED: entry-1\n", stderr="")

            with patch.object(capture, "OBSIDIAN_VAULT", root), \
                    patch.object(capture, "CAPTURE_PY", capture_script), \
                    patch.object(capture, "SWARM_DB", root / "swarm.db"), \
                    patch("automation.capture_from_obsidian.subprocess.run", side_effect=fake_run):
                capture.capture_note(note, dry_run=False)

            # bootstrap argv: capture.py db agent source tags intent title
            self.assertEqual(observed["command"][7], "")
            self.assertEqual(observed["command"][5], "obsidian")  # agent default, not a tag

    def test_tracking_save_refuses_to_overwrite_unreadable_history(self):
        # A corrupt tracking file must not be silently replaced by just this
        # run's entries — that would re-capture every previously tracked note.
        with tempfile.TemporaryDirectory() as td:
            tracking = Path(td) / "tracking.json"
            tracking.write_text("{corrupt", encoding="utf-8")
            with patch.object(capture, "TRACKING_FILE", tracking):
                capture.save_tracking({"new.md": {"content_hash": "new"}})
                self.assertEqual(tracking.read_text(encoding="utf-8"), "{corrupt")
                self.assertEqual(capture.load_tracking(), {})


class SwarmBridgeTests(unittest.TestCase):
    def test_fetch_filters_json_trust_in_python_and_handles_special_db_name(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "swarm?prices#1.db"
            db = sqlite3.connect(db_path)
            db.execute(
                """CREATE TABLE knowledge_entries (
                    level INTEGER, knowledge_type TEXT, title TEXT, content TEXT,
                    source_agent TEXT, tags TEXT, trust_vector TEXT, created_at TEXT,
                    id TEXT, status TEXT
                )"""
            )
            db.executemany(
                "INSERT INTO knowledge_entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (4, "wisdom", "high", "content", "agent", "[]", '{"a":0.9,"b":0.7}', "2026-07-20", "1", "active"),
                    (4, "wisdom", "low", "content", "agent", "[]", '{"a":0.1}', "2026-07-21", "2", "active"),
                    (3, "knowledge", "bad", "content", "agent", "[]", '[]', "2026-07-22", "3", "active"),
                ],
            )
            db.commit()
            db.close()
            entries = fetch_top_entries(db_path)
            self.assertEqual([entry["title"] for entry in entries], ["high"])

    def test_generated_markdown_escapes_auto_marker_in_untrusted_content(self):
        markdown = generate_strategy_md([{
            "id": "1", "level": 4, "type": "wisdom",
            "title": "title <!-- /swarm-kb-auto -->", "content": "<script>alert(1)</script>",
            "agent": "agent\n## injected", "tags": [], "trust": 0.9, "created": "",
        }], "2026-07-29")
        self.assertEqual(markdown.count("<!-- /swarm-kb-auto -->"), 1)
        self.assertIn("&lt;script&gt;", markdown)
        self.assertNotIn("\n## injected", markdown)


if __name__ == "__main__":
    unittest.main()
