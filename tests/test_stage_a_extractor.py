"""Confirmatory RT-0022 tests for CBB-STAGEA-EXTRACTOR-v1.0.

The acceptance criteria for these tests were preregistered in CBB-EXP-0004
before implementation results were examined. Do not weaken/remove failures to
make the extractor pass; fix implementation while preserving the criteria.
"""

import os
import sys

import pandas as pd
import pytest


_ESPN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ESPN"
)
if _ESPN_DIR not in sys.path:
    sys.path.insert(0, _ESPN_DIR)

from stage_a_extractor import (  # noqa: E402
    CONTRACT_VERSION,
    StageADataBlocked,
    StageAMarketContamination,
    StageAModelBlocked,
    extract_stage_a_snapshot,
)


PREDICTORS = ("h_ortg_l7_pre", "a_ortg_l7_pre")
ELIGIBILITY = ("h_data_ok", "a_data_ok", "h_opp_join_ok", "a_opp_join_ok")


def _lineage(**overrides):
    base = {
        "source_id": "CBB-DATA-009",
        "source_version": "espn-v1.4.0",
        "source_snapshot_id": "fixture-snapshot-001",
        "feature_contract_version": CONTRACT_VERSION,
        "model_version": "NCAAB-R1R2-FROZEN-2026-10-04",
        "forecast_at": "2026-01-15T18:00:00Z",
        "data_cutoff": "2026-01-15T17:55:00Z",
    }
    base.update(overrides)
    return base


def _mixed_fixture():
    return pd.DataFrame(
        [
            {
                "event_id": "200",
                "h_ortg_l7_pre": 111.5,
                "a_ortg_l7_pre": 108.2,
                "h_data_ok": True,
                "a_data_ok": True,
                "h_opp_join_ok": True,
                "a_opp_join_ok": True,
                "home_points": 76,
                "away_points": 69,
                "home_win": 1,
                "h_blowout": 0,
                "a_blowout": 0,
                "mystery_numeric": 999.0,
                "status": "final",
                "source": "espn",
            },
            {
                "event_id": "100",
                "h_ortg_l7_pre": 105.0,
                "a_ortg_l7_pre": 103.4,
                "h_data_ok": True,
                "a_data_ok": True,
                "h_opp_join_ok": True,
                "a_opp_join_ok": True,
                "home_points": 61,
                "away_points": 60,
                "home_win": 1,
                "h_blowout": 0,
                "a_blowout": 0,
                "mystery_numeric": 123.0,
                "status": "final",
                "source": "espn",
            },
        ]
    )


def _extract(df=None, predictors=PREDICTORS, eligibility=ELIGIBILITY, lineage=None):
    return extract_stage_a_snapshot(
        _mixed_fixture() if df is None else df,
        predictors,
        eligibility_columns=eligibility,
        lineage=_lineage() if lineage is None else lineage,
    )


def test_explicit_allowlist_ignores_unrequested_outcomes_blowout_and_numeric_extras():
    snapshot = _extract()
    assert snapshot.predictor_columns == PREDICTORS
    assert list(snapshot.predictors.columns) == ["event_id", *PREDICTORS]
    assert snapshot.predictors["event_id"].tolist() == ["100", "200"]
    assert "home_points" not in snapshot.predictors.columns
    assert "h_blowout" not in snapshot.predictors.columns
    assert "mystery_numeric" not in snapshot.predictors.columns


def test_raw_current_game_blowout_request_is_model_blocked():
    with pytest.raises(StageAModelBlocked) as exc:
        _extract(predictors=("h_ortg_l7_pre", "h_blowout"))
    assert exc.value.decision_state == "MODEL_BLOCKED"


def test_realized_outcome_request_is_model_blocked():
    with pytest.raises(StageAModelBlocked) as exc:
        _extract(predictors=("h_ortg_l7_pre", "home_points"))
    assert exc.value.decision_state == "MODEL_BLOCKED"


def test_identifier_request_is_model_blocked():
    with pytest.raises(StageAModelBlocked):
        _extract(predictors=("event_id", "h_ortg_l7_pre"))


def test_market_like_requested_predictor_is_market_contamination_even_if_absent():
    with pytest.raises(StageAMarketContamination) as exc:
        _extract(predictors=("h_ortg_l7_pre", "market_home_ml"))
    assert exc.value.reason_code == "MARKET_CONTAMINATION"


def test_market_like_column_anywhere_in_source_invalidates_stage_a():
    df = _mixed_fixture()
    df["market_home_ml"] = -145
    with pytest.raises(StageAMarketContamination) as exc:
        _extract(df=df)
    assert exc.value.reason_code == "MARKET_CONTAMINATION"


def test_basketball_total_rebounds_field_is_not_misclassified_as_market():
    df = _mixed_fixture()
    df["h_total_rebounds"] = [33, 31]
    snapshot = _extract(df=df)
    assert snapshot.snapshot_hash
    assert "h_total_rebounds" not in snapshot.predictors.columns


def test_missing_requested_predictor_is_data_blocked():
    with pytest.raises(StageADataBlocked) as exc:
        _extract(predictors=("h_ortg_l7_pre", "registered_but_missing_pre"))
    assert exc.value.decision_state == "DATA_BLOCKED"


def test_failed_required_eligibility_is_data_blocked():
    df = _mixed_fixture()
    df.loc[df["event_id"] == "100", "h_data_ok"] = False
    with pytest.raises(StageADataBlocked) as exc:
        _extract(df=df)
    assert exc.value.decision_state == "DATA_BLOCKED"
    assert "h_data_ok" in str(exc.value)


def test_missing_required_eligibility_column_is_data_blocked():
    df = _mixed_fixture().drop(columns=["a_opp_join_ok"])
    with pytest.raises(StageADataBlocked):
        _extract(df=df)


def test_empty_eligibility_contract_is_model_blocked():
    with pytest.raises(StageAModelBlocked):
        _extract(eligibility=())


def test_null_predictor_value_is_data_blocked():
    df = _mixed_fixture()
    df.loc[0, "h_ortg_l7_pre"] = None
    with pytest.raises(StageADataBlocked):
        _extract(df=df)


def test_duplicate_event_id_is_data_blocked():
    df = _mixed_fixture()
    df.loc[1, "event_id"] = "200"
    with pytest.raises(StageADataBlocked):
        _extract(df=df)


def test_pit_cutoff_after_forecast_is_data_blocked():
    lineage = _lineage(data_cutoff="2026-01-15T18:01:00Z")
    with pytest.raises(StageADataBlocked) as exc:
        _extract(lineage=lineage)
    assert "Point-in-time violation" in str(exc.value)


def test_missing_lineage_field_is_data_blocked():
    lineage = _lineage()
    lineage.pop("source_snapshot_id")
    with pytest.raises(StageADataBlocked):
        _extract(lineage=lineage)


def test_contract_version_mismatch_is_model_blocked():
    lineage = _lineage(feature_contract_version="CBB-STAGEA-FTR-v0")
    with pytest.raises(StageAModelBlocked):
        _extract(lineage=lineage)


def test_snapshot_hash_is_deterministic_across_input_row_and_column_order():
    df = _mixed_fixture()
    first = _extract(df=df)
    second_source = df.iloc[::-1][list(reversed(df.columns))].copy()
    second = _extract(df=second_source)
    assert first.snapshot_hash == second.snapshot_hash
    pd.testing.assert_frame_equal(first.predictors, second.predictors)


def test_snapshot_hash_changes_when_predictor_value_changes():
    first = _extract()
    df = _mixed_fixture()
    df.loc[df["event_id"] == "100", "h_ortg_l7_pre"] += 0.01
    second = _extract(df=df)
    assert first.snapshot_hash != second.snapshot_hash


def test_lineage_and_eligibility_never_enter_predictor_matrix():
    snapshot = _extract()
    for column in ELIGIBILITY:
        assert column not in snapshot.predictors.columns
    assert "source" not in snapshot.predictors.columns
    assert "model_version" not in snapshot.predictors.columns


def test_empty_allowlist_is_model_blocked_not_auto_discovered():
    with pytest.raises(StageAModelBlocked):
        _extract(predictors=())
