import os
import sys


_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

import espn_boxscore_builder


def test_player_boxscore_path_includes_csv_subdir():
    assert espn_boxscore_builder.OUT_PLAYER_BOX == "CSV/espn_player_boxscores.csv"
