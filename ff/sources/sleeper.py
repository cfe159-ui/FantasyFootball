"""Sleeper's public API: the player universe, injuries, depth charts, and the
single best free waiver-wire signal available -- league-wide add/drop velocity.

Entirely unauthenticated. Nothing here depends on Yahoo approval, which is why
the analytics engine is built on top of it.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..players import Player, PlayerUniverse
from ..util import cached, get_json, norm_pos, norm_team

BASE = "https://api.sleeper.app/v1"

# The full dump is ~14MB and changes slowly; Sleeper explicitly asks callers to
# fetch it at most once per day.
PLAYERS_TTL = 12 * 3600
TRENDING_TTL = 30 * 60

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def _fetch_players_raw() -> Dict:
    return get_json(f"{BASE}/players/nfl", timeout=120)


def load_universe(include_free_agents: bool = True) -> PlayerUniverse:
    """Build the canonical player universe from Sleeper's player dump."""
    raw = cached("sleeper_players_nfl", PLAYERS_TTL, _fetch_players_raw)
    players: List[Player] = []
    for sid, p in raw.items():
        pos = p.get("position")
        if pos not in FANTASY_POSITIONS:
            continue
        if not p.get("active") and not include_free_agents:
            continue
        name = p.get("full_name") or " ".join(
            filter(None, [p.get("first_name"), p.get("last_name")])
        )
        if not name:
            continue
        players.append(Player(
            name=name,
            position=norm_pos(pos),
            team=norm_team(p.get("team")),
            sleeper_id=sid,
            yahoo_id=str(p["yahoo_id"]) if p.get("yahoo_id") else None,
            gsis_id=p.get("gsis_id"),
            age=p.get("age"),
            years_exp=p.get("years_exp"),
            injury_status=p.get("injury_status"),
            injury_body_part=p.get("injury_body_part"),
            depth_chart_order=p.get("depth_chart_order"),
            depth_chart_position=p.get("depth_chart_position"),
            search_rank=p.get("search_rank"),
            extra={
                "status": p.get("status"),
                "active": p.get("active"),
                "injury_notes": p.get("injury_notes"),
                "practice_participation": p.get("practice_participation"),
                "news_updated": p.get("news_updated"),
            },
        ))
    # Sleeper's search_rank approximates consensus relevance; ordering by it
    # means PlayerUniverse's "first writer wins" resolves name ties to the
    # more relevant player.
    players.sort(key=lambda p: (p.search_rank is None, p.search_rank or 0))
    return PlayerUniverse(players)


def trending(kind: str = "add", lookback_hours: int = 24,
             limit: int = 50) -> List[Tuple[str, int]]:
    """Raw (sleeper_id, count) pairs for players being added or dropped."""
    assert kind in ("add", "drop"), "kind must be 'add' or 'drop'"
    key = f"sleeper_trending_{kind}_{lookback_hours}_{limit}"
    data = cached(key, TRENDING_TTL, lambda: get_json(
        f"{BASE}/players/nfl/trending/{kind}",
        params={"lookback_hours": lookback_hours, "limit": limit},
    ))
    return [(d["player_id"], d["count"]) for d in data]


def trending_players(universe: PlayerUniverse, kind: str = "add",
                     lookback_hours: int = 24,
                     limit: int = 50) -> List[Tuple[Player, int]]:
    """Trending adds/drops resolved to Player objects, unknown IDs skipped."""
    out = []
    for sid, count in trending(kind, lookback_hours, limit):
        player = universe.by_sleeper_id(sid)
        if player:
            out.append((player, count))
    return out


def nfl_state() -> Dict:
    """Current season, week, and season phase (pre/regular/post)."""
    return cached("sleeper_state", 3600, lambda: get_json(f"{BASE}/state/nfl"))
