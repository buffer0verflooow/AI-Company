#!/usr/bin/env python3
"""Emit mandatory isolation instructions for the daily auto-fix Cron job.

The actual repair remains agent-driven, but this deterministic preflight makes
the dirty-worktree boundary explicit and creates a disposable worktree from
the current HEAD.  It prevents the recurring prompt's broad "commit all
changes" wording from swallowing unrelated knowledge-base edits.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path("/home/pwn/workspace/company")


def _run(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=REPO,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{exc.__class__.__name__}: {exc}"
    # Preserve the two leading porcelain status columns; ``strip()`` would
    # remove the first column of the first dirty path and make scope checks
    # unreliable.
    return proc.returncode, proc.stdout.rstrip()


def main() -> int:
    status_code, status = _run("git", "status", "--porcelain")
    head_code, head = _run("git", "rev-parse", "HEAD")
    if status_code != 0 or head_code != 0 or not head:
        print("MANDATORY SAFETY STOP: unable to inspect the company repository; do not edit or commit.")
        return 0

    dirty_paths = [line[3:] for line in status.splitlines() if len(line) >= 4]
    automation_dirty = [path for path in dirty_paths if path == "automation" or path.startswith("automation/")]
    temporary_root = Path(tempfile.mkdtemp(prefix="company-auto-fix-"))
    worktree = temporary_root / "worktree"
    add_code, add_output = _run("git", "worktree", "add", "--detach", str(worktree), head)
    if add_code != 0:
        shutil.rmtree(temporary_root, ignore_errors=True)
        print("MANDATORY SAFETY STOP: could not create an isolated worktree.")
        print(add_output[-1000:])
        return 0

    payload = {
        "base_head": head,
        "worktree": str(worktree),
        "dirty_paths": dirty_paths,
        "automation_dirty_paths": automation_dirty,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print("=== MANDATORY AUTO-FIX SAFETY OVERRIDE ===")
    print(json.dumps(payload, ensure_ascii=False))
    print(f"1. Do all reads, edits, tests and the repair commit only in {worktree}.")
    print("2. Never edit or commit /home/pwn/workspace/company directly.")
    print("3. Stage only automation/** and its targeted tests; do not stage DASHBOARD, wiki, projects, or unrelated files.")
    if automation_dirty:
        print("4. The main checkout already has automation changes; do not cherry-pick or overwrite them. Report the conflict.")
    else:
        print("4. After tests pass, report the isolated commit SHA; merge only automation-scoped files if the base HEAD is unchanged.")
    print("5. Remove the disposable worktree after handing off the commit or a clear failure report.")
    print("=== END SAFETY OVERRIDE ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
