import sys
from pathlib import Path


_ESPN_DIR = Path(__file__).resolve().parents[1] / "ESPN"
if str(_ESPN_DIR) not in sys.path:
    sys.path.insert(0, str(_ESPN_DIR))

import ncaa_casablanca_config


def test_ncaa_team_game_logs_is_header_only_schema_artifact():
    csv_path = _ESPN_DIR / "CSV" / "ncaa_team_game_logs.csv"
    assert csv_path.exists()

    lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert lines[0].split(",") == ncaa_casablanca_config.CSV_SCHEMAS["team_logs"]
