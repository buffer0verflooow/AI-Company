"""Small, dependency-free helpers for safe automation I/O.

The automation entrypoints are invoked by cron and by concurrent workers.  A
single uncaught I/O error must therefore be visible to the caller, while
temporary files, SQLite connections, and lock descriptors must always be
released.  This module centralises those mechanics so individual pipelines do
not each implement a subtly different version.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import stat
import tempfile
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from urllib.parse import quote

try:  # pragma: no cover - Windows is not the deployment platform
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_ENV_RE = re.compile(
    r"(?:^|_)(?:SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE_KEY|ACCESS_KEY|"
    r"API_KEY|KEY|TOKEN|BEARER|COOKIE|AUTH|AUTHORIZATION|DSN)(?:_|$)|"
    r"(?:DATABASE|REDIS)_URL$",
    re.IGNORECASE,
)
SECRET_ENV_EXTRA = {
    "WEIXIN_APP_ID",
    "WEIXIN_APP_SECRET",
    "WEIXIN_TOKEN",
    "QQ_APP_ID",
    "QQ_CLIENT_SECRET",
    "DASHSCOPE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
}
URL_CREDENTIAL_RE = re.compile(r"://[^/@\s]+@")
LOGGER = logging.getLogger(__name__)

__all__ = [
    "POOL_WORKER_ENV_EXACT",
    "POOL_WORKER_ENV_PREFIXES",
    "atomic_write_text",
    "file_lock",
    "locked_append_text",
    "locked_atomic_write_text",
    "pool_worker_environment",
    "quote_identifier",
    "read_text_limited",
    "read_text_limited_nofollow",
    "scrub_environment",
    "sqlite_connection",
    "sqlite_uri",
    "stream_contains",
]


def quote_identifier(identifier: str, *, allowed: Collection[str] | None = None) -> str:
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


def scrub_environment(
    base: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Remove credentials before launching an isolated or untrusted worker."""

    source = os.environ if base is None else base
    env: dict[str, str] = {}
    dropped: list[str] = []
    for key, value in source.items():
        if (
            key in SECRET_ENV_EXTRA
            or SECRET_ENV_RE.search(key)
            or URL_CREDENTIAL_RE.search(str(value))
        ):
            dropped.append(key)
            continue
        env[str(key)] = str(value)
    return env, sorted(dropped)


#: 池 worker 拉起环境白名单(deny-by-default;W12,用户 2026-09-19 裁定
#: "常驻池 worker 拉起环境收敛")。
#:
#: 精确键 = 进程定位/运行/临时目录所需的最小面(值不做任何默认填充:环境里
#: 没有就不出现)。
POOL_WORKER_ENV_EXACT = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TMPDIR", "TEMP", "TMP", "PYTHONUNBUFFERED", "PYTHONPATH",
    "VIRTUAL_ENV",
})

#: 前缀 = provider 配置/客户端盐(`SWARM_`)、provider 配置(`DEEPSEEK_`)、
#: 令牌键(`AUTH`,历史口径)、公司路由 bypass(`COMPANY_ROUTER_`)、出网代理
#: (含凭据的代理 URL 由第 1 层黑名单按值剔除)。**故意不含**
#: `HERMES_`/`AWS_`/`GITHUB_`/`GH_`/`*_TOKEN`(除 `AUTH`)/`*_SECRET`
#: 这类通用面。
POOL_WORKER_ENV_PREFIXES = (
    "SWARM_", "DEEPSEEK_", "AUTH", "COMPANY_ROUTER_",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
    "https_proxy", "http_proxy", "no_proxy",
)


def _pool_worker_env_allowed(key: str) -> bool:
    if key in POOL_WORKER_ENV_EXACT:
        return True
    return any(key.startswith(prefix) for prefix in POOL_WORKER_ENV_PREFIXES)


def pool_worker_environment(
    base: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """池 worker 拉起环境:白名单(deny-by-default)+ 记录被剔键名。

    两层顺序(W12):先 :func:`scrub_environment`(既有黑名单:密钥类键名 +
    含凭据 URL 值),再按本白名单收窄。因此"白名单前缀内的密钥"(如
    ``SWARM_API_SECRET`` / ``DEEPSEEK_TOKEN_X``)仍被第 1 层剔除;第 2 层
    只做收窄,**永不回填**。

    返回 ``(env, dropped)``。``dropped`` 只含键名(排序去重),绝不记值;
    某个可选键在 ``base`` 中不存在时结果里也不出现(不注入默认值)。
    """

    scrubbed, dropped = scrub_environment(base)
    env = {key: value for key, value in scrubbed.items()
           if _pool_worker_env_allowed(key)}
    dropped.extend(key for key in scrubbed if not _pool_worker_env_allowed(key))
    return env, sorted(set(dropped))


def read_text_limited(
    path: Path,
    *,
    max_bytes: int,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> str:
    """Read a text file while rejecting content larger than *max_bytes*."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    with Path(path).open("rb") as stream:
        payload = stream.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"file exceeds {max_bytes} bytes: {path}")
    return payload.decode(encoding, errors)


def read_text_limited_nofollow(
    path: Path,
    *,
    max_bytes: int,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> str:
    """Read a text file via ``O_NOFOLLOW``, rejecting symlinks atomically.

    Callers that validate ``is_symlink()`` before ``open()`` leave a TOCTOU
    window: between the check and the open an untrusted writer can swap the
    file for a symlink, and the open would follow it.  Opening with
    ``O_NOFOLLOW`` makes the guard atomic; on platforms without the flag it
    degrades to a plain read-only open.

    ``O_NONBLOCK`` plus a regular-file check keep a planted FIFO or device node
    from hanging the caller forever (a plain ``os.open(..., O_RDONLY)`` blocks
    on a FIFO until a writer appears) or leaking device bytes — several callers
    read files from worker-writable directories.
    """

    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    fd = os.open(path, flags)
    try:
        # fstat after open: the check is atomic against a swap between a
        # pre-open is_file() check and the open itself.
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"refusing to read non-regular file: {path}")
        # A single os.read() may return short (notably on network/FUSE
        # filesystems); treating that as EOF would silently truncate the
        # payload and let an oversized file slip past the max_bytes guard.
        # Loop until EOF or max_bytes + 1 bytes have been collected.
        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(fd, max_bytes + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(fd)
    if len(payload) > max_bytes:
        raise ValueError(f"file exceeds {max_bytes} bytes: {path}")
    return payload.decode(encoding, errors)


def stream_contains(path: Path, needle: bytes, *, chunk_size: int = 1024 * 1024) -> bool:
    """Search a file without loading the whole transcript into memory."""

    if not needle:
        return True
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    overlap = max(0, len(needle) - 1)
    previous = b""
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            combined = previous + chunk
            if needle in combined:
                return True
            previous = combined[-overlap:] if overlap else b""
    return False


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
    existing_mode: int | None = None
    with suppress(OSError):
        existing_mode = path.stat().st_mode & 0o7777
    temporary: Path | None = None
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
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        os.replace(temporary, path)
        temporary = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                with suppress(OSError):
                    # Some network/virtual filesystems do not support syncing
                    # directory descriptors.  The replace itself has already
                    # succeeded, so do not report a false write failure.
                    os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning("failed to remove temporary file %s: %s", temporary, exc)


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
    with file_lock(path), path.open("a", encoding=encoding) as stream:
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

    db: sqlite3.Connection | None = None
    try:
        path = Path(path)
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(path, timeout=timeout)
        else:
            db = sqlite3.connect(sqlite_uri(path, mode="ro"), uri=True, timeout=timeout)
        db.row_factory = sqlite3.Row
        # ``sqlite3.connect(timeout=...)`` already installs the busy handler;
        # re-setting a hard-coded 5000 ms here would silently override a caller
        # that asked for a shorter timeout.  Mirror the requested timeout.
        db.execute(f"PRAGMA busy_timeout={max(0, int(timeout * 1000))}")
        if not read_only:
            db.execute("PRAGMA journal_mode=WAL")
        yield db
        if not read_only:
            db.commit()
    except BaseException:
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
