"""Value over replacement, derived from YOUR league's roster rules.

Projected points alone do not rank players. A 12-team league that starts one QB
makes elite quarterbacks nearly worthless in trade -- the 13th-best QB is free
on waivers -- while a superflex league makes them the most valuable asset on the
board. Same projections, opposite draft boards. The difference is entirely
replacement level, and replacement level comes from roster slots and team count.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from ..players import Player
from ..util import norm_pos
from .projections import Projection

# Which positions can fill each flex-type slot.
FLEX_ELIGIBILITY = {
    "W/R": ("WR", "RB"),
    "W/T": ("WR", "TE"),
    "W/R/T": ("WR", "RB", "TE"),
    "FLEX": ("WR", "RB", "TE"),
    "Q/W/R/T": ("QB", "WR", "RB", "TE"),
    "SUPERFLEX": ("QB", "WR", "RB", "TE"),
    "OP": ("QB", "WR", "RB", "TE"),
}

BASE_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


@dataclass
class LeagueShape:
    """The roster rules that determine positional value.

    Built from Yahoo league settings when available, or by hand so the draft
    board works before API access is granted.
    """
    num_teams: int = 12
    slots: Dict[str, int] = field(default_factory=lambda: {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "W/R/T": 1, "K": 1, "DST": 1, "BN": 6,
    })
    ppr: float = 1.0
    is_keeper: bool = False

    @classmethod
    def from_yahoo(cls, settings) -> "LeagueShape":
        return cls(
            num_teams=settings.num_teams or 12,
            slots=dict(settings.roster_slots),
            ppr=settings.is_ppr or 0.0,
            is_keeper=settings.is_keeper,
        )

    @property
    def bench_slots(self) -> int:
        return self.slots.get("BN", 0)

    @property
    def is_superflex(self) -> bool:
        return any(pos in self.slots and self.slots[pos] > 0
                   for pos in ("SUPERFLEX", "OP", "Q/W/R/T"))

    def starters_drafted(self) -> Dict[str, float]:
        """Expected number of each position rostered as a starter, league-wide.

        Dedicated slots count fully. Flex slots are distributed across their
        eligible positions by how often each actually fills a flex in practice --
        running backs and receivers absorb nearly all flex usage.
        """
        counts: Dict[str, float] = {p: 0.0 for p in BASE_POSITIONS}
        flex_split = {"RB": 0.45, "WR": 0.45, "TE": 0.10}

        for slot, n in self.slots.items():
            if n <= 0 or slot in ("BN", "IR"):
                continue
            slot_u = slot.upper()
            if slot_u in counts:
                counts[slot_u] += n
                continue
            eligible = FLEX_ELIGIBILITY.get(slot_u)
            if not eligible:
                continue
            if slot_u in ("SUPERFLEX", "OP", "Q/W/R/T"):
                # Superflex is filled by a quarterback almost every time.
                counts["QB"] += n * 0.85
                counts["RB"] += n * 0.06
                counts["WR"] += n * 0.07
                counts["TE"] += n * 0.02
            else:
                total = sum(flex_split.get(p, 0) for p in eligible) or 1.0
                for p in eligible:
                    counts[p] += n * flex_split.get(p, 0) / total
        return {p: v * self.num_teams for p, v in counts.items()}

    def replacement_rank(self, position: str) -> int:
        """The 1-based rank at which a position becomes replaceable.

        Starters plus a fraction of bench spend: managers hoard running backs
        and receivers far more than kickers or defenses.
        """
        starters = self.starters_drafted().get(norm_pos(position) or "", 0.0)
        bench_appetite = {"RB": 0.9, "WR": 0.9, "QB": 0.35, "TE": 0.35,
                          "K": 0.0, "DST": 0.05}
        extra = self.bench_slots * self.num_teams * bench_appetite.get(
            norm_pos(position) or "", 0.2) / 6.0
        return max(1, int(round(starters + extra)))


@dataclass
class ValuedPlayer:
    projection: Projection
    vor: float
    position_rank: int
    tier: int = 0            # tier across the whole board
    position_tier: int = 0   # tier among players at the same position

    @property
    def player(self) -> Player:
        return self.projection.player

    @property
    def points(self) -> float:
        return self.projection.points


def replacement_levels(projections: Mapping[Tuple[str, str], Projection],
                       shape: LeagueShape,
                       positions: Iterable[str] = ("QB", "RB", "WR", "TE")) -> Dict[str, float]:
    """Projected points of the replacement-level player at each position."""
    levels: Dict[str, float] = {}
    for position in positions:
        pos = norm_pos(position)
        pool = sorted((p.points for p in projections.values()
                       if norm_pos(p.player.position) == pos), reverse=True)
        if not pool:
            levels[pos] = 0.0
            continue
        rank = shape.replacement_rank(pos)
        levels[pos] = float(pool[min(rank - 1, len(pool) - 1)])
    return levels


def assign_tiers(values: List[float], multiplier: float = 2.5,
                 window: int = 10, floor: float = 0.75,
                 max_tier_size: int = 8) -> List[int]:
    """Group a descending list of values into tiers at meaningful dropoffs.

    A single absolute threshold cannot work here: gaps between adjacent players
    are ~14 points at the top of the board and ~0.5 in the middle, so any fixed
    cutoff either splits the top into singletons or lumps sixty mid-round players
    into one tier. Instead each gap is judged against the median gap in its own
    neighbourhood, so "a big dropoff" means big *relative to where you are*.

    max_tier_size forces a break through genuinely flat stretches, where players
    really are interchangeable but a 20-deep tier is useless to read.
    """
    if len(values) < 2:
        return [1] * len(values)

    gaps = [values[i - 1] - values[i] for i in range(1, len(values))]
    tiers = [1]
    tier = 1
    size = 1
    for i, gap in enumerate(gaps):
        lo = max(0, i - window)
        hi = min(len(gaps), i + window + 1)
        local = sorted(gaps[lo:hi])
        median = local[len(local) // 2] if local else 0.0
        threshold = max(floor, multiplier * median)
        if gap > threshold or size >= max_tier_size:
            tier += 1
            size = 0
        size += 1
        tiers.append(tier)
    return tiers


def value_players(projections: Mapping[Tuple[str, str], Projection],
                  shape: LeagueShape,
                  positions: Iterable[str] = ("QB", "RB", "WR", "TE"),
                  tier_multiplier: float = 2.5) -> List[ValuedPlayer]:
    """Rank players by value over replacement, with tier breaks."""
    levels = replacement_levels(projections, shape, positions)
    pos_set = {norm_pos(p) for p in positions}

    valued: List[ValuedPlayer] = []
    for proj in projections.values():
        pos = norm_pos(proj.player.position)
        if pos not in pos_set:
            continue
        valued.append(ValuedPlayer(
            projection=proj,
            vor=round(proj.points - levels.get(pos, 0.0), 2),
            position_rank=0,
        ))

    # Position ranks by raw points.
    by_pos: Dict[str, List[ValuedPlayer]] = {}
    for v in valued:
        by_pos.setdefault(norm_pos(v.player.position) or "", []).append(v)
    for pos, group in by_pos.items():
        group.sort(key=lambda v: v.points, reverse=True)
        for i, v in enumerate(group, 1):
            v.position_rank = i

    # Positional tiers first: "which tier of tight end is left" is the question
    # that actually gets asked on the clock.
    for pos, group in by_pos.items():
        for v, t in zip(group, assign_tiers([x.vor for x in group], tier_multiplier)):
            v.position_tier = t

    valued.sort(key=lambda v: v.vor, reverse=True)
    for v, t in zip(valued, assign_tiers([x.vor for x in valued], tier_multiplier)):
        v.tier = t
    return valued
