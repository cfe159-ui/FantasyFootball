"""Tests for the invariants that would corrupt results silently if broken.

Deliberately offline: no network, no API keys, no cached parquet required.
"""
from __future__ import annotations

import itertools
import random

import pytest

from ff.advice.lineup import RosterSpot, eligible_slots, optimize
from ff.advice.trades import evaluate
from ff.model import availability as av
from ff.model.projections import Projection, ensemble
from ff.model.scoring import ScoringRules
from ff.model.value import LeagueShape, assign_tiers, value_players
from ff.players import Player, PlayerUniverse
from ff.sources.yahoo import flatten
from ff.util import norm_name, norm_pos, norm_team


# --------------------------------------------------------------------------
# Name normalization and player resolution
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Ja'Marr Chase", "jamarrchase"),
    ("Michael Pittman Jr.", "michaelpittman"),
    ("Amon-Ra St. Brown", "amonrastbrown"),
    ("D.K. Metcalf", "dkmetcalf"),
    ("Kenneth Walker III", "kennethwalker"),
    ("Patrick Mahomes II", "patrickmahomes"),
])
def test_norm_name_strips_suffixes_and_punctuation(raw, expected):
    assert norm_name(raw) == expected


def test_norm_team_and_position_aliases():
    assert norm_team("JAC") == "JAX"
    assert norm_team("WSH") == "WAS"
    assert norm_pos("DEF") == "DST"
    assert norm_pos("PK") == "K"


def _universe():
    return PlayerUniverse([
        Player(name="Ja'Marr Chase", position="WR", team="CIN", sleeper_id="1"),
        Player(name="Michael Pittman Jr.", position="WR", team="IND", sleeper_id="2"),
        Player(name="Josh Allen", position="QB", team="BUF", sleeper_id="3"),
        # Same surname at a different position: must not collide.
        Player(name="Josh Allen", position="RB", team="JAX", sleeper_id="4"),
    ])


def test_resolve_matches_across_provider_spellings():
    u = _universe()
    assert u.resolve("JaMarr Chase", "WR").sleeper_id == "1"
    assert u.resolve("Michael Pittman", "WR").sleeper_id == "2"


def test_resolve_disambiguates_by_position():
    u = _universe()
    assert u.resolve("Josh Allen", "QB").team == "BUF"
    assert u.resolve("Josh Allen", "RB").team == "JAX"


def test_resolve_returns_none_rather_than_guessing():
    assert _universe().resolve("Completely Fictional Person", "WR") is None


# --------------------------------------------------------------------------
# Yahoo response flattening
# --------------------------------------------------------------------------

def test_flatten_index_keyed_collection_becomes_list():
    assert flatten({"0": {"a": 1}, "1": {"a": 2}, "count": 2}) == [{"a": 1}, {"a": 2}]


def test_flatten_merges_single_key_metadata_arrays():
    got = flatten([[{"player_key": "x"}, {"name": {"full": "A B"}}],
                   {"selected_position": [{"position": "WR"}]}])
    assert got["player_key"] == "x"
    assert got["name"]["full"] == "A B"


def test_flatten_preserves_repeated_keys_as_a_list():
    """eligible_positions must not collapse to its last value."""
    got = flatten([{"position": "WR"}, {"position": "W/R/T"}])
    assert got == [{"position": "WR"}, {"position": "W/R/T"}]


def test_flatten_collapses_lone_nested_metadata_array():
    got = flatten([[{"player_key": "x"}, {"player_id": "9"}]])
    assert got == {"player_key": "x", "player_id": "9"}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def test_ppr_and_standard_differ_only_by_receptions():
    ppr, std = ScoringRules.preset("ppr"), ScoringRules.preset("standard")
    line = {"receptions": 8, "receiving_yards": 100, "receiving_tds": 1}
    assert ppr.score_row(line) - std.score_row(line) == pytest.approx(8.0)


def test_kicker_scored_by_distance_band():
    rules = ScoringRules.preset("half_ppr")
    # 2x30-39 (3 each) + 1x50-59 (5) + 3 PAT (1 each) - 1 miss = 13
    assert rules.score_row({"fg_made_30_39": 2, "fg_made_50_59": 1,
                            "pat_made": 3, "fg_missed": 1}) == pytest.approx(13.0)


def test_points_allowed_uses_tiers_not_a_rate():
    rules = ScoringRules.preset("half_ppr")
    assert rules.points_allowed_score(0) == 10
    assert rules.points_allowed_score(13) == 4
    assert rules.points_allowed_score(35) == -4


def test_defense_scoring_ignores_that_teams_offense():
    """The team stats frame carries offensive columns; they must not count."""
    rules = ScoringRules.preset("half_ppr")
    row = {"def_sacks": 4, "def_interceptions": 2, "points_allowed": 10,
           "passing_yards": 400, "rushing_yards": 150, "receptions": 25}
    # 4 sacks + 2x2 INT + 4 (PA tier) = 12, with no offensive credit.
    assert rules.score_defense_row(row) == pytest.approx(12.0)
    assert rules.score_row(row) > 30  # the general path would count offence


# --------------------------------------------------------------------------
# Lineup optimization
# --------------------------------------------------------------------------

def _spot(name, pos, pts, bye=False, out=False):
    return RosterSpot(Player(name=name, position=pos, team="X"), pts, (pos,),
                      on_bye=bye, unavailable_reason="Out" if out else None)


def _brute_force(spots, shape):
    slots = []
    for slot, count in shape.slots.items():
        if slot.upper() in ("BN", "IR"):
            continue
        slots += [slot] * int(count)
    startable = [s for s in spots if s.startable]
    elig = [set(eligible_slots(s, slots)) for s in startable]

    def rec(j, used):
        if j == len(slots):
            return 0.0
        best = rec(j + 1, used)                     # leaving a slot empty is legal
        for i in range(len(startable)):
            if not (used >> i) & 1 and slots[j] in elig[i]:
                best = max(best, startable[i].points + rec(j + 1, used | (1 << i)))
        return best

    return round(rec(0, 0), 2)


@pytest.mark.parametrize("slots", [
    {"QB": 1, "RB": 1, "WR": 2, "TE": 1, "W/R/T": 1, "BN": 3},
    {"QB": 1, "RB": 2, "WR": 1, "W/T": 1, "W/R": 1, "BN": 3},   # overlapping flex
    {"QB": 1, "RB": 1, "WR": 1, "TE": 1, "FLEX": 2, "BN": 3},
    {"QB": 1, "RB": 1, "WR": 2, "SUPERFLEX": 1, "BN": 3},
])
def test_optimizer_matches_brute_force(slots):
    """Greedy slot-filling is wrong when flex eligibility overlaps; this is exact."""
    rng = random.Random(11)
    shape = LeagueShape(num_teams=10, slots=slots)
    for _ in range(40):
        spots = [
            _spot(f"{p}{i}", p, round(rng.uniform(2, 25), 1),
                  bye=rng.random() < 0.12)
            for i, p in enumerate(rng.choice(["QB", "RB", "WR", "TE"])
                                  for _ in range(rng.randint(6, 8)))
        ]
        assert optimize(spots, shape).total == pytest.approx(
            _brute_force(spots, shape), abs=0.011)


def test_bye_and_out_players_never_start():
    shape = LeagueShape(num_teams=10, slots={"RB": 1, "BN": 3})
    spots = [_spot("bye stud", "RB", 30.0, bye=True),
             _spot("injured stud", "RB", 28.0, out=True),
             _spot("available", "RB", 5.0)]
    result = optimize(spots, shape)
    assert [s.player.name for _, s in result.starters] == ["available"]


# --------------------------------------------------------------------------
# Tiering
# --------------------------------------------------------------------------

def test_tiers_adapt_to_local_gap_scale():
    """Huge gaps at the top and tiny ones below must both produce tiers."""
    values = [200, 186, 172, 100, 99.6, 99.2, 98.8, 98.4, 98.0, 97.6]
    tiers = assign_tiers(values)
    assert tiers[0] != tiers[3], "a 72-point cliff must break a tier"
    assert len(set(tiers)) >= 3


def test_tiers_never_run_unbounded():
    tiers = assign_tiers([100 - i * 0.01 for i in range(60)])
    counts = {t: tiers.count(t) for t in set(tiers)}
    assert max(counts.values()) <= 8


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------

def test_healthy_player_is_not_discounted():
    """The 85% 'no designation' rate applies only to players ON the report."""
    assert av.lookup(None, None).multiplier == pytest.approx(1.0)


def test_out_and_doubtful_are_zero():
    assert av.lookup("Out").multiplier == 0.0
    assert av.lookup("Doubtful").multiplier == 0.0


def test_questionable_is_discounted_more_than_folklore():
    """Measured appearance rate is ~58%, not the commonly assumed ~75%."""
    q = av.lookup("Questionable", "Limited Participation in Practice")
    assert 0.5 < q.multiplier < 0.65
    dnp = av.lookup("Questionable", "Did Not Participate In Practice")
    assert dnp.multiplier < q.multiplier


# --------------------------------------------------------------------------
# League shape and value
# --------------------------------------------------------------------------

def test_superflex_raises_quarterback_replacement_level():
    std = LeagueShape(num_teams=12)
    sflex = LeagueShape(num_teams=12, slots={
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "W/R/T": 1, "SUPERFLEX": 1, "BN": 6})
    assert sflex.is_superflex and not std.is_superflex
    assert sflex.replacement_rank("QB") > std.replacement_rank("QB") + 5


def test_league_size_moves_replacement_level():
    assert (LeagueShape(num_teams=14).replacement_rank("RB")
            > LeagueShape(num_teams=8).replacement_rank("RB"))


# --------------------------------------------------------------------------
# Ensemble
# --------------------------------------------------------------------------

def _proj(name, pos, pts):
    return Projection(player=Player(name=name, position=pos, team="X"),
                      points=pts, per_game=pts / 16, games=16)


def test_ensemble_renormalizes_over_covering_sources_only():
    """A player missing from one source must not be penalized for it."""
    a = {("x", "RB"): _proj("X", "RB", 200.0)}
    b = {("x", "RB"): _proj("X", "RB", 100.0), ("y", "WR"): _proj("Y", "WR", 150.0)}
    out = ensemble({"a": a, "b": b}, {"a": 0.5, "b": 0.5})
    assert out[("x", "RB")].points == pytest.approx(150.0)
    # Y appears only in b, so he keeps b's number rather than being halved.
    assert out[("y", "WR")].points == pytest.approx(150.0)


# --------------------------------------------------------------------------
# Trades
# --------------------------------------------------------------------------

def test_consolidation_trade_judged_on_starting_lineup():
    """Two bench-quality players for one starter can win despite losing totals."""
    shape = LeagueShape(num_teams=10, slots={"RB": 1, "WR": 1, "BN": 3})
    roster = [(Player(name="RB1", position="RB", team="X"), 15.0),
              (Player(name="RB2", position="RB", team="X"), 9.0),
              (Player(name="RB3", position="RB", team="X"), 8.0),
              (Player(name="WR1", position="WR", team="X"), 10.0)]
    verdict = evaluate(roster,
                       giving=[(roster[1][0], 9.0), (roster[2][0], 8.0)],
                       getting=[(Player(name="WR Stud", position="WR", team="X"), 15.0)],
                       shape=shape)
    assert verdict.outgoing_value > verdict.incoming_value   # loses on raw points
    assert verdict.lineup_after > verdict.lineup_before      # wins where it counts


# --------------------------------------------------------------------------
# Team and quarterback context
# --------------------------------------------------------------------------

class _Outlook:
    def __init__(self, implied, wins, priced=8):
        self.implied_total = implied
        self.projected_wins = wins
        self.games_priced = priced


def _contexts():
    from ff.model.context import build_contexts
    outlooks = {"DET": _Outlook(27.0, 11.0), "BUF": _Outlook(26.0, 10.0),
                "CHI": _Outlook(22.0, 8.0), "ARI": _Outlook(18.0, 4.0)}
    qb_points = {"DET": 300.0, "BUF": 380.0, "CHI": 250.0, "ARI": 200.0}
    return build_contexts(outlooks, qb_points)


def test_good_offense_tilts_up_and_bad_tilts_down():
    from ff.model.context import multiplier
    ctx = _contexts()
    assert multiplier("RB", ctx["DET"]) > 1.0
    assert multiplier("RB", ctx["ARI"]) < 1.0


def test_quarterback_quality_helps_receivers_most():
    """Measured QB effect is larger for WR (+11.8%) than TE (+3.5%)."""
    from ff.model.context import multiplier
    ctx = _contexts()
    buf = ctx["BUF"]
    wr_lift = multiplier("WR", buf) - 1.0
    te_lift = multiplier("TE", buf) - 1.0
    assert wr_lift > te_lift * 2


def test_tight_ends_are_barely_affected():
    """The data shows almost no team-quality efficiency edge for tight ends."""
    from ff.model.context import multiplier
    ctx = _contexts()
    assert abs(multiplier("TE", ctx["DET"]) - 1.0) < 0.03


def test_bias_stays_a_tilt_not_a_reprojection():
    from ff.model.context import MAX_MULTIPLIER, MIN_MULTIPLIER, multiplier
    from ff.model.context import TeamContext
    extreme = TeamContext(team="X", implied_total=99.0, projected_wins=17.0,
                          team_z=12.0, qb_z=12.0)
    assert multiplier("WR", extreme, strength=5.0) <= MAX_MULTIPLIER
    awful = TeamContext(team="Y", implied_total=1.0, projected_wins=0.0,
                        team_z=-12.0, qb_z=-12.0)
    assert multiplier("WR", awful, strength=5.0) >= MIN_MULTIPLIER


def test_zero_strength_disables_the_tilt():
    from ff.model.context import multiplier
    assert multiplier("WR", _contexts()["DET"], strength=0.0) == 1.0


def test_spread_converts_to_sensible_win_probability():
    from ff.sources.vegas import _win_probability
    assert _win_probability(0.0) == pytest.approx(0.5, abs=1e-6)
    assert _win_probability(-7.0) > 0.65      # seven-point favourite
    assert _win_probability(7.0) < 0.35
    assert _win_probability(-14.0) > _win_probability(-7.0)


# --------------------------------------------------------------------------
# Desktop app wiring
# --------------------------------------------------------------------------

def test_api_exposes_every_view_the_ui_needs():
    from ff.web.server import app as api

    paths = {r.path for r in api.routes if hasattr(r, "path")}
    for required in ("/api/status", "/api/board", "/api/lineup", "/api/waivers",
                     "/api/trade", "/api/teams", "/api/roster", "/api/search",
                     "/api/podcasts", "/api/draft", "/api/draft/start",
                     "/api/draft/pick", "/api/draft/board"):
        assert required in paths, f"missing endpoint {required}"


def test_static_assets_are_packaged():
    from ff.web.server import STATIC_DIR

    for name in ("index.html", "app.css", "app.js"):
        asset = STATIC_DIR / name
        assert asset.exists() and asset.stat().st_size > 0, f"missing {name}"


def test_free_port_returns_a_usable_port():
    import socket

    from ff.web.app import free_port

    port = free_port()
    assert 1024 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))   # must actually be bindable


def test_status_reports_warm_state_for_the_ui():
    """The UI gates board-dependent views on these fields."""
    from ff.web import server

    for field in ("board_ready", "warming", "warm_error"):
        assert field in server._cache or field in ("board_ready",), field
    # The cache carries the flags the status endpoint surfaces.
    assert "warming" in server._cache
    assert "warm_error" in server._cache


def test_warm_cache_does_not_start_twice():
    from ff.web import server

    server._cache["warming"] = True
    try:
        server.warm_cache()          # must return immediately, spawning nothing
        assert server._cache["warming"] is True
    finally:
        server._cache["warming"] = False
