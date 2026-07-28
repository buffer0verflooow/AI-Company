from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation.agentkey_radar_probe import (
    _canonical_url,
    _parse_agentkey_result,
    _run_one_query,
)


class AgentKeyProbeTests(unittest.TestCase):
    def test_canonical_url_rejects_credentials_and_private_ip_literals(self):
        self.assertEqual(_canonical_url("https://user:pass@example.com/path"), "")
        self.assertEqual(_canonical_url("http://127.0.0.1/private"), "")
        self.assertEqual(_canonical_url("http://10.0.0.1/private"), "")
        self.assertEqual(
            _canonical_url("HTTPS://Example.COM:443/a?utm_source=x&keep=1#frag"),
            "https://example.com/a?keep=1",
        )

    def test_parser_uses_canonical_hostname_not_worker_supplied_domain(self):
        parsed = _parse_agentkey_result(
            {
                "title": "Signal",
                "url": "https://Example.com/path",
                "snippet": "2026-07-20 evidence",
                "source_domain": "attacker.invalid",
            },
            "theme", "Theme", "company", "query",
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["source_domain"], "example.com")

    def test_worker_output_is_bounded_typed_cleaned_and_credentials_are_scrubbed(self):
        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td)
            output_path = output_dir / "ak-000000000000.json"
            output_path.write_text(json.dumps([
                {"title": "ok", "url": "https://example.com", "snippet": "x"},
                "not-an-object",
            ]), encoding="utf-8")
            captured = {}

            def fake_run(_cmd, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with patch.dict("os.environ", {"PATH": "/bin", "OPENAI_API_KEY": "secret"}, clear=True), \
                    patch("automation.agentkey_radar_probe.uuid.uuid4", return_value=uuid.UUID(int=0)), \
                    patch("automation.agentkey_radar_probe.subprocess.run", side_effect=fake_run):
                result = _run_one_query("Theme", "query", output_dir)

            self.assertEqual(result[0]["title"], "ok")
            self.assertIn("non-object", result[-1]["error"])
            self.assertFalse(output_path.exists())
            self.assertNotIn("OPENAI_API_KEY", captured["env"])
            self.assertEqual(captured["env"]["HERMES_WRITE_SAFE_ROOT"], str(output_dir.resolve()))


if __name__ == "__main__":
    unittest.main()
