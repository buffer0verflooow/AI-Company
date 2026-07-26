#!/usr/bin/env python3
"""Hermes cron entrypoint for the read-only company daily digest."""

from __future__ import annotations

import sys


AUTOMATION_DIR = "/home/pwn/workspace/company/automation"
if AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, AUTOMATION_DIR)

from company_daily_digest import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
