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
    _run_mcp_tool,
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

    def test_prompt_embeds_evidence_discipline(self):
        # 证据纪律必须是执行器默认行为（即使 worker 不加载任何 skill 也生效）：
        # 外部 writeup 非证据、无法本地复现须标注、冲突以本地为准、拒绝先查环境。
        prompt = build_prompt({"task": {"reason": "x"}, "context": ""})
        self.assertIn("永远不是证据", prompt)
        self.assertIn("外部来源、未本地验证", prompt)
        self.assertIn("以本地实测为准", prompt)
        self.assertIn("先核查环境状态", prompt)
        self.assertIn("平台答案不符", prompt)

    def test_build_prompt_redacts_model_profile_secrets(self):
        # model_profile may carry an api_key; it must never be echoed into the
        # prompt (sent to the provider and passed as worker argv).
        prompt = build_prompt({
            "task": {"reason": "x"},
            "model_profile": {
                "provider": "zenmux", "model": "m", "api_key": "sk-secret-value",
                "nested": {"token": "t0ken"},
            },
        })
        self.assertIn("zenmux", prompt)
        self.assertNotIn("sk-secret-value", prompt)
        self.assertNotIn("t0ken", prompt)


class SwarmNativeExecutorContractTests(unittest.TestCase):
    def test_non_object_payload_returns_clean_json_failure(self):
        # A parseable non-object payload (e.g. a JSON array) must produce the
        # documented clean-JSON failure, not a raw AttributeError traceback.
        import contextlib
        import io

        import automation.swarm_native_executor as sne
        with patch("sys.stdin", io.StringIO("[1, 2]")):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sne.main()
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["success"])
        self.assertIn("must be an object", payload["error"])

    def test_oversized_stdin_returns_clean_json_failure(self):
        # The external stdin payload must be size-bounded, mirroring
        # content_hermes_executor / company_router.
        import contextlib
        import io

        import automation.swarm_native_executor as sne
        with patch("sys.stdin", io.StringIO("x" * (sne.MAX_STDIN_BYTES + 1))):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = sne.main()
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["success"])
        self.assertIn("exceeds", payload["error"])

    def test_hermes_oversized_stdin_returns_clean_json_failure(self):
        import contextlib
        import io

        import automation.swarm_hermes_executor as she
        with patch("sys.stdin", io.StringIO("x" * (she.MAX_STDIN_BYTES + 1))):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = she.main()
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["success"])
        self.assertIn("exceeds", payload["error"])

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

    def test_non_dict_event_fields_do_not_crash_opencode_parse(self):
        # ``part``/``tokens`` can be any JSON type from the external opencode
        # process; a truthy non-dict must degrade instead of reaching ``.get``.
        events = [
            {"type": "text", "part": ["not", "a", "dict"]},
            {"type": "step_finish", "tokens": "not-a-dict"},
            {"type": "text", "part": {"type": "text", "text": "ok"}},
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
        self.assertEqual(result["content"], "ok")
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

    def test_resolve_llm_config_non_utf8_config_falls_back_to_env(self):
        # A binary/non-UTF-8 config must degrade like a corrupt YAML file
        # instead of raising UnicodeDecodeError out of the executor contract.
        import automation.swarm_native_executor as sne
        with tempfile.TemporaryDirectory() as td, \
                patch.dict(os.environ, {"ZENMUX_API_KEY": "env-key-789"}, clear=False), \
                patch.object(sne.Path, "home", return_value=Path(td)):
            hermes = Path(td) / ".hermes"
            hermes.mkdir(parents=True, exist_ok=True)
            (hermes / "config.yaml").write_bytes(b"custom_providers: [\xff\xfe")
            _base_url, api_key, model = _resolve_llm_config({})
        self.assertEqual(api_key, "env-key-789")
        self.assertEqual(model, sne.DEFAULT_MODEL)

    def test_resolve_llm_config_non_dict_custom_providers_falls_back_to_env(self):
        # A malformed custom_providers value (list of scalars, mapping, scalar)
        # in the external config must not crash with AttributeError.
        import automation.swarm_native_executor as sne
        for raw in (
            "custom_providers: [zenmux]\n",
            "custom_providers: {zenmux: {api_key: k}}\n",
            "custom_providers: 5\n",
        ):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as td, \
                    patch.dict(os.environ, {"ZENMUX_API_KEY": "env-key-cp"}, clear=False), \
                    patch.object(sne.Path, "home", return_value=Path(td)):
                hermes = Path(td) / ".hermes"
                hermes.mkdir(parents=True, exist_ok=True)
                (hermes / "config.yaml").write_text(raw, encoding="utf-8")
                _base_url, api_key, model = _resolve_llm_config({})
            self.assertEqual(api_key, "env-key-cp")
            self.assertEqual(model, sne.DEFAULT_MODEL)

    def test_llm_backend_non_dict_usage_does_not_crash(self):
        # An OpenAI-compatible endpoint may return a non-object ``usage``;
        # reading total_tokens must not raise out of the tool loop.
        answer = {"choices": [{"message": {"content": '{"answer": "done"}'}}], "usage": ["x"]}
        with patch.dict(os.environ, {"ZENMUX_API_KEY": "env-key"}), \
                patch("automation.swarm_native_executor._chat_once", return_value=(answer, "")):
            result = _run_llm_backend(
                {"model_profile": {}},
                {"required_role": "analyst", "task_type": "analyze"},
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["content"], "done")

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


class SwarmNativeMcpToolTests(unittest.TestCase):
    def test_non_object_tool_stdout_is_reported_not_raised(self):
        # An MCP tool printing a JSON array/scalar must surface as bounded
        # in-band output, not an AttributeError counted as a tool failure.
        fake = subprocess.CompletedProcess(
            args=["mcp_tool"], returncode=0, stdout="[1, 2]", stderr="",
        )
        with patch("automation.swarm_native_executor.subprocess.run", return_value=fake):
            result = _run_mcp_tool("apk", "jadx", {})
        self.assertIn("非 JSON 对象", result)
        self.assertIn("[1, 2]", result)


if __name__ == "__main__":
    unittest.main()