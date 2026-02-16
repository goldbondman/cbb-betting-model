import csv
import os
import sys
from pathlib import Path

_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from ncaa_casablanca_config import CSV_SCHEMAS


def test_ncaa_team_game_logs_csv_exists_with_expected_header():
    csv_path = Path(_ESPN_DIR) / "CSV" / "ncaa_team_game_logs.csv"
    assert csv_path.exists()

    with csv_path.open(newline="") as f:
        reader = csv.reader(f)
        header = next(reader)

    assert header == CSV_SCHEMAS["team_logs"]
