#!/usr/bin/env python3
"""
Play/no-play rules.
"""

from __future__ import annotations


def should_bet(edge: float, conf: float, min_edge: float = 0.02, min_conf: float = 0.7) -> bool:
    return edge >= min_edge and conf >= min_conf
