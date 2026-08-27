# Data sources

The projection engine is an ensemble on purpose. Published multi-year accuracy
studies keep finding the same thing: **averaged projections beat every
individual source**, including the sources being averaged. FantasyFootball
Analytics' long-running study has "FFA Average" and "FFA Weighted" at or near
the top of the RB accuracy tables across eleven seasons, ahead of every single
contributor. So the goal is not to find the one best feed — it is to add
several decent ones and blend them.

Adding a source means producing a `{(name, position): points}` mapping and
passing it to `ensemble()` with a weight. That is the whole contract.

---

## Free, already wired in

### nflverse — the backbone
`ff/sources/nflverse.py`. Reads published parquet releases directly, so it does
not need `nflreadpy` (which requires Python ≥ 3.10; this project runs on the
system 3.9).

| Release | What it gives you |
|---|---|
| `stats_player` | 150 columns of weekly production: targets, target share, air yards share, EPA, snaps |
| `snap_counts` | Offensive snap share — the cleanest opportunity signal there is |
| `nextgen_stats` | Separation, cushion, time to throw, rush yards over expected |
| `pfr_advstats` | Pro Football Reference advanced splits |
| `ftn_charting` | Manual charting: play action, pressure, screens |
| `depth_charts` | Weekly depth chart position |
| `injuries` | Practice participation and game status |
| `schedules` | Used here to derive bye weeks |
| `pbp` | Full play-by-play back to 1999 |

Free, no key, no rate limit, rebuilt nightly in season. This is the best free
NFL data that exists — most paid products are built on top of it.

### Sleeper — player universe and crowd signal
`ff/sources/sleeper.py`. No auth, no key. Player metadata, injury status, depth
chart order, and league-wide add/drop velocity, which is the single best free
waiver signal available: it tells you what 
several million managers are reacting to within hours.

---

## Free, worth adding next

**nflverse `ffopportunity` / expected fantasy points** — models expected points
from opportunity alone. The gap between actual and expected is the best
regression signal in fantasy; players far above expected tend to fall back.
Strong addition, and free. <https://github.com/ffverse/ffopportunity>

**ESPN's undocumented fantasy API** — carries ESPN's own projections via the
`mProjectedStats` view, no key required. Unofficial and can break without
notice, which is exactly why it belongs in an ensemble rather than as a primary.

**Open-Meteo** — free weather, no API key, no signup. Wind above roughly 15mph
measurably suppresses passing and kicking. Cheap to add, genuinely predictive
for a handful of games per week.

---

## Paid, ranked for this specific use case

| Source | Cost | Why |
|---|---|---|
| **FantasyPros Premium** | $8.99/mo, bundled with their HOF subscription | Consensus of 130+ experts — the thing that keeps winning accuracy studies. Also ships external ID cross-references, which would strengthen player resolution here. Best single purchase. |
| **FTN Fantasy** | ~$69.99/yr | Jeff Ratcliffe and Tyler Orginski both finished top-10 in FantasyPros' expert accuracy contest in 2023, 2024 *and* 2025. Genuinely sharp projections. |
| **4for4** | $29/season Classic, $59 Pro | Cheapest credible paid projections. Good floor. |
| **Establish The Run** | $54.99 Draft Kit Pro | Strongest for draft prep specifically. Less useful for weekly automation. |
| **The Odds API** | $29/mo Professional | Vegas implied team totals are among the best single predictors of fantasy scoring. **Note: the free tier does not include NFL** — NBA/MLB only. |
| **PFF** | ~$40/mo+ | Best-in-class charting (route participation, grades). Overkill unless you want the underlying signal rather than projections. |
| **SportsDataIO / FantasyData** | Enterprise | Real API with SLAs. Priced for businesses, not one league. |

### A caution on the free tiers

- **FantasyPros free tier is sample data, non-production use.** It is for
  prototyping against the schema, not for running your season.
- **The Odds API free tier is NBA and MLB only.** NFL is not included.

---

## What to actually do

1. **Nothing, for now.** nflverse plus Sleeper plus the market prior is a
   legitimate baseline, and it costs nothing.
2. **If you spend one dollar, spend it on FantasyPros Premium** ($8.99/mo).
   Expert consensus is the most reliable single input, and it plugs straight
   into `ensemble()` as another provider.
3. **Add `ffopportunity` before adding a second paid source.** Free, and
   regression-to-expected catches the mistakes that consensus rankings make.

Weights are configurable — `DEFAULT_WEIGHTS` in `ff/advice/draft.py`. Adding a
strong paid source should shift weight away from the historical baseline, not
replace it: the blend is the point.

---

# How to actually get access

Verified live. Ordered by value per unit of hassle.

## 1. ffopportunity — expected fantasy points (free, no signup)

The single best free addition. It models how many fantasy points a player
*should* have scored given his opportunity — carries, targets, air yards, field
position. The gap between actual and expected is the most reliable regression
signal in fantasy: players far above expected tend to come back down.

Published as parquet on GitHub releases, exactly like nflverse. No key, no
account:

```
https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_pbp_pass_2025.parquet
https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_pbp_rush_2025.parquet
```

182 assets covering 2006 onward, in `.parquet`, `.csv`, and `.rds`. Drop-in for
`ff/sources/nflverse.py`, which already reads this exact release pattern.

## 2. ESPN projections (free, no key)

ESPN's fantasy API is undocumented but open. This returns player data including
ESPN's own projections:

```
https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2025/players
  ?scoringPeriodId=1&view=kona_player_info
```

Confirmed returning HTTP 200 unauthenticated. Unofficial, so treat it as a
source that may vanish — which is exactly why it belongs in an ensemble rather
than as a primary.

## 3. Open-Meteo — weather (free, no key, no signup)

```
https://api.open-meteo.com/v1/forecast?latitude=42.77&longitude=-78.78&hourly=wind_speed_10m
```

Confirmed working with no credentials. Sustained wind over ~15mph measurably
suppresses passing and kicking. You need a stadium coordinate table (32 rows)
and to skip domes.

## 4. FantasyPros — the one worth paying for

$8.99/month, bundled with their HOF subscription. Consensus of 130+ experts,
which is the thing that keeps winning accuracy studies, plus external ID
cross-references that would strengthen player resolution here.

1. Create a FantasyPros account (or sign in) at <https://www.fantasypros.com/>
2. Subscribe to HOF — API access is bundled, not sold separately
3. Request a key at <https://secure.fantasypros.com/api-keys/request/>
   (redirects to login if you are not signed in)
4. Send it as the `x-api-key` header on every request

The free tier returns **sample data for non-production use**. It is for
prototyping against the schema, not for running your season.

## What I would actually do

Add **ffopportunity** first. It is free, it needs no account, it loads through
code that already exists here, and expected-points regression catches the
mistakes that consensus rankings make. Only then consider paying FantasyPros.
