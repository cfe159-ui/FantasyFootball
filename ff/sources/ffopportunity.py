"""ffopportunity: expected fantasy points modeled from opportunity.

Expected points answer "how many points *should* this player have scored, given
his carries, targets, air yards, and field position?" The gap between actual and
expected is the most reliable regression signal in fantasy -- a back who scored
on 40% of his red-zone carries will not do it again, and expected points say so
while raw production does not.

Free, no key, published on GitHub releases in the same pattern as nflverse.
"""
from __future__ import annotations

from typing import List

import pandas as pd

from .nflverse import _cached_parquet

RELEASE = "https://github.com/ffverse/ffopportunity/releases/download/latest-data"


def weekly_expected(seasons: List[int], regular_season_only: bool = True) -> pd.DataFrame:
    """Per-player, per-week actual and expected fantasy points."""
    frames = []
    for season in seasons:
        try:
            frames.append(_cached_parquet(
                f"{RELEASE}/ep_weekly_{season}.parquet",
                f"ffopp_weekly_{season}",
            ))
        except Exception:  # noqa: BLE001 - season not published yet
            continue
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    if regular_season_only and "week" in out.columns:
        out = out[out["week"] <= 18]
    return out
