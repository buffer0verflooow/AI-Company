"""W10 C-4 跨仓锁:公司侧账本回填接线(v2 run 收口 ⇒ 公司账本 ⇒ 蜂群回填)。

覆盖派工书 §1 的五条断言(全部副本库排练,不动活库、不开活库开关):
  ① 开关开 ⇒ 公司 `actual_transactions` 1 行 + 蜂群 `ops_backfill` 审计 1 条
     (含 ledger_ref/amount/unit/b_delta),且 evidence_path 指向真实文件;
  ② 开关关 ⇒ 零账本写入 + 响亮失败 + 待回填登记可重放(开后只补一次);
  ③ 同一 task 连跑 3 次 ⇒ 账本仍 1 行、审计仍 1 条;
  ④ 金额 = tokens × 价格(定点算例逐字对拍);
  ⑤ 缺价格 ⇒ 标"未定价"且不发回填请求(不编数、零账本/零审计);
外加一条:既有 v2 结果接收路径(`company_result_notifier.process_once`)真的驱动 C-4。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from automation import finance_ledger
from automation import swarm_ledger_backfill as sbf
from automation.company_result_notifier import process_once

SWARM_REPO = Path("/home/pwn/workspace/research/swarm-knowledge")
LIVE_SWARM_DB = SWARM_REPO / "swarm_v2.db"
TASK_ID = "t-w10-c4"
RUN_ID = "r-w10-c4"

MODEL_PRICE_ROWS = {
    "W10Prov": ("w10-model", 2.0),
    "NoPriceProv": ("no-price-model", None),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _copy_swarm_db(td: str) -> Path:
    if not LIVE_SWARM_DB.is_file():
        raise unittest.SkipTest("蜂群活库不在本机")
    dst = Path(td) / "swarm_v2.db"
    shutil.copy2(LIVE_SWARM_DB, dst)
    wal = Path(str(LIVE_SWARM_DB) + "-wal")
    if wal.is_file() and wal.stat().st_size:
        shutil.copy2(wal, Path(str(dst) + "-wal"))
    for sidecar in (Path(str(dst) + "-shm"),):
        if sidecar.exists():
            sidecar.unlink()
    return dst


def _seed_swarm(swarm_db: Path, *, switch_on: bool, token_cost: int = 1_000_000,
                provider: str = "W10Prov", task_id: str = TASK_ID,
                run_id: str = RUN_ID) -> None:
    con = sqlite3.connect(swarm_db)
    try:
        con.execute(
            "UPDATE feature_switches SET enabled=? WHERE switch_name='company_backfill'",
            (1 if switch_on else 0,))
        con.execute("UPDATE scheduler_policy SET policy='market'")
        con.execute(
            "INSERT OR REPLACE INTO value_param_packs(id,run_type,w_q,u,k,b,active,created_at)"
            " VALUES (9901,'ops',0.5,0.5,0.5,0.5,1,'2026-01-01 00:00:00')")
        con.execute(
            "INSERT OR REPLACE INTO model_profiles(profile_id,role,provider,model)"
            " VALUES ('mp-w10','executor',?,?)",
            (provider, MODEL_PRICE_ROWS[provider][0]))
        con.execute(
            "INSERT OR REPLACE INTO swarm_runs(run_id,swarm_name,intent,target_type,"
            "target_id,status,ended_at,updated_at,run_type) VALUES (?,?,'analyze',"
            "'unknown','w10-target','completed',datetime('now'),datetime('now'),'ops')",
            (run_id, f"w10-{run_id}"))
        con.execute(
            """INSERT OR REPLACE INTO agent_tasks
               (task_id,run_id,task_type,run_type,status,acceptance_status,token_cost,
                estimated_tokens,value_pack_id,model_profile_id,published_by,market_source,
                funding_source,publisher_client_ref,ended_at)
               VALUES (?,?,'analyze','ops','completed','accepted',?,1200,9901,'mp-w10',
                       'client','market','run','client:0123456789ab','2026-01-02 00:00:00')""",
            (task_id, run_id, token_cost))
        con.commit()
    finally:
        con.close()


def _make_ledger(path: Path, *, with_price: bool = True) -> None:
    db = finance_ledger.connect(path)
    try:
        if with_price:
            db.execute(
                "INSERT OR REPLACE INTO model_prices(price_id,provider,model,model_slug,"
                "currency,unit,input_price,output_price,source_url,evidence_path,"
                "evidence_sha256,collected_at,status)"
                " VALUES ('w10p','W10Prov','W10 Model','w10-model','USD','millionTokens',"
                "1.0,2.0,'http://x','/x',?,'2026-01-01','observed')",
                ("0" * 64,))
        db.commit()
    finally:
        db.close()


def _config(td: str, swarm_db: Path, ledger_db: Path) -> dict:
    return {
        "swarm_repo": str(SWARM_REPO),
        "swarm_v2_db": str(swarm_db),
        "finance_ledger_db": str(ledger_db),
        "state_db": str(Path(td) / "router.db"),
    }


def _ledger_rows(ledger_db: Path) -> list[dict]:
    con = sqlite3.connect(ledger_db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM actual_transactions")]
    finally:
        con.close()


def _ops_backfills(swarm_db: Path) -> list[dict]:
    con = sqlite3.connect(swarm_db)
    con.row_factory = sqlite3.Row
    try:
        return [json.loads(r[0]) for r in con.execute(
            "SELECT payload_json FROM audit_events WHERE event_type='ops_backfill'")]
    finally:
        con.close()


def _b_settlements(swarm_db: Path) -> list[tuple]:
    con = sqlite3.connect(swarm_db)
    try:
        return list(con.execute(
            "SELECT task_id, component, delta, evidence_ref FROM value_settlements"
            " WHERE component='b'"))
    finally:
        con.close()


class W10LedgerBackfillTests(unittest.TestCase):
    def setUp(self):
        if not LIVE_SWARM_DB.is_file():
            self.skipTest("蜂群活库不在本机")

    def test_1_switch_on_writes_ledger_and_backfill_audit(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            _seed_swarm(swarm_db, switch_on=True)
            ledger_db = Path(td) / "finance_ledger.db"
            _make_ledger(ledger_db)
            cfg = _config(td, swarm_db, ledger_db)

            result = sbf.process_task(cfg, TASK_ID)
            self.assertEqual(result["action"], "done", result)
            self.assertEqual(result["amount"], 2.0)
            self.assertEqual(result["currency"], "USD")

            rows = _ledger_rows(ledger_db)
            self.assertEqual(len(rows), 1, rows)
            row = rows[0]
            self.assertEqual(row["source_ref"], f"swarm:{RUN_ID}/{TASK_ID}")
            self.assertEqual(row["amount"], 2.0)
            self.assertEqual(row["kind"], "expense")
            self.assertTrue(Path(row["evidence_path"]).is_file(),
                            "evidence_path 必须是真实文件")
            self.assertEqual(row["evidence_sha256"], _sha256(Path(row["evidence_path"])))

            audits = _ops_backfills(swarm_db)
            self.assertEqual(len(audits), 1, audits)
            payload = audits[0]
            self.assertEqual(payload["ledger_ref"], f"swarm:{RUN_ID}/{TASK_ID}")
            self.assertEqual(payload["amount"], 2.0)
            self.assertEqual(payload["unit"], "USD")
            self.assertEqual(payload["b_delta"], 1.0)
            self.assertEqual(_b_settlements(swarm_db),
                             [(TASK_ID, "b", 1.0, f"ledger:swarm:{RUN_ID}/{TASK_ID}")])

    def test_2_switch_off_is_loud_and_replayable_exactly_once(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            _seed_swarm(swarm_db, switch_on=False)
            ledger_db = Path(td) / "finance_ledger.db"
            _make_ledger(ledger_db)
            cfg = _config(td, swarm_db, ledger_db)

            result = sbf.process_task(cfg, TASK_ID)
            self.assertEqual(result["action"], "pending", result)
            self.assertEqual(result["swarm_rc"], 2)
            self.assertIn("company_backfill", result["reason"])
            # 零账本写入 + 零审计(只有待补登记)。
            self.assertEqual(_ledger_rows(ledger_db), [])
            self.assertEqual(_ops_backfills(swarm_db), [])
            pending = sbf.PendingStore(Path(cfg["state_db"]).parent
                                       / "swarm_backfill_pending.db").get(TASK_ID)
            self.assertIsNotNone(pending)
            self.assertEqual(pending["status"], "pending")
            self.assertEqual(pending["attempts"], 1)

            # 开闸后重放 ⇒ 补上且只补一次。
            con = sqlite3.connect(swarm_db)
            con.execute("UPDATE feature_switches SET enabled=1"
                        " WHERE switch_name='company_backfill'")
            con.commit()
            con.close()
            first = sbf.replay_pending(cfg)
            self.assertEqual(first["done"], 1, first)
            self.assertEqual(len(_ledger_rows(ledger_db)), 1)
            self.assertEqual(len(_ops_backfills(swarm_db)), 1)
            second = sbf.replay_pending(cfg)
            self.assertEqual(second["replayed"], 0, second)
            self.assertEqual(len(_ledger_rows(ledger_db)), 1)
            self.assertEqual(len(_ops_backfills(swarm_db)), 1)

    def test_3_idempotent_three_runs(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            _seed_swarm(swarm_db, switch_on=True)
            ledger_db = Path(td) / "finance_ledger.db"
            _make_ledger(ledger_db)
            cfg = _config(td, swarm_db, ledger_db)

            first = sbf.process_task(cfg, TASK_ID)
            second = sbf.process_task(cfg, TASK_ID)
            third = sbf.process_task(cfg, TASK_ID)
            self.assertFalse(first.get("duplicate"))
            self.assertTrue(second.get("duplicate"))
            self.assertTrue(third.get("duplicate"))
            self.assertEqual(len(_ledger_rows(ledger_db)), 1)
            self.assertEqual(len(_ops_backfills(swarm_db)), 1)

    def test_4_amount_is_tokens_times_price_exact(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            _seed_swarm(swarm_db, switch_on=True, token_cost=1_000_000)
            ledger_db = Path(td) / "finance_ledger.db"
            _make_ledger(ledger_db)
            cfg = _config(td, swarm_db, ledger_db)

            result = sbf.process_task(cfg, TASK_ID)
            plan = result["plan"]
            self.assertTrue(plan["priced"])
            self.assertEqual(plan["tokens"], 1_000_000)
            self.assertEqual(plan["price"]["price_value"], 2.0)
            self.assertEqual(plan["price"]["price_component"], "output")
            # 1_000_000 tokens × 2.0 USD / 1e6 = 2.0 USD;b=0.5 ⇒ ΔV=1.0。
            self.assertEqual(plan["amount"], 2.0)
            self.assertEqual(result["amount"], 2.0)
            self.assertEqual(_ledger_rows(ledger_db)[0]["amount"], 2.0)
            self.assertEqual(_ops_backfills(swarm_db)[0]["b_delta"], 1.0)

    def test_5_missing_price_is_unpriced_never_fabricated(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            _seed_swarm(swarm_db, switch_on=True, provider="NoPriceProv")
            ledger_db = Path(td) / "finance_ledger.db"
            _make_ledger(ledger_db, with_price=False)
            cfg = _config(td, swarm_db, ledger_db)

            result = sbf.process_task(cfg, TASK_ID)
            self.assertEqual(result["action"], "unpriced", result)
            self.assertFalse(result["priced"])
            self.assertIn("model_prices", result["reason"])
            # 不发回填请求:零账本、零审计。
            self.assertEqual(_ledger_rows(ledger_db), [])
            self.assertEqual(_ops_backfills(swarm_db), [])
            pending = sbf.PendingStore(Path(cfg["state_db"]).parent
                                       / "swarm_backfill_pending.db").get(TASK_ID)
            self.assertEqual(pending["status"], "unpriced")

    def test_6_notifier_tick_drives_backfill(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            _seed_swarm(swarm_db, switch_on=True)
            ledger_db = Path(td) / "finance_ledger.db"
            _make_ledger(ledger_db)
            cfg = _config(td, swarm_db, ledger_db)
            cfg.update({"proactive_delivery": True, "state_db": str(Path(td) / "router.db")})
            summary = process_once(cfg)
            self.assertGreaterEqual(summary["backfill_done"], 1, summary)
            self.assertEqual(len(_ledger_rows(ledger_db)), 1)
            self.assertEqual(len(_ops_backfills(swarm_db)), 1)

    def test_7_operator_model_fallback_is_traceable(self):
        with tempfile.TemporaryDirectory() as td:
            swarm_db = _copy_swarm_db(td)
            _seed_swarm(swarm_db, switch_on=True)
            con = sqlite3.connect(swarm_db)
            con.execute("UPDATE agent_tasks SET model_profile_id=NULL WHERE task_id=?",
                        (TASK_ID,))
            con.commit()
            con.close()
            ledger_db = Path(td) / "finance_ledger.db"
            _make_ledger(ledger_db)
            cfg = _config(td, swarm_db, ledger_db)
            cfg.update({"swarm_v2_provider": "W10Prov", "swarm_v2_model": "w10-model"})
            result = sbf.process_task(cfg, TASK_ID)
            self.assertEqual(result["action"], "done", result)
            self.assertEqual(result["plan"]["model"]["source"], "config_fallback")
            self.assertEqual(result["amount"], 2.0)


if __name__ == "__main__":
    unittest.main()
