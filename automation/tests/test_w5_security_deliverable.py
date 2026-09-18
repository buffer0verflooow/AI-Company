"""W5-b-1 回归:exec-criteria 安全线任务书必须写死"交付物落盘"硬要求。

用户裁定(2026-09-18):交付物必须落盘,判据按产物文件校验。声明了判据却不
在任务书里要求产出被判据校验的文件 ⇒ 判定必然判负且无从定位。本批把要求
写进任务书正文(未声明 runtime_brief 时)并另随 `deliverable_requirement` 下发
(声明式 runtime_brief 逐字保留,不改写用户正文)。

不变量:
  1. 声明判据 + 未声明 runtime_brief ⇒ 生成的 runtime_brief 含 fs.write 落盘
     硬要求与"判据 ↔ 产物"示例对应(逐条列出被判据引用的产物文件)。
  2. 声明判据 + 已声明 runtime_brief ⇒ 用户正文**逐字保留**,硬要求仍随
     `deliverable_requirement` 下发(不丢)。
  3. 未声明判据(binding-record)⇒ 现状逐字不变:无 `deliverable_requirement`、
     无编造 argv/expect_exit。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import (
    build_security_exec_deliverable_requirement,
    classify_message,
    security_criteria_artifacts,
    submit_security_v2,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
SECURITY_MESSAGE = "分析本机 APK 逆向报告中的认证逻辑"

CRITERIA = [
    {"argv": ["grep", "-F", "-q", "package=com.example.app", "report.md"],
     "expect_exit": 0, "timeout": 30},
    {"argv": ["sha256sum", "report.md"], "expect_exit": 0, "timeout": 30},
]


def _config(td, **extra):
    config = {
        "enabled": True,
        "dispatch_security": False,
        "dispatch_research": False,
        "auto_run_article": True,
        "state_db": str(Path(td) / "router.db"),
        "swarm_repo": SWARM_REPO,
        "swarm_v2_db": str(Path(td) / "swarm_v2.db"),
        "swarm_v2_agent": "content-writer-1",
        "swarm_v2_judge": "content-judge-1",
        "swarm_v2_security_agent": "vuln-executor-1",
        "swarm_v2_security_judge": "vuln-judge-1",
        "swarm_v2_gray": {
            "enabled": True, "run_types": ["vuln"], "task_types": [],
            "ratio_pct": 100, "client_source": "company-router",
        },
        "log_dir": str(Path(td) / "logs"),
        "content_job_dir": str(Path(td) / "content-jobs"),
    }
    config.update(extra)
    return config


def _submit(config, message=SECURITY_MESSAGE, **kwargs):
    decision = classify_message(message)
    gray = {"hit": True, "run_type": "vuln", "task_type": "analyze"}
    with patch("automation.company_router.v2_swarm_command",
               side_effect=[{"run_id": "x"}, {"task_id": "t-1"}]) as v2cli:
        out = submit_security_v2(
            config, decision=decision, message=message, session_id="sess-1",
            platform="cli", gray=gray, **kwargs)
    return out, v2cli


def _focus_of(v2cli):
    publish = v2cli.call_args_list[1].args
    return json.loads(publish[publish.index("--focus") + 1])


class DeliverableRequirementTests(unittest.TestCase):
    def test_rule_text_requires_fs_write_landing(self):
        text = build_security_exec_deliverable_requirement(CRITERIA)
        self.assertIn("fs.write", text)
        self.assertIn("落盘", text)
        self.assertIn("判负", text)
        # 判据 ↔ 产物示例对应
        self.assertIn("grep", text)
        self.assertIn("<产物文件>", text)

    def test_artifact_extraction_skips_patterns_and_options(self):
        # "package=com.example.app" 含 '=' 视为模式值,不得当产物;report.md 保留
        self.assertEqual(security_criteria_artifacts(CRITERIA), ["report.md"])

    def test_generated_brief_when_none_declared(self):
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(_config(td), task_book={"exec_criteria": CRITERIA})
            focus = _focus_of(v2cli)
            self.assertEqual(focus["exec_criteria"], CRITERIA)
            brief = focus["runtime_brief"]
            self.assertIn("fs.write", brief)
            self.assertIn("report.md", brief)
            self.assertIn("判负", brief)
            # 另随字段下发同一硬要求(任务书正文/字段双保险)
            self.assertIn("fs.write", focus["deliverable_requirement"])
            self.assertIn("report.md", focus["deliverable_requirement"])

    def test_declared_brief_preserved_verbatim_requirement_still_shipped(self):
        with tempfile.TemporaryDirectory() as td:
            declared = "离线分析 report.md"
            out, v2cli = _submit(
                _config(td),
                task_book={"exec_criteria": CRITERIA, "runtime_brief": declared})
            focus = _focus_of(v2cli)
            self.assertEqual(focus["runtime_brief"], declared,
                             "用户声明的 runtime_brief 必须逐字保留")
            self.assertIn("fs.write", focus["deliverable_requirement"])
            self.assertIn("report.md", focus["deliverable_requirement"])

    def test_binding_record_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(_config(td))
            focus = _focus_of(v2cli)
            self.assertEqual(focus["vuln_verify"]["mode"], "binding-record")
            self.assertNotIn("deliverable_requirement", focus)
            self.assertNotIn("exec_criteria", focus)
            blob = json.dumps(focus, ensure_ascii=False)
            self.assertNotIn('"argv"', blob)
            self.assertNotIn('"expect_exit"', blob)


if __name__ == "__main__":
    unittest.main()
