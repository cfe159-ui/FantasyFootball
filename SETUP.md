# Setup

## Step 1 — Apply for Yahoo API access (do this first; it gates everything)

Yahoo no longer hands out Fantasy API access automatically. A human on the
Yahoo Fantasy Sports team reviews every application, and the portal warns that
**"incomplete or insufficiently detailed submissions cannot be evaluated and
will be closed without further correspondence."** There is no published review
time, so submit today and build against free data meanwhile.

Apply at: <https://sports.yahoo.com/developer/access/>

The form is written for businesses, but personal / single-league use is an
explicitly accepted category. Answer honestly as an individual — do not invent
a company.

### Fields you must supply (personal)

| Field | Notes |
|---|---|
| Name * | Your real name |
| Business Title * | No employer? `Independent Developer` is fine |
| Email Address * | Use the address on your Yahoo account |
| Phone Number * | Required, no way around it |
| Business Name & Address * | `Individual — <City, State>` if you have no company |
| Website URL or App Store Details * | A GitHub repo URL is ideal; otherwise state that it is a private personal project |

### Fields to paste

**Consumer-Facing Product or App Name**

> Personal Fantasy Football Assistant (not publicly distributed)

**Brief Company Description**

> No company — I am an individual hobbyist developer building this for my own
> use. Non-commercial, not distributed to anyone else.

**Describe Your Intended Use Case**

> Personal fantasy football assistant for my own use in one Yahoo league that I
> play in. Non-commercial, not distributed, single user (me). Read-only access.
> Yahoo data required: league settings and scoring rules, so recommendations
> match my league's actual scoring; my team's roster and weekly lineup slots,
> for start/sit analysis; league free agents and waiver status, for waiver-wire
> suggestions; weekly scoreboard and matchups, to weigh decisions against my
> opponent; and draft results, to track my roster during and after the draft.
> The tool produces recommendations that I execute manually in the Yahoo app —
> it performs no writes. Expected volume is low: a few dozen locally cached
> requests per week, during the NFL season only.

**Expected Users:** `Small (< 1,000 users)`

**Client ID:** leave blank if you have never registered a Yahoo developer app.

**Additional Notes**

> Read-only access is sufficient; I am not requesting write access. Use is
> limited to personal, single-league use — one user, one league, with no
> redistribution of Yahoo data.

## Step 2 — Register the app (after approval)

Go to <https://developer.yahoo.com/apps/create/> and create an app:

- **Application Type:** Web Application
- **Redirect URI:** `http://localhost:8723/callback`
- **API Permissions:** check **Fantasy Sports**, then **Read**

Copy the Client ID and Client Secret into `.env`:

```
cp .env.example .env
# then edit .env
```

## Step 3 — Authorize

```
ff auth
```

A browser window opens, you approve, and the tool captures the redirect
automatically. The refresh token is written to `data/yahoo_token.json` with
`0600` permissions and renews itself from then on.

## Step 4 — Point it at your league

```
ff leagues          # lists your NFL leagues
ff use <league_key> # remembers the one you want
```

## Works right now, without Yahoo

These read only free public data and need no credentials:

```
ff trending          # league-wide add/drop velocity
ff trending --drops
ff player "Ja'Marr Chase"
```
