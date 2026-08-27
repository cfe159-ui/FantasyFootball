"""Team and quarterback context adjustments.

Measured, not assumed. Across 875 player-seasons (2023-2025), comparing actual
fantasy points to ffopportunity's expected points -- which already controls for
opportunity, so what remains is efficiency:

    team quality (points per game), top vs bottom quartile
        RB +10.8%   WR +8.7%   TE +2.3%

    quarterback quality (passing EPA per attempt), best vs worst quartile
        WR +11.8%   RB +9.5%   TE +3.5%

Two findings worth stating. First, the "bad teams throw more so their receivers
eat" theory does not survive contact with the data: good teams gave skill
players BOTH more opportunity (WR +25%, RB +20% expected points) and better
efficiency. Second, quarterback quality matters more to receivers than general
team quality does, which is why the two are modelled separately.

Sizing: coefficients below are per standard deviation, roughly the measured
quartile spread divided by the ~2.5 SD separating those quartiles. They are then
scaled by `strength`, which defaults to 0.5 -- because expert consensus already
prices some of this, and applying the full measured effect on top would double
count it. This is a tilt, not a re-projection.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple

from ..util import norm_pos, norm_team

# Effect per standard deviation of team implied total.
TEAM_COEF = {"RB": 0.043, "WR": 0.035, "TE": 0.009, "QB": 0.020,
             "K": 0.030, "DST": 0.000}

# Effect per standard deviation of quarterback quality. Receivers gain most.
QB_COEF = {"WR": 0.047, "RB": 0.038, "TE": 0.014, "QB": 0.000,
           "K": 0.010, "DST": 0.000}

# A tilt should never become a re-projection.
MIN_MULTIPLIER = 0.92
MAX_MULTIPLIER = 1.08


@dataclass
class TeamContext:
    team: str
    implied_total: float
    projected_wins: float
    team_z: float
    qb_z: float
    games_priced: int = 0


def _zscores(values: Mapping[str, float]) -> Dict[str, float]:
    if len(values) < 2:
        return {k: 0.0 for k in values}
    nums = list(values.values())
    mean = statistics.fmean(nums)
    sd = statistics.pstdev(nums)
    if sd == 0:
        return {k: 0.0 for k in values}
    return {k: (v - mean) / sd for k, v in values.items()}


def build_contexts(outlooks: Mapping[str, object],
                   qb_points_by_team: Mapping[str, float]) -> Dict[str, TeamContext]:
    """Combine market team strength with projected quarterback quality.

    Quarterback quality comes from this engine's own QB projections rather than
    a new data source: the best available estimate of how good each starter is.
    """
    implied = {norm_team(t): float(getattr(o, "implied_total", 0.0))
               for t, o in outlooks.items()}
    wins = {norm_team(t): float(getattr(o, "projected_wins", 0.0))
            for t, o in outlooks.items()}
    priced = {norm_team(t): int(getattr(o, "games_priced", 0))
              for t, o in outlooks.items()}
    qb = {norm_team(t): float(v) for t, v in qb_points_by_team.items()}

    team_z = _zscores(implied)
    qb_z = _zscores(qb)

    out: Dict[str, TeamContext] = {}
    for team in implied:
        out[team] = TeamContext(
            team=team,
            implied_total=implied.get(team, 0.0),
            projected_wins=wins.get(team, 0.0),
            team_z=round(team_z.get(team, 0.0), 3),
            qb_z=round(qb_z.get(team, 0.0), 3),
            games_priced=priced.get(team, 0),
        )
    return out


def multiplier(position: Optional[str], context: Optional[TeamContext],
               strength: float = 0.5) -> float:
    """Adjustment factor for one player, clamped to a modest range."""
    if context is None or strength <= 0:
        return 1.0
    pos = norm_pos(position) or ""
    tilt = (TEAM_COEF.get(pos, 0.0) * context.team_z
            + QB_COEF.get(pos, 0.0) * context.qb_z)
    return max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, 1.0 + strength * tilt))


def apply(projections: Dict, contexts: Mapping[str, TeamContext],
          strength: float = 0.5) -> Dict:
    """Tilt every projection by its player's team and quarterback context."""
    if strength <= 0:
        return projections
    for key, proj in projections.items():
        team = norm_team(proj.player.team)
        factor = multiplier(proj.player.position, contexts.get(team or ""), strength)
        if factor == 1.0:
            continue
        proj.points = round(proj.points * factor, 2)
        proj.per_game = round(proj.per_game * factor, 3)
        proj.components["context"] = round(factor, 4)
    return projections
