import os
import sys


_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

import espn_config


def test_out_team_logs_path_points_to_repo_espn_csv_dir():
    expected = os.path.join(_ESPN_DIR, "CSV", "espn_team_game_logs.csv")
    assert os.path.abspath(espn_config.OUT_TEAM_LOGS) == os.path.abspath(expected)
