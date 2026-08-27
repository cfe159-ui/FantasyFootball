"""Start/sit: the provably best legal lineup, and what it costs you to ignore it.

Greedy slot-filling is not reliable here. Flex eligibility sets overlap without
nesting -- a W/R slot and a W/T slot both accept receivers but neither contains
the other -- so filling the most restrictive slot first can strand points on the
bench. This solves the assignment exactly instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..model.value import FLEX_ELIGIBILITY, LeagueShape
from ..players import Player
from ..util import norm_pos

# Slots that never score.
NON_SCORING_SLOTS = {"BN", "IR"}

# Large enough to make an illegal assignment never chosen, small enough to
# avoid overflow in the solver.
INELIGIBLE = 1e6


@dataclass
class RosterSpot:
    """A player available for this week's lineup.

    `points` is the expected value already discounted for injury risk, so the
    optimizer compares like with like: a 14-point Questionable player and a
    9-point healthy one are 7.8 versus 9.0, and the healthy one starts.
    """
    player: Player
    points: float
    eligible: Tuple[str, ...]
    on_bye: bool = False
    unavailable_reason: Optional[str] = None
    raw_points: Optional[float] = None
    availability: Optional[object] = None

    @property
    def startable(self) -> bool:
        return not self.on_bye and self.unavailable_reason is None


@dataclass
class LineupResult:
    starters: List[Tuple[str, RosterSpot]] = field(default_factory=list)
    bench: List[RosterSpot] = field(default_factory=list)
    empty_slots: List[str] = field(default_factory=list)
    total: float = 0.0


def eligible_slots(spot: RosterSpot, slots: Sequence[str]) -> List[str]:
    """Which of the league's slots this player may legally fill."""
    out = []
    player_positions = {norm_pos(p) for p in spot.eligible if p}
    for slot in slots:
        slot_u = slot.upper()
        if slot_u in NON_SCORING_SLOTS:
            continue
        if slot_u in player_positions:
            out.append(slot)
            continue
        allowed = FLEX_ELIGIBILITY.get(slot_u)
        if allowed and player_positions & {norm_pos(a) for a in allowed}:
            out.append(slot)
    return out


def optimize(spots: Sequence[RosterSpot], shape: LeagueShape) -> LineupResult:
    """Find the highest-scoring legal lineup.

    Solved as a maximum-weight bipartite matching between players and slot
    instances, which is exact -- unlike greedy filling.
    """
    slot_instances: List[str] = []
    for slot, count in shape.slots.items():
        if slot.upper() in NON_SCORING_SLOTS:
            continue
        slot_instances.extend([slot] * int(count))

    startable = [s for s in spots if s.startable]
    result = LineupResult()
    if not slot_instances or not startable:
        result.bench = list(spots)
        result.empty_slots = list(slot_instances)
        return result

    # Pad so the matrix is square; padding rows represent an unfilled slot.
    n = max(len(startable), len(slot_instances))
    cost = np.full((n, n), INELIGIBLE, dtype=float)
    for i, spot in enumerate(startable):
        allowed = set(eligible_slots(spot, slot_instances))
        for j, slot in enumerate(slot_instances):
            if slot in allowed:
                cost[i, j] = -spot.points  # maximize points => minimize -points
    for i in range(len(startable), n):
        cost[i, :] = 0.0        # phantom player: leaves the slot empty
    for j in range(len(slot_instances), n):
        cost[:, j] = 0.0        # phantom slot: player sits on the bench

    rows, cols = linear_sum_assignment(cost)

    used = set()
    filled_slots = set()
    for i, j in zip(rows, cols):
        if i >= len(startable) or j >= len(slot_instances):
            continue
        if cost[i, j] >= INELIGIBLE:
            continue  # solver had to place an ineligible pair; treat as empty
        result.starters.append((slot_instances[j], startable[i]))
        used.add(id(startable[i]))
        filled_slots.add(j)
        result.total += startable[i].points

    result.starters.sort(key=lambda pair: (_slot_order(pair[0]), -pair[1].points))
    result.bench = [s for s in spots if id(s) not in used]
    result.bench.sort(key=lambda s: -s.points)
    result.empty_slots = [slot_instances[j] for j in range(len(slot_instances))
                          if j not in filled_slots]
    result.total = round(result.total, 2)
    return result


_SLOT_ORDER = ["QB", "RB", "WR", "TE", "W/R", "W/T", "W/R/T", "FLEX",
               "SUPERFLEX", "OP", "Q/W/R/T", "K", "DST"]


def _slot_order(slot: str) -> int:
    try:
        return _SLOT_ORDER.index(slot.upper())
    except ValueError:
        return len(_SLOT_ORDER)


@dataclass
class Swap:
    slot: str
    bench_in: RosterSpot
    starter_out: RosterSpot
    gain: float


def compare_to_current(spots: Sequence[RosterSpot], shape: LeagueShape,
                       current: Mapping[str, str]) -> Tuple[LineupResult, List[Swap], float]:
    """Optimal lineup, the changes needed to reach it, and points left on the bench.

    `current` maps player name -> the slot they are currently in.
    """
    optimal = optimize(spots, shape)
    started_now = {name for name, slot in current.items()
                   if slot and slot.upper() not in NON_SCORING_SLOTS}

    current_total = sum(s.points for s in spots
                        if s.player.name in started_now and s.startable)

    optimal_names = {s.player.name for _, s in optimal.starters}
    add = [(slot, s) for slot, s in optimal.starters if s.player.name not in started_now]
    drop = [s for s in spots if s.player.name in started_now
            and s.player.name not in optimal_names]

    swaps: List[Swap] = []
    for (slot, bench_in), starter_out in zip(add, drop):
        swaps.append(Swap(slot=slot, bench_in=bench_in, starter_out=starter_out,
                          gain=round(bench_in.points - starter_out.points, 2)))
    swaps.sort(key=lambda s: -s.gain)
    return optimal, swaps, round(optimal.total - current_total, 2)
