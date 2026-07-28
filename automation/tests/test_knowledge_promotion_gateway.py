from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from automation.knowledge_promotion_gateway import approve, connect_gate, list_candidates, promote, scan


class KnowledgePromotionTests(unittest.TestCase):
    def _source(self, path: Path, rows):
        db = sqlite3.connect(path)
        db.execute("""CREATE TABLE knowledge_entries (
            id TEXT PRIMARY KEY, level INTEGER, knowledge_type TEXT, content TEXT,
            title TEXT, domain TEXT, knowledge_intent TEXT, trust_vector TEXT,
            status TEXT, tags TEXT, last_validated_at TEXT
        )""")
        db.executemany("INSERT INTO knowledge_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.commit()
        db.close()

    def test_sensitive_target_knowledge_is_blocked_without_copying_raw_content(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "swarm.db"
            gate = Path(td) / "gate.db"
            raw = "PoC for https://target.example exploit token=secret-value"
            self._source(source, [(
                "k1", 3, "vulnerability", raw, "Target PoC", "web", "attack",
                json.dumps({"base_confidence": 0.9, "cross_validation": 1.0}),
                "active", json.dumps(["public"]), "2026-07-15",
            )])
            counts = scan(source, gate)
            self.assertEqual(counts["blocked_sensitive"], 1)
            row = list_candidates(gate)[0]
            self.assertNotIn("secret-value", row["sanitized_preview"])
            db = sqlite3.connect(gate)
            schema_text = " ".join(r[1] for r in db.execute("PRAGMA table_info(promotion_candidates)"))
            self.assertNotIn("raw_content", schema_text)
            db.close()

    def test_public_validated_generic_knowledge_requires_human_approval(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "swarm.db"
            gate = Path(td) / "gate.db"
            wiki = Path(td) / "wiki"
            self._source(source, [(
                "k2", 3, "technique", "Use evidence gates before synthesis.", "Evidence workflow",
                "general", "understand", json.dumps({"base_confidence": 0.9, "cross_validation": 1.0}),
                "active", json.dumps(["public"]), "2026-07-15",
            )])
            scan(source, gate)
            candidate = list_candidates(gate)[0]
            self.assertEqual(candidate["status"], "pending_approval")
            with self.assertRaises(ValueError):
                promote(gate, candidate["candidate_id"], wiki)
            reviewed = Path(td) / "reviewed.md"
            reviewed.write_text("# Evidence workflow\n\nUse evidence gates before synthesis.", encoding="utf-8")
            approve(gate, candidate["candidate_id"], "reviewer", reviewed, "public")
            output = promote(gate, candidate["candidate_id"], wiki)
            self.assertTrue(output.exists())
            self.assertIn("approved_by: reviewer", output.read_text(encoding="utf-8"))

    def test_security_topic_reaches_disclosure_gate_and_accepts_clean_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "swarm.db"
            gate = Path(td) / "gate.db"
            self._source(source, [(
                "k3", 3, "vulnerability", "Internal network SMB exploit", "Sensitive finding",
                "security", "attack", json.dumps({"base_confidence": 0.9, "cross_validation": 1.0}),
                "active", json.dumps([]), "2026-07-15",
            )])
            scan(source, gate)
            candidate = list_candidates(gate)[0]
            self.assertEqual(candidate["status"], "needs_disclosure")
            self.assertEqual(candidate["sensitivity_status"], "topic_needs_review")
            reviewed = Path(td) / "reviewed.md"
            reviewed.write_text("# General lesson\n\nUse layered patch management and evidence-based validation.", encoding="utf-8")
            with self.assertRaises(ValueError):
                approve(gate, candidate["candidate_id"], "reviewer", reviewed, "unknown")
            approve(gate, candidate["candidate_id"], "reviewer", reviewed, "public")
            self.assertEqual(list_candidates(gate)[0]["status"], "approved")

    def test_code_filenames_are_not_misclassified_as_live_domains(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "swarm.db"
            gate = Path(td) / "gate.db"
            self._source(source, [(
                "k4", 3, "technique", "Review main.py and config.json before release.",
                "Code review", "engineering", "understand",
                json.dumps({"base_confidence": 0.9, "cross_validation": 1.0}),
                "active", json.dumps(["public"]), "2026-07-15",
            )])
            scan(source, gate)
            candidate = list_candidates(gate)[0]
            self.assertEqual(candidate["status"], "pending_approval")
            self.assertEqual(candidate["sensitivity_status"], "passed")

    def test_approve_still_rejects_concrete_leaks(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "swarm.db"
            gate = Path(td) / "gate.db"
            self._source(source, [(
                "k5", 3, "technique", "Generic public lesson.", "Lesson", "general", "understand",
                json.dumps({"base_confidence": 0.9, "cross_validation": 1.0}),
                "active", json.dumps(["public"]), "2026-07-15",
            )])
            scan(source, gate)
            candidate = list_candidates(gate)[0]
            reviewed = Path(td) / "reviewed.md"
            reviewed.write_text("Contact analyst@example.com with token=secret-value", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must-redact"):
                approve(gate, candidate["candidate_id"], "reviewer", reviewed, "public")

    def test_promote_rejects_content_tampered_after_approval(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "swarm.db"
            gate = Path(td) / "gate.db"
            wiki = Path(td) / "wiki"
            self._source(source, [(
                "k6", 3, "technique", "Generic public lesson.", "Lesson", "general", "understand",
                json.dumps({"base_confidence": 0.9, "cross_validation": 1.0}),
                "active", json.dumps(["public"]), "2026-07-15",
            )])
            scan(source, gate)
            candidate = list_candidates(gate)[0]
            reviewed = Path(td) / "reviewed.md"
            reviewed.write_text("# Reviewed lesson", encoding="utf-8")
            approve(gate, candidate["candidate_id"], "reviewer", reviewed, "public")
            db = connect_gate(gate)
            db.execute(
                "UPDATE promotion_candidates SET reviewed_content='tampered' WHERE candidate_id=?",
                (candidate["candidate_id"],),
            )
            db.commit()
            db.close()
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                promote(gate, candidate["candidate_id"], wiki)

    def test_malformed_trust_and_tags_do_not_crash_or_grant_disclosure(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "swarm.db"
            gate = Path(td) / "gate.db"
            self._source(source, [(
                "k7", 3, "technique", "Generic lesson.", "Lesson", "general", "understand",
                "[]", "active", json.dumps({"public": True}), "2026-07-15",
            )])
            counts = scan(source, gate)
            self.assertEqual(counts["needs_validation"], 1)
            candidate = list_candidates(gate)[0]
            self.assertEqual(candidate["disclosure_status"], "unknown")

    def test_frontmatter_quotes_reviewer_with_newline(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "swarm.db"
            gate = Path(td) / "gate.db"
            wiki = Path(td) / "wiki"
            self._source(source, [(
                "k8", 3, "technique", "Generic lesson.", "Lesson", "general", "understand",
                json.dumps({"base_confidence": 0.9, "cross_validation": 1.0}),
                "active", json.dumps(["public"]), "2026-07-15",
            )])
            scan(source, gate)
            candidate = list_candidates(gate)[0]
            reviewed = Path(td) / "reviewed.md"
            reviewed.write_text("# Reviewed lesson", encoding="utf-8")
            approve(gate, candidate["candidate_id"], "reviewer\nstatus: hacked", reviewed, "public")
            text = promote(gate, candidate["candidate_id"], wiki).read_text(encoding="utf-8")
            self.assertNotIn("\nstatus: hacked\n", text)
            self.assertIn('approved_by: "reviewer\\nstatus: hacked"', text)


if __name__ == "__main__":
    unittest.main()
