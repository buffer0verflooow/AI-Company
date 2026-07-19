#!/usr/bin/env python3
"""Hermes cron entrypoint for the external market radar."""

from __future__ import annotations

import sys


AUTOMATION_DIR = "/home/pwn/workspace/company/automation"
if AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, AUTOMATION_DIR)

from market_radar import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
