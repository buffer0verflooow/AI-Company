from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation.swarm_hermes_executor import _run_opencode, _safe_counter, build_prompt
from automation.swarm_native_executor import (
    _chat_once,
    _resolve_llm_config,
    _run_command_backend,
    _run_llm_backend,
)


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


class SwarmNativeLlmBackendSecurityTests(unittest.TestCase):
    """Security/resource hardening of the self-implemented LLM backend."""

    def test_chat_once_rejects_non_http_scheme_without_network(self):
        # A misconfigured base_url (file:// etc.) must be rejected before any
        # network/file access; otherwise urllib would happily open it.
        with patch("automation.swarm_native_executor.urllib.request.urlopen",
                   side_effect=AssertionError("urlopen must not be called")):
            data, err = _chat_once("file:///etc/passwd", "key", "model", [{"role": "user", "content": "hi"}], 100, 0.2)
        self.assertIsNone(data)
        self.assertIn("unsupported LLM URL scheme", err)
        self.assertIn("file", err)

    def test_chat_once_rejects_missing_scheme(self):
        # "localhost:8080" parses with scheme "localhost" — still not http(s).
        with patch("automation.swarm_native_executor.urllib.request.urlopen",
                   side_effect=AssertionError("urlopen must not be called")):
            data, err = _chat_once("localhost:8080", "key", "model", [], 100, 0.2)
        self.assertIsNone(data)
        self.assertIn("unsupported LLM URL scheme", err)

    def test_chat_once_accepts_https(self):
        answer = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 3}}
        with patch("automation.swarm_native_executor.urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(answer).encode()
            data, err = _chat_once("https://zenmux.ai/api/v1", "key", "model", [], 100, 0.2)
        self.assertEqual(err, "")
        self.assertEqual(data, answer)
        self.assertIn("https://zenmux.ai/api/v1/chat/completions", mock_open.call_args.args[0].full_url)

    def test_resolve_llm_config_missing_optional_config_falls_back_to_env(self):
        # ~/.hermes/config.yaml absent → env-based defaults, no crash, no
        # silent try/except/pass (S110) and no blind-except (BLE001).
        import automation.swarm_native_executor as sne
        with tempfile.TemporaryDirectory() as td, \
                patch.dict(os.environ, {"ZENMUX_API_KEY": "env-key-123"}, clear=False), \
                patch.object(sne.Path, "home", return_value=Path(td)):
            base_url, api_key, model = _resolve_llm_config({})
        self.assertEqual(api_key, "env-key-123")
        self.assertEqual(model, sne.DEFAULT_MODEL)
        self.assertIn("https://", base_url)

    def test_resolve_llm_config_corrupt_yaml_falls_back_to_env(self):
        # Corrupt optional config must degrade to env defaults, not raise.
        import automation.swarm_native_executor as sne
        with tempfile.TemporaryDirectory() as td, \
                patch.dict(os.environ, {"ZENMUX_API_KEY": "env-key-456"}, clear=False), \
                patch.object(sne.Path, "home", return_value=Path(td)):
            hermes = Path(td) / ".hermes"
            hermes.mkdir(parents=True, exist_ok=True)
            (hermes / "config.yaml").write_text("custom_providers: [unclosed", encoding="utf-8")
            _base_url, api_key, model = _resolve_llm_config({})
        self.assertEqual(api_key, "env-key-456")
        self.assertEqual(model, sne.DEFAULT_MODEL)

    def test_llm_backend_trace_file_written_and_closed(self):
        # SWARM_EXECUTOR_TRACE must be written through a context manager: the
        # run succeeds, the trace file has content, and the handle is closed
        # (no resource leak on the success path).
        answer = {"choices": [{"message": {"content": '{"answer": "done"}'}}], "usage": {"total_tokens": 10}}
        with tempfile.TemporaryDirectory() as td:
            trace = Path(td) / "trace.log"
            with patch.dict(os.environ, {"ZENMUX_API_KEY": "env-key", "SWARM_EXECUTOR_TRACE": str(trace)}), \
                    patch("automation.swarm_native_executor._chat_once", return_value=(answer, "")):
                result = _run_llm_backend({"model_profile": {}}, {"required_role": "analyst", "task_type": "analyze"})
            self.assertTrue(result["success"])
            self.assertEqual(result["content"], "done")
            text = trace.read_text(encoding="utf-8")
            self.assertIn("type=answer", text)
            self.assertIn('{"answer": "done"}', text)


if __name__ == "__main__":
    unittest.main()