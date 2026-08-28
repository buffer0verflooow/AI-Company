from __future__ import annotations

import json
import os
import subprocess
import unittest
from unittest.mock import patch

from automation.swarm_hermes_executor import _run_opencode, _safe_counter, build_prompt
from automation.swarm_native_executor import _run_command_backend


class SwarmExecutorPromptTests(unittest.TestCase):
    def test_build_prompt_returns_text_for_normal_task(self):
        prompt = build_prompt({
            "task": {
                "required_role": "analyst",
                "task_type": "analyze",
                "reason": "smoke-test task",
            },
            "context": "local evidence",
        })

        self.assertIsInstance(prompt, str)
        self.assertIn("smoke-test task", prompt)

    def test_build_prompt_handles_empty_task(self):
        prompt = build_prompt({"task": {}})

        self.assertIsInstance(prompt, str)
        self.assertIn("任务角色：analyst", prompt)
        self.assertIn("任务类型：analyze", prompt)

    def test_json_example_uses_literal_braces(self):
        prompt = build_prompt({"task": {"reason": "literal JSON"}})

        # The braces are part of the prompt example, not f-string
        # interpolation.  Seeing them here proves the expression is escaped.
        self.assertIn('params={"query": "...", "num": 5}', prompt)


class SwarmNativeExecutorContractTests(unittest.TestCase):
    def test_malformed_agent_command_returns_clean_json_failure(self):
        # A broken SWARM_NATIVE_AGENT_COMMAND (unbalanced quote) must surface as
        # a JSON error payload, not a raw ValueError traceback with no stdout.
        with patch.dict(os.environ, {"SWARM_NATIVE_AGENT_COMMAND": 'opencode run "unterminated'}):
            result = _run_command_backend({"task": {}}, {})
        self.assertFalse(result["success"])
        self.assertIn("No closing quotation", str(result.get("error") or ""))

    def test_missing_agent_command_returns_clean_failure(self):
        with patch.dict(os.environ, {"SWARM_NATIVE_AGENT_COMMAND": ""}, clear=False):
            result = _run_command_backend({"task": {}}, {})
        self.assertFalse(result["success"])
        self.assertIn("SWARM_NATIVE_AGENT_COMMAND", str(result.get("error") or ""))


class SwarmHermesExecutorTokenTests(unittest.TestCase):
    def test_safe_counter_defaults_and_fallbacks(self):
        self.assertEqual(_safe_counter(None), 0)
        self.assertEqual(_safe_counter(""), 0)
        self.assertEqual(_safe_counter("7"), 7)
        self.assertEqual(_safe_counter(3.9), 3)
        for bad in ("abc", [1], {"a": 1}, float("inf")):
            self.assertEqual(_safe_counter(bad), 0, bad)

    def test_corrupt_token_event_does_not_crash_opencode_parse(self):
        # The opencode JSON event stream is external input; a corrupt
        # tokens.total must degrade to 0 instead of crashing the executor.
        events = [
            {"type": "text", "part": {"type": "text", "text": "hello"}},
            {"type": "step_finish", "tokens": {"total": "not-a-number"}},
        ]
        fake = subprocess.CompletedProcess(
            args=["opencode"],
            returncode=0,
            stdout="\n".join(json.dumps(event) for event in events) + "\n",
            stderr="",
        )
        with patch("automation.swarm_hermes_executor.subprocess.run", return_value=fake):
            result = _run_opencode({"resolved_model": "free-model"}, "prompt", {})
        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["token_cost"], 0)


if __name__ == "__main__":
    unittest.main()
