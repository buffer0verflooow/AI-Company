from __future__ import annotations

import unittest

from automation.swarm_hermes_executor import build_prompt


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


if __name__ == "__main__":
    unittest.main()
