"""Report helpers for summarizing backtest output."""

from __future__ import annotations

from typing import Any

import pandas as pd


def summarize_backtest(results: dict[str, Any]) -> pd.DataFrame:
    """Create a single-row DataFrame summary from backtest results."""
    return pd.DataFrame(
        [
            {
                "mae": results.get("mae", 0.0),
                "win_pct": results.get("win_pct", 0.0),
                "roi": results.get("roi", 0.0),
                "total_games": results.get("total_games", 0),
            }
        ]
    )
