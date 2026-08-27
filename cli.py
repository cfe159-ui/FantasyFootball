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

    with console.status(f"Projecting from {season - 3}-{season - 1} data..."):
        board = build_board(universe, shape, rules, season)

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
