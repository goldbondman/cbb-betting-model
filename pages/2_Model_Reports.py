#!/usr/bin/env python3
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st

from core.supabase_utils import get_public_supabase_client

st.set_page_config(page_title="Model Reports", page_icon="📊", layout="wide")

# ===== Config =====
# Point Model Reports at a single reporting view (recommended).
# Set this to whatever you created, e.g. v_model_report_spread
REPORTING_VIEW = (os.getenv("MODEL_REPORT_VIEW") or "v_backtest_spread").strip()

# Fallback local CSV if Supabase isn't available
FALLBACK_CSV = (os.getenv("MODEL_REPORT_FALLBACK_CSV") or "ml/predictions_latest.csv").strip()

# How far back to pull from DB
LOOKBACK_DAYS = int(os.getenv("MODEL_REPORT_LOOKBACK_DAYS") or "30")


def _get_supabase_client():
    return get_public_supabase_client()


def _load_predictions() -> pd.DataFrame:
    """
    Loads predictions from a single reporting view (preferred).
    Falls back to predictions table, raw.predictions_latest, then local CSV.

    Expected (ideal) columns from the view:
      - model_version_id (or model_version)
      - game_datetime_utc (timestamp)
      - pred_margin_home (or ensemble_prediction / pred_spread)
      - closing_spread_home (or market_spread / spread_home)
      - actual_margin_home (numeric)
      - optionally: ats_result_closing_proxy (home_cover/away_cover/push)
    """
    client = _get_supabase_client()
    if client is None:
        if os.path.exists(FALLBACK_CSV):
            return pd.read_csv(FALLBACK_CSV)
        return pd.DataFrame()

    start = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    # REDUNDANCY 1: Try reporting view (primary)
    try:
        resp = (
            client.from_(REPORTING_VIEW)
            .select("*")
            .gte("game_datetime_utc", start.isoformat())
            .execute()
        )
        data = pd.DataFrame(resp.data or [])
        if not data.empty:
            return data
    except Exception:
        pass

    # REDUNDANCY 2: Try public.predictions table
    try:
        resp = (
            client.table("predictions")
            .select("*")
            .gte("game_datetime_utc", start.isoformat())
            .execute()
        )
        data = pd.DataFrame(resp.data or [])
        if not data.empty:
            return data
    except Exception:
        pass

    # REDUNDANCY 3: Try raw.predictions_latest
    try:
        resp = client.schema("raw").table("predictions_latest").select("*").execute()
        data = pd.DataFrame(resp.data or [])
        if not data.empty:
            # Filter by date if column exists
            if "game_datetime_utc" in data.columns:
                data["game_datetime_utc"] = pd.to_datetime(data["game_datetime_utc"], errors="coerce")
                data = data[data["game_datetime_utc"] >= start]
            return data
    except Exception:
        pass

    # REDUNDANCY 4: CSV fallback
    if os.path.exists(FALLBACK_CSV):
        return pd.read_csv(FALLBACK_CSV)
    return pd.DataFrame()


def _first_present(df: pd.DataFrame, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _roi(series: pd.Series) -> float:
    """
    Simple -110 proxy:
      win = +0.91u
      loss = -1.00u
      push = 0.00u
    """
    s = series.dropna()
    if s.empty:
        return float("nan")
    wins = (s == 1.0).sum()
    losses = (s == 0.0).sum()
    pushes = (s == 0.5).sum()
    stake = wins + losses + pushes
    if stake == 0:
        return float("nan")
    pnl = wins * 0.91 - losses * 1.0
    return pnl / stake


st.title("Model Reports")
st.caption("Leaderboards for win%, ROI, and hit rate. Rolling charts are optional.")

preds = _load_predictions()
if preds.empty:
    st.warning("No predictions available.")
    st.stop()

# ===== Column normalization =====
model_col = _first_present(preds, ["model_version_id", "model_version"])
dt_col = _first_present(preds, ["game_datetime_utc", "start_time_utc", "closing_pulled_at"])
pred_col = _first_present(preds, ["pred_margin_home", "ensemble_prediction", "pred_spread"])
line_col = _first_present(preds, ["closing_spread_home", "market_spread", "spread_home"])
actual_col = _first_present(preds, ["actual_margin_home"])

# Ensure key fields exist
if model_col is None:
    preds["model_version"] = "unknown"
    model_col = "model_version"

if dt_col is None:
    # Allows leaderboard without time filters/rolling chart
    preds["game_datetime_utc"] = pd.NaT
    dt_col = "game_datetime_utc"

if pred_col is None:
    preds["pred_margin_home"] = np.nan
    pred_col = "pred_margin_home"

if line_col is None:
    preds["market_spread"] = np.nan
    line_col = "market_spread"

if actual_col is None:
    preds["actual_margin_home"] = np.nan
    actual_col = "actual_margin_home"

# Create normalized columns used by the report
preds["model_version_norm"] = preds[model_col].astype(str).fillna("unknown")
preds["game_datetime_utc_norm"] = pd.to_datetime(preds[dt_col], utc=True, errors="coerce")
preds["pred_spread_norm"] = pd.to_numeric(preds[pred_col], errors="coerce")
preds["market_spread_norm"] = pd.to_numeric(preds[line_col], errors="coerce")
preds["actual_margin_home_norm"] = pd.to_numeric(preds[actual_col], errors="coerce")

# Winner hit rate (direction only)
has_actuals = preds["actual_margin_home_norm"].notna()
preds["winner_hit"] = np.where(
    has_actuals,
    (preds["pred_spread_norm"] > 0) == (preds["actual_margin_home_norm"] > 0),
    np.nan,
)

# ===== Bet signal logic =====
# Prefer using the view’s ATS result if it exists, else compute from (actual, line).
ats_col = _first_present(preds, ["ats_result_closing_proxy", "ats_result", "ats_result_closing"])

# Default: bet whenever we have both a prediction and a line
preds["edge_spread_home"] = preds["pred_spread_norm"] - preds["market_spread_norm"]
preds["bet_signal"] = preds["edge_spread_home"].abs().ge(3.0)  # can be replaced with model-driven tiers later

# Side based on edge sign
preds["bet_side"] = np.where(preds["edge_spread_home"] >= 0, "home", "away")

def _bet_result(row) -> float:
    """
    1.0 win, 0.0 loss, 0.5 push, NaN if no bet or missing data.
    Uses ATS label from the view if present; otherwise derives from actual vs line.
    """
    if not bool(row.get("bet_signal")):
        return np.nan

    # Prefer ATS from view if available
    if ats_col is not None and isinstance(row.get(ats_col), str) and row.get(ats_col):
        ats = row.get(ats_col)
        if row.get("bet_side") == "home":
            return 1.0 if ats == "home_cover" else 0.0 if ats == "away_cover" else 0.5
        if row.get("bet_side") == "away":
            return 1.0 if ats == "away_cover" else 0.0 if ats == "home_cover" else 0.5
        return np.nan

    # Fallback: compute from actual and spread
    actual = row.get("actual_margin_home_norm")
    spread = row.get("market_spread_norm")
    if pd.isna(actual) or pd.isna(spread):
        return np.nan

    if row.get("bet_side") == "home":
        return 1.0 if actual > spread else 0.0 if actual < spread else 0.5
    if row.get("bet_side") == "away":
        return 1.0 if actual < spread else 0.0 if actual > spread else 0.5
    return np.nan

preds["bet_result"] = preds.apply(_bet_result, axis=1)

# ===== Summary / leaderboard =====
summary = (
    preds.groupby("model_version_norm", dropna=False)
    .agg(
        games=("pred_spread_norm", "count"),
        hit_rate=("winner_hit", "mean"),
        bets=("bet_signal", "sum"),
        roi=("bet_result", _roi),
        avg_edge=("edge_spread_home", "mean"),
        median_edge=("edge_spread_home", "median"),
    )
    .reset_index()
    .rename(columns={"model_version_norm": "model_version"})
    .sort_values(["roi", "hit_rate", "games"], ascending=[False, False, False])
)

st.subheader("Leaderboard")
st.caption(f"Source: {REPORTING_VIEW} (set MODEL_REPORT_VIEW env var to change).")
st.dataframe(summary, use_container_width=True)

with st.expander("Rolling hit rate (optional)", expanded=False):
    show = st.checkbox("Show rolling chart", value=False)
    window = st.number_input("Rolling window (games)", min_value=5, max_value=200, value=20, step=5)

    if show:
        df = preds.copy()
        df = df[df["game_datetime_utc_norm"].notna()].sort_values("game_datetime_utc_norm")
        if df.empty:
            st.info("No usable timestamps available for rolling chart.")
        else:
            # Rolling by timestamp order, not by day bucket
            series = (
                df.set_index("game_datetime_utc_norm")["winner_hit"]
                .rolling(int(window), min_periods=max(5, int(window) // 4))
                .mean()
                .dropna()
            )
            st.line_chart(series)

with st.expander("Raw sample (debug)", expanded=False):
    st.write("Columns detected:")
    st.json(
        {
            "model_col": model_col,
            "dt_col": dt_col,
            "pred_col": pred_col,
            "line_col": line_col,
            "actual_col": actual_col,
            "ats_col": ats_col,
            "reporting_view": REPORTING_VIEW,
        }
    )
    st.dataframe(preds.head(50), use_container_width=True)
