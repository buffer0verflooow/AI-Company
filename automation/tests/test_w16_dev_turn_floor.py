"""W16-① 回归:dev 线轮数下限 24(2026-09-20 用户裁定 40→24;W15 分档不得压低 dev 的多轮工序)。

背景(实测,2026-09-19):W15-b② 的 `v2_task_plan` 对 `run_type="dev"` 也按简报
分档 ⇒ dev 1KB→12 轮、6.4KB→18、30KB→24;而 W14 交付时 dev 线固定
`--max-turns 24`(蜂群 dev 档硬顶)。dev 是"改文件→跑测试→看失败→再改"的多轮
工序,轮数下限必须保住 24。

断言:
  ① dev 1KB ⇒ 24 轮;
  ② dev 30KB ⇒ 24 轮(升档后仍被下限抬回 24);
  ③ `why` 写出"dev 是多轮工序,轮数下限 24(2026-09-20 用户裁定 40→24,与实测 ~19k/轮对齐)";
  ④ 非 dev 档分档**逐字不变**(回归锁,防"顺手改坏");
  ⑤ `max_turns` 永不超过 `_V2_HARD_MAX_TURNS[run_type]`;
  ⑥ 下限读数 = 单一来源 `_V2_HARD_MAX_TURNS["dev"]`(改表即变,不是写死 40);
  ⑦ fixed 模式 dev 仍 24(改前口径保持)。

改前对照(变异反证):把 `permission == "dev"` 的 `max(tier, 24)` 分支去掉
⇒ ①②③ 红;把非 dev golden 改一处 ⇒ ④ 红。
"""
from __future__ import annotations

import unittest

from automation.company_router import (
    _V2_DEV_MAX_TURNS,
    _V2_HARD_MAX_TURNS,
    _V2_PERMISSION_BY_RUN_TYPE,
    v2_task_plan,
)

#: 非 dev 档 golden(改前实测逐字;任何"顺手改坏分档"必须让本表红)
_NON_DEV_GOLDEN = {
    ("ops", 1000): (12, 240000),
    ("ops", 6400): (18, 360000),
    ("ops", 30000): (24, 400000),     # 下夹 24×20000=480000 ⇒ 撞 fixture cap 400000
    ("vuln", 1000): (12, 240000),
    ("vuln", 6400): (18, 360000),
    ("vuln", 30000): (24, 400000),
    ("content", 1000): (12, 240000),
    ("content", 6400): (12, 240000),
    ("content", 30000): (12, 360000),
}


def _cfg(**extra):
    gray = {
        "enabled": True,
        "run_types": ["content", "vuln", "ops", "dev"],
        "task_types": [],
        "ratio_pct": 100,
        "client_source": "company-router",
    }
    base = {"swarm_v2_gray": gray, "swarm_v2_budget_cap": 400000}
    base.update(extra)
    return base


class DevTurnFloorTests(unittest.TestCase):
    def test_1_dev_small_brief_keeps_24_turns(self):
        plan = v2_task_plan(_cfg(), message="x" * 10, task_book=None, run_type="dev")
        self.assertEqual(plan["max_turns"], 24)
        self.assertEqual(plan["max_turns"], _V2_HARD_MAX_TURNS["dev"])

    def test_2_dev_large_brief_keeps_24_turns(self):
        plan = v2_task_plan(_cfg(), message="x" * 30000, task_book=None, run_type="dev")
        self.assertEqual(plan["max_turns"], 24)
        self.assertEqual(plan["max_turns"], _V2_HARD_MAX_TURNS["dev"])

    def test_3_dev_why_states_multi_turn_floor(self):
        plan = v2_task_plan(_cfg(), message="x" * 6400, task_book=None, run_type="dev")
        self.assertIn("dev 是多轮工序,轮数下限 24(2026-09-20 用户裁定 40→24,与实测 ~19k/轮对齐)", plan["why"])

    def test_4_non_dev_tiers_verbatim(self):
        for (run_type, size), (turns, budget) in _NON_DEV_GOLDEN.items():
            with self.subTest(run_type=run_type, size=size):
                plan = v2_task_plan(_cfg(), message="x" * size, task_book=None,
                                    run_type=run_type)
                self.assertEqual((plan["max_turns"], plan["token_budget"]),
                                 (turns, budget))

    def test_5_never_exceeds_hard_cap_for_any_run_type(self):
        for run_type, permission in _V2_PERMISSION_BY_RUN_TYPE.items():
            for size in (10, 1000, 8192, 100000):
                with self.subTest(run_type=run_type, size=size):
                    plan = v2_task_plan(_cfg(), message="x" * size, task_book=None,
                                        run_type=run_type)
                    self.assertLessEqual(plan["max_turns"],
                                         _V2_HARD_MAX_TURNS[permission])

    def test_6_floor_reads_single_source_table(self):
        # 改表即变 ⇒ 证明读到的是 `_V2_HARD_MAX_TURNS["dev"]` 而不是写死 40。
        import automation.company_router as cr
        patched = dict(cr._V2_HARD_MAX_TURNS)
        patched["dev"] = 41
        original = cr._V2_HARD_MAX_TURNS
        cr._V2_HARD_MAX_TURNS = patched
        try:
            plan = v2_task_plan(_cfg(), message="x" * 1000, task_book=None,
                                run_type="dev")
        finally:
            cr._V2_HARD_MAX_TURNS = original
        self.assertEqual(plan["max_turns"], 41)

    def test_7_fixed_mode_dev_is_still_24(self):
        plan = v2_task_plan(_cfg(swarm_v2_budget_mode="fixed"), message="x" * 6400,
                            task_book=None, run_type="dev")
        self.assertEqual(plan["max_turns"], _V2_DEV_MAX_TURNS)
        self.assertEqual(plan["max_turns"], 24)


if __name__ == "__main__":
    unittest.main()
