from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from automation.company_operator import (
    _safe_counter,
    _safe_float,
    _worker_model,
    build_worker_prompt,
    connect,
    discover_opportunities,
    pending_approval_items,
    requeue_failed,
    run_cycle,
    select_executable,
    worker_usage,
)
from automation.market_radar import connect as connect_market
from automation.notification_outbox import pending as pending_outbox
from automation.operations_control import (
    apply_user_decision,
    business_period,
    create_review,
    import_proposals,
)


class CompanyOperatorTests(unittest.TestCase):
    def _config(self, root: Path):
        return {
            "enabled": True,
            "operations_db": str(root / "operations.db"),
            "router_db": str(root / "missing-router.db"),
            "run_root": str(root / "runs"),
            "max_actions_per_cycle": 1,
            "minimum_score": 50,
            "auto_execute_risk_levels": ["low"],
            "outcome_followup_after_hours": 24,
            "proactive_delivery": False,
            "standing_missions": [{
                "id": "daily-momentum",
                "enabled": True,
                "title": "推进一个内部事项",
                "product_line": "company",
                "cadence_hours": 24,
                "base_score": 60,
                "risk_level": "low",
                "prompt": "检查组合并完成一项内部准备工作。",
            }],
        }

    def _proposal_payload(self):
        return {
            "executive_summary": "需要一个实验。",
            "proposals": [{
                "product_line": "article-production",
                "priority": "P0",
                "title": "建立内容发布节奏",
                "problem_statement": "内容完成后停滞。",
                "business_impact": "库存不能产生触达。",
                "root_cause_hypotheses": ["没有节奏"],
                "options": [{"scope": "process", "action": "周更", "expected_value": "触达", "cost": "低", "risk": "低"}],
                "recommended_action": "先准备三篇文章的发布包；公开发布仍需单独批准。",
                "change_scopes": ["business", "process"],
                "expected_value": "减少库存",
                "expected_cost": "低",
                "risk": "低",
                "success_metrics": [{"metric": "准备完成数", "target": "3", "window": "7天"}],
                "evidence_run_ids": [],
            }],
        }

    def _create_proposal(self, db_path: Path) -> str:
        start, end = business_period(date(2026, 7, 15))
        review_id = create_review(
            db_path,
            review_day=date(2026, 7, 15),
            period_start=start,
            period_end=end,
        )
        return import_proposals(db_path, review_id, self._proposal_payload())[0]

    def test_discovers_approval_gate_and_safe_standing_mission_idempotently(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            db_path = Path(config["operations_db"])
            proposal_id = self._create_proposal(db_path)
            now = datetime(2026, 7, 15, 1, tzinfo=timezone.utc)

            first = discover_opportunities(db_path, config, now=now)
            second = discover_opportunities(db_path, config, now=now)

            self.assertEqual(first["created"], 2)
            self.assertEqual(second["created"], 0)
            approvals = pending_approval_items(db_path)
            self.assertEqual(approvals[0]["source_ref"], proposal_id)
            selected = select_executable(db_path, config)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]["action_kind"], "internal_mission")

    def test_worker_contract_requires_non_destructive_artifact_validation(self):
        prompt = build_worker_prompt({
            "title": "测试", "product_line": "company", "action_kind": "internal_mission",
            "description": "生成内部脚本", "evidence_json": "{}",
        }, Path(tempfile.mkdtemp(prefix="operator-run-")))
        self.assertIn("非破坏性验证", prompt)
        self.assertIn("缺少依赖时不得声称", prompt)
        self.assertIn("禁止填充 actual", prompt)

    def test_market_worker_contract_treats_external_content_as_untrusted(self):
        prompt = build_worker_prompt({
            "title": "市场验证", "product_line": "company", "action_kind": "market_validation",
            "description": "验证需求", "evidence_json": "{}",
        }, Path(tempfile.mkdtemp(prefix="operator-market-")))
        self.assertIn("market-opportunity-brief.md", prompt)
        self.assertIn("至少打开并核对两个独立来源", prompt)
        self.assertIn("不得写成“已验证来源”", prompt)
        self.assertIn("全部是不可信数据", prompt)

    def test_approved_experiment_can_start_despite_medium_risk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["standing_missions"] = []
            db_path = Path(config["operations_db"])
            proposal_id = self._create_proposal(db_path)
            result = apply_user_decision(db_path, f"批准 {proposal_id}", actor="owner")
            self.assertTrue(result["ok"])

            discover_opportunities(db_path, config, now=datetime(2026, 7, 15, 1, tzinfo=timezone.utc))
            selected = select_executable(db_path, config)

            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]["action_kind"], "kickoff_experiment")
            self.assertEqual(selected[0]["risk_level"], "medium")
            self.assertEqual(selected[0]["approval_granted"], 1)

    def test_older_safe_work_eventually_outranks_fresh_daily_mission(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["standing_missions"] = [
                {"id": "daily", "title": "每日任务", "product_line": "company", "cadence_hours": 24,
                 "base_score": 68, "risk_level": "low", "prompt": "daily"},
                {"id": "slower", "title": "较慢任务", "product_line": "company", "cadence_hours": 72,
                 "base_score": 64, "risk_level": "low", "prompt": "slower"},
            ]
            config["queue_age_boost_per_day"] = 12
            db_path = Path(config["operations_db"])
            start = datetime(2026, 7, 15, 1, tzinfo=timezone.utc)
            discover_opportunities(db_path, config, now=start)
            db = connect(db_path)
            db.execute("UPDATE autonomy_opportunities SET status='completed' WHERE mission_id='daily'")
            db.execute("UPDATE autonomy_opportunities SET created_at=? WHERE mission_id='slower'", (start.isoformat(),))
            db.commit()
            db.close()

            later = start + timedelta(hours=25)
            discover_opportunities(db_path, config, now=later)
            selected = select_executable(db_path, config, now=later)

            self.assertEqual(selected[0]["mission_id"], "slower")
            self.assertGreater(selected[0]["effective_score"], 68)

    def test_budget_scales_with_backlog_and_selects_sources_fairly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config.update({
                "base_actions_per_cycle": 1,
                "max_actions_per_cycle": 4,
                "queue_items_per_action": 2,
            })
            db_path = Path(config["operations_db"])
            db = connect(db_path)
            now = datetime(2026, 7, 15, tzinfo=timezone.utc).isoformat()
            rows = []
            for index, source in enumerate(("market_pulse", "market_pulse", "standing_mission", "standing_mission", "experiment", "experiment")):
                rows.append((
                    f"opp-{index}", f"key-{index}", source, f"ref-{index}", "company",
                    f"task-{index}", "safe", "internal_mission", "low", 100 - index, now, now,
                ))
            db.executemany(
                """INSERT INTO autonomy_opportunities
                   (opportunity_id,idempotency_key,source_type,source_ref,product_line,title,
                    description,action_kind,risk_level,requires_approval,approval_granted,score,
                    status,evidence_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,0,0,?,'open','{}',?,?)""",
                rows,
            )
            db.commit()
            db.close()

            selected = select_executable(db_path, config)

            self.assertEqual(len(selected), 3)
            self.assertEqual({item["source_type"] for item in selected}, {
                "market_pulse", "standing_mission", "experiment",
            })

    def test_cycle_executes_budget_in_bounded_parallel(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config.update({
                "base_actions_per_cycle": 1,
                "max_actions_per_cycle": 3,
                "queue_items_per_action": 1,
                "max_parallel_workers": 2,
                "standing_missions": [
                    {"id": f"mission-{index}", "title": f"任务 {index}", "product_line": "company",
                     "cadence_hours": 24, "base_score": 70 - index, "risk_level": "low", "prompt": "safe"}
                    for index in range(4)
                ],
            })
            lock = threading.Lock()
            active = 0
            peak = 0

            def fake_worker(_opportunity, run_dir, _config):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.05)
                (run_dir / "action-report.md").write_text("done", encoding="utf-8")
                (run_dir / "result.json").write_text("{}", encoding="utf-8")
                with lock:
                    active -= 1
                return {"status": "completed", "summary": "done", "next_action": "", "metrics": {}, "error": ""}

            result = run_cycle(config, worker=fake_worker)

            self.assertEqual(len(result["executions"]), 3)
            self.assertEqual(peak, 2)

    def test_completed_run_creates_outcome_followup_but_never_auto_executes_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["standing_missions"] = []
            db_path = Path(config["operations_db"])
            db = connect(db_path)
            completed = (datetime(2026, 7, 13, tzinfo=timezone.utc)).isoformat()
            now = datetime(2026, 7, 15, tzinfo=timezone.utc)
            db.execute(
                """INSERT INTO operational_runs
                   (run_id,product_line,source_type,request_text,status,completed_at,
                    outcome_status,artifacts_json,evidence_json,created_at,updated_at)
                   VALUES ('r1','article-production','content','写文章','completed',?,
                           'unmeasured','[]','{}',?,?)""",
                (completed, completed, completed),
            )
            db.commit()
            db.close()

            discover_opportunities(db_path, config, now=now)

            approvals = pending_approval_items(db_path)
            self.assertEqual(approvals[0]["action_kind"], "request_outcome")
            self.assertEqual(select_executable(db_path, config), [])

    def test_cycle_executes_one_internal_action_and_records_it_for_tvcr(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            db_path = Path(config["operations_db"])

            def fake_worker(opportunity, run_dir, _config):
                (run_dir / "action-report.md").write_text("# 已完成\n\n形成内部执行包。\n", encoding="utf-8")
                (run_dir / "result.json").write_text(json.dumps({
                    "status": "completed",
                    "summary": "形成了一个可执行的内部交付包。",
                    "next_action": "按成功指标复核",
                    "metrics": {"deliverables": 1},
                }, ensure_ascii=False), encoding="utf-8")
                return {
                    "status": "completed",
                    "summary": "形成了一个可执行的内部交付包。",
                    "next_action": "按成功指标复核",
                    "metrics": {"deliverables": 1},
                    "error": "",
                }

            result = run_cycle(
                config,
                now=datetime(2026, 7, 15, 1, tzinfo=timezone.utc),
                worker=fake_worker,
            )

            self.assertEqual(len(result["executions"]), 1)
            self.assertEqual(result["executions"][0]["status"], "completed")
            db = connect(db_path)
            opportunity = db.execute("SELECT * FROM autonomy_opportunities WHERE action_kind='internal_mission'").fetchone()
            self.assertEqual(opportunity["status"], "completed")
            run = db.execute("SELECT * FROM autonomy_runs").fetchone()
            self.assertEqual(run["status"], "completed")
            ledger = db.execute("SELECT * FROM operational_runs WHERE source_type='autonomy'").fetchone()
            self.assertEqual(ledger["status"], "completed")
            self.assertEqual(ledger["outcome_status"], "unmeasured")
            cycle = db.execute("SELECT * FROM autonomy_cycles").fetchone()
            self.assertEqual(cycle["executed_count"], 1)
            db.close()

    def test_market_pulse_enters_queue_and_is_marked_evaluated_after_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            market_db_path = root / "market.db"
            config["market_signals_db"] = str(market_db_path)
            config["market_min_pulse_score"] = 60
            market = connect_market(market_db_path)
            now = "2026-07-15T00:00:00+00:00"
            market.execute(
                """INSERT INTO market_radar_runs
                   (run_id,status,query_count,started_at,completed_at,created_at,updated_at)
                   VALUES ('mrun','completed',2,?,?,?,?)""",
                (now, now, now, now),
            )
            market.execute(
                """INSERT INTO market_pulses
                   (pulse_id,run_id,theme,theme_title,product_line,summary,signal_ids_json,
                    source_domains_json,source_urls_json,independent_sources,signal_count,
                    average_score,max_score,confidence,score,status,evidence_path,created_at,updated_at)
                   VALUES ('pulse-1','mrun','agent-demand','智能体安全需求','security-exploration',
                           '企业预算和治理需求上升','[]','[\"a.example\",\"b.example\"]',
                           '[\"https://a.example/x\",\"https://b.example/y\"]',2,2,70,80,0.8,82,
                           'new',?,?,?)""",
                (str(root / "evidence"), now, now),
            )
            market.commit()
            market.close()

            def fake_worker(_opportunity, run_dir, _config):
                (run_dir / "action-report.md").write_text("done", encoding="utf-8")
                (run_dir / "result.json").write_text("{}", encoding="utf-8")
                return {"status": "completed", "summary": "市场验证包完成", "next_action": "", "metrics": {}, "error": ""}

            result = run_cycle(config, worker=fake_worker)

            self.assertEqual(result["executions"][0]["title"], "验证市场机会：智能体安全需求")
            db = connect(Path(config["operations_db"]))
            opportunity = db.execute("SELECT * FROM autonomy_opportunities WHERE source_type='market_pulse'").fetchone()
            self.assertEqual(opportunity["action_kind"], "market_validation")
            evidence = json.loads(opportunity["evidence_json"])
            self.assertTrue(evidence["untrusted_external_data"])
            db.close()
            market = connect_market(market_db_path)
            self.assertEqual(market.execute("SELECT status FROM market_pulses WHERE pulse_id='pulse-1'").fetchone()[0], "evaluated")
            market.close()

    def test_superseded_market_pulse_dismisses_stale_open_opportunity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            market_db_path = root / "market.db"
            config["market_signals_db"] = str(market_db_path)
            config["standing_missions"] = []
            market = connect_market(market_db_path)
            now = "2026-07-15T00:00:00+00:00"
            market.execute(
                """INSERT INTO market_radar_runs
                   (run_id,status,query_count,started_at,completed_at,created_at,updated_at)
                   VALUES ('mrun','completed',2,?,?,?,?)""",
                (now, now, now, now),
            )
            market.execute(
                """INSERT INTO market_pulses
                   (pulse_id,run_id,theme,theme_title,product_line,summary,signal_ids_json,
                    source_domains_json,source_urls_json,independent_sources,signal_count,
                    average_score,max_score,confidence,score,status,evidence_path,created_at,updated_at)
                   VALUES ('pulse-stale','mrun','theme','主题','company','summary','[]','[]','[]',
                           2,2,70,80,0.8,82,'new',?,?,?)""",
                (str(root / "evidence"), now, now),
            )
            market.commit()
            market.close()

            db_path = Path(config["operations_db"])
            discover_opportunities(db_path, config)
            market = connect_market(market_db_path)
            market.execute("UPDATE market_pulses SET status='superseded' WHERE pulse_id='pulse-stale'")
            market.commit()
            market.close()
            discover_opportunities(db_path, config)

            db = connect(db_path)
            status = db.execute(
                "SELECT status FROM autonomy_opportunities WHERE source_ref='pulse-stale'"
            ).fetchone()[0]
            db.close()
            self.assertEqual(status, "dismissed")

    def test_recently_evaluated_market_theme_is_cooled_down_without_material_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            market_db_path = root / "market.db"
            config["market_signals_db"] = str(market_db_path)
            config["standing_missions"] = []
            config["market_theme_cooldown_hours"] = 168
            config["market_material_score_delta"] = 8
            now = datetime.now(timezone.utc)
            now_text = now.isoformat(timespec="seconds")
            market = connect_market(market_db_path)
            for run_id in ("old-run", "new-run"):
                market.execute(
                    """INSERT INTO market_radar_runs
                       (run_id,status,query_count,started_at,completed_at,created_at,updated_at)
                       VALUES (?,'completed',2,?,?,?,?)""",
                    (run_id, now_text, now_text, now_text, now_text),
                )
            pulse_values = (
                "theme", "主题", "company", "summary", "[]", "[\"a\",\"b\"]",
                "[\"https://a/x\",\"https://b/y\"]", 2, 2, 70, 82, 0.8,
                str(root / "evidence"), now_text, now_text,
            )
            market.execute(
                """INSERT INTO market_pulses
                   (pulse_id,run_id,theme,theme_title,product_line,summary,signal_ids_json,
                    source_domains_json,source_urls_json,independent_sources,signal_count,
                    average_score,max_score,confidence,score,status,evidence_path,created_at,updated_at)
                   VALUES ('old-pulse','old-run',?,?,?,?,?,?,?,?,?,?,?,?,82,'evaluated',?,?,?)""",
                pulse_values,
            )
            market.execute(
                """INSERT INTO market_pulses
                   (pulse_id,run_id,theme,theme_title,product_line,summary,signal_ids_json,
                    source_domains_json,source_urls_json,independent_sources,signal_count,
                    average_score,max_score,confidence,score,status,evidence_path,created_at,updated_at)
                   VALUES ('new-pulse','new-run',?,?,?,?,?,?,?,?,?,?,?,?,84,'new',?,?,?)""",
                pulse_values,
            )
            market.commit()
            market.close()
            db_path = Path(config["operations_db"])
            db = connect(db_path)
            db.execute(
                """INSERT INTO autonomy_opportunities
                   (opportunity_id,idempotency_key,source_type,source_ref,product_line,title,
                    description,action_kind,risk_level,requires_approval,approval_granted,score,
                    status,evidence_json,created_at,updated_at,completed_at)
                   VALUES ('old-opp','market-pulse:old-pulse','market_pulse','old-pulse','company',
                           'old','old','market_validation','low',0,0,82,'completed',?,?,?,?)""",
                (json.dumps({"theme": "theme"}), now_text, now_text, now_text),
            )
            db.commit()
            db.close()

            discover_opportunities(db_path, config, now=now + timedelta(hours=1))

            market = connect_market(market_db_path)
            new_status = market.execute("SELECT status FROM market_pulses WHERE pulse_id='new-pulse'").fetchone()[0]
            market.close()
            db = connect(db_path)
            created = db.execute("SELECT COUNT(*) FROM autonomy_opportunities WHERE source_ref='new-pulse'").fetchone()[0]
            db.close()
            self.assertEqual(new_status, "dismissed")
            self.assertEqual(created, 0)

    def test_operator_proactively_delivers_cycle_summary_to_management_origin(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["proactive_delivery"] = True
            config["proactive_delivery_platforms"] = ["weixin"]
            router = sqlite3.connect(config["router_db"])
            router.execute(
                """CREATE TABLE route_events (
                   delivery_platform TEXT,delivery_chat_id TEXT,delivery_thread_id TEXT,
                   delivery_user_id TEXT,updated_at TEXT)"""
            )
            router.execute(
                "INSERT INTO route_events VALUES ('weixin','chat-1','','user-1','2026-07-15T00:00:00+00:00')"
            )
            router.commit()
            router.close()
            delivered = []

            def fake_worker(_opportunity, run_dir, _config):
                (run_dir / "action-report.md").write_text("done", encoding="utf-8")
                (run_dir / "result.json").write_text("{}", encoding="utf-8")
                return {"status": "completed", "summary": "主动完成", "next_action": "", "metrics": {}, "error": ""}

            def fake_deliverer(_config, origin, message):
                delivered.append((origin, message))
                return True, ""

            result = run_cycle(config, worker=fake_worker, deliverer=fake_deliverer)

            self.assertTrue(result["delivered"])
            self.assertEqual(delivered[0][0]["chat_id"], "chat-1")
            self.assertIn("公司自驱日报", delivered[0][1])
            self.assertIn("主动完成", delivered[0][1])

    def test_operator_queues_default_delivery_in_notification_outbox(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["proactive_delivery"] = True
            config["proactive_delivery_platforms"] = ["weixin"]
            router = sqlite3.connect(config["router_db"])
            router.execute(
                """CREATE TABLE route_events (
                   delivery_platform TEXT,delivery_chat_id TEXT,delivery_thread_id TEXT,
                   delivery_user_id TEXT,updated_at TEXT)"""
            )
            router.execute(
                "INSERT INTO route_events VALUES ('weixin','chat-1','','user-1','2026-07-15T00:00:00+00:00')"
            )
            router.commit()
            router.close()

            result = run_cycle(config, worker=lambda *_args: {
                "status": "completed", "summary": "已排队", "next_action": "", "metrics": {}, "error": "",
            })

            self.assertFalse(result["delivered"])
            self.assertEqual(result["delivery_error"], "queued in notification outbox")
            rows = pending_outbox(Path(config["operations_db"]))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "autonomy_cycle")

    def test_operator_surfaces_non_allowlisted_delivery_in_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["proactive_delivery"] = True
            config["proactive_delivery_platforms"] = ["weixin"]
            config["delivery_fallback_path"] = str(root / "dead-letters.jsonl")
            router = sqlite3.connect(config["router_db"])
            router.execute(
                """CREATE TABLE route_events (
                   delivery_platform TEXT,delivery_chat_id TEXT,delivery_thread_id TEXT,
                   delivery_user_id TEXT,updated_at TEXT)"""
            )
            router.execute(
                "INSERT INTO route_events VALUES ('qq','chat-1','','user-1','2026-07-15T00:00:00+00:00')"
            )
            router.commit()
            router.close()

            result = run_cycle(config, worker=lambda *_args: {
                "status": "completed", "summary": "done", "next_action": "", "metrics": {}, "error": "",
            })

            self.assertFalse(result["delivered"])
            self.assertIn("terminal:", result["delivery_error"])
            records = [json.loads(line) for line in Path(config["delivery_fallback_path"]).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["kind"], "autonomy-cycle")
            self.assertIn("公司自驱日报", records[0]["message"])

    def test_worker_usage_is_linked_by_operator_run_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_db = root / "state.db"
            run_dir = root / "run"
            db = sqlite3.connect(state_db)
            db.executescript(
                """
                CREATE TABLE sessions (
                    id TEXT,model TEXT,input_tokens INTEGER,output_tokens INTEGER,
                    cache_read_tokens INTEGER,cache_write_tokens INTEGER,reasoning_tokens INTEGER,
                    tool_call_count INTEGER,estimated_cost_usd REAL,actual_cost_usd REAL,cost_status TEXT
                );
                CREATE TABLE messages (session_id TEXT,role TEXT,content TEXT,timestamp REAL);
                """
            )
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                "worker-1", "model-a", 10, 2, 30, 0, 1, 4, 0.1, None, "estimated",
            ))
            db.execute("INSERT INTO messages VALUES (?,?,?,?)", (
                "worker-1", "user", f"产物目录：{run_dir}", 1.0,
            ))
            db.commit()
            db.close()

            usage = worker_usage(run_dir, {"hermes_state_db": str(state_db)})

            self.assertEqual(usage["id"], "worker-1")
            self.assertEqual(usage["tool_call_count"], 4)

    def test_failed_worker_auto_retries_with_degradation_then_exhausts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["auto_retry_enabled"] = True
            config["auto_retry_max"] = 1
            db_path = Path(config["operations_db"])

            def failed_worker(_opportunity, _run_dir, _config):
                return {"status": "failed", "summary": "", "next_action": "", "metrics": {}, "error": "boom"}

            # First failure: re-opened for a degraded retry rather than parked failed.
            result = run_cycle(
                config,
                now=datetime(2026, 7, 15, 1, tzinfo=timezone.utc),
                worker=failed_worker,
            )
            self.assertEqual(result["executions"][0]["status"], "failed")
            self.assertTrue(result["executions"][0]["auto_retry"])
            opportunity_id = result["executions"][0]["opportunity_id"]
            db = connect(db_path)
            row = db.execute(
                "SELECT status,retry_count FROM autonomy_opportunities WHERE opportunity_id=?",
                (opportunity_id,),
            ).fetchone()
            self.assertEqual(row["status"], "open")
            self.assertEqual(row["retry_count"], 1)
            self.assertEqual(db.execute("SELECT status FROM operational_runs").fetchone()[0], "failed")
            db.close()

            # Second failure exhausts the ladder: the opportunity is now failed.
            result2 = run_cycle(
                config,
                now=datetime(2026, 7, 15, 2, tzinfo=timezone.utc),
                worker=failed_worker,
            )
            self.assertFalse(result2["executions"][0]["auto_retry"])
            db = connect(db_path)
            self.assertEqual(db.execute(
                "SELECT status FROM autonomy_opportunities WHERE opportunity_id=?", (opportunity_id,)
            ).fetchone()[0], "failed")
            db.close()

            # A parked failed opportunity can still be requeued manually.
            self.assertTrue(requeue_failed(db_path, opportunity_id))
            db = connect(db_path)
            self.assertEqual(db.execute(
                "SELECT status FROM autonomy_opportunities WHERE opportunity_id=?", (opportunity_id,)
            ).fetchone()[0], "open")
            db.close()

    def test_corrupt_retry_counter_does_not_crash_cycle(self):
        # A corrupt retry_count in the opportunity row must not crash the
        # cycle: it degrades to 0 via _safe_counter and the auto-retry ladder
        # still re-opens the opportunity with retry_count=1.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["auto_retry_enabled"] = True
            config["auto_retry_max"] = 1
            db_path = Path(config["operations_db"])

            def failed_worker(_opportunity, _run_dir, _config):
                return {"status": "failed", "summary": "", "next_action": "", "metrics": {}, "error": "boom"}

            # Plant a corrupt counter before the cycle picks the row up.
            db = connect(db_path)
            db.execute(
                "UPDATE autonomy_opportunities SET retry_count='not-a-number' WHERE status='open'"
            )
            db.commit()
            db.close()

            result = run_cycle(
                config,
                now=datetime(2026, 7, 15, 1, tzinfo=timezone.utc),
                worker=failed_worker,
            )
            self.assertEqual(result["executions"][0]["status"], "failed")
            self.assertTrue(result["executions"][0]["auto_retry"])
            opportunity_id = result["executions"][0]["opportunity_id"]
            db = connect(db_path)
            row = db.execute(
                "SELECT status,retry_count FROM autonomy_opportunities WHERE opportunity_id=?",
                (opportunity_id,),
            ).fetchone()
            self.assertEqual(row["status"], "open")
            self.assertEqual(row["retry_count"], 1)
            db.close()

    def test_failed_worker_parks_immediately_when_auto_retry_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["auto_retry_enabled"] = False
            db_path = Path(config["operations_db"])

            def failed_worker(_opportunity, _run_dir, _config):
                return {"status": "failed", "summary": "", "next_action": "", "metrics": {}, "error": "boom"}

            result = run_cycle(
                config,
                now=datetime(2026, 7, 15, 1, tzinfo=timezone.utc),
                worker=failed_worker,
            )

            self.assertEqual(result["executions"][0]["status"], "failed")
            self.assertFalse(result["executions"][0]["auto_retry"])
            db = connect(db_path)
            self.assertEqual(db.execute("SELECT status FROM autonomy_opportunities").fetchone()[0], "failed")
            db.close()

    def test_empty_output_completion_is_retried_and_not_marked_evaluated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = self._config(root)
            config["auto_retry_enabled"] = True
            config["auto_retry_max"] = 1
            db_path = Path(config["operations_db"])

            def empty_worker(_opportunity, run_dir, _config):
                (run_dir / "action-report.md").write_text("", encoding="utf-8")
                (run_dir / "result.json").write_text("{}", encoding="utf-8")
                return {"status": "completed", "summary": "", "next_action": "", "metrics": {}, "error": ""}

            result = run_cycle(
                config,
                now=datetime(2026, 7, 15, 1, tzinfo=timezone.utc),
                worker=empty_worker,
            )

            self.assertTrue(result["executions"][0]["empty_output"])
            self.assertTrue(result["executions"][0]["auto_retry"])
            db = connect(db_path)
            row = db.execute("SELECT status,retry_count FROM autonomy_opportunities").fetchone()
            self.assertEqual(row["status"], "open")
            self.assertEqual(row["retry_count"], 1)
            db.close()

    def test_worker_model_degrades_along_ladder(self):
        config = {"worker_model_ladder": ["primary", "flash"]}
        self.assertEqual(_worker_model(config, 0), "primary")
        self.assertEqual(_worker_model(config, 1), "flash")
        # Beyond the ladder length we stay on the cheapest tier.
        self.assertEqual(_worker_model(config, 5), "flash")
        # An empty ladder disables model override entirely.
        self.assertEqual(_worker_model({"worker_model_ladder": []}, 0), "")

    def test_safe_counter_defaults_and_fallbacks(self):
        self.assertEqual(_safe_counter(None), 0)
        self.assertEqual(_safe_counter("7"), 7)
        self.assertEqual(_safe_counter(-3), 0)
        for bad in ("abc", [1], {"a": 1}, float("inf")):
            self.assertEqual(_safe_counter(bad), 0, bad)

    def test_safe_float_defaults_and_fallbacks(self):
        self.assertEqual(_safe_float(None), 0.0)
        self.assertEqual(_safe_float("2.5"), 2.5)
        self.assertEqual(_safe_float(-1.0), -1.0)
        for bad in ("abc", [1], {"a": 1}, "nan", float("inf"), float("-inf")):
            self.assertEqual(_safe_float(bad), 0.0, bad)


if __name__ == "__main__":
    unittest.main()
