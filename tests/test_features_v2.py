import unittest
import pandas as pd

from ml.features_v2 import add_features_v2


class TestFeaturesV2(unittest.TestCase):
    def test_feature_calcs(self):
        df = pd.DataFrame(
            {
                "pace_l7_pre_home": [70.0],
                "pace_l7_pre_away": [68.0],
                "efg_l7_pre_home": [0.52],
                "efg_l7_pre_away": [0.48],
                "tov_pct_l7_pre_home": [0.18],
                "tov_pct_l7_pre_away": [0.20],
                "orb_pct_l7_pre_home": [0.30],
                "orb_pct_l7_pre_away": [0.28],
                "drb_pct_l7_pre_home": [0.70],
                "drb_pct_l7_pre_away": [0.72],
                "ftr_l7_pre_home": [0.28],
                "ftr_l7_pre_away": [0.24],
                "3par_l7_pre_home": [0.40],
                "3par_l7_pre_away": [0.35],
                "netrtg_l7_pre_home": [10.0],
                "netrtg_l7_pre_away": [5.0],
                "games_last_3_days_home": [1.0],
                "games_last_3_days_away": [2.0],
                "games_last_7_days_home": [2.0],
                "games_last_7_days_away": [3.0],
                "style_distance_l7_home": [1.2],
                "style_distance_l7_away": [0.8],
            }
        )
        out = add_features_v2(df)
        self.assertAlmostEqual(out.loc[0, "tempo_gap_l7"], 2.0)
        self.assertAlmostEqual(out.loc[0, "shot_quality_gap_l7"], 0.04)
        self.assertAlmostEqual(out.loc[0, "turnover_gap_l7"], 0.02)
        self.assertAlmostEqual(out.loc[0, "ftr_gap_l7"], 0.04)
        self.assertAlmostEqual(out.loc[0, "three_rate_gap_l7"], 0.05)
        self.assertAlmostEqual(out.loc[0, "netrtg_gap_l7"], 5.0)
        self.assertAlmostEqual(out.loc[0, "rest_gap_3d"], 1.0)
        self.assertAlmostEqual(out.loc[0, "rest_gap_7d"], 1.0)
        self.assertAlmostEqual(out.loc[0, "style_distance_l7"], 1.0)


if __name__ == "__main__":
    unittest.main()
