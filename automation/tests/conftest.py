"""Test bootstrap: make the ``automation`` package importable from the repo root.

The ``pytest`` console script does not add the current working directory to
``sys.path`` the way ``python -m pytest`` does, so tests that ``import
automation.*`` fail to collect when invoked as ``pytest automation/tests -q``
from the repo root.  This conftest adds the repository root to ``sys.path`` so
the documented test command works regardless of how pytest is launched.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
