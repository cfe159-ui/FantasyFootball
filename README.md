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

## Commands

Working now, no credentials required:

```
ff status                     # what is configured and working
ff trending --pos RB WR       # league-wide add/drop velocity
ff trending --drops --hours 48
ff player "Ja'Marr Chase"
```

After Yahoo approval (see `SETUP.md`):

```
ff auth                       # browser OAuth, token stored 0600
ff leagues
ff use <league_key>
ff league                     # settings and scoring, read from your league
ff roster
```

## Notes on the data

`ff trending` reports what the whole Sleeper population is adding. That is a
signal about **opportunity** — usually an injury or a depth-chart change — not
about value. A player with 140k adds is being chased; it does not follow that
he is worth a top waiver claim in your league.
