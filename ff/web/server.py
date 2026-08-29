"""HTTP API wrapping the projection engine for the desktop app.

The board takes several seconds to build (four projection sources, three seasons
of history), so it is computed once per league configuration and held in memory.
Everything downstream -- lineup, waivers, draft, trades -- reuses that snapshot.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config
from ..advice.draft import build_board, positional_scarcity
from ..advice.draftroom import (DraftState, positional_runs, rank_candidates,
                                roster_needs)
from ..advice.lineup import RosterSpot, optimize
from ..advice.trades import evaluate
from ..advice.waivers import assume_available, rank_targets
from ..model import availability as av
from ..model.scoring import ScoringRules
from ..model.value import LeagueShape
from ..sources import fantasypros, nflverse, sleeper, vegas
from ..util import DATA_DIR

STATIC_DIR = Path(__file__).resolve().parent / "static"

# The CLI loads .env before importing anything; the server may be started
# directly (uvicorn, the app bundle), so load it here too.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

app = FastAPI(title="Fantasy Football Agent")

_lock = threading.Lock()
_cache: Dict[str, Any] = {"key": None, "board": None, "universe": None, "built_at": 0}


# --------------------------------------------------------------------------
# Shared state
# --------------------------------------------------------------------------

def league_shape() -> LeagueShape:
    saved = config.get("league_shape") or {}
    return LeagueShape(
        num_teams=saved.get("num_teams", 12),
        slots=saved.get("slots") or LeagueShape().slots,
        ppr=saved.get("ppr", 1.0),
    )


def scoring_rules(shape: LeagueShape) -> ScoringRules:
    preset = {1.0: "ppr", 0.5: "half_ppr", 0.0: "standard"}.get(shape.ppr, "ppr")
    rules = ScoringRules.preset(preset)
    rules.values["rec"] = shape.ppr
    return rules


def current_season() -> int:
    try:
        return int(sleeper.nfl_state().get("season") or 2026)
    except Exception:  # noqa: BLE001
        return 2026


def current_week() -> int:
    try:
        return int(sleeper.nfl_state().get("week") or 1)
    except Exception:  # noqa: BLE001
        return 1


def _cache_key(shape: LeagueShape, bias: float) -> str:
    return f"{shape.num_teams}|{sorted(shape.slots.items())}|{shape.ppr}|{bias}"


def get_board(team_bias: float = 0.5, refresh: bool = False):
    """Board and universe, rebuilt only when the league config changes."""
    shape = league_shape()
    key = _cache_key(shape, team_bias)
    with _lock:
        if not refresh and _cache["key"] == key and _cache["board"]:
            return _cache["board"], _cache["universe"], shape
        universe = sleeper.load_universe()
        board = build_board(universe, shape, scoring_rules(shape),
                            current_season(), team_bias=team_bias)
        _cache.update({"key": key, "board": board, "universe": universe,
                       "built_at": time.time()})
        return board, universe, shape


def board_index(board) -> Dict:
    return {v.player.key: v for v in board}


def player_json(v) -> Dict:
    p = v.player
    return {
        "name": p.name, "position": p.position, "team": p.team,
        "points": v.points, "ppg": round(v.projection.per_game, 2),
        "vor": v.vor, "tier": v.tier, "position_tier": v.position_tier,
        "position_rank": v.position_rank, "basis": v.projection.basis,
        "injury": p.injury_status, "bye": p.bye_week,
        "age": p.age, "rookie": p.years_exp == 0,
    }


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/api/status")
def status() -> Dict:
    from ..sources.yahoo import YahooClient

    client = YahooClient()
    shape = league_shape()
    saved = config.get("league_shape")
    return {
        "season": current_season(),
        "week": current_week(),
        "league": {
            "teams": shape.num_teams, "ppr": shape.ppr,
            "slots": shape.slots, "superflex": shape.is_superflex,
            "configured": bool(saved),
        },
        "sources": {
            "sleeper": True,
            "nflverse": True,
            "fantasypros": bool(fantasypros.api_key()),
            "yahoo_authorized": client.authenticated,
            "yahoo_credentials": bool(client.client_id and client.client_secret),
        },
        "roster_file": str(DATA_DIR / "roster.txt"),
        "board_age_seconds": round(time.time() - _cache["built_at"], 1)
        if _cache["built_at"] else None,
    }


class LeagueUpdate(BaseModel):
    num_teams: int = 12
    ppr: float = 1.0
    slots: Dict[str, int]


@app.post("/api/league")
def set_league(update: LeagueUpdate) -> Dict:
    config.set_("league_shape", {"num_teams": update.num_teams,
                                 "ppr": update.ppr, "slots": update.slots})
    with _lock:
        _cache["key"] = None      # force a rebuild on next request
    return {"ok": True}


@app.get("/api/board")
def board(position: Optional[str] = None, limit: int = 200,
          team_bias: float = 0.5, refresh: bool = False) -> Dict:
    b, _, shape = get_board(team_bias, refresh)
    rows = b
    if position and position != "ALL":
        wanted = {p.strip().upper() for p in position.split(",")}
        rows = [v for v in rows if (v.player.position or "").upper() in wanted]
    return {
        "players": [player_json(v) for v in rows[:limit]],
        "scarcity": positional_scarcity(b, shape),
        "total": len(rows),
    }


@app.get("/api/teams")
def teams(team_bias: float = 0.5) -> Dict:
    from ..model.context import build_contexts, multiplier

    season = current_season()
    outlooks = vegas.team_outlooks(season)
    if not outlooks:
        return {"teams": [], "note": f"No posted lines for {season} yet."}
    b, _, _ = get_board(0.0)
    qb_points, qb_name = {}, {}
    for v in b:
        if (v.player.position or "").upper() == "QB" and v.player.team:
            if v.points > qb_points.get(v.player.team, 0.0):
                qb_points[v.player.team] = v.points
                qb_name[v.player.team] = v.player.name
    contexts = build_contexts(outlooks, qb_points)
    out = []
    for c in sorted(contexts.values(), key=lambda c: -c.implied_total):
        out.append({
            "team": c.team, "implied_total": c.implied_total,
            "projected_wins": c.projected_wins, "qb": qb_name.get(c.team),
            "games_priced": c.games_priced,
            "rb_tilt": round((multiplier("RB", c, team_bias) - 1) * 100, 1),
            "wr_tilt": round((multiplier("WR", c, team_bias) - 1) * 100, 1),
        })
    return {"teams": out}


def _load_roster(universe) -> List:
    path = DATA_DIR / "roster.txt"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        p = universe.resolve(name)
        if p:
            out.append(p)
    return out


@app.get("/api/roster")
def roster() -> Dict:
    _, universe, _ = get_board()
    players = _load_roster(universe)
    idx = board_index(get_board()[0])
    return {"players": [
        {"name": p.name, "position": p.position, "team": p.team,
         "injury": p.injury_status,
         "ppg": round(idx[p.key].projection.per_game, 2) if p.key in idx else 0.0}
        for p in players]}


class RosterUpdate(BaseModel):
    names: List[str]


@app.post("/api/roster")
def save_roster(update: RosterUpdate) -> Dict:
    path = DATA_DIR / "roster.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(n.strip() for n in update.names if n.strip()) + "\n")
    return {"ok": True, "count": len(update.names)}


@app.get("/api/lineup")
def lineup(week: Optional[int] = None) -> Dict:
    b, universe, shape = get_board()
    idx = board_index(b)
    season, wk = current_season(), week or current_week()
    byes = nflverse.bye_weeks(season) or nflverse.bye_weeks(season - 1)
    reports = av.build_report_index(nflverse.injuries([season]), season)

    players = _load_roster(universe)
    if not players:
        return {"starters": [], "bench": [], "total": 0, "empty": [],
                "note": "No roster set."}

    spots = []
    for p in players:
        v = idx.get(p.key)
        ppg = v.projection.per_game if v else 0.0
        a = av.for_player(p, reports, wk)
        spots.append(RosterSpot(
            player=p, points=round(ppg * a.multiplier, 2), eligible=(p.position,),
            on_bye=byes.get(p.team or "") == wk,
            unavailable_reason="Out" if a.is_out else None,
            raw_points=round(ppg, 2), availability=a))
    result = optimize(spots, shape)

    def spot_json(s, slot=None):
        return {"slot": slot, "name": s.player.name, "position": s.player.position,
                "team": s.player.team, "points": s.points,
                "raw_points": s.raw_points,
                "status": s.availability.describe() if s.availability else "",
                "on_bye": s.on_bye}

    return {
        "week": wk,
        "starters": [spot_json(s, slot) for slot, s in result.starters],
        "bench": [spot_json(s) for s in result.bench],
        "empty": result.empty_slots,
        "total": result.total,
    }


@app.get("/api/waivers")
def waivers(limit: int = 25, week: Optional[int] = None) -> Dict:
    b, universe, shape = get_board()
    idx = board_index(b)
    season, wk = current_season(), week or current_week()
    byes = nflverse.bye_weeks(season) or nflverse.bye_weeks(season - 1)
    trend = dict(sleeper.trending("add", 24, 200))

    ownership, basis = {}, "consensus rank (rough)"
    if fantasypros.api_key():
        try:
            ownership = fantasypros.yahoo_ownership(
                season, fantasypros.scoring_code(shape.ppr))
            if ownership:
                basis = f"Yahoo ownership ({len(ownership)} players)"
        except Exception:  # noqa: BLE001
            ownership = {}

    players = _load_roster(universe)
    if not players:
        return {"targets": [], "note": "No roster set.", "basis": basis}

    def ppg(p):
        v = idx.get(p.key)
        return v.projection.per_game if v else 0.0

    pool = assume_available(universe, shape, [p.name for p in players],
                            ownership=ownership)
    by_pos: Dict[str, List] = {}
    for p in pool:
        val = ppg(p)
        if val > 0:
            by_pos.setdefault(p.position, []).append((p, val))
    candidates = []
    for group in by_pos.values():
        group.sort(key=lambda pair: -pair[1])
        candidates.extend(group[:30])

    targets = rank_targets([(p, ppg(p)) for p in players], candidates, shape,
                           trending=trend, bye_weeks=byes, week=wk, limit=limit)
    return {"basis": basis, "targets": [{
        "name": t.player.name, "position": t.player.position,
        "team": t.player.team, "ppg": t.projected_ppg,
        "marginal": t.marginal_ppg, "faab": t.faab_pct,
        "adds": t.trending_adds,
        "drop": t.drop_candidate.name if t.drop_candidate else None,
        "reasons": t.reasons,
    } for t in targets]}


class TradeRequest(BaseModel):
    give: List[str] = []
    get: List[str] = []


@app.post("/api/trade")
def trade(req: TradeRequest) -> Dict:
    b, universe, shape = get_board()
    idx = board_index(b)
    season = current_season()
    byes = nflverse.bye_weeks(season) or nflverse.bye_weeks(season - 1)

    def resolve(names, label):
        out = []
        for n in names:
            p = universe.resolve(n)
            if not p:
                raise HTTPException(400, f"No match for '{n}' in {label}")
            out.append(p)
        return out

    def ppg(p):
        v = idx.get(p.key)
        return v.projection.per_game if v else 0.0

    players = _load_roster(universe)
    if not players:
        raise HTTPException(400, "No roster set.")
    giving, getting = resolve(req.give, "give"), resolve(req.get, "get")
    v = evaluate([(p, ppg(p)) for p in players],
                 [(p, ppg(p)) for p in giving],
                 [(p, ppg(p)) for p in getting], shape, bye_weeks=byes)
    return {
        "verdict": v.verdict, "net": v.net,
        "lineup_before": v.lineup_before, "lineup_after": v.lineup_after,
        "outgoing_value": v.outgoing_value, "incoming_value": v.incoming_value,
        "depth_penalty": v.depth_penalty,
        "starters_gained": v.starters_gained, "starters_lost": v.starters_lost,
        "notes": v.notes,
        "give": [{"name": p.name, "position": p.position, "ppg": round(ppg(p), 2)}
                 for p in giving],
        "get": [{"name": p.name, "position": p.position, "ppg": round(ppg(p), 2)}
                for p in getting],
    }


@app.get("/api/search")
def search(q: str, limit: int = 12) -> Dict:
    b, universe, _ = get_board()
    ql = q.strip().lower()
    if not ql:
        return {"players": []}
    hits = [v for v in b if ql in v.player.name.lower()]
    return {"players": [player_json(v) for v in hits[:limit]]}


# -- draft --------------------------------------------------------------------

class DraftStart(BaseModel):
    teams: int = 10
    my_pick: int = 1
    rounds: int = 16
    snake: bool = True


class DraftPick(BaseModel):
    name: str
    mine: bool = False


def _draft_json(state: DraftState) -> Dict:
    rnd, slot = state.round_and_slot()
    return {
        "teams": state.teams, "my_pick": state.my_pick, "rounds": state.rounds,
        "pick_number": state.pick_number(), "round": rnd, "slot": slot,
        "my_turn": state.is_my_turn(),
        "picks_until_mine": state.picks_until_mine(),
        "taken": state.taken[-60:],
        "my_roster": state.my_roster_names,
    }


@app.get("/api/draft")
def draft_status() -> Dict:
    state = DraftState.load()
    if state is None:
        return {"active": False}
    return {"active": True, **_draft_json(state)}


@app.post("/api/draft/start")
def draft_start(req: DraftStart) -> Dict:
    state = DraftState(teams=req.teams, my_pick=req.my_pick,
                       rounds=req.rounds, snake=req.snake)
    state.save()
    return {"active": True, **_draft_json(state)}


@app.post("/api/draft/pick")
def draft_pick(req: DraftPick) -> Dict:
    _, universe, _ = get_board()
    state = DraftState.load()
    if state is None:
        raise HTTPException(400, "No draft in progress.")
    player = universe.resolve(req.name)
    if not player:
        raise HTTPException(400, f"No match for '{req.name}'")
    if player.key in state.taken_keys:
        raise HTTPException(400, f"{player.name} is already off the board.")
    state.taken.append({"name": player.name, "pos": player.position,
                        "team": player.team, "mine": req.mine,
                        "pick": state.pick_number()})
    state.save()
    return {"active": True, **_draft_json(state)}


@app.post("/api/draft/undo")
def draft_undo() -> Dict:
    state = DraftState.load()
    if state is None or not state.taken:
        raise HTTPException(400, "Nothing to undo.")
    state.taken.pop()
    state.save()
    return {"active": True, **_draft_json(state)}


@app.get("/api/draft/board")
def draft_board(limit: int = 20, team_bias: float = 0.5) -> Dict:
    b, universe, shape = get_board(team_bias)
    state = DraftState.load()
    if state is None:
        raise HTTPException(400, "No draft in progress.")
    shape.num_teams = state.teams
    candidates = rank_candidates(b, state, shape, universe, limit=limit)
    return {
        "needs": roster_needs(state, shape, universe),
        "runs": positional_runs(state),
        "candidates": [{
            "name": c.player.name, "position": c.player.position,
            "team": c.player.team, "vor": c.valued.vor,
            "lineup_gain": round(c.lineup_gain * 16, 1), "score": c.score,
            "tier_remaining": c.tier_remaining, "survival": c.survival,
            "tier": c.valued.position_tier, "bye": c.player.bye_week,
        } for c in candidates],
        **_draft_json(state),
    }


@app.post("/api/draft/export")
def draft_export() -> Dict:
    state = DraftState.load()
    if state is None or not state.my_roster_names:
        raise HTTPException(400, "Nothing to export.")
    path = DATA_DIR / "roster.txt"
    path.write_text("\n".join(state.my_roster_names) + "\n")
    return {"ok": True, "count": len(state.my_roster_names)}


# -- podcasts -----------------------------------------------------------------

@app.get("/api/podcasts")
def podcast_mentions(limit: int = 40, injury_only: bool = False,
                     roster_only: bool = True) -> Dict:
    from ..model.mentions import find_mentions, summarize
    from ..sources import podcasts

    _, universe, _ = get_board()
    transcripts = podcasts.load_transcripts()
    if not transcripts:
        return {"mentions": [], "transcripts": 0,
                "note": "No transcripts yet. Fetch episodes first."}

    players = _load_roster(universe) if roster_only else [
        p for p in universe.filter(["QB", "RB", "WR", "TE"])
        if p.search_rank is not None and p.search_rank < 400]
    if not players:
        players = [p for p in universe.filter(["QB", "RB", "WR", "TE"])
                   if p.search_rank is not None and p.search_rank < 300]

    mentions = find_mentions(transcripts, players)
    if injury_only:
        mentions = [m for m in mentions if m.injury_related]
    return {
        "transcripts": len(transcripts),
        "shows": sorted({t.get("show", "?") for t in transcripts}),
        "mentions": [{
            "player": m.player.name, "position": m.player.position,
            "show": m.show, "episode": m.episode, "clock": m.clock,
            "context": m.context, "injury": m.injury_related,
            "opinion": m.opinion_related,
        } for m in mentions[:limit]],
    }


# -- static -------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
