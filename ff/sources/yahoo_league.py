"""Typed accessors over YahooClient for the league resources we actually use.

Every method is a GET; the Yahoo API grants no write access, so nothing here
can alter your team.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..util import norm_pos, norm_team
from .yahoo import YahooClient

NFL_GAME_KEY = "nfl"  # Yahoo resolves the bare code to the current season.


@dataclass
class LeagueSettings:
    league_key: str
    name: str
    num_teams: int
    scoring_type: str                      # 'head', 'roto', 'point'
    roster_slots: Dict[str, int] = field(default_factory=dict)
    stat_modifiers: Dict[str, float] = field(default_factory=dict)
    stat_names: Dict[str, str] = field(default_factory=dict)
    current_week: Optional[int] = None
    start_week: Optional[int] = None
    end_week: Optional[int] = None
    playoff_start_week: Optional[int] = None
    waiver_type: Optional[str] = None
    uses_faab: bool = False
    draft_status: Optional[str] = None
    is_keeper: bool = False

    @property
    def starting_slots(self) -> Dict[str, int]:
        """Roster slots that score points, excluding bench and IR."""
        return {k: v for k, v in self.roster_slots.items() if k not in ("BN", "IR")}

    @property
    def is_ppr(self) -> Optional[float]:
        """Points per reception, read from the league's own scoring rules."""
        for stat_id, mod in self.stat_modifiers.items():
            if self.stat_names.get(stat_id, "").lower() in ("rec", "receptions"):
                return mod
        return None


def _as_list(node) -> List:
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


class League:
    """A single Yahoo fantasy league."""

    def __init__(self, client: YahooClient, league_key: str):
        self.client = client
        self.league_key = league_key
        self._settings: Optional[LeagueSettings] = None

    # -- discovery ----------------------------------------------------------

    @staticmethod
    def discover(client: YahooClient) -> List[Dict]:
        """List the logged-in user's NFL leagues for the current season."""
        data = client.get(
            f"users;use_login=1/games;game_keys={NFL_GAME_KEY}/leagues"
        )
        out: List[Dict] = []
        for user in _as_list(data.get("users")):
            u = user.get("user", user)
            for game in _as_list(u.get("games")):
                g = game.get("game", game)
                for league in _as_list(g.get("leagues")):
                    lg = league.get("league", league)
                    if isinstance(lg, dict) and lg.get("league_key"):
                        out.append({
                            "league_key": lg["league_key"],
                            "name": lg.get("name"),
                            "num_teams": lg.get("num_teams"),
                            "season": g.get("season"),
                            "scoring_type": lg.get("scoring_type"),
                            "draft_status": lg.get("draft_status"),
                            "url": lg.get("url"),
                        })
        return out

    # -- settings -----------------------------------------------------------

    def settings(self, refresh: bool = False) -> LeagueSettings:
        if self._settings and not refresh:
            return self._settings
        data = self.client.get(f"league/{self.league_key}/settings")
        lg = _as_list(data.get("league"))
        meta: Dict = {}
        settings: Dict = {}
        for chunk in lg if isinstance(lg, list) else [lg]:
            if not isinstance(chunk, dict):
                continue
            if "settings" in chunk:
                s = chunk["settings"]
                settings.update(s[0] if isinstance(s, list) and s else s)
            else:
                meta.update(chunk)

        slots: Dict[str, int] = {}
        for pos in _as_list(settings.get("roster_positions")):
            p = pos.get("roster_position", pos)
            if isinstance(p, dict) and p.get("position"):
                slots[p["position"]] = int(p.get("count", 0))

        modifiers: Dict[str, float] = {}
        for st in _as_list(settings.get("stat_modifiers", {}).get("stats")
                           if isinstance(settings.get("stat_modifiers"), dict) else None):
            s = st.get("stat", st)
            if isinstance(s, dict) and s.get("stat_id") is not None:
                try:
                    modifiers[str(s["stat_id"])] = float(s.get("value", 0))
                except (TypeError, ValueError):
                    continue

        names: Dict[str, str] = {}
        for st in _as_list(settings.get("stat_categories", {}).get("stats")
                           if isinstance(settings.get("stat_categories"), dict) else None):
            s = st.get("stat", st)
            if isinstance(s, dict) and s.get("stat_id") is not None:
                names[str(s["stat_id"])] = s.get("display_name") or s.get("name") or ""

        waiver_type = settings.get("waiver_type")
        self._settings = LeagueSettings(
            league_key=self.league_key,
            name=meta.get("name", "?"),
            num_teams=int(meta.get("num_teams", 0) or 0),
            scoring_type=meta.get("scoring_type", ""),
            roster_slots=slots,
            stat_modifiers=modifiers,
            stat_names=names,
            current_week=_int(meta.get("current_week")),
            start_week=_int(meta.get("start_week")),
            end_week=_int(meta.get("end_week")),
            playoff_start_week=_int(settings.get("playoff_start_week")),
            waiver_type=waiver_type,
            uses_faab=str(settings.get("uses_faab", "0")) in ("1", "true", "True"),
            draft_status=meta.get("draft_status"),
            is_keeper=str(settings.get("is_keeper_league", "0")) in ("1", "true", "True"),
        )
        return self._settings

    # -- rosters and matchups ----------------------------------------------

    def teams(self) -> List[Dict]:
        data = self.client.get(f"league/{self.league_key}/teams")
        out = []
        for lg in _as_list(data.get("league")):
            for team in _as_list(lg.get("teams") if isinstance(lg, dict) else None):
                t = team.get("team", team)
                if isinstance(t, dict) and t.get("team_key"):
                    out.append(t)
        return out

    def my_team_key(self) -> Optional[str]:
        """The team in this league belonging to the authenticated user."""
        data = self.client.get(
            f"users;use_login=1/games;game_keys={NFL_GAME_KEY}/teams"
        )
        for user in _as_list(data.get("users")):
            u = user.get("user", user)
            for game in _as_list(u.get("games")):
                g = game.get("game", game)
                for team in _as_list(g.get("teams") if isinstance(g, dict) else None):
                    t = team.get("team", team)
                    key = t.get("team_key") if isinstance(t, dict) else None
                    if key and key.startswith(self.league_key + "."):
                        return key
        return None

    def roster(self, team_key: str, week: Optional[int] = None) -> List[Dict]:
        """Raw roster entries with selected lineup slot and player metadata."""
        path = f"team/{team_key}/roster"
        if week:
            path += f";week={week}"
        data = self.client.get(path)
        out = []
        for team in _as_list(data.get("team")):
            if not isinstance(team, dict):
                continue
            roster = team.get("roster")
            if not roster:
                continue
            entries = roster.get("players") if isinstance(roster, dict) else None
            for p in _as_list(entries):
                player = p.get("player", p)
                if isinstance(player, dict) and player.get("player_key"):
                    out.append(_player_summary(player))
        return out

    def free_agents(self, position: Optional[str] = None, count: int = 50,
                    status: str = "FA") -> List[Dict]:
        """Available players, ranked by Yahoo's own actual-rank ordering.

        status: 'FA' free agents, 'W' on waivers, 'A' all available.
        """
        path = (f"league/{self.league_key}/players;status={status};"
                f"sort=AR;start=0;count={min(count, 25)}")
        if position:
            path += f";position={position}"
        collected: List[Dict] = []
        start = 0
        while len(collected) < count:
            page = path.replace("start=0", f"start={start}")
            data = self.client.get(page)
            batch = []
            for lg in _as_list(data.get("league")):
                for p in _as_list(lg.get("players") if isinstance(lg, dict) else None):
                    player = p.get("player", p)
                    if isinstance(player, dict) and player.get("player_key"):
                        batch.append(_player_summary(player))
            if not batch:
                break
            collected.extend(batch)
            start += 25
        return collected[:count]

    def draft_results(self) -> List[Dict]:
        data = self.client.get(f"league/{self.league_key}/draftresults")
        out = []
        for lg in _as_list(data.get("league")):
            for d in _as_list(lg.get("draft_results") if isinstance(lg, dict) else None):
                r = d.get("draft_result", d)
                if isinstance(r, dict) and r.get("player_key"):
                    out.append(r)
        return out

    def transactions(self, count: int = 25) -> List[Dict]:
        data = self.client.get(
            f"league/{self.league_key}/transactions;count={count}")
        out = []
        for lg in _as_list(data.get("league")):
            for t in _as_list(lg.get("transactions") if isinstance(lg, dict) else None):
                tx = t.get("transaction", t)
                if isinstance(tx, dict):
                    out.append(tx)
        return out


def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _player_summary(player) -> Dict:
    """Reduce a Yahoo player blob to the fields the analysis layer needs."""
    if isinstance(player, list):
        # Defensive: collapse any residual nesting flatten() left behind.
        merged: Dict = {}
        for chunk in player:
            if isinstance(chunk, dict):
                merged.update(chunk)
        player = merged
    name = player.get("name")
    full = name.get("full") if isinstance(name, dict) else name
    elig = player.get("eligible_positions")
    if isinstance(elig, dict):
        elig = [elig.get("position")]
    elif isinstance(elig, list):
        elig = [e.get("position") if isinstance(e, dict) else e for e in elig]
    selected = player.get("selected_position")
    if isinstance(selected, dict):
        selected = selected.get("position")
    elif isinstance(selected, list) and selected:
        selected = next((s.get("position") for s in selected
                         if isinstance(s, dict) and s.get("position")), None)
    return {
        "player_key": player.get("player_key"),
        "player_id": player.get("player_id"),
        "name": full,
        "position": norm_pos(player.get("display_position")
                             or player.get("primary_position")),
        "team": norm_team(player.get("editorial_team_abbr")),
        "eligible_positions": [norm_pos(e) for e in (elig or []) if e],
        "selected_position": selected,
        "status": player.get("status_full") or player.get("status"),
        "injury_note": player.get("injury_note"),
        "bye_week": _bye(player),
        "percent_owned": _percent_owned(player),
        "uniform_number": player.get("uniform_number"),
    }


def _bye(player: Dict) -> Optional[int]:
    bye = player.get("bye_weeks")
    if isinstance(bye, dict):
        return _int(bye.get("week"))
    return None


def _percent_owned(player: Dict) -> Optional[float]:
    po = player.get("percent_owned")
    if isinstance(po, dict):
        try:
            return float(po.get("value"))
        except (TypeError, ValueError):
            return None
    return None
