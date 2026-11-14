"""VGI worker exposing cricket matches, scorecards, standings, and rankings as a SQL catalog."""

from __future__ import annotations

import json
import os
import sys

import pyarrow as pa
from vgi import Worker
from vgi.catalog import Catalog, ConstraintInfo, Schema, Table, constraint_record

from vgi_cricket import functions as f
from vgi_cricket.meta import column_comments
from vgi_cricket.schemas import (
    COMPETITIONS_SCHEMA,
    ECB_TOURNAMENTS_SCHEMA,
    ESPN_MATCH_SCHEMA,
    MATCH_SCHEMA,
    PEOPLE_SCHEMA,
    PLAYER_RANKINGS_SCHEMA,
    TEAM_RANKINGS_SCHEMA,
    VENUES_SCHEMA,
)

SOURCE_URL = "https://github.com/Query-farm/vgi-cricket"

_ATTRIBUTION = (
    "Match, ball-by-ball, and register data from Cricsheet (https://cricsheet.org), licensed under the Open "
    "Data Commons Attribution License (ODC-By 1.0). Live data from ESPN's undocumented site API, rankings "
    "from the ICC rankings feed, and English domestic data from the ECB PulseLive API are unofficial, "
    "fetched on demand, never rehosted, and subject to their owners' terms. Not affiliated with or endorsed "
    "by Cricsheet, ESPN, Disney, the ICC, the ECB, or any league."
)


def _table_tags(
    *, category: str, title: str, keywords: list[str], llm: str, md: str, examples: list[tuple[str, str]]
) -> dict[str, str]:
    return {
        "provider": "cricket",
        "vgi.category": category,
        "vgi.title": title,
        "vgi.keywords": json.dumps(keywords),
        "vgi.doc_llm": llm,
        "vgi.doc_md": md,
        "vgi.example_queries": json.dumps([{"description": text, "sql": sql} for text, sql in examples]),
    }


_COMPETITIONS_TAGS = _table_tags(
    category="reference",
    title="Supported Competitions",
    keywords=["competitions", "leagues", "codes", "IPL", "BBL", "The Hundred", "Tests"],
    llm=(
        "The competition codes accepted by matches(), standings(), batting_stats(), and the other Cricsheet "
        "functions, with gender, format, region, and the ESPN league id used by schedule(). Start here to "
        "find the right code (for example ipl, wpl, bbl, wbb, hnd_male, cch, psl, tests_male, t20s_female)."
    ),
    md="Static catalogue of supported Cricsheet archives and their ESPN league ids.",
    examples=[
        (
            "Franchise T20 leagues and their ESPN ids.",
            "SELECT competition, name, region, espn_league_id FROM cricket.main.competitions "
            "WHERE team_type = 'club' AND match_format = 'T20' ORDER BY competition",
        )
    ],
)

_CURRENT_MATCHES_TAGS = _table_tags(
    category="live",
    title="Live and Upcoming Matches",
    keywords=["live scores", "scorepanel", "today", "fixtures", "results"],
    llm=(
        "Every live, recently finished, and upcoming match on ESPN's cricket scorepanel across all "
        "competitions, one row per match with status, score text, and result summary. Start here for "
        "'what is on now' and to find an event_id for live_batting_scorecard() or live_commentary()."
    ),
    md=(
        "ESPN scorepanel, cached for 30 seconds. `state` is pre, in, or post. ESPN data is unofficial "
        "and subject to ESPN's terms."
    ),
    examples=[
        (
            "Matches in progress right now.",
            "SELECT league_name, name, home_score, away_score, status FROM cricket.main.current_matches "
            "WHERE state = 'in'",
        ),
        (
            "Today's finished results.",
            "SELECT league_name, name, summary FROM cricket.main.current_matches WHERE state = 'post'",
        ),
    ],
)

_RECENT_TAGS = _table_tags(
    category="matches",
    title="Recently Played Matches",
    keywords=["recent results", "last week", "cricsheet", "scores"],
    llm=(
        "Every match Cricsheet has published from the last seven days, across all competitions and "
        "internationals: teams, innings scores, result, and venue. Use matches() for a full competition "
        "season and current_matches for live play."
    ),
    md="Cricsheet `recently_played_7` archive. Results lag play by one to three days.",
    examples=[
        (
            "Results from the last week, newest first.",
            "SELECT start_date, event_name, team1, team1_score, team2, team2_score, winner "
            "FROM cricket.main.current_recent_matches ORDER BY start_date DESC",
        )
    ],
)

_TEAM_RANKING_TAGS = _table_tags(
    category="rankings",
    title="Current ICC Team Rankings",
    keywords=["ICC rankings", "team rankings", "Test", "ODI", "T20I", "rating"],
    llm=(
        "Current ICC team rankings for all five formats in one table: test, odi, t20 (men) and odiw, t20w "
        "(women), with rank, matches, points, and rating."
    ),
    md="Official ICC team rankings, cached for six hours.",
    examples=[
        (
            "Number-one team in each format.",
            "SELECT format, team, rating FROM cricket.main.current_team_rankings WHERE rank = 1 "
            "ORDER BY format",
        )
    ],
)

_PLAYER_RANKING_TAGS = _table_tags(
    category="rankings",
    title="Current ICC Player Rankings",
    keywords=["ICC rankings", "player rankings", "batting", "bowling", "all-rounder"],
    llm=(
        "Current ICC player rankings (top 100 each) for every format (test, odi, t20, odiw, t20w) and "
        "discipline (bat, bowl, allrounder) in one table."
    ),
    md="Official ICC player rankings, cached for six hours. Tied players share a rank.",
    examples=[
        (
            "Top five Test bowlers.",
            "SELECT rank, player, country, rating FROM cricket.main.current_player_rankings "
            "WHERE format = 'test' AND discipline = 'bowl' ORDER BY rank LIMIT 5",
        )
    ],
)

_PEOPLE_TAGS = _table_tags(
    category="reference",
    title="People Register",
    keywords=["players", "identifiers", "cricinfo id", "register", "cross-reference"],
    llm=(
        "Cricsheet's register of players and officials with cross-source identifiers. person_id joins to "
        "batter_id/bowler_id/player_id in Cricsheet results; key_cricinfo equals the ESPN athlete id in "
        "the live_* functions."
    ),
    md="Cricsheet `people.csv` register (ODC-By 1.0).",
    examples=[
        (
            "Find a player's identifiers.",
            "SELECT person_id, unique_name, key_cricinfo FROM cricket.main.people WHERE name = 'V Kohli'",
        )
    ],
)

_VENUES_TAGS = _table_tags(
    category="reference",
    title="Venue Register",
    keywords=["venues", "grounds", "stadiums", "identifiers"],
    llm=(
        "Cricsheet's register of grounds. venue_id joins matches.venue_id; unique_name is the canonical "
        "ground name, which can differ from the name printed on a match (Narendra Modi Stadium is "
        "registered as Motera Stadium, Ahmedabad)."
    ),
    md="Cricsheet `venues.csv` register (ODC-By 1.0).",
    examples=[
        (
            "Grounds in Mumbai.",
            "SELECT DISTINCT unique_name FROM cricket.main.venues WHERE unique_name LIKE '%Mumbai%'",
        )
    ],
)

_ECB_TOURNAMENTS_TAGS = _table_tags(
    category="ecb",
    title="English Cricket Tournaments",
    keywords=["ECB", "England", "The Hundred", "County Championship", "Vitality Blast", "tournament id"],
    llm=(
        "Every tournament in the official ECB feed with its id, type, and scheduled match counts. Use the "
        "tournament_id with ecb_fixtures() and ecb_standings(); higher ids are more recent."
    ),
    md="Official ECB PulseLive data, cached for a day.",
    examples=[
        (
            "Recent County Championship tournament ids.",
            "SELECT tournament_id, name, match_types FROM cricket.main.ecb_tournaments "
            "WHERE name LIKE '%County Championship%' ORDER BY tournament_id DESC LIMIT 4",
        )
    ],
)

_EXECUTABLE_EXAMPLES = json.dumps(
    [
        {
            "name": "competitions_are_listed",
            "description": "The static competition catalogue includes the IPL.",
            "sql": (
                "SELECT count(*) FILTER (WHERE competition = 'ipl') = 1 AS has_ipl "
                "FROM cricket.main.competitions"
            ),
            "expected_result": [{"has_ipl": True}],
        },
        {
            "name": "ipl_2026_final_winner",
            "description": "Royal Challengers Bengaluru won the IPL 2026 final by 5 wickets.",
            "sql": (
                "SELECT winner, win_by_wickets FROM cricket.main.matches('ipl', season := '2026') "
                "WHERE match_id = 1535465"
            ),
            "expected_result": [{"winner": "Royal Challengers Bengaluru", "win_by_wickets": 5}],
        },
        {
            "name": "test_team_rankings_populated",
            "description": "ICC Test team rankings have ranked teams.",
            "sql": "SELECT count(*) >= 9 AS populated FROM cricket.main.team_rankings('test')",
            "expected_result": [{"populated": True}],
        },
    ]
)

_AGENT_TEST_TASKS = json.dumps(
    [
        {
            "name": "ipl_2026_final_batting_card",
            "prompt": (
                "Show the full batting card of the 2026 IPL final in batting order, including each "
                "batter's ESPNcricinfo player id."
            ),
        },
        {
            "name": "ipl_2026_final_innings_and_bowling",
            "prompt": (
                "For the 2026 IPL final (match 1535465), give each innings' total, wickets, and overs, "
                "the bowler with the best figures in that innings, and the ground's canonical name."
            ),
        },
        {
            "name": "ipl_2026_final_over_by_over",
            "prompt": "How many runs and wickets came in each over of the chase in the 2026 IPL final?",
        },
        {
            "name": "ipl_2026_points_table",
            "prompt": "What was the IPL 2026 league-stage points table, with net run rate?",
        },
        {
            "name": "ipl_2026_playoff_teams_top_scorers",
            "prompt": (
                "For each team that finished in the top four of the IPL 2026 league table, who scored the "
                "most runs for that team across the whole season, playoffs included? Return league "
                "position, team, batter, and total runs, ordered by league position."
            ),
        },
        {
            "name": "ipl_2026_wankhede_wickets",
            "prompt": (
                "Which bowler took the most wickets at Wankhede Stadium in IPL 2026, and how many? Return "
                "the bowler and the wicket count."
            ),
        },
        {
            "name": "ipl_2026_leaders",
            "prompt": "Who scored the most runs and who took the most wickets in IPL 2026?",
        },
        {
            "name": "hundred_2026_official_table",
            "prompt": "Show the official ECB table for The Hundred men's 2026 (tournament 76753).",
        },
        {
            "name": "hundred_2026_final_result",
            "prompt": "What were the scores and result of the 2026 Hundred men's final (tournament 76753)?",
        },
        {
            "name": "espn_final_top_performers",
            "prompt": (
                "Using ESPN data for the 2026 IPL final, name the fixture, its top scorer, its best "
                "bowler, and how many wickets fell according to the ball-by-ball commentary."
            ),
        },
        {
            "name": "icc_rankings_now",
            "prompt": (
                "Which team is ranked number one in each ICC format, and who is the top-ranked Test batter?"
            ),
        },
        {
            "name": "whats_on_now",
            "prompt": "Which cricket matches are being played right now, and which finished this past week?",
        },
    ]
)


def _key_constraints(table: str, key: tuple[str, ...]) -> tuple[ConstraintInfo, ...]:
    """Primary-key and NOT NULL records for a read-only table keyed by ``key``."""
    records = [
        constraint_record(
            ["main"],
            table,
            "pk",
            "PRIMARY_KEY",
            [[column] for column in key],
            enforcement="ENFORCED",
            validation="ALL_ROWS",
        )
    ]
    records += [
        constraint_record(
            ["main"],
            table,
            f"{column}_not_null",
            "NOT_NULL",
            [[column]],
            enforcement="ENFORCED",
            validation="ALL_ROWS",
        )
        for column in key
    ]
    return tuple(records)


def _table(
    name: str, function: type, comment: str, tags: dict, schema: pa.Schema, key: tuple[str, ...], rows: int
) -> Table:
    return Table(
        name=name,
        function=function,
        comment=comment,
        tags=tags,
        column_comments=column_comments(schema),
        constraints=_key_constraints(name, key),
        cardinality_estimate=rows,
    )


CRICKET_CATALOG = Catalog(
    name="cricket",
    default_schema="main",
    source_url=SOURCE_URL,
    comment=(
        "Read-only cricket data: Cricsheet ball-by-ball history, ESPN live scores, ICC rankings, and "
        "official ECB tables"
    ),
    tags={
        "provider": "cricket",
        "domain": "sports",
        "vgi.title": "Cricket Data",
        "vgi.subtitle": (
            "Cricket matches, scorecards, ball-by-ball deliveries, standings, and ICC rankings as SQL tables."
        ),
        "vgi.source_url": SOURCE_URL,
        "vgi.author": "Query Farm LLC <hello@query.farm>",
        "vgi.copyright": "Worker (c) 2026 Query Farm LLC. " + _ATTRIBUTION,
        "vgi.license": "MIT",
        "data_license": (
            "ODC-By-1.0 (Cricsheet); ESPN, ICC, and ECB data are subject to their owners' terms"
        ),
        "attribution": _ATTRIBUTION,
        "vgi.support_contact": "https://github.com/Query-farm/vgi-cricket/issues",
        "vgi.support_policy_url": "https://github.com/Query-farm/vgi-cricket/blob/main/README.md",
        "vgi.keywords": json.dumps(
            ["cricket", "IPL", "Big Bash", "The Hundred", "Test cricket", "ICC rankings", "ball-by-ball"]
        ),
        "vgi.executable_examples": _EXECUTABLE_EXAMPLES,
        "vgi.agent_test_tasks": _AGENT_TEST_TASKS,
        "vgi.doc_llm": (
            "Read-only cricket data from four public sources, queryable as ordinary SQL. Cricsheet "
            "(ODC-By 1.0) supplies completed-match history for men's and women's internationals and the "
            "major franchise and domestic leagues (IPL, WPL, BBL, WBBL, The Hundred, County Championship, "
            "T20 Blast, PSL, CPL, SA20, MLC, and more): match results, innings totals, batting and bowling "
            "cards, every delivery, league tables computed with net run rate, and season or all-time "
            "player aggregates, selected by competition code and season. ESPN supplies live and upcoming "
            "matches, season fixture lists, in-play batting and bowling cards, and ball-by-ball commentary "
            "by event id. The ICC feed supplies official team and player rankings for Tests, ODIs, and "
            "T20Is (men and women). The ECB feed supplies official English fixtures and tables with bonus "
            "points. Match ids are shared by Cricsheet, ESPN, and ESPNcricinfo, and the people register "
            "maps player ids across sources."
        ),
        "vgi.doc_md": (
            "# Cricket data\n\n"
            "Query international and franchise cricket directly from SQL: match results, full batting and "
            "bowling cards, ball-by-ball deliveries, computed league tables with net run rate, season "
            "leaderboards, live scores and commentary, ICC rankings, and official ECB tables.\n\n"
            "## Sources and licensing\n\n"
            "Historical data comes from [Cricsheet](https://cricsheet.org) under ODC-By 1.0 and must be "
            "attributed. ESPN, ICC, and ECB endpoints are public but undocumented or unlicensed; the worker "
            "fetches them on demand, caches politely, and never rehosts them. It is not affiliated with any "
            "of these organisations or leagues."
        ),
    },
    schemas=[
        Schema(
            path=["main"],
            comment="Cricket matches, scorecards, standings, rankings, and live scores",
            tags={
                "provider": "cricket",
                "domain": "sports",
                "vgi.title": "Cricket",
                "vgi.keywords": json.dumps(
                    ["cricket", "scorecards", "standings", "rankings", "live scores", "ball-by-ball"]
                ),
                "vgi.categories": json.dumps(
                    [
                        {
                            "name": "reference",
                            "title": "Reference",
                            "description": "Competition codes and the Cricsheet people and venue registers.",
                            "keywords": ["competitions", "players", "venues", "identifiers"],
                        },
                        {
                            "name": "matches",
                            "title": "Matches and Schedules",
                            "description": "Match lists, fixtures, scores, and results.",
                            "keywords": ["matches", "results", "fixtures", "schedule"],
                        },
                        {
                            "name": "scorecards",
                            "title": "Scorecards",
                            "description": "Innings totals and batting and bowling cards from Cricsheet.",
                            "keywords": ["scorecard", "batting", "bowling", "innings"],
                        },
                        {
                            "name": "ball-by-ball",
                            "title": "Ball-by-Ball",
                            "description": "Every delivery of a match or season.",
                            "keywords": ["deliveries", "balls", "overs", "wickets"],
                        },
                        {
                            "name": "standings",
                            "title": "Standings",
                            "description": "League tables with points and net run rate.",
                            "keywords": ["points table", "standings", "net run rate"],
                        },
                        {
                            "name": "player-stats",
                            "title": "Player Statistics",
                            "description": "Season and all-time batting and bowling aggregates.",
                            "keywords": ["runs", "wickets", "averages", "leaders"],
                        },
                        {
                            "name": "live",
                            "title": "Live",
                            "description": "Live scores, scorecards, and commentary from ESPN.",
                            "keywords": ["live", "scores", "commentary"],
                        },
                        {
                            "name": "rankings",
                            "title": "ICC Rankings",
                            "description": "Official ICC team and player rankings.",
                            "keywords": ["rankings", "ratings", "ICC"],
                        },
                        {
                            "name": "ecb",
                            "title": "ECB",
                            "description": "Official English domestic tournaments, fixtures, and tables.",
                            "keywords": ["ECB", "England", "County Championship", "The Hundred"],
                        },
                    ]
                ),
                "vgi.doc_llm": (
                    "Start with competitions for codes, current_matches for live play, "
                    "current_recent_matches for last week's results, and "
                    "current_team_rankings/current_player_rankings for ICC rankings. Parameterized functions "
                    "cover competition seasons, scorecards, ball-by-ball deliveries, computed standings, "
                    "player aggregates, ESPN live cards, and ECB tables."
                ),
                "vgi.doc_md": (
                    "## Cricket analytical data\n\n"
                    "Rows are match-, innings-, batter-innings-, bowler-innings-, delivery-, team-, or "
                    "player-grain.\n\n"
                    "## Arguments\n\n"
                    "Cricsheet functions take a competition code first, then an optional season label and "
                    "match id. ESPN functions take an event id (equal to the Cricsheet match id). ECB "
                    "functions take a tournament id.\n\n"
                    "## Freshness\n\n"
                    "Cricsheet lags play by one to three days; ESPN live data is cached for 30 seconds, ICC "
                    "rankings for six hours, and ECB data for ten minutes."
                ),
                "vgi.example_queries": json.dumps(
                    [{"description": text, "sql": sql} for text, sql in f.COMPOSED_EXAMPLES]
                    + json.loads(_CURRENT_MATCHES_TAGS["vgi.example_queries"])
                ),
            },
            functions=list(f.FUNCTIONS),
            tables=[
                _table(
                    "competitions",
                    f.CompetitionsScan,
                    "Supported competition codes with ESPN league ids",
                    _COMPETITIONS_TAGS,
                    COMPETITIONS_SCHEMA,
                    ("competition",),
                    32,
                ),
                _table(
                    "current_matches",
                    f.CurrentMatchesScan,
                    "Live, recent, and upcoming matches from ESPN, one row per match",
                    _CURRENT_MATCHES_TAGS,
                    ESPN_MATCH_SCHEMA,
                    ("event_id",),
                    30,
                ),
                _table(
                    "current_recent_matches",
                    f.CurrentRecentMatchesScan,
                    "Matches from the last seven days from Cricsheet, one row per match",
                    _RECENT_TAGS,
                    MATCH_SCHEMA,
                    ("match_id",),
                    40,
                ),
                _table(
                    "current_team_rankings",
                    f.CurrentTeamRankingsScan,
                    "Current ICC team rankings for every format",
                    _TEAM_RANKING_TAGS,
                    TEAM_RANKINGS_SCHEMA,
                    ("format", "team"),
                    230,
                ),
                _table(
                    "current_player_rankings",
                    f.CurrentPlayerRankingsScan,
                    "Current ICC player rankings for every format and discipline",
                    _PLAYER_RANKING_TAGS,
                    PLAYER_RANKINGS_SCHEMA,
                    ("format", "discipline", "player_id"),
                    1_510,
                ),
                _table(
                    "people",
                    f.PeopleScan,
                    "Cricsheet people register with cross-source identifiers",
                    _PEOPLE_TAGS,
                    PEOPLE_SCHEMA,
                    ("person_id",),
                    17_500,
                ),
                _table(
                    "venues",
                    f.VenuesScan,
                    "Cricsheet register of cricket grounds and their canonical names",
                    _VENUES_TAGS,
                    VENUES_SCHEMA,
                    ("venue_id",),
                    1_500,
                ),
                _table(
                    "ecb_tournaments",
                    f.EcbTournamentsScan,
                    "Every tournament in the official ECB feed",
                    _ECB_TOURNAMENTS_TAGS,
                    ECB_TOURNAMENTS_SCHEMA,
                    ("tournament_id",),
                    561,
                ),
            ],
        )
    ],
)


class CricketWorker(Worker):
    """Worker process hosting the read-only cricket catalog."""

    catalog = CRICKET_CATALOG


def main() -> None:
    """Run over stdio by default, or HTTP when VGI's ``--http`` flag is supplied."""
    CricketWorker.main()


def main_http() -> None:
    """Run HTTP with production-safe configuration requirements."""
    if not os.environ.get("VGI_SIGNING_KEY"):
        raise SystemExit(
            "VGI_SIGNING_KEY is required for HTTP mode so state remains valid across restarts and replicas"
        )
    auth_configured = bool(os.environ.get("VGI_BEARER_TOKENS") or os.environ.get("VGI_JWT_ISSUER"))
    auth_configured = auth_configured or os.environ.get("VGI_PROXY_PROOF_MODE", "").lower() == "require"
    if not auth_configured and os.environ.get("VGI_ALLOW_ANONYMOUS") != "1":
        raise SystemExit(
            "HTTP authentication is required; configure bearer/JWT/proxy proof, or explicitly set "
            "VGI_ALLOW_ANONYMOUS=1 for an intentionally public deployment"
        )
    argv = sys.argv[1:]
    if "--http" not in argv:
        argv = ["--http", *argv]
    if "--describe" not in argv and "--no-describe" not in argv:
        argv = ["--no-describe", *argv]
    if not any(value == "--cors-origins" or value.startswith("--cors-origins=") for value in argv):
        argv = ["--cors-origins", "", *argv]
    sys.argv = [sys.argv[0], *argv]
    CricketWorker.main()
