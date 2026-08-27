"""FantasyPros: expert consensus rankings, ADP, and projections.

Consensus of 130+ ranked experts. Accuracy studies keep finding that averaged
expert projections beat any individual model, which makes this the strongest
single input available -- and the reason the projection engine is an ensemble.

Requires a paid key (Premium/HOF, ~$9/month). The key is read from the
FANTASYPROS_API_KEY environment variable, normally set in .env, which is
gitignored. It is never written to disk by this module and never logged.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from ..util import cached

BASE = "https://api.fantasypros.com/public/v2/json"

# Rankings shift daily in preseason and hourly on game days.
RANKINGS_TTL = 3 * 3600

# FantasyPros scoring codes, keyed by points per reception.
SCORING_BY_PPR = {0.0: "STD", 0.5: "HALF", 1.0: "PPR"}


class FantasyProsError(RuntimeError):
    pass


def api_key(explicit: Optional[str] = None) -> Optional[str]:
    return explicit or os.environ.get("FANTASYPROS_API_KEY") or None


def scoring_code(ppr: Optional[float]) -> str:
    """Map a league's points-per-reception to FantasyPros' scoring code."""
    if ppr is None:
        return "PPR"
    return SCORING_BY_PPR.get(round(float(ppr) * 2) / 2, "PPR")


def _get(path: str, params: Dict[str, Any], key: Optional[str] = None,
         ttl: int = RANKINGS_TTL) -> Dict:
    token = api_key(key)
    if not token:
        raise FantasyProsError(
            "No FantasyPros API key. Add FANTASYPROS_API_KEY=... to .env "
            "(the file is gitignored)."
        )
    cache_key = "fp_" + path.strip("/").replace("/", "_") + "_" + "_".join(
        f"{k}{v}" for k, v in sorted(params.items()))

    def fetch() -> Dict:
        resp = requests.get(f"{BASE}/{path.lstrip('/')}", params=params,
                            headers={"x-api-key": token}, timeout=30)
        if resp.status_code in (401, 403):
            raise FantasyProsError(
                f"FantasyPros rejected the key ({resp.status_code}). Check that the "
                "key is active and that your subscription includes API access."
            )
        if resp.status_code == 429:
            raise FantasyProsError("FantasyPros rate limit hit; try again shortly.")
        resp.raise_for_status()
        return resp.json()

    return cached(cache_key, ttl, fetch)


def consensus_rankings(season: int, position: str = "ALL", scoring: str = "PPR",
                       week: Optional[int] = None, key: Optional[str] = None) -> Dict:
    """Expert consensus rankings (ECR) with tiers and spread of opinion."""
    params: Dict[str, Any] = {"position": position, "scoring": scoring}
    if week is not None:
        params["week"] = week
    return _get(f"nfl/{season}/consensus-rankings", params, key)


def projections(season: int, position: str = "ALL", scoring: str = "PPR",
                week: Optional[int] = None, key: Optional[str] = None) -> Dict:
    """Projected stat lines. Endpoint shape confirmed by `ff fp-check`."""
    params: Dict[str, Any] = {"position": position, "scoring": scoring}
    if week is not None:
        params["week"] = week
    return _get(f"nfl/{season}/projections", params, key)


def parse_players(payload: Dict) -> List[Dict]:
    """Pull the player list out of a FantasyPros response.

    The wrapper key varies by endpoint, so find the first list of dicts that
    looks like players rather than hardcoding a shape.
    """
    if not isinstance(payload, dict):
        return []
    for candidate in ("players", "data", "results", "rankings"):
        value = payload.get(candidate)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if any(k in value[0] for k in ("player_name", "name", "player_id")):
                return value
    return []


def probe(season: int, key: Optional[str] = None) -> List[Dict]:
    """Try each endpoint and report what the key actually unlocks.

    Subscription tiers differ in what they expose, so rather than assuming,
    this reports per-endpoint status and the field names that came back.
    """
    results = []
    attempts = [
        ("consensus-rankings", lambda: consensus_rankings(season, "ALL", "PPR", key=key)),
        ("consensus-rankings (RB, HALF)",
         lambda: consensus_rankings(season, "RB", "HALF", key=key)),
        ("projections", lambda: projections(season, "ALL", "PPR", key=key)),
        ("projections (week 1)",
         lambda: projections(season, "ALL", "PPR", week=1, key=key)),
    ]
    for label, call in attempts:
        try:
            payload = call()
            players = parse_players(payload)
            results.append({
                "endpoint": label,
                "ok": True,
                "count": len(players),
                "top_keys": sorted(players[0].keys())[:14] if players else [],
                "wrapper_keys": sorted(payload.keys())[:10] if isinstance(payload, dict) else [],
            })
        except Exception as exc:  # noqa: BLE001 - report, do not raise
            results.append({"endpoint": label, "ok": False, "error": str(exc)[:180]})
    return results


# FantasyPros stat keys -> our canonical vocabulary.
FP_STAT_MAPPING = {
    "pass_yds": "pass_yd", "pass_tds": "pass_td", "pass_ints": "pass_int",
    "rush_yds": "rush_yd", "rush_tds": "rush_td",
    "rec_rec": "rec", "rec_yds": "rec_yd", "rec_tds": "rec_td",
    "fumbles": "fum_lost", "ret_tds": "ret_td", "2pt_tds": "rush_2pt",
    "xpt": "pat_made",
    "def_sack": "def_sack", "def_int": "def_int", "def_fr": "def_fum_rec",
    "def_td": "def_td", "def_safety": "def_safety", "def_retd": "def_ret_td",
}

# Defensive points-allowed buckets, in tier order: 0, 1-6, 7-13, 14-20,
# 21-27, 28-34, 35+. Values are expected games landing in each bucket.
PA_BUCKETS = ("def_pa_a", "def_pa_b", "def_pa_c", "def_pa_d",
              "def_pa_e", "def_pa_f", "def_pa_g")

# FantasyPros reports field goals as a single total with no distance split,
# so distance-banded scoring has to be approximated. This is the points-per-FG
# a typical distribution of attempts yields under Yahoo's default bands.
BLENDED_FG_VALUE = 3.5


def score_projection(stats: Dict[str, Any], rules, position: Optional[str] = None) -> float:
    """Score a FantasyPros stat line under YOUR league's rules.

    The API echoes back its own scoring regardless of the requested setting, so
    the stat line is rescored locally rather than trusting its points field.
    """
    total = 0.0
    for fp_key, canonical in FP_STAT_MAPPING.items():
        value = stats.get(fp_key)
        if value is None:
            continue
        try:
            total += rules.values.get(canonical, 0.0) * float(value)
        except (TypeError, ValueError):
            continue

    if position == "K":
        try:
            total += float(stats.get("fg") or 0) * BLENDED_FG_VALUE
        except (TypeError, ValueError):
            pass

    if position == "DST":
        for bucket, (_, _, tier_value) in zip(PA_BUCKETS, rules.pa_tiers):
            try:
                total += float(stats.get(bucket) or 0) * float(tier_value)
            except (TypeError, ValueError):
                continue

    return total


RANKING_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")


def all_consensus_rankings(season: int, scoring: str = "PPR",
                           week: Optional[int] = None,
                           key: Optional[str] = None) -> List[Dict]:
    """Consensus rankings across every position.

    The API rejects position=ALL with a 400, so each position is fetched
    separately and the results concatenated.
    """
    out: List[Dict] = []
    for position in RANKING_POSITIONS:
        try:
            payload = consensus_rankings(season, position, scoring, week, key)
        except Exception:  # noqa: BLE001 - one bad position must not kill the rest
            continue
        for entry in parse_players(payload):
            entry.setdefault("player_position_id", position)
            out.append(entry)
    return out


def _to_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def yahoo_ownership(season: int, scoring: str = "PPR",
                    key: Optional[str] = None) -> Dict[Tuple[str, str], float]:
    """Percentage of Yahoo leagues in which each player is rostered.

    This is the real thing the waiver module needs: who is actually available
    in a Yahoo league, rather than an estimate from consensus relevance rank.
    """
    from ..util import norm_name, norm_pos

    out: Dict[Tuple[str, str], float] = {}
    for entry in all_consensus_rankings(season, scoring, key=key):
        name = entry.get("player_name")
        pos = norm_pos(entry.get("player_position_id"))
        owned = _to_float(entry.get("player_owned_yahoo"))
        if name and pos and owned is not None:
            out[(norm_name(name), pos)] = owned
    return out


def consensus_ecr(season: int, scoring: str = "PPR",
                  key: Optional[str] = None) -> Dict[Tuple[str, str], float]:
    """Expert consensus rank per player, for use as a market prior."""
    from ..util import norm_name, norm_pos

    out: Dict[Tuple[str, str], float] = {}
    for entry in all_consensus_rankings(season, scoring, key=key):
        name = entry.get("player_name")
        pos = norm_pos(entry.get("player_position_id"))
        ecr = _to_float(entry.get("rank_ecr") or entry.get("player_ecr"))
        if name and pos and ecr is not None:
            out[(norm_name(name), pos)] = ecr
    return out
