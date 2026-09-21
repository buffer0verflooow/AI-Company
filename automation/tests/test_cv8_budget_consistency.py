"""CV8 回归:预算 × 轮数**自洽**(2026-09-21)。

实证(蜂群 run 账本 `tokens_spent ÷ 轮数`):
  - `company-dev-84c1c4c4cf64`:541,045 / 17 = **31,826/轮** ⇒ budget_exceeded,零产出;
  - `company-dev-8228381db841`:525,386 / 23 = **22,843/轮** ⇒ budget_exceeded,零产出;
  - 两者都拿到 `token_budget=480,000 ∧ max_turns=24` —— 按实测每轮口径只够 15~21 轮,
    **结构上跑不到 24 轮**(通用下夹 20k 在 dev 线不够)。

本批两件事:
  ① `dev` 线每轮下夹按实测取 25k(其余线仍 20k,不受影响);
  ② 上夹后加**自洽自检**:打不满就按预算下修轮数并留痕;连一轮都打不起 ⇒ 拒发(零写库)。
`fixed` 模式 = 改前口径逃生舱(操作者自报数值),不受该闸;生产配置无该键 ⇒ 走 auto。

改前对照:`_V2_BUDGET_MIN_PER_TURN_BY_RUN_TYPE` / 自洽自检不存在 ⇒ ②③④ 全红
(③ 拿到 24 轮 × 400k 的自相矛盾计划;④ 静默发出一轮都打不起的配置)。
"""
from __future__ import annotations

import pytest

from automation.company_router import (
    _V2_BUDGET_MIN_PER_TURN,
    _V2_BUDGET_MIN_PER_TURN_BY_RUN_TYPE,
    _V2_HARD_MAX_TURNS,
    _v2_min_per_turn,
    v2_task_plan,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"


def _cfg(**extra):
    gray = {"enabled": True, "run_types": ["content", "vuln", "ops", "dev"],
            "task_types": [], "ratio_pct": 100, "client_source": "company-router"}
    base = {
        "enabled": True, "dispatch_security": True, "dispatch_research": True,
        "swarm_repo": SWARM_REPO, "swarm_v2_db": "/nonexistent/swarm_v2.db",
        "swarm_v2_agent": "content-writer-1", "swarm_v2_judge": "content-judge-1",
        "swarm_v2_security_agent": "sec-exec-1", "swarm_v2_security_judge": "sec-judge-1",
        "swarm_v2_research_agent": "res-exec-1", "swarm_v2_research_judge": "res-judge-1",
        "swarm_v2_gray": gray, "log_dir": "/nonexistent/logs",
        "content_job_dir": "/nonexistent/content-jobs",
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# ① dev 线每轮下夹按实测对齐(其余线不动)
# ---------------------------------------------------------------------------

def test_dev_floor_uses_measured_per_turn():
    """dev:24 轮 × 25k = 600,000(改前 480,000 ⇒ 结构性跑不到 24 轮)。"""
    plan = v2_task_plan(_cfg(), message="x" * 3000, task_book=None, run_type="dev")
    assert plan["max_turns"] == _V2_HARD_MAX_TURNS["dev"] == 24
    assert plan["token_budget"] == 24 * _v2_min_per_turn("dev") == 600000
    assert "25000" in plan["why"]


def test_generic_lines_keep_20k_floor():
    """content/ops/vuln 的判据值仍是 20k(通用口径),本批不改它们的预算。"""
    assert _v2_min_per_turn("dev") == 25000
    for run_type in ("content", "ops", "vuln"):
        assert _v2_min_per_turn(run_type) == _V2_BUDGET_MIN_PER_TURN == 20000
    ops = v2_task_plan(_cfg(), message="x" * 3000, task_book=None, run_type="ops")
    assert ops["token_budget"] == 18 * 20000 == 360000
    content = v2_task_plan(_cfg(), message="x" * 3000, task_book=None, run_type="content")
    assert content["token_budget"] == 12 * 20000 == 240000
    assert _V2_BUDGET_MIN_PER_TURN_BY_RUN_TYPE == {"dev": 25000}


def test_production_config_plans_are_all_self_consistent():
    """生产口径(缺省 cap 800k)下:每条线、每个档位的计划**都自洽**。

    这正是本批要达到的终态 —— 改前 dev 线(480k/24 轮)不自洽,两次 CV1 撞
    `budget_exceeded` 且零产出。
    """
    for run_type in ("content", "ops", "vuln", "dev"):
        for size in (10, 2048, 8192, 24576, 100000):
            plan = v2_task_plan(_cfg(), message="x" * size, task_book=None,
                                run_type=run_type)
            assert plan["budget_selfcheck"]["ok"] is True, (run_type, size, plan)
            assert plan["token_budget"] >= plan["max_turns"] * _v2_min_per_turn(run_type)


# ---------------------------------------------------------------------------
# ② 上夹后自洽:轮数按预算下修(留痕)
# ---------------------------------------------------------------------------

def test_tight_cap_is_flagged_not_silent():
    """cap=400k(< 24×25k):W15 上夹 / W16 轮数下限是**锁定行为**(不改),但计划必须
    把"打不满"如实报出来(`budget_selfcheck.ok=false` + why 点名),不再静默。"""
    plan = v2_task_plan(_cfg(swarm_v2_budget_cap=400000), message="x" * 3000,
                        task_book=None, run_type="dev")
    assert plan["token_budget"] == 400000
    assert plan["max_turns"] == 24           # W16-① 锁定:dev 轮数下限不因预算被压低
    sc = plan["budget_selfcheck"]
    assert sc["ok"] is False
    assert sc["needed"] == 24 * 25000 == 600000
    assert sc["affordable_turns"] == 16      # 400000 ÷ 25000
    assert "上夹" in plan["why"] and "400000" in plan["why"]
    assert "不自洽" in plan["why"] and "只够 16 轮" in plan["why"]


def test_w15_upper_clamp_contract_still_holds():
    """W15-④ 锁定行为不回归:上夹永远赢、why 说明 cap、轮数不被压低。"""
    plan = v2_task_plan(_cfg(swarm_v2_budget_cap=50000), message="x" * 6400,
                        task_book=None, run_type="ops")
    assert plan["token_budget"] == 50000
    assert "上夹" in plan["why"] and "50000" in plan["why"]
    assert plan["max_turns"] == 18           # 分档值,未被预算改写
    assert plan["budget_selfcheck"]["ok"] is False   # 但如实报出打不满


# ---------------------------------------------------------------------------
# ③ 连一轮都打不起 ⇒ 拒发(零写库)
# ---------------------------------------------------------------------------

def test_unaffordable_budget_is_refused_loudly():
    """cap=20k < 每轮 25k ⇒ 拒发(改前:静默发出一个必然超顶的 run)。"""
    with pytest.raises(ValueError) as ei:
        v2_task_plan(_cfg(swarm_v2_budget_cap=20000), message="x" * 3000,
                     task_book=None, run_type="dev")
    msg = str(ei.value)
    assert "不自洽" in msg and "拒发" in msg and "25000" in msg
    # 边界:恰好等于每轮判据值 ⇒ 打得满 1 轮,不拒(但如实报"只够 1 轮")
    ok = v2_task_plan(_cfg(swarm_v2_budget_cap=25000), message="x" * 3000,
                      task_book=None, run_type="dev")
    assert ok["token_budget"] == 25000 and ok["max_turns"] == 24
    assert ok["budget_selfcheck"]["affordable_turns"] == 1
    assert ok["budget_selfcheck"]["ok"] is False


# ---------------------------------------------------------------------------
# ④ fixed 模式 = 改前口径逃生舱(不受自洽闸;生产配置无该键 ⇒ 走 auto)
# ---------------------------------------------------------------------------

def test_fixed_mode_is_exempt_and_documented():
    fixed = {"swarm_v2_budget_mode": "fixed",
             "swarm_v2_gray": {"token_budget": 100000, "est_tokens": 100000}}
    plan = v2_task_plan(_cfg(**fixed), message="x" * 6400, task_book=None,
                        run_type="dev")
    assert plan["token_budget"] == 100000 and plan["max_turns"] == 24
    assert "fixed" in plan["why"]
    # 生产配置(router_config.json)没有该键 ⇒ auto
    auto = v2_task_plan(_cfg(), message="x" * 6400, task_book=None, run_type="dev")
    assert auto["token_budget"] == 600000
