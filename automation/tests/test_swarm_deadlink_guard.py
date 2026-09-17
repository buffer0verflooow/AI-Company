"""D-16 tombstone dead-link + v2 schema guard regression tests (batchCap1).

Covers the capability-audit cleanup (2026-09-16):
  * v2 schema guard rejects missing / tombstoned / non-v2 DBs loudly;
  * each repointed read/write script exits non-zero with a clear message;
  * an empty v2 ``knowledge_entries`` is labelled, never a silent success;
  * the health check is green on a ready v2 and fails on a missing v2;
  * the v1 security-line entry points refuse with the patch pointer.
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import capture_from_obsidian as capture
from automation import knowledge_promotion_gateway as gateway
from automation import swarm_health_check as health
from automation import swarm_kb_to_obsidian as bridge
from automation.company_router import (
    RouteDecision,
    _v1_swarm_db_unavailable,
)
from automation.swarm_db_guard import (
    EMPTY_KB_NOTE,
    SwarmDbUnavailable,
    check_v2_db,
    count_knowledge_entries,
)


def _make_v2_db(path: Path, *, rows=()) -> Path:
    """Create a minimal schema-v2 database (marker tables + knowledge_entries)."""
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE knowledge_entries (
            id TEXT, level INTEGER, knowledge_type TEXT, content TEXT, title TEXT,
            source_agent TEXT, domain TEXT, knowledge_intent TEXT, trust_vector TEXT,
            status TEXT, tags TEXT, created_at TEXT, last_validated_at TEXT
        )"""
    )
    for marker in ("audit_events", "feature_switches", "scheduler_policy", "verdict_registry"):
        db.execute(f"CREATE TABLE {marker} (id INTEGER)")  # nosec B608 -- fixed literals
    # Tables the health check reads for the run/trace probes.
    db.execute("CREATE TABLE swarm_runs (run_id TEXT, swarm_name TEXT, status TEXT, created_at TEXT)")
    db.execute("CREATE TABLE agent_tasks (run_id TEXT, status TEXT, updated_at TEXT)")
    if rows:
        db.executemany(
            "INSERT INTO knowledge_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
    db.commit()
    db.close()
    return path


def _make_v1_shaped_db(path: Path) -> Path:
    """Create a DB that only looks like the (frozen) v1 schema."""
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE knowledge_entries (id TEXT, level INTEGER, status TEXT)")
    db.commit()
    db.close()
    return path


class V2SchemaGuardTests(unittest.TestCase):
    def test_valid_v2_schema_passes(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_v2_db(Path(td) / "v2.db")
            check_v2_db(db)  # must not raise
            self.assertEqual(count_knowledge_entries(db), 0)

    def test_missing_db_raises_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SwarmDbUnavailable) as ctx:
                check_v2_db(Path(td) / "nope.db")
            self.assertIn("缺失", str(ctx.exception))

    def test_tombstone_directory_raises_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            tombstone = Path(td) / "swarm_knowledge.db"
            tombstone.mkdir()
            with self.assertRaises(SwarmDbUnavailable) as ctx:
                check_v2_db(tombstone)
            self.assertIn("墓碑", str(ctx.exception))

    def test_non_v2_schema_raises_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            v1 = _make_v1_shaped_db(Path(td) / "v1.db")
            with self.assertRaises(SwarmDbUnavailable) as ctx:
                check_v2_db(v1)
            self.assertIn("非 v2 schema", str(ctx.exception))


class KbToObsidianTests(unittest.TestCase):
    def _run(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            with patch.object(sys, "argv", ["swarm_kb_to_obsidian.py", *argv]):
                rc = bridge.main()
        return rc, out.getvalue(), err.getvalue()

    def test_missing_db_exits_nonzero_with_reason(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(bridge, "SWARM_DB", Path(td) / "missing.db"):
                rc, _out, err = self._run(["--dry-run"])
            self.assertEqual(rc, 2)
            self.assertIn("v2 swarm KB unavailable", err)

    def test_empty_v2_kb_is_labelled_not_silent(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_v2_db(Path(td) / "v2.db")
            with patch.object(bridge, "SWARM_DB", db):
                rc, _out, err = self._run(["--dry-run"])
            self.assertEqual(rc, 3)
            self.assertIn(EMPTY_KB_NOTE, err)

    def test_populated_v2_kb_syncs(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_v2_db(
                Path(td) / "v2.db",
                rows=[(
                    "k1", 4, "wisdom", "body", "high", "agent", "web", "understand",
                    '{"a":0.9,"b":0.9}', "active", "[]", "2026-09-01", "",
                )],
            )
            with patch.object(bridge, "SWARM_DB", db):
                rc, out, _err = self._run(["--dry-run"])
            self.assertEqual(rc, 0)
            self.assertIn("Would write 1 entries", out)


class PromotionGatewayTests(unittest.TestCase):
    def _run(self, swarm_db: Path, gate_db: Path):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            with patch.object(
                sys,
                "argv",
                ["knowledge_promotion_gateway.py", "--scan", "--swarm-db", str(swarm_db), "--gate-db", str(gate_db)],
            ):
                rc = gateway.main()
        return rc, err.getvalue()

    def test_missing_db_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            rc, err = self._run(Path(td) / "missing.db", Path(td) / "gate.db")
            self.assertEqual(rc, 2)
            self.assertIn("v2 swarm KB unavailable", err)

    def test_empty_v2_kb_is_labelled(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_v2_db(Path(td) / "v2.db")
            rc, err = self._run(db, Path(td) / "gate.db")
            self.assertEqual(rc, 3)
            self.assertIn(EMPTY_KB_NOTE, err)

    def test_populated_v2_kb_scans(self):
        with tempfile.TemporaryDirectory() as td:
            db = _make_v2_db(
                Path(td) / "v2.db",
                rows=[(
                    "k1", 3, "technique", "Generic public lesson.", "Lesson", "agent",
                    "general", "understand",
                    '{"base_confidence":0.9,"cross_validation":1.0}',
                    "active", '["public"]', "2026-09-01", "",
                )],
            )
            gate_db = Path(td) / "gate.db"
            rc, _ = self._run(db, gate_db)
            self.assertEqual(rc, 0)
            self.assertTrue(gate_db.is_file())


class CaptureFromObsidianTests(unittest.TestCase):
    def _run(self, swarm_db: Path):
        with tempfile.TemporaryDirectory() as vault_td:
            vault = Path(vault_td)
            capture_py = vault / "capture.py"
            capture_py.write_text("", encoding="utf-8")
            with patch.object(capture, "OBSIDIAN_VAULT", vault), \
                    patch.object(capture, "CAPTURE_PY", capture_py), \
                    patch.object(capture, "SWARM_DB", swarm_db), \
                    patch.object(sys, "argv", ["capture_from_obsidian.py"]):
                with self.assertRaises(SystemExit) as ctx:
                    capture.main()
            return ctx.exception.code

    def test_missing_db_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self._run(Path(td) / "missing.db"), 2)

    def test_non_v2_schema_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            v1 = _make_v1_shaped_db(Path(td) / "v1.db")
            self.assertEqual(self._run(v1), 2)

    def test_isomorphic_v2_schema_is_usable(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / "note.md").write_text(
                "---\nswarm: capture\n---\n# Title\n\nbody", encoding="utf-8"
            )
            capture_py = vault / "capture.py"
            capture_py.write_text("", encoding="utf-8")
            db = _make_v2_db(vault / "v2.db")
            with patch.object(capture, "OBSIDIAN_VAULT", vault), \
                    patch.object(capture, "CAPTURE_PY", capture_py), \
                    patch.object(capture, "SWARM_DB", db), \
                    patch.object(sys, "argv", ["capture_from_obsidian.py", "--dry-run"]):
                capture.main()  # returns None on a successful dry run
            self.assertTrue(db.is_file())


class SecurityLineGuardTests(unittest.TestCase):
    def _decision(self):
        return RouteDecision(
            action="dispatch_swarm", route="security", confidence=1.0, reason="test",
            intent="analyze", target_type="domain", profile="default", target="",
            authorization_required=False,
        )

    def test_v1_tombstone_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            tombstone = Path(td) / "swarm_knowledge.db"
            tombstone.mkdir()
            reason = _v1_swarm_db_unavailable({"swarm_db": str(tombstone)})
            self.assertIsNotNone(reason)
            self.assertIn("v1 已停用", reason)
            self.assertIn("gray1-wip.patch", reason)

    def test_real_v1_file_passes_guard(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "v1.db"
            _make_v1_shaped_db(db)
            self.assertIsNone(_v1_swarm_db_unavailable({"swarm_db": str(db)}))


class HealthCheckTests(unittest.TestCase):
    REAL_REPO = Path("/home/pwn/workspace/research/swarm-knowledge")

    def setUp(self):
        health.CHECKS.clear()

    def tearDown(self):
        health.CHECKS.clear()

    def _run(self, config: dict):
        import json

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "router_config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()):
                with patch.object(health, "CONFIG_PATH", config_path), \
                        patch.object(sys, "argv", ["swarm_health_check.py"]):
                    rc = health.main()
        return rc, out.getvalue()

    def _config(self, v2_db: Path, v1_db: Path) -> dict:
        return {
            "swarm_repo": str(self.REAL_REPO),
            "swarm_db": str(v1_db),
            "swarm_v2_db": str(v2_db),
        }

    def test_ready_v2_is_zero_anomalies(self):
        with tempfile.TemporaryDirectory() as td:
            v2 = _make_v2_db(Path(td) / "swarm_v2.db")
            v1 = Path(td) / "swarm_knowledge.db"
            v1.mkdir()  # tombstone directory
            rc, out = self._run(self._config(v2, v1))
            self.assertEqual(rc, 0)
            self.assertIn("蜂群接入健康", out)

    def test_missing_v2_fails_loudly(self):
        with tempfile.TemporaryDirectory() as td:
            v1 = Path(td) / "swarm_knowledge.db"
            v1.mkdir()
            rc, out = self._run(self._config(Path(td) / "missing_v2.db", v1))
            self.assertEqual(rc, 1)
            self.assertIn("swarm_v2 活库存在", out)
            self.assertIn("缺失", out)

    def test_non_v2_schema_fails_fingerprint(self):
        with tempfile.TemporaryDirectory() as td:
            v2 = _make_v1_shaped_db(Path(td) / "swarm_v2.db")
            v1 = Path(td) / "swarm_knowledge.db"
            v1.mkdir()
            rc, out = self._run(self._config(v2, v1))
            self.assertEqual(rc, 1)
            self.assertIn("schema 指纹", out)


if __name__ == "__main__":
    unittest.main()
