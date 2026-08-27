"""Season projections.

Design note, stated plainly: a from-scratch statistical model does not beat
expert consensus. Published multi-year accuracy studies consistently show that
*averaged* projections outperform every individual source, including the
sources being averaged. So this module is built as an ensemble that is cheap to
add sources to, and ships with a free baseline rather than pretending the
baseline is best-in-class.

  BaselineProjector  -- recency-weighted historical production, regressed toward
                        a positional baseline by sample size, with an age curve
  MarketPrior        -- consensus draft-market ordering (free, via Sleeper),
                        which covers rookies and situation changes that history
                        cannot
  ensemble()         -- weighted blend of any providers, including paid ones

Add a paid source by producing a {player_key: points} mapping and passing it in.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd

from ..players import Player, PlayerUniverse
from ..util import norm_name, norm_pos
from .scoring import ScoringRules

GAMES_IN_SEASON = 17

# How much each prior season counts toward the historical estimate.
SEASON_WEIGHTS = (0.60, 0.28, 0.12)

# Shrinkage constant: a player needs ~K games before his own rate dominates the
# positional baseline. Tuned to be conservative -- small samples regress hard.
SHRINK_GAMES = 10.0

# Rough positional aging. Values multiply projected per-game output.
AGE_CURVES = {
    "RB": {21: 0.96, 22: 1.00, 23: 1.02, 24: 1.03, 25: 1.03, 26: 1.01,
           27: 0.98, 28: 0.94, 29: 0.89, 30: 0.83, 31: 0.76},
    "WR": {21: 0.90, 22: 0.95, 23: 1.00, 24: 1.03, 25: 1.05, 26: 1.05,
           27: 1.04, 28: 1.02, 29: 0.99, 30: 0.95, 31: 0.90, 32: 0.85},
    "TE": {22: 0.85, 23: 0.92, 24: 0.97, 25: 1.01, 26: 1.04, 27: 1.05,
           28: 1.04, 29: 1.02, 30: 0.99, 31: 0.95, 32: 0.90},
    "QB": {22: 0.94, 23: 0.97, 24: 1.00, 25: 1.02, 26: 1.03, 27: 1.03,
           28: 1.03, 29: 1.02, 30: 1.01, 31: 1.00, 32: 0.98, 33: 0.96},
}


@dataclass
class Projection:
    player: Player
    points: float                 # projected season total
    per_game: float
    games: float
    components: Dict[str, float] = field(default_factory=dict)
    basis: str = ""               # how this number was reached

    @property
    def key(self) -> Tuple[str, str]:
        return self.player.key


def _age_factor(position: Optional[str], age: Optional[float]) -> float:
    curve = AGE_CURVES.get(norm_pos(position) or "")
    if not curve or age is None:
        return 1.0
    ages = sorted(curve)
    a = int(round(age))
    if a <= ages[0]:
        return curve[ages[0]]
    if a >= ages[-1]:
        return curve[ages[-1]]
    return curve.get(a, 1.0)


class BaselineProjector:
    """Projects season points from recency-weighted historical production."""

    def __init__(self, weekly: pd.DataFrame, rules: ScoringRules,
                 seasons: Optional[List[int]] = None):
        self.rules = rules
        self.weekly = weekly
        self.seasons = sorted(seasons or weekly["season"].unique(), reverse=True)
        self._per_game: Optional[pd.DataFrame] = None

    def _compute_per_game(self) -> pd.DataFrame:
        """Per-player, per-season points-per-game under this league's rules."""
        df = self.weekly.copy()
        df["fp"] = df.apply(self.rules.score_row, axis=1)
        # A week with no offensive snaps is an absence, not a zero performance.
        played = df[(df.get("offense_snaps", pd.Series(1, index=df.index)).fillna(0) > 0)
                    | (df["fp"] != 0)]
        grouped = played.groupby(["player_display_name", "position", "season"], observed=True)
        out = grouped.agg(points=("fp", "sum"), games=("fp", "size")).reset_index()
        out["ppg"] = out["points"] / out["games"].clip(lower=1)
        return out

    @property
    def per_game(self) -> pd.DataFrame:
        if self._per_game is None:
            self._per_game = self._compute_per_game()
        return self._per_game

    def positional_baseline(self, position: str) -> float:
        """Points per game for a fringe-roster player at this position.

        Used as the shrinkage target: unproven players regress toward the level
        of a replacement, not toward the league average of established starters.
        """
        pg = self.per_game
        recent = pg[(pg["season"] == self.seasons[0]) & (pg["position"] == position)]
        recent = recent[recent["games"] >= 4]
        if recent.empty:
            return 0.0
        return float(recent["ppg"].quantile(0.25))

    def project(self, universe: PlayerUniverse,
                positions: Iterable[str] = ("QB", "RB", "WR", "TE")) -> Dict[Tuple[str, str], Projection]:
        pg = self.per_game
        baselines = {p: self.positional_baseline(p) for p in positions}
        # Index history by (normalized name, position) to match the universe.
        pg = pg.copy()
        pg["nkey"] = pg["player_display_name"].map(norm_name)
        history: Dict[Tuple[str, str], List[Tuple[int, float, int]]] = {}
        for row in pg.itertuples(index=False):
            history.setdefault((row.nkey, row.position), []).append(
                (int(row.season), float(row.ppg), int(row.games)))

        out: Dict[Tuple[str, str], Projection] = {}
        for player in universe.filter(positions=positions, rostered_only=True):
            key = player.key
            seasons = history.get(key, [])
            baseline = baselines.get(norm_pos(player.position) or "", 0.0)

            weighted_sum = 0.0
            weight_total = 0.0
            games_seen = 0.0
            for season, ppg, games in seasons:
                try:
                    idx = self.seasons.index(season)
                except ValueError:
                    continue
                if idx >= len(SEASON_WEIGHTS):
                    continue
                w = SEASON_WEIGHTS[idx] * min(games, GAMES_IN_SEASON)
                weighted_sum += ppg * w
                weight_total += w
                games_seen += games * SEASON_WEIGHTS[idx]

            if weight_total > 0:
                raw_ppg = weighted_sum / weight_total
                # Shrink toward the positional baseline by effective sample size.
                shrink = games_seen / (games_seen + SHRINK_GAMES)
                ppg_est = shrink * raw_ppg + (1 - shrink) * baseline
                basis = f"{len(seasons)}s/{games_seen:.0f}g"
            else:
                ppg_est = baseline
                basis = "no history"

            ppg_est *= _age_factor(player.position, player.age)

            # Expected games: injury-prone and backup roles cost availability.
            games = 16.0
            if player.injury_status in ("Out", "IR", "Doubtful"):
                games -= 3.0
            elif player.injury_status == "Questionable":
                games -= 0.5
            if player.depth_chart_order and player.depth_chart_order > 2:
                games -= 1.0

            out[key] = Projection(
                player=player,
                points=round(ppg_est * games, 2),
                per_game=round(ppg_est, 3),
                games=games,
                basis=basis,
            )
        return out


class MarketPrior:
    """Consensus draft-market ordering, free via Sleeper's relevance rank.

    History cannot see rookies, scheme changes, or a backfield that just opened
    up; the draft market can. This converts market rank into a points estimate
    calibrated to the historical projections it is blended with.
    """

    def __init__(self, universe: PlayerUniverse):
        self.universe = universe

    def project(self, historical: Mapping[Tuple[str, str], Projection],
                positions: Iterable[str] = ("QB", "RB", "WR", "TE")) -> Dict[Tuple[str, str], Projection]:
        pos_set = {norm_pos(p) for p in positions}
        ranked = [p for p in self.universe.filter(positions=positions, rostered_only=True)
                  if p.search_rank is not None]
        ranked.sort(key=lambda p: p.search_rank)

        # Calibrate: map market order onto the distribution of historical
        # projections at each position, so the two sources are commensurable.
        by_pos: Dict[str, List[float]] = {}
        for proj in historical.values():
            pos = norm_pos(proj.player.position)
            if pos in pos_set:
                by_pos.setdefault(pos, []).append(proj.points)
        for pos in by_pos:
            by_pos[pos].sort(reverse=True)

        counters: Dict[str, int] = {}
        out: Dict[Tuple[str, str], Projection] = {}
        for player in ranked:
            pos = norm_pos(player.position)
            curve = by_pos.get(pos or "")
            if not curve:
                continue
            i = counters.get(pos, 0)
            counters[pos] = i + 1
            points = curve[min(i, len(curve) - 1)]
            out[player.key] = Projection(
                player=player, points=round(points, 2),
                per_game=round(points / 16.0, 3), games=16.0,
                basis=f"market #{player.search_rank}",
            )
        return out


def ensemble(sources: Mapping[str, Mapping[Tuple[str, str], Projection]],
             weights: Optional[Mapping[str, float]] = None) -> Dict[Tuple[str, str], Projection]:
    """Blend projection sources. Missing players fall back to whoever has them.

    Weights are normalized per player across only the sources that actually
    cover him, so a player absent from one source is not silently penalized.
    """
    weights = weights or {name: 1.0 for name in sources}
    keys = set()
    for table in sources.values():
        keys.update(table.keys())

    blended: Dict[Tuple[str, str], Projection] = {}
    for key in keys:
        total = 0.0
        wsum = 0.0
        components: Dict[str, float] = {}
        player = None
        games = 16.0
        for name, table in sources.items():
            proj = table.get(key)
            if proj is None:
                continue
            w = float(weights.get(name, 1.0))
            if w <= 0:
                continue
            components[name] = proj.points
            total += proj.points * w
            wsum += w
            player = player or proj.player
            games = proj.games
        if player is None or wsum == 0:
            continue
        points = total / wsum
        blended[key] = Projection(
            player=player, points=round(points, 2),
            per_game=round(points / max(games, 1), 3), games=games,
            components=components,
            basis="+".join(sorted(components)),
        )
    return blended


class DefenseProjector:
    """Season projections for team defenses.

    Defenses are keyed by team abbreviation rather than by name, and they carry
    less signal year over year than any offensive position: turnovers in
    particular regress hard. Historical weighting is therefore flatter, and the
    result is shrunk further toward the league mean.
    """

    # Defense is noisy. Pull harder toward the league average than for skill
    # positions, where individual talent persists.
    DEFENSE_SHRINK = 0.55

    def __init__(self, team_weekly: pd.DataFrame, rules: ScoringRules,
                 seasons: Optional[List[int]] = None):
        self.rules = rules
        self.weekly = team_weekly
        self.seasons = sorted(seasons or team_weekly["season"].unique(), reverse=True)

    def project(self, universe: PlayerUniverse) -> Dict[Tuple[str, str], Projection]:
        if self.weekly.empty:
            return {}
        df = self.weekly.copy()
        df["fp"] = df.apply(self.rules.score_defense_row, axis=1)
        grouped = df.groupby(["team", "season"], observed=True)
        agg = grouped.agg(points=("fp", "sum"), games=("fp", "size")).reset_index()
        agg["ppg"] = agg["points"] / agg["games"].clip(lower=1)

        history: Dict[str, List[Tuple[int, float, int]]] = {}
        for row in agg.itertuples(index=False):
            history.setdefault(row.team, []).append(
                (int(row.season), float(row.ppg), int(row.games)))
        league_mean = float(agg["ppg"].mean()) if not agg.empty else 0.0

        out: Dict[Tuple[str, str], Projection] = {}
        for player in universe.filter(positions=["DST"], rostered_only=True):
            # Sleeper names defenses by city ("Denver Broncos"); the join key is
            # the team abbreviation.
            team = player.team or player.sleeper_id
            seasons = history.get(team, [])
            weighted, wtotal = 0.0, 0.0
            for season, ppg, games in seasons:
                try:
                    idx = self.seasons.index(season)
                except ValueError:
                    continue
                if idx >= len(SEASON_WEIGHTS):
                    continue
                w = SEASON_WEIGHTS[idx] * games
                weighted += ppg * w
                wtotal += w
            raw = weighted / wtotal if wtotal else league_mean
            ppg_est = (self.DEFENSE_SHRINK * raw
                       + (1 - self.DEFENSE_SHRINK) * league_mean)
            out[player.key] = Projection(
                player=player, points=round(ppg_est * 16.0, 2),
                per_game=round(ppg_est, 3), games=16.0,
                basis=f"def {len(seasons)}s" if seasons else "league mean",
            )
        return out
