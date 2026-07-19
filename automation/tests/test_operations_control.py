from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from automation.company_router import RouterState, classify_message
from automation.operations_control import (
    apply_user_decision,
    backfill_outcomes,
    business_period,
    connect,
    create_review,
    format_review_message,
    import_proposals,
    reap_experiments,
    record_outcome,
    runs_for_period,
    sync_operational_runs,
    update_experiment,
    utc_now,
)


class OperatingLedgerTests(unittest.TestCase):
    def _hermes_db(self, path: Path, job_dir: Path) -> None:
        db = sqlite3.connect(path)
        db.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
                cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER,
                tool_call_count INTEGER, estimated_cost_usd REAL, actual_cost_usd REAL,
                cost_status TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL
            );
            """
        )
        db.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("worker-1", "model-a", 100, 20, 300, 0, 5, 7, 0.0, None, "unknown"),
        )
        db.execute(
            "INSERT INTO messages(session_id,role,content,timestamp) VALUES (?,?,?,?)",
            ("worker-1", "user", f"产物目录：{job_dir}", 1.0),
        )
        db.commit()
        db.close()

    def test_syncs_content_run_tokens_without_inventing_business_outcome(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            router_db = root / "router.db"
            state = RouterState(str(router_db))
            decision = classify_message("写一篇文章")
            event_id = state.insert("s1", "weixin", "h1", "写一篇文章", decision)
            run_id = "run-article"
            state.update(event_id, run_id=run_id, status="completed")
            state.close()

            job_dir = root / "jobs" / run_id
            job_dir.mkdir(parents=True)
            (job_dir / "request.json").write_text(json.dumps({
                "run_id": run_id, "route": "article", "message": "写一篇文章",
                "created_at": "2026-07-15T01:00:00+00:00",
            }), encoding="utf-8")
            (job_dir / "status.json").write_text(json.dumps({
                "status": "completed", "started_at": "2026-07-15T01:00:00+00:00",
                "completed_at": "2026-07-15T01:05:00+00:00",
                "artifacts": [str(job_dir / "draft.md"), str(job_dir / "qa-report.md")],
            }), encoding="utf-8")
            (job_dir / "draft.md").write_text("# draft", encoding="utf-8")
            (job_dir / "qa-report.md").write_text("Gate 1 通过\nGate 2 通过\nGate 3 通过", encoding="utf-8")
            hermes_db = root / "hermes.db"
            self._hermes_db(hermes_db, job_dir)
            ledger = root / "operations.db"

            result = sync_operational_runs(ledger, router_db, root / "jobs", hermes_db)
            self.assertEqual(result["content"], 1)
            db = connect(ledger)
            row = db.execute("SELECT * FROM operational_runs WHERE run_id=?", (run_id,)).fetchone()
            self.assertEqual(row["input_tokens"], 100)
            self.assertEqual(row["cache_read_tokens"], 300)
            self.assertEqual(row["quality_status"], "qa_reported_pass")
            self.assertEqual(row["outcome_status"], "unmeasured")
            db.close()

            record_outcome(ledger, run_id, outcome_status="measured", accepted=1, value_score=4.0)
            sync_operational_runs(ledger, router_db, root / "jobs", hermes_db)
            start, end = business_period(date(2026, 7, 15))
            row = runs_for_period(ledger, start, end)[0]
            self.assertEqual(row["accepted"], 1)
            self.assertEqual(row["value_score"], 4.0)

    def test_syncs_usage_from_codex_native_session(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            router_db = root / "router.db"
            state = RouterState(str(router_db))
            decision = classify_message("写一篇文章")
            event_id = state.insert("s1", "weixin", "h1", "写一篇文章", decision)
            run_id = "codex-run"
            state.update(event_id, run_id=run_id, status="completed")
            state.close()

            job_dir = root / "jobs" / run_id
            job_dir.mkdir(parents=True)
            (job_dir / "request.json").write_text(json.dumps({
                "run_id": run_id, "route": "article", "message": "写一篇文章",
                "created_at": "2026-07-15T01:00:00+00:00",
            }), encoding="utf-8")
            (job_dir / "status.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

            sessions = root / "codex" / "2026" / "07" / "15"
            sessions.mkdir(parents=True)
            session = sessions / "rollout-test.jsonl"
            events = [
                {"timestamp": "2026-07-15T01:00:00Z", "type": "session_meta", "payload": {
                    "session_id": "codex-session-1", "timestamp": "2026-07-15T01:00:00Z",
                    "cwd": str(root), "model_provider": "openai",
                }},
                {"timestamp": "2026-07-15T01:00:01Z", "type": "turn_context", "payload": {
                    "model": "gpt-5-codex", "request": str(job_dir),
                }},
                {"timestamp": "2026-07-15T01:00:02Z", "type": "response_item", "payload": {
                    "type": "custom_tool_call", "name": "exec",
                }},
                {"timestamp": "2026-07-15T01:00:03Z", "type": "event_msg", "payload": {
                    "type": "token_count", "info": {"total_token_usage": {
                        "input_tokens": 1000, "cached_input_tokens": 600,
                        "output_tokens": 80, "reasoning_output_tokens": 20,
                    }},
                }},
            ]
            session.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")

            ledger = root / "operations.db"
            sync_operational_runs(ledger, router_db, root / "jobs", root / "missing-hermes.db", sessions.parent.parent.parent)
            db = connect(ledger)
            row = db.execute("SELECT * FROM operational_runs WHERE run_id=?", (run_id,)).fetchone()
            evidence = json.loads(row["evidence_json"])
            self.assertEqual(row["worker_session_id"], "codex-session-1")
            self.assertEqual(row["model"], "gpt-5-codex")
            self.assertEqual(row["input_tokens"], 400)
            self.assertEqual(row["cache_read_tokens"], 600)
            self.assertEqual(row["output_tokens"], 80)
            self.assertEqual(row["reasoning_tokens"], 20)
            self.assertEqual(row["tool_call_count"], 1)
            self.assertEqual(evidence["usage_source"], "codex-session-jsonl")
            db.close()

    def test_syncs_usage_from_claude_native_session_and_deduplicates_messages(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            router_db = root / "router.db"
            state = RouterState(str(router_db))
            decision = classify_message("分析代码")
            event_id = state.insert("s1", "weixin", "h1", "分析代码", decision)
            run_id = "claude-run"
            state.update(event_id, run_id=run_id, status="completed")
            state.close()
            job_dir = root / "jobs" / run_id
            job_dir.mkdir(parents=True)
            (job_dir / "request.json").write_text(json.dumps({"run_id": run_id, "route": "security"}), encoding="utf-8")
            (job_dir / "status.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
            projects = root / "claude" / "project"
            projects.mkdir(parents=True)
            session = projects / "session.jsonl"
            usage = {"input_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 30, "output_tokens": 4}
            base = {"model": "claude-sonnet-4-5", "id": "msg-1", "type": "message", "role": "assistant", "content": [{"type": "tool_use", "id": "tool-1", "input": {"path": str(job_dir)}}], "usage": usage}
            session.write_text("\n".join(json.dumps({"sessionId": "claude-session", "cwd": str(root), "timestamp": "2026-07-15T01:00:00Z", "message": base}) for _ in range(2)) + "\n", encoding="utf-8")
            ledger = root / "operations.db"
            sync_operational_runs(ledger, router_db, root / "jobs", root / "missing-hermes.db", root / "missing-codex", projects.parent)
            db = connect(ledger)
            row = db.execute("SELECT * FROM operational_runs WHERE run_id=?", (run_id,)).fetchone()
            self.assertEqual(row["worker_session_id"], "claude-session")
            self.assertEqual(row["input_tokens"], 10)
            self.assertEqual(row["cache_read_tokens"], 30)
            self.assertEqual(row["cache_write_tokens"], 20)
            self.assertEqual(row["output_tokens"], 4)
            self.assertEqual(row["tool_call_count"], 1)
            self.assertEqual(json.loads(row["evidence_json"])["usage_source"], "claude-session-jsonl")
            db.close()


class TVCRGovernanceTests(unittest.TestCase):
    def _proposal_payload(self):
        return {
            "executive_summary": "文章产线需要先做成本与采用率实验。",
            "proposals": [{
                "product_line": "article-production",
                "priority": "P1",
                "title": "文章分级生产实验",
                "problem_statement": "文章消耗上升但采用结果未记录。",
                "business_impact": "无法判断投入产出。",
                "root_cause_hypotheses": ["所有文章使用同一深度流程"],
                "options": [{"scope": "process", "action": "分级", "expected_value": "降低浪费", "cost": "低", "risk": "质量波动"}],
                "recommended_action": "先运行 5 篇分级生产实验",
                "change_scopes": ["product", "process", "resource"],
                "expected_value": "降低单位成本",
                "expected_cost": "5 篇实验",
                "risk": "质量下降时停止",
                "success_metrics": [{"metric": "direct_tokens", "baseline": "当前", "target": "下降30%", "window": "5篇"}],
                "evidence_run_ids": ["run-1"],
            }],
        }

    def test_approval_creates_operating_experiment_not_code_task(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            start, end = business_period(date(2026, 7, 15))
            review_id = create_review(
                db_path, review_day=date(2026, 7, 15), period_start=start, period_end=end,
                origin={"platform": "weixin", "chat_id": "chat"},
            )
            ids = import_proposals(db_path, review_id, self._proposal_payload())
            self.assertEqual(ids, ["TVCR-P-20260715-01"])
            message = format_review_message(db_path, review_id)
            self.assertIn("公司日报｜2026-07-15", message)
            self.assertIn("待决策：1 项", message)
            self.assertNotIn(ids[0], message)
            compact = format_review_message(db_path, review_id, limit=800)
            self.assertLessEqual(len(compact), 800)
            self.assertIn("批准第1项", compact)

            result = apply_user_decision(db_path, f"批准 {ids[0]}", actor="user-1")
            self.assertTrue(result["ok"])
            self.assertEqual(result["decision"], "approved")
            db = connect(db_path)
            experiment = db.execute("SELECT * FROM operating_experiments").fetchone()
            self.assertEqual(experiment["product_line"], "article-production")
            self.assertEqual(experiment["status"], "planned")
            plan = json.loads(experiment["implementation_plan_json"])
            self.assertIn("经营方案", plan["rule"])
            db.close()

    def test_rejection_does_not_create_experiment(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            start, end = business_period(date(2026, 7, 15))
            review_id = create_review(db_path, review_day=date(2026, 7, 15), period_start=start, period_end=end)
            proposal_id = import_proposals(db_path, review_id, self._proposal_payload())[0]
            result = apply_user_decision(db_path, f"拒绝 {proposal_id}", actor="user-1")
            self.assertEqual(result["decision"], "rejected")
            db = connect(db_path)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operating_experiments").fetchone()[0], 0)
            db.close()

    def test_not_approved_phrase_cannot_be_misread_as_approval(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            start, end = business_period(date(2026, 7, 15))
            review_id = create_review(db_path, review_day=date(2026, 7, 15), period_start=start, period_end=end)
            proposal_id = import_proposals(db_path, review_id, self._proposal_payload())[0]
            result = apply_user_decision(db_path, f"不批准 {proposal_id}", actor="user-1")
            self.assertEqual(result["decision"], "rejected")


class ExperimentStateMachineTests(unittest.TestCase):
    def _payload(self):
        return {
            "executive_summary": "test",
            "proposals": [{
                "product_line": "company",
                "priority": "P1",
                "title": "闭环修复实验",
                "problem_statement": "实验状态机会死锁。",
                "recommended_action": "加护栏与 reaper",
                "change_scopes": ["process", "technology"],
                "success_metrics": [{"metric": "stuck_experiments", "baseline": "2", "target": "0", "window": "1天"}],
                "evidence_run_ids": [],
            }],
        }

    def _make_experiment(self, db_path: Path) -> str:
        start, end = business_period(date(2026, 7, 15))
        review_id = create_review(db_path, review_day=date(2026, 7, 15), period_start=start, period_end=end)
        ids = import_proposals(db_path, review_id, self._payload())
        result = apply_user_decision(db_path, f"批准 {ids[0]}", actor="u")
        return result["experiment_id"]

    def test_illegal_transition_is_rejected_but_legal_path_works(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            exp = self._make_experiment(db_path)
            with self.assertRaises(ValueError):  # planned -> succeeded is nonsense
                update_experiment(db_path, exp, status="succeeded")
            update_experiment(db_path, exp, status="running")
            update_experiment(db_path, exp, status="evaluating")
            update_experiment(db_path, exp, status="succeeded", conclusion="done")
            db = connect(db_path)
            row = db.execute("SELECT * FROM operating_experiments WHERE experiment_id=?", (exp,)).fetchone()
            db.close()
            self.assertEqual(row["status"], "succeeded")
            self.assertTrue(row["ended_at"])

    def test_baseline_is_captured_at_approval(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            exp = self._make_experiment(db_path)
            db = connect(db_path)
            row = db.execute("SELECT baseline_json FROM operating_experiments WHERE experiment_id=?", (exp,)).fetchone()
            db.close()
            baseline = json.loads(row["baseline_json"])
            self.assertNotEqual(baseline, {})
            self.assertIn("cost_rollup", baseline)
            self.assertIn("captured_at", baseline)

    def test_running_sets_due_and_does_not_rewrite_started_at(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            exp = self._make_experiment(db_path)
            update_experiment(db_path, exp, status="running")
            db = connect(db_path)
            row = db.execute("SELECT started_at,evaluation_due_at FROM operating_experiments WHERE experiment_id=?", (exp,)).fetchone()
            db.close()
            self.assertTrue(row["started_at"])
            self.assertTrue(row["evaluation_due_at"])
            update_experiment(db_path, exp, status="running")  # re-entry must not reset the clock
            db = connect(db_path)
            row2 = db.execute("SELECT started_at FROM operating_experiments WHERE experiment_id=?", (exp,)).fetchone()
            db.close()
            self.assertEqual(row2["started_at"], row["started_at"])

    def test_reaper_advances_overdue_running_to_evaluating(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            exp = self._make_experiment(db_path)
            update_experiment(db_path, exp, status="running", evaluation_window_hours=-1)  # due in the past
            out = reap_experiments(db_path)
            self.assertIn(exp, out["advanced_to_evaluating"])
            db = connect(db_path)
            row = db.execute("SELECT status FROM operating_experiments WHERE experiment_id=?", (exp,)).fetchone()
            db.close()
            self.assertEqual(row["status"], "evaluating")

    def test_reaper_backfills_missing_due_date_without_advancing_fresh_runs(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            exp = self._make_experiment(db_path)
            db = connect(db_path)  # simulate a legacy running row with no due date
            db.execute(
                "UPDATE operating_experiments SET status='running',started_at=?,evaluation_due_at='' WHERE experiment_id=?",
                (utc_now(), exp),
            )
            db.commit()
            db.close()
            out = reap_experiments(db_path)
            self.assertIn(exp, out["due_backfilled"])
            self.assertNotIn(exp, out["advanced_to_evaluating"])  # started now -> due is in the future
            db = connect(db_path)
            row = db.execute("SELECT status,evaluation_due_at FROM operating_experiments WHERE experiment_id=?", (exp,)).fetchone()
            db.close()
            self.assertEqual(row["status"], "running")
            self.assertTrue(row["evaluation_due_at"])

    def test_force_bypasses_the_transition_guard(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            exp = self._make_experiment(db_path)
            update_experiment(db_path, exp, status="succeeded", force=True)
            db = connect(db_path)
            row = db.execute("SELECT status FROM operating_experiments WHERE experiment_id=?", (exp,)).fetchone()
            db.close()
            self.assertEqual(row["status"], "succeeded")


class OutcomeBackfillTests(unittest.TestCase):
    def _seed_run(self, db_path: Path, run_id: str, product_line: str, artifacts: list[str]) -> None:
        db = connect(db_path)
        now = utc_now()
        db.execute(
            """INSERT INTO operational_runs (run_id,product_line,source_type,status,outcome_status,
               artifacts_json,created_at,updated_at)
               VALUES (?,?,?,?,'unmeasured',?,?,?)""",
            (run_id, product_line, "content-job", "completed", json.dumps(artifacts), now, now),
        )
        db.commit()
        db.close()

    def _article_perf_db(self, path: Path, title: str, reads: int) -> None:
        db = sqlite3.connect(path)
        db.execute(
            """CREATE TABLE article_metrics (article_id TEXT, title TEXT, platform TEXT,
               published_at TEXT, reads INTEGER)"""
        )
        db.execute("INSERT INTO article_metrics VALUES (?,?,?,?,?)", ("A1", title, "微信", "2026-07-09", reads))
        db.commit()
        db.close()

    def _finance_db(self, path: Path, source_ref: str, amount: float) -> None:
        db = sqlite3.connect(path)
        db.execute(
            """CREATE TABLE actual_transactions (transaction_id TEXT, kind TEXT, product_line TEXT,
               amount REAL, currency TEXT, source_ref TEXT, occurred_at TEXT)"""
        )
        db.execute("INSERT INTO actual_transactions VALUES (?,?,?,?,?,?,?)",
                   ("TX1", "revenue", "article-production", amount, "CNY", source_ref, "2026-07-10"))
        db.commit()
        db.close()

    def test_reach_backfilled_only_on_exact_title_match(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "ops.db"
            title = "Agent 的灵魂只有 120 行代码"
            draft = root / "draft-humanized.md"
            draft.write_text(f"# {title}\n\n正文", encoding="utf-8")
            self._seed_run(db_path, "run-hit", "article-production", [str(draft)])
            # a different article whose title does not match anything measured
            other = root / "other.md"
            other.write_text("# 完全不同的标题\n\n正文", encoding="utf-8")
            self._seed_run(db_path, "run-miss", "article-production", [str(other)])
            perf = root / "perf.db"
            self._article_perf_db(perf, title, 250)

            out = backfill_outcomes(db_path, finance_db=root / "none.db", article_perf_db=perf)
            self.assertEqual(out["backfilled"], 1)
            self.assertEqual(out["still_unmeasured"], 1)
            db = connect(db_path)
            hit = db.execute("SELECT * FROM operational_runs WHERE run_id='run-hit'").fetchone()
            miss = db.execute("SELECT * FROM operational_runs WHERE run_id='run-miss'").fetchone()
            db.close()
            self.assertEqual(hit["outcome_status"], "measured")
            self.assertEqual(hit["reach"], 250)
            self.assertEqual(hit["published"], 1)
            self.assertEqual(miss["outcome_status"], "unmeasured")  # no evidence -> not invented

    def test_revenue_backfilled_on_source_ref_key(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "ops.db"
            self._seed_run(db_path, "run-rev", "article-production", [])
            finance = root / "finance.db"
            self._finance_db(finance, source_ref="content-job run-rev delivered", amount=88.0)
            out = backfill_outcomes(db_path, finance_db=finance, article_perf_db=root / "none.db")
            self.assertEqual(out["backfilled"], 1)
            db = connect(db_path)
            row = db.execute("SELECT * FROM operational_runs WHERE run_id='run-rev'").fetchone()
            db.close()
            self.assertEqual(row["outcome_status"], "measured")
            self.assertEqual(row["revenue_amount"], 88.0)
            self.assertEqual(row["accepted"], 1)

    def test_backfill_never_overwrites_measured_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "ops.db"
            self._seed_run(db_path, "run-done", "article-production", [])
            record_outcome(db_path, "run-done", outcome_status="measured", value_score=5.0)
            finance = root / "finance.db"
            self._finance_db(finance, source_ref="run-done", amount=999.0)
            out = backfill_outcomes(db_path, finance_db=finance, article_perf_db=root / "none.db")
            self.assertEqual(out["backfilled"], 0)  # already measured -> untouched
            db = connect(db_path)
            row = db.execute("SELECT * FROM operational_runs WHERE run_id='run-done'").fetchone()
            db.close()
            self.assertEqual(row["value_score"], 5.0)
            self.assertIsNone(row["revenue_amount"])

    def test_missing_evidence_dbs_backfill_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "ops.db"
            self._seed_run(db_path, "run-x", "article-production", [])
            out = backfill_outcomes(db_path, finance_db=root / "none.db", article_perf_db=root / "none2.db")
            self.assertEqual(out["backfilled"], 0)
            self.assertEqual(out["still_unmeasured"], 1)


if __name__ == "__main__":
    unittest.main()
