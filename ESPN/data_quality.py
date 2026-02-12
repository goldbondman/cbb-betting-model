"""
Data Quality Validation and Repair
Detect and repair data quality issues in team-game rows.
Includes Data Quality Repair Gate (DQRG) for self-healing.
"""

from typing import Dict, List, Tuple, Any

import pandas as pd
import numpy as np

from espn_config import (
    DQRG_ENABLE,
    DQRG_MAX_EVENTS,
    DQRG_REFETCH_ON_FAIL,
    PARSE_VERSION,
)
from data_utils import (
    _normalize_id_series,
    _normalize_home_away_series,
    _to_int,
    _safe_div,
    _estimate_possessions,
    _utc_now_iso,
    _completeness_score_row,
)


def _dedupe_by_completeness(df: pd.DataFrame, keys: List[str], label: str) -> pd.DataFrame:
    """
    Deduplicate using completeness scoring.
    Keeps the best version of each duplicate based on data quality.
    
    Args:
        df: DataFrame to deduplicate
        keys: Columns that define uniqueness
        label: Description for logging
        
    Returns:
        Deduplicated DataFrame
    """
    out = df.copy()
    for k in keys:
        if k in out.columns:
            out[k] = _normalize_id_series(out[k])

    out["_dq_score"] = out.apply(_completeness_score_row, axis=1)
    out = out.sort_values(keys + ["_dq_score"], ascending=[True] * len(keys) + [False])
    out = out.drop_duplicates(subset=keys, keep="first").drop(columns=["_dq_score"], errors="ignore")
    print(f"{label}: deduped to {len(out)} rows using completeness score on keys={keys}")
    return out


def _dqrg_find_issues(df_logs: pd.DataFrame) -> pd.DataFrame:
    """
    Identify team-game rows that are completed but missing key derived fields.
    
    Checks for:
    - Completed games
    - Base inputs present (fga, fta, tov, orb)
    - Derived fields missing (poss, efg, ftr, 3par, shooting %, ratings)
    
    Args:
        df_logs: DataFrame with team-game rows
        
    Returns:
        DataFrame with rows that have issues (columns: event_id, team_id, dq_missing_fields, dq_reason_codes)
    """
    if df_logs is None or df_logs.empty:
        return pd.DataFrame()

    d = df_logs.copy()
    
    # Convert to numeric
    for c in ["completed", "fga", "fta", "tov", "orb", "fgm", "tpm", "tpa", "ftm", 
              "poss", "efg", "ftr", "3par", "3p_pct", "ft_pct", "ortg", "drtg"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce") if c not in ("completed",) else d[c]

    # Identify problematic rows
    completed = d["completed"] == True if "completed" in d.columns else pd.Series(False, index=d.index)
    has_base = (
        (d.get("fga", 0).fillna(0) > 0) & 
        (d.get("fta", 0).fillna(0) >= 0) & 
        (d.get("tov", 0).fillna(0) >= 0) & 
        (d.get("orb", 0).fillna(0) >= 0)
    )

    missing_poss = d.get("poss", pd.Series(np.nan, index=d.index)).isna()
    missing_efg = d.get("efg", pd.Series(np.nan, index=d.index)).isna()
    missing_rates = (
        d.get("ftr", pd.Series(np.nan, index=d.index)).isna() |
        d.get("3par", pd.Series(np.nan, index=d.index)).isna() |
        d.get("3p_pct", pd.Series(np.nan, index=d.index)).isna() |
        d.get("ft_pct", pd.Series(np.nan, index=d.index)).isna()
    )
    missing_rtgs = (
        d.get("ortg", pd.Series(np.nan, index=d.index)).isna() | 
        d.get("drtg", pd.Series(np.nan, index=d.index)).isna()
    )

    mask = completed & has_base & (missing_poss | missing_efg | missing_rates | missing_rtgs)

    issues = d.loc[mask, ["event_id", "team_id", "team", "home_away", "game_datetime_utc"]].copy()
    if issues.empty:
        return issues

    # Identify which fields are missing
    def _missing_list(ridx):
        miss = []
        if missing_poss.loc[ridx]:
            miss.append("poss")
        if missing_efg.loc[ridx]:
            miss.append("efg")
        if d.get("ftr", pd.Series(np.nan, index=d.index)).isna().loc[ridx]:
            miss.append("ftr")
        if d.get("3par", pd.Series(np.nan, index=d.index)).isna().loc[ridx]:
            miss.append("3par")
        if d.get("3p_pct", pd.Series(np.nan, index=d.index)).isna().loc[ridx]:
            miss.append("3p_pct")
        if d.get("ft_pct", pd.Series(np.nan, index=d.index)).isna().loc[ridx]:
            miss.append("ft_pct")
        if d.get("ortg", pd.Series(np.nan, index=d.index)).isna().loc[ridx]:
            miss.append("ortg")
        if d.get("drtg", pd.Series(np.nan, index=d.index)).isna().loc[ridx]:
            miss.append("drtg")
        return "|".join(miss)

    issues["dq_missing_fields"] = [_missing_list(i) for i in issues.index]
    issues["dq_reason_codes"] = "derived_missing_base_present"
    return issues


def _dqrg_repair_in_place(df_logs: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Attempt to repair derived fields for completed rows when base inputs are present.
    Also builds an audit df for what happened.
    
    Repair strategy:
    1. Identify rows with issues
    2. Attempt to recompute derived fields from base inputs
    3. If repair fails and DQRG_REFETCH_ON_FAIL=True, refetch summary and replace
    
    Args:
        df_logs: DataFrame with team-game rows
        
    Returns:
        Tuple of (repaired_df, audit_df)
        - repaired_df: Original df with repairs applied
        - audit_df: Log of repair attempts
    """
    if df_logs is None or df_logs.empty or not DQRG_ENABLE:
        return df_logs, pd.DataFrame()

    df = df_logs.copy()
    df["event_id"] = _normalize_id_series(df["event_id"]) if "event_id" in df.columns else df.get("event_id")
    df["team_id"] = _normalize_id_series(df["team_id"]) if "team_id" in df.columns else df.get("team_id")
    if "home_away" in df.columns:
        df["home_away"] = _normalize_home_away_series(df["home_away"])

    issues = _dqrg_find_issues(df)
    if issues.empty:
        return df, pd.DataFrame()

    issues = issues.head(DQRG_MAX_EVENTS).copy()

    audit_rows: List[Dict[str, Any]] = []

    for _, r in issues.iterrows():
        event_id = str(r.get("event_id"))
        team_id = str(r.get("team_id"))
        missing = str(r.get("dq_missing_fields") or "")

        action_plan = []
        success = False
        actions_taken = []

        try:
            m = (df["event_id"].astype(str) == event_id) & (df["team_id"].astype(str) == team_id)
            if m.sum() != 1:
                raise ValueError("dqrg_key_mismatch")

            idx = df.index[m][0]
            row = df.loc[idx].to_dict()

            # Repair purely from base columns
            fgm = _to_int(row.get("fgm"), 0)
            fga = _to_int(row.get("fga"), 0)
            tpm = _to_int(row.get("tpm"), 0)
            tpa = _to_int(row.get("tpa"), 0)
            ftm = _to_int(row.get("ftm"), 0)
            fta = _to_int(row.get("fta"), 0)
            tov = _to_int(row.get("tov"), 0)
            orb = _to_int(row.get("orb"), 0)
            pf = _to_int(row.get("points_for"), 0)
            pa = _to_int(row.get("points_against"), 0)

            if fga <= 0:
                raise ValueError("dqrg_no_fga")

            # Poss + shooting rates
            poss = _estimate_possessions(fga, fta, tov, orb)
            efg = _safe_div((fgm + 0.5 * tpm), fga, np.nan)
            ftr = _safe_div(fta, fga, np.nan)
            threepar = _safe_div(tpa, fga, np.nan)
            three_pct = _safe_div(tpm, tpa, np.nan)
            ft_pct = _safe_div(ftm, fta, np.nan)

            df.at[idx, "poss"] = float(poss) if pd.notna(poss) else np.nan
            df.at[idx, "efg"] = float(efg) if pd.notna(efg) else np.nan
            df.at[idx, "ftr"] = float(ftr) if pd.notna(ftr) else np.nan
            df.at[idx, "3par"] = float(threepar) if pd.notna(threepar) else np.nan
            df.at[idx, "3p_pct"] = float(three_pct) if pd.notna(three_pct) else np.nan
            df.at[idx, "ft_pct"] = float(ft_pct) if pd.notna(ft_pct) else np.nan

            # Ratings
            ortg = _safe_div(pf * 100.0, poss, np.nan)
            drtg = _safe_div(pa * 100.0, poss, np.nan)
            netrtg = (ortg - drtg) if (pd.notna(ortg) and pd.notna(drtg)) else np.nan

            df.at[idx, "ortg"] = float(ortg) if pd.notna(ortg) else np.nan
            df.at[idx, "drtg"] = float(drtg) if pd.notna(drtg) else np.nan
            df.at[idx, "netrtg"] = float(netrtg) if pd.notna(netrtg) else np.nan
            df.at[idx, "pace"] = df.at[idx, "poss"]

            actions_taken.append("recompute_derived_from_base")
            success = True

        except Exception as e:
            action_plan.append("refetch_summary_and_rebuild" if DQRG_REFETCH_ON_FAIL else "skip_refetch")
            actions_taken.append(f"repair_failed:{type(e).__name__}")

            if DQRG_REFETCH_ON_FAIL:
                try:
                    # Import here to avoid circular dependency
                    from espn_http_client import fetch_summary
                    from espn_parsers import parse_summary_json, summary_to_team_rows
                    from metrics_calculator import _compute_per_game_advanced_metrics
                    
                    raw = fetch_summary(event_id)
                    s = parse_summary_json(raw, event_id)
                    hrow, arow = summary_to_team_rows(s)
                    repair_rows = [hrow, arow]
                    repair_df = pd.DataFrame(repair_rows)
                    repair_df = _compute_per_game_advanced_metrics(repair_df)
                    repair_df["event_id"] = _normalize_id_series(repair_df["event_id"])
                    repair_df["team_id"] = _normalize_id_series(repair_df["team_id"])
                    if "home_away" in repair_df.columns:
                        repair_df["home_away"] = _normalize_home_away_series(repair_df["home_away"])

                    # Replace both team rows for that event_id (only if both exist)
                    if (repair_df["event_id"].astype(str) == event_id).sum() == 2:
                        df = df[df["event_id"].astype(str) != event_id].copy()
                        df = pd.concat([df, repair_df], ignore_index=True)
                        actions_taken.append("refetch_summary_replaced_event_rows")
                        success = True
                except Exception as e2:
                    actions_taken.append(f"refetch_failed:{type(e2).__name__}")
                    from file_io import log_error
                    log_error("dqrg_refetch", e2, event_id=event_id, extra={"team_id": team_id})

        audit_rows.append({
            "event_id": event_id,
            "team_id": team_id,
            "team": r.get("team"),
            "home_away": r.get("home_away"),
            "dq_missing_fields": missing,
            "dq_reason_codes": str(r.get("dq_reason_codes") or ""),
            "dq_action_plan": "|".join(action_plan) if action_plan else "recompute_derived_from_base",
            "dq_repair_success": int(success),
            "dq_repair_actions_taken": "|".join(actions_taken),
            "pulled_at_utc": _utc_now_iso(),
            "parse_version": PARSE_VERSION,
        })

    audit_df = pd.DataFrame(audit_rows)
    return df, audit_df
