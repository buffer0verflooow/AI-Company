from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from automation._safe_io import (
    atomic_write_text,
    read_text_limited,
    read_text_limited_nofollow,
    scrub_environment,
    stream_contains,
)


class SafeIOTests(unittest.TestCase):
    def test_scrub_environment_drops_secrets_and_credentialed_urls(self):
        env, dropped = scrub_environment({  # nosec B105 -- fake fixture values
            "PATH": "/bin",
            "GIT_AUTHOR_NAME": "Codex",
            "SERVICE_URL": "https://user:password@example.com/api",
            "AUTH_TOKEN": "secret",
        })
        self.assertEqual(env["GIT_AUTHOR_NAME"], "Codex")
        self.assertNotIn("SERVICE_URL", env)
        self.assertNotIn("AUTH_TOKEN", env)
        self.assertEqual(dropped, ["AUTH_TOKEN", "SERVICE_URL"])

    def test_stream_contains_finds_value_across_chunk_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "large.bin"
            path.write_bytes(b"abcde" + b"needle" + b"tail")
            self.assertTrue(stream_contains(path, b"needle", chunk_size=8))
            self.assertFalse(stream_contains(path, b"missing", chunk_size=4))

    def test_read_text_limited_rejects_oversized_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "payload.txt"
            path.write_text("12345", encoding="utf-8")
            self.assertEqual(read_text_limited(path, max_bytes=5), "12345")
            with self.assertRaises(ValueError):
                read_text_limited(path, max_bytes=4)

    def test_read_text_limited_nofollow_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            target = root / "secret.txt"
            target.write_text("secret", encoding="utf-8")
            link = root / "payload.txt"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not supported on this platform")
            # O_NOFOLLOW must refuse to follow the link atomically.
            with self.assertRaises(OSError):
                read_text_limited_nofollow(link, max_bytes=1024)
            # Regular files still read normally.
            self.assertEqual(
                read_text_limited_nofollow(target, max_bytes=1024), "secret"
            )

    def test_atomic_write_preserves_existing_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            path.write_text("old", encoding="utf-8")
            path.chmod(0o640)
            atomic_write_text(path, "new")
            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)


if __name__ == "__main__":
    unittest.main()
