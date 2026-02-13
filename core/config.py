"""Application framework configuration for the modular CBB betting app."""

from __future__ import annotations

APP_CONFIG: dict[str, object] = {
    "version": "v2.1",
    "ui_theme": {"primary_color": "#53D337", "bg_color": "#0E1E25"},
    "data": {"feature_store_path": "espn_team_game_features.csv", "feature_store_fallback_path": "ESPN/CSV/espn_team_game_logs.csv"},
}

BETTING_FRAMEWORK: dict[str, float] = {
    "kelly_fraction": 0.25,
    "max_units": 3.0,
    "min_edge": 0.03,
    "min_confidence": 0.60,
}

STRATEGY_PRESETS: dict[str, dict[str, object]] = {
    "Conservative": {
        "label": "Conservative",
        "betting": {**BETTING_FRAMEWORK, "kelly_fraction": 0.15, "max_units": 1.5, "min_edge": 0.05},
    },
    "Balanced": {
        "label": "Balanced",
        "betting": {**BETTING_FRAMEWORK},
    },
    "Aggressive": {
        "label": "Aggressive",
        "betting": {**BETTING_FRAMEWORK, "kelly_fraction": 0.4, "max_units": 4.0, "min_edge": 0.02},
    },
}

FEATURE_ALIASES: dict[str, list[str]] = {
    "torvik_adjem": ["torvik_adj_em", "adj_em"],
    "sos_weighted": ["sos_weighted_margin_l10_pre"],
}
