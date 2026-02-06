import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import streamlit as st
from supabase import create_client


st.set_page_config(page_title="Model Reports", page_icon="📊", layout="wide")


def _get_supabase_client():
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_ANON_KEY") or "").strip()
    if not url or not key:
        return None
    return create_client(url, key)


def _load_predictions() -> pd.DataFrame:
    client = _get_supabase_client()
    if client is None:
        path = "ml/predictions_latest.csv"
        if os.path.exists(path):
            return pd.read_csv(path)
        return pd.DataFrame()

    start = datetime.now(timezone.utc) - timedelta(days=30)
    try:
        resp = (
            client.table("predictions")
            .select("*")
            .gte("game_datetime_utc", start.isoformat())
            .execute()
        )
        return pd.DataFrame(resp.data or [])
    except Exception:
        path = "ml/predictions_latest.csv"
        if os.path.exists(path):
            return pd.read_csv(path)
        return pd.DataFrame()


st.title("Model Reports")
st.caption("Leaderboards for win%, ROI, and hit rate. Rolling charts are optional.")

preds = _load_predictions()
if preds.empty:
    st.warning("No predictions available.")
    st.stop()

preds["model_version"] = preds.get("model_version", "unknown")
preds["pred_spread"] = preds.get("pred_spread", preds.get("pred_margin_home"))
preds["actual_margin_home"] = preds.get("actual_margin_home")
preds["market_spread"] = preds.get("market_spread")

has_actuals = preds["actual_margin_home"].notna()
preds["winner_hit"] = np.where(
    has_actuals,
    (preds["pred_spread"] > 0) == (preds["actual_margin_home"] > 0),
    np.nan,
)

preds["bet_signal"] = preds.get("bet_signal", False)
preds["bet_side"] = preds.get("bet_side")

def _bet_result(row):
    if not row.get("bet_signal"):
        return np.nan
    actual = row.get("actual_margin_home")
    spread = row.get("market_spread")
    if pd.isna(actual) or pd.isna(spread):
        return np.nan
    if row.get("bet_side") == "home":
        return 1.0 if actual > spread else 0.0 if actual < spread else 0.5
    if row.get("bet_side") == "away":
        return 1.0 if actual < spread else 0.0 if actual > spread else 0.5
    return np.nan


preds["bet_result"] = preds.apply(_bet_result, axis=1)

def _roi(series: pd.Series) -> float:
    if series.dropna().empty:
        return float("nan")
    wins = (series == 1.0).sum()
    losses = (series == 0.0).sum()
    pushes = (series == 0.5).sum()
    stake = wins + losses + pushes
    if stake == 0:
        return float("nan")
    pnl = wins * 0.91 - losses * 1.0
    return pnl / stake


summary = (
    preds.groupby("model_version", dropna=False)
    .agg(
        games=("pred_spread", "count"),
        hit_rate=("winner_hit", "mean"),
        bets=("bet_signal", "sum"),
        roi=("bet_result", _roi),
    )
    .reset_index()
)

st.subheader("Leaderboard")
st.dataframe(summary, use_container_width=True)

with st.expander("Rolling hit rate (optional)", expanded=False):
    show = st.checkbox("Show rolling chart", value=False)
    if show:
        if "game_datetime_utc" in preds.columns:
            preds["game_datetime_utc"] = pd.to_datetime(preds["game_datetime_utc"], utc=True, errors="coerce")
            series = (
                preds.sort_values("game_datetime_utc")
                .set_index("game_datetime_utc")["winner_hit"]
                .rolling(20, min_periods=5)
                .mean()
                .dropna()
            )
            st.line_chart(series)
        else:
            st.info("No game_datetime_utc column available for rolling chart.")
