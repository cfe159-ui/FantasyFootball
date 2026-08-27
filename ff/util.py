"""Shared utilities: name normalization, on-disk caching, HTTP with retries."""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import warnings
from pathlib import Path
from typing import Any, Callable, Optional

# macOS system Python ships LibreSSL; urllib3 v2 warns loudly and harmlessly.
# Must be registered before urllib3 is imported (via requests) to take effect.
warnings.filterwarnings("ignore", message=".*OpenSSL.*")
warnings.filterwarnings("ignore", category=Warning, module="urllib3")

import requests  # noqa: E402

DATA_DIR = Path(os.environ.get("FF_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
CACHE_DIR = DATA_DIR / "cache"

# Suffixes and punctuation that differ between data providers for the same human.
_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
_NON_ALPHA = re.compile(r"[^a-z]")

# Providers disagree on team abbreviations; normalize to nflverse convention.
TEAM_ALIASES = {
    "JAC": "JAX", "WSH": "WAS", "WFT": "WAS", "LA": "LAR", "STL": "LAR",
    "SD": "LAC", "OAK": "LV", "LVR": "LV", "ARZ": "ARI", "BLT": "BAL",
    "CLV": "CLE", "HST": "HOU", "SL": "LAR", "KCC": "KC", "TAM": "TB",
    "NWE": "NE", "NOR": "NO", "SFO": "SF", "GNB": "GB",
}

# Yahoo uses DEF, Sleeper uses DEF, nflverse uses DST. Canonical: DST.
POSITION_ALIASES = {"DEF": "DST", "D/ST": "DST", "PK": "K", "FB": "RB"}


def norm_name(name: Optional[str]) -> str:
    """Collapse a player name to a provider-independent key.

    'Ja'Marr Chase' -> 'jamarrchase';  'Michael Pittman Jr.' -> 'michaelpittman'
    Verified to produce zero collisions across active NFL skill players when
    paired with position.
    """
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower().replace(".", " ").replace("'", "").replace("-", " ")
    name = _SUFFIXES.sub("", name)
    return _NON_ALPHA.sub("", name)


def norm_team(team: Optional[str]) -> Optional[str]:
    if not team:
        return None
    t = team.strip().upper()
    return TEAM_ALIASES.get(t, t)


def norm_pos(pos: Optional[str]) -> Optional[str]:
    if not pos:
        return None
    p = pos.strip().upper()
    return POSITION_ALIASES.get(p, p)


def cached(key: str, ttl_seconds: int, producer: Callable[[], Any]) -> Any:
    """Disk-cache a JSON-serializable value.

    Keeps us well inside provider rate limits and makes the tooling usable
    offline mid-draft if the network hiccups.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl_seconds:
        try:
            with path.open() as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass  # Corrupt or truncated cache: fall through and refetch.
    value = producer()
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as fh:
        json.dump(value, fh)
    tmp.replace(path)  # Atomic: never leave a half-written cache behind.
    return value


def get_json(url: str, *, params: Optional[dict] = None, headers: Optional[dict] = None,
             timeout: int = 30, retries: int = 3) -> Any:
    """GET with exponential backoff. Retries transient failures and 429/5xx."""
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{resp.status_code} from {url}")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - retry any transport failure
            last_error = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed after {retries} attempts: {url}") from last_error
