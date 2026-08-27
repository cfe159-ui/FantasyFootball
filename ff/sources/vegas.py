"""Team strength from betting markets.

nflverse ships each scheduled game with a spread and a total, which is Vegas
data for free -- no odds API subscription. Two things are derived here:

  implied team total  -- projected points for a team in a game, the single best
                         market read on offensive strength
  projected wins      -- summed win probability across scheduled games

Only games with posted lines contribute; early in a season that is a partial
schedule, so projected wins are extrapolated and flagged by coverage.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from .nflverse import schedules

# Standard deviation of NFL game margins. Converts a spread into a win
# probability via the normal CDF.
MARGIN_SD = 13.45

GAMES_IN_SEASON = 17


@dataclass
class TeamOutlook:
    team: str
    implied_total: float          # average projected points per game
    projected_wins: float
    games_priced: int

    @property
    def coverage(self) -> float:
        return self.games_priced / GAMES_IN_SEASON


def _win_probability(spread: float) -> float:
    """Spread from this team's perspective; negative means favoured."""
    return 0.5 * (1.0 + math.erf(-spread / (MARGIN_SD * math.sqrt(2.0))))


def team_outlooks(season: int) -> Dict[str, TeamOutlook]:
    """Implied points per game and projected wins for every team."""
    games = schedules()
    games = games[(games["season"] == season) & (games["game_type"] == "REG")]
    games = games.dropna(subset=["spread_line", "total_line"])
    if games.empty:
        return {}

    rows: List[Dict] = []
    for g in games.itertuples(index=False):
        spread = float(g.spread_line)   # positive favours the home team
        total = float(g.total_line)
        # Implied totals split the game total by the spread.
        home_total = total / 2.0 + spread / 2.0
        away_total = total / 2.0 - spread / 2.0
        rows.append({"team": g.home_team, "implied": home_total,
                     "win_prob": _win_probability(-spread)})
        rows.append({"team": g.away_team, "implied": away_total,
                     "win_prob": _win_probability(spread)})

    df = pd.DataFrame(rows)
    agg = df.groupby("team").agg(implied=("implied", "mean"),
                                 win_rate=("win_prob", "mean"),
                                 n=("implied", "size")).reset_index()
    out: Dict[str, TeamOutlook] = {}
    for r in agg.itertuples(index=False):
        out[r.team] = TeamOutlook(
            team=r.team,
            implied_total=round(float(r.implied), 2),
            # Extrapolate the priced games' win rate across a full season.
            projected_wins=round(float(r.win_rate) * GAMES_IN_SEASON, 1),
            games_priced=int(r.n),
        )
    return out
