import unittest

import pandas as pd

from ml.feature_matrix import _derive_features_if_needed


class TestFeatureMatrixDerivation(unittest.TestCase):
    def test_derivation_falls_back_to_team_name_when_team_ids_unstable(self):
        rows = []
        games = [
            ("g1", "2026-01-01T00:00:00Z", 80, 70),
            ("g2", "2026-01-03T00:00:00Z", 75, 65),
            ("g3", "2026-01-05T00:00:00Z", 78, 72),
        ]
        for game_idx, (event_id, game_dt, home_pts, away_pts) in enumerate(games, start=1):
            rows.append(
                {
                    "event_id": event_id,
                    "team_id": f"H-{game_idx}",
                    "team": "HomeTeam",
                    "home_away": "home",
                    "game_datetime_utc": game_dt,
                    "points_for": home_pts,
                    "points_against": away_pts,
                    "fgm": 30,
                    "fga": 60,
                    "tpm": 8,
                    "tpa": 20,
                    "fta": 18,
                    "tov": 12,
                    "orb": 10,
                    "drb": 25,
                }
            )
            rows.append(
                {
                    "event_id": event_id,
                    "team_id": f"A-{game_idx}",
                    "team": "AwayTeam",
                    "home_away": "away",
                    "game_datetime_utc": game_dt,
                    "points_for": away_pts,
                    "points_against": home_pts,
                    "fgm": 26,
                    "fga": 58,
                    "tpm": 7,
                    "tpa": 19,
                    "fta": 16,
                    "tov": 13,
                    "orb": 9,
                    "drb": 24,
                }
            )

        out = _derive_features_if_needed(pd.DataFrame(rows))
        self.assertGreater(out["ortg_l3_pre"].notna().sum(), 0)
        self.assertGreater(out["drtg_l3_pre"].notna().sum(), 0)


if __name__ == "__main__":
    unittest.main()
