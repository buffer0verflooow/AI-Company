#!/usr/bin/env python3
"""Hermes cron entrypoint for the external market radar + agentkey probe.

Runs the primary AnySearch-backed radar first, then dispatches the agentkey
probe as a secondary data source.  Results converge in the same market_signals.db
so the pulse builder sees both sources.
"""

from __future__ import annotations

import json
import sys

AUTOMATION_DIR = "/home/pwn/workspace/company/automation"
if AUTOMATION_DIR not in sys.path:
    sys.path.insert(0, AUTOMATION_DIR)

from market_radar import main as radar_main  # noqa: E402
from agentkey_radar_probe import run_probe, load_config  # noqa: E402


if __name__ == "__main__":
    # Step 1 — Primary radar (AnySearch)
    radar_exit = radar_main()

    # Step 2 — AgentKey probe (best-effort, never fail the cron)
    try:
        config = load_config()
        if config.get("enabled", True) and config.get("agentkey_probe_enabled", True):
            print("agentkey-probe: starting...", flush=True)
            result = run_probe(config)
            print(f"agentkey-probe: {json.dumps(result, ensure_ascii=False)}", flush=True)
        else:
            print("agentkey-probe: disabled (agentkey_probe_enabled=false or radar disabled)", flush=True)
    except Exception as exc:
        print(f"agentkey-probe: failed (non-fatal) — {exc}", flush=True)

    # Exit code from the primary radar
    raise SystemExit(radar_exit)
