#!/usr/bin/env python3
"""
Run logging utilities.

Goals:
- Safe, atomic writes (avoid partial/corrupt JSON on crash).
- Works with Path or string-like paths.
- JSON-serializes common non-JSON types (datetime, numpy, pandas) best-effort.
- Backward compatible with existing callers.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Union


def _json_default(o: Any) -> Any:
    # datetime/date
    if isinstance(o, (datetime, date)):
        return o.isoformat()

    # numpy scalars / arrays (best-effort, without hard deps)
    try:
        import numpy as np  # type: ignore
    except Exception:
        np = None  # type: ignore

    if np is not None:
        if isinstance(o, (np.integer, np.floating)):  # type: ignore[attr-defined]
            return o.item()
        if isinstance(o, np.ndarray):  # type: ignore[attr-defined]
            return o.tolist()

    # pandas Timestamp/Series/DataFrame (best-effort)
    try:
        import pandas as pd  # type: ignore
    except Exception:
        pd = None  # type: ignore

    if pd is not None:
        if isinstance(o, getattr(pd, "Timestamp", ())):
            return o.isoformat()
        if isinstance(o, getattr(pd, "Series", ())):
            return o.to_dict()
        if isinstance(o, getattr(pd, "DataFrame", ())):
            return o.to_dict(orient="records")

    # Path
    if isinstance(o, Path):
        return str(o)

    # last resort
    return str(o)


def _as_path(p: Union[Path, str, os.PathLike[str]]) -> Path:
    return p if isinstance(p, Path) else Path(p)


def write_run_log(path: Union[Path, str, os.PathLike[str]], payload: Mapping[str, Any]) -> None:
    """
    Write a JSON run log.

    - Creates parent dirs.
    - Writes atomically via temp file + replace.
    - Ensures UTF-8 and trailing newline for nicer diffs.
    """
    out_path = _as_path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    data = json.dumps(
        dict(payload),
        indent=2,
        ensure_ascii=False,
        default=_json_default,
        sort_keys=True,
    ) + "\n"

    # Atomic write
    fd, tmp_name = tempfile.mkstemp(prefix=out_path.name + ".", suffix=".tmp", dir=str(out_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, out_path)
    finally:
        # If something failed before replace, try to clean up temp file
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except Exception:
            pass
