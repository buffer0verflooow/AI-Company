"""W3-b 回归:安全线任务书声明式判据(`vuln_verify.mode=exec-criteria`)。

不变量:
  1. **缺判据 ⇒ 逐字现状**:任务书未声明 `exec_criteria` ⇒ `vuln_verify.mode=
     "binding-record"`、focus 内无 `exec_criteria`/`argv`/`expect_exit`。
  2. **声明 ⇒ 原样透传**:`task_book.exec_criteria`(或正文 fenced JSON)合法
     ⇒ `mode="exec-criteria"` 且 `focus["exec_criteria"]` 与声明逐字一致
     (**不编造** `argv`/`expect_exit`;红旗)。
  3. **坏判据 ⇒ 发布前拒**:白名单外命令/绝对路径/`..`/形状错 ⇒ ValueError,
     零 CLI 调用(不静默降级成 binding-record,不发布假判据)。
  4. **跨仓白名单单一来源**:本层白名单 = swarm 侧 `exec_verify.WHITELIST`
     (跨仓对拍;swarm 检出不在时跳过)。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.company_router import (
    _V2_EXEC_WHITELIST,
    classify_message,
    security_declared_task_book,
    security_exec_criteria,
    submit_security_v2,
    validate_security_exec_criteria,
)

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"
SECURITY_MESSAGE = "分析本机 APK 逆向报告中的认证逻辑"


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


class NoCriteriaTests(unittest.TestCase):
    def test_missing_criteria_keeps_binding_record_verbatim(self):
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(_config(td))
            focus = _focus_of(v2cli)
            self.assertEqual(focus["vuln_verify"]["mode"], "binding-record")
            self.assertEqual(focus["vuln_verify"]["provider"], "p5-exec-verify")
            blob = json.dumps(focus, ensure_ascii=False)
            self.assertNotIn("exec_criteria", focus)
            self.assertNotIn('"argv"', blob)
            self.assertNotIn('"expect_exit"', blob)

    def test_natural_language_word_is_not_a_declaration(self):
        # 正文里出现裸 exec_criteria 字样 ≠ 声明(必须走 fenced JSON)
        msg = "请分析 APK;把 exec_criteria 写进报告里(这里只是描述,不是声明)"
        self.assertEqual(security_declared_task_book(msg), {})
        self.assertIsNone(security_exec_criteria(msg))


class DeclaredCriteriaTests(unittest.TestCase):
    CRITERIA = [
        {"argv": ["grep", "-q", "com.example.app", "report.md"],
         "expect_exit": 0, "timeout": 30},
        {"argv": ["sha256sum", "report.md"], "expect_exit": 0, "timeout": 30},
    ]

    def test_explicit_task_book_criteria_shipped_verbatim(self):
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(_config(td), task_book={"exec_criteria": self.CRITERIA})
            focus = _focus_of(v2cli)
            self.assertEqual(focus["vuln_verify"]["mode"], "exec-criteria")
            self.assertEqual(focus["exec_criteria"], self.CRITERIA)
            # 不编造:声明里没有的键/值不得出现
            blob = json.dumps(focus["exec_criteria"], ensure_ascii=False, sort_keys=True)
            self.assertNotIn("expect_exit\": 1", blob)

    def test_message_fenced_json_declares_criteria(self):
        block = ("```json\n"
                 + json.dumps({"exec_criteria": self.CRITERIA,
                               "runtime_brief": "离线分析 report.md"}, ensure_ascii=False)
                 + "\n```")
        message = f"{SECURITY_MESSAGE}\n\n{block}\n"
        with tempfile.TemporaryDirectory() as td:
            out, v2cli = _submit(_config(td), message=message)
            focus = _focus_of(v2cli)
            self.assertEqual(focus["vuln_verify"]["mode"], "exec-criteria")
            self.assertEqual(focus["exec_criteria"], self.CRITERIA)
            self.assertEqual(focus["runtime_brief"], "离线分析 report.md")

    def test_timeout_is_capped_at_hard_limit(self):
        crit = validate_security_exec_criteria(
            [{"argv": ["sleep", "1"], "timeout": 10_000}])
        self.assertEqual(crit[0]["timeout"], 60)

    def test_empty_declaration_is_rejected_not_silently_downgraded(self):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            decision = classify_message(SECURITY_MESSAGE)
            gray = {"hit": True, "run_type": "vuln", "task_type": "analyze"}
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError):
                    submit_security_v2(
                        config, decision=decision, message=SECURITY_MESSAGE,
                        session_id="s", platform="cli", gray=gray,
                        task_book={"exec_criteria": []})
                v2cli.assert_not_called()


class BadCriteriaTests(unittest.TestCase):
    def _assert_rejected(self, raw):
        with tempfile.TemporaryDirectory() as td:
            config = _config(td)
            decision = classify_message(SECURITY_MESSAGE)
            gray = {"hit": True, "run_type": "vuln", "task_type": "analyze"}
            with patch("automation.company_router.v2_swarm_command") as v2cli:
                with self.assertRaises(ValueError, msg=raw):
                    submit_security_v2(
                        config, decision=decision, message=SECURITY_MESSAGE,
                        session_id="s", platform="cli", gray=gray,
                        task_book={"exec_criteria": raw})
                v2cli.assert_not_called()

    def test_network_command_rejected(self):
        self._assert_rejected([{"argv": ["curl", "http://x"]}])

    def test_shell_rejected(self):
        self._assert_rejected([{"argv": ["sh", "-c", "rm x"]}])

    def test_absolute_path_rejected(self):
        self._assert_rejected([{"argv": ["cat", "/etc/passwd"]}])

    def test_dotdot_rejected(self):
        self._assert_rejected([{"argv": ["grep", "-r", "x", "../etc"]}])

    def test_bad_shapes_rejected(self):
        for raw in ("grep -q x", [{"argv": "cat f"}], [{"argv": []}],
                    [{"argv": ["cat"], "evil": 1}],
                    [{"argv": ["cat"], "expect_exit": 999}]):
            self._assert_rejected(raw)


class WhitelistParityTests(unittest.TestCase):
    def test_whitelist_matches_swarm_single_source(self):
        repo = Path(SWARM_REPO)
        if not (repo / "src" / "swarm_v2" / "exec_verify.py").exists():
            self.skipTest("swarm 检出不在本机")
        inserted = str(repo) not in sys.path
        if inserted:
            sys.path.insert(0, str(repo))
        try:
            from src.swarm_v2 import exec_verify
        finally:
            if inserted and str(repo) in sys.path:
                sys.path.remove(str(repo))
        self.assertEqual(set(_V2_EXEC_WHITELIST), set(exec_verify.WHITELIST))


if __name__ == "__main__":
    unittest.main()
