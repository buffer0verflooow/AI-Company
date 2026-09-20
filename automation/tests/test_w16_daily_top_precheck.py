"""W16-③ 回归:日顶预检(v2 run create 之前;不制造悬挂 run)。

缺陷(实测 2026-09-19):`company-ops-a69154536706` = `status=running`、0 task,
审计只有 `run_create` + `budget_cap(reason=daily_top)` —— `v2 run create` 成功、
`market publish` 被 NFR1 日顶拒发,留下一个永远不会被认领的幽灵 run。

断言:
  ① 预检超顶 ⇒ **零** `v2 run create` 调用(桩断言调用序)、零悬挂 run,文案带
     日顶值/已用值/本次需值/重置口径 = UTC 日历日;
  ② 未超顶 ⇒ 行为与改前逐字一致(与"读数不可得而降级"路径的 CLI 调用序列逐字相等);
  ③ 日顶值 = 蜂群单一来源(`src.swarm_v2.budget.DAILY_TOP_TOKENS`);改蜂群一处 ⇒
     公司侧预检边界随之改变(证明未在公司侧抄一份 500000);
  ④ 读数不可得(库不可读)⇒ 响亮降级为"不预检",绝不当 0,真实闸仍放行到 publish;
  ⑤ 四条提交口(content/security/research/dev)的 run create 前都有该预检。

改前对照(变异反证):删掉 run create 前的 `_v2_daily_top_precheck(...)` 调用
⇒ ①⑤ 红(超顶时仍会调 CLI);把 `swarm_budget.DAILY_TOP_TOKENS` 换成本地
500000 常量 ⇒ ③ 红。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from automation.company_router import (
    _V2_DAILY_TOP_DEGRADED,
    _v2_swarm_budget_modules,
    classify_message,
    submit_content_v2,
    submit_dev_v2,
    submit_research_v2,
    submit_security_v2,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
FIXED_UUID = uuid.UUID("00000000-0000-0000-0000-0000000000ab")
DEV_MESSAGE = "请重构这个模块的代码并让测试通过"


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _config(td: str, *, swarm_v2_db: str, dev_repo: str = "") -> dict:
    return {
        "enabled": True,
        "dispatch_security": True,
        "dispatch_research": True,
        "dispatch_dev": True,
        "dev_route_enabled": False,
        "swarm_repo": SWARM_REPO,
        "swarm_v2_db": swarm_v2_db,
        "swarm_v2_agent": "content-writer-1",
        "swarm_v2_judge": "content-judge-1",
        "swarm_v2_security_agent": "sec-exec-1",
        "swarm_v2_security_judge": "sec-judge-1",
        "swarm_v2_research_agent": "res-exec-1",
        "swarm_v2_research_judge": "res-judge-1",
        "swarm_v2_dev_agent": "dev-executor-1",
        "swarm_v2_dev_judge": "dev-verifier-1",
        "swarm_v2_dev_repo": dev_repo,
        "swarm_v2_gray": {
            "enabled": True,
            "run_types": ["content", "vuln", "ops", "dev"],
            "task_types": [],
            "ratio_pct": 100,
            "client_source": "company-router",
        },
        "log_dir": str(Path(td) / "logs"),
        "content_job_dir": str(Path(td) / "content-jobs"),
        "content_executor": str(Path(td) / "content_executor.py"),
    }


def _dev_decision():
    from automation.company_router import RouteDecision
    return RouteDecision(route="dev", confidence=1.0, action="dispatch_swarm",
                         reason="test", intent="custom")


def _quota_db(td: str, *, pool_spent: int = 0) -> Path:
    """最小只读库:只含日顶读数所需两表(不依赖蜂群 schema)。"""
    db = Path(td) / "swarm_v2.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE budget_pools(pool_type TEXT, quota INTEGER,"
                " spent INTEGER, day TEXT)")
    con.execute("CREATE TABLE agent_tasks(task_id TEXT PRIMARY KEY, run_id TEXT,"
                " token_cost INTEGER DEFAULT 0, escrowed_tokens INTEGER DEFAULT 0,"
                " status TEXT, funding_source TEXT, ended_at TEXT)")
    if pool_spent:
        con.execute("INSERT INTO budget_pools VALUES ('evaluation',0,?,?)",
                    (pool_spent, _day()))
    con.commit()
    con.close()
    return db


def _swarm_daily_top() -> int:
    """日顶读数 = 蜂群单一来源(`src.swarm_v2.budget.DAILY_TOP_TOKENS`)。

    不在公司侧/测试里抄常量:日顶一改,公司侧预检边界与这些种子断言同时跟着变。
    """
    inserted = SWARM_REPO not in sys.path
    if inserted:
        sys.path.insert(0, SWARM_REPO)
    try:
        from src.swarm_v2 import budget as _swarm_budget
        return int(_swarm_budget.DAILY_TOP_TOKENS)
    finally:
        if inserted and SWARM_REPO in sys.path:
            sys.path.remove(SWARM_REPO)


class DailyTopPrecheckTests(unittest.TestCase):
    def test_1_over_top_rejects_before_any_run_create(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            db = _quota_db(td, pool_spent=_swarm_daily_top())  # 本日已承诺 = 日顶
            cfg = _config(td, swarm_v2_db=str(db), dev_repo=str(repo))
            with mock.patch("automation.company_router.v2_swarm_command") as cli:
                with self.assertRaises(RuntimeError) as ctx:
                    submit_dev_v2(cfg, decision=_dev_decision(), message=DEV_MESSAGE,
                                  session_id="s", platform="cli",
                                  gray={"hit": True}, dev_repo=str(repo))
            cli.assert_not_called()                        # 零 run create = 零悬挂 run
            msg = str(ctx.exception)
            self.assertIn("零悬挂 run", msg)
            self.assertIn(str(_swarm_daily_top()), msg)              # 日顶值(单一来源派生)
            self.assertIn(f"今日已承诺 {_swarm_daily_top()}", msg)     # 已用值
            self.assertIn("escrow 130000", msg)            # 本次需值(ceil(100000×1.3))
            self.assertIn("UTC 日历日", msg)                # 重置口径
            self.assertIn(_day(), msg)

    def test_2_under_top_is_verbatim_vs_degraded_path(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            ok = _config(td, swarm_v2_db=str(_quota_db(td, pool_spent=0)),
                         dev_repo=str(repo))
            degraded = _config(td, swarm_v2_db=str(Path(td) / "missing.db"),
                               dev_repo=str(repo))

            def _args(cfg):
                with mock.patch("automation.company_router.uuid.uuid4",
                                return_value=FIXED_UUID), \
                        mock.patch("automation.company_router.v2_swarm_command",
                                   side_effect=[{"run_id": "created"},
                                                {"task_id": "t-dev"}]) as cli:
                    submit_dev_v2(cfg, decision=_dev_decision(), message=DEV_MESSAGE,
                                  session_id="s", platform="cli",
                                  gray={"hit": True}, dev_repo=str(repo))
                return [c.args[1:] for c in cli.call_args_list]

            ok_calls = _args(ok)
            # 降级路径会响亮告警(证明"响亮");用 assertLogs 吞掉并核对文案
            with self.assertLogs("automation.company_router", level="WARNING") as cm:
                deg_calls = _args(degraded)
            self.assertEqual(ok_calls, deg_calls)          # 未超顶 ⇒ 逐字一致
            self.assertTrue(any(_V2_DAILY_TOP_DEGRADED in m for m in cm.output))
            self.assertEqual(ok_calls[0][:3], ("v2", "run", "create"))
            self.assertEqual(ok_calls[1][:2], ("market", "publish"))

    def test_3_daily_top_is_swarm_single_source(self):
        import sys
        inserted = SWARM_REPO not in sys.path
        if inserted:
            sys.path.insert(0, SWARM_REPO)
        try:
            from src.swarm_v2 import budget as swarm_budget
        finally:
            if inserted and SWARM_REPO in sys.path:
                sys.path.remove(SWARM_REPO)
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            db = _quota_db(td, pool_spent=1000)
            cfg = _config(td, swarm_v2_db=str(db), dev_repo=str(repo))
            mods, why = _v2_swarm_budget_modules(cfg)
            self.assertIsNotNone(mods, why)
            self.assertIs(mods[0], swarm_budget)
            self.assertEqual(mods[0].DAILY_TOP_TOKENS, swarm_budget.DAILY_TOP_TOKENS)
            original = swarm_budget.DAILY_TOP_TOKENS
            swarm_budget.DAILY_TOP_TOKENS = 1000          # 改蜂群一处
            try:
                with mock.patch("automation.company_router.v2_swarm_command") as cli:
                    with self.assertRaises(RuntimeError) as ctx:
                        submit_dev_v2(cfg, decision=_dev_decision(),
                                      message=DEV_MESSAGE, session_id="s",
                                      platform="cli", gray={"hit": True},
                                      dev_repo=str(repo))
            finally:
                swarm_budget.DAILY_TOP_TOKENS = original
            cli.assert_not_called()
            self.assertIn("日顶 1000", str(ctx.exception))  # 公司侧随之改变

    def test_4_unavailable_reading_degrades_loudly_never_zero(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            cfg = _config(td, swarm_v2_db=str(Path(td) / "missing.db"),
                          dev_repo=str(repo))
            with mock.patch("automation.company_router.v2_swarm_command",
                            side_effect=[{"run_id": "created"},
                                         {"task_id": "t-dev"}]) as cli:
                with self.assertLogs("automation.company_router",
                                     level="WARNING") as cm:
                    submit_dev_v2(cfg, decision=_dev_decision(), message=DEV_MESSAGE,
                                  session_id="s", platform="cli",
                                  gray={"hit": True}, dev_repo=str(repo))
            self.assertEqual(cli.call_count, 2)            # 不把日顶当 0 直接拒
            self.assertTrue(any(_V2_DAILY_TOP_DEGRADED in m for m in cm.output))

    def test_5_all_four_submit_ports_precheck_before_run_create(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            cfg_template = _config(td, swarm_v2_db="", dev_repo=str(repo))

            def _run(name):
                db = _quota_db(td, pool_spent=_swarm_daily_top())
                cfg = dict(cfg_template, swarm_v2_db=str(db))
                with mock.patch("automation.company_router.v2_swarm_command") as cli:
                    with self.assertRaises(RuntimeError) as ctx:
                        if name == "content":
                            submit_content_v2(
                                cfg, decision=classify_message("写一篇公众号文章"),
                                message="写一篇公众号文章", session_id="s",
                                platform="cli", gray={"hit": True})
                        elif name == "vuln":
                            submit_security_v2(
                                cfg,
                                decision=classify_message("分析本机 APK 逆向报告中的认证逻辑"),
                                message="分析本机 APK 逆向报告中的认证逻辑",
                                session_id="s", platform="cli", gray={"hit": True})
                        elif name == "ops":
                            submit_research_v2(
                                cfg, decision=classify_message("调研一下竞品 X 的技术方案"),
                                message="调研一下竞品 X 的技术方案", session_id="s",
                                platform="cli", gray={"hit": True})
                        else:
                            submit_dev_v2(
                                cfg, decision=_dev_decision(), message=DEV_MESSAGE,
                                session_id="s", platform="cli", gray={"hit": True},
                                dev_repo=str(repo))
                    cli.assert_not_called()
                    self.assertIn("零悬挂 run", str(ctx.exception))

            for name in ("content", "vuln", "ops", "dev"):
                with self.subTest(name=name):
                    _run(name)


if __name__ == "__main__":
    unittest.main()
