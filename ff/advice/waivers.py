"""Waiver wire: what a pickup is worth *to your roster*, not in the abstract.

The common mistake is ranking free agents by projected points. That answers the
wrong question. A 9-point-per-game receiver is worthless to a team already
starting three better ones, and a league-winner to a team with a hole. Value
here is measured by re-solving your optimal lineup with the player added and
seeing how much the lineup actually improves.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..model.projections import Projection
from ..model.value import LeagueShape
from ..players import Player, PlayerUniverse
from ..util import norm_pos
from .lineup import RosterSpot, optimize

# Weeks of a 17-game season a mid-season pickup typically contributes.
DEFAULT_WEEKS_REMAINING = 10


@dataclass
class WaiverTarget:
    player: Player
    projected_ppg: float
    marginal_ppg: float          # lineup improvement per week, this roster
    drop_candidate: Optional[Player]
    trending_adds: int = 0
    faab_pct: int = 0
    reasons: List[str] = field(default_factory=list)

    @property
    def season_gain(self) -> float:
        return round(self.marginal_ppg * DEFAULT_WEEKS_REMAINING, 1)


def _spot(player: Player, ppg: float, eligible: Optional[Sequence[str]] = None,
          on_bye: bool = False) -> RosterSpot:
    return RosterSpot(player=player, points=ppg,
                      eligible=tuple(eligible or [norm_pos(player.position) or ""]),
                      on_bye=on_bye)


def faab_bid(marginal_ppg: float, trending_adds: int, budget: int = 100) -> int:
    """Suggested FAAB bid as a percentage of remaining budget.

    A heuristic, not a model. Marginal lineup improvement sets the base; crowd
    demand raises it because you are bidding against other managers, not against
    the player's true value.
    """
    if marginal_ppg <= 0:
        return 0
    # ~4% of budget per point-per-week of genuine lineup improvement.
    base = min(60.0, marginal_ppg * 4.0)
    if trending_adds > 100_000:
        base *= 1.6
    elif trending_adds > 25_000:
        base *= 1.35
    elif trending_adds > 5_000:
        base *= 1.15
    return int(max(1, min(budget, round(base))))


def rank_targets(roster: Sequence[Tuple[Player, float]],
                 available: Sequence[Tuple[Player, float]],
                 shape: LeagueShape,
                 trending: Optional[Mapping[str, int]] = None,
                 bye_weeks: Optional[Mapping[str, int]] = None,
                 week: Optional[int] = None,
                 limit: int = 25) -> List[WaiverTarget]:
    """Rank available players by how much they improve your starting lineup.

    roster/available are (player, projected points per game) pairs.
    """
    trending = trending or {}
    bye_weeks = bye_weeks or {}

    def on_bye(p: Player) -> bool:
        return week is not None and bye_weeks.get(p.team or "") == week

    base_spots = [_spot(p, ppg, on_bye=on_bye(p)) for p, ppg in roster]
    base_total = optimize(base_spots, shape).total

    # The weakest roster player is the realistic drop; never drop a starter.
    starters = {id(s) for _, s in optimize(base_spots, shape).starters}
    droppable = sorted((s for s in base_spots if id(s) not in starters),
                       key=lambda s: s.points)

    targets: List[WaiverTarget] = []
    for player, ppg in available:
        drop = droppable[0] if droppable else None
        trial = [s for s in base_spots if drop is None or id(s) != id(drop)]
        trial = trial + [_spot(player, ppg, on_bye=on_bye(player))]
        gain = optimize(trial, shape).total - base_total

        reasons: List[str] = []
        adds = int(trending.get(player.sleeper_id or "", 0))
        if adds > 25_000:
            reasons.append(f"{adds:,} adds in 24h")
        if player.is_starter:
            reasons.append("atop depth chart")
        elif player.depth_chart_order and player.depth_chart_order <= 2:
            reasons.append(f"depth #{player.depth_chart_order}")
        if player.injury_status:
            reasons.append(f"[{player.injury_status}]")
        if gain <= 0 and adds > 25_000:
            reasons.append("crowd interest, no lineup gain for you")

        targets.append(WaiverTarget(
            player=player,
            projected_ppg=round(ppg, 2),
            marginal_ppg=round(gain, 2),
            drop_candidate=drop.player if drop else None,
            trending_adds=adds,
            faab_pct=faab_bid(gain, adds),
            reasons=reasons,
        ))

    # Primary sort is lineup improvement. Among players who improve nothing --
    # common for a healthy roster -- fall back to crowd demand then raw output,
    # so the list stays a meaningful watchlist instead of arbitrary order.
    targets.sort(key=lambda t: (-t.marginal_ppg, -t.trending_adds, -t.projected_ppg))
    return targets[:limit]


def assume_available(universe: PlayerUniverse, shape: LeagueShape,
                     rostered_names: Optional[Iterable[str]] = None,
                     positions=("QB", "RB", "WR", "TE")) -> List[Player]:
    """Approximate the free-agent pool without Yahoo access.

    Sleeper's search_rank orders players by consensus relevance, so the top
    (teams x roster size) are treated as rostered somewhere. This is an estimate;
    once Yahoo access is granted the real free-agent list replaces it.
    """
    taken = {n.lower() for n in (rostered_names or [])}
    roster_size = sum(shape.slots.values())
    cutoff = shape.num_teams * roster_size

    ranked = [p for p in universe.filter(positions=positions, rostered_only=True)
              if p.search_rank is not None]
    ranked.sort(key=lambda p: p.search_rank)
    return [p for p in ranked[cutoff:] if p.name.lower() not in taken]
