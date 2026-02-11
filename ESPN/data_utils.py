"""
Data Utilities for ESPN CBB Pipeline
Generic data manipulation, normalization, and helper functions.
No ESPN-specific logic - these are reusable across data sources.
"""

import hashlib
import numpy as np
import pandas as pd
from typing import Any, Optional
from datetime import datetime, timezone


# ---------------- Type Coercion Utilities ----------------

def _to_int(x, default=0):
    """
    Safely convert value to integer with fallback.
    
    Args:
        x: Value to convert
        default: Default value if conversion fails
        
    Returns:
        Integer value or default
    """
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip()
            if x in ("", "--"):
                return default
            return int(float(x))
        return int(x)
    except Exception:
        return default


def _to_float(x, default=np.nan):
    """
    Safely convert value to float with fallback.
    
    Args:
        x: Value to convert
        default: Default value if conversion fails
        
    Returns:
        Float value or default
    """
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.strip()
            if x in ("", "--"):
                return default
            return float(x)
        return float(x)
    except Exception:
        return default


# ---------------- Math Utilities ----------------

def _safe_div(num, den, default=np.nan):
    """
    Safe division with fallback for zero/null denominators.
    
    Args:
        num: Numerator
        den: Denominator
        default: Value to return if denominator is zero/null
        
    Returns:
        Division result or default
    """
    return default if den in (0, 0.0, None) else (num / den)


# ---------------- String Parsing Utilities ----------------

def _parse_made_attempt(display: str):
    """
    Parse "X-Y" or "X/Y" format strings into (made, attempted) tuple.
    Common in box scores for FG, 3PT, FT stats.
    
    Args:
        display: String like "5-10" or "3/7"
        
    Returns:
        Tuple of (made, attempted) as integers
    """
    if not display or not isinstance(display, str):
        return (0, 0)
    d = display.strip()
    if d in ("--", ""):
        return (0, 0)
    if "-" in d:
        a, b = d.split("-", 1)
        return (_to_int(a, 0), _to_int(b, 0))
    if "/" in d:
        a, b = d.split("/", 1)
        return (_to_int(a, 0), _to_int(b, 0))
    return (0, 0)


# ---------------- Data Normalization ----------------

def _normalize_home_away_series(s: pd.Series) -> pd.Series:
    """
    Normalize home_away column to clean string values.
    Converts to lowercase, strips whitespace, handles nulls.
    
    Args:
        s: Pandas Series with home/away values
        
    Returns:
        Normalized Series with values: 'home', 'away', or pd.NA
    """
    s2 = s.astype("string")
    s2 = s2.str.strip().str.lower()
    s2 = s2.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA})
    return s2


def _normalize_id_series(s: pd.Series) -> pd.Series:
    """
    Normalize ID columns (game_id, event_id, team_id).
    Removes trailing .0 from float-like IDs, handles nulls.
    
    Args:
        s: Pandas Series with ID values
        
    Returns:
        Normalized Series with clean string IDs
    """
    s2 = s.astype(str)
    s2 = s2.str.replace(r"\.0$", "", regex=True)
    s2 = s2.replace({"nan": np.nan, "None": np.nan})
    return s2


# ---------------- Hashing & Identity ----------------

def _stable_row_hash(d: dict, keys: list) -> str:
    """
    Generate deterministic hash from dictionary values for specified keys.
    Used for deduplication and change detection.
    
    Args:
        d: Dictionary with row data
        keys: List of keys to include in hash
        
    Returns:
        SHA1 hex digest string
    """
    payload = "|".join([str(d.get(k, "")) for k in keys])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


# ---------------- Date/Time Utilities ----------------

def _utc_now_iso() -> str:
    """
    Get current UTC timestamp in ISO format without microseconds.
    Format: YYYY-MM-DDTHH:MM:SSZ
    
    Returns:
        ISO timestamp string
    """
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------- Data Quality Scoring ----------------

def _completeness_score_row(r: pd.Series) -> float:
    """
    Calculate completeness score for a row to determine quality.
    Used for deterministic deduplication - keeps best version of duplicate rows.
    
    Scoring:
        - 2.0 points: Row is completed
        - 2.0 points: Row has data_ok=True
        - 1.0 points: Fraction of critical fields present
        - 0.05 points: Has pulled_at_utc timestamp
    
    Args:
        r: Pandas Series representing a row
        
    Returns:
        Completeness score (float, typically 0-5.05)
    """
    completed = 1.0 if bool(r.get("completed")) else 0.0
    data_ok = 1.0 if bool(r.get("data_ok")) else 0.0

    critical = ["points_for", "points_against", "fga", "fta", "tov", "orb", "drb", "reb", "poss"]
    present = 0.0
    for c in critical:
        v = r.get(c, np.nan)
        if pd.notna(v):
            present += 1.0
    critical_frac = present / float(len(critical)) if critical else 0.0

    pulled = r.get("pulled_at_utc")
    pulled_bonus = 0.0
    if isinstance(pulled, str) and pulled.strip():
        pulled_bonus = 0.05

    return (2.0 * completed) + (2.0 * data_ok) + (1.0 * critical_frac) + pulled_bonus


# ---------------- Basketball-Specific Calculations ----------------

def _estimate_possessions(fga: float, fta: float, tov: float, orb: float) -> float:
    """
    Estimate team possessions using standard formula.
    Formula: FGA + 0.44*FTA - ORB + TOV
    
    Args:
        fga: Field goal attempts
        fta: Free throw attempts
        tov: Turnovers
        orb: Offensive rebounds
        
    Returns:
        Estimated possessions (float)
    """
    return float(fga + 0.44 * fta - orb + tov)


# ---------------- Home/Away Utilities ----------------

def _flip_home_away(val: Any) -> Optional[str]:
    """
    Flip home/away designation for opponent matching.
    
    Args:
        val: 'home', 'away', or other value
        
    Returns:
        Opposite value or None if invalid
    """
    v = str(val).strip().lower() if val is not None else ""
    if v == "home":
        return "away"
    if v == "away":
        return "home"
    return None
