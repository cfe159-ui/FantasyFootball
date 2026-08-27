"""Yahoo Fantasy Sports API v2 client.

Yahoo's API is READ-ONLY as of the 2026 portal -- write access is not granted
by default -- so this client never mutates your team. It supplies league state
(settings, rosters, matchups, free agents, draft results); all analysis runs on
top of it.

Access requires a reviewed application at https://sports.yahoo.com/developer/access/
followed by self-serve app registration for the client key/secret.

The API returns XML-shaped JSON: collections arrive as objects keyed "0", "1",
... alongside a "count", and entity metadata arrives as arrays of single-key
dicts. `flatten()` normalizes both into ordinary Python structures.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from ..util import DATA_DIR, norm_pos, norm_team

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

TOKEN_PATH = DATA_DIR / "yahoo_token.json"
DEFAULT_REDIRECT_PORT = 8723


# --------------------------------------------------------------------------
# Response normalization
# --------------------------------------------------------------------------

def flatten(node: Any) -> Any:
    """Normalize Yahoo's XML-derived JSON into plain dicts and lists.

    Handles the two shapes Yahoo uses:
      {"0": {...}, "1": {...}, "count": 2}  ->  [{...}, {...}]
      [{"player_key": x}, {"name": {...}}, []]  ->  {"player_key": x, "name": {...}}
    """
    if isinstance(node, dict):
        # Index-keyed collection -> list, preserving numeric order.
        idx_keys = [k for k in node if k.isdigit()]
        if idx_keys and ("count" in node or len(idx_keys) == len(node)):
            return [flatten(node[k]) for k in sorted(idx_keys, key=int)]
        return {k: flatten(v) for k, v in node.items() if k != "count"}

    if isinstance(node, list):
        # An array of single-key dicts is really one entity: merge it.
        if node and all(isinstance(i, (dict, list)) for i in node):
            merged: Dict[str, Any] = {}
            leftovers: List[Any] = []
            repeated = False
            for item in node:
                if isinstance(item, dict):
                    for k, v in item.items():
                        if k == "count":
                            continue
                        if k in merged:
                            # The same key twice means this is a real collection
                            # (e.g. eligible_positions: [{position: WR},
                            # {position: W/R/T}]), not one entity split across
                            # single-key dicts. Merging would silently drop
                            # every value but the last.
                            repeated = True
                            break
                        merged[k] = flatten(v)
                elif isinstance(item, list) and item:
                    leftovers.append(flatten(item))
                if repeated:
                    break
            if repeated:
                return [flatten(i) for i in node]
            if merged:
                for extra in leftovers:
                    if isinstance(extra, dict):
                        merged.update(extra)
                    else:
                        merged.setdefault("_items", []).append(extra)
                return merged
            # A lone nested metadata array (e.g. [[{...}, {...}]]) carries no
            # sibling keys to merge into; collapse it rather than returning a
            # one-element list that callers would have to unwrap.
            if len(leftovers) == 1 and isinstance(leftovers[0], dict):
                return leftovers[0]
        return [flatten(i) for i in node]

    return node


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches Yahoo's OAuth redirect so the user never copies a code by hand."""
    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        params = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = params.get("code", [None])[0]
        _CallbackHandler.error = params.get("error", [None])[0]
        body = (b"<h2>Authorized. You can close this tab.</h2>"
                if _CallbackHandler.code else
                b"<h2>Authorization failed. Check the terminal.</h2>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # Silence the default stderr access log.
        return


class YahooClient:
    def __init__(self, client_id: Optional[str] = None,
                 client_secret: Optional[str] = None,
                 redirect_port: int = DEFAULT_REDIRECT_PORT,
                 token_path: Path = TOKEN_PATH):
        self.client_id = client_id or os.environ.get("YAHOO_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("YAHOO_CLIENT_SECRET")
        self.redirect_uri = f"http://localhost:{redirect_port}/callback"
        self.redirect_port = redirect_port
        self.token_path = token_path
        self._token: Optional[Dict] = None
        if token_path.exists():
            try:
                self._token = json.loads(token_path.read_text())
            except (json.JSONDecodeError, OSError):
                self._token = None

    # -- auth ---------------------------------------------------------------

    @property
    def authenticated(self) -> bool:
        return bool(self._token and self._token.get("refresh_token"))

    def _basic_auth(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return base64.b64encode(raw).decode()

    def _save_token(self, token: Dict) -> None:
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60
        self._token = token
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(token, indent=2))
        os.chmod(self.token_path, 0o600)  # Refresh tokens are long-lived secrets.

    def login(self, open_browser: bool = True) -> None:
        """Run the OAuth2 authorization-code flow via a loopback redirect."""
        if not self.client_id or not self.client_secret:
            raise RuntimeError(
                "Missing YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET. Register an app at "
                "https://developer.yahoo.com/apps/create/ once your API access "
                "application is approved."
            )
        _CallbackHandler.code = _CallbackHandler.error = None
        server = HTTPServer(("localhost", self.redirect_port), _CallbackHandler)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        url = f"{AUTH_URL}?" + urlencode({
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "language": "en-us",
        })
        print(f"\nAuthorize this app in your browser:\n  {url}\n")
        if open_browser:
            webbrowser.open(url)

        thread.join(timeout=300)
        server.server_close()
        if _CallbackHandler.error:
            raise RuntimeError(f"Yahoo returned an error: {_CallbackHandler.error}")
        if not _CallbackHandler.code:
            raise RuntimeError("Timed out waiting for the Yahoo authorization redirect.")

        resp = requests.post(TOKEN_URL, timeout=30,
            headers={"Authorization": f"Basic {self._basic_auth()}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "authorization_code",
                  "redirect_uri": self.redirect_uri,
                  "code": _CallbackHandler.code})
        resp.raise_for_status()
        self._save_token(resp.json())
        print("Authorized. Refresh token stored at", self.token_path)

    def _refresh(self) -> None:
        resp = requests.post(TOKEN_URL, timeout=30,
            headers={"Authorization": f"Basic {self._basic_auth()}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token",
                  "redirect_uri": self.redirect_uri,
                  "refresh_token": self._token["refresh_token"]})
        resp.raise_for_status()
        token = resp.json()
        # Yahoo may omit the refresh token on renewal; keep the existing one.
        token.setdefault("refresh_token", self._token["refresh_token"])
        self._save_token(token)

    def _access_token(self) -> str:
        if not self._token:
            raise RuntimeError("Not authenticated. Run `ff auth` first.")
        if time.time() >= self._token.get("expires_at", 0):
            self._refresh()
        return self._token["access_token"]

    # -- requests -----------------------------------------------------------

    def get(self, path: str, *, retry_auth: bool = True) -> Dict:
        """GET a Fantasy API path, returning flattened `fantasy_content`."""
        url = f"{BASE}/{path.lstrip('/')}"
        sep = "&" if "?" in url else "?"
        resp = requests.get(f"{url}{sep}format=json", timeout=30,
                            headers={"Authorization": f"Bearer {self._access_token()}"})
        if resp.status_code == 401 and retry_auth:
            self._refresh()
            return self.get(path, retry_auth=False)
        if resp.status_code == 403:
            raise RuntimeError(
                "Yahoo returned 403. This usually means your API access application "
                "has not been approved yet, or the app lacks Fantasy Sports scope."
            )
        resp.raise_for_status()
        return flatten(resp.json().get("fantasy_content", {}))
