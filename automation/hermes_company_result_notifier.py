#!/usr/bin/env python3
"""Hermes cron entrypoint for the company Research result notifier."""

from __future__ import annotations

import sys
from pathlib import Path

AUTOMATION_DIR = str(Path(__file__).resolve().parent)
if AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, AUTOMATION_DIR)

from company_result_notifier import main

if __name__ == "__main__":
    raise SystemExit(main())
