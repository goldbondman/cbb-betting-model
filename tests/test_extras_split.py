"""Tests for the core/extras CSV split logic in espn_config."""

import os
import sys

_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from espn_config import (
    OUT_TEAM_EXTRAS,
    is_extras_column,
    EXTRAS_COLUMN_EXACT,
    EXTRAS_COLUMN_PREFIXES,
)


class TestExtrasColumnClassification:
    """Verify is_extras_column correctly routes columns."""

    # -- Extras columns (should return True) --

    def test_weight_columns_are_extras(self):
        for col in ["w_recency", "w_opp_quality", "w_location", "w_noise", "w_g", "w_g_prime"]:
            assert is_extras_column(col), f"{col} should be extras"

    def test_plus_metrics_are_extras(self):
        for col in ["efg_plus", "tov_plus", "orb_plus", "ftr_plus", "ppp_plus"]:
            assert is_extras_column(col), f"{col} should be extras"

    def test_composite_ratings_are_extras(self):
        for col in ["pwr", "pwr_plus", "pwr_raw", "triangle", "triangle_plus", "moi", "rim_proxy"]:
            assert is_extras_column(col), f"{col} should be extras"

    def test_vs_expectation_scores_are_extras(self):
        for col in ["gps", "off_delta", "def_delta", "net_over_exp"]:
            assert is_extras_column(col), f"{col} should be extras"

    def test_edge_metrics_are_extras(self):
        for col in ["efg_edge_pre", "ftr_edge_pre", "orb_edge_pre", "tov_edge_pre", "def_ppp_edge_pre"]:
            assert is_extras_column(col), f"{col} should be extras"

    def test_rf10_rolling_are_extras(self):
        for col in ["rf10_netrtg_mean", "rf10_ortg_std", "rf10_efg_pct"]:
            assert is_extras_column(col), f"{col} should be extras"

    def test_game_level_allowed_forced_are_extras(self):
        for col in ["efg_allowed_game", "ftr_allowed_game", "tov_forced_game", "def_ppp_allowed_game"]:
            assert is_extras_column(col), f"{col} should be extras"

    def test_epi_columns_are_extras(self):
        for col in ["epi_per_game", "epi_per_100"]:
            assert is_extras_column(col), f"{col} should be extras"

    # -- Core columns (should return False) --

    def test_identifiers_are_core(self):
        for col in ["event_id", "team_id", "team", "home_away", "game_datetime_utc"]:
            assert not is_extras_column(col), f"{col} should be core"

    def test_rolling_features_are_core(self):
        for col in ["ortg_l3_pre", "ortg_l7_pre", "drtg_season_pre", "ha_efg_l7_pre"]:
            assert not is_extras_column(col), f"{col} should be core"

    def test_opponent_rolling_are_core(self):
        for col in ["opp_ortg_l7_pre", "opp_efg_allowed_l7_pre"]:
            assert not is_extras_column(col), f"{col} should be core"

    def test_rest_schedule_are_core(self):
        for col in ["days_rest", "back_to_back", "games_last_7_days"]:
            assert not is_extras_column(col), f"{col} should be core"

    def test_differential_features_are_core(self):
        for col in ["oreb_diff", "efg_diff", "ts_diff", "to_diff"]:
            assert not is_extras_column(col), f"{col} should be core"

    def test_exp_margin_and_style_distance_are_core(self):
        for col in ["exp_margin", "style_distance_l7", "pace_mismatch_l7"]:
            assert not is_extras_column(col), f"{col} should be core"

    def test_defensive_rolling_pre_are_core(self):
        for col in ["efg_allowed_l3_pre", "efg_allowed_l7_pre", "ha_tov_forced_l7_pre"]:
            assert not is_extras_column(col), f"{col} should be core"


def test_out_team_extras_path():
    """OUT_TEAM_EXTRAS should point to ESPN/CSV/espn_team_game_extras.csv."""
    expected = os.path.join(_ESPN_DIR, "CSV", "espn_team_game_extras.csv")
    assert os.path.abspath(OUT_TEAM_EXTRAS) == os.path.abspath(expected)
