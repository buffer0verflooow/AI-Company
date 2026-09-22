"""D2 回归:选题 → 蜂群 inbox 自动投递桥(`automation/swarm_intake.py`;2026-09-21)。

背景(BG-02 根因):市场雷达每天往 `market_signals` 写信号(实测 957 条 / 932 clean),
而蜂群 v2 的唯一人写入口是 `inbox/` ⇒ 需求侧零转化。用户裁 D2 = 按触发条件表:
content 选题自动投递 / ops 周期单 / vuln 手投 / dev 走公司路由 ⇒ 本桥实现 content 那一条。

断言(每条都要能红):
  ① 候选选择:只取 clean ∧ 分数 ≥ 阈值 ∧ 窗口内(脏/低分/过期一律不进);
  ② 素材:抓取失败 ⇒ 回退长 snippet;正文与 snippet 都过薄 ⇒ **跳过**(宁缺勿滥);
  ③ 订单形状:D3 合规(显式 5 件产物)、run_type=content、est ≤ token_budget、
     instruction 内联质量门规范全文 + 素材路径 + fs.append 分段写;
  ④ `--apply` 端到端:素材落 `materials/`、订单落 `inbox/`、state 记录 ⇒ **再跑不重复投**;
  ⑤ dry-run:零写入(不建素材/订单/state)。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import swarm_intake as si

SCHEMA = """
CREATE TABLE market_signals (
  signal_id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL DEFAULT '',
  theme TEXT NOT NULL, theme_title TEXT NOT NULL,
  product_line TEXT NOT NULL DEFAULT 'company',
  query_id TEXT NOT NULL DEFAULT '', query_text TEXT NOT NULL DEFAULT '',
  channel TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, url TEXT NOT NULL,
  source_domain TEXT NOT NULL DEFAULT '', snippet TEXT NOT NULL,
  published_at TEXT DEFAULT '', first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL DEFAULT '',
  occurrences INTEGER NOT NULL DEFAULT 1,
  relevance_score REAL NOT NULL DEFAULT 0, commercial_score REAL NOT NULL DEFAULT 0,
  freshness_score REAL NOT NULL DEFAULT 0, source_score REAL NOT NULL DEFAULT 0,
  total_score REAL NOT NULL DEFAULT 0, content_risk TEXT NOT NULL DEFAULT 'clean',
  latest_run_id TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL DEFAULT '{}')
"""

LONG_SNIPPET = "这是一段足够长的信号摘要。" * 60          # > MIN_SNIPPET_CHARS


def _seed(db: Path, rows: list[dict]) -> None:
    con = sqlite3.connect(db)
    con.execute(SCHEMA)
    for i, r in enumerate(rows):
        con.execute(
            "INSERT INTO market_signals(signal_id, theme, theme_title, title, url,"
            " snippet, first_seen_at, total_score, content_risk)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (r["signal_id"], r.get("theme", "agent-security"),
             r.get("theme_title", "企业 AI 智能体安全治理需求"),
             r.get("title", f"标题 {i}"), r.get("url", f"https://example.com/{i}"),
             r.get("snippet", LONG_SNIPPET),
             r.get("first_seen_at", "2026-09-20T00:30:00+00:00"),
             r.get("total_score", 70.0), r.get("content_risk", "clean")))
    con.commit()
    con.close()


def _config(td: Path) -> Path:
    swarm = Path(td) / "swarm"
    (swarm / "inbox").mkdir(parents=True, exist_ok=True)
    (swarm / "materials").mkdir(parents=True, exist_ok=True)
    cfg = {
        "swarm_repo": str(swarm),
        "market_signals_db": str(Path(td) / "market_signals.db"),
        "operations_db": str(Path(td) / "operations_control.db"),
    }
    p = Path(td) / "router_config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


class SelectionTests(unittest.TestCase):
    def test_only_clean_high_score_in_window(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "market_signals.db"
            _seed(db, [
                {"signal_id": "S-ok", "total_score": 70},
                {"signal_id": "S-dirty", "total_score": 80, "content_risk": "prompt_injection"},
                {"signal_id": "S-low", "total_score": 40},
                {"signal_id": "S-old", "total_score": 90,
                 "first_seen_at": "2026-01-01T00:00:00+00:00"},
            ])
            con = sqlite3.connect(db)
            got = si.select_candidates(con, min_score=60, lookback_days=14, limit=5)
            con.close()
            self.assertEqual([r["signal_id"] for r in got], ["S-ok"])


class MaterialTests(unittest.TestCase):
    def test_fetch_failure_falls_back_to_long_snippet(self):
        with patch.object(si, "fetch", side_effect=RuntimeError("no network")):
            text, note = si.material_text({"url": "https://x.test/a", "snippet": LONG_SNIPPET})
        self.assertEqual(text, LONG_SNIPPET)
        self.assertIn("摘要回退", note)

    def test_fetched_body_wins_over_snippet(self):
        body = "<html><body>" + ("正文内容。" * 500) + "</body></html>"
        with patch.object(si, "fetch", return_value=body):
            text, note = si.material_text({"url": "https://x.test/a", "snippet": LONG_SNIPPET})
        self.assertIn("正文抓取", note)
        self.assertGreater(len(text), si.MIN_FETCHED_CHARS)
        self.assertNotIn("<html>", text)

    def test_thin_material_is_skipped_not_dispatched(self):
        with patch.object(si, "fetch", side_effect=RuntimeError("no network")):
            text, note = si.material_text({"url": "https://x.test/a", "snippet": "太短"})
        self.assertEqual(text, "")
        self.assertIn("素材过薄", note)


class OrderShapeTests(unittest.TestCase):
    def _order(self):
        signal = {"signal_id": "MKT-SIG-abcdef123456", "theme": "agent",
                  "theme_title": "主题", "title": "标题", "url": "https://x.test/a"}
        instr = si.build_instruction(signal, material_rel="materials/x.md",
                                     source_note="正文抓取", spec_text="(规范)")
        return si.build_order(signal, instruction=instr)

    def test_order_is_d3_compliant_and_measured_budget(self):
        order = self._order()
        self.assertEqual(order["run_type"], "content")
        self.assertEqual(order["task_type"], "report")
        # run_id 带 UTC 日:被日顶拒发(cancelled 占位)后次日重投不撞 id
        self.assertTrue(order["run_id"].startswith("intake-abcdef123456-"), order["run_id"])
        self.assertEqual(len(order["run_id"].rsplit("-", 1)[1]), 8)
        self.assertLessEqual(order["est"], order["token_budget"])
        self.assertGreater(order["token_budget"], 0)
        self.assertIsInstance(order["focus_params"], dict)
        # D3:content 单必须显式声明产物(缺键会被 inbox 拒发)
        self.assertEqual(order["focus_params"]["deliverables"], si.DELIVERABLES)
        self.assertEqual(len(si.DELIVERABLES), 5)      # CV2:含排版/预览

    def test_instruction_is_self_contained(self):
        signal = {"signal_id": "S", "theme_title": "T", "title": "ti",
                  "url": "https://x.test/a"}
        instr = si.build_instruction(signal, material_rel="materials/x.md",
                                     source_note="正文抓取", spec_text="质量门规范正文")
        self.assertIn("materials/x.md", instr)
        self.assertIn("质量门规范正文", instr)            # 规范全文内联(运行时读不到公司仓)
        self.assertIn("fs.append", instr)                # CV-3 分段写
        self.assertIn("Gate 1/2/3", instr)               # 质量门要求(QA 三 Gate)
        self.assertIn("Gate 4", instr)                   # 微信预览检查
        self.assertIn("https://x.test/a", instr)          # 来源可追溯


class EndToEndTests(unittest.TestCase):
    def _run(self, cfg: Path, *extra: str, apply: bool = True):
        argv = ["--config", str(cfg), "--json", *extra]
        if apply:
            argv.append("--apply")
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = si.run(argv)
        self.assertEqual(rc, 0)
        return json.loads(buf.getvalue()), json.loads(cfg.read_text(encoding="utf-8"))

    def test_apply_writes_material_order_and_state_then_dedups(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _config(td)
            _seed(Path(cfg.read_text(encoding="utf-8") and json.loads(
                cfg.read_text(encoding="utf-8"))["market_signals_db"]),
                [{"signal_id": "MKT-SIG-aaaabbbbcccc", "total_score": 77}])
            with patch.object(si, "fetch", side_effect=RuntimeError("no network")):
                out, conf = self._run(cfg)
                self.assertEqual(len(out["dispatched"]), 1)
                d = out["dispatched"][0]
                self.assertTrue(d["run_id"].startswith("intake-aaaabbbbcccc-"))
                swarm = Path(conf["swarm_repo"])
                order_file = swarm / si.INTAKE_SUBDIR / d["order_file"]
                self.assertTrue(order_file.is_file(), "订单必须落 inbox-intake/(.*.json)")
                order = json.loads(order_file.read_text(encoding="utf-8"))
                self.assertEqual(order["focus_params"]["deliverables"], si.DELIVERABLES)
                self.assertTrue((swarm / d["material"]).is_file(), "素材必须先落盘")
                self.assertIn(d["material"], order["focus_params"]["instruction"])
                state = json.loads((Path(conf["operations_db"]).parent
                                    / "swarm_intake_state.json").read_text(encoding="utf-8"))
                self.assertEqual(state["MKT-SIG-aaaabbbbcccc"]["status"], "dispatched")
                # 第二次:同一信号**不再重复投递**
                out2, _ = self._run(cfg)
                self.assertEqual(out2["dispatched"], [])
                self.assertEqual(len(list((swarm / si.INTAKE_SUBDIR).glob("*.json"))), 1)

    def test_apply_skips_thin_material_and_records_reason(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _config(td)
            conf = json.loads(cfg.read_text(encoding="utf-8"))
            _seed(Path(conf["market_signals_db"]),
                  [{"signal_id": "MKT-SIG-thin00000000", "total_score": 70,
                    "snippet": "太短"}])
            with patch.object(si, "fetch", side_effect=RuntimeError("no network")):
                out, conf = self._run(cfg)
            self.assertEqual(out["dispatched"], [])
            self.assertEqual(len(out["skipped"]), 1)
            self.assertEqual(
                list((Path(conf["swarm_repo"]) / si.INTAKE_SUBDIR).glob("*.json")), [])
            state = json.loads((Path(conf["operations_db"]).parent
                                / "swarm_intake_state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["MKT-SIG-thin00000000"]["status"], "skipped")
            self.assertIn("素材过薄", state["MKT-SIG-thin00000000"]["reason"])

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = _config(td)
            conf = json.loads(cfg.read_text(encoding="utf-8"))
            _seed(Path(conf["market_signals_db"]),
                  [{"signal_id": "MKT-SIG-dryrun000000", "total_score": 70}])
            with patch.object(si, "fetch", side_effect=RuntimeError("no network")):
                out, conf = self._run(cfg, apply=False)
            self.assertEqual(len(out["dispatched"]), 1)
            self.assertTrue(out["dry_run"])
            swarm = Path(conf["swarm_repo"])
            self.assertEqual(list((swarm / si.INTAKE_SUBDIR).glob("*.json")), [])
            self.assertEqual(list((swarm / "materials").glob("*.md")), [])
            self.assertFalse((Path(conf["operations_db"]).parent
                              / "swarm_intake_state.json").exists())


if __name__ == "__main__":
    unittest.main()
