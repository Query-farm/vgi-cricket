<p align="center">
  <a href="https://query.farm/vgi/">
    <img src="https://raw.githubusercontent.com/Query-farm/vgi-kalshi/main/docs/vgi-logo.png" alt="Vector Gateway Interface logo" width="320">
  </a>
</p>

<h1 align="center">vgi-cricket</h1>

<p align="center">
  Cricket matches, scorecards, ball-by-ball data, standings, and ICC rankings as ordinary DuckDB tables.<br>
  A <strong>read-only</strong> <a href="https://query.farm/vgi/">VGI</a> worker, built by <a href="https://query.farm">🚜 Query.Farm</a>
</p>

<p align="center">
  <a href="https://github.com/Query-farm/vgi-cricket/actions/workflows/ci.yml"><img src="https://github.com/Query-farm/vgi-cricket/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/python-3.13%2B-3776AB.svg" alt="Python 3.13+">
  <a href="https://query.farm/vgi/"><img src="https://img.shields.io/badge/VGI-read--only-6f42c1.svg" alt="Read-only VGI worker"></a>
</p>

---

`vgi-cricket` fetches data on demand from four public sources and reformats it
as typed Arrow data for Haybarn SQL:

- **[Cricsheet](https://cricsheet.org)** ball-by-ball archives (ODC-By 1.0) for
  completed internationals and franchise and domestic leagues,
- **ESPN's** cricket site API for live and upcoming matches, scorecards, and
  commentary,
- the **ICC** rankings feed behind icc-cricket.com, and
- the **ECB** PulseLive API for official English fixtures and tables.

It requires no credentials and does not rehost or redistribute any dataset.

## Quick start

The worker is not published to PyPI. `uvx` runs the tagged release straight from
GitHub and caches it after the first use:

```bash
uvx --from git+https://github.com/Query-farm/vgi-cricket@v0.2.0 vgi-cricket --help
```

Launch Haybarn with the catalog pre-attached:

```bash
uvx haybarn-cli \
  -cmd "INSTALL vgi FROM community; LOAD vgi; ATTACH 'cricket' (TYPE vgi, LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-cricket@v0.2.0 vgi-cricket');" \
  :memory:
```

Or attach it in an existing Haybarn session:

```sql
INSTALL vgi FROM community;
LOAD vgi;
ATTACH 'cricket' (
  TYPE vgi,
  LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-cricket@v0.2.0 vgi-cricket'
);
```

For source development, run `uv lock --check --no-sources`, then
`uv sync --no-sources` and `uv run --no-sources vgi-cricket`.
The lockfile resolves all dependencies from package registries and does not
require a sibling checkout.

## Catalog surface

The catalog exposes eight tables and sixteen parameterized table functions:

| Kind | Objects | Source |
| --- | --- | --- |
| Reference | `competitions`, `people`, `venues` | Static catalogue, Cricsheet registers |
| Current | `current_matches`, `current_recent_matches`, `current_team_rankings`, `current_player_rankings` | ESPN, Cricsheet, ICC |
| Match history | `matches()`, `innings_summary()`, `batting_scorecard()`, `bowling_scorecard()`, `deliveries()` | Cricsheet |
| Tables and leaders | `standings()`, `batting_stats()`, `bowling_stats()` | Computed from Cricsheet |
| Live | `schedule()`, `live_batting_scorecard()`, `live_bowling_scorecard()`, `live_commentary()` | ESPN |
| Rankings | `team_rankings()`, `player_rankings()` | ICC |
| England | `ecb_tournaments`, `ecb_fixtures()`, `ecb_standings()` | ECB |

Cricsheet functions take a competition code first. `SELECT * FROM
cricket.main.competitions` lists the 32 supported codes, including `ipl`, `wpl`,
`bbl`, `wbb`, `hnd_male`, `hnd_female`, `cch`, `ntb`, `psl`, `cpl`, `sat`,
`mlc`, `ilt`, `bpl`, `lpl`, `npl`, `ssm`, `ssh`, the ICC World Cups, and
`tests_male`/`odis_female`/`t20s_male` style international archives. The
rolling `recently_played_2`, `recently_played_7`, and `recently_played_30`
archives are accepted too.

Match ids are shared: a Cricsheet `match_id` equals the ESPN `event_id` and the
ESPNcricinfo match id, and `people.key_cricinfo` equals ESPN's athlete id.

## Usage

### Results and scorecards

```sql
-- IPL 2026 knockouts with scores
SELECT match_id, start_date, stage, team1, team1_score, team2, team2_score, winner
FROM cricket.main.matches('ipl', season := '2026')
WHERE stage IS NOT NULL
ORDER BY start_date;

-- Batting card of the IPL 2026 final
SELECT innings_number, batting_position, batter, runs, balls, fours, sixes, dismissal_kind
FROM cricket.main.batting_scorecard('ipl', match_id := 1535465)
ORDER BY innings_number, batting_position;

-- Bowling card of the same match
SELECT innings_number, bowler, overs, maidens, runs_conceded, wickets, economy
FROM cricket.main.bowling_scorecard('ipl', match_id := 1535465)
ORDER BY innings_number, bowling_order;
```

`season` is Cricsheet's label: `'2026'` or a split-year label such as
`'2025/26'`. A year also matches the split-year season that starts in it, so
`batting_stats('bbl', season := '2025')` selects BBL 2025/26.

### Ball-by-ball

`deliveries()` returns every ball, wides and no-balls included, for one match or
one season:

```sql
-- Death-over economy in IPL 2026 (overs 17-20)
SELECT bowler, sum(runs_total) * 6.0 / count(*) FILTER (WHERE is_legal) AS economy
FROM cricket.main.deliveries('ipl', season := '2026')
WHERE over_number >= 17 AND NOT super_over
GROUP BY bowler
HAVING count(*) FILTER (WHERE is_legal) >= 60
ORDER BY economy;

-- League table alongside each team's powerplay (overs 1-6) batting
WITH pp AS (
  SELECT batting_team AS team,
         round(sum(runs_total) * 6.0 / count(*) FILTER (WHERE is_legal), 2) AS powerplay_run_rate,
         count(*) FILTER (WHERE is_wicket) AS powerplay_wickets
  FROM cricket.main.deliveries('ipl', season := '2026')
  WHERE over_number <= 6 AND NOT super_over
  GROUP BY team
)
SELECT s.position, s.team, s.points, pp.powerplay_run_rate, pp.powerplay_wickets
FROM cricket.main.standings('ipl', season := '2026') s
JOIN pp USING (team)
ORDER BY s.position;
```

### Standings and leaderboards

`standings()` computes a league-stage points table with net run rate from
Cricsheet results. Super-over winners take the win; knockout matches are
excluded; an all-out side is charged its full allocation of balls. With no
season it uses the archive's most recent season.

```sql
SELECT position, team, played, won, lost, no_result, points, net_run_rate
FROM cricket.main.standings('ipl', season := '2026')
ORDER BY position;

-- Orange and Purple Cap races
SELECT player, teams, runs, average, strike_rate
FROM cricket.main.batting_stats('ipl', season := '2026')
ORDER BY runs DESC LIMIT 10;

SELECT player, teams, wickets, economy, best_figures
FROM cricket.main.bowling_stats('ipl', season := '2026')
ORDER BY wickets DESC, average LIMIT 10;

-- All-time IPL six hitters
SELECT player, sixes FROM cricket.main.batting_stats('ipl') ORDER BY sixes DESC LIMIT 10;
```

The computed table does not model penalty deductions or bonus points. For
English competitions use the official ECB table instead (below).

### Live scores

```sql
-- Everything ESPN lists right now
SELECT league_name, name, home_score, away_score, status
FROM cricket.main.current_matches
WHERE state = 'in';

-- A league's season fixtures (competition code or ESPN league id)
SELECT start_time, description, name, summary
FROM cricket.main.schedule('bbl', season_year := 2026)
ORDER BY start_time;

-- Live cards and commentary for one event_id
SELECT player, runs, balls_faced, dismissal FROM cricket.main.live_batting_scorecard(1535465);
SELECT overs, short_text FROM cricket.main.live_commentary(1535465) WHERE is_wicket;
```

### ICC rankings

```sql
SELECT format, team, rating
FROM cricket.main.current_team_rankings
WHERE rank = 1;

SELECT rank, player, country, rating
FROM cricket.main.player_rankings('odi', 'bat')
ORDER BY rank LIMIT 10;
```

Formats are `test`, `odi`, `t20` (men) and `odiw`, `t20w` (women); disciplines
are `bat`, `bowl`, and `allrounder`.

### Official ECB tables

```sql
SELECT tournament_id, name, match_types
FROM cricket.main.ecb_tournaments
WHERE name LIKE '%County Championship%'
ORDER BY tournament_id DESC;

SELECT position, team, played, won, lost, drawn, batting_bonus, bowling_bonus, points
FROM cricket.main.ecb_standings(76738)
ORDER BY position;

SELECT start_time, label, team1_score, team2_score, result
FROM cricket.main.ecb_fixtures(76753)
ORDER BY start_time;
```

## Data sources, licences, and affiliation

**This project is not affiliated with, endorsed by, or sponsored by Cricsheet,
ESPN, ESPNcricinfo, The Walt Disney Company, the International Cricket Council,
the England and Wales Cricket Board, the BCCI, or any league or team.** All
names and marks belong to their owners.

- **Cricsheet.** Match, ball-by-ball, people, and venue data come from
  [Cricsheet](https://cricsheet.org) and are licensed under the
  [Open Data Commons Attribution License (ODC-By 1.0)](https://opendatacommons.org/licenses/by/1-0/).
  If you publish results derived from these tables, attribute Cricsheet.
  Cricsheet publishes matches one to three days after play and deliberately
  [withholds matches involving the Afghanistan men's team](https://cricsheet.org/article/explanation-for-withholding-of-afghanistani-matches).
  The worker never downloads the 149 MB all-matches archive.
- **ESPN.** `current_matches`, `schedule()`, and the `live_*` functions read
  ESPN's undocumented cricket site API. These endpoints are not a published or
  licensed API, are subject to ESPN's (Disney's) terms of use, and can change
  without notice. The worker fetches them only on demand, caches responses
  politely, identifies itself honestly, and never rehosts the data.
- **ICC.** Rankings come from the public feed behind icc-cricket.com, using the
  client id embedded in that site. The feed has no published licence; the id
  can rotate (override it with `CRICKET_ICC_CLIENT_ID`).
- **ECB.** English fixtures and tables come from the public ECB PulseLive API,
  which has no published licence and is subject to the ECB's website terms.
  Club contact details in that feed are never returned.

You are responsible for ensuring that your use of the data complies with each
upstream's terms. The MIT licence below covers this worker's code only.

## Production HTTP deployment

Stdio is the recommended transport when Haybarn can launch the worker locally.
Use HTTP when the worker must be shared across hosts. The production HTTP entry
point fails closed unless it has a stable signing key and either authentication
or an explicit anonymous-access decision:

```bash
export VGI_SIGNING_KEY='a-long-random-secret-shared-by-every-replica'
export VGI_BEARER_TOKENS='replace-with-a-random-token=haybarn'

uvx --from git+https://github.com/Query-farm/vgi-cricket@v0.2.0 vgi-cricket-http \
  --host 0.0.0.0 \
  --port 8000 \
  --cors-origins 'https://sql.example.com' \
  --log-format json
```

`vgi-cricket-http` disables the description pages and cross-origin access unless
they are explicitly configured. Terminate TLS at a trusted proxy or load
balancer. All replicas must receive the same `VGI_SIGNING_KEY`; rotate it as a
coordinated deployment because outstanding state tokens are signed with it.

Supported authentication options are:

- `VGI_BEARER_TOKENS`: comma-separated `token=principal` pairs.
- `VGI_JWT_ISSUER` and `VGI_JWT_AUDIENCE`: JWT/JWKS authentication.
- `VGI_PROXY_PROOF_MODE=require` with the corresponding VGI proxy-proof
  settings when a trusted proxy authenticates users.

Because this connector exposes read-only public data, an intentionally public
service can set `VGI_ALLOW_ANONYMOUS=1`. It should still be protected by gateway
rate limits so that it does not amplify traffic to the upstreams. The health
probe is `GET /health`.

### Container

```bash
docker build -t vgi-cricket .
docker run --rm -p 8000:8000 \
  -e VGI_SIGNING_KEY='replace-with-a-stable-random-secret' \
  -e VGI_BEARER_TOKENS='replace-with-a-random-token=haybarn' \
  vgi-cricket
```

The image runs as an unprivileged user, emits JSON logs, disables interactive
description pages, and includes a health check.

## Reliability and operations

Every request carries the User-Agent
`vgi-cricket/<version> (+https://github.com/Query-farm/vgi-cricket)` and follows
redirects. Calls use a 60-second request timeout, a 120-second total query
deadline, at most four attempts, and at most two simultaneous upstream requests
per process. HTTP 429 and transient 5xx responses are retried with jittered
exponential backoff; `Retry-After` is honored with a 30-second per-retry cap.
Bodies that arrive gzip-compressed without a `Content-Encoding` header (an ESPN
quirk) are decoded.

Responses are cached in process (256 MB, least recently used) for a per-source
freshness window, and that window is forwarded to VGI clients as the result's
cache TTL:

| Source | Freshness |
| --- | --- |
| ESPN scorepanel, summaries, commentary | 30 seconds |
| ESPN schedules | 5 minutes |
| ECB fixtures and standings | 10 minutes |
| Cricsheet archives | 1 hour, then revalidated |
| ICC rankings | 6 hours |
| Cricsheet registers, ECB tournament list | 1 day |

Cricsheet archives and registers are also kept in a private disk cache
(`$XDG_CACHE_HOME/vgi-cricket`, or `~/.cache/vgi-cricket`) and revalidated with
`If-Modified-Since`/`If-None-Match`, so an unchanged 5-24 MB archive is not
downloaded again. A cached copy is served if Cricsheet is unreachable. Set
`CRICKET_CACHE_DIR` to move the cache or to `off` to disable it. ECB sometimes
refuses a whole page of results over a single bad record; the worker re-reads
such a page in smaller slices and logs how many records it had to skip.

Base URLs can be redirected for hermetic tests with `CRICKET_CRICSHEET_BASE_URL`,
`CRICKET_ESPN_SITE_BASE_URL`, `CRICKET_ESPN_WEB_BASE_URL`,
`CRICKET_ICC_BASE_URL`, and `CRICKET_ECB_BASE_URL`. Do not set them from
untrusted user input.

## Development and release checks

```bash
uv lock --check --no-sources
uv sync --no-sources
uv run --no-sources ruff check .
uv run --no-sources ruff format --check .
uv run --no-sources pytest
uv build
uvx twine check dist/*
uvx --no-sources --from vgi-lint-check==2.5.0 vgi-lint lint \
  'uv run --no-sources cricket_worker.py' \
  --no-execute --no-check-links --audit-waivers --fail-on warning
```

The offline suite serves trimmed, recorded upstream responses from
`tests/fixtures` through a local HTTP server, including a Haybarn end-to-end test
that attaches the worker and queries every table. Live upstream smoke tests are
opt-in with `uv run pytest -m live` and also run in a serialized scheduled
workflow alongside the executing VGI lint. Publishing is driven by releases. Create a GitHub release named `vX.Y.Z` that
matches the version in `pyproject.toml`. CI then builds, validates and attests
the wheel and sdist, attaches them to the release, and publishes a
multi-platform container image to `ghcr.io/query-farm/vgi-cricket`. The package is
not published to PyPI; install it from the release tag as shown in the quick
start.

## License

Copyright 2026 Query Farm LLC - https://query.farm

The worker's code is released under the [MIT License](LICENSE). Data returned by
the worker remains subject to the upstream licences and terms described above.
