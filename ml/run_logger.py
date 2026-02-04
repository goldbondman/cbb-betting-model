#!/usr/bin/env python3
"""
Run logging utilities.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


def write_run_log(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
