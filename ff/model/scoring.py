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
}

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
)

PRESETS: Dict[str, Dict[str, float]] = {
    "standard": {
        "pass_yd": 0.04, "pass_td": 4, "pass_int": -1, "pass_2pt": 2,
        "rush_yd": 0.1, "rush_td": 6, "rush_2pt": 2,
        "rec": 0.0, "rec_yd": 0.1, "rec_td": 6, "rec_2pt": 2,
        "fum_lost": -2, "ret_td": 6,
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

    def points(self, stats: Mapping[str, float]) -> float:
        """Score a canonical stat line."""
        return sum(self.values.get(k, 0.0) * float(v or 0)
                   for k, v in stats.items() if k in self.values)

    def score_row(self, row: Mapping) -> float:
        """Score a raw nflverse stats row."""
        total = 0.0
        for col, canonical in NFLVERSE_MAPPING.items():
            if col in row:
                value = row[col]
                if value is not None and value == value:  # not NaN
                    total += self.values.get(canonical, 0.0) * float(value)
        return total

    def describe(self) -> str:
        ppr = self.ppr
        label = {0.0: "standard", 0.5: "half-PPR", 1.0: "full PPR"}.get(ppr, f"{ppr}/rec")
        return f"{label} (from {self.source})"
