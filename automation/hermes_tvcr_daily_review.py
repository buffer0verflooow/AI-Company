#!/usr/bin/env python3
"""Hermes cron entrypoint for the daily company TVCR operating review."""

from __future__ import annotations

import sys
from pathlib import Path


AUTOMATION_DIR = str(Path(__file__).resolve().parent)
if AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, AUTOMATION_DIR)

from tvcr_daily_review import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
