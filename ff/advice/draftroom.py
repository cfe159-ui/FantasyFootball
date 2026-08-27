"""Live draft assistant: what to do with the pick that is actually in front of you.

A static ranking is not enough on the clock. It does not know that six running
backs went while you were deciding, that your flex is already covered, or that
the tier you were waiting on is one pick from empty. This tracks the board as it
empties and values each candidate by how much he improves YOUR starting lineup,
using the same exact optimizer the weekly lineup command uses.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..model.value import LeagueShape, ValuedPlayer, replacement_levels
from ..players import Player
from ..util import DATA_DIR, norm_name, norm_pos
from .lineup import RosterSpot, optimize

STATE_PATH = DATA_DIR / "draft.json"


@dataclass
class DraftState:
    teams: int = 10
    my_pick: int = 1                  # first-round slot, 1-based
    rounds: int = 16
    snake: bool = True
    taken: List[Dict] = field(default_factory=list)   # {name, pos, team, mine}

    @property
    def picks_made(self) -> int:
        return len(self.taken)

    @property
    def my_roster_names(self) -> List[str]:
        return [t["name"] for t in self.taken if t.get("mine")]

    @property
    def taken_keys(self) -> set:
        return {(norm_name(t["name"]), norm_pos(t.get("pos"))) for t in self.taken}

    def pick_number(self) -> int:
        return self.picks_made + 1

    def round_and_slot(self, pick: Optional[int] = None) -> Tuple[int, int]:
        pick = pick or self.pick_number()
        rnd = (pick - 1) // self.teams + 1
        idx = (pick - 1) % self.teams
        slot = idx + 1
        if self.snake and rnd % 2 == 0:
            slot = self.teams - idx
        return rnd, slot

    def is_my_turn(self) -> bool:
        _, slot = self.round_and_slot()
        return slot == self.my_pick

    def picks_until_mine(self) -> int:
        """How many selections happen before you are on the clock again."""
        for ahead in range(0, self.teams * self.rounds):
            pick = self.pick_number() + ahead
            rnd = (pick - 1) // self.teams + 1
            idx = (pick - 1) % self.teams
            slot = self.teams - idx if (self.snake and rnd % 2 == 0) else idx + 1
            if slot == self.my_pick:
                return ahead
        return 0

    def next_gap(self) -> int:
        """Picks between your next selection and the one after it."""
        first = self.picks_until_mine()
        for ahead in range(first + 1, self.teams * self.rounds):
            pick = self.pick_number() + ahead
            rnd = (pick - 1) // self.teams + 1
            idx = (pick - 1) % self.teams
            slot = self.teams - idx if (self.snake and rnd % 2 == 0) else idx + 1
            if slot == self.my_pick:
                return ahead - first
        return 0

    # -- persistence --------------------------------------------------------

    def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({
            "teams": self.teams, "my_pick": self.my_pick, "rounds": self.rounds,
            "snake": self.snake, "taken": self.taken}, indent=2))

    @classmethod
    def load(cls) -> Optional["DraftState"]:
        if not STATE_PATH.exists():
            return None
        try:
            d = json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            return None
        return cls(teams=d.get("teams", 10), my_pick=d.get("my_pick", 1),
                   rounds=d.get("rounds", 16), snake=d.get("snake", True),
                   taken=d.get("taken", []))


@dataclass
class Candidate:
    valued: ValuedPlayer
    lineup_gain: float           # improvement to your starting lineup
    score: float                 # blended draft value
    tier_remaining: int          # players left in this player's positional tier
    survival: float              # rough chance he lasts until your next pick

    @property
    def player(self) -> Player:
        return self.valued.player


def _my_spots(state: DraftState, board_by_key: Dict, universe) -> List[RosterSpot]:
    spots = []
    for name in state.my_roster_names:
        p = universe.resolve(name)
        if not p:
            continue
        v = board_by_key.get(p.key)
        ppg = v.projection.per_game if v else 0.0
        spots.append(RosterSpot(player=p, points=round(ppg, 2), eligible=(p.position,)))
    return spots


def _replacement_filler(shape: LeagueShape, levels: Dict[str, float],
                        existing: List[RosterSpot]) -> List[RosterSpot]:
    """Placeholder players representing what you can get later for free.

    Without these, an empty roster makes every open slot look worth a player's
    entire point total, so the highest-scoring position wins -- which is exactly
    the reasoning that drafts a quarterback in round one. Seeding replacement
    level means a pick is credited only with what he adds ABOVE the player you
    could have had anyway.
    """
    have: Dict[str, int] = {}
    for s in existing:
        pos = norm_pos(s.player.position) or ""
        have[pos] = have.get(pos, 0) + 1

    fillers: List[RosterSpot] = []
    for slot, count in shape.starting_slots.items():
        pos = norm_pos(slot) or slot.upper()
        if pos not in ("QB", "RB", "WR", "TE", "K", "DST"):
            # Flex slots are covered by whichever skill players are already in.
            pos = "RB"
        for _ in range(int(count)):
            if have.get(pos, 0) > 0:
                have[pos] -= 1
                continue
            level = levels.get(pos, 0.0) / 16.0
            fillers.append(RosterSpot(
                player=Player(name=f"replacement {pos}", position=pos, team=None),
                points=round(level, 2), eligible=(pos,)))
    return fillers


def rank_candidates(board: Sequence[ValuedPlayer], state: DraftState,
                    shape: LeagueShape, universe,
                    limit: int = 15, need_weight: float = 0.5) -> List[Candidate]:
    """Rank available players for the pick on the clock.

    Score blends raw value over replacement with how much the player actually
    improves your starting lineup. Early on those agree; later they diverge
    sharply, which is exactly when a static ranking starts giving bad advice --
    it will happily hand you a fourth running back for a flex you have filled.
    """
    taken = state.taken_keys
    board_by_key = {v.player.key: v for v in board}
    available = [v for v in board if v.player.key not in taken]

    my_spots = _my_spots(state, board_by_key, universe)
    levels = replacement_levels(
        {v.player.key: v.projection for v in board}, shape,
        positions=("QB", "RB", "WR", "TE", "K", "DST"))
    baseline = my_spots + _replacement_filler(shape, levels, my_spots)
    base_total = optimize(baseline, shape).total

    # How many picks until you are up again, for survival estimates.
    gap = state.picks_until_mine() + state.next_gap()

    # Tier inventory among players still on the board.
    tier_counts: Dict[Tuple[str, int], int] = {}
    for v in available:
        key = (norm_pos(v.player.position) or "", v.position_tier)
        tier_counts[key] = tier_counts.get(key, 0) + 1

    out: List[Candidate] = []
    for v in available[: max(limit * 6, 60)]:
        trial = baseline + [RosterSpot(player=v.player,
                                       points=round(v.projection.per_game, 2),
                                       eligible=(v.player.position,))]
        gain = optimize(trial, shape).total - base_total
        # Per-game lineup gain scaled to a season, so it is comparable to VOR.
        score = v.vor + need_weight * (gain * 16.0)

        # Rough survival: players ranked above him are likelier to go first.
        ahead = sum(1 for o in available if o.vor > v.vor)
        survival = max(0.0, 1.0 - (gap / max(ahead + gap, 1)))

        out.append(Candidate(
            valued=v, lineup_gain=round(gain, 2), score=round(score, 1),
            tier_remaining=tier_counts.get(
                (norm_pos(v.player.position) or "", v.position_tier), 0),
            survival=round(survival, 2)))

    out.sort(key=lambda c: -c.score)
    return out[:limit]


def roster_needs(state: DraftState, shape: LeagueShape, universe) -> Dict[str, int]:
    """Starting slots still unfilled, by position."""
    have: Dict[str, int] = {}
    for name in state.my_roster_names:
        p = universe.resolve(name)
        if p:
            pos = norm_pos(p.position) or ""
            have[pos] = have.get(pos, 0) + 1

    need: Dict[str, int] = {}
    for slot, count in shape.starting_slots.items():
        pos = norm_pos(slot) or slot.upper()
        if pos in ("QB", "RB", "WR", "TE", "K", "DST"):
            need[pos] = max(0, count - have.get(pos, 0))
    return need


def positional_runs(state: DraftState, window: int = 8) -> Dict[str, int]:
    """Positions being taken heavily in the most recent picks."""
    recent = state.taken[-window:]
    counts: Dict[str, int] = {}
    for t in recent:
        pos = norm_pos(t.get("pos")) or "?"
        counts[pos] = counts.get(pos, 0) + 1
    return counts
