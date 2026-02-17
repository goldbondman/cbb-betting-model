#!/usr/bin/env python3
"""Quant 1 - Archeologist: team archetype profiling."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

import pandas as pd


def _f(row: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default))
    except Exception:
        return float(default)


def _i(row: Mapping[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(row.get(key, default))
    except Exception:
        return int(default)


def _bool_conf(flag: bool, confidence: float, hist: Dict[str, float] | None = None) -> Dict[str, Any]:
    return {
        "label": bool(flag),
        "confidence": max(0.0, min(1.0, float(confidence))),
        "historical_base_rates": hist or {},
    }


def _safe_rate(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def _elite_defense_base_rates(historical_df: pd.DataFrame | None) -> Dict[str, float]:
    if historical_df is None or historical_df.empty:
        return {"final_four_rate": 0.0, "championship_rate": 0.0}
    df = historical_df.copy()
    mask = df.get("adj_def_rank", pd.Series(dtype=float)).fillna(999) <= 15
    return {
        "final_four_rate": _safe_rate((df[mask].get("made_final_four", pd.Series(dtype=float)).fillna(0) > 0).sum(), mask.sum()),
        "championship_rate": _safe_rate((df[mask].get("won_championship", pd.Series(dtype=float)).fillna(0) > 0).sum(), mask.sum()),
    }


def team_archetype_profile(team_row: Mapping[str, Any], historical_df: pd.DataFrame | None = None) -> Dict[str, Any]:
    team_name = str(team_row.get("team", team_row.get("name", "UNKNOWN")))
    adj_def_rank = _i(team_row, "adj_def_rank", 999)
    tourney_players = _i(team_row, "players_with_tourney_game", 0)
    ft_pct = _f(team_row, "ft_pct", 0.0)
    ft_rate_rank = _i(team_row, "ft_rate_rank", 999)
    three_pa_rate = _f(team_row, "three_pa_rate", 0.0)
    two_pt_pct = _f(team_row, "two_pt_pct", 0.0)
    guard_score_share = _f(team_row, "guard_scoring_assist_share", 0.0)
    sos_rank = _i(team_row, "sos_rank", 999)
    road_top50_games = _i(team_row, "road_games_vs_top50", 0)
    win_streak = _i(team_row, "win_streak_entering", 0)
    center_minutes_if_foul = _f(team_row, "center_minutes_in_foul_trouble", 25.0)
    three_possession_rate = _f(team_row, "three_pt_possession_rate", three_pa_rate)
    seed_inflation_score = _f(team_row, "seed_inflation_score", 0.0)
    first_year_transfers = _i(team_row, "first_year_transfers", 0)
    tempo_rank = _i(team_row, "tempo_rank", 175)
    faced_top40_pace_games = _i(team_row, "faced_top40_pace_games", 1)
    coach_tourney_appearances = _i(team_row, "coach_tourney_appearances", 3)
    conf_tourney_games = _i(team_row, "conference_tournament_games", 0)

    winning = {
        "ELITE_DEFENSE_WINS": _bool_conf(adj_def_rank <= 15, 1.0 - min(adj_def_rank, 120) / 120.0, _elite_defense_base_rates(historical_df)),
        "EXPERIENCED_MARCH": _bool_conf(tourney_players >= 4, min(1.0, tourney_players / 6.0)),
        "FREE_THROW_CLUTCH": _bool_conf(ft_pct > 0.75 and ft_rate_rank <= 40, min(1.0, ((ft_pct - 0.70) / 0.15 + (40 - min(ft_rate_rank, 40)) / 40) / 2)),
        "LOW_VARIANCE_OFFENSE": _bool_conf(three_pa_rate < 0.35 and two_pt_pct >= 0.52, min(1.0, (0.40 - min(three_pa_rate, 0.40)) * 1.8 + max(0.0, two_pt_pct - 0.50))),
        "GUARD_DOMINANT": _bool_conf(guard_score_share >= 0.60, min(1.0, guard_score_share)),
        "BATTLE_TESTED_SCHEDULE": _bool_conf(sos_rank <= 25 and road_top50_games >= 8, min(1.0, ((25 - min(sos_rank, 25)) / 25 + min(road_top50_games, 12) / 12) / 2)),
        "HOT_HAND_ENTRY": _bool_conf(win_streak >= 7, min(1.0, win_streak / 12.0)),
    }
    failure = {
        "THREE_POINT_DEPENDENT": _bool_conf(three_possession_rate >= 0.40, min(1.0, three_possession_rate)),
        "ONE_BIG_DEPENDENT": _bool_conf(center_minutes_if_foul < 25, min(1.0, (25 - center_minutes_if_foul) / 25.0)),
        "SOFT_SCHEDULE_FRAUD": _bool_conf(seed_inflation_score > 3, min(1.0, max(0.0, seed_inflation_score - 3) / 6.0)),
        "TRANSFER_PORTAL_CHAOS": _bool_conf(first_year_transfers >= 4, min(1.0, first_year_transfers / 8.0)),
        "STYLE_MISMATCH_VULNERABLE": _bool_conf(tempo_rank > 250 and faced_top40_pace_games == 0, min(1.0, (tempo_rank - 250) / 113.0)),
        "COACH_FIRST_RODEO": _bool_conf(coach_tourney_appearances <= 2, min(1.0, (3 - coach_tourney_appearances) / 3.0)),
        "CONFERENCE_TOURNEY_EXHAUSTED": _bool_conf(conf_tourney_games >= 4, min(1.0, conf_tourney_games / 6.0)),
    }

    return {
        "team": team_name,
        "winning_archetypes": winning,
        "failure_archetypes": failure,
    }


def team_archetype_profiles(teams: Iterable[Mapping[str, Any]], historical_df: pd.DataFrame | None = None) -> Dict[str, Dict[str, Any]]:
    return {str(t.get("team", t.get("name", f"team_{idx}"))): team_archetype_profile(t, historical_df) for idx, t in enumerate(teams)}


def validate_against_closing_line(seasons: Iterable[int] = range(2015, 2025), games_df: pd.DataFrame | None = None) -> Dict[str, Any]:
    if games_df is None or games_df.empty:
        return {"seasons": list(seasons), "sample_size": 0, "clv_delta": 0.0, "ats_roi": 0.0}
    df = games_df.copy()
    if "our_spread" not in df.columns or "closing_spread" not in df.columns:
        return {"seasons": list(seasons), "sample_size": len(df), "clv_delta": 0.0, "ats_roi": 0.0}
    clv = (df["closing_spread"] - df["our_spread"]).astype(float)
    ats = df.get("ats_result", pd.Series([0.0] * len(df))).fillna(0.0).astype(float)
    return {"seasons": list(seasons), "sample_size": len(df), "clv_delta": float(clv.mean()), "ats_roi": float(ats.mean())}
