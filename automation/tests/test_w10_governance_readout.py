"""W10 C-7 跨仓锁:公司侧 v2 治理只读读数(判定/声誉/预算/审计)。

覆盖派工书 §2 的四条断言(全部副本库;只读,不动活库):
  ① 副本库上四类计数与**直接 SQL 查库**真值逐项一致(不是与命令输出比对);
  ② 注入某一类读数命令失败 ⇒ 输出含"不可用"且不显示 0(不静默、不填 0 冒充);
  ③ 快照持久化两次(模拟两天)⇒ 能算出 delta;
  ④ 只读:副本库 sha 前后不变,模块内无写库语句。
外加:并入 `swarm_health_check.main()` 的输出 + 同日快照落盘。
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automation import swarm_governance_readout as ro
from automation import swarm_health_check as health

SWARM_REPO = Path("/home/pwn/workspace/research/swarm-knowledge")
LIVE_SWARM_DB = SWARM_REPO / "swarm_v2.db"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _copy_swarm_db(td: str) -> Path:
    if not LIVE_SWARM_DB.is_file():
        raise unittest.SkipTest("蜂群活库不在本机")
    import shutil

    dst = Path(td) / "swarm_v2.db"
    shutil.copy2(LIVE_SWARM_DB, dst)
    return dst


def _config(td: str, swarm_db: Path) -> dict:
    return {
        "swarm_repo": str(SWARM_REPO),
        "swarm_v2_db": str(swarm_db),
        "state_db": str(Path(td) / "router.db"),
    }


def _rows(db: Path, sql: str, params: tuple = ()) -> list:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return list(con.execute(sql, params))
    finally:
        con.close()


class W10GovernanceReadoutTests(unittest.TestCase):
    def setUp(self):
        if not LIVE_SWARM_DB.is_file():
            self.skipTest("蜂群活库不在本机")

    def test_1_counts_match_direct_sql(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            cfg = _config(td, swarm_db)
            readout = ro.build_readout(cfg)
            cats = readout["categories"]
            for name in ro.CATEGORIES:
                self.assertTrue(cats[name].get("available"),
                                f"{name} 不可用: {cats[name].get('reason')}")

            # 判定:judge_decision accepted 计数 + exec_verify 判据求和。
            judge = [json.loads(r[0]) for r in _rows(
                swarm_db, "SELECT payload_json FROM audit_events"
                          " WHERE event_type='judge_decision'")]
            exp_pass = sum(1 for p in judge if p.get("accepted") is True)
            exp_fail = sum(1 for p in judge if p.get("accepted") is False)
            exec_verify = [json.loads(r[0]) for r in _rows(
                swarm_db, "SELECT payload_json FROM audit_events"
                          " WHERE event_type='exec_verify'")]
            exp_criteria = {k: sum(int(p.get(k, 0) or 0) for p in exec_verify)
                            for k in ("passed", "failed", "refused", "timeout")}
            self.assertEqual(cats["decision"]["judge_decisions"], len(judge))
            self.assertEqual(cats["decision"]["pass"], exp_pass)
            self.assertEqual(cats["decision"]["fail"], exp_fail)
            self.assertEqual(cats["decision"]["criteria"], exp_criteria)
            self.assertEqual(cats["decision"]["refused"], exp_criteria["refused"])
            self.assertEqual(cats["decision"]["timeout"], exp_criteria["timeout"])

            # 声誉:agent 维度与 reputation_events/judge_decision 派生集合一致。
            exp_agents = {r[0] for r in _rows(
                swarm_db, "SELECT DISTINCT agent_id FROM reputation_events")}
            for payload in judge:
                rep = payload.get("reputation")
                if isinstance(rep, dict) and rep.get("agent_id"):
                    exp_agents.add(rep["agent_id"])
            self.assertEqual(cats["reputation"]["agent_count"], len(exp_agents))
            self.assertEqual({a["agent_id"] for a in cats["reputation"]["agents"]},
                             exp_agents)

            # 预算:agent_tasks 聚合 + stop_reason 计数 + run 级预算使用率。
            tasks, measured, estimated = _rows(
                swarm_db, "SELECT COUNT(*), COALESCE(SUM(token_cost),0),"
                          " COALESCE(SUM(estimated_tokens),0) FROM agent_tasks")[0]
            stops = {}
            for row in _rows(swarm_db, "SELECT payload_json FROM audit_events"
                                        " WHERE event_type='agent_trace_close'"):
                reason = json.loads(row[0]).get("stop_reason") or ""
                if reason:
                    stops[reason] = stops.get(reason, 0) + 1
            stats = cats["budget"]
            self.assertEqual(stats["tasks"], tasks)
            self.assertEqual(stats["measured_tokens"], measured)
            self.assertEqual(stats["estimated_tokens"], estimated)
            self.assertEqual(stats["budget_exceeded"], stops.get("budget_exceeded", 0))
            self.assertEqual(stats["max_turns_exceeded"],
                             stops.get("max_turns_exceeded", 0))
            latest_run = _rows(swarm_db, "SELECT run_id FROM swarm_runs"
                                         " ORDER BY created_at DESC, run_id LIMIT 1")[0][0]
            self.assertEqual(stats["run_id"], latest_run)
            self.assertEqual(stats["run_token_budget"],
                             (stats["metrics_run"] or {}).get("budget_execution", {})
                             .get("token_budget"))

            # 审计:按 event_type 计数逐项一致。
            exp_counts = {r[0]: r[1] for r in _rows(
                swarm_db, "SELECT event_type, COUNT(*) FROM audit_events"
                          " GROUP BY event_type")}
            self.assertEqual(cats["audit"]["event_counts"], exp_counts)
            self.assertEqual(cats["audit"]["total"], sum(exp_counts.values()))
            self.assertEqual(cats["audit"]["ops_backfill"],
                             exp_counts.get("ops_backfill", 0))

    def test_2_missing_category_is_loud_and_never_zero(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            cfg = _config(td, swarm_db)
            real = ro.default_runner(cfg)

            def failing(args):
                if args[:2] == ["metrics", "decision"]:
                    raise RuntimeError("injected readout failure")
                return real(args)

            readout = ro.build_readout(cfg, runner=failing)
            decision = readout["categories"]["decision"]
            self.assertFalse(decision["available"])
            self.assertIn("injected readout failure", decision["reason"])
            line = ro.format_category("decision", decision)
            self.assertIn(ro.UNAVAILABLE_MARK, line)
            # 不可用不得显示 0 冒充。
            self.assertNotIn("pass=0", line)
            self.assertNotIn("fail=0", line)
            self.assertIn("decision", readout["unavailable_categories"])
            # 其它类仍可用(不是整份拖垮)。
            self.assertTrue(readout["categories"]["audit"]["available"])

    def test_3_two_day_snapshots_yield_delta(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            cfg = _config(td, swarm_db)
            snapdir = Path(td) / "snapshots"
            readout = ro.build_readout(cfg)
            day_two = copy.deepcopy(readout)
            day_two["categories"]["decision"]["pass"] += 1
            day_two["categories"]["audit"]["total"] += 2
            p1 = ro.persist_snapshot(readout, directory=snapdir, day="2026-01-01")
            p2 = ro.persist_snapshot(day_two, directory=snapdir, day="2026-01-02")
            self.assertEqual(p1.name, "swarm-governance-2026-01-01.json")
            self.assertEqual(p2.name, "swarm-governance-2026-01-02.json")
            snaps = ro.list_snapshots(snapdir)
            self.assertEqual([p.name for p in snaps], [p1.name, p2.name])
            delta = ro.compute_delta(ro.load_snapshot(snaps[0]), ro.load_snapshot(snaps[1]))
            self.assertEqual(delta["decision.pass"]["delta"], 1)
            self.assertEqual(delta["audit.total"]["delta"], 2)
            self.assertNotIn("decision.pass_rate", delta)  # 非数值字段不进 delta

    def test_4_readonly_sha_and_no_write_sql(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            cfg = _config(td, swarm_db)
            before = _sha256(swarm_db)
            ro.build_readout(cfg)
            after = _sha256(swarm_db)
            self.assertEqual(before, after, "治理读数不得改动蜂群(副本)库")
            source = Path(ro.__file__).read_text(encoding="utf-8")
            for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
                         "VACUUM", "REPLACE"):
                self.assertIsNone(re.search(rf"\b{word}\b", source, re.IGNORECASE),
                                  f"只读模块不得含写库关键词 {word}")

    def test_5_health_check_emits_readout_and_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            cfg = _config(td, swarm_db)
            config_path = Path(td) / "router_config.json"
            config_path.write_text(json.dumps(cfg), encoding="utf-8")
            health.CHECKS.clear()
            try:
                with redirect_stdout(io.StringIO()) as out:
                    with patch.object(health, "CONFIG_PATH", config_path), \
                            patch.object(sys, "argv", ["swarm_health_check.py"]):
                        rc = health.main()
            finally:
                health.CHECKS.clear()
            text = out.getvalue()
            self.assertEqual(rc, 0, text)
            self.assertIn("治理-判定:", text)
            self.assertIn("治理-审计:", text)
            snaps = ro.list_snapshots(Path(td) / "governance-snapshots")
            self.assertEqual(len(snaps), 1, snaps)


if __name__ == "__main__":
    unittest.main()
