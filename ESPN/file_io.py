"""
File I/O Operations
All CSV read/write operations with safety guarantees, checkpointing, and error logging.
"""

import os
import json
import shutil
import tempfile
from typing import Any, Dict, List, Optional

import pandas as pd

from espn_config import (
    OUTPUT_FILE_SCHEMAS,
    CHECKPOINT_FILE,
    ERROR_LOG_PATH,
    DRY_RUN,
    VALID_HOME_AWAY,
)
from data_utils import (
    _normalize_id_series,
    _normalize_home_away_series,
    _completeness_score_row,
    _utc_now_iso,
)


# ---------------- Error Logging ----------------

# Global error log accumulator
ERROR_LOG: List[Dict[str, Any]] = []


def log_error(
    context: str,
    error: Exception,
    event_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None
) -> None:
    """
    Log an error to the global error log.
    
    Args:
        context: Description of where/why error occurred
        error: The exception that was raised
        event_id: Optional event ID for context
        extra: Optional additional context dictionary
    """
    rec = {
        "ts_utc": _utc_now_iso(),
        "context": context,
        "event_id": event_id,
        "error_type": type(error).__name__,
        "error_message": str(error)[:600],
    }
    if extra:
        rec.update(extra)
    ERROR_LOG.append(rec)


def write_error_summary(path: str = ERROR_LOG_PATH) -> None:
    """
    Write accumulated errors to JSON file.
    
    Args:
        path: Output path for error log JSON
    """
    if not ERROR_LOG:
        return
    payload = {
        "run_ts_utc": _utc_now_iso(),
        "total_errors": len(ERROR_LOG),
        "errors": ERROR_LOG,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


# ---------------- Checkpointing ----------------

def load_checkpoint() -> Dict[str, Any]:
    """
    Load checkpoint from disk for pipeline resumption.
    
    Returns:
        Checkpoint dictionary, or empty dict if not found/invalid
    """
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            log_error("checkpoint_load", e, extra={"path": CHECKPOINT_FILE})
            return {}
    return {}


def save_checkpoint(payload: Dict[str, Any]) -> None:
    """
    Save checkpoint to disk.
    
    Args:
        payload: Checkpoint data to persist
    """
    try:
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        log_error("checkpoint_save", e, extra={"path": CHECKPOINT_FILE})


def clear_checkpoint() -> None:
    """
    Remove checkpoint file (typically after successful run).
    """
    try:
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
    except Exception as e:
        log_error("checkpoint_clear", e, extra={"path": CHECKPOINT_FILE})


# ---------------- Safe CSV Operations ----------------

def _atomic_csv_write(df: pd.DataFrame, path: str) -> None:
    """
    Write CSV atomically with backup/rollback on failure.
    
    Strategy:
    1. Write to temp file
    2. Backup existing file if present
    3. Move temp file to target path
    4. Delete backup on success
    
    Args:
        df: DataFrame to write
        path: Target CSV path
        
    Raises:
        Exception: If write fails (original file remains untouched)
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir=os.path.dirname(path) or ".")
    try:
        df.to_csv(tmp_path, index=False)
        os.close(fd)

        # Backup existing file
        if os.path.exists(path) and os.path.getsize(path) > 0:
            shutil.copy2(path, f"{path}.backup")

        # Atomic move
        shutil.move(tmp_path, path)

        # Clean up backup
        backup = f"{path}.backup"
        if os.path.exists(backup):
            os.remove(backup)

    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise


def _ensure_csv_exists(path: str, columns: list) -> None:
    """
    Initialize CSV file with schema if it doesn't exist.
    
    Args:
        path: CSV file path
        columns: List of column names for schema
    """
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pd.DataFrame(columns=columns).to_csv(path, index=False)


def _read_csv_if_exists(path: str) -> pd.DataFrame:
    """
    Read CSV with normalization, or return empty DataFrame if not found.
    
    Applies automatic normalization:
    - game_id, event_id, team_id → normalized IDs
    - home_away → normalized to 'home'/'away'
    
    Args:
        path: CSV file path
        
    Returns:
        DataFrame (empty if file doesn't exist or read fails)
    """
    if os.path.exists(path) and os.path.getsize(path) > 0:
        try:
            df = pd.read_csv(path)
            if "game_id" in df.columns:
                df["game_id"] = _normalize_id_series(df["game_id"])
            if "event_id" in df.columns:
                df["event_id"] = _normalize_id_series(df["event_id"])
            if "team_id" in df.columns:
                df["team_id"] = _normalize_id_series(df["team_id"])
            if "home_away" in df.columns:
                df["home_away"] = _normalize_home_away_series(df["home_away"])
            return df
        except Exception as e:
            log_error("read_csv", e, extra={"path": path})
            return pd.DataFrame()
    return pd.DataFrame()


# ---------------- Data Quality & Integrity ----------------

def verify_dataframe_integrity(df: pd.DataFrame, filename: str) -> tuple[bool, List[str]]:
    """
    Verify DataFrame meets integrity requirements.
    
    Checks:
    - Required columns present
    - home_away values valid ('home'/'away')
    - Timestamps parseable
    
    Args:
        df: DataFrame to validate
        filename: Filename for error messages (e.g., "espn_games.csv")
        
    Returns:
        Tuple of (hard_fail, issues_list)
        - hard_fail: True if critical errors found
        - issues_list: List of issue descriptions
    """
    issues: List[str] = []
    if df is None or df.empty:
        return False, [f"{filename}: dataframe is empty"]

    required_cols = {
        "espn_games.csv": ["date", "game_id", "game_datetime_utc", "home_team", "away_team", "completed"],
        "espn_team_game_logs.csv": ["event_id", "team_id", "team", "home_away", "game_datetime_utc"],
        "espn_team_game_features.csv": ["event_id", "team_id", "game_datetime_utc"],
        "espn_matchups_model_ready.csv": ["event_id"],
    }

    if filename in required_cols:
        missing = [c for c in required_cols[filename] if c not in df.columns]
        if missing:
            issues.append(f"{filename}: missing required columns: {missing}")

    # Validate home_away values
    if "home_away" in df.columns:
        bad = df[~df["home_away"].isin(list(VALID_HOME_AWAY)) & df["home_away"].notna()]
        if len(bad) > 0:
            issues.append(
                f"{filename}: {len(bad)} rows have invalid home_away values: "
                f"{bad['home_away'].unique()[:10].tolist()}"
            )

    # Validate timestamps
    if "game_datetime_utc" in df.columns:
        dt = pd.to_datetime(df["game_datetime_utc"], utc=True, errors="coerce")
        bad_dt = int(dt.isna().sum())
        if bad_dt > 0 and (bad_dt / max(1, len(df))) > 0.10:
            issues.append(
                f"{filename}: {bad_dt}/{len(df)} ({bad_dt/len(df)*100:.1f}%) "
                f"bad game_datetime_utc values"
            )

    hard_fail = any("missing required columns" in x for x in issues)
    return (not hard_fail), issues


def _enforce_column_order(df: pd.DataFrame, filename: str) -> pd.DataFrame:
    """
    Enforce column order based on OUTPUT_FILE_SCHEMAS.
    
    Args:
        df: DataFrame to reorder
        filename: Filename to look up schema
        
    Returns:
        DataFrame with columns in schema order (extra columns appended at end)
    """
    if filename not in OUTPUT_FILE_SCHEMAS:
        return df
    
    schema_cols = OUTPUT_FILE_SCHEMAS[filename]
    df_cols = df.columns.tolist()
    
    # Start with schema order
    ordered_cols = [c for c in schema_cols if c in df_cols]
    
    # Append any extra columns not in schema
    extra_cols = [c for c in df_cols if c not in schema_cols]
    ordered_cols.extend(extra_cols)
    
    return df[ordered_cols]


def _append_dedupe_write(
    existing_path: str,
    new_df: pd.DataFrame,
    subset_keys: List[str],
    sort_cols: Optional[List[str]] = None
) -> pd.DataFrame:
    """
    Append new data to existing CSV with smart deduplication.
    
    Deduplication strategy:
    - Uses completeness_score_row() to pick best version of duplicates
    - Considers: completed status, data_ok flag, field presence, timestamp
    - Deterministic: same input always produces same output
    
    Args:
        existing_path: Path to existing CSV
        new_df: New data to append
        subset_keys: Columns that define uniqueness (e.g., ["event_id", "team_id"])
        sort_cols: Optional columns to sort final result
        
    Returns:
        Combined DataFrame (normalized and deduplicated)
        
    Raises:
        ValueError: If integrity check fails critically
    """
    filename = os.path.basename(existing_path)

    # Normalize new data
    if new_df is not None and not new_df.empty:
        new_df = new_df.copy()
        if "game_id" in new_df.columns:
            new_df["game_id"] = _normalize_id_series(new_df["game_id"])
        if "event_id" in new_df.columns:
            new_df["event_id"] = _normalize_id_series(new_df["event_id"])
        if "team_id" in new_df.columns:
            new_df["team_id"] = _normalize_id_series(new_df["team_id"])
        if "home_away" in new_df.columns:
            new_df["home_away"] = _normalize_home_away_series(new_df["home_away"])

    # Integrity check on new data
    ok, issues = verify_dataframe_integrity(new_df, filename)
    if issues:
        if not ok:
            raise ValueError("Integrity gate failed:\n  - " + "\n  - ".join(issues))
        print(f"[WARN] Integrity notes for {filename}:")
        for x in issues[:20]:
            print(f"  - {x}")

    # Load existing data
    old = _read_csv_if_exists(existing_path)
    combined = new_df.copy() if old.empty else pd.concat([old, new_df], ignore_index=True)

    # Deduplicate using completeness scoring
    if subset_keys:
        # Normalize keys
        for k in subset_keys:
            if k in combined.columns:
                if k == "home_away":
                    combined[k] = _normalize_home_away_series(combined[k])
                else:
                    combined[k] = _normalize_id_series(combined[k])

        # Calculate quality score for each row
        combined["_dq_score"] = combined.apply(_completeness_score_row, axis=1)

        # Add timestamp for tie-breaking
        if "pulled_at_utc" in combined.columns:
            pulled = pd.to_datetime(combined["pulled_at_utc"], utc=True, errors="coerce")
            combined["_pulled_ts"] = pulled.astype("int64", errors="ignore")
            combined["_pulled_ts"] = combined["_pulled_ts"].fillna(0)
        else:
            combined["_pulled_ts"] = 0

        # Sort and dedupe: keep best row per key group
        sort_by = list(subset_keys) + ["_dq_score", "_pulled_ts"]
        asc = [True] * len(subset_keys) + [False, False]  # Higher score = better
        combined = combined.sort_values(sort_by, ascending=asc)
        combined = combined.drop_duplicates(subset=subset_keys, keep="first")
        combined = combined.drop(columns=["_dq_score", "_pulled_ts"], errors="ignore")

    # Final sort
    if sort_cols:
        sort_cols_present = [c for c in sort_cols if c in combined.columns]
        if sort_cols_present:
            combined = combined.sort_values(sort_cols_present)

    # Enforce column order from schema
    combined = _enforce_column_order(combined, filename)

    # Write or dry run
    if DRY_RUN:
        print(f"[DRY RUN] Would write {len(combined)} rows -> {existing_path}")
        return combined

    _atomic_csv_write(combined, existing_path)
    return combined


# ---------------- Initialization ----------------

def ensure_all_output_files_exist() -> None:
    """
    Initialize all pipeline output CSV files with schemas.
    Safe to call multiple times (no-op if files exist).
    """
    for path, schema in OUTPUT_FILE_SCHEMAS.items():
        _ensure_csv_exists(path, schema)
