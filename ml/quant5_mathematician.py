#!/usr/bin/env python3
"""Quant 5 - Mathematician: integration, calibration, and bet execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

import numpy as np
import pandas as pd

from ml.calibration import ece, platt_apply, platt_fit
from ml.edge import american_to_prob
from ml.staking import kelly_fraction


@dataclass(frozen=True)
class TournamentBet:
    edge: float
    kelly_full: float
    kelly_recommended: float
    max_bet_sizing: float
    correlation_group: str
    hedge_opportunity: bool
    best_book: str
    line_shopping_value: float
    timing_recommendation: str


def learn_composite_weights(
    train_df: pd.DataFrame,
    ridge_alpha: float = 1.0,
    feature_cols: Sequence[str] = ("archetype_alignment_score", "upset_dog_dna_score", "favorite_fragility_index", "situational_adjustment_points"),
    target_col: str = "ats_outcome",
) -> Dict[str, float]:
    if train_df.empty or target_col not in train_df.columns:
        return {"w1": 0.25, "w2": 0.25, "w3": 0.25, "w4": 0.25}
    X = train_df.loc[:, list(feature_cols)].fillna(0.0).to_numpy(dtype=float)
    y = train_df[target_col].fillna(0.0).to_numpy(dtype=float).reshape(-1, 1)
    X = np.hstack([np.ones((X.shape[0], 1)), X])
    eye = np.eye(X.shape[1], dtype=float)
    eye[0, 0] = 0.0
    beta = np.linalg.pinv(X.T @ X + ridge_alpha * eye) @ X.T @ y
    # Normalize module weights to stable shares.
    ws = np.abs(beta.reshape(-1)[1:5])
    ws = ws / ws.sum() if ws.sum() > 0 else np.array([0.25, 0.25, 0.25, 0.25], dtype=float)
    return {"w1": float(ws[0]), "w2": float(ws[1]), "w3": float(ws[2]), "w4": float(ws[3])}


def composite_edge_score(
    archetype_alignment_score: float,
    upset_dog_dna_score: float,
    favorite_fragility_index: float,
    situational_adjustment_points: float,
    market_implied_edge: float,
    weights: Mapping[str, float],
) -> float:
    return float(
        weights.get("w1", 0.25) * archetype_alignment_score
        + weights.get("w2", 0.25) * upset_dog_dna_score
        + weights.get("w3", 0.25) * favorite_fragility_index
        + weights.get("w4", 0.25) * situational_adjustment_points
        - market_implied_edge
    )


def confidence_decay_function(confidence: float, hours_to_tipoff: float, half_life_hours: float = 24.0) -> float:
    c = max(0.0, min(1.0, float(confidence)))
    if hours_to_tipoff <= 0:
        return c
    decay = 0.5 ** (float(hours_to_tipoff) / max(1.0, half_life_hours))
    return float(c * (0.6 + 0.4 * decay))


def platt_scale_by_round(train_probs: np.ndarray, train_y: np.ndarray, eval_probs: np.ndarray) -> np.ndarray:
    model = platt_fit(train_probs, train_y)
    return platt_apply(eval_probs, model)


def expected_calibration_error(probs: np.ndarray, outcomes: np.ndarray, bins: int = 20) -> float:
    return float(ece(probs, outcomes, bins=bins))


def build_tournament_bet_card_row(
    game: Mapping[str, Any],
    composite_edge: float,
    fair_prob: float,
    market_odds: float,
    correlation_group: str,
    best_book: str,
    line_shopping_value: float,
) -> Dict[str, Any]:
    k_full = float(kelly_fraction(fair_prob, market_odds))
    rec = float(max(0.0, min(0.25 * k_full, 0.05)))
    tier = "A" if composite_edge >= 2.0 else ("B" if composite_edge >= 1.0 else "C")
    timing = "bet now" if composite_edge >= 1.5 and line_shopping_value >= 0.25 else ("wait for sharp action" if composite_edge >= 1.0 else "avoid late")
    bet = TournamentBet(
        edge=float(composite_edge),
        kelly_full=k_full,
        kelly_recommended=rec,
        max_bet_sizing=float(min(rec, 0.05)),
        correlation_group=correlation_group,
        hedge_opportunity=bool(abs(composite_edge) >= 3.0),
        best_book=best_book,
        line_shopping_value=float(line_shopping_value),
        timing_recommendation=timing,
    )
    return {
        "game_id": game.get("game_id"),
        "composite_edge": composite_edge,
        "recommended_bet": game.get("recommended_bet", ""),
        "kelly_recommended": rec,
        "confidence_tier": tier,
        "timing": timing,
        "best_book": best_book,
        "correlation_group": correlation_group,
        "bet": asdict(bet),
    }


def bracket_correlation_matrix(games: pd.DataFrame) -> pd.DataFrame:
    if games.empty or "correlation_group" not in games.columns:
        return pd.DataFrame()
    keys = games["game_id"].astype(str).tolist()
    groups = games["correlation_group"].astype(str).tolist()
    out = np.zeros((len(keys), len(keys)), dtype=float)
    for i, gi in enumerate(groups):
        for j, gj in enumerate(groups):
            out[i, j] = 1.0 if i == j else (0.6 if gi == gj else 0.05)
    return pd.DataFrame(out, index=keys, columns=keys)


def max_correlated_exposure(bets_df: pd.DataFrame, max_exposure: float = 0.15) -> pd.DataFrame:
    if bets_df.empty or "correlation_group" not in bets_df.columns:
        return bets_df
    out = bets_df.copy()
    out["kelly_recommended"] = out["kelly_recommended"].fillna(0.0).astype(float)
    for group, idx in out.groupby("correlation_group").groups.items():
        group_sum = float(out.loc[list(idx), "kelly_recommended"].sum())
        if group_sum > max_exposure and group_sum > 0:
            out.loc[list(idx), "kelly_recommended"] *= max_exposure / group_sum
    return out


def contrarian_value_finder(games_df: pd.DataFrame) -> pd.DataFrame:
    if games_df.empty:
        return games_df.copy()
    df = games_df.copy()
    for c in ("public_bet_pct", "model_edge_points"):
        if c not in df.columns:
            df[c] = 0.0
    return df[(df["public_bet_pct"].astype(float) > 0.65) & (df["model_edge_points"].astype(float).abs() > 3.0)].copy()


def export_bet_card_csv(bet_card_df: pd.DataFrame, output_path: str) -> str:
    bet_card_df.to_csv(output_path, index=False)
    return output_path


def discord_webhook_payload(bet_card_df: pd.DataFrame) -> Dict[str, Any]:
    lines = []
    for _, row in bet_card_df.iterrows():
        lines.append(f"{row.get('game_id')} | {row.get('recommended_bet')} | edge={row.get('composite_edge'):.2f}")
    return {"content": "\n".join(lines)}


def market_implied_edge_from_american_odds(model_prob: float, market_odds: float) -> float:
    return float(model_prob - american_to_prob(market_odds))


def validate_against_closing_line(seasons: Iterable[int] = range(2015, 2025), games_df: pd.DataFrame | None = None) -> Dict[str, Any]:
    if games_df is None or games_df.empty:
        return {"seasons": list(seasons), "sample_size": 0, "clv_delta": 0.0, "roi": 0.0}
    df = games_df.copy()
    clv = (df.get("closing_spread", pd.Series([0.0] * len(df))) - df.get("bet_spread", pd.Series([0.0] * len(df)))).astype(float).mean()
    roi = df.get("unit_pnl", pd.Series([0.0] * len(df))).astype(float).mean()
    return {"seasons": list(seasons), "sample_size": len(df), "clv_delta": float(clv), "roi": float(roi)}
