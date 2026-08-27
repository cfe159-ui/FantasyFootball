"""Trade evaluation: does this improve the lineup you actually start?

Summing projected points on each side is the standard approach and it is wrong
in a specific, costly way: it treats a bench player's points as real. They are
not. Points only count when a player is in your starting lineup, so a 2-for-1
that consolidates two flex-quality backs into one stud usually wins even though
you "lost" on raw totals -- and a trade that guts your depth can lose even when
the totals favour you, once a bye week arrives.

This evaluates by re-solving the optimal lineup before and after, then charges a
separate, explicit cost for the depth given up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from ..model.value import LeagueShape
from ..players import Player
from ..util import norm_pos
from .lineup import RosterSpot, optimize

# Each surrendered roster spot costs something real: fewer bye-week fills,
# fewer injury replacements, less waiver leverage. Charged per net player lost.
DEPTH_COST_PER_PLAYER = 0.35


@dataclass
class TradeSide:
    players: List[Player] = field(default_factory=list)
    points: float = 0.0


@dataclass
class TradeVerdict:
    lineup_before: float
    lineup_after: float
    depth_delta: int
    depth_penalty: float
    net: float
    outgoing_value: float
    incoming_value: float
    starters_gained: List[str] = field(default_factory=list)
    starters_lost: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.net > 1.5:
            return "accept"
        if self.net > 0.3:
            return "slight win"
        if self.net > -0.3:
            return "roughly even"
        if self.net > -1.5:
            return "slight loss"
        return "decline"


def evaluate(roster: Sequence[Tuple[Player, float]],
             giving: Sequence[Tuple[Player, float]],
             getting: Sequence[Tuple[Player, float]],
             shape: LeagueShape,
             bye_weeks: Optional[Mapping[str, int]] = None) -> TradeVerdict:
    """Evaluate a proposed trade from your side.

    roster/giving/getting are (player, projected points per game) pairs.
    """
    bye_weeks = bye_weeks or {}
    give_names = {p.name for p, _ in giving}

    def spots(pairs):
        return [RosterSpot(player=p, points=round(ppg, 2),
                           eligible=(norm_pos(p.position) or "",))
                for p, ppg in pairs]

    before_spots = spots(roster)
    before = optimize(before_spots, shape)

    kept = [(p, ppg) for p, ppg in roster if p.name not in give_names]
    after_spots = spots(kept + list(getting))
    after = optimize(after_spots, shape)

    before_starters = {s.player.name for _, s in before.starters}
    after_starters = {s.player.name for _, s in after.starters}

    depth_delta = len(getting) - len(giving)
    # Only losing bodies costs depth; gaining them is not a real benefit
    # beyond what the lineup already captures.
    penalty = DEPTH_COST_PER_PLAYER * max(0, -depth_delta)

    net = (after.total - before.total) - penalty

    notes: List[str] = []
    if depth_delta < 0:
        notes.append(f"gives up {-depth_delta} roster spot(s) of depth")
    if depth_delta > 0:
        notes.append(f"adds {depth_delta} body/bodies; you must drop someone")

    # Bye-week collisions among incoming starters.
    incoming_byes: Dict[int, List[str]] = {}
    for p, _ in getting:
        week = bye_weeks.get(p.team or "")
        if week:
            incoming_byes.setdefault(week, []).append(p.name)
    for week, names in incoming_byes.items():
        stacked = [n for n, _ in
                   [(s.player.name, s) for s in after_spots
                    if bye_weeks.get(s.player.team or "") == week]]
        if len(stacked) >= 3:
            notes.append(f"week {week} bye stack: {len(stacked)} players")

    return TradeVerdict(
        lineup_before=round(before.total, 2),
        lineup_after=round(after.total, 2),
        depth_delta=depth_delta,
        depth_penalty=round(penalty, 2),
        net=round(net, 2),
        outgoing_value=round(sum(ppg for _, ppg in giving), 2),
        incoming_value=round(sum(ppg for _, ppg in getting), 2),
        starters_gained=sorted(after_starters - before_starters),
        starters_lost=sorted(before_starters - after_starters),
        notes=notes,
    )
