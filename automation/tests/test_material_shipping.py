"""素材随任务落盘(2026-09-22;路径 A 成本修复)。

实测根因:把 15 KB 原文**内联进任务书** ⇒ 每轮重发 + 每次截断重试再发一整遍 ⇒
**≈72k tok/轮**(首单 5 轮 358,518 tok 撞 280k 预算被判负);而素材作**文件**
(模型 `fs.read` 读一次)的同题材单,09-21 已 accepted 且**全程 48.5k tok**。

本批:素材随任务落盘到 `<job_dir>/_material/`,任务书只给路径;越界/缺失/超限 ⇒
发布前**响亮拒绝**(不制造"没素材却照写"的任务)。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import company_router as cr
from automation.company_router import (
    _hook_materials,
    build_runtime_brief,
    classify_message,
    content_job_path,
    ship_task_materials,
    submit_content_v2,
    v2_gray_decision,
)

COMPANY = Path(__file__).resolve().parent.parent.parent


def _gray():
    return {"enabled": True, "run_types": ["content"], "task_types": [],
            "ratio_pct": 100, "client_source": "company-router"}


def _config(td: str) -> dict:
    return {
        "enabled": True, "dispatch_security": False, "auto_run_security": False,
        "auto_run_article": True, "auto_run_video": True, "auto_run_company": True,
        "state_db": str(Path(td) / "router.db"),
        "swarm_repo": "/home/pwn/workspace/research/swarm-knowledge",
        "swarm_db": str(Path(td) / "swarm.db"),
        "swarm_v2_db": str(Path(td) / "swarm_v2.db"),
        "swarm_v2_agent": "content-worker", "swarm_v2_judge": "content-judge",
        "swarm_v2_gray": _gray(),
        "log_dir": str(Path(td) / "logs"),
        "content_executor": str(Path(td) / "content_executor.py"),
        "content_job_dir": str(Path(td) / "content-jobs"),
        "gateway_sessions_index": str(Path(td) / "sessions.json"),
        "max_active_runs_per_session": 2,
        "max_active_content_jobs_per_session": 2,
    }


class ShipTests(unittest.TestCase):
    def test_ships_into_material_dir_and_reports_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            job = Path(td) / "job"
            job.mkdir()
            src = COMPANY / "marketing" / "content-quality-gates.md"
            out = ship_task_materials(job, [str(src)])
            rel, size = out[0]
            self.assertEqual(rel, f"{cr._V2_MATERIAL_SUBDIR}/{src.name}")
            self.assertEqual(size, src.stat().st_size)
            self.assertEqual((job / rel).read_bytes(), src.read_bytes())

    def test_same_name_from_two_roots_gets_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a"
            b = Path(td) / "b"
            a.mkdir(); b.mkdir()
            (a / "x.md").write_text("A", encoding="utf-8")
            (b / "x.md").write_text("B", encoding="utf-8")
            job = Path(td) / "job"; job.mkdir()
            out = ship_task_materials(job, [str(a / "x.md"), str(b / "x.md")],
                                      roots=[a, b])
            self.assertEqual([r for r, _ in out],
                             [f"{cr._V2_MATERIAL_SUBDIR}/x.md",
                              f"{cr._V2_MATERIAL_SUBDIR}/x-1.md"])
            self.assertEqual((job / out[1][0]).read_text(encoding="utf-8"), "B")

    def test_outside_root_refused(self):
        with tempfile.TemporaryDirectory() as td:
            outside = Path(td) / "secret.md"
            outside.write_text("S", encoding="utf-8")
            job = Path(td) / "job"; job.mkdir()
            with self.assertRaises(ValueError) as ei:
                ship_task_materials(job, [str(outside)], roots=[COMPANY / "marketing"])
            self.assertIn("不在允许的根内", str(ei.exception))

    def test_missing_and_oversize_refused(self):
        with tempfile.TemporaryDirectory() as td:
            job = Path(td) / "job"; job.mkdir()
            with self.assertRaises(ValueError) as ei:
                ship_task_materials(job, [str(COMPANY / "marketing" / "nope.md")])
            self.assertIn("素材不存在", str(ei.exception))
            big = Path(td) / "big.md"
            big.write_bytes(b"x" * (cr._V2_MATERIAL_MAX_BYTES + 1))
            with self.assertRaises(ValueError) as ei2:
                ship_task_materials(job, [str(big)], roots=[Path(td)])
            self.assertIn("素材过大", str(ei2.exception))


class BriefTests(unittest.TestCase):
    def test_brief_carries_hard_chunking_rule(self):
        """单次写入 ≤2000 字 / 长文 fs.append 分段 —— 2026-09-22 实测校准的定价规则。"""
        decision = classify_message("文章：写一篇技术文章")
        brief = build_runtime_brief(decision, "文章：写一篇技术文章", Path("/tmp/job-z"))
        self.assertIn("≤ 2000 字", brief)
        self.assertIn("分多次追加", brief)
        self.assertIn("禁止一次写整篇", brief)

    def test_brief_lists_path_but_never_inlines_body(self):
        decision = classify_message("文章：把它翻成中文公众号文章")
        job = Path("/tmp/job-x")
        brief = build_runtime_brief(decision, "文章：把它翻成中文公众号文章", job,
                                    materials=["_material/src.md"])
        self.assertIn("_material/src.md", brief)
        self.assertIn("已随任务落盘", brief)
        self.assertIn("不要尝试访问任何 URL", brief)
        # 素材正文**不得**被内联(这正是 72k/轮 的根因)
        self.assertNotIn("Pwning AI Agents", brief)

    def test_no_materials_means_no_new_block(self):
        decision = classify_message("文章：写一篇技术文章")
        brief = build_runtime_brief(decision, "文章：写一篇技术文章", Path("/tmp/job-y"))
        self.assertNotIn("素材（", brief)


class PublishTests(unittest.TestCase):
    def test_publish_ships_material_and_brief_points_at_it(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            msg = "文章：把它完整翻译成中文公众号文章"
            decision = classify_message(msg)
            gray = v2_gray_decision(config, decision, msg)
            # 用真素材(蜂群 `materials/`,与 m10x 单同源)——它**不是**规范,故只可能是
            # "落盘 + 指路",不可能被当规范内联 ⇒ 断言才有意义
            src = (Path(config["swarm_repo"]) / "materials"
                   / "m10x-pwning-ai-agents-1.md")
            with patch("automation.company_router.v2_swarm_command",
                       side_effect=[{"run_id": "x"}, {"task_id": "t-1"}]) as v2cli:
                out = submit_content_v2(config, decision=decision, message=msg,
                                        session_id="sess-mat", platform="cli",
                                        gray=gray, materials=[str(src)])
            job = content_job_path(config, out["run_id"])
            self.assertTrue((job / "_material" / src.name).is_file())
            focus = json.loads(
                v2cli.call_args_list[1].args[
                    v2cli.call_args_list[1].args.index("--focus") + 1])
            brief = focus["runtime_brief"]
            self.assertIn(f"_material/{src.name}", brief)
            # 素材正文**未被内联**(15 KB 内联是 72k tok/轮 的根因)
            self.assertNotIn("Pwning AI Agents (Part 1/4)", brief)
            self.assertLess(len(brief), 30_000)
            # 2026-09-22:发布方声明**产物根** ⇒ 中标者若是别人,supervisor
            # (`src/swarm_v2/award_exec.py`)补位执行时才能把产物写回本 run 的 job dir
            self.assertEqual(focus["repo_root"], str(job))

    def test_bad_material_fails_before_publish(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            msg = "文章：翻译一篇"
            decision = classify_message(msg)
            gray = v2_gray_decision(config, decision, msg)
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError):
                    submit_content_v2(config, decision=decision, message=msg,
                                      session_id="sess-bad", platform="cli",
                                      gray=gray,
                                      materials=["/etc/passwd"])
            v2cli.assert_not_called()          # 发布前失败,不制造"没素材却照写"的任务


class HookMaterialsTests(unittest.TestCase):
    def test_extracts_from_extra_and_top_level(self):
        self.assertEqual(_hook_materials({"extra": {"materials": ["a.md"]}}), ["a.md"])
        self.assertEqual(_hook_materials({"materials": "b.md"}), ["b.md"])
        self.assertEqual(_hook_materials({"extra": {"materials": ["a.md", 3, ""]}}),
                         ["a.md"])
        self.assertEqual(_hook_materials({"extra": {}}), [])
        self.assertEqual(_hook_materials({}), [])


if __name__ == "__main__":
    unittest.main()
