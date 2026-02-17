#!/usr/bin/env python3
"""Shared tournament game contract used by quant modules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class TournamentTeam:
    name: str
    seed: int
    kenpom_rank: int


@dataclass(frozen=True)
class BaseModelOutput:
    spread: float
    win_prob_a: float


@dataclass
class TournamentGame:
    game_id: str
    team_a: TournamentTeam
    team_b: TournamentTeam
    base_model_output: BaseModelOutput
    q1_archetype: Dict[str, Any] = field(default_factory=dict)
    q2_upset: Dict[str, Any] = field(default_factory=dict)
    q3_fragility: Dict[str, Any] = field(default_factory=dict)
    q4_situational: Dict[str, Any] = field(default_factory=dict)
    q5_final: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        # Keep JSON contract key names exactly as requested.
        return out


def build_tournament_game(
    game_id: str,
    team_a: Dict[str, Any],
    team_b: Dict[str, Any],
    base_model_output: Dict[str, Any],
) -> Dict[str, Any]:
    team_a_clean = {
        "name": team_a["name"],
        "seed": team_a["seed"],
        "kenpom_rank": team_a["kenpom_rank"],
    }
    team_b_clean = {
        "name": team_b["name"],
        "seed": team_b["seed"],
        "kenpom_rank": team_b["kenpom_rank"],
    }
    base_clean = {
        "spread": base_model_output["spread"],
        "win_prob_a": base_model_output["win_prob_a"],
    }
    game = TournamentGame(
        game_id=game_id,
        team_a=TournamentTeam(**team_a_clean),
        team_b=TournamentTeam(**team_b_clean),
        base_model_output=BaseModelOutput(**base_clean),
    )
    return game.to_dict()
