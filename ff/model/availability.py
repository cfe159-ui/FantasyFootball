"""Will he play, and how well?

Every number here was measured from NFL injury reports joined against whether
the player actually recorded a stat line, 2023-2025, rather than assumed. The
headline result contradicts the usual rule of thumb: a Questionable player
appears only ~58% of the time, not the ~75% most managers assume, and produces
~93% of his own season average when he does.

Multiply those and a Questionable tag is worth about 56% of a projection in
expectation -- a far steeper discount than conventional advice applies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

from ..util import norm_name, norm_pos

# Measured appearance rate by (report_status, practice_status).
# Practice participation refines the report status meaningfully: a Questionable
# player who did not practice all week is a materially worse bet.
PLAY_RATE: Dict[Tuple[str, str], float] = {
    ("out", "*"): 0.00,
    ("doubtful", "*"): 0.00,
    ("questionable", "did not participate in practice"): 0.49,
    ("questionable", "limited participation in practice"): 0.60,
    ("questionable", "full participation in practice"): 0.58,
    ("questionable", "*"): 0.58,
    # These "(none)" rows describe players who ARE on the injury report with no
    # designation -- carrying a knock but not tagged. They are NOT the baseline
    # for a healthy player, who never appears on the report at all and is
    # treated as 1.0. Conflating the two would deflate every projection by 15%.
    ("(none)", "did not participate in practice"): 0.62,
    ("(none)", "limited participation in practice"): 0.89,
    ("(none)", "full participation in practice"): 0.87,
    ("(none)", "*"): 0.85,
}

# A player who does not appear on the injury report at all.
NOT_ON_REPORT = 1.0

# Output relative to the player's own season average, given that he played.
EFFECTIVENESS = {
    "questionable": 0.928 / 0.971,   # normalized against the healthy baseline
    "doubtful": 0.90,
    "out": 0.0,
    "(none)": 1.0,
}

# Sleeper's live status vocabulary, mapped onto report-status terms. Sleeper is
# the current-week source until nflverse publishes the season's injury reports.
SLEEPER_STATUS = {
    "out": "out",
    "ir": "out",
    "pup": "out",
    "sus": "out",
    "doubtful": "doubtful",
    "questionable": "questionable",
    "probable": "(none)",
    "healthy": "(none)",
    None: "(none)",
}


@dataclass
class Availability:
    play_probability: float
    effectiveness: float
    report_status: str
    practice_status: Optional[str] = None
    source: str = "sleeper"

    @property
    def multiplier(self) -> float:
        """Fraction of a full projection this player is worth in expectation."""
        return round(self.play_probability * self.effectiveness, 4)

    @property
    def is_out(self) -> bool:
        return self.play_probability <= 0.0

    def describe(self) -> str:
        if self.is_out:
            return "OUT"
        pct = int(round(self.multiplier * 100))
        if pct >= 95:
            return ""
        return f"{pct}% expected"


def _norm_status(value: Optional[str]) -> str:
    if not value:
        return "(none)"
    return str(value).strip().lower()


def lookup(report_status: Optional[str],
           practice_status: Optional[str] = None,
           on_report: Optional[bool] = None) -> Availability:
    """Expected availability from an injury report designation.

    `on_report` distinguishes a player absent from the injury report entirely
    (fully healthy) from one listed without a designation (carrying a knock).
    When not given, it is inferred: any status at all means he is on the report.
    """
    if on_report is None:
        on_report = bool(report_status) or bool(practice_status)
    if not on_report:
        return Availability(play_probability=NOT_ON_REPORT, effectiveness=1.0,
                            report_status="(none)", source="not on report")

    report = _norm_status(report_status)
    report = SLEEPER_STATUS.get(report, report)
    if report not in ("out", "doubtful", "questionable"):
        report = "(none)"
    practice = _norm_status(practice_status)

    rate = PLAY_RATE.get((report, practice))
    if rate is None:
        rate = PLAY_RATE.get((report, "*"), 0.85)
    effect = EFFECTIVENESS.get(report, 1.0)
    return Availability(play_probability=rate, effectiveness=effect,
                        report_status=report, practice_status=practice_status)


def for_player(player, injury_reports: Optional[Mapping] = None,
               week: Optional[int] = None) -> Availability:
    """Availability for a player, preferring the official report over Sleeper.

    The NFL injury report carries practice participation, which Sleeper does
    not; it is used whenever this week's report has been published.
    """
    if injury_reports and week is not None:
        key = (norm_name(player.name), norm_pos(player.position), week)
        report = injury_reports.get(key)
        if report:
            out = lookup(report.get("report_status"), report.get("practice_status"))
            out.source = "nfl injury report"
            return out
    return lookup(player.injury_status)


def build_report_index(injuries_df, season: int) -> Dict:
    """Index an nflverse injuries frame by (name, position, week)."""
    index: Dict = {}
    if injuries_df is None or getattr(injuries_df, "empty", True):
        return index
    df = injuries_df[injuries_df["season"] == season] if "season" in injuries_df else injuries_df
    for row in df.itertuples(index=False):
        key = (norm_name(getattr(row, "full_name", "")),
               norm_pos(getattr(row, "position", "")),
               int(getattr(row, "week", 0)))
        index[key] = {
            "report_status": getattr(row, "report_status", None),
            "practice_status": getattr(row, "practice_status", None),
            "injury": getattr(row, "report_primary_injury", None),
        }
    return index
