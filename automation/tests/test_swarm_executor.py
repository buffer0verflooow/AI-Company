from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from automation.swarm_hermes_executor import build_prompt
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


if __name__ == "__main__":
    unittest.main()
