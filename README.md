# Fantasy Football Agent

A personal assistant for a Yahoo fantasy football team: it reads your league,
blends it with free public NFL data, and tells you what to do. It never touches
your roster — Yahoo's API grants read access only, so every output is a
recommendation you act on yourself.

## Design

The system is split so that **nothing interesting depends on Yahoo approval**:

```
Yahoo API  ──►  league state      (settings, roster, matchups, free agents)
                     │              gated on Yahoo's application review
                     ▼
              player resolution   ──►  analysis  ──►  recommendations
                     ▲
Sleeper API ──►  player universe   (injuries, depth charts, add/drop velocity)
nflverse    ──►  historical stats  no auth, available right now
```

**Player resolution** is the keystone. Providers do not share IDs — Sleeper's
`yahoo_id` is populated for only ~23% of active players and is null even for
stars like Ja'Marr Chase. So identity is resolved on normalized name +
position, which was measured to produce **zero collisions** across all 1,026
active NFL skill players. Unmatched players are surfaced, never silently
dropped.

## Layout

| Path | Purpose |
|---|---|
| `ff/util.py` | Name/team normalization, disk cache, HTTP with backoff |
| `ff/players.py` | `Player`, `PlayerUniverse`, cross-provider resolution |
| `ff/sources/sleeper.py` | Free player universe, injuries, trending adds/drops |
| `ff/sources/yahoo.py` | OAuth2 client, token refresh, response flattener |
| `ff/sources/yahoo_league.py` | League settings, rosters, free agents, draft |
| `ff/config.py` | Which league you're managing |
| `cli.py` | Command line entry point |

## Desktop app

```
ff app                     # native macOS window
ff app --browser           # or in your browser
python make_app.py         # build a double-clickable .app bundle
```

`make_app.py` writes `dist/Fantasy Agent.app`, which you can drag into
/Applications. It launches this project's venv directly, so editing the source
changes the app on next launch -- there is nothing to rebuild.

Eight views: draft room, my team, rankings, waivers, trade, team outlook,
podcasts, settings. The projection board is computed once per league
configuration and cached in the server process, so the first load takes a few
seconds and everything after is instant.

## Talking to it

With an `ANTHROPIC_API_KEY` in `.env`, the draft console holds a spoken
conversation:

    speech in   your browser's SpeechRecognition (only the transcript leaves)
    reasoning   Claude Opus 5, with the live board as context
    speech out  your browser's speechSynthesis

There is no realtime or voice endpoint to connect to -- the Messages API is
text in, text out -- so speech happens in the browser at both ends. Answers run
at low effort with a 700-token ceiling and a system prompt demanding two or
three sentences, because a spoken answer that rambles is worse than no answer
while you are on the clock.

Speech recognition needs Chrome or Safari; the native window's WKWebView may
not expose it, in which case the console offers a text box instead. Run
`ff app --browser` if you want the voice loop for certain.

Without a key everything else works unchanged and the console falls back to
draft-driven guidance.

## Commands

Draft day:

```
ff draft start --teams 10 --pick 4   # begin, with your slot
ff draft take "Player Name"          # someone else picked
ff draft mine "Player Name"          # you picked
ff draft now                         # best available for THIS pick
ff draft export                      # write your team to roster.txt
```

In season:

```
ff board --scarcity        # draft board plus positional cliffs
ff lineup                  # optimal starters, injury-discounted
ff waivers                 # targets ranked by lineup improvement
ff trade --give "A" --get "B"
ff podcasts --fetch --injury --quotes
```

Setup and inspection:

```
ff status                  # what is configured and working
ff league-setup --teams 10 --ppr 0.5 --slots "QB:1,RB:2,WR:2,TE:1,W/R/T:2,K:1,DST:1,BN:6"
ff fp-check                # what your FantasyPros key unlocks
ff player "Name"
ff trending --pos RB WR
ff teams                   # Vegas implied scoring, projected wins, tilt applied
```

After Yahoo approval (see `SETUP.md`):

```
ff auth
ff leagues
ff use <league_key>
ff league                  # real settings replace the manual ones
ff roster
```

## The projection ensemble

Four sources, blended. Averaged projections beat every individual source in
published accuracy studies, so the architecture optimizes for adding sources
cheaply rather than perfecting one model.

| Source | Weight | Contributes |
|---|---|---|
| FantasyPros consensus | 0.40 | 130+ experts; also covers K and DST |
| Expected points (ffopportunity) | 0.25 | Regression signal from opportunity |
| Historical production | 0.20 | Actual output under your scoring |
| Market prior | 0.15 | Rookies and changed situations |

Without a FantasyPros key the other three renormalize to 1.00 rather than
silently losing 40% of the blend.

## Things worth knowing

**Injury discounts are measured, not assumed.** Joining three seasons of NFL
injury reports against whether the player actually recorded a stat line:
Questionable players appear **58%** of the time, not the ~75% folklore assumes,
and produce 93% of their season average when they do. A Questionable tag is
worth ~56% of a projection; Questionable plus did-not-practice, 47%.

**Lineups are solved, not filled greedily.** Flex eligibility sets overlap
without nesting -- `W/R` and `W/T` both take receivers, neither contains the
other -- so greedy slot-filling strands points on your bench. Solved as
maximum-weight bipartite matching and verified exact against brute force.

**Team and QB context is measured, not assumed.** Across 875 player-seasons,
comparing actual points to expected points (which already controls for
opportunity), skill players on good offenses beat expectation: RB +10.8%,
WR +8.7%, TE +2.3% between top and bottom quartile. Quarterback quality matters
more to receivers than team quality does: WR +11.8%, RB +9.5%, TE +3.5%. The
"bad teams throw more so their receivers eat" theory does not survive the data --
good teams gave skill players both more opportunity and better efficiency. The
tilt is halved before application, since consensus already prices some of it,
and clamped to +/-8%. Disable with `--no-team-bias`.

**Waivers and trades are judged by lineup improvement.** Bench points do not
score. A 14-ppg quarterback is worth zero to a team already starting a 20-ppg
one, and a 2-for-1 consolidation can win while losing on raw totals.

## Tests

```
.venv/bin/python -m pytest tests/ -q
```

50 tests, no network or API keys required.

## Notes on the data

`ff trending` reports what the whole Sleeper population is adding. That is a
signal about **opportunity** — usually an injury or a depth-chart change — not
about value. A player with 140k adds is being chased; it does not follow that
he is worth a top waiver claim in your league.
