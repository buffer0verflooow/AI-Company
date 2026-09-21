"""CV2 回归:文字侧质量门接进 content 线**生产面**(2026-09-21)。

现状(实测):
  - hermes 隔离执行器(`content_hermes_executor.build_prompt`)提示词里有完整的
    "去 AI 味(34 条)/ QA Gate 1-3 / 微信预览 Gate 4 / 排版 CSS"要求;
  - 蜂群 v2 内容线(`company_router.build_runtime_brief`)只内联两份规范的**节选**,
    且**截断在 4000 字**(源文件实测 9,997 B / 8,557 B ⇒ 过半规范从未送达),
    去 AI 味/QA Gate/微信预览的要求**一个字都没有**;产物清单也从不声明
    `draft-formatted.md`/`wechat-preview.html`。

本批(生产面;判定面沿用 CV1/CV6 的声明清单闸):
  ① 新增共享规范 `marketing/content-quality-gates.md`(正文由执行器提示词逐字整理);
  ② 规范**全文 + CSS 模板随任务落盘**到 `<产物目录>/_spec/`(运行时读得到);
  ③ 任务书补 `_spec/` 路径 + "冲突以全文为准" + `fs.append` 长文分段写;
  ④ 产物清单按任务关键词追加排版/预览两件(与执行器步骤 5/6 同源),单一来源。

改前对照:`ship_content_specs` / `content_deliverables` 不存在,`_spec/` 从不落盘,
产物清单恒 3 件 ⇒ ①②④ 全红。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import (
    _V2_CONTENT_SPECS,
    _V2_SPEC_ASSETS,
    classify_message,
    content_deliverables,
    content_quality_markers,
    content_job_path,
    ship_content_specs,
    submit_content_v2,
    v2_gray_decision,
)

COMPANY = Path(__file__).resolve().parent.parent.parent
ARTICLE_MESSAGE = "写一篇 Agent 工程公众号文章"
PLAIN_ARTICLE = "写一篇关于 Agent 工程的技术文章"


def _gray():
    return {"enabled": True, "run_types": ["content"], "task_types": [],
            "ratio_pct": 100, "client_source": "company-router"}


def _config(td):
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


class SpecShippingTests(unittest.TestCase):
    def test_specs_ship_full_text_not_truncated(self):
        """规范全文 + CSS 模板落盘;字节数与源文件**逐字相同**(不是 4000 字节节选)。"""
        with tempfile.TemporaryDirectory() as td:
            job_dir = Path(td) / "job"
            job_dir.mkdir()
            shipped = dict(ship_content_specs(job_dir, "article"))
            self.assertTrue(shipped, "article 线必须落盘规范")
            expected = list(_V2_CONTENT_SPECS["article"]) + list(_V2_SPEC_ASSETS)
            self.assertEqual(sorted(shipped), sorted(
                f"_spec/{Path(r).name}" for r in expected))
            for rel, size in shipped.items():
                src = next(COMPANY / r for r in expected if Path(r).name == Path(rel).name)
                self.assertEqual(size, src.stat().st_size, rel)
                self.assertEqual((job_dir / rel).read_bytes(), src.read_bytes(), rel)
            # 关键:落盘的是**全量** —— 至少一份规范超过 4000 字内联上限
            # (实测 article-quality-constraints.md 9,997 B / article-production.md 8,557 B)
            self.assertTrue(any(sz > 4000 for sz in shipped.values()),
                            f"落盘必须绕过 4000 字内联截断;实得 {shipped}")
            gates = job_dir / "_spec" / "content-quality-gates.md"
            self.assertIn("去 AI 味", gates.read_text(encoding="utf-8"))
            self.assertIn("Gate 4", gates.read_text(encoding="utf-8"))
            self.assertTrue((job_dir / "_spec" / "wechat-article.css").is_file())

    def test_missing_source_is_skipped_not_faked(self):
        """源文件读不到 ⇒ 跳过(不假装已下发)。"""
        with tempfile.TemporaryDirectory() as td:
            job_dir = Path(td) / "job"
            job_dir.mkdir()
            self.assertEqual(ship_content_specs(job_dir, "no-such-route"), [])


class DeliverablesTests(unittest.TestCase):
    def test_article_declares_three_by_default(self):
        self.assertEqual(content_deliverables("article", PLAIN_ARTICLE),
                         ["draft.md", "draft-humanized.md", "qa-report.md"])

    def test_wechat_task_declares_format_and_preview(self):
        """「公众号/排版/微信」⇒ 追加 draft-formatted.md + wechat-preview.html。"""
        for msg in ("写一篇公众号文章", "这篇要排版好", "微信发布用"):
            self.assertEqual(content_deliverables("article", msg),
                             ["draft.md", "draft-humanized.md", "qa-report.md",
                              "draft-formatted.md", "wechat-preview.html"], msg)

    def test_format_keywords_do_not_leak_to_other_routes(self):
        """排版/预览是 article 线专属 ⇒ 其它子线清单不因关键词变形。"""
        self.assertEqual(content_deliverables("company", "微信相关的公司报告"),
                         ["task-report.md", "result.json"])


class QualityMarkerTests(unittest.TestCase):
    """CV2 判定面:发布方声明"质量门标记",蜂群只核验声明过的(单向下发)。"""

    def test_article_declares_three_gates(self):
        self.assertEqual(content_quality_markers("article", PLAIN_ARTICLE),
                         {"qa-report.md": ["Gate 1", "Gate 2", "Gate 3"]})

    def test_wechat_task_also_declares_gate4(self):
        self.assertEqual(content_quality_markers("article", ARTICLE_MESSAGE),
                         {"qa-report.md": ["Gate 1", "Gate 2", "Gate 3", "Gate 4"]})

    def test_other_routes_declare_nothing(self):
        """未声明 ⇒ 蜂群判定面逐字旧行为(不核验任何标记)。"""
        self.assertEqual(content_quality_markers("company", "微信相关的公司报告"), {})
        self.assertEqual(content_quality_markers("video", ARTICLE_MESSAGE), {})


class PublishWiringTests(unittest.TestCase):
    def _submit(self, config, message=ARTICLE_MESSAGE):
        decision = classify_message(message)
        gray = v2_gray_decision(config, decision, message)
        with patch("automation.company_router.v2_swarm_command",
                   side_effect=[{"run_id": "x"}, {"task_id": "t-1"}]) as v2cli:
            out = submit_content_v2(config, decision=decision, message=message,
                                    session_id="sess-cv2", platform="cli", gray=gray)
        return out, v2cli

    def test_publish_ships_specs_into_job_dir_and_declares_them(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            out, v2cli = self._submit(config)
            run_id = out["run_id"]
            job_dir = content_job_path(config, run_id)
            self.assertTrue((job_dir / "_spec" / "content-quality-gates.md").is_file(),
                            "发布即落盘规范(早于 worker 拉起)")
            publish = v2cli.call_args_list[1].args
            focus = json.loads(publish[publish.index("--focus") + 1])
            brief = focus["runtime_brief"]
            self.assertIn("content-quality-gates.md", brief)
            self.assertIn("以全文为准", brief)
            self.assertIn("fs.append", brief)
            self.assertIn("Gate 1", brief)      # CV2:标记要求必须让执行体看见
            self.assertEqual(focus["content_verify"]["markers"],
                             {"qa-report.md": ["Gate 1", "Gate 2", "Gate 3", "Gate 4"]})
            self.assertEqual(focus["content_verify"]["files"],
                             ["draft.md", "draft-humanized.md", "qa-report.md",
                              "draft-formatted.md", "wechat-preview.html"])

    def test_plain_article_publish_keeps_three_deliverables(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            _out, v2cli = self._submit(config, PLAIN_ARTICLE)
            publish = v2cli.call_args_list[1].args
            focus = json.loads(publish[publish.index("--focus") + 1])
            self.assertEqual(focus["content_verify"]["files"],
                             ["draft.md", "draft-humanized.md", "qa-report.md"])
            self.assertEqual(focus["content_verify"]["markers"],
                             {"qa-report.md": ["Gate 1", "Gate 2", "Gate 3"]})


if __name__ == "__main__":
    unittest.main()
