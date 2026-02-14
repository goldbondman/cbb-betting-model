"""Tests for enhanced formula model features."""
from pathlib import Path
from unittest import mock

from backtesting.backtest_engine import BacktestEngine
from core import model_registry


def test_backtest_engine_with_enhanced_features() -> None:
    """Test that BacktestEngine correctly handles new weighted features."""
    params = {
        "formula_type": "weighted_components",
        "weights": {
            "torvik_adjem": 0.40,
            "recent_netrtg": 0.20,
            "four_factors": 0.12,
            "sos_weighted": 0.08,
            "def_efficiency": 0.08,
            "off_efficiency": 0.06,
            "tempo_advantage": 0.04,
            "three_rate": 0.02,
        },
        "hca_mode": "static",
        "hca_static_value": 2.7,
        "pace_adjustment": True,
    }

    home_snap = {
        "torvik_adj_em": 10.0,
        "netrtg_l7_pre": 8.0,
        "efg_l7_pre": 0.52,
        "tov_pct_l7_pre": 0.15,
        "orb_pct_l7_pre": 0.32,
        "ftr_l7_pre": 0.35,
        "sos_weighted_margin_l10_pre": 50.0,
        "pace_l7_pre": 72.0,
        "drtg_l7_pre": 95.0,
        "ortg_l7_pre": 110.0,
        "3par_l7_pre": 0.40,
    }

    away_snap = {
        "torvik_adj_em": 5.0,
        "netrtg_l7_pre": 3.0,
        "efg_l7_pre": 0.48,
        "tov_pct_l7_pre": 0.18,
        "orb_pct_l7_pre": 0.28,
        "ftr_l7_pre": 0.30,
        "sos_weighted_margin_l10_pre": 30.0,
        "pace_l7_pre": 70.0,
        "drtg_l7_pre": 105.0,
        "ortg_l7_pre": 102.0,
        "3par_l7_pre": 0.35,
    }

    engine = BacktestEngine()
    prediction = engine._predict_with_params(home_snap, away_snap, params)

    assert "predicted_spread" in prediction
    assert isinstance(prediction["predicted_spread"], float)
    assert 0 < prediction["predicted_spread"] < 30  # Reasonable spread


def test_backtest_engine_backwards_compatible() -> None:
    """Test that legacy models still work without new features."""
    params = {
        "formula_type": "weighted_components",
        "weights": {
            "torvik_adjem": 0.50,
            "recent_netrtg": 0.25,
            "four_factors": 0.15,
            "sos_weighted": 0.10,
        },
        "hca_mode": "static",
        "hca_static_value": 2.7,
        "pace_adjustment": True,
    }

    home_snap = {
        "torvik_adj_em": 10.0,
        "netrtg_l7_pre": 8.0,
        "efg_l7_pre": 0.52,
        "tov_pct_l7_pre": 0.15,
        "orb_pct_l7_pre": 0.32,
        "ftr_l7_pre": 0.35,
        "sos_weighted_margin_l10_pre": 50.0,
        "pace_l7_pre": 72.0,
    }

    away_snap = {
        "torvik_adj_em": 5.0,
        "netrtg_l7_pre": 3.0,
        "efg_l7_pre": 0.48,
        "tov_pct_l7_pre": 0.18,
        "orb_pct_l7_pre": 0.28,
        "ftr_l7_pre": 0.30,
        "sos_weighted_margin_l10_pre": 30.0,
        "pace_l7_pre": 70.0,
    }

    engine = BacktestEngine()
    prediction = engine._predict_with_params(home_snap, away_snap, params)

    assert "predicted_spread" in prediction
    assert isinstance(prediction["predicted_spread"], float)
    assert 0 < prediction["predicted_spread"] < 30


def test_model_registry_with_enhanced_features(tmp_path: Path) -> None:
    """Test that model registry handles enhanced feature models."""
    local_path = tmp_path / "model_registry_local.json"
    with mock.patch.object(model_registry, "_LOCAL_REGISTRY_PATH", local_path), mock.patch.object(
        model_registry, "_client", return_value=None
    ):
        params = {
            "formula_type": "weighted_components",
            "weights": {
                "torvik_adjem": 0.40,
                "recent_netrtg": 0.20,
                "four_factors": 0.12,
                "sos_weighted": 0.08,
                "def_efficiency": 0.08,
                "off_efficiency": 0.06,
                "tempo_advantage": 0.04,
                "three_rate": 0.02,
            },
            "hca_mode": "static",
            "hca_static_value": 2.7,
            "pace_adjustment": True,
        }

        created = model_registry.create_model("enhanced-v1", "Enhanced Model", "spread", params)
        assert created is True

        models = model_registry.list_all_models("spread")
        assert len(models) == 1
        assert len(models[0]["params"]["weights"]) == 8  # 8 features

        assert model_registry.activate_model("enhanced-v1") is True
        active = model_registry.get_active_model("spread")
        assert active["model_id"] == "enhanced-v1"
        assert len(active["params"]["weights"]) == 8

        assert model_registry.delete_model("enhanced-v1") is True


def test_new_features_handle_missing_data() -> None:
    """Test that new features handle missing data gracefully."""
    params = {
        "formula_type": "weighted_components",
        "weights": {
            "torvik_adjem": 0.40,
            "recent_netrtg": 0.20,
            "four_factors": 0.12,
            "sos_weighted": 0.08,
            "def_efficiency": 0.08,
            "off_efficiency": 0.06,
            "tempo_advantage": 0.04,
            "three_rate": 0.02,
        },
        "hca_mode": "static",
        "hca_static_value": 2.7,
        "pace_adjustment": True,
    }

    # Home has new features, away doesn't
    home_snap = {
        "torvik_adj_em": 10.0,
        "netrtg_l7_pre": 8.0,
        "efg_l7_pre": 0.52,
        "tov_pct_l7_pre": 0.15,
        "orb_pct_l7_pre": 0.32,
        "ftr_l7_pre": 0.35,
        "sos_weighted_margin_l10_pre": 50.0,
        "pace_l7_pre": 72.0,
        "drtg_l7_pre": 95.0,
        "ortg_l7_pre": 110.0,
        "3par_l7_pre": 0.40,
    }

    away_snap = {
        "torvik_adj_em": 5.0,
        "netrtg_l7_pre": 3.0,
        "efg_l7_pre": 0.48,
        "tov_pct_l7_pre": 0.18,
        "orb_pct_l7_pre": 0.28,
        "ftr_l7_pre": 0.30,
        "sos_weighted_margin_l10_pre": 30.0,
        "pace_l7_pre": 70.0,
        # Missing: drtg_l7_pre, ortg_l7_pre, 3par_l7_pre
    }

    engine = BacktestEngine()
    # Should not raise exception, uses defaults
    prediction = engine._predict_with_params(home_snap, away_snap, params)

    assert "predicted_spread" in prediction
    assert isinstance(prediction["predicted_spread"], float)
