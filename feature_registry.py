#!/usr/bin/env python3
"""
feature_registry.py

Single source of truth for:
- which metrics exist at team-game level
- which metrics we roll (means, volatility)
- naming conventions for derived rolling columns

Keep this file declarative (constants only). No heavy logic.
"""

from __future__ import annotations

# ----------------------------
# Identity + ordering columns
# ----------------------------

KEY_COLS = ["event_id", "team_id"]
ORDER_COL = "game_datetime_utc"

TEAM_COL = "team"
OPP_TEAM_COL = "opponent"

HOME_AWAY_COL = "home_away"   # "home" | "away" (neutral can be added later)
COMPLETED_COL = "completed"
DATA_OK_COL = "data_ok"

WEIGHT_COL = "w_g"            # produced by weights.py


# ----------------------------
# Team-game base boxscore totals expected to exist
# ----------------------------
# (These are the raw-ish totals your pipeline already parses or derives.)
TEAM_GAME_TOTALS = [
    "points_for",
    "points_against",
    "margin",
    "fga",
    "fgm",
    "tpa",
    "tpm",
    "fta",
    "ftm",
    "tov",
    "orb",
    "drb",
    "reb",
    "poss",
    "pace",  # alias of poss in your pipeline
]


# ----------------------------
# Team-game rate/process metrics expected to exist
# ----------------------------
# NOTE: Some of these may require a small add in parsing:
# - 3p_pct and ft_pct should be derived from makes/attempts if you want them.
TEAM_GAME_RATES = [
    # Four factors style
    "efg",
    "tov_pct",   # your pipeline currently uses TOV / poss (works, just be consistent)
    "orb_pct",
    "drb_pct",
    "ftr",
    "3par",

    # Shooting splits (add if you want, but included here for registry completeness)
    "3p_pct",    # tpm / tpa
    "ft_pct",    # ftm / fta
]


# ----------------------------
# Outcome/efficiency metrics expected to exist
# ----------------------------
TEAM_GAME_EFFICIENCY = [
    "ortg",
    "drtg",
    "netrtg",
]


# ----------------------------
# Flags / context columns (optional but useful)
# ----------------------------
CONTEXT_FLAGS = [
    "blowout",       # abs(margin) >= threshold
    "noise_flag",    # OT / extreme pace / foul variance proxy, etc. (you control definition)
    "is_ot",         # optional if you add it explicitly
    "num_ot",        # optional if you add it explicitly
]


# ----------------------------
# Opponent-merged (per game) columns created in _merge_opponent_rows
# ----------------------------
# These are "opponent in that game" values (not pregame expectations).
OPPONENT_GAME_COLS = [
    "opp_team",
    "opp_team_id",
    "efg_allowed_game",
    "ftr_allowed_game",
    "tov_forced_game",
    "opp_join_ok",
]


# ----------------------------
# Rolling feature targets (unweighted rolling pack already produces l3/l7/season_pre)
# ----------------------------
# These are the *base* metrics you consider core for rollups.
CORE_GAME_METRICS = (
    TEAM_GAME_EFFICIENCY
    + [
        "pace",
        "efg",
        "tov_pct",
        "orb_pct",
        "drb_pct",
        "ftr",
        "3par",
        # include if you add them
        "3p_pct",
        "ft_pct",
    ]
)

# Common roll windows you already use (and last10 for weighted module)
ROLL_WINDOWS = {
    "l3": 3,
    "l7": 7,
    "last10": 10,
}


# ----------------------------
# Weighted last-10 module targets
# ----------------------------
# Mean targets: what gets weighted mean last10
ROLL_MEAN_TARGETS = [
    # efficiency
    "ortg",
    "drtg",
    "netrtg",

    # process
    "pace",
    "efg",
    "tov_pct",
    "orb_pct",
    "drb_pct",
    "ftr",
    "3par",

    # shooting splits (only if computed)
    "3p_pct",
    "ft_pct",

    # outcome
    "margin",
]

# Volatility targets: what gets weighted std last10
ROLL_VOL_TARGETS = [
    # performance volatility
    "netrtg",
    "ortg",
    "drtg",

    # process volatility
    "efg",
    "tov_pct",
    "3par",
    "orb_pct",
    "ftr",

    # 3PT variance (only if computed)
    "3p_pct",
]


# ----------------------------
# Naming conventions for rolling outputs
# ----------------------------

# Weighted last10
WMEAN_SUFFIX = "wmean10_pre"   # leak-free pregame (shifted)
WSTD_SUFFIX = "wstd10_pre"
WSHIFT_SUFFIX = "shift10_pre"  # last5 - prev5 (optional, future)

# Unweighted last10 (if you add later)
MEAN_SUFFIX = "mean10_pre"
STD_SUFFIX = "std10_pre"
IQR_SUFFIX = "iqr10_pre"
RANGE_SUFFIX = "range10_pre"
SLOPE_SUFFIX = "slope10_pre"


# ----------------------------
# Expected “pregame baseline expectation” columns (future modules)
# ----------------------------
# These do NOT exist yet in your builder, but are in your spec.
EXPECTATION_COLS = [
    "expected_margin_pre",
    "residual_margin",
    "gps_points",
    "gps_cap",
    "gps_z",
    "grade_0_100",
]


# ----------------------------
# “Plus” / opponent-adjusted metric names (future)
# ----------------------------
PLUS_METRICS = [
    "efg_plus",
    "tov_plus",
    "orb_plus",
    "ftr_plus",
    "ppp_plus",
]


# ----------------------------
# Fit metrics (prediction-time, future)
# ----------------------------
FIT_METRICS = [
    "shooting_fit",
    "turnover_fit",
    "glass_fit",
    "ftr_fit",
    "fit3",
]


# ----------------------------
# Composite indices (future)
# ----------------------------
COMPOSITES = [
    "pwr",
    "pwr_plus",
    "epi",
    "triangle",
    "triangle_plus",
    "moi",
    "rim_proxy",
    "disrupt",
    "nep",
    "deftri",
]


# ----------------------------
# Player-based team-game aggregates (future)
# ----------------------------
PLAYER_TEAM_GAME_METRICS = [
    "usage_hhi",
    "top1_share",
    "top2_share",
    "top3_share",
    "creator_share",
    "bench_share",
    "rotation_entropy",
    "minutes_hhi",
    "rotation_std",
    "threepa_hhi",
    "threepa_top2_share",
    "team_shooter_heat",
    "key_minutes",
    "key_minutes_delta",
    "player_vol_risk",
]


# ----------------------------
# Minimal “must-have” columns for weighted rolling to run safely
# ----------------------------
MIN_COLS_FOR_WEIGHTED_LAST10 = KEY_COLS + [ORDER_COL, WEIGHT_COL]

# Helpful list for integrity/audit checks
ALL_DECLARED_METRICS = sorted(
    set(
        TEAM_GAME_TOTALS
        + TEAM_GAME_RATES
        + TEAM_GAME_EFFICIENCY
        + CONTEXT_FLAGS
        + OPPONENT_GAME_COLS
        + EXPECTATION_COLS
        + PLUS_METRICS
        + FIT_METRICS
        + COMPOSITES
        + PLAYER_TEAM_GAME_METRICS
    )
)
