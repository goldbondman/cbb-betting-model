import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from core.data_loader import DataLoader
from core.supabase_utils import get_public_supabase_client


REPO_ROOT = Path(__file__).resolve().parents[1]
ESPN_DIR = REPO_ROOT / "ESPN"
if str(ESPN_DIR) not in sys.path:
    sys.path.insert(0, str(ESPN_DIR))

try:
    import espn_boxscore_builder_modular as espn
except Exception:
    import espn_boxscore_builder as espn


st.set_page_config(page_title="Daily Dashboard", page_icon="📅", layout="wide")
logger = logging.getLogger(__name__)


def _get_supabase_client():
    return get_public_supabase_client()


def _normalize_prediction_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names to handle variations between tables."""
    if df.empty:
        return df
    
    # Column aliases - map various names to standard names
    column_mappings = {
        # Margin/spread predictions
        "pred_spread": "pred_margin_home",
        "ensemble_prediction": "pred_margin_home",
        "predicted_spread": "pred_margin_home",
        # Total predictions
        "predicted_total": "pred_total",
        # Team names
        "team_a": "home_team",
        "team_b": "away_team",
        "team_home": "home_team",
        "team_away": "away_team",
        # Game identifiers
        "game_id": "event_id",
    }
    
    # Apply mappings only if source column exists and target doesn't
    for source_col, target_col in column_mappings.items():
        if source_col in df.columns and target_col not in df.columns:
            df[target_col] = df[source_col]
    
    return df


def _load_predictions() -> pd.DataFrame:
    """Load predictions with multiple fallback mechanisms for redundancy."""
    client = _get_supabase_client()
    csv_paths = ["data/predictions.csv", "ml/predictions_latest.csv"]

    if client is None:
        logger.info("Supabase client unavailable; trying CSV fallback")
        for path in csv_paths:
            if os.path.exists(path):
                logger.info("Loaded predictions from CSV: %s", path)
                return _normalize_prediction_columns(pd.read_csv(path))
        logger.warning("No CSV predictions found")
        return pd.DataFrame()

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    
    # REDUNDANCY 1: Try public.predictions (primary table)
    try:
        logger.info("Attempting to load predictions from public.predictions")
        resp = (
            client.table("predictions")
            .select("*")
            .gte("game_datetime_utc", start.isoformat())
            .lt("game_datetime_utc", end.isoformat())
            .execute()
        )
        data = pd.DataFrame(resp.data or [])
        if not data.empty:
            logger.info("✓ Loaded %d predictions from public.predictions", len(data))
            return _normalize_prediction_columns(data)
        logger.info("No predictions in public.predictions for today's date range")
    except Exception as exc:
        logger.warning("Failed to query public.predictions: %s", exc)

    # REDUNDANCY 2: Try raw.predictions_latest (source table)
    try:
        logger.info("Attempting fallback to raw.predictions_latest")
        resp = client.schema("raw").table("predictions_latest").select("*").execute()
        data = pd.DataFrame(resp.data or [])
        if not data.empty:
            # Filter to today's games if possible
            if "game_datetime_utc" in data.columns:
                data["game_datetime_utc"] = pd.to_datetime(data["game_datetime_utc"], errors="coerce")
                mask = (data["game_datetime_utc"] >= start) & (data["game_datetime_utc"] < end)
                data = data[mask]
            
            if not data.empty:
                logger.info("✓ Loaded %d predictions from raw.predictions_latest", len(data))
                return _normalize_prediction_columns(data)
            logger.info("No predictions in raw.predictions_latest for today")
    except Exception as exc:
        logger.warning("Failed to query raw.predictions_latest: %s", exc)

    # REDUNDANCY 3: Try unfiltered public.predictions (all dates)
    try:
        logger.info("Attempting to load all predictions from public.predictions (no date filter)")
        resp = client.table("predictions").select("*").limit(1000).execute()
        data = pd.DataFrame(resp.data or [])
        if not data.empty:
            logger.info("✓ Loaded %d predictions from public.predictions (all dates)", len(data))
            # Try to filter to recent games
            if "game_datetime_utc" in data.columns:
                data["game_datetime_utc"] = pd.to_datetime(data["game_datetime_utc"], errors="coerce")
                # Get last 7 days
                week_ago = start - timedelta(days=7)
                mask = data["game_datetime_utc"] >= week_ago
                recent_data = data[mask]
                if not recent_data.empty:
                    logger.info("Filtered to %d predictions from last 7 days", len(recent_data))
                    return _normalize_prediction_columns(recent_data)
            return _normalize_prediction_columns(data)
        logger.info("No predictions found in public.predictions (unfiltered)")
    except Exception as exc:
        logger.warning("Failed to query public.predictions (unfiltered): %s", exc)

    # REDUNDANCY 4: CSV fallback
    logger.info("Trying CSV fallback as last resort")
    for path in csv_paths:
        if os.path.exists(path):
            logger.info("✓ Loaded predictions from CSV: %s", path)
            return _normalize_prediction_columns(pd.read_csv(path))
    
    logger.warning("⚠ No predictions found in any source (Supabase or CSV)")
    return pd.DataFrame()


def _load_scoreboard() -> pd.DataFrame:
    today = datetime.now().strftime("%Y%m%d")
    rows = []
    try:
        if hasattr(espn, "fetch_scoreboard_games"):
            rows = espn.fetch_scoreboard_games(today)
        elif hasattr(espn, "fetch_scoreboard_games_for_date"):
            rows = espn.fetch_scoreboard_games_for_date(today)
    except Exception as exc:
        logger.warning("Scoreboard fetch failed; using CSV fallback: %s", exc)
        rows = []
    df = pd.DataFrame(rows)
    if not df.empty:
        return df
    return DataLoader().load_vegas_lines(date="today")


st.title("Daily Dashboard")
st.caption("Today's games with model edges and bet recommendations.")

preds = _load_predictions()
scoreboard = _load_scoreboard()

if scoreboard.empty:
    st.warning("No ESPN scoreboard data returned.")
    st.stop()

if preds.empty:
    st.warning("⚠️ No predictions found")
    
    # Provide diagnostic information
    with st.expander("🔍 Troubleshooting Information", expanded=True):
        st.markdown("""
        **Why are predictions missing?**
        
        Predictions may not be available because:
        """)
        
        # Show Supabase connection status
        client = _get_supabase_client()
        if client:
            st.success("✅ Supabase is connected - The connection is working")
            
            # Check if raw.predictions_latest has any data
            try:
                resp = client.schema("raw").table("predictions_latest").select("*").limit(1).execute()
                if resp.data and len(resp.data) > 0:
                    st.info("ℹ️ Raw predictions table has data, but none match today's games")
                else:
                    st.error("❌ No prediction data found - Predictions may not have been generated yet")
            except Exception as e:
                st.warning(f"⚠️ Could not check raw predictions table: {e}")
        else:
            st.error("✗ Supabase client not available - check credentials")
        
        st.markdown("""
        **Possible causes:**
        - The daily prediction pipeline hasn't run yet (scheduled for 9 AM UTC, then 3 PM UTC)
        - There are no games scheduled for today
        - The ML pipeline failed or is still running
        - Predictions exist in `raw.predictions_latest` but haven't been synced to `public.predictions`
        
        **What you can do:**
        1. **Check GitHub Actions workflows**:
           - [Run ML Pipeline](https://github.com/goldbondman/cbb-betting-model/actions/workflows/run-ml-pipeline.yml) (should run at 9 AM UTC)
           - [Daily Auto Predict](https://github.com/goldbondman/cbb-betting-model/actions/workflows/daily_auto_predict.yml) (should run at 3 PM UTC)
        2. **Manual trigger**: You can manually trigger these workflows from the Actions tab
        3. **Run diagnostics**: Use `python scripts/diagnose_predictions.py` to get detailed information
        
        **Technical Details:**
        - Checked: `public.predictions` ✓
        - Checked: `raw.predictions_latest` ✓
        - Checked: CSV files (`data/predictions.csv`, `ml/predictions_latest.csv`) ✓
        - All sources returned empty results for today's date range
        """)
    
    st.stop()

if "pred_margin_home" not in preds.columns and "pred_spread" in preds.columns:
    preds["pred_margin_home"] = preds["pred_spread"]

scoreboard["game_id"] = scoreboard["game_id"].astype(str)
if "event_id" in preds.columns:
    preds["event_id"] = preds["event_id"].astype(str)
    merged = preds.merge(scoreboard, left_on="event_id", right_on="game_id", how="left", suffixes=("", "_game"))
elif "external_game_id" in preds.columns:
    preds["external_game_id"] = preds["external_game_id"].astype(str)
    merged = preds.merge(scoreboard, left_on="external_game_id", right_on="game_id", how="left", suffixes=("", "_game"))
else:
    merged = scoreboard.copy()

if "edge_spread" not in merged.columns:
    if "pred_margin_home" in merged.columns:
        merged["edge_spread"] = merged["pred_margin_home"] - merged.get("market_spread")
    else:
        merged["edge_spread"] = pd.NA

edge_min = st.slider("Minimum edge", 0.0, 15.0, 3.0, 0.5)

display_cols = [
    "game_datetime_utc",
    "home_team",
    "away_team",
    "market_spread",
    "pred_margin_home",
    "edge_spread",
    "bet_side",
    "bet_units",
]

table = merged.copy()
table = table[table["edge_spread"].abs() >= edge_min] if "edge_spread" in table else table
table = table.sort_values("edge_spread", ascending=False, key=lambda s: s.abs())
table = table[[c for c in display_cols if c in table.columns]]

st.dataframe(table, use_container_width=True)
