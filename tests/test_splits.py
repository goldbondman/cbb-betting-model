import unittest
import pandas as pd

from ml.splits import SplitConfig, time_series_split


class TestSplits(unittest.TestCase):
    def test_time_series_split_order(self):
        df = pd.DataFrame(
            {
                "game_datetime_utc": pd.date_range("2024-01-01", periods=10, freq="D"),
                "value": range(10),
            }
        )
        cfg = SplitConfig(val_ratio=0.2, test_ratio=0.2)
        train, val, test = time_series_split(df, cfg)
        self.assertTrue(train["game_datetime_utc"].max() < val["game_datetime_utc"].min())
        self.assertTrue(val["game_datetime_utc"].max() < test["game_datetime_utc"].min())


if __name__ == "__main__":
    unittest.main()
