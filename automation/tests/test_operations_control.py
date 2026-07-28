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
    auto_approve_proposals,
    backfill_outcomes,
    business_period,
    connect,
    create_review,
    escalate_stale_proposals,
    format_review_message,
    import_proposals,
    reap_experiments,
    reap_stale_runs,
    record_outcome,
    runs_for_period,
    sync_operational_runs,
    update_experiment,
    utc_now,
)
from automation.operations_control import _apportion_shared_sessions


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

    def _article_source_db(self, path: Path, title: str, reads: int) -> None:
        db = sqlite3.connect(path)
        db.execute(
            """CREATE TABLE article_source_metrics (title TEXT, channel TEXT, reads INTEGER,
               published_at TEXT)"""
        )
        # An exported header row plus per-channel rows; only the aggregate counts.
        db.execute("INSERT INTO article_source_metrics VALUES (?,?,?,?)", ("内容标题", "传播渠道", 0, "发表日期"))
        db.execute("INSERT INTO article_source_metrics VALUES (?,?,?,?)", (title, "推荐", reads - 40, "2026-07-05"))
        db.execute("INSERT INTO article_source_metrics VALUES (?,?,?,?)", (title, "全部", reads, "2026-07-05"))
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
            # Publication to the official account is recorded as adoption.
            self.assertEqual(hit["accepted"], 1)
            self.assertEqual(miss["outcome_status"], "unmeasured")  # no evidence -> not invented

    def test_reach_matched_via_non_primary_draft_variant(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "ops.db"
            title = "Agent 的灵魂只有 120 行代码"
            # The plain draft H1 was rewritten during humanising; only the
            # humanized variant carries the published title.
            plain = root / "draft.md"
            plain.write_text("# 一个工作草稿标题\n\n正文", encoding="utf-8")
            humanized = root / "draft-humanized.md"
            humanized.write_text(f"# {title}\n\n正文", encoding="utf-8")
            self._seed_run(db_path, "run-variant", "article-production", [str(plain), str(humanized)])
            perf = root / "perf.db"
            self._article_perf_db(perf, title, 300)

            out = backfill_outcomes(db_path, finance_db=root / "none.db", article_perf_db=perf)
            self.assertEqual(out["backfilled"], 1)
            db = connect(db_path)
            row = db.execute("SELECT * FROM operational_runs WHERE run_id='run-variant'").fetchone()
            db.close()
            self.assertEqual(row["outcome_status"], "measured")
            self.assertEqual(row["reach"], 300)

    def test_reach_backfilled_from_source_metrics_total_channel(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "ops.db"
            title = "只在渠道分表里出现的文章"
            draft = root / "draft-humanized.md"
            draft.write_text(f"# {title}\n\n正文", encoding="utf-8")
            self._seed_run(db_path, "run-src", "article-production", [str(draft)])
            perf = root / "perf.db"
            # Only article_source_metrics carries this title; the aggregate wins.
            self._article_source_db(perf, title, 120)

            out = backfill_outcomes(db_path, finance_db=root / "none.db", article_perf_db=perf)
            self.assertEqual(out["backfilled"], 1)
            self.assertEqual(out["by_source"]["article-reach"], 1)
            db = connect(db_path)
            row = db.execute("SELECT * FROM operational_runs WHERE run_id='run-src'").fetchone()
            db.close()
            self.assertEqual(row["reach"], 120)  # aggregate '全部' channel, not '推荐'
            self.assertEqual(row["published"], 1)

    def test_reach_matched_via_frontmatter_title_when_h1_differs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db_path = root / "ops.db"
            published = "发布用的最终标题"
            draft = root / "draft-humanized.md"
            # Frontmatter title is what wechat_push publishes under; the visible
            # H1 was left as an earlier working headline.
            draft.write_text(
                f"---\ntitle: {published}\nauthor: x\n---\n\n# 一个较早的工作标题\n\n正文",
                encoding="utf-8",
            )
            self._seed_run(db_path, "run-fm", "article-production", [str(draft)])
            perf = root / "perf.db"
            self._article_perf_db(perf, published, 77)

            out = backfill_outcomes(db_path, finance_db=root / "none.db", article_perf_db=perf)
            self.assertEqual(out["backfilled"], 1)
            db = connect(db_path)
            row = db.execute("SELECT * FROM operational_runs WHERE run_id='run-fm'").fetchone()
            db.close()
            self.assertEqual(row["reach"], 77)
            self.assertEqual(row["accepted"], 1)


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


class SharedSessionApportionTests(unittest.TestCase):
    def _row(self, run_id, session_id, **over):
        row = {
            "run_id": run_id,
            "worker_session_id": session_id,
            "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "reasoning_tokens": 0, "tool_call_count": 0,
            "estimated_cost_usd": None, "estimated_cost_native": None,
            "actual_cost_usd": None, "evidence_json": "{}",
        }
        row.update(over)
        return row

    def test_shared_session_split_evenly_and_sum_preserved(self):
        rows = [
            self._row(f"r{i}", "sess", input_tokens=1000, cache_read_tokens=300,
                      estimated_cost_usd=6.0, estimated_cost_native=6.0)
            for i in range(4)
        ]
        _apportion_shared_sessions(rows)
        self.assertEqual(sum(r["input_tokens"] for r in rows), 1000)
        self.assertEqual(sum(r["cache_read_tokens"] for r in rows), 300)
        self.assertAlmostEqual(sum(r["estimated_cost_usd"] for r in rows), 6.0, places=6)
        self.assertEqual([r["input_tokens"] for r in rows], [250, 250, 250, 250])
        for r in rows:
            self.assertEqual(json.loads(r["evidence_json"])["session_apportioned"]["runs_sharing"], 4)

    def test_integer_remainder_is_distributed(self):
        rows = [self._row(f"r{i}", "sess", input_tokens=100) for i in range(3)]
        _apportion_shared_sessions(rows)
        self.assertEqual(sorted(r["input_tokens"] for r in rows), [33, 33, 34])
        self.assertEqual(sum(r["input_tokens"] for r in rows), 100)

    def test_single_run_and_empty_session_untouched(self):
        rows = [
            self._row("solo", "only-me", input_tokens=999, estimated_cost_usd=5.0),
            self._row("blank", "", input_tokens=42),
        ]
        _apportion_shared_sessions(rows)
        self.assertEqual(rows[0]["input_tokens"], 999)
        self.assertEqual(rows[0]["estimated_cost_usd"], 5.0)
        self.assertEqual(rows[1]["input_tokens"], 42)
        self.assertNotIn("session_apportioned", json.loads(rows[0]["evidence_json"]))
        self.assertNotIn("session_apportioned", json.loads(rows[1]["evidence_json"]))

    def test_none_cost_left_as_none(self):
        rows = [self._row(f"r{i}", "sess", input_tokens=10, estimated_cost_usd=None) for i in range(2)]
        _apportion_shared_sessions(rows)
        self.assertEqual([r["estimated_cost_usd"] for r in rows], [None, None])
        self.assertEqual(sum(r["input_tokens"] for r in rows), 10)


class TieredApprovalTests(unittest.TestCase):
    def _proposal(self, *, priority="P2", risk="低", scopes=("process",), item_title="小步实验"):
        return {
            "product_line": "article-production",
            "priority": priority,
            "title": item_title,
            "problem_statement": "需要优化。",
            "recommended_action": "执行小步实验",
            "change_scopes": list(scopes),
            "risk": risk,
            "success_metrics": [{"metric": "direct_tokens", "baseline": "当前", "target": "下降", "window": "5篇"}],
        }

    def _review(self, db_path, day, proposals):
        start, end = business_period(day)
        review_id = create_review(db_path, review_day=day, period_start=start, period_end=end)
        import_proposals(db_path, review_id, {"executive_summary": "s", "proposals": proposals})
        return review_id

    def test_auto_approves_p2_low_risk_within_approved_scope(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            # Establish an approved scope baseline via a manual P1 approval.
            r1 = self._review(db_path, date(2026, 7, 10), [self._proposal(priority="P1", scopes=("process",), item_title="奠基提案")])
            base_id = connect(db_path).execute(
                "SELECT proposal_id FROM improvement_proposals WHERE review_id=?", (r1,)
            ).fetchone()[0]
            apply_user_decision(db_path, f"批准 {base_id}", actor="user")

            self._review(db_path, date(2026, 7, 11), [self._proposal(priority="P2", risk="低", scopes=("process",))])
            result = auto_approve_proposals(db_path)
            self.assertEqual(len(result["approved"]), 1)
            db = connect(db_path)
            statuses = {row["priority"]: row["status"] for row in db.execute("SELECT priority,status FROM improvement_proposals")}
            self.assertEqual(statuses["P2"], "approved")
            # An operating experiment is spawned by the policy approval too.
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operating_experiments").fetchone()[0], 2)
            db.close()

    def test_scope_outside_baseline_is_not_auto_approved(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            r1 = self._review(db_path, date(2026, 7, 10), [self._proposal(priority="P1", scopes=("process",), item_title="奠基提案")])
            base_id = connect(db_path).execute(
                "SELECT proposal_id FROM improvement_proposals WHERE review_id=?", (r1,)
            ).fetchone()[0]
            apply_user_decision(db_path, f"批准 {base_id}", actor="user")
            self._review(db_path, date(2026, 7, 11), [self._proposal(priority="P2", risk="低", scopes=("technology",))])
            result = auto_approve_proposals(db_path)
            self.assertEqual(result["approved"], [])
            self.assertEqual(len(result["skipped"]), 1)

    def test_high_risk_and_non_p2_are_never_auto_approved(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            r1 = self._review(db_path, date(2026, 7, 10), [self._proposal(priority="P1", scopes=("process",), item_title="奠基提案")])
            base_id = connect(db_path).execute(
                "SELECT proposal_id FROM improvement_proposals WHERE review_id=?", (r1,)
            ).fetchone()[0]
            apply_user_decision(db_path, f"批准 {base_id}", actor="user")
            self._review(db_path, date(2026, 7, 11), [
                self._proposal(priority="P2", risk="高", scopes=("process",), item_title="高风险"),
                self._proposal(priority="P0", risk="低", scopes=("process",), item_title="高优先"),
            ])
            result = auto_approve_proposals(db_path)
            self.assertEqual(result["approved"], [])  # P0 excluded by priority, high-risk by risk


class ProposalSLATests(unittest.TestCase):
    def _payload(self, priority):
        return {"executive_summary": "s", "proposals": [{
            "product_line": "company",
            "priority": priority,
            "title": "SLA 提案",
            "problem_statement": "p",
            "recommended_action": "a",
            "change_scopes": ["process"],
            "risk": "低",
            "success_metrics": [{"metric": "m", "baseline": "b", "target": "t", "window": "w"}],
        }]}

    def test_stale_p0_gets_escalation_stamp(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            start, end = business_period(date(2026, 7, 1))
            review_id = create_review(db_path, review_day=date(2026, 7, 1), period_start=start, period_end=end)
            pid = import_proposals(db_path, review_id, self._payload("P0"))[0]
            # Backdate creation to 5 days ago so it is past the 72h SLA.
            db = connect(db_path)
            db.execute("UPDATE improvement_proposals SET created_at=? WHERE proposal_id=?", ("2026-07-01T00:00:00+00:00", pid))
            db.commit()
            db.close()
            result = escalate_stale_proposals(db_path, now="2026-07-08T00:00:00+00:00")
            self.assertIn(pid, result["escalated"])
            db = connect(db_path)
            row = db.execute("SELECT status,escalated_at FROM improvement_proposals WHERE proposal_id=?", (pid,)).fetchone()
            self.assertEqual(row["status"], "pending_approval")  # user can still decide
            self.assertTrue(row["escalated_at"])
            db.close()

    def test_superseded_backlog_is_auto_expired(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "operations.db"
            s1, e1 = business_period(date(2026, 7, 1))
            old = create_review(db_path, review_day=date(2026, 7, 1), period_start=s1, period_end=e1)
            old_pid = import_proposals(db_path, old, self._payload("P1"))[0]
            s2, e2 = business_period(date(2026, 7, 5))
            create_review(db_path, review_day=date(2026, 7, 5), period_start=s2, period_end=e2)
            new = connect(db_path).execute(
                "SELECT review_id FROM tvcr_reviews WHERE review_date='2026-07-05'"
            ).fetchone()[0]
            import_proposals(db_path, new, self._payload("P1"))
            result = escalate_stale_proposals(db_path, now="2026-07-06T00:00:00+00:00")
            self.assertIn(old_pid, result["superseded"])
            db = connect(db_path)
            self.assertEqual(
                db.execute("SELECT status FROM improvement_proposals WHERE proposal_id=?", (old_pid,)).fetchone()[0],
                "superseded",
            )
            db.close()


class StaleRunReaperTests(unittest.TestCase):
    def _seed_run(self, db_path: Path, run_id: str, *, status: str, started_at: str,
                  tokens: int = 0, output_bytes: int = 0) -> None:
        db = connect(db_path)
        now = utc_now()
        db.execute(
            """INSERT INTO operational_runs (run_id,product_line,source_type,status,
               started_at,created_at,updated_at,input_tokens,output_bytes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (run_id, "company", "content-job", status, started_at, started_at, now, tokens, output_bytes),
        )
        db.commit()
        db.close()

    def _seed_router_event(self, router_db: Path, run_id: str, *, status: str, runner_pid: int) -> None:
        state = RouterState(str(router_db))
        event_id = state.insert(
            "session-1", "cli", f"hash-{run_id}", f"msg {run_id}",
            classify_message("/company 继续推进公司任务"),
        )
        state.update(event_id, run_id=run_id, runner_pid=runner_pid, status=status)
        state.close()

    def test_reaps_stale_running_run_with_dead_worker(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            router_db = Path(td) / "router.db"
            old = "2026-07-21T02:00:00+00:00"
            self._seed_run(db_path, "run-stale", status="running", started_at=old)
            self._seed_router_event(router_db, "run-stale", status="running", runner_pid=999999999)

            out = reap_stale_runs(db_path, router_db=router_db, stale_minutes=120)
            self.assertIn("run-stale", out["reaped"])

            db = connect(db_path)
            row = db.execute(
                "SELECT status,quality_status,completed_at FROM operational_runs WHERE run_id='run-stale'"
            ).fetchone()
            db.close()
            self.assertEqual(row["status"], "failed")
            self.assertEqual(row["quality_status"], "empty_output")
            self.assertTrue(row["completed_at"])

            # Router event terminalized so a later sync cannot resurrect it.
            router = sqlite3.connect(router_db)
            rstatus = router.execute(
                "SELECT status FROM route_events WHERE run_id='run-stale'"
            ).fetchone()[0]
            router.close()
            self.assertEqual(rstatus, "failed")

    def test_fresh_running_run_is_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            router_db = Path(td) / "router.db"
            self._seed_run(db_path, "run-fresh", status="running", started_at=utc_now())
            self._seed_router_event(router_db, "run-fresh", status="running", runner_pid=999999999)

            out = reap_stale_runs(db_path, router_db=router_db, stale_minutes=120)
            self.assertEqual(out["reaped"], [])
            db = connect(db_path)
            self.assertEqual(
                db.execute("SELECT status FROM operational_runs WHERE run_id='run-fresh'").fetchone()[0],
                "running",
            )
            db.close()

    def test_reaper_is_idempotent_and_ignores_terminal_runs(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            router_db = Path(td) / "router.db"
            old = "2026-07-21T02:00:00+00:00"
            self._seed_run(db_path, "run-stale", status="running", started_at=old)
            self._seed_run(db_path, "run-done", status="completed", started_at=old)
            self._seed_router_event(router_db, "run-stale", status="running", runner_pid=999999999)

            first = reap_stale_runs(db_path, router_db=router_db, stale_minutes=120)
            second = reap_stale_runs(db_path, router_db=router_db, stale_minutes=120)
            self.assertEqual(first["reaped"], ["run-stale"])
            self.assertEqual(second["reaped"], [])  # already failed, nothing to do

    def test_dry_run_reports_without_mutating(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "ops.db"
            router_db = Path(td) / "router.db"
            old = "2026-07-21T02:00:00+00:00"
            self._seed_run(db_path, "run-stale", status="running", started_at=old)
            self._seed_router_event(router_db, "run-stale", status="running", runner_pid=999999999)

            out = reap_stale_runs(db_path, router_db=router_db, stale_minutes=120, dry_run=True)
            self.assertEqual(out["reaped"], ["run-stale"])
            db = connect(db_path)
            self.assertEqual(
                db.execute("SELECT status FROM operational_runs WHERE run_id='run-stale'").fetchone()[0],
                "running",
            )
            db.close()


if __name__ == "__main__":
    unittest.main()
