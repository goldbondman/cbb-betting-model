import os
import sys

_SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from recover_espn_csvs_from_storage import _needs_recovery, RECOVERY_MAP


class TestNeedsRecovery:
    def test_missing_file_needs_recovery(self, tmp_path):
        assert _needs_recovery(str(tmp_path / "nofile.csv")) is True

    def test_empty_file_needs_recovery(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("")
        assert _needs_recovery(str(p)) is True

    def test_valid_file_does_not_need_recovery(self, tmp_path):
        p = tmp_path / "good.csv"
        p.write_text("header\n1\n")
        assert _needs_recovery(str(p)) is False


class TestRecoveryMapCoversExpectedFiles:
    def test_critical_files_in_recovery_map(self):
        critical = [
            "espn_games.csv",
            "espn_teams.csv",
            "espn_team_game_logs.csv",
            "espn_team_game_features.csv",
            "espn_matchups_model_ready.csv",
        ]
        for f in critical:
            assert f in RECOVERY_MAP, f"{f} missing from RECOVERY_MAP"
