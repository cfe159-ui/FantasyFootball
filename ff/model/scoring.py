"""League scoring: turn a stat line into fantasy points.

Every downstream number -- projections, VOR, the draft board -- is only as
league-specific as this module. When Yahoo credentials are available the rules
are read from your actual league settings; otherwise a named preset is used.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional

# Canonical stat vocabulary. nflverse column names on the left of MAPPING.
CANONICAL = (
    "pass_yd", "pass_td", "pass_int", "pass_2pt",
    "rush_yd", "rush_td", "rush_2pt",
    "rec", "rec_yd", "rec_td", "rec_2pt",
    "fum_lost", "ret_td",
    # Kicking: field goals score by distance, so each band is its own stat.
    "fg_0_19", "fg_20_29", "fg_30_39", "fg_40_49", "fg_50_59", "fg_60",
    "fg_missed", "pat_made", "pat_missed",
    # Team defense. Points allowed is scored by tier, not per point, and is
    # handled separately in points_allowed_score().
    "def_sack", "def_int", "def_fum_rec", "def_td", "def_safety", "def_block",
    "def_ret_td",
)

# nflverse column -> canonical stat
NFLVERSE_MAPPING = {
    "passing_yards": "pass_yd",
    "passing_tds": "pass_td",
    "passing_interceptions": "pass_int",
    "interceptions": "pass_int",
    "passing_2pt_conversions": "pass_2pt",
    "rushing_yards": "rush_yd",
    "rushing_tds": "rush_td",
    "rushing_2pt_conversions": "rush_2pt",
    "receptions": "rec",
    "receiving_yards": "rec_yd",
    "receiving_tds": "rec_td",
    "receiving_2pt_conversions": "rec_2pt",
    "rushing_fumbles_lost": "fum_lost",
    "receiving_fumbles_lost": "fum_lost",
    "sack_fumbles_lost": "fum_lost",
    "special_teams_tds": "ret_td",
    # Kicking
    "fg_made_0_19": "fg_0_19",
    "fg_made_20_29": "fg_20_29",
    "fg_made_30_39": "fg_30_39",
    "fg_made_40_49": "fg_40_49",
    "fg_made_50_59": "fg_50_59",
    "fg_made_60_": "fg_60",
    "fg_missed": "fg_missed",
    "pat_made": "pat_made",
    "pat_missed": "pat_missed",
    # Team defense
    "def_sacks": "def_sack",
    "def_interceptions": "def_int",
    "def_fumbles": "def_fum_rec",
    "def_tds": "def_td",
    "def_safeties": "def_safety",
    "def_punt_blocks": "def_block",
    "def_pat_blocks": "def_block",
    "def_fg_blocks": "def_block",
}

# Team-defense columns only. The team stats frame also carries that team's
# OFFENSIVE production, so scoring a defense with the full mapping would credit
# it for its own quarterback -- worth ~100 fantasy points per game.
DEFENSE_MAPPING = {
    "def_sacks": "def_sack",
    "def_interceptions": "def_int",
    "def_fumbles": "def_fum_rec",
    "def_tds": "def_td",
    "def_safeties": "def_safety",
    "def_punt_blocks": "def_block",
    "def_pat_blocks": "def_block",
    "def_fg_blocks": "def_block",
}

# Yahoo's default points-allowed tiers for team defense. Non-linear, so this is
# a lookup rather than a multiplier: the difference between a shutout and
# allowing 35 is 14 points, and no per-point rate reproduces that shape.
DEFAULT_PA_TIERS = (
    (0, 0, 10.0),
    (1, 6, 7.0),
    (7, 13, 4.0),
    (14, 20, 1.0),
    (21, 27, 0.0),
    (28, 34, -1.0),
    (35, 99, -4.0),
)

# Yahoo stat display names are terse and inconsistently punctuated; match on a
# normalized form so "Pass Yds", "Passing Yards" and "PassYds" all resolve.
YAHOO_NAME_PATTERNS = (
    (r"^pass(ing)?yds?$|^passingyards$", "pass_yd"),
    (r"^pass(ing)?td$", "pass_td"),
    (r"^int$|^interceptions?$", "pass_int"),
    (r"^rush(ing)?yds?$|^rushingyards$", "rush_yd"),
    (r"^rush(ing)?td$", "rush_td"),
    (r"^rec$|^receptions?$", "rec"),
    (r"^rec(eiving)?yds?$|^receivingyards$", "rec_yd"),
    (r"^rec(eiving)?td$", "rec_td"),
    (r"^rettd$|^returntd$", "ret_td"),
    (r"^2ptp?$|^2ptconversions?$", "rush_2pt"),
    (r"^fumlost$|^fumbleslost$", "fum_lost"),
    (r"^fg0?19$|^fgmade019$", "fg_0_19"),
    (r"^fg2029$", "fg_20_29"),
    (r"^fg3039$", "fg_30_39"),
    (r"^fg4049$", "fg_40_49"),
    (r"^fg5059$|^fg50$", "fg_50_59"),
    (r"^fg60$", "fg_60"),
    (r"^fgmiss$|^fgmissed$", "fg_missed"),
    (r"^pat$|^patmade$|^xp$|^xpmade$", "pat_made"),
    (r"^patmiss$|^patmissed$|^xpmissed$", "pat_missed"),
    (r"^sack$|^sacks$", "def_sack"),
    (r"^fumrec$|^fumblerecovery$|^fumblesrecovered$", "def_fum_rec"),
    (r"^td$|^deftd$|^touchdowns?$", "def_td"),
    (r"^saf$|^safe$|^safety$|^safeties$", "def_safety"),
    (r"^blk$|^blockkick$|^blockedkick$", "def_block"),
)

PRESETS: Dict[str, Dict[str, float]] = {
    "standard": {
        "pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "pass_2pt": 2,
        "rush_yd": 0.1, "rush_td": 6, "rush_2pt": 2,
        "rec": 0.0, "rec_yd": 0.1, "rec_td": 6, "rec_2pt": 2,
        "fum_lost": -2, "ret_td": 6,
        # Kicking (Yahoo defaults)
        "fg_0_19": 3, "fg_20_29": 3, "fg_30_39": 3, "fg_40_49": 4,
        "fg_50_59": 5, "fg_60": 5, "fg_missed": -1,
        "pat_made": 1, "pat_missed": -1,
        # Team defense (Yahoo defaults)
        "def_sack": 1, "def_int": 2, "def_fum_rec": 2, "def_td": 6,
        "def_safety": 2, "def_block": 2, "def_ret_td": 6,
    },
    "half_ppr": {},   # filled below
    "ppr": {},        # filled below
}
PRESETS["half_ppr"] = dict(PRESETS["standard"], rec=0.5)
PRESETS["ppr"] = dict(PRESETS["standard"], rec=1.0)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


@dataclass
class ScoringRules:
    """Point values per canonical stat."""
    values: Dict[str, float] = field(default_factory=dict)
    source: str = "preset"
    name: str = "ppr"
    pa_tiers: tuple = DEFAULT_PA_TIERS

    @classmethod
    def preset(cls, name: str = "ppr") -> "ScoringRules":
        if name not in PRESETS:
            raise ValueError(f"unknown preset '{name}'; try {sorted(PRESETS)}")
        return cls(values=dict(PRESETS[name]), source="preset", name=name)

    @classmethod
    def from_yahoo(cls, settings) -> "ScoringRules":
        """Build rules from a LeagueSettings read off the Yahoo API.

        Falls back to the closest preset for any category Yahoo exposes that we
        do not model, so a league is never silently scored as zero.
        """
        values: Dict[str, float] = {}
        for stat_id, modifier in settings.stat_modifiers.items():
            display = _norm(settings.stat_names.get(stat_id, ""))
            for pattern, canonical in YAHOO_NAME_PATTERNS:
                if re.match(pattern, display):
                    values[canonical] = float(modifier)
                    break
        # Anything the league did not define keeps the standard default rather
        # than scoring zero -- except receptions, where 0 is a real setting.
        merged = dict(PRESETS["standard"])
        merged.update(values)
        if "rec" in values:
            merged["rec"] = values["rec"]
        return cls(values=merged, source="yahoo", name=getattr(settings, "name", "league"))

    @property
    def ppr(self) -> float:
        return self.values.get("rec", 0.0)

    def points_allowed_score(self, points_allowed: Optional[float]) -> float:
        """Score a team defense's points allowed against the league's tiers."""
        if points_allowed is None or points_allowed != points_allowed:
            return 0.0
        pa = int(points_allowed)
        for low, high, value in self.pa_tiers:
            if low <= pa <= high:
                return float(value)
        return 0.0

    def points(self, stats: Mapping[str, float]) -> float:
        """Score a canonical stat line."""
        return sum(self.values.get(k, 0.0) * float(v or 0)
                   for k, v in stats.items() if k in self.values)

    def score_defense_row(self, row: Mapping) -> float:
        """Score a team-defense row, ignoring that team's offensive production."""
        total = 0.0
        for col, canonical in DEFENSE_MAPPING.items():
            if col in row:
                value = row[col]
                if value is not None and value == value:
                    total += self.values.get(canonical, 0.0) * float(value)
        if "points_allowed" in row:
            total += self.points_allowed_score(row["points_allowed"])
        return total

    def score_row(self, row: Mapping) -> float:
        """Score a raw nflverse stats row.

        Handles offence, kicking, and team defense. A row carrying
        `points_allowed` is treated as a defense and scored against the tiers.
        """
        total = 0.0
        for col, canonical in NFLVERSE_MAPPING.items():
            if col in row:
                value = row[col]
                if value is not None and value == value:  # not NaN
                    total += self.values.get(canonical, 0.0) * float(value)
        if "points_allowed" in row:
            total += self.points_allowed_score(row["points_allowed"])
        return total

    def describe(self) -> str:
        ppr = self.ppr
        label = {0.0: "standard", 0.5: "half-PPR", 1.0: "full PPR"}.get(ppr, f"{ppr}/rec")
        return f"{label} (from {self.source})"
