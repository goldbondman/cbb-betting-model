"""Tests for core.supabase_schema centralised constants."""

from core.supabase_schema import (
    MARKET_SPREAD_ALIASES,
    PREDICTION_MARGIN_ALIASES,
    PREDICTION_TOTAL_ALIASES,
    PUBLIC_GAMES_TABLE,
    PUBLIC_PREDICTIONS_TABLE,
    PUBLIC_SCHEMA,
    RAW_PREDICTIONS_LIMIT,
    RAW_PREDICTIONS_SCHEMA,
    RAW_PREDICTIONS_TABLE,
    RAW_SCHEMA,
)


class TestSchemaDefaults:
    """Schema/table constants should have sensible defaults."""

    def test_raw_schema_default(self) -> None:
        assert RAW_SCHEMA == "raw"

    def test_public_schema(self) -> None:
        assert PUBLIC_SCHEMA == "public"

    def test_raw_predictions_table(self) -> None:
        assert RAW_PREDICTIONS_TABLE == "predictions_latest"

    def test_raw_predictions_schema(self) -> None:
        assert RAW_PREDICTIONS_SCHEMA == "raw"

    def test_public_games_table(self) -> None:
        assert PUBLIC_GAMES_TABLE == "games"

    def test_public_predictions_table(self) -> None:
        assert PUBLIC_PREDICTIONS_TABLE == "predictions"

    def test_raw_predictions_limit_is_positive(self) -> None:
        assert RAW_PREDICTIONS_LIMIT > 0


class TestColumnAliases:
    """Alias lists should be non-empty and ordered by priority."""

    def test_prediction_margin_aliases(self) -> None:
        assert len(PREDICTION_MARGIN_ALIASES) >= 2
        assert PREDICTION_MARGIN_ALIASES[0] == "pred_margin_home"

    def test_prediction_total_aliases(self) -> None:
        assert len(PREDICTION_TOTAL_ALIASES) >= 2
        assert PREDICTION_TOTAL_ALIASES[0] == "pred_total"

    def test_market_spread_aliases(self) -> None:
        assert len(MARKET_SPREAD_ALIASES) >= 2
        assert MARKET_SPREAD_ALIASES[0] == "market_spread"
