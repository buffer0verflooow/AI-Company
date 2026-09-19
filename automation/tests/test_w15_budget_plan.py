"""W15-b② 回归:预算/轮数按任务复杂度(纯函数 + 三处同源)。

派工书 §3.2 断言:
  ① 同输入 ⇒ 同计划(确定性,连跑两次逐字相同);
  ② 四档边界(1.9KB/2.0KB/8.0KB/24.0KB 各两侧);
  ③ 下夹:token_budget ≥ max_turns × 15000;
  ④ 上夹:超 `swarm_v2_budget_cap` 被压到 cap 且 why 说明;
  ⑤ 轮数永不超过蜂群该档硬顶(跨仓对拍);
  ⑥ fixed 模式与改前逐字一致(12/40 轮 + 灰度 token_budget/est_tokens);
  ⑦ 三处同源:run create --token-budget == market publish --est == worker
     --max-tokens-budget,且轮数 = focus_params.budget_plan.max_turns。

改前对照:`v2_task_plan`/`_V2_HARD_MAX_TURNS` 不存在 ⇒ ①②③④⑤⑥⑦ 全红。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.company_router import (
    _V2_HARD_MAX_TURNS,
    _V2_PERMISSION_BY_RUN_TYPE,
    build_v2_content_worker_cmd,
    build_v2_dev_worker_cmd,
    build_v2_research_worker_cmd,
    classify_message,
    submit_research_v2,
    v2_task_plan,
    v2_worker_plan,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
RESEARCH_MESSAGE = "调研一下竞品 X 的技术方案"


def _cfg(**extra):
    gray = {
        "enabled": True,
        "run_types": ["content", "vuln", "ops", "dev"],
        "task_types": [],
        "ratio_pct": 100,
        "client_source": "company-router",
    }
    base = {
        "enabled": True,
        "dispatch_security": True,
        "dispatch_research": True,
        "swarm_repo": SWARM_REPO,
        "swarm_v2_db": "/nonexistent/swarm_v2.db",
        "swarm_v2_agent": "content-writer-1",
        "swarm_v2_judge": "content-judge-1",
        "swarm_v2_security_agent": "sec-exec-1",
        "swarm_v2_security_judge": "sec-judge-1",
        "swarm_v2_research_agent": "res-exec-1",
        "swarm_v2_research_judge": "res-judge-1",
        "swarm_v2_gray": gray,
        "log_dir": "/nonexistent/logs",
        "content_job_dir": "/nonexistent/content-jobs",
    }
    base.update(extra)
    return base


def _why(plan):
    return plan["why"]


class DeterminismAndTiersTests(unittest.TestCase):
    def test_1_same_input_same_plan(self):
        a = v2_task_plan(_cfg(), message="x" * 6400, task_book=None, run_type="ops")
        b = v2_task_plan(_cfg(), message="x" * 6400, task_book=None, run_type="ops")
        self.assertEqual(a, b)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_2_tier_boundaries(self):
        cases = [
            (2047, "档 0", 12), (2048, "档 1", 18),
            (8191, "档 1", 18), (8192, "档 2", 24),
            (24575, "档 2", 24), (24576, "档 3", 24),
        ]
        for size, tier, turns in cases:
            with self.subTest(size=size):
                plan = v2_task_plan(_cfg(), message="x" * size, task_book=None,
                                    run_type="ops")
                self.assertEqual(plan["max_turns"], turns)
                self.assertIn(tier, _why(plan))

    def test_escalation_on_criteria_or_mcp(self):
        base = v2_task_plan(_cfg(), message="x" * 1000, task_book=None, run_type="ops")
        self.assertEqual(base["max_turns"], 12)
        criteria = [{"argv": ["wc", "-l", "cleanup-plan.md"], "expect_exit": 0} for _ in range(4)]
        esc = v2_task_plan(_cfg(), message="x" * 1000,
                           task_book={"exec_criteria": criteria}, run_type="ops")
        self.assertEqual(esc["max_turns"], 18)      # 上一档
        mcp = v2_task_plan(_cfg(), message="x" * 1000,
                           task_book={"required_capabilities": ["command", "mcp"]},
                           run_type="ops")
        self.assertEqual(mcp["max_turns"], 18)

    def test_3_lower_clamp(self):
        for size in (100, 2048, 8192):
            plan = v2_task_plan(_cfg(), message="x" * size, task_book=None,
                                run_type="ops")
            self.assertGreaterEqual(plan["token_budget"],
                                    plan["max_turns"] * 15000)
        smallest = v2_task_plan(_cfg(), message="x" * 10, task_book=None, run_type="ops")
        self.assertGreaterEqual(smallest["token_budget"], 180000)

    def test_4_upper_clamp(self):
        plan = v2_task_plan(_cfg(swarm_v2_budget_cap=50000), message="x" * 6400,
                            task_book=None, run_type="ops")
        self.assertEqual(plan["token_budget"], 50000)
        self.assertIn("上夹", _why(plan))
        self.assertIn("50000", _why(plan))

    def test_5_turn_cap_never_exceeds_swarm_tier(self):
        big = "x" * 100000
        for run_type, permission in _V2_PERMISSION_BY_RUN_TYPE.items():
            with self.subTest(run_type=run_type):
                plan = v2_task_plan(_cfg(), message=big, task_book=None,
                                    run_type=run_type)
                self.assertLessEqual(plan["max_turns"], _V2_HARD_MAX_TURNS[permission])
        # 内容线(write)硬顶 12 生效
        content = v2_task_plan(_cfg(), message="x" * 6400, task_book=None,
                               run_type="content")
        self.assertEqual(content["max_turns"], 12)
        self.assertIn("硬顶 12", _why(content))

    def test_6_fixed_mode_is_verbatim_pre_change(self):
        fixed = {"swarm_v2_budget_mode": "fixed",
                 "swarm_v2_gray": {"token_budget": 100000, "est_tokens": 100000}}
        for run_type, turns in (("content", 12), ("ops", 12), ("vuln", 12),
                                ("dev", 40)):
            with self.subTest(run_type=run_type):
                plan = v2_task_plan(_cfg(**fixed), message="x" * 6400,
                                    task_book=None, run_type=run_type)
                self.assertEqual((plan["max_turns"], plan["token_budget"],
                                  plan["est_tokens"]), (turns, 100000, 100000))
        # 显式 override(仍受档位硬顶)
        over = v2_task_plan(_cfg(swarm_v2_budget_mode="fixed", token_budget=12345,
                                 max_turns=9), message="x", task_book=None,
                            run_type="ops")
        self.assertEqual((over["max_turns"], over["token_budget"]), (9, 12345))
        dev_over = v2_task_plan(_cfg(swarm_v2_budget_mode="fixed", max_turns=99),
                                message="x", task_book=None, run_type="dev")
        self.assertEqual(dev_over["max_turns"], 40)

    def test_bad_budget_mode_or_cap_fails_loudly(self):
        with self.assertRaises(ValueError):
            v2_task_plan(_cfg(swarm_v2_budget_mode="turbo"), message="x",
                         task_book=None, run_type="ops")
        with self.assertRaises(ValueError):
            v2_task_plan(_cfg(swarm_v2_budget_cap=0), message="x",
                         task_book=None, run_type="ops")
        with self.assertRaises(ValueError):
            v2_task_plan(_cfg(), message="x", task_book=None, run_type="nope")


class CrossRepoParityTests(unittest.TestCase):
    def test_7_hard_caps_match_swarm_single_source(self):
        repo = Path(SWARM_REPO)
        if not (repo / "src" / "swarm_v2" / "agent_runtime.py").exists():
            self.skipTest("swarm 检出不在本机")
        inserted = str(repo) not in sys.path
        if inserted:
            sys.path.insert(0, str(repo))
        try:
            from src.swarm_v2 import agent_runtime
        finally:
            if inserted and str(repo) in sys.path:
                sys.path.remove(str(repo))
        self.assertEqual(_V2_HARD_MAX_TURNS,
                         dict(agent_runtime.HARD_MAX_TURNS_BY_PERMISSION))


class ThreeSiteSamePlanTests(unittest.TestCase):
    def test_8_publish_and_launch_share_one_plan(self):
        message = "x" * 6400
        with tempfile.TemporaryDirectory() as td:
            config = _cfg(log_dir=str(Path(td) / "logs"),
                          content_job_dir=str(Path(td) / "content-jobs"))
            decision = classify_message(RESEARCH_MESSAGE)
            with mock.patch("automation.company_router.v2_swarm_command",
                            side_effect=[{"run_id": "r"}, {"task_id": "t"}]) as cli:
                out = submit_research_v2(
                    config, decision=decision, message=message,
                    session_id="s", platform="cli",
                    gray={"hit": True, "reason": "ratio_hit"},
                    task_book={"exec_criteria": [
                        {"argv": ["wc", "-l", "cleanup-plan.md"], "expect_exit": 0} for _ in range(4)]})
            run_id = out["run_id"]
            create = cli.call_args_list[0].args
            publish = cli.call_args_list[1].args
            focus = json.loads(publish[publish.index("--focus") + 1])
            plan = focus["budget_plan"]
            self.assertIn("max_turns", plan)
            # run create --token-budget == plan.token_budget
            self.assertEqual(create[create.index("--token-budget") + 1],
                             str(plan["token_budget"]))
            # market publish --est == plan.est_tokens
            self.assertEqual(publish[publish.index("--est") + 1],
                             str(plan["est_tokens"]))
            # 拉起侧复用同一计划(进程内登记)
            worker_plan = v2_worker_plan(config, run_id, run_type="ops")
            self.assertEqual(worker_plan, plan)
            cmd = build_v2_research_worker_cmd(config, run_id, plan=worker_plan)
            self.assertEqual(cmd[cmd.index("--max-turns") + 1], str(plan["max_turns"]))
            self.assertEqual(cmd[cmd.index("--max-tokens-budget") + 1],
                             str(plan["token_budget"]))
            # 任务书声明 4 条判据 ⇒ 从档 1 升到档 2(24 轮)
            self.assertEqual(plan["max_turns"], 24)

    def test_9_fixed_mode_three_sites_verbatim(self):
        message = "x" * 6400
        with tempfile.TemporaryDirectory() as td:
            config = _cfg(log_dir=str(Path(td) / "logs"),
                          content_job_dir=str(Path(td) / "content-jobs"),
                          swarm_v2_budget_mode="fixed")
            decision = classify_message(RESEARCH_MESSAGE)
            with mock.patch("automation.company_router.v2_swarm_command",
                            side_effect=[{"run_id": "r"}, {"task_id": "t"}]) as cli:
                out = submit_research_v2(
                    config, decision=decision, message=message,
                    session_id="s", platform="cli", gray={"hit": True})
            create = cli.call_args_list[0].args
            publish = cli.call_args_list[1].args
            self.assertEqual(create[create.index("--token-budget") + 1], "100000")
            self.assertEqual(publish[publish.index("--est") + 1], "100000")
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["budget_plan"]["max_turns"], 12)
            cmd = build_v2_research_worker_cmd(
                config, out["run_id"],
                plan=v2_worker_plan(config, out["run_id"], run_type="ops"))
            self.assertEqual(cmd[cmd.index("--max-turns") + 1], "12")
            self.assertEqual(cmd[cmd.index("--max-tokens-budget") + 1], "100000")

    def test_10_direct_builders_keep_pre_change_fallback(self):
        # 未经发布计划直接调用(既有测试/手工拉起)⇒ 改前固定口径,逐字不变
        config = _cfg()
        security = build_v2_research_worker_cmd(config, "company-ops-000000000001")
        self.assertEqual(security[security.index("--max-turns") + 1], "12")
        self.assertEqual(security[security.index("--max-tokens-budget") + 1], "100000")
        dev = build_v2_dev_worker_cmd(config, "company-dev-000000000001",
                                      dev_repo="/tmp")
        self.assertEqual(dev[dev.index("--max-turns") + 1], "40")
        content = build_v2_content_worker_cmd(config, "company-content-000000000001")
        self.assertEqual(content[content.index("--max-turns") + 1], "12")


if __name__ == "__main__":
    unittest.main()
