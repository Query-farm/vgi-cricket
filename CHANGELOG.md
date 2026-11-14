# Changelog

All notable changes to this project are documented here.

## [Unreleased]

- Composed example queries that join the worker's functions and tables (knockout top scorers,
  ground first-innings averages, powerplay batting beside the league table), published on the
  schema and on the driving function, plus two composed agent tasks with exact-compare graders.

## [0.2.0]

- Port to VGI protocol 3.3 (vgi-python 0.51.0); requires the current VGI DuckDB extension
  (3.2 extensions are incompatible). Catalog linted with vgi-lint-check 2.5.0.

## [0.1.0]

- Initial release on VGI protocol 3.2 (vgi-python 0.50) with vgi-lint-check 2.2.0.
- Cricsheet (ODC-By 1.0) history for 32 competitions: matches, innings totals, batting
  and bowling cards, ball-by-ball deliveries, computed league tables with net run rate,
  and season or all-time batting and bowling aggregates, plus the people and venue
  registers.
- ESPN live data: the scorepanel, league schedules, in-play batting and bowling cards,
  and ball-by-ball commentary.
- Official ICC team and player rankings for every format.
- Official ECB tournaments, fixtures, and standings with bonus points and net run rate.
- Polite HTTP client: honest User-Agent, bounded retries honoring `Retry-After`,
  per-source in-process caching, and a revalidating disk cache for Cricsheet archives.
