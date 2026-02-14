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


def _load_predictions() -> pd.DataFrame:
    client = _get_supabase_client()
    csv_paths = ["data/predictions.csv", "ml/predictions_latest.csv"]

    if client is None:
        for path in csv_paths:
            if os.path.exists(path):
                return pd.read_csv(path)
        return pd.DataFrame()

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    try:
        resp = (
            client.table("predictions")
            .select("*")
            .gte("game_datetime_utc", start.isoformat())
            .lt("game_datetime_utc", end.isoformat())
            .execute()
        )
        data = pd.DataFrame(resp.data or [])
        if not data.empty:
            return data
    except Exception:
        pass

    for path in csv_paths:
        if os.path.exists(path):
            return pd.read_csv(path)
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
    st.info("No predictions found. Run the pipeline to populate predictions.")

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
