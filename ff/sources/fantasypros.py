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
