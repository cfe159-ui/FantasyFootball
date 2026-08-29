#!/usr/bin/env python
"""Command line entry point for the fantasy football agent."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ff  # noqa: F401,E402  - installs warning filters before urllib3 loads

from dotenv import load_dotenv  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from ff import config  # noqa: E402
from ff.sources import sleeper  # noqa: E402

load_dotenv(Path(__file__).resolve().parent / ".env")
console = Console()


def _universe():
    with console.status("Loading player universe..."):
        return sleeper.load_universe()


def _yahoo_league():
    """Build a League handle, failing with actionable guidance."""
    from ff.sources.yahoo import YahooClient
    from ff.sources.yahoo_league import League

    client = YahooClient()
    if not client.authenticated:
        console.print("[red]Not authorized with Yahoo yet.[/red] "
                      "Run [bold]ff auth[/bold] (see SETUP.md).")
        raise SystemExit(1)
    lk = config.league_key()
    if not lk:
        console.print("[red]No league selected.[/red] "
                      "Run [bold]ff leagues[/bold] then [bold]ff use <league_key>[/bold].")
        raise SystemExit(1)
    return League(client, lk)


def cmd_status(args):
    from ff.sources.yahoo import YahooClient

    state = sleeper.nfl_state()
    table = Table(title="Agent status", show_header=False, box=None)
    table.add_row("NFL season", f"{state.get('season')} "
                                f"({state.get('season_type')}, week {state.get('week')})")
    client = YahooClient()
    creds = "set" if (client.client_id and client.client_secret) else "[red]missing[/red]"
    table.add_row("Yahoo credentials", creds)
    table.add_row("Yahoo authorized", "yes" if client.authenticated else "[yellow]no[/yellow]")
    table.add_row("League", config.league_key() or "[yellow]not selected[/yellow]")
    table.add_row("Free data sources", "[green]available[/green] (Sleeper, no auth)")
    console.print(table)
    if not client.authenticated:
        console.print("\n[dim]Free-data commands work now: "
                      "ff trending, ff player <name>[/dim]")


def cmd_auth(args):
    from ff.sources.yahoo import YahooClient

    YahooClient().login()


def cmd_leagues(args):
    from ff.sources.yahoo import YahooClient
    from ff.sources.yahoo_league import League

    client = YahooClient()
    if not client.authenticated:
        console.print("[red]Run ff auth first.[/red]")
        raise SystemExit(1)
    leagues = League.discover(client)
    if not leagues:
        console.print("[yellow]No NFL leagues found for this Yahoo account.[/yellow]")
        return
    table = Table(title="Your Yahoo NFL leagues")
    for col in ("league_key", "name", "teams", "scoring", "draft"):
        table.add_column(col)
    for lg in leagues:
        table.add_row(lg["league_key"], str(lg.get("name")), str(lg.get("num_teams")),
                      str(lg.get("scoring_type")), str(lg.get("draft_status")))
    console.print(table)
    console.print("\n[dim]Select one with: ff use <league_key>[/dim]")


def cmd_use(args):
    config.set_("league_key", args.league_key)
    console.print(f"[green]League set to {args.league_key}[/green]")


def cmd_league(args):
    league = _yahoo_league()
    with console.status("Fetching league settings..."):
        s = league.settings()
    table = Table(title=f"{s.name}", show_header=False, box=None)
    table.add_row("Teams", str(s.num_teams))
    table.add_row("Scoring", s.scoring_type)
    ppr = s.is_ppr
    table.add_row("PPR", "none" if ppr in (None, 0) else f"{ppr} per reception")
    table.add_row("Starters", ", ".join(f"{k}x{v}" for k, v in s.starting_slots.items()))
    table.add_row("Bench", str(s.roster_slots.get("BN", 0)))
    table.add_row("Waivers", f"{s.waiver_type or '?'}"
                             f"{' (FAAB)' if s.uses_faab else ''}")
    table.add_row("Playoffs start", str(s.playoff_start_week))
    table.add_row("Draft status", str(s.draft_status))
    console.print(table)


def cmd_roster(args):
    league = _yahoo_league()
    with console.status("Fetching roster..."):
        team_key = args.team or league.my_team_key()
        if not team_key:
            console.print("[red]Could not identify your team in this league.[/red]")
            raise SystemExit(1)
        roster = league.roster(team_key, week=args.week)
        universe = sleeper.load_universe()

    table = Table(title=f"Roster ({team_key})")
    for col in ("slot", "player", "pos", "team", "bye", "status"):
        table.add_column(col)
    unresolved = []
    for entry in roster:
        p = universe.resolve(entry["name"], entry["position"], entry["team"])
        if not p:
            unresolved.append(entry["name"])
        status = entry.get("status") or (p.injury_status if p else None) or ""
        table.add_row(entry.get("selected_position") or "-", entry["name"],
                      entry.get("position") or "", entry.get("team") or "",
                      str(entry.get("bye_week") or ""),
                      f"[yellow]{status}[/yellow]" if status else "")
    console.print(table)
    if unresolved:
        console.print(f"[dim]Unmatched against player database: "
                      f"{', '.join(unresolved)}[/dim]")


def cmd_trending(args):
    universe = _universe()
    kind = "drop" if args.drops else "add"
    rows = sleeper.trending_players(universe, kind, args.hours, args.limit * 3)
    if args.pos:
        want = {p.upper() for p in args.pos}
        rows = [(p, c) for p, c in rows if (p.position or "").upper() in want]
    rows = rows[: args.limit]

    table = Table(title=f"Trending {kind}s - last {args.hours}h "
                        f"(league-wide, via Sleeper)")
    for col in ("#", f"{kind}s", "player", "pos", "team", "depth", "status"):
        table.add_column(col)
    for i, (p, count) in enumerate(rows, 1):
        depth = f"#{p.depth_chart_order}" if p.depth_chart_order else ""
        status = p.injury_status or ""
        table.add_row(str(i), f"{count:,}", p.name, p.position or "", p.team or "",
                      depth, f"[yellow]{status}[/yellow]" if status else "")
    console.print(table)
    console.print("[dim]Add velocity signals opportunity, not value. "
                  "It is the crowd reacting, usually to an injury or a depth-chart move.[/dim]")


def cmd_player(args):
    universe = _universe()
    p = universe.resolve(args.name, args.pos)
    if not p:
        console.print(f"[red]No match for '{args.name}'.[/red]")
        raise SystemExit(1)
    table = Table(title=p.name, show_header=False, box=None)
    table.add_row("Position", p.position or "?")
    table.add_row("Team", p.team or "free agent")
    table.add_row("Age", str(p.age or "?"))
    table.add_row("Experience", f"{p.years_exp} yrs" if p.years_exp is not None else "?")
    if p.depth_chart_position or p.depth_chart_order:
        table.add_row("Depth chart",
                      f"{p.depth_chart_position or ''} #{p.depth_chart_order or '?'}")
    if p.injury_status:
        table.add_row("Injury", f"[yellow]{p.injury_status}"
                                f"{' - ' + p.injury_body_part if p.injury_body_part else ''}[/yellow]")
        if p.extra.get("injury_notes"):
            table.add_row("Notes", str(p.extra["injury_notes"]))
    console.print(table)


def main():
    parser = argparse.ArgumentParser(prog="ff", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show what is configured and working").set_defaults(fn=cmd_status)
    sub.add_parser("auth", help="authorize with Yahoo").set_defaults(fn=cmd_auth)
    sub.add_parser("leagues", help="list your Yahoo NFL leagues").set_defaults(fn=cmd_leagues)

    p_use = sub.add_parser("use", help="select the league to manage")
    p_use.add_argument("league_key")
    p_use.set_defaults(fn=cmd_use)

    sub.add_parser("league", help="show league settings and scoring").set_defaults(fn=cmd_league)

    p_roster = sub.add_parser("roster", help="show a team roster")
    p_roster.add_argument("--team", help="team key (defaults to yours)")
    p_roster.add_argument("--week", type=int)
    p_roster.set_defaults(fn=cmd_roster)

    p_tr = sub.add_parser("trending", help="league-wide add/drop velocity (no auth needed)")
    p_tr.add_argument("--drops", action="store_true", help="show drops instead of adds")
    p_tr.add_argument("--hours", type=int, default=24)
    p_tr.add_argument("--limit", type=int, default=20)
    p_tr.add_argument("--pos", nargs="*", help="filter to positions, e.g. --pos RB WR")
    p_tr.set_defaults(fn=cmd_trending)

    p_pl = sub.add_parser("player", help="look up a player (no auth needed)")
    p_pl.add_argument("name")
    p_pl.add_argument("--pos")
    p_pl.set_defaults(fn=cmd_player)

    p_bd = sub.add_parser("board", help="draft board ranked by value over replacement")
    p_bd.add_argument("--limit", type=int, default=40)
    p_bd.add_argument("--pos", nargs="*", help="filter positions")
    p_bd.add_argument("--teams", type=int, help="league size if not configured")
    p_bd.add_argument("--ppr", type=float, help="points per reception if not configured")
    p_bd.add_argument("--season", type=int)
    p_bd.add_argument("--scarcity", action="store_true", help="show positional cliffs")
    p_bd.add_argument("--team-bias", type=float, default=0.5,
                      help="strength of team/QB context tilt (0 disables)")
    p_bd.add_argument("--no-team-bias", action="store_true")
    p_bd.set_defaults(fn=cmd_board)

    p_ls = sub.add_parser("league-setup",
                          help="record league rules by hand (works without Yahoo)")
    p_ls.add_argument("--teams", type=int, default=12)
    p_ls.add_argument("--ppr", type=float, default=1.0,
                      help="0 standard, 0.5 half, 1 full")
    p_ls.add_argument("--superflex", action="store_true")
    p_ls.add_argument("--slots",
                      help="explicit slots, e.g. QB:1,RB:2,WR:3,TE:1,W/R/T:1,K:1,DST:1,BN:6")
    p_ls.set_defaults(fn=cmd_league_setup)

    p_lu = sub.add_parser("lineup", help="optimal starting lineup for a week")
    p_lu.add_argument("--week", type=int)
    p_lu.add_argument("--season", type=int)
    p_lu.add_argument("--teams", type=int)
    p_lu.add_argument("--ppr", type=float)
    p_lu.set_defaults(fn=cmd_lineup)

    p_wv = sub.add_parser("waivers", help="waiver targets ranked by lineup improvement")
    p_wv.add_argument("--week", type=int)
    p_wv.add_argument("--season", type=int)
    p_wv.add_argument("--limit", type=int, default=20)
    p_wv.add_argument("--teams", type=int)
    p_wv.add_argument("--ppr", type=float)
    p_wv.set_defaults(fn=cmd_waivers)

    p_fp = sub.add_parser("fp-check",
                          help="probe the FantasyPros API and show what your key unlocks")
    p_fp.add_argument("--season", type=int)
    p_fp.set_defaults(fn=cmd_fp_check)

    p_pod = sub.add_parser("podcasts",
                           help="what fantasy podcasts said about your players")
    p_pod.add_argument("--fetch", action="store_true",
                       help="download and transcribe recent episodes")
    p_pod.add_argument("--days", type=int, default=7)
    p_pod.add_argument("--per-show", type=int, default=2)
    p_pod.add_argument("--limit-episodes", type=int, default=6)
    p_pod.add_argument("--max-minutes", type=int, default=70,
                       help="skip episodes longer than this")
    p_pod.add_argument("--model", default="base",
                       choices=["tiny", "base", "small", "medium"])
    p_pod.add_argument("--force", action="store_true", help="re-transcribe")
    p_pod.add_argument("--player", nargs="*", help="focus on one player")
    p_pod.add_argument("--all", action="store_true",
                       help="track all relevant players, not just your roster")
    p_pod.add_argument("--quotes", action="store_true", help="print passages")
    p_pod.add_argument("--injury", action="store_true",
                       help="only availability-related mentions")
    p_pod.add_argument("--limit", type=int, default=20)
    p_pod.set_defaults(fn=cmd_podcasts)

    p_dr = sub.add_parser("draft", help="live draft assistant")
    dsub = p_dr.add_subparsers(dest="draft_command", required=True)

    d_start = dsub.add_parser("start", help="begin a draft")
    d_start.add_argument("--teams", type=int, default=10)
    d_start.add_argument("--pick", type=int, default=1, help="your first-round slot")
    d_start.add_argument("--rounds", type=int, default=16)
    d_start.add_argument("--linear", action="store_true", help="non-snake draft")
    d_start.set_defaults(fn=cmd_draft_start)

    d_take = dsub.add_parser("take", help="record a pick by someone else")
    d_take.add_argument("name", nargs="+")
    d_take.set_defaults(fn=cmd_draft_take)

    d_mine = dsub.add_parser("mine", help="record your own pick")
    d_mine.add_argument("name", nargs="+")
    d_mine.set_defaults(fn=cmd_draft_mine)

    d_now = dsub.add_parser("now", help="recommendations for the current pick")
    d_now.add_argument("--limit", type=int, default=12)
    d_now.add_argument("--season", type=int)
    d_now.add_argument("--teams", type=int)
    d_now.add_argument("--ppr", type=float)
    d_now.set_defaults(fn=cmd_draft_now)

    dsub.add_parser("undo", help="undo the last pick").set_defaults(fn=cmd_draft_undo)
    dsub.add_parser("roster", help="show your picks").set_defaults(fn=cmd_draft_roster)
    dsub.add_parser("export", help="write your picks to roster.txt").set_defaults(fn=cmd_draft_export)

    p_tr2 = sub.add_parser("trade", help="evaluate a proposed trade")
    p_tr2.add_argument("--give", nargs="*", help="players you send")
    p_tr2.add_argument("--get", nargs="*", help="players you receive")
    p_tr2.add_argument("--season", type=int)
    p_tr2.add_argument("--teams", type=int)
    p_tr2.add_argument("--ppr", type=float)
    p_tr2.set_defaults(fn=cmd_trade)

    p_tm = sub.add_parser("teams", help="Vegas implied scoring and projected wins")
    p_tm.add_argument("--season", type=int)
    p_tm.add_argument("--limit", type=int, default=32)
    p_tm.add_argument("--team-bias", type=float, default=0.5)
    p_tm.add_argument("--teams", type=int)
    p_tm.add_argument("--ppr", type=float)
    p_tm.set_defaults(fn=cmd_teams)

    p_app = sub.add_parser("app", help="launch the desktop app")
    p_app.add_argument("--browser", action="store_true",
                       help="open in your browser instead of a native window")
    p_app.add_argument("--port", type=int)
    p_app.set_defaults(fn=cmd_app)

    args = parser.parse_args()
    try:
        args.fn(args)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        raise SystemExit(130)


if __name__ == "__main__":
    main()


def _league_shape_and_rules(args):
    """League rules from Yahoo when authorized, else from local overrides."""
    from ff.model.scoring import ScoringRules
    from ff.model.value import LeagueShape
    from ff.sources.yahoo import YahooClient

    client = YahooClient()
    lk = config.league_key()
    if client.authenticated and lk:
        from ff.sources.yahoo_league import League
        settings = League(client, lk).settings()
        return (LeagueShape.from_yahoo(settings),
                ScoringRules.from_yahoo(settings),
                f"Yahoo league '{settings.name}'")

    saved = config.get("league_shape") or {}
    shape = LeagueShape(
        num_teams=saved.get("num_teams", args.teams or 12),
        slots=saved.get("slots") or LeagueShape().slots,
        ppr=saved.get("ppr", args.ppr if args.ppr is not None else 1.0),
    )
    rules = ScoringRules.preset(
        {1.0: "ppr", 0.5: "half_ppr", 0.0: "standard"}.get(shape.ppr, "ppr"))
    rules.values["rec"] = shape.ppr
    origin = "saved league settings" if saved else "defaults (no league configured)"
    return shape, rules, origin


def cmd_board(args):
    from ff.advice.draft import build_board, positional_scarcity

    universe = _universe()
    shape, rules, origin = _league_shape_and_rules(args)
    state = sleeper.nfl_state()
    season = int(args.season or state.get("season") or 2026)

    bias = 0.0 if getattr(args, "no_team_bias", False) else getattr(args, "team_bias", 0.5)
    with console.status(f"Projecting from {season - 3}-{season - 1} data..."):
        board = build_board(universe, shape, rules, season, team_bias=bias)

    if args.pos:
        want = {p.upper() for p in args.pos}
        board = [v for v in board if (v.player.position or "").upper() in want]

    console.print(f"[dim]Rules: {origin} | {shape.num_teams} teams | "
                  f"{rules.describe()}"
                  f"{' | SUPERFLEX' if shape.is_superflex else ''}[/dim]\n")

    table = Table(title=f"Draft board - {season}")
    for col in ("#", "player", "pos", "team", "proj", "VOR", "tier", "basis"):
        table.add_column(col)
    prev_tier = None
    for i, v in enumerate(board[: args.limit], 1):
        if prev_tier is not None and v.tier != prev_tier:
            table.add_section()
        prev_tier = v.tier
        table.add_row(str(i), v.player.name, v.player.position or "",
                      v.player.team or "", f"{v.points:.0f}", f"{v.vor:+.0f}",
                      str(v.tier), v.projection.basis)
    console.print(table)

    if args.scarcity:
        sc = positional_scarcity(board, shape)
        t2 = Table(title="Positional scarcity")
        for col in ("pos", "elite VOR", "next tier", "cliff", "replacement rank"):
            t2.add_column(col)
        for pos in sorted(sc, key=lambda p: -sc[p]["cliff"]):
            d = sc[pos]
            t2.add_row(pos, f"{d['elite_vor']:+.0f}", f"{d['next_tier_vor']:+.0f}",
                       f"{d['cliff']:.0f}", str(d["replacement_rank"]))
        console.print(t2)
        console.print("[dim]Cliff = how much value you lose waiting. "
                      "Attack the steepest position first.[/dim]")


def cmd_league_setup(args):
    """Record league rules by hand, so the board is correct before Yahoo approval."""
    from ff.model.value import LeagueShape

    slots = {}
    if args.slots:
        for part in args.slots.split(","):
            if ":" not in part:
                console.print(f"[red]Bad slot '{part}', expected POS:COUNT[/red]")
                raise SystemExit(1)
            pos, n = part.split(":", 1)
            slots[pos.strip().upper()] = int(n)
    else:
        slots = dict(LeagueShape().slots)
        if args.superflex:
            slots["SUPERFLEX"] = 1

    shape = {"num_teams": args.teams, "slots": slots, "ppr": args.ppr}
    config.set_("league_shape", shape)

    s = LeagueShape(num_teams=args.teams, slots=slots, ppr=args.ppr)
    table = Table(title="Saved league settings", show_header=False, box=None)
    table.add_row("Teams", str(s.num_teams))
    table.add_row("PPR", f"{s.ppr} per reception")
    table.add_row("Starters", ", ".join(f"{k}x{v}" for k, v in s.slots.items()
                                        if k not in ("BN", "IR")))
    table.add_row("Bench", str(s.bench_slots))
    table.add_row("Superflex", "yes" if s.is_superflex else "no")
    table.add_row("Replacement rank",
                  ", ".join(f"{p}#{s.replacement_rank(p)}" for p in ("QB", "RB", "WR", "TE")))
    console.print(table)
    console.print("\n[dim]These are used until Yahoo API access is approved, "
                  "after which your real league settings take over.[/dim]")


def _projection_table(universe, shape, rules, season):
    """Per-game projections keyed by player, shared by lineup and waivers."""
    from ff.advice.draft import build_board
    board = build_board(universe, shape, rules, season)
    return {v.player.key: v for v in board}


def _load_local_roster(universe):
    """Roster from data/roster.txt (one player name per line) for pre-Yahoo use."""
    from ff.util import DATA_DIR
    path = DATA_DIR / "roster.txt"
    if not path.exists():
        return None, path
    players, unresolved = [], []
    for line in path.read_text().splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        p = universe.resolve(name)
        (players.append(p) if p else unresolved.append(name))
    if unresolved:
        console.print(f"[yellow]Unresolved in roster.txt: {', '.join(unresolved)}[/yellow]")
    return players, path


def cmd_lineup(args):
    from ff.advice.lineup import RosterSpot, optimize
    from ff.sources import nflverse

    universe = _universe()
    shape, rules, origin = _league_shape_and_rules(args)
    state = sleeper.nfl_state()
    season = int(args.season or state.get("season") or 2026)
    week = args.week or int(state.get("week") or 1)

    roster, path = _load_local_roster(universe)
    if not roster:
        console.print(f"[red]No roster found.[/red] Create [bold]{path}[/bold] with one "
                      "player name per line, or authorize Yahoo to read it automatically.")
        raise SystemExit(1)

    from ff.model import availability as av

    with console.status("Projecting..."):
        projections = _projection_table(universe, shape, rules, season)
        byes = nflverse.bye_weeks(season) or nflverse.bye_weeks(season - 1)
        reports = av.build_report_index(nflverse.injuries([season]), season)

    spots = []
    for p in roster:
        v = projections.get(p.key)
        ppg = v.projection.per_game if v else 0.0
        avail = av.for_player(p, reports, week)
        spots.append(RosterSpot(
            player=p, points=round(ppg * avail.multiplier, 2),
            eligible=(p.position,),
            on_bye=byes.get(p.team or "") == week,
            unavailable_reason="Out" if avail.is_out else None,
            raw_points=round(ppg, 2), availability=avail))
    result = optimize(spots, shape)

    console.print(f"[dim]{origin} | week {week} | {rules.describe()}[/dim]\n")
    table = Table(title=f"Optimal lineup - week {week}")
    for col in ("slot", "player", "pos", "team", "proj", "status"):
        table.add_column(col)
    for slot, s in result.starters:
        note = s.availability.describe() if s.availability else ""
        raw = f"{s.raw_points:.1f} -> " if note and s.raw_points else ""
        table.add_row(slot, s.player.name, s.player.position or "",
                      s.player.team or "", f"{raw}{s.points:.1f}",
                      f"[yellow]{note}[/yellow]" if note else "")
    table.add_section()
    table.add_row("", "[bold]TOTAL[/bold]", "", "", f"[bold]{result.total:.1f}[/bold]")
    console.print(table)

    if result.empty_slots:
        console.print(f"[yellow]Unfilled slots: {', '.join(result.empty_slots)}[/yellow]")
    if result.bench:
        bench = Table(title="Bench")
        for col in ("player", "pos", "team", "proj", "note"):
            bench.add_column(col)
        for s in result.bench:
            note = "BYE" if s.on_bye else (
                s.unavailable_reason
                or (s.availability.describe() if s.availability else ""))
            bench.add_row(s.player.name, s.player.position or "", s.player.team or "",
                          f"{s.points:.1f}", f"[yellow]{note}[/yellow]" if note else "")
        console.print(bench)


def cmd_waivers(args):
    from ff.advice.waivers import assume_available, rank_targets
    from ff.sources import nflverse

    universe = _universe()
    shape, rules, origin = _league_shape_and_rules(args)
    state = sleeper.nfl_state()
    season = int(args.season or state.get("season") or 2026)
    week = args.week or int(state.get("week") or 1)

    roster, path = _load_local_roster(universe)
    if not roster:
        console.print(f"[red]No roster found.[/red] Create [bold]{path}[/bold] with one "
                      "player name per line.")
        raise SystemExit(1)

    with console.status("Projecting and scanning the wire..."):
        projections = _projection_table(universe, shape, rules, season)
        byes = nflverse.bye_weeks(season) or nflverse.bye_weeks(season - 1)
        trend = dict(sleeper.trending("add", 24, 200))

        ownership = {}
        pool_basis = "consensus rank (rough)"
        from ff.sources import fantasypros as fp
        if fp.api_key():
            try:
                ownership = fp.yahoo_ownership(season, fp.scoring_code(shape.ppr))
                if ownership:
                    pool_basis = f"Yahoo ownership, {len(ownership)} players"
            except Exception:  # noqa: BLE001 - fall back rather than fail
                ownership = {}
        pool = assume_available(universe, shape, [p.name for p in roster],
                                ownership=ownership)

    def ppg(p):
        v = projections.get(p.key)
        return v.projection.per_game if v else 0.0

    roster_pairs = [(p, ppg(p)) for p in roster]

    # Take the best candidates *per position*. Ranking the pool by raw points
    # would fill it with backup quarterbacks, who outscore startable receivers
    # in raw terms while being worth nothing to a team that already has a QB.
    by_pos = {}
    for p in pool:
        v = ppg(p)
        if v > 0:
            by_pos.setdefault(p.position, []).append((p, v))
    avail_pairs = []
    for pos, group in by_pos.items():
        group.sort(key=lambda pair: -pair[1])
        avail_pairs.extend(group[:30])

    targets = rank_targets(roster_pairs, avail_pairs, shape,
                           trending=trend, bye_weeks=byes, week=week,
                           limit=args.limit)

    console.print(f"[dim]{origin} | week {week} | "
                  f"free-agent pool from {pool_basis}[/dim]\n")
    table = Table(title="Waiver targets, ranked by improvement to YOUR lineup")
    for col in ("#", "player", "pos", "team", "ppg", "+lineup", "FAAB", "why"):
        table.add_column(col, overflow="ellipsis")
    for i, t in enumerate(targets, 1):
        gain = f"[green]+{t.marginal_ppg:.1f}[/green]" if t.marginal_ppg > 0 else "0.0"
        table.add_row(str(i), t.player.name, t.player.position or "",
                      t.player.team or "", f"{t.projected_ppg:.1f}", gain,
                      f"{t.faab_pct}%" if t.faab_pct else "-",
                      "; ".join(t.reasons[:2]))
    console.print(table)
    console.print("[dim]+lineup is points per week added to your optimal starting "
                  "lineup. A high-ppg player who cannot crack your lineup is worth 0.[/dim]")


def cmd_fp_check(args):
    """Probe the FantasyPros API and report what your key unlocks."""
    from ff.sources import fantasypros as fp

    if not fp.api_key():
        console.print("[red]No FANTASYPROS_API_KEY found.[/red]\n")
        console.print("Add it to your .env file (already gitignored):\n")
        console.print("  [bold]echo 'FANTASYPROS_API_KEY=your_key_here' >> "
                      '"/Users/nickcolucci/Claude Stuff/ff-agent/.env"[/bold]\n')
        console.print("[dim]Do not paste the key into a chat window - "
                      "it would persist in the transcript.[/dim]")
        raise SystemExit(1)

    state = sleeper.nfl_state()
    season = int(args.season or state.get("season") or 2026)
    console.print(f"[dim]Probing FantasyPros for season {season}. "
                  f"Key loaded from environment (not shown).[/dim]\n")

    table = Table(title="FantasyPros endpoint probe")
    for col in ("endpoint", "status", "players", "fields returned"):
        table.add_column(col, overflow="fold")
    for r in fp.probe(season):
        if r["ok"]:
            table.add_row(r["endpoint"], "[green]ok[/green]", str(r["count"]),
                          ", ".join(r["top_keys"]) or f"wrapper: {r['wrapper_keys']}")
        else:
            table.add_row(r["endpoint"], "[red]failed[/red]", "-", r["error"])
    console.print(table)


def cmd_podcasts(args):
    """Ingest recent fantasy podcasts and surface what was said about players."""
    from ff.model.mentions import find_mentions, summarize
    from ff.sources import podcasts

    universe = _universe()

    if args.fetch:
        with console.status("Resolving podcast feeds..."):
            feeds = podcasts.discover_feeds()
        console.print(f"[dim]{len(feeds)} feeds resolved[/dim]")
        episodes = []
        for show, url in feeds.items():
            try:
                episodes.extend(podcasts.recent_episodes(url, show, days=args.days,
                                                         limit=args.per_show))
            except Exception:  # noqa: BLE001 - one bad feed must not stop the rest
                console.print(f"[yellow]feed unavailable: {show[:40]}[/yellow]")
        episodes = [e for e in episodes if e.duration_seconds]
        episodes.sort(key=lambda e: e.duration_seconds or 0)
        if args.max_minutes:
            episodes = [e for e in episodes
                        if (e.duration_seconds or 0) <= args.max_minutes * 60]
        episodes = episodes[: args.limit_episodes]

        total_min = sum(e.duration_seconds or 0 for e in episodes) / 60
        console.print(f"[dim]{len(episodes)} episodes, {total_min:.0f} min of audio. "
                      f"Transcribing at roughly 10-15x real time.[/dim]\n")
        for i, ep in enumerate(episodes, 1):
            if podcasts.transcript_path(ep).exists() and not args.force:
                console.print(f"  [dim]{i}/{len(episodes)} cached: {ep.title[:56]}[/dim]")
                continue
            console.print(f"  {i}/{len(episodes)} {ep.show[:26]} - {ep.title[:44]} "
                          f"({(ep.duration_seconds or 0)//60}min)")
            try:
                path = podcasts.download(ep)
                podcasts.transcribe(ep, path, model_size=args.model, force=args.force)
            except Exception as exc:  # noqa: BLE001
                console.print(f"     [yellow]failed: {str(exc)[:80]}[/yellow]")

    transcripts = podcasts.load_transcripts()
    if not transcripts:
        console.print("[yellow]No transcripts yet.[/yellow] "
                      "Run [bold]ff podcasts --fetch[/bold] first.")
        raise SystemExit(1)

    # Who to look for: your roster, or anyone relevant.
    roster, _ = _load_local_roster(universe)
    if args.player:
        target = universe.resolve(" ".join(args.player))
        if not target:
            console.print(f"[red]No match for '{' '.join(args.player)}'[/red]")
            raise SystemExit(1)
        players = [target]
    elif roster and not args.all:
        players = roster
    else:
        players = [p for p in universe.filter(["QB", "RB", "WR", "TE"])
                   if p.search_rank is not None and p.search_rank < 400]

    mentions = find_mentions(transcripts, players)
    if args.injury:
        mentions = [m for m in mentions if m.injury_related]

    console.print(f"[dim]{len(transcripts)} transcripts | {len(players)} players "
                  f"tracked | {len(mentions)} mentions[/dim]\n")

    if args.quotes or args.player:
        for m in mentions[: args.limit]:
            tags = " ".join(t for t, v in (("injury", m.injury_related),
                                           ("opinion", m.opinion_related)) if v)
            console.print(f"[bold]{m.player.name}[/bold] - {m.show} [{m.clock}]"
                          f"{'  [yellow]' + tags + '[/yellow]' if tags else ''}")
            console.print(f"  [dim]{m.context}[/dim]\n")
    else:
        table = Table(title="Podcast mentions")
        for col in ("player", "pos", "mentions", "injury", "opinion", "shows"):
            table.add_column(col)
        for name, d in sorted(summarize(mentions).items(),
                              key=lambda kv: -kv[1]["total"])[: args.limit]:
            table.add_row(name, d["player"].position or "", str(d["total"]),
                          str(d["injury"]), str(d["opinion"]), str(len(d["shows"])))
        console.print(table)
        console.print("[dim]Add --quotes to read the actual passages, "
                      "--injury to filter to availability talk.[/dim]")
    console.print("[dim]Transcripts are local only and gitignored - "
                  "podcast audio is copyrighted, do not commit or redistribute.[/dim]")


def _draft_state_or_exit():
    from ff.advice.draftroom import DraftState

    state = DraftState.load()
    if state is None:
        console.print("[red]No draft in progress.[/red] Start one with "
                      "[bold]ff draft start --teams 10 --pick 4[/bold]")
        raise SystemExit(1)
    return state


def cmd_draft_start(args):
    from ff.advice.draftroom import DraftState

    state = DraftState(teams=args.teams, my_pick=args.pick, rounds=args.rounds,
                       snake=not args.linear)
    state.save()
    rnd, slot = state.round_and_slot()
    console.print(f"[green]Draft started.[/green] {state.teams} teams, "
                  f"{'snake' if state.snake else 'linear'}, your slot #{state.my_pick}.")
    console.print(f"[dim]Pick 1 is round {rnd}, slot {slot}. "
                  f"Mark picks with [bold]ff draft take \"Name\"[/bold] and your own "
                  f"with [bold]ff draft mine \"Name\"[/bold].[/dim]")


def _record_pick(args, mine: bool):
    universe = _universe()
    state = _draft_state_or_exit()
    name = " ".join(args.name)
    player = universe.resolve(name)
    if not player:
        console.print(f"[red]No match for '{name}'.[/red]")
        raise SystemExit(1)
    if (player.key in state.taken_keys):
        console.print(f"[yellow]{player.name} is already off the board.[/yellow]")
        raise SystemExit(1)
    pick_no = state.pick_number()
    rnd, slot = state.round_and_slot()
    state.taken.append({"name": player.name, "pos": player.position,
                        "team": player.team, "mine": mine, "pick": pick_no})
    state.save()
    who = "[green]YOU[/green]" if mine else "someone"
    console.print(f"Pick {pick_no} (R{rnd}.{slot:02d}): {who} took "
                  f"[bold]{player.name}[/bold] ({player.position}-{player.team})")
    if state.is_my_turn():
        console.print("[green]You are on the clock.[/green] Run [bold]ff draft now[/bold]")
    else:
        console.print(f"[dim]{state.picks_until_mine()} picks until you are up.[/dim]")


def cmd_draft_take(args):
    _record_pick(args, mine=False)


def cmd_draft_mine(args):
    _record_pick(args, mine=True)


def cmd_draft_undo(args):
    state = _draft_state_or_exit()
    if not state.taken:
        console.print("[yellow]Nothing to undo.[/yellow]")
        return
    last = state.taken.pop()
    state.save()
    console.print(f"[yellow]Undid:[/yellow] {last['name']} (pick {last.get('pick')})")


def cmd_draft_now(args):
    from ff.advice.draft import build_board
    from ff.advice.draftroom import (positional_runs, rank_candidates,
                                     roster_needs)

    universe = _universe()
    state = _draft_state_or_exit()
    shape, rules, origin = _league_shape_and_rules(args)
    shape.num_teams = state.teams
    season = int(args.season or sleeper.nfl_state().get("season") or 2026)

    with console.status("Building board..."):
        board = build_board(universe, shape, rules, season)
        candidates = rank_candidates(board, state, shape, universe, limit=args.limit)

    rnd, slot = state.round_and_slot()
    header = (f"Pick {state.pick_number()} - round {rnd}, slot {slot}"
              f"{'  [green]YOU ARE UP[/green]' if state.is_my_turn() else ''}")
    console.print(header)
    needs = roster_needs(state, shape, universe)
    unfilled = ", ".join(f"{p}x{n}" for p, n in needs.items() if n > 0) or "starters full"
    console.print(f"[dim]{origin} | your roster: {len(state.my_roster_names)} "
                  f"players | still needed: {unfilled}[/dim]")

    runs = positional_runs(state)
    if runs:
        hot = ", ".join(f"{p} x{n}" for p, n in sorted(runs.items(), key=lambda kv: -kv[1])
                        if n >= 3)
        if hot:
            console.print(f"[yellow]Run in progress (last 8 picks): {hot}[/yellow]")
    console.print()

    table = Table(title="Best available")
    for col in ("#", "player", "pos", "team", "VOR", "+lineup", "score",
                "tier left", "lasts?"):
        table.add_column(col)
    for i, c in enumerate(candidates, 1):
        warn = "[red]" if c.tier_remaining <= 2 else ""
        end = "[/red]" if warn else ""
        survival = ("[green]likely[/green]" if c.survival > 0.6 else
                    "[yellow]maybe[/yellow]" if c.survival > 0.3 else "[red]no[/red]")
        table.add_row(str(i), c.player.name, c.player.position or "",
                      c.player.team or "", f"{c.valued.vor:+.0f}",
                      f"{c.lineup_gain * 16:+.0f}", f"{c.score:.0f}",
                      f"{warn}{c.tier_remaining}{end}", survival)
    console.print(table)
    console.print("[dim]score = value over replacement plus how much he improves your "
                  "actual starting lineup. 'lasts?' estimates whether he survives to "
                  "your next pick.[/dim]")


def cmd_draft_roster(args):
    universe = _universe()
    state = _draft_state_or_exit()
    if not state.my_roster_names:
        console.print("[yellow]You have not drafted anyone yet.[/yellow]")
        return
    table = Table(title="Your draft picks")
    for col in ("pick", "player", "pos", "team"):
        table.add_column(col)
    for t in state.taken:
        if t.get("mine"):
            table.add_row(str(t.get("pick", "")), t["name"], t.get("pos") or "",
                          t.get("team") or "")
    console.print(table)
    console.print("[dim]Export to your lineup file with "
                  "[bold]ff draft export[/bold] when the draft ends.[/dim]")


def cmd_draft_export(args):
    from ff.util import DATA_DIR

    state = _draft_state_or_exit()
    names = state.my_roster_names
    if not names:
        console.print("[yellow]Nothing to export.[/yellow]")
        return
    path = DATA_DIR / "roster.txt"
    path.write_text("\n".join(names) + "\n")
    console.print(f"[green]Wrote {len(names)} players to {path}[/green]")
    console.print("[dim]ff lineup and ff waivers now use your real team.[/dim]")


def cmd_trade(args):
    from ff.advice.trades import evaluate
    from ff.sources import nflverse

    universe = _universe()
    shape, rules, origin = _league_shape_and_rules(args)
    season = int(args.season or sleeper.nfl_state().get("season") or 2026)

    roster, path = _load_local_roster(universe)
    if not roster:
        console.print(f"[red]No roster found.[/red] Create {path} or run "
                      "[bold]ff draft export[/bold].")
        raise SystemExit(1)

    def resolve_all(names, label):
        out = []
        for raw in names:
            p = universe.resolve(raw)
            if not p:
                console.print(f"[red]No match for '{raw}' in --{label}[/red]")
                raise SystemExit(1)
            out.append(p)
        return out

    giving = resolve_all(args.give or [], "give")
    getting = resolve_all(args.get or [], "get")
    if not giving and not getting:
        console.print("[red]Specify --give and/or --get.[/red]")
        raise SystemExit(1)

    with console.status("Projecting..."):
        projections = _projection_table(universe, shape, rules, season)
        byes = nflverse.bye_weeks(season) or nflverse.bye_weeks(season - 1)

    def ppg(p):
        v = projections.get(p.key)
        return v.projection.per_game if v else 0.0

    verdict = evaluate([(p, ppg(p)) for p in roster],
                       [(p, ppg(p)) for p in giving],
                       [(p, ppg(p)) for p in getting],
                       shape, bye_weeks=byes)

    console.print(f"[dim]{origin}[/dim]\n")
    console.print(f"[bold]You give:[/bold] " +
                  ", ".join(f"{p.name} ({ppg(p):.1f})" for p in giving))
    console.print(f"[bold]You get:[/bold]  " +
                  ", ".join(f"{p.name} ({ppg(p):.1f})" for p in getting))
    console.print()

    table = Table(show_header=False, box=None)
    table.add_row("Raw points out / in",
                  f"{verdict.outgoing_value:.1f}  ->  {verdict.incoming_value:.1f}")
    table.add_row("Starting lineup",
                  f"{verdict.lineup_before:.1f}  ->  {verdict.lineup_after:.1f} "
                  f"({verdict.lineup_after - verdict.lineup_before:+.1f}/week)")
    if verdict.depth_penalty:
        table.add_row("Depth cost", f"-{verdict.depth_penalty:.2f}/week")
    colour = ("green" if verdict.net > 0.3 else
              "yellow" if verdict.net > -0.3 else "red")
    table.add_row("[bold]Net[/bold]",
                  f"[{colour}]{verdict.net:+.2f} per week - {verdict.verdict.upper()}[/{colour}]")
    console.print(table)

    if verdict.starters_gained:
        console.print(f"\n[green]Enters your lineup:[/green] "
                      f"{', '.join(verdict.starters_gained)}")
    if verdict.starters_lost:
        console.print(f"[yellow]Leaves your lineup:[/yellow] "
                      f"{', '.join(verdict.starters_lost)}")
    for note in verdict.notes:
        console.print(f"[dim]- {note}[/dim]")
    console.print("\n[dim]Judged by change to your optimal starting lineup, not raw "
                  "point totals: bench points do not score.[/dim]")


def cmd_teams(args):
    """Market view of every team: implied scoring and projected wins."""
    from ff.model.context import build_contexts, multiplier
    from ff.sources import vegas

    season = int(args.season or sleeper.nfl_state().get("season") or 2026)
    with console.status("Reading betting markets..."):
        outlooks = vegas.team_outlooks(season)
    if not outlooks:
        console.print(f"[yellow]No posted lines for {season} yet.[/yellow]")
        raise SystemExit(1)

    universe = _universe()
    shape, rules, origin = _league_shape_and_rules(args)
    with console.status("Projecting quarterbacks..."):
        from ff.advice.draft import build_board
        board = build_board(universe, shape, rules, season, team_bias=0.0)
    qb_by_team = {}
    qb_name = {}
    for v in board:
        if (v.player.position or "").upper() == "QB" and v.player.team:
            if v.points > qb_by_team.get(v.player.team, 0.0):
                qb_by_team[v.player.team] = v.points
                qb_name[v.player.team] = v.player.name
    contexts = build_contexts(outlooks, qb_by_team)

    rows = sorted(contexts.values(), key=lambda c: -c.implied_total)
    table = Table(title=f"Team outlook - {season} (Vegas)")
    for col in ("team", "implied pts/gm", "proj wins", "QB", "RB/WR tilt", "games priced"):
        table.add_column(col)
    for c in rows[: args.limit]:
        wr = multiplier("WR", c, args.team_bias)
        rb = multiplier("RB", c, args.team_bias)
        colour = "green" if wr > 1.01 else ("red" if wr < 0.99 else "")
        tilt = f"{(rb - 1) * 100:+.1f}% / {(wr - 1) * 100:+.1f}%"
        table.add_row(c.team, f"{c.implied_total:.1f}", f"{c.projected_wins:.1f}",
                      qb_name.get(c.team, "?")[:18],
                      f"[{colour}]{tilt}[/{colour}]" if colour else tilt,
                      str(c.games_priced))
    console.print(table)
    console.print("[dim]Tilt is the adjustment applied to that team's running backs "
                  "and receivers. Sized from measured efficiency edges "
                  "(RB +10.8%, WR +8.7% team; WR +11.8% QB, top vs bottom quartile) "
                  "and halved, since consensus already prices some of it.[/dim]")


def cmd_app(args):
    """Launch the desktop app."""
    from ff.web.app import run_browser, run_window

    if args.browser:
        run_browser(port=args.port)
    else:
        run_window(port=args.port)
