from __future__ import annotations

import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from automation.import_company_data import _extract_xls_exports


class SafeArchiveExtractionTests(unittest.TestCase):
    def _extract(self, archive_path: Path, destination: Path) -> list[Path]:
        with zipfile.ZipFile(archive_path) as archive:
            return _extract_xls_exports(archive, destination)

    def test_extracts_only_root_level_xls_exports(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive_path = root / "exports.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("article.xls", b"xls-data")
                archive.writestr("nested/ignored.xls", b"nested")
                archive.writestr("readme.txt", b"text")
            destination = root / "out"
            destination.mkdir()
            exports = self._extract(archive_path, destination)
            self.assertEqual([path.name for path in exports], ["article.xls"])
            self.assertEqual(exports[0].read_bytes(), b"xls-data")

    def test_rejects_path_traversal_and_backslash_members(self):
        for unsafe in ("../escape.xls", "/absolute.xls", "folder\\escape.xls"):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                archive_path = root / "unsafe.zip"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(unsafe, b"data")
                destination = root / "out"
                destination.mkdir()
                with self.assertRaises(ValueError):
                    self._extract(archive_path, destination)

    def test_rejects_symlink_members(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive_path = root / "symlink.zip"
            info = zipfile.ZipInfo("link.xls")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(info, "target.xls")
            destination = root / "out"
            destination.mkdir()
            with self.assertRaises(ValueError):
                self._extract(archive_path, destination)

    def test_rejects_excessive_member_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive_path = root / "many.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("a.xls", b"a")
                archive.writestr("b.xls", b"b")
            destination = root / "out"
            destination.mkdir()
            with (
                patch("automation.import_company_data.MAX_ARCHIVE_MEMBERS", 1),
                self.assertRaises(ValueError),
            ):
                self._extract(archive_path, destination)

    def test_rejects_suspicious_compression_ratio(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive_path = root / "bomb.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("article.xls", b"0" * (1024 * 1024))
            destination = root / "out"
            destination.mkdir()
            with self.assertRaises(ValueError):
                self._extract(archive_path, destination)


if __name__ == "__main__":
    unittest.main()
