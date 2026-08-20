#!/usr/bin/env python3
"""Backfill lifecycle.json for existing completed/failed content jobs.

For jobs that finished BEFORE the state machine existed: derive current state
from status.json — completed → review (awaiting human), failed → terminated.
Records a single backfill event in events.jsonl.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, '/home/pwn/workspace/company/automation')
from content_job_state import (
    log_event,
    read_lifecycle,
    utc_now,
    write_lifecycle,
)

JOBS_DIR = Path('/home/pwn/workspace/company/operations/runtime/content-jobs')

done = 0
skipped = 0
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
        status = json.loads(status_path.read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
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
