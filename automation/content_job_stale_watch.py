#!/usr/bin/env python3
"""
content-jobs 队列滞留告警 (2026-08-17, TVCR-P-20260809-03 落地)

检测 content-jobs 目录中状态非终态(pending/running/qa/review)且
超过 48h 未更新的 job, 输出滞留清单。配合 cron 使用:
- 有滞留 -> stdout 输出清单 (cron no_agent 模式直接投递)
- 无滞留 -> 静默 (exit 0, 无输出)
"""
import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from ._safe_io import read_text_limited_nofollow
except ImportError:  # direct execution from automation/
    from _safe_io import read_text_limited_nofollow

CONTENT_JOBS = '/home/pwn/workspace/company/operations/runtime/content-jobs'

# Cap per state file: content-job dirs are worker-writable, and a planted
# symlink/FIFO/huge file must not hang or OOM this alarm cron.
MAX_STATE_FILE_BYTES = 2 * 1024 * 1024


def _env_float(name: str, default: float) -> float:
    """Read a numeric env var, falling back on malformed cron values."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


STALE_HOURS = _env_float('STALE_HOURS', 48.0)

TERMINAL_STATES = {'published', 'archived', 'terminated', 'cancelled'}
# 活跃异常状态: 应该持续推进但卡住 (review 是等人工发布, 合法, 不在此列)
ACTIVE_STATES = {'pending', 'running', 'qa', 'retrying', 'started'}
# 超过该时长未完成的 review 才提示 (汇总行)
REVIEW_STALE_HOURS = _env_float('REVIEW_STALE_HOURS', 168.0)


def job_state(job_dir: str):
    """返回 (state, mtime) —— state 优先 lifecycle.json, 回退 status.json.

    状态文件位于 worker 可写的 job 目录: 以 O_NOFOLLOW + 字节上限读取,
    绝不跟随 worker 植入的符号链接, 也不让超大文件拖垮告警 cron。
    """
    for name, key in (("lifecycle.json", "state"), ("status.json", "status")):
        candidate = Path(job_dir) / name
        try:
            if candidate.is_symlink():
                continue
            payload = read_text_limited_nofollow(
                candidate, max_bytes=MAX_STATE_FILE_BYTES, errors="replace"
            )
            d = json.loads(payload)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(d, dict):
            try:
                modified = candidate.stat().st_mtime
            except OSError:
                modified = os.path.getmtime(job_dir)
            return d.get(key), modified
    # 目录可能正好在 isdir 检查后被删除; 告警脚本自身不允许被未捕获的
    # OSError 打崩 (否则滞留告警会静默消失)。
    try:
        return None, os.path.getmtime(job_dir)
    except OSError:
        return None, 0.0


def main():
    now = datetime.now(timezone.utc).timestamp()
    stale = []
    review_stale = []
    no_state = 0
    for job_dir in sorted(glob.glob(os.path.join(CONTENT_JOBS, '*'))):
        if os.path.islink(job_dir) or not os.path.isdir(job_dir):
            continue
        state, mtime = job_state(job_dir)
        # job_state() falls back to status.json's raw status when lifecycle.json
        # is absent (the executor writes status.json and never lifecycle.json).
        # Map the executor's terminal statuses onto the lifecycle vocabulary so
        # finished-but-unreviewed jobs are flagged by the REVIEW_STALE_HOURS
        # alarm instead of silently skipped. Mirrors backfill_job_states.py and
        # content_job_state._derive_initial_state.
        state = {
            "completed": "review",
            "needs_approval": "review",
            "failed": "terminated",
        }.get(state, state)
        if state in TERMINAL_STATES:
            continue
        age_h = (now - mtime) / 3600.0
        if state is None:
            no_state += 1
        elif state in ACTIVE_STATES and age_h > STALE_HOURS:
            stale.append((age_h, os.path.basename(job_dir), state))
        elif state == 'review' and age_h > REVIEW_STALE_HOURS:
            review_stale.append((age_h, os.path.basename(job_dir)))

    if not stale and not review_stale:
        return  # 静默

    lines = []
    if stale:
        lines.append(f"⚠️ content-jobs 活跃滞留告警: {len(stale)} 个 job 超过 {STALE_HOURS:.0f}h 未更新")
        lines.append("")
        lines.append("| 滞留时长 | job | 状态 |")
        lines.append("|---|---|---|")
        stale.sort(reverse=True)
        for age_h, rid, state in stale[:30]:
            lines.append(f"| {age_h:.0f}h | `{rid[:16]}` | {state} |")
        if len(stale) > 30:
            lines.append(f"| ... | 其余 {len(stale)-30} 个 | |")
        lines.append("")
        lines.append("处置: 卡 pending/running = executor 可能提前退出, 看 status.json.result 确认产物后走状态机; retrying = 重试超限, 人工确认。")
    if review_stale:
        lines.append(f"📋 另有 {len(review_stale)} 个 job 停在 review 超 {REVIEW_STALE_HOURS:.0f}h(等人工发布/归档), 最近: " +
                     ", ".join(f"`{rid[:10]}`({age_h:.0f}h)" for age_h, rid in sorted(review_stale, reverse=True)[:5]))
    if no_state:
        lines.append(f"ℹ️ {no_state} 个历史 job 无状态文件(老格式遗留), 未计入告警。")
    print("\n".join(lines))
    return 1


if __name__ == '__main__':
    sys.exit(main())
