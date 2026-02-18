"""Tests for the data_ok normalization logic used in PASS2 of the pipeline."""

import pandas as pd
import numpy as np


def _normalize_data_ok(df):
    """
    Replicate the data_ok normalization logic from the pipeline builders.
    This is the same logic used in espn_boxscore_builder.py and
    espn_boxscore_builder_modular.py at PASS2.
    """
    if "data_ok" not in df.columns:
        df["data_ok"] = True
    else:
        df["data_ok"] = (
            df["data_ok"]
            .astype(str)
            .str.strip()
            .str.lower()
            .map({"true": True, "false": False, "1": True, "0": False, "1.0": True, "0.0": False, "nan": False})
            .fillna(False)
        )
    return df


class TestDataOkNormalization:
    """Verify data_ok normalization handles all common types from CSV round-trip."""

    def test_boolean_true_kept(self):
        df = pd.DataFrame({"data_ok": [True, True, False]})
        df = _normalize_data_ok(df)
        assert df["data_ok"].tolist() == [True, True, False]

    def test_string_true_false(self):
        df = pd.DataFrame({"data_ok": ["True", "False", "True"]})
        df = _normalize_data_ok(df)
        assert df["data_ok"].tolist() == [True, False, True]

    def test_string_with_whitespace(self):
        df = pd.DataFrame({"data_ok": [" True ", " False"]})
        df = _normalize_data_ok(df)
        assert df["data_ok"].tolist() == [True, False]

    def test_integer_1_0(self):
        df = pd.DataFrame({"data_ok": [1, 0, 1]})
        df = _normalize_data_ok(df)
        assert df["data_ok"].tolist() == [True, False, True]

    def test_float_1_0(self):
        df = pd.DataFrame({"data_ok": [1.0, 0.0, 1.0]})
        df = _normalize_data_ok(df)
        assert df["data_ok"].tolist() == [True, False, True]

    def test_nan_defaults_to_false(self):
        df = pd.DataFrame({"data_ok": [True, np.nan, False]})
        df = _normalize_data_ok(df)
        assert df["data_ok"].tolist() == [True, False, False]

    def test_missing_column_defaults_to_true(self):
        df = pd.DataFrame({"event_id": [1, 2, 3]})
        df = _normalize_data_ok(df)
        assert df["data_ok"].tolist() == [True, True, True]

    def test_mixed_types(self):
        df = pd.DataFrame({"data_ok": [True, "False", 1, 0, np.nan, "true"]})
        df = _normalize_data_ok(df)
        assert df["data_ok"].tolist() == [True, False, True, False, False, True]

    def test_csv_round_trip_preserves_values(self):
        """Simulate what happens when data_ok goes through CSV write+read."""
        import io
        original = pd.DataFrame({"data_ok": [True, False, True], "x": [1, 2, 3]})
        csv_buf = io.StringIO()
        original.to_csv(csv_buf, index=False)
        csv_buf.seek(0)
        reloaded = pd.read_csv(csv_buf)
        # After CSV round-trip, booleans become strings
        reloaded = _normalize_data_ok(reloaded)
        assert reloaded["data_ok"].tolist() == [True, False, True]

    def test_all_false_produces_empty_clean(self):
        df = pd.DataFrame({"data_ok": [False, False, False], "x": [1, 2, 3]})
        df = _normalize_data_ok(df)
        df_clean = df[df["data_ok"]].copy()
        assert len(df_clean) == 0

    def test_all_string_false_produces_empty_clean(self):
        df = pd.DataFrame({"data_ok": ["False", "False"], "x": [1, 2]})
        df = _normalize_data_ok(df)
        df_clean = df[df["data_ok"]].copy()
        assert len(df_clean) == 0

    def test_all_string_true_produces_full_clean(self):
        df = pd.DataFrame({"data_ok": ["True", "True"], "x": [1, 2]})
        df = _normalize_data_ok(df)
        df_clean = df[df["data_ok"]].copy()
        assert len(df_clean) == 2
