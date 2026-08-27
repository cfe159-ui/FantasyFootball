"""The player universe and cross-provider identity resolution.

Every other module joins on `Player.key`. Sleeper's own cross-platform IDs
(yahoo_id, espn_id, gsis_id) are too sparse to rely on -- yahoo_id is populated
for only ~23% of active players and is null for stars like Ja'Marr Chase -- so
we resolve on normalized name + position, which was measured to produce zero
collisions across all active NFL skill players.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .util import norm_name, norm_pos, norm_team

FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


@dataclass
class Player:
    """A single NFL player, unified across data providers."""
    name: str
    position: Optional[str]
    team: Optional[str]
    sleeper_id: Optional[str] = None
    yahoo_id: Optional[str] = None
    gsis_id: Optional[str] = None
    age: Optional[float] = None
    years_exp: Optional[int] = None
    injury_status: Optional[str] = None
    injury_body_part: Optional[str] = None
    depth_chart_order: Optional[int] = None
    depth_chart_position: Optional[str] = None
    bye_week: Optional[int] = None
    search_rank: Optional[int] = None
    extra: Dict = field(default_factory=dict)

    @property
    def key(self) -> Tuple[str, str]:
        """The canonical join key used across every data source."""
        return (norm_name(self.name), norm_pos(self.position) or "")

    @property
    def is_injured(self) -> bool:
        return bool(self.injury_status) and self.injury_status not in ("Healthy", "Active")

    @property
    def is_starter(self) -> bool:
        """True when the player is atop his team's depth chart at his position."""
        return self.depth_chart_order == 1

    def __repr__(self) -> str:
        bits = f"{self.name} ({self.position}"
        if self.team:
            bits += f"-{self.team}"
        bits += ")"
        if self.is_injured:
            bits += f" [{self.injury_status}]"
        return bits


class PlayerUniverse:
    """An indexed collection of players supporting exact and fuzzy lookup."""

    def __init__(self, players: Iterable[Player]):
        self.players: List[Player] = list(players)
        self._by_key: Dict[Tuple[str, str], Player] = {}
        self._by_name: Dict[str, List[Player]] = {}
        self._by_sleeper: Dict[str, Player] = {}
        for p in self.players:
            # First writer wins; the Sleeper dump is ordered with active players
            # ahead of retired ones sharing a name.
            self._by_key.setdefault(p.key, p)
            self._by_name.setdefault(norm_name(p.name), []).append(p)
            if p.sleeper_id:
                self._by_sleeper[p.sleeper_id] = p

    def __len__(self) -> int:
        return len(self.players)

    def by_sleeper_id(self, sleeper_id: str) -> Optional[Player]:
        return self._by_sleeper.get(str(sleeper_id))

    def resolve(self, name: str, position: Optional[str] = None,
                team: Optional[str] = None, fuzzy: bool = True) -> Optional[Player]:
        """Find a player from another provider's (name, position, team) triple.

        Tries exact key match, then name-only, then a conservative fuzzy match.
        Returns None rather than guessing when nothing is close enough --
        callers surface unresolved players instead of silently dropping them.
        """
        nname, npos = norm_name(name), norm_pos(position)
        if npos:
            hit = self._by_key.get((nname, npos))
            if hit:
                return hit

        candidates = self._by_name.get(nname, [])
        if candidates:
            if npos:
                for c in candidates:
                    if norm_pos(c.position) == npos:
                        return c
            nteam = norm_team(team)
            if nteam:
                for c in candidates:
                    if norm_team(c.team) == nteam:
                        return c
            return candidates[0]

        if not fuzzy or len(nname) < 4:
            return None
        # Restrict fuzzy search to the same position: cheaper and far safer.
        pool = [n for n, ps in self._by_name.items()
                if not npos or any(norm_pos(p.position) == npos for p in ps)]
        close = difflib.get_close_matches(nname, pool, n=1, cutoff=0.88)
        if close:
            for c in self._by_name[close[0]]:
                if not npos or norm_pos(c.position) == npos:
                    return c
        return None

    def filter(self, positions: Optional[Iterable[str]] = None,
               rostered_only: bool = True) -> List[Player]:
        pos = {norm_pos(p) for p in positions} if positions else None
        out = []
        for p in self.players:
            if pos and norm_pos(p.position) not in pos:
                continue
            if rostered_only and not p.team:
                continue
            out.append(p)
        return out
