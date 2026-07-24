"""Small, dependency-free helpers for safe automation I/O.

The automation entrypoints are invoked by cron and by concurrent workers.  A
single uncaught I/O error must therefore be visible to the caller, while
temporary files, SQLite connections, and lock descriptors must always be
released.  This module centralises those mechanics so individual pipelines do
not each implement a subtly different version.
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Collection, Iterator, Optional
from urllib.parse import quote

try:  # pragma: no cover - Windows is not the deployment platform
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_identifier(identifier: str, *, allowed: Optional[Collection[str]] = None) -> str:
    """Validate and quote a SQL identifier.

    SQLite parameters cannot represent table/column names.  Callers that need
    dynamic identifiers must pass through this function (and preferably an
    explicit allow-list), rather than interpolating user/configuration text.
    """

    value = str(identifier)
    if allowed is not None and value not in allowed:
        raise ValueError(f"unsupported SQL identifier: {value!r}")
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"invalid SQL identifier: {value!r}")
    return '"' + value.replace('"', '""') + '"'


def sqlite_uri(path: Path, *, mode: str = "ro") -> str:
    """Build a SQLite URI for *path* without letting path text alter options.

    A raw ``f"file:{path}?mode=ro"`` is subtly unsafe for paths containing
    ``?``, ``#`` or percent escapes: SQLite can interpret part of the filename
    as URI options.  Percent-encoding the path keeps it a filename while still
    allowing the caller to select read-only mode.
    """

    if mode not in {"ro", "rw", "rwc", "memory"}:
        raise ValueError(f"unsupported SQLite URI mode: {mode!r}")
    resolved = Path(path).expanduser().resolve()
    encoded = quote(str(resolved), safe="/")
    return f"file:{encoded}?mode={mode}"


@contextmanager
def file_lock(target: Path) -> Iterator[None]:
    """Serialize writes associated with *target* using a sibling lock file."""

    lock_path = Path(f"{target}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    try:
        handle = lock_path.open("a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if handle is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a text file and clean up on every failure path."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def locked_atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically write *path* while excluding concurrent writers."""

    with file_lock(Path(path)):
        atomic_write_text(Path(path), text, encoding=encoding)


def locked_append_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Append text while serializing writers and forcing it to disk.

    Appending a JSONL record with a plain ``open(..., "a")`` is not atomic
    across processes: two cron workers can interleave bytes or one can observe
    a half-written record.  The sibling lock covers the whole write and the
    flush/fsync makes a successful return durable enough for the caller to
    record the delivery state.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        with path.open("a", encoding=encoding) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())


@contextmanager
def sqlite_connection(
    path: Path,
    *,
    read_only: bool = False,
    timeout: float = 5.0,
) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with rollback/close guarantees.

    The context manager commits successful write sessions and rolls back any
    exception before closing.  Read-only sessions use SQLite's URI mode and
    never issue a commit.
    """

    db: Optional[sqlite3.Connection] = None
    try:
        path = Path(path)
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(path, timeout=timeout)
        else:
            db = sqlite3.connect(sqlite_uri(path, mode="ro"), uri=True, timeout=timeout)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        if not read_only:
            db.execute("PRAGMA journal_mode=WAL")
        yield db
        if not read_only:
            db.commit()
    except Exception:
        if db is not None:
            try:
                if not read_only:
                    db.rollback()
            finally:
                db.close()
            db = None
        raise
    finally:
        if db is not None:
            db.close()
