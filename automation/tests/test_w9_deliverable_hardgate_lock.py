"""W9 跨仓锁:交付物落盘硬语义(用户裁定;FINDINGS-7)。

事实(live run 5):执行体多轮 `answered` 却零 `fs.write` ⇒ 交付物不存在 ⇒ 判负。
W9 把"落盘"从软提醒升级为**硬语义**:声明交付物时"完成"= 交付物已落盘;
模型想收尾而交付物缺失 ⇒ 每轮拒绝收尾(不扩预算);截断/非法 JSON 不得静默当终答。

本测试 = 公司侧跨仓锁(与 `test_w8_path_convention_lock` 同法),只读蜂群源码,
不改公司语义:
  1. **生产接线**:公司安全线任务书声明的 `exec_criteria` 经
     `exec_verify.executor_focus_view` 派生 `focus_params.deliverables` ⇒
     硬闸真的生效(缺文件时拒收尾;落盘后才收尾)。这条证明公司侧**无需改代码**
     即可获得 W9 硬语义。
  2. **文本锁**:swarm 系统/任务提示含"先落盘才允许 answer";未声明 ⇒ 不含。
  3. **工具描述锁**:`fs.write` 含"不要长篇报告";`sh.run` argv 要求前置。
  4. **可审计常量**:硬闸/解析失败工具名 + close payload 计数字段存在。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SWARM_REPO = "/home/pwn/workspace/research/swarm-knowledge"


def _load_agent_runtime():
    inserted = str(SWARM_REPO) not in sys.path
    if inserted:
        sys.path.insert(0, SWARM_REPO)
    try:
        from src.swarm_v2 import agent_runtime
    finally:
        if inserted and SWARM_REPO in sys.path:
            sys.path.remove(SWARM_REPO)
    return agent_runtime


def _load_exec_verify():
    inserted = str(SWARM_REPO) not in sys.path
    if inserted:
        sys.path.insert(0, SWARM_REPO)
    try:
        from src.swarm_v2 import exec_verify
    finally:
        if inserted and SWARM_REPO in sys.path:
            sys.path.remove(SWARM_REPO)
    return exec_verify


def _scripted(replies):
    box = {"i": 0}

    def llm(messages, **_kw):
        i = box["i"]
        box["i"] += 1
        return replies[min(i, len(replies) - 1)]

    return llm


def _answer(text="done", tokens=10):
    return (json.dumps({"answer": text}), {"total_tokens": tokens}, None)


def _write(path, content, tokens=10):
    return (json.dumps({"tool_call": {"tool": "fs.write",
                                      "args": {"path": path, "content": content}}}),
            {"total_tokens": tokens}, None)


class W9HardGateLockTests(unittest.TestCase):
    def setUp(self):
        if not (Path(SWARM_REPO) / "src" / "swarm_v2" / "agent_runtime.py").exists():
            self.skipTest("swarm 检出不在本机")

    def test_company_criteria_derive_deliverables_and_fire_hard_gate(self):
        """生产接线:公司任务书 exec_criteria ⇒ 派生 deliverables ⇒ 硬闸生效。"""
        ar = _load_agent_runtime()
        ev = _load_exec_verify()
        criteria_focus = json.dumps({
            "exec_criteria": [
                {"argv": ["grep", "-q", "package=com.example.app", "report.md"]},
                {"argv": ["sha256sum", "report.md"]},
            ],
        }, ensure_ascii=False)
        view = ev.executor_focus_view(criteria_focus)
        self.assertEqual(view["deliverables"], ["report.md"])
        task = {"task_id": "t-lock", "run_id": "r-lock", "focus_params": view}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # 一直想收尾而 report.md 未落盘 ⇒ 每轮拒绝,预算耗尽判负。
            res = ar.run_agent_task(task, "ctx", repo_root=root, permission="write",
                                    llm_call=_scripted([_answer("x")]), max_turns=2)
            self.assertFalse(res["ok"])
            self.assertEqual(res["stop_reason"], "max_turns_exceeded")
            self.assertFalse((root / "report.md").exists())
            self.assertEqual(
                sum(1 for r in res["trace"]
                    if r[4] == ar.DELIVERABLE_GATE_TOOL), 2)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # 落盘后可正常收尾。
            res = ar.run_agent_task(
                task, "ctx", repo_root=root, permission="write",
                llm_call=_scripted([_answer("x"),
                                    _write("report.md", "package=com.example.app\n"),
                                    _answer("done")]), max_turns=8)
            self.assertTrue(res["ok"])
            self.assertEqual(res["stop_reason"], "answered")
            self.assertTrue((root / "report.md").is_file())

    def test_swarm_prompts_state_landing_first_only_when_declared(self):
        ar = _load_agent_runtime()
        note = ar.deliverable_completion_note(["report.md"])
        self.assertIn("先落盘才允许 answer", note)
        self.assertIn("未落盘视为未完成", note)
        sys_declared = ar._SYSTEM_PROMPT.format(
            perm_label="写档", write_rule=ar._WRITE_RULE, exec_rule="", dev_rule="",
            completion_rule="\n   " + note)
        self.assertIn("先落盘才允许 answer", sys_declared)
        task_declared = ar._task_prompt(
            {"task_id": "t", "focus_params": {"deliverables": ["report.md"]}}, "ctx")
        self.assertIn("先落盘才允许 answer", task_declared)
        # 未声明 ⇒ 提示里不得出现该硬闸字样(逐字不变由 swarm 侧锁测试覆盖)
        sys_plain = ar._SYSTEM_PROMPT.format(
            perm_label="写档", write_rule=ar._WRITE_RULE, exec_rule="", dev_rule="",
            completion_rule="")
        self.assertNotIn("先落盘才允许 answer", sys_plain)
        task_plain = ar._task_prompt({"task_id": "t", "focus_params": {}}, "ctx")
        self.assertNotIn("先落盘才允许 answer", task_plain)
        self.assertTrue(task_plain.endswith("请开始:必要时调用工具核实,最终给出 answer。"))

    def test_swarm_tool_descriptions_brevity_and_argv_first(self):
        ar = _load_agent_runtime()
        write_summary = ar.TOOL_REGISTRY["fs.write"].summary
        sh_summary = ar.TOOL_REGISTRY["sh.run"].summary
        self.assertIn(ar.FS_WRITE_BREVITY_HINT, write_summary)
        self.assertIn("不要长篇报告", write_summary)
        self.assertIn(ar.SH_RUN_ARGV_HINT, sh_summary)
        self.assertLess(sh_summary.index("argv"), sh_summary.index("白名单"))

    def test_swarm_auditable_gate_and_parse_constants(self):
        ar = _load_agent_runtime()
        self.assertEqual(ar.REMEDIATION_TOOL, ar.DELIVERABLE_GATE_TOOL)
        self.assertTrue(ar.DELIVERABLE_GATE_TOOL)
        self.assertTrue(ar.PARSE_ERROR_TOOL)
        self.assertIn("输出被截断或非法 JSON", ar.PARSE_ERROR_MESSAGE)
        # close payload 计数字段名(硬闸次数 / 解析失败次数)是跨仓契约
        src = (Path(SWARM_REPO) / "src" / "swarm_v2" / "agent_runtime.py").read_text(
            encoding="utf-8")
        self.assertIn('"deliverable_gate_rejections"', src)
        self.assertIn('"parse_errors"', src)


if __name__ == "__main__":
    unittest.main()
