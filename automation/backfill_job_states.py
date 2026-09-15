#!/usr/bin/env python3
"""Backfill lifecycle.json for existing completed/failed content jobs.

For jobs that finished BEFORE the state machine existed: derive current state
from status.json — completed → review (awaiting human), failed → terminated.
Records a single backfill event in events.jsonl.
"""
import json
from pathlib import Path

try:
    from ._safe_io import file_lock, read_text_limited_nofollow
    from .content_job_state import (
        log_event,
        read_lifecycle,
        utc_now,
        write_lifecycle,
    )
except ImportError:  # direct script execution
    from _safe_io import file_lock, read_text_limited_nofollow
    from content_job_state import (
        log_event,
        read_lifecycle,
        utc_now,
        write_lifecycle,
    )

JOBS_DIR = Path('/home/pwn/workspace/company/operations/runtime/content-jobs')


def main() -> int:
    done = 0
    skipped = 0
    # A missing content-jobs directory (fresh host/cleanup/wrong mount) means
    # there is nothing to backfill; it must not crash the cron.
    if not JOBS_DIR.is_dir():
        print(f"nothing to backfill: {JOBS_DIR} does not exist")
        return 0
    for job_dir in sorted(JOBS_DIR.iterdir()):
        if not job_dir.is_dir():
            continue
        status_path = job_dir / 'status.json'
        lc_path = job_dir / 'lifecycle.json'
        if not status_path.exists() or lc_path.exists():
            if not status_path.exists():
                skipped += 1
            continue
        try:
            # status.json lives in the worker-writable job tree: bound the
            # read and refuse symlinks exactly like content_job_state does,
            # so a huge file or planted link cannot stall/OOM the backfill.
            status = json.loads(read_text_limited_nofollow(
                status_path, max_bytes=2 * 1024 * 1024, errors="replace",
            ))
        except (OSError, ValueError):
            skipped += 1
            continue
        if not isinstance(status, dict):
            # A parseable non-object status.json (e.g. a JSON array) is as
            # unusable as an unreadable one; skip it instead of raising
            # AttributeError and aborting the whole backfill.
            skipped += 1
            continue
        s = str(status.get('status') or '')
        if s == 'completed':
            target = 'review'
            detail = 'backfill: worker completed before state machine existed'
        elif s == 'failed':
            target = 'terminated'
            detail = 'backfill: worker failed before state machine existed'
        elif s in ('needs_approval',):
            target = 'review'
            detail = 'backfill: needs approval before state machine existed'
        else:
            skipped += 1
            continue
        # Serialize the read-modify-write with content_job_state.transition():
        # a concurrent human transition would otherwise be silently overwritten
        # by a backfill that read the pre-transition lifecycle, and the existence
        # re-check must happen under the same lock to avoid resurrecting a job.
        with file_lock(lc_path):
            if lc_path.exists():
                skipped += 1
                continue
            lc = read_lifecycle(job_dir)
            lc['state'] = target
            lc.setdefault('history', []).append({
                'state': target, 'ts': utc_now(), 'event': 'backfill', 'detail': detail,
            })
            write_lifecycle(job_dir, lc)
        log_event(job_dir, target, 'backfill', detail)
        print(f"{job_dir.name}: status={s} -> {target}")
        done += 1

    print(f"\nbackfilled: {done}, skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
