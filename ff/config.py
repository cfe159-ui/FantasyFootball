"""Local, non-secret configuration (which league you're managing)."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from .util import DATA_DIR

CONFIG_PATH = DATA_DIR / "config.json"


def load() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


def set_(key: str, value: Any) -> None:
    cfg = load()
    cfg[key] = value
    save(cfg)


def league_key() -> Optional[str]:
    """Env var wins, so a shell can override the saved default."""
    return os.environ.get("FF_LEAGUE_KEY") or get("league_key")
