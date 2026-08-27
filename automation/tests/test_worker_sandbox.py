from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from automation.company_operator import audit_sandbox_writes, scrub_worker_env


def _mtime(path: Path, when: float) -> None:
    os.utime(path, (when, when))


class WorkerEnvScrubTests(unittest.TestCase):
    def test_drops_external_service_credentials(self):
        base = {  # nosec B105 -- fake fixture values, never real credentials
            "PATH": "/usr/bin", "HOME": "/home/x", "LANG": "en_US.UTF-8", "TERM": "xterm",
            "OPENAI_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "sk-y", "WEIXIN_APP_SECRET": "s",
            "WEIXIN_APP_ID": "wx1", "DASHSCOPE_API_KEY": "sk-z", "MY_DB_PASSWORD": "p",
            "GITHUB_TOKEN": "gh", "SOME_SECRET": "v", "AUTH_TOKEN": "t", "AWS_ACCESS_KEY_ID": "a",
        }
        env, dropped = scrub_worker_env(base)
        for keep in ("PATH", "HOME", "LANG", "TERM"):
            self.assertIn(keep, env)
        for gone in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "WEIXIN_APP_SECRET", "WEIXIN_APP_ID",
                     "DASHSCOPE_API_KEY", "MY_DB_PASSWORD", "GITHUB_TOKEN", "SOME_SECRET",
                     "AUTH_TOKEN", "AWS_ACCESS_KEY_ID"):
            self.assertNotIn(gone, env, gone)
            self.assertIn(gone, dropped, gone)

    def test_keeps_benign_env_untouched(self):
        env, dropped = scrub_worker_env({"PATH": "/bin", "TERM": "xterm", "EDITOR": "vi"})
        self.assertEqual(dropped, [])
        self.assertEqual(set(env), {"PATH", "TERM", "EDITOR"})


class SandboxAuditTests(unittest.TestCase):
    def _company(self, root: Path) -> Path:
        (root / "automation").mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / "automation" / "company_router.py").write_text("orig", encoding="utf-8")
        return root

    def test_flags_write_to_readonly_code(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._company(Path(td))
            run_dir = root / "operations" / "runtime" / "autonomy-runs" / "R1"
            run_dir.mkdir(parents=True)
            since = 1_000_000.0
            code = root / "automation" / "company_router.py"
            code.write_text("tampered", encoding="utf-8")
            _mtime(code, since + 5)  # mutated after the worker started
            violations = audit_sandbox_writes(run_dir, since, company_root=root)
            self.assertEqual(len(violations), 1)
            self.assertTrue(violations[0].endswith("company_router.py"))

    def test_clean_when_only_run_dir_written(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._company(Path(td))
            _mtime(root / "automation" / "company_router.py", 999_000.0)  # untouched (older)
            run_dir = root / "operations" / "runtime" / "autonomy-runs" / "R2"
            run_dir.mkdir(parents=True)
            since = 1_000_000.0
            artifact = run_dir / "result.json"
            artifact.write_text("{}", encoding="utf-8")
            _mtime(artifact, since + 5)  # allowed: inside run_dir, not a scanned surface
            self.assertEqual(audit_sandbox_writes(run_dir, since, company_root=root), [])

    def test_ignores_pyc_and_pycache(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._company(Path(td))
            _mtime(root / "automation" / "company_router.py", 999_000.0)
            cache = root / "automation" / "__pycache__"
            cache.mkdir()
            since = 1_000_000.0
            for junk in (cache / "m.cpython-311.pyc", root / "automation" / "x.pyc"):
                junk.write_text("x", encoding="utf-8")
                _mtime(junk, since + 5)
            self.assertEqual(audit_sandbox_writes(root / "nope", since, company_root=root), [])

    def test_flags_write_to_readonly_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._company(Path(td))
            _mtime(root / "automation" / "company_router.py", 999_000.0)
            ledger = root / "finance" / "finance_ledger.db"
            ledger.parent.mkdir(parents=True)
            ledger.write_text("db", encoding="utf-8")
            since = 1_000_000.0
            _mtime(ledger, since + 5)
            violations = audit_sandbox_writes(root / "run", since, company_root=root)
            self.assertEqual(len(violations), 1)
            self.assertTrue(violations[0].endswith("finance_ledger.db"))


if __name__ == "__main__":
    unittest.main()
