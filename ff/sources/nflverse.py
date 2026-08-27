"""nflverse historical NFL data.

The `nflreadpy` package requires Python >= 3.10, so this reads nflverse's
published parquet releases directly. Same data, no version constraint.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

import pandas as pd

from ..util import CACHE_DIR

RELEASE = "https://github.com/nflverse/nflverse-data/releases/download"

# Parquet files are a few MB and rebuilt nightly in season.
PARQUET_TTL = 12 * 3600


def _cached_parquet(url: str, key: str, ttl: int = PARQUET_TTL) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.parquet"
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            return pd.read_parquet(path)
        except Exception:  # noqa: BLE001 - corrupt cache, refetch
            pass
    df = pd.read_parquet(url)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp)
    tmp.replace(path)
    return df


def player_week_stats(seasons: List[int], regular_season_only: bool = True) -> pd.DataFrame:
    """Weekly per-player statistics for the given seasons."""
    frames = []
    for season in seasons:
        try:
            df = _cached_parquet(
                f"{RELEASE}/stats_player/stats_player_week_{season}.parquet",
                f"nflverse_stats_player_week_{season}",
            )
        except Exception:  # noqa: BLE001 - season not published yet
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    if regular_season_only and "season_type" in out.columns:
        out = out[out["season_type"] == "REG"]
    return out


def schedules() -> pd.DataFrame:
    """Game schedule for every season, used to derive bye weeks."""
    return _cached_parquet(f"{RELEASE}/schedules/games.parquet", "nflverse_games")


def bye_weeks(season: int) -> dict:
    """Map team abbreviation -> bye week for a season."""
    games = schedules()
    games = games[(games["season"] == season) & (games["game_type"] == "REG")]
    if games.empty:
        return {}
    teams = set(games["home_team"]) | set(games["away_team"])
    all_weeks = set(games["week"].unique())
    out = {}
    for team in teams:
        played = set(games.loc[(games["home_team"] == team) |
                               (games["away_team"] == team), "week"])
        missing = sorted(all_weeks - played)
        if len(missing) == 1:
            out[team] = int(missing[0])
    return out


def snap_counts(seasons: List[int]) -> pd.DataFrame:
    """Offensive snap counts and share -- the cleanest available opportunity signal."""
    frames = []
    for season in seasons:
        try:
            frames.append(_cached_parquet(
                f"{RELEASE}/snap_counts/snap_counts_{season}.parquet",
                f"nflverse_snaps_{season}",
            ))
        except Exception:  # noqa: BLE001
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
