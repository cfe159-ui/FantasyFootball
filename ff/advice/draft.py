"""Draft board: what your league's rules say a player is worth."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..model.projections import (BaselineProjector, DefenseProjector,
                                 ExpectedPointsProjector, MarketPrior,
                                 Projection, ensemble)
from ..model.scoring import ScoringRules
from ..model.value import LeagueShape, ValuedPlayer, value_players
from ..players import PlayerUniverse
from ..sources import ffopportunity, nflverse

DEFAULT_SEASONS = 3

# Three views of the same player, deliberately disagreeing:
#   history  -- what he actually produced
#   expected -- what his opportunity said he should have produced
#   market   -- what the draft market thinks, which is the only one that sees
#               rookies and changed situations at all
# Expected points carry real weight because unsustainable touchdown rates show
# up there first, and nowhere in raw production.
DEFAULT_WEIGHTS = {"history": 0.40, "expected": 0.30, "market": 0.30}


def build_board(universe: PlayerUniverse, shape: LeagueShape, rules: ScoringRules,
                current_season: int, positions=("QB", "RB", "WR", "TE", "K", "DST"),
                weights: Optional[Dict[str, float]] = None,
                seasons: int = DEFAULT_SEASONS) -> List[ValuedPlayer]:
    """Project, blend, and rank every relevant player by value over replacement."""
    history_seasons = [current_season - i for i in range(1, seasons + 1)]
    weekly = nflverse.player_week_stats(history_seasons)
    if weekly.empty:
        raise RuntimeError(f"No nflverse data for seasons {history_seasons}")

    skill = tuple(p for p in positions if p != "DST")
    projector = BaselineProjector(weekly, rules, seasons=history_seasons)
    historical = projector.project(universe, positions=skill)
    market = MarketPrior(universe).project(historical, positions=skill)

    sources = {"history": historical, "market": market}

    # Expected points cover only positions ffopportunity models (no K).
    exp_positions = tuple(p for p in skill if p in ("QB", "RB", "WR", "TE"))
    if exp_positions:
        exp_weekly = ffopportunity.weekly_expected(history_seasons)
        if not exp_weekly.empty:
            sources["expected"] = ExpectedPointsProjector(
                exp_weekly, projector.per_game, seasons=history_seasons
            ).project(universe, positions=exp_positions)

    blended = ensemble(sources, weights or DEFAULT_WEIGHTS)

    if "DST" in positions:
        team_weekly = nflverse.team_defense(history_seasons)
        blended.update(DefenseProjector(team_weekly, rules,
                                        seasons=history_seasons).project(universe))

    return value_players(blended, shape, positions=positions)


def positional_scarcity(board: List[ValuedPlayer], shape: LeagueShape) -> Dict[str, Dict]:
    """How fast value falls off at each position -- the real draft signal.

    A position where the top 5 are far above replacement and the next 15 are
    flat is one to attack early; a flat position can wait.
    """
    out: Dict[str, Dict] = {}
    by_pos: Dict[str, List[ValuedPlayer]] = {}
    for v in board:
        by_pos.setdefault(v.player.position or "", []).append(v)
    for pos, group in by_pos.items():
        group.sort(key=lambda v: v.vor, reverse=True)
        top5 = [v.vor for v in group[:5]]
        next10 = [v.vor for v in group[5:15]]
        out[pos] = {
            "elite_vor": round(sum(top5) / len(top5), 1) if top5 else 0.0,
            "next_tier_vor": round(sum(next10) / len(next10), 1) if next10 else 0.0,
            "cliff": round((sum(top5) / len(top5)) - (sum(next10) / len(next10)), 1)
            if top5 and next10 else 0.0,
            "replacement_rank": shape.replacement_rank(pos),
        }
    return out
