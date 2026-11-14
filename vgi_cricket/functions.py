"""VGI table functions for the cricket catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.cache_control import CacheControl
from vgi.metadata import FunctionExample
from vgi.table_function import (
    ProcessParams,
    TableCardinality,
    TableFunctionGenerator,
    bind_fixed_schema,
    init_single_worker,
)
from vgi_rpc.rpc import OutputCollector

from vgi_cricket import api, loaders
from vgi_cricket.competitions import ARCHIVE_CHOICES
from vgi_cricket.meta import batch_from_rows, result_columns_schema
from vgi_cricket.schemas import (
    BATTING_SCORECARD_SCHEMA,
    BATTING_STATS_SCHEMA,
    BOWLING_SCORECARD_SCHEMA,
    BOWLING_STATS_SCHEMA,
    COMMENTARY_SCHEMA,
    COMPETITIONS_SCHEMA,
    DELIVERIES_SCHEMA,
    ECB_FIXTURES_SCHEMA,
    ECB_STANDINGS_SCHEMA,
    ECB_TOURNAMENTS_SCHEMA,
    ESPN_MATCH_SCHEMA,
    INNINGS_SCHEMA,
    LIVE_BATTING_SCHEMA,
    LIVE_BOWLING_SCHEMA,
    MATCH_SCHEMA,
    PEOPLE_SCHEMA,
    PLAYER_RANKINGS_SCHEMA,
    STANDINGS_SCHEMA,
    TEAM_RANKINGS_SCHEMA,
    VENUES_SCHEMA,
)

_CRICSHEET = "Cricsheet (cricsheet.org), ODC-By 1.0"
_ESPN = "ESPN's undocumented cricket site API"
_ICC = "the ICC rankings feed behind icc-cricket.com"
_ECB = "the ECB PulseLive cricket API"

_FINAL = "1535465"  # IPL 2026 final, Royal Challengers Bengaluru v Gujarat Titans

# Composed examples combine two or more of the worker's objects in one query. Each is defined once,
# listed on the example list of the function that drives it, and collected in COMPOSED_EXAMPLES for
# the schema-level example list.
KNOCKOUT_TOP_SCORERS = (
    "Top scorer in each IPL 2026 knockout match: matches() picks the playoff fixtures and "
    "batting_scorecard() supplies the batters.",
    "SELECT m.start_date, m.stage, b.batting_team, b.batter, b.runs, b.balls "
    "FROM cricket.main.matches('ipl', season := '2026') m "
    "JOIN cricket.main.batting_scorecard('ipl', season := '2026') b USING (match_id) "
    "WHERE m.stage IS NOT NULL "
    "QUALIFY row_number() OVER (PARTITION BY m.match_id ORDER BY b.runs DESC, b.balls) = 1 "
    "ORDER BY m.start_date",
)
GROUND_FIRST_INNINGS_AVERAGES = (
    "Average first-innings total at each IPL 2026 ground, using the venues register's canonical names "
    "(innings_summary joined to matches and venues on venue_id).",
    "SELECT v.unique_name AS ground, count(*) AS matches_played, round(avg(i.runs), 1) AS avg_first_innings "
    "FROM cricket.main.innings_summary('ipl', season := '2026') i "
    "JOIN cricket.main.matches('ipl', season := '2026') m USING (match_id) "
    "JOIN cricket.main.venues v USING (venue_id) "
    "WHERE i.innings_number = 1 AND NOT i.super_over "
    "GROUP BY ALL ORDER BY avg_first_innings DESC, ground",
)
POWERPLAY_VS_STANDINGS = (
    "The IPL 2026 league table alongside each team's powerplay (overs 1-6) batting run rate and wickets "
    "lost, aggregated from deliveries() over the whole season.",
    "WITH pp AS (SELECT batting_team AS team, "
    "round(sum(runs_total) * 6.0 / count(*) FILTER (WHERE is_legal), 2) AS powerplay_run_rate, "
    "count(*) FILTER (WHERE is_wicket) AS powerplay_wickets "
    "FROM cricket.main.deliveries('ipl', season := '2026') WHERE over_number <= 6 AND NOT super_over "
    "GROUP BY team) "
    "SELECT s.position, s.team, s.points, pp.powerplay_run_rate, pp.powerplay_wickets "
    "FROM cricket.main.standings('ipl', season := '2026') s JOIN pp USING (team) ORDER BY s.position",
)
COMPOSED_EXAMPLES = [KNOCKOUT_TOP_SCORERS, GROUND_FIRST_INNINGS_AVERAGES, POWERPLAY_VS_STANDINGS]


def _emit(loaded: loaders.Rows, output_schema: pa.Schema, out: OutputCollector) -> None:
    rows, ttl = loaded
    out.emit(batch_from_rows(rows, output_schema), cache_control=CacheControl(ttl=ttl))
    out.finish()


def _tags(
    *,
    category: str,
    title: str,
    llm: str,
    md: str,
    schema: pa.Schema,
    examples: list[tuple[str, str]],
    source: str,
) -> dict[str, str]:
    return {
        "provider": "cricket",
        "vgi.category": category,
        "vgi.title": title,
        "source": source,
        "vgi.result_columns_schema": result_columns_schema(schema),
        "vgi.doc_llm": llm,
        "vgi.doc_md": md,
        "vgi.example_queries": json.dumps([{"description": text, "sql": sql} for text, sql in examples]),
    }


def _examples(examples: list[tuple[str, str]]) -> list[FunctionExample]:
    return [FunctionExample(sql=sql, description=text) for text, sql in examples]


# --------------------------------------------------------------------------- argument types

_COMPETITION_DOC = (
    "Cricsheet competition code from the competitions table (for example ipl, bbl, hnd_male, tests_male), "
    "or recently_played_2/7/30 for every match Cricsheet published in the last 2, 7, or 30 days."
)
_SEASON_DOC = (
    "Season label such as '2026' or '2025/26'. '2025' also matches the split-year label 2025/26. "
    "NULL selects every season."
)


def _competition() -> Arg:
    return Arg(0, arrow_type=pa.string(), choices=ARCHIVE_CHOICES, doc=_COMPETITION_DOC)


def _season(doc: str = _SEASON_DOC) -> Arg:
    return Arg("season", default=None, arrow_type=pa.string(), doc=doc)


def _match_id() -> Arg:
    return Arg(
        "match_id",
        default=None,
        arrow_type=pa.int64(),
        ge=1,
        doc="Match id (equal to the ESPNcricinfo/ESPN id). NULL selects every match in the season.",
    )


@dataclass(kw_only=True)
class CompetitionSeasonArgs:
    """A Cricsheet archive and an optional season."""

    competition: Annotated[str, _competition()]
    season: Annotated[str | None, _season()]


@dataclass(kw_only=True)
class StandingsArgs:
    """A Cricsheet archive and an optional season, defaulting to the latest."""

    competition: Annotated[str, _competition()]
    season: Annotated[
        str | None,
        _season("Season label such as '2026' or '2025/26'. NULL selects the archive's most recent season."),
    ]


@dataclass(kw_only=True)
class MatchSelectionArgs:
    """A Cricsheet archive with an optional match id and season."""

    competition: Annotated[str, _competition()]
    match_id: Annotated[int | None, _match_id()]
    season: Annotated[str | None, _season()]


@dataclass(kw_only=True)
class ScheduleArgs:
    """An ESPN league and an optional season."""

    league: Annotated[
        str,
        Arg(
            0,
            arrow_type=pa.string(),
            pattern=r"^([a-z0-9_]+|[0-9]+)$",
            doc=(
                "Competition code with an ESPN mapping (ipl, wpl, bbl, wbb, hnd_male, hnd_female, cch, ntb, "
                "psl, cpl, bpl, npl, ssh, odc, icc_mens_cricket_world_cup, icc_mens_t20_world_cup) or a "
                "numeric ESPN league id such as 8048."
            ),
        ),
    ]
    season_year: Annotated[
        int | None,
        Arg(
            "season_year",
            default=None,
            arrow_type=pa.int32(),
            ge=1990,
            le=2100,
            doc="ESPN season as a calendar year, such as 2026. NULL returns ESPN's current window.",
        ),
    ]


_EVENT_ID = Arg(
    0, arrow_type=pa.int64(), ge=1, doc="ESPN event id (equal to the Cricsheet and ESPNcricinfo match id)."
)


@dataclass(kw_only=True)
class EventArgs:
    """One ESPN event."""

    event_id: Annotated[int, _EVENT_ID]


@dataclass(kw_only=True)
class CommentaryArgs:
    """One ESPN event and an optional commentary page."""

    event_id: Annotated[int, _EVENT_ID]
    page: Annotated[
        int | None,
        Arg(
            "page",
            default=None,
            arrow_type=pa.int32(),
            ge=1,
            doc="25-ball commentary page starting at 1. NULL reads every page (up to 120).",
        ),
    ]


_FORMAT = Arg(
    0,
    arrow_type=pa.string(),
    choices=api.ICC_FORMATS,
    doc="ICC format: test, odi, t20 (men), odiw, or t20w (women).",
)


@dataclass(kw_only=True)
class TeamRankingArgs:
    """One ICC format."""

    format: Annotated[str, _FORMAT]


@dataclass(kw_only=True)
class PlayerRankingArgs:
    """One ICC format and discipline."""

    format: Annotated[str, _FORMAT]
    discipline: Annotated[
        str,
        Arg(
            1, arrow_type=pa.string(), choices=("bat", "bowl", "allrounder"), doc="bat, bowl, or allrounder."
        ),
    ]


@dataclass(kw_only=True)
class TournamentArgs:
    """One ECB tournament."""

    tournament_id: Annotated[
        int, Arg(0, arrow_type=pa.int64(), ge=1, doc="ECB tournament id from the ecb_tournaments table.")
    ]


# --------------------------------------------------------------------------- Cricsheet functions

_MATCHES_EXAMPLES = [
    (
        "IPL 2026 knockout results.",
        "SELECT match_id, start_date, stage, team1, team1_score, team2, team2_score, winner "
        "FROM cricket.main.matches('ipl', season := '2026') WHERE stage IS NOT NULL ORDER BY start_date",
    ),
    (
        "Most IPL player-of-the-match awards in 2026.",
        "SELECT player_of_match, count(*) AS awards FROM cricket.main.matches('ipl', season := '2026') "
        "GROUP BY ALL ORDER BY awards DESC LIMIT 5",
    ),
    KNOCKOUT_TOP_SCORERS,
]


@init_single_worker
@bind_fixed_schema
class MatchesFunction(TableFunctionGenerator[CompetitionSeasonArgs]):
    """Match list and results for a Cricsheet competition."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = MATCH_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "matches"
        description = "Matches, scores, and results for a competition and season from Cricsheet"
        categories = ["cricket", "matches"]
        projection_pushdown = True
        tags = _tags(
            category="matches",
            title="Competition Matches",
            source=_CRICSHEET,
            llm=(
                "One row per match in a Cricsheet competition archive: date, teams, innings scores, toss, "
                "result, margin, venue, player of the match, and knockout stage. Filter by season. Use "
                "match_id to drill into innings_summary, batting_scorecard, bowling_scorecard, and "
                "deliveries. Cricsheet lags real results by one to three days and withholds Afghanistan "
                "men's matches."
            ),
            md=(
                "Completed matches from [Cricsheet](https://cricsheet.org) (ODC-By 1.0). `stage` is NULL for "
                "league-stage matches. Super-over and bowl-out winners appear in `winner` with `decided_by`."
            ),
            schema=MATCH_SCHEMA,
            examples=_MATCHES_EXAMPLES,
        )
        examples = _examples(_MATCHES_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A season is tens of matches; a whole archive can be thousands."""
        return TableCardinality(estimate=80, max=10_000)

    @classmethod
    def process(cls, params: ProcessParams[CompetitionSeasonArgs], state: None, out: OutputCollector) -> None:
        """Load and emit the selected matches."""
        _emit(loaders.matches(params.args.competition, params.args.season), params.output_schema, out)


_INNINGS_EXAMPLES = [
    (
        "Innings totals in the IPL 2026 final.",
        f"SELECT innings_number, batting_team, runs, wickets, overs, extras "
        f"FROM cricket.main.innings_summary('ipl', match_id := {_FINAL}) ORDER BY innings_number",
    ),
    (
        "Highest IPL 2026 team totals.",
        "SELECT match_id, batting_team, runs, wickets, overs FROM cricket.main.innings_summary('ipl', "
        "season := '2026') WHERE NOT super_over ORDER BY runs DESC LIMIT 5",
    ),
    GROUND_FIRST_INNINGS_AVERAGES,
]


@init_single_worker
@bind_fixed_schema
class InningsSummaryFunction(TableFunctionGenerator[MatchSelectionArgs]):
    """Per-innings totals computed from Cricsheet deliveries."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = INNINGS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "innings_summary"
        description = "Per-innings totals, overs, extras, and targets from Cricsheet"
        categories = ["cricket", "scorecards"]
        projection_pushdown = True
        tags = _tags(
            category="scorecards",
            title="Innings Summary",
            source=_CRICSHEET,
            llm=(
                "One row per innings (super overs flagged) with runs, wickets, legal balls, overs notation, "
                "run rate per six balls, extras breakdown, declared/all-out flags, and chase targets. Select "
                "one match with match_id or a whole season."
            ),
            md="Innings totals computed from Cricsheet ball-by-ball data.",
            schema=INNINGS_SCHEMA,
            examples=_INNINGS_EXAMPLES,
        )
        examples = _examples(_INNINGS_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Two to four innings per match."""
        return TableCardinality(estimate=150, max=50_000)

    @classmethod
    def process(cls, params: ProcessParams[MatchSelectionArgs], state: None, out: OutputCollector) -> None:
        """Load and emit innings totals."""
        args = params.args
        _emit(loaders.innings(args.competition, args.match_id, args.season), params.output_schema, out)


_BATTING_CARD_EXAMPLES = [
    (
        "Batting card of the IPL 2026 final.",
        f"SELECT innings_number, batting_position, batter, runs, balls, fours, sixes, dismissal_kind "
        f"FROM cricket.main.batting_scorecard('ipl', match_id := {_FINAL}) "
        "ORDER BY innings_number, batting_position",
    ),
    (
        "Every IPL 2026 century.",
        "SELECT start_date, batter, batting_team, bowling_team, runs, balls "
        "FROM cricket.main.batting_scorecard('ipl', season := '2026') WHERE runs >= 100 ORDER BY runs DESC",
    ),
]


@init_single_worker
@bind_fixed_schema
class BattingScorecardFunction(TableFunctionGenerator[MatchSelectionArgs]):
    """Batting cards computed from Cricsheet deliveries."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = BATTING_SCORECARD_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "batting_scorecard"
        description = "Batting cards, one row per batter per innings, from Cricsheet"
        categories = ["cricket", "scorecards"]
        projection_pushdown = True
        tags = _tags(
            category="scorecards",
            title="Batting Scorecard",
            source=_CRICSHEET,
            llm=(
                "Batting-card rows for one match (match_id) or every match in a season: batting position, "
                "runs, balls, fours, sixes, dots, strike rate, and dismissal. A season selection is a "
                "player-innings log, useful for per-match form and milestones. Super overs are excluded."
            ),
            md="Batter-innings rows computed from Cricsheet deliveries. `batter_id` joins to `people`.",
            schema=BATTING_SCORECARD_SCHEMA,
            examples=_BATTING_CARD_EXAMPLES,
        )
        examples = _examples(_BATTING_CARD_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Up to eleven batters per innings."""
        return TableCardinality(estimate=1_000, max=500_000)

    @classmethod
    def process(cls, params: ProcessParams[MatchSelectionArgs], state: None, out: OutputCollector) -> None:
        """Load and emit batting cards."""
        args = params.args
        _emit(
            loaders.batting_scorecard(args.competition, args.match_id, args.season), params.output_schema, out
        )


_BOWLING_CARD_EXAMPLES = [
    (
        "Bowling card of the IPL 2026 final.",
        f"SELECT innings_number, bowler, overs, maidens, runs_conceded, wickets, economy "
        f"FROM cricket.main.bowling_scorecard('ipl', match_id := {_FINAL}) "
        "ORDER BY innings_number, bowling_order",
    ),
    (
        "IPL 2026 four-wicket hauls.",
        "SELECT start_date, bowler, bowling_team, wickets, runs_conceded, overs "
        "FROM cricket.main.bowling_scorecard('ipl', season := '2026') WHERE wickets >= 4 "
        "ORDER BY wickets DESC, runs_conceded",
    ),
]


@init_single_worker
@bind_fixed_schema
class BowlingScorecardFunction(TableFunctionGenerator[MatchSelectionArgs]):
    """Bowling cards computed from Cricsheet deliveries."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = BOWLING_SCORECARD_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "bowling_scorecard"
        description = "Bowling cards, one row per bowler per innings, from Cricsheet"
        categories = ["cricket", "scorecards"]
        projection_pushdown = True
        tags = _tags(
            category="scorecards",
            title="Bowling Scorecard",
            source=_CRICSHEET,
            llm=(
                "Bowling-card rows for one match or a season: overs, maidens, runs conceded (bat runs plus "
                "wides and no-balls), wickets credited to the bowler, economy per six balls, and dots."
            ),
            md="Bowler-innings rows computed from Cricsheet deliveries. `bowler_id` joins to `people`.",
            schema=BOWLING_SCORECARD_SCHEMA,
            examples=_BOWLING_CARD_EXAMPLES,
        )
        examples = _examples(_BOWLING_CARD_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A handful of bowlers per innings."""
        return TableCardinality(estimate=900, max=400_000)

    @classmethod
    def process(cls, params: ProcessParams[MatchSelectionArgs], state: None, out: OutputCollector) -> None:
        """Load and emit bowling cards."""
        args = params.args
        _emit(
            loaders.bowling_scorecard(args.competition, args.match_id, args.season), params.output_schema, out
        )


_DELIVERIES_EXAMPLES = [
    (
        "The last over of the IPL 2026 final.",
        f"SELECT over_number, ball_in_over, bowler, batter, runs_total, is_wicket "
        f"FROM cricket.main.deliveries('ipl', match_id := {_FINAL}) WHERE innings_number = 2 "
        "ORDER BY over_number DESC, ball_in_over DESC LIMIT 6",
    ),
    (
        "IPL 2026 sixes by bowler conceding them.",
        "SELECT bowler, count(*) AS sixes FROM cricket.main.deliveries('ipl', season := '2026') "
        "WHERE runs_batter = 6 AND is_boundary GROUP BY ALL ORDER BY sixes DESC LIMIT 10",
    ),
    POWERPLAY_VS_STANDINGS,
]


@init_single_worker
@bind_fixed_schema
class DeliveriesFunction(TableFunctionGenerator[MatchSelectionArgs]):
    """Ball-by-ball deliveries from Cricsheet."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = DELIVERIES_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "deliveries"
        description = "Ball-by-ball deliveries for one match or one season from Cricsheet"
        categories = ["cricket", "ball-by-ball"]
        projection_pushdown = True
        tags = _tags(
            category="ball-by-ball",
            title="Ball-by-Ball Deliveries",
            source=_CRICSHEET,
            llm=(
                "One row per delivery, wides and no-balls included: over, ball sequence, batter, bowler, "
                "runs off the bat, extras by type, boundary and wicket flags, and dismissal detail. "
                "Requires match_id or season. Use it for phase analysis (powerplay, death overs), "
                "matchups, and custom metrics."
            ),
            md=(
                "Cricsheet ball-by-ball data. `over_number` starts at 1; `legal_ball_number` counts legal "
                "balls in the innings and is NULL for wides and no-balls. Either `match_id` or `season` "
                "is required."
            ),
            schema=DELIVERIES_SCHEMA,
            examples=_DELIVERIES_EXAMPLES,
        )
        examples = _examples(_DELIVERIES_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About 250 balls per T20 match; far more for Tests."""
        return TableCardinality(estimate=18_000, max=2_000_000)

    @classmethod
    def process(cls, params: ProcessParams[MatchSelectionArgs], state: None, out: OutputCollector) -> None:
        """Load and emit deliveries."""
        args = params.args
        _emit(loaders.deliveries(args.competition, args.match_id, args.season), params.output_schema, out)


_STANDINGS_EXAMPLES = [
    (
        "The IPL 2026 league-stage table with net run rate.",
        "SELECT position, team, played, won, lost, no_result, points, net_run_rate "
        "FROM cricket.main.standings('ipl', season := '2026') ORDER BY position",
    ),
    (
        "The most recent PSL table.",
        "SELECT position, team, points, net_run_rate FROM cricket.main.standings('psl') ORDER BY position",
    ),
]


@init_single_worker
@bind_fixed_schema
class StandingsFunction(TableFunctionGenerator[StandingsArgs]):
    """League tables computed from Cricsheet results."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = STANDINGS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "standings"
        description = "Computed league-stage table with points and net run rate from Cricsheet results"
        categories = ["cricket", "standings"]
        projection_pushdown = True
        tags = _tags(
            category="standings",
            title="Computed League Standings",
            source=_CRICSHEET,
            llm=(
                "League-stage points table for a franchise or domestic competition season, computed from "
                "Cricsheet results: played, won, lost, tied, no result, points, and net run rate. Knockout "
                "matches are excluded; super-over winners take the win. NULL season means the latest "
                "season. For official English tables with bonus points use ecb_standings()."
            ),
            md=(
                "Computed standings. Points use the competition's `points_for_win` (2, or 4 for The "
                "Hundred) and half that for a tie, draw, or no result. Net run rate charges an all-out side "
                "its full allocation. Penalty deductions and bonus points are not modelled, so official "
                "tables can differ."
            ),
            schema=STANDINGS_SCHEMA,
            examples=_STANDINGS_EXAMPLES,
        )
        examples = _examples(_STANDINGS_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A handful of teams per league."""
        return TableCardinality(estimate=10, max=200)

    @classmethod
    def process(cls, params: ProcessParams[StandingsArgs], state: None, out: OutputCollector) -> None:
        """Compute and emit the table."""
        _emit(loaders.standings(params.args.competition, params.args.season), params.output_schema, out)


_BATTING_STATS_EXAMPLES = [
    (
        "IPL 2026 leading run scorers (the Orange Cap race).",
        "SELECT player, teams, innings, runs, average, strike_rate, hundreds "
        "FROM cricket.main.batting_stats('ipl', season := '2026') ORDER BY runs DESC LIMIT 10",
    ),
    (
        "All-time IPL six hitters.",
        "SELECT player, sixes, runs FROM cricket.main.batting_stats('ipl') ORDER BY sixes DESC LIMIT 10",
    ),
]


@init_single_worker
@bind_fixed_schema
class BattingStatsFunction(TableFunctionGenerator[CompetitionSeasonArgs]):
    """Per-player batting aggregates from Cricsheet."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = BATTING_STATS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "batting_stats"
        description = "Per-player batting totals and averages for a competition and season from Cricsheet"
        categories = ["cricket", "player-stats"]
        projection_pushdown = True
        tags = _tags(
            category="player-stats",
            title="Batting Statistics",
            source=_CRICSHEET,
            llm=(
                "One row per batter: matches, innings, not outs, runs, balls, highest score, average, strike "
                "rate, fifties, hundreds, ducks, fours, and sixes for a competition season, or for the "
                "whole archive when season is NULL (all-time records)."
            ),
            md=(
                "Batting aggregates computed from Cricsheet scorecards. `average` is NULL without a "
                "dismissal."
            ),
            schema=BATTING_STATS_SCHEMA,
            examples=_BATTING_STATS_EXAMPLES,
        )
        examples = _examples(_BATTING_STATS_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A couple of hundred players per season."""
        return TableCardinality(estimate=250, max=50_000)

    @classmethod
    def process(cls, params: ProcessParams[CompetitionSeasonArgs], state: None, out: OutputCollector) -> None:
        """Aggregate and emit batting figures."""
        _emit(loaders.batting_stats(params.args.competition, params.args.season), params.output_schema, out)


_BOWLING_STATS_EXAMPLES = [
    (
        "IPL 2026 leading wicket takers (the Purple Cap race).",
        "SELECT player, teams, overs, wickets, average, economy, best_figures "
        "FROM cricket.main.bowling_stats('ipl', season := '2026') ORDER BY wickets DESC, average LIMIT 10",
    ),
    (
        "Most economical IPL 2026 bowlers with at least 120 legal balls.",
        "SELECT player, overs, economy FROM cricket.main.bowling_stats('ipl', season := '2026') "
        "WHERE legal_balls >= 120 ORDER BY economy LIMIT 10",
    ),
]


@init_single_worker
@bind_fixed_schema
class BowlingStatsFunction(TableFunctionGenerator[CompetitionSeasonArgs]):
    """Per-player bowling aggregates from Cricsheet."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = BOWLING_STATS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "bowling_stats"
        description = "Per-player bowling totals and averages for a competition and season from Cricsheet"
        categories = ["cricket", "player-stats"]
        projection_pushdown = True
        tags = _tags(
            category="player-stats",
            title="Bowling Statistics",
            source=_CRICSHEET,
            llm=(
                "One row per bowler: innings, legal balls, overs, maidens, runs conceded, wickets, best "
                "figures, average, economy per six balls, strike rate, and four- and five-wicket hauls for "
                "a competition season, or the whole archive when season is NULL."
            ),
            md="Bowling aggregates computed from Cricsheet scorecards.",
            schema=BOWLING_STATS_SCHEMA,
            examples=_BOWLING_STATS_EXAMPLES,
        )
        examples = _examples(_BOWLING_STATS_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Around a hundred bowlers per season."""
        return TableCardinality(estimate=150, max=30_000)

    @classmethod
    def process(cls, params: ProcessParams[CompetitionSeasonArgs], state: None, out: OutputCollector) -> None:
        """Aggregate and emit bowling figures."""
        _emit(loaders.bowling_stats(params.args.competition, params.args.season), params.output_schema, out)


# --------------------------------------------------------------------------- ESPN functions

_SCHEDULE_EXAMPLES = [
    (
        "The IPL 2026 fixture list with results.",
        "SELECT start_time, description, name, summary "
        "FROM cricket.main.schedule('ipl', season_year := 2026) ORDER BY start_time",
    ),
    (
        "Upcoming Big Bash League fixtures.",
        "SELECT start_time, name, venue FROM cricket.main.schedule('bbl') WHERE state = 'pre' "
        "ORDER BY start_time",
    ),
]


@init_single_worker
@bind_fixed_schema
class ScheduleFunction(TableFunctionGenerator[ScheduleArgs]):
    """A league's fixtures and results from ESPN."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = ESPN_MATCH_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "schedule"
        description = "Fixtures, live scores, and results for a league season from ESPN"
        categories = ["cricket", "matches"]
        projection_pushdown = True
        tags = _tags(
            category="matches",
            title="League Schedule",
            source=_ESPN,
            llm=(
                "Fixture list and results for one league from ESPN, including matches not yet played and "
                "live scores. league is a competition code with an ESPN mapping (ipl, bbl, psl, ...) or a "
                "numeric ESPN league id. Pass season_year for the whole season; NULL returns ESPN's "
                "current window. event_id equals the Cricsheet match_id."
            ),
            md=(
                "Unofficial ESPN data, fetched on demand and cached for five minutes. Use `matches()` for "
                "completed Cricsheet history."
            ),
            schema=ESPN_MATCH_SCHEMA,
            examples=_SCHEDULE_EXAMPLES,
        )
        examples = _examples(_SCHEDULE_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A franchise season is under a hundred matches."""
        return TableCardinality(estimate=60, max=1_000)

    @classmethod
    def process(cls, params: ProcessParams[ScheduleArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the schedule."""
        _emit(loaders.schedule(params.args.league, params.args.season_year), params.output_schema, out)


_LIVE_BATTING_EXAMPLES = [
    (
        "ESPN batting card for the IPL 2026 final.",
        f"SELECT innings_number, batting_position, player, runs, balls_faced, dismissal "
        f"FROM cricket.main.live_batting_scorecard({_FINAL}) ORDER BY innings_number, batting_position",
    )
]


@init_single_worker
@bind_fixed_schema
class LiveBattingFunction(TableFunctionGenerator[EventArgs]):
    """ESPN batting card for one match, including matches in progress."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = LIVE_BATTING_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "live_batting_scorecard"
        description = "Batting card for one match from ESPN, live while the match is in progress"
        categories = ["cricket", "live"]
        projection_pushdown = True
        tags = _tags(
            category="live",
            title="Live Batting Scorecard",
            source=_ESPN,
            llm=(
                "ESPN batting card for one event_id (find ids in current_matches or schedule): runs, balls, "
                "minutes, boundaries, strike rate, dismissal text, and whether the batter is at the crease. "
                "Works during play, unlike the Cricsheet scorecards."
            ),
            md="Unofficial ESPN data, cached for 30 seconds. `player_id` joins to `people.key_cricinfo`.",
            schema=LIVE_BATTING_SCHEMA,
            examples=_LIVE_BATTING_EXAMPLES,
        )
        examples = _examples(_LIVE_BATTING_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Up to 44 batter innings in a Test."""
        return TableCardinality(estimate=18, max=60)

    @classmethod
    def process(cls, params: ProcessParams[EventArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the batting card."""
        _emit(loaders.live_batting(params.args.event_id), params.output_schema, out)


_LIVE_BOWLING_EXAMPLES = [
    (
        "ESPN bowling card for the IPL 2026 final.",
        f"SELECT innings_number, player, overs, runs_conceded, wickets, economy "
        f"FROM cricket.main.live_bowling_scorecard({_FINAL}) ORDER BY innings_number, wickets DESC",
    )
]


@init_single_worker
@bind_fixed_schema
class LiveBowlingFunction(TableFunctionGenerator[EventArgs]):
    """ESPN bowling card for one match, including matches in progress."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = LIVE_BOWLING_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "live_bowling_scorecard"
        description = "Bowling card for one match from ESPN, live while the match is in progress"
        categories = ["cricket", "live"]
        projection_pushdown = True
        tags = _tags(
            category="live",
            title="Live Bowling Scorecard",
            source=_ESPN,
            llm=(
                "ESPN bowling card for one event_id: overs, maidens, runs conceded, wickets, economy, dots, "
                "boundaries conceded, wides, and no-balls, updated during play."
            ),
            md="Unofficial ESPN data, cached for 30 seconds.",
            schema=LIVE_BOWLING_SCHEMA,
            examples=_LIVE_BOWLING_EXAMPLES,
        )
        examples = _examples(_LIVE_BOWLING_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A dozen or so bowler innings per match."""
        return TableCardinality(estimate=12, max=60)

    @classmethod
    def process(cls, params: ProcessParams[EventArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit the bowling card."""
        _emit(loaders.live_bowling(params.args.event_id), params.output_schema, out)


_COMMENTARY_EXAMPLES = [
    (
        "Wickets in the IPL 2026 final from ESPN commentary.",
        f"SELECT innings_number, overs, short_text FROM cricket.main.live_commentary({_FINAL}) "
        "WHERE is_wicket ORDER BY sequence",
    ),
    (
        "The first 25 balls of the IPL 2026 final.",
        f"SELECT overs, short_text FROM cricket.main.live_commentary({_FINAL}, page := 1) ORDER BY sequence",
    ),
]


@init_single_worker
@bind_fixed_schema
class LiveCommentaryFunction(TableFunctionGenerator[CommentaryArgs]):
    """ESPN ball-by-ball commentary for one match."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = COMMENTARY_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "live_commentary"
        description = "Ball-by-ball text commentary for one match from ESPN"
        categories = ["cricket", "live"]
        projection_pushdown = True
        tags = _tags(
            category="live",
            title="Live Commentary",
            source=_ESPN,
            llm=(
                "ESPN ball-by-ball commentary for one event_id: over.ball, bowler, batter, play type, runs, "
                "wicket flag, running innings score, and commentary text. Pages hold 25 balls; NULL page "
                "reads them all (T20 about 10 pages). Use for live matches or the narrative of a game."
            ),
            md="Unofficial ESPN data, cached for 30 seconds. Text has markup removed.",
            schema=COMMENTARY_SCHEMA,
            examples=_COMMENTARY_EXAMPLES,
        )
        examples = _examples(_COMMENTARY_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About 250 balls for a T20."""
        return TableCardinality(estimate=250, max=3_000)

    @classmethod
    def process(cls, params: ProcessParams[CommentaryArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit commentary."""
        _emit(loaders.live_commentary(params.args.event_id, params.args.page), params.output_schema, out)


# --------------------------------------------------------------------------- ICC functions

_TEAM_RANKING_EXAMPLES = [
    (
        "The ICC men's Test team rankings.",
        "SELECT rank, team, matches, rating FROM cricket.main.team_rankings('test') ORDER BY rank",
    ),
]


@init_single_worker
@bind_fixed_schema
class TeamRankingsFunction(TableFunctionGenerator[TeamRankingArgs]):
    """ICC team rankings for one format."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = TEAM_RANKINGS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "team_rankings"
        description = "Official ICC team rankings for one format"
        categories = ["cricket", "rankings"]
        projection_pushdown = True
        tags = _tags(
            category="rankings",
            title="ICC Team Rankings",
            source=_ICC,
            llm=(
                "ICC team rankings for one format: test, odi, t20 (men's) or odiw, t20w (women's), with "
                "rank, matches, points, rating, and movement. current_team_rankings holds every format."
            ),
            md="ICC rankings, cached for six hours.",
            schema=TEAM_RANKINGS_SCHEMA,
            examples=_TEAM_RANKING_EXAMPLES,
        )
        examples = _examples(_TEAM_RANKING_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """10 Test teams up to about 100 T20I teams."""
        return TableCardinality(estimate=30, max=120)

    @classmethod
    def process(cls, params: ProcessParams[TeamRankingArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit team rankings."""
        _emit(loaders.team_rankings(params.args.format), params.output_schema, out)


_PLAYER_RANKING_EXAMPLES = [
    (
        "Top ten ODI batters.",
        "SELECT rank, player, country, rating FROM cricket.main.player_rankings('odi', 'bat') "
        "ORDER BY rank LIMIT 10",
    ),
]


@init_single_worker
@bind_fixed_schema
class PlayerRankingsFunction(TableFunctionGenerator[PlayerRankingArgs]):
    """ICC player rankings for one format and discipline."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = PLAYER_RANKINGS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "player_rankings"
        description = "Official ICC player rankings for one format and discipline"
        categories = ["cricket", "rankings"]
        projection_pushdown = True
        tags = _tags(
            category="rankings",
            title="ICC Player Rankings",
            source=_ICC,
            llm=(
                "ICC player rankings (top 100) for one format (test, odi, t20, odiw, t20w) and discipline "
                "(bat, bowl, allrounder): rank, player, country, rating, and career best."
            ),
            md="ICC rankings, cached for six hours. Tied players share a rank.",
            schema=PLAYER_RANKINGS_SCHEMA,
            examples=_PLAYER_RANKING_EXAMPLES,
        )
        examples = _examples(_PLAYER_RANKING_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """The feed publishes the top 100 (plus ties)."""
        return TableCardinality(estimate=100, max=120)

    @classmethod
    def process(cls, params: ProcessParams[PlayerRankingArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit player rankings."""
        _emit(loaders.player_rankings(params.args.format, params.args.discipline), params.output_schema, out)


# --------------------------------------------------------------------------- ECB functions

_ECB_FIXTURES_EXAMPLES = [
    (
        "The Hundred Men 2026 fixtures and results.",
        "SELECT start_time, label, team1_score, team2_score, result FROM cricket.main.ecb_fixtures(76753) "
        "ORDER BY start_time",
    ),
]


@init_single_worker
@bind_fixed_schema
class EcbFixturesFunction(TableFunctionGenerator[TournamentArgs]):
    """Fixtures and results for one ECB tournament."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = ECB_FIXTURES_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "ecb_fixtures"
        description = "Fixtures and results for one ECB tournament from the official ECB feed"
        categories = ["cricket", "ecb"]
        projection_pushdown = True
        tags = _tags(
            category="ecb",
            title="ECB Fixtures",
            source=_ECB,
            llm=(
                "Fixtures, scores, and results for one ECB tournament_id (from ecb_tournaments): The "
                "Hundred, County Championship divisions, Vitality Blast, One-Day Cup, women's county "
                "competitions, and England internationals."
            ),
            md="Official ECB data, cached for ten minutes.",
            schema=ECB_FIXTURES_SCHEMA,
            examples=_ECB_FIXTURES_EXAMPLES,
        )
        examples = _examples(_ECB_FIXTURES_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Tournaments hold a few dozen to a few hundred fixtures."""
        return TableCardinality(estimate=60, max=2_000)

    @classmethod
    def process(cls, params: ProcessParams[TournamentArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit fixtures."""
        _emit(loaders.ecb_fixtures(params.args.tournament_id), params.output_schema, out)


_ECB_STANDINGS_EXAMPLES = [
    (
        "The official 2026 County Championship Division One table.",
        "SELECT position, team, played, won, lost, drawn, batting_bonus, bowling_bonus, points "
        "FROM cricket.main.ecb_standings(76738) ORDER BY position",
    ),
    (
        "The Hundred Men 2026 table with net run rate.",
        "SELECT position, team, won, lost, points, net_run_rate FROM cricket.main.ecb_standings(76753) "
        "ORDER BY position",
    ),
]


@init_single_worker
@bind_fixed_schema
class EcbStandingsFunction(TableFunctionGenerator[TournamentArgs]):
    """Official ECB standings for one tournament."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = ECB_STANDINGS_SCHEMA

    class Meta:
        """Function discovery metadata."""

        name = "ecb_standings"
        description = "Official ECB standings with bonus points and net run rate for one tournament"
        categories = ["cricket", "ecb", "standings"]
        projection_pushdown = True
        tags = _tags(
            category="ecb",
            title="ECB Standings",
            source=_ECB,
            llm=(
                "Official ECB table for one tournament_id: position, played, won, lost, tied, drawn, "
                "batting and bowling bonus points, deductions, points, net run rate, and recent form. "
                "Prefer this over the computed standings() for English competitions."
            ),
            md="Official ECB data, cached for ten minutes. Club contact details are never returned.",
            schema=ECB_STANDINGS_SCHEMA,
            examples=_ECB_STANDINGS_EXAMPLES,
        )
        examples = _examples(_ECB_STANDINGS_EXAMPLES)

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Eight to eighteen teams."""
        return TableCardinality(estimate=10, max=100)

    @classmethod
    def process(cls, params: ProcessParams[TournamentArgs], state: None, out: OutputCollector) -> None:
        """Fetch and emit standings."""
        _emit(loaders.ecb_standings(params.args.tournament_id), params.output_schema, out)


# --------------------------------------------------------------------------- table backing scans


def _backing_tags(*, table: str, category: str, title: str, schema: pa.Schema, source: str, sql: str) -> dict:
    return _tags(
        category=category,
        title=title,
        source=source,
        llm=f"The scan behind the {table} table. Prefer selecting from {table} directly.",
        md=f"Returns the rows of `{table}`; this function exists as that table's scan.",
        schema=schema,
        examples=[(f"Read the {table} backing scan directly.", sql)],
    )


def _scan_meta(name: str, description: str, tags: dict, sql: str, text: str):
    return type(
        "Meta",
        (),
        {
            "__doc__": "Function discovery metadata.",
            "name": name,
            "description": description,
            "categories": ["cricket", tags["vgi.category"]],
            "projection_pushdown": True,
            "tags": tags,
            "examples": [FunctionExample(sql=sql, description=text)],
        },
    )


_COMPETITIONS_SQL = (
    "SELECT competition, name, espn_league_id FROM cricket.main.all_competitions() ORDER BY competition"
)


@init_single_worker
@bind_fixed_schema
class CompetitionsScan(TableFunctionGenerator[None]):
    """Scan behind ``competitions``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = COMPETITIONS_SCHEMA
    Meta = _scan_meta(
        "all_competitions",
        "Supported competition codes with formats and ESPN league ids (scan behind competitions)",
        _backing_tags(
            table="competitions",
            category="reference",
            title="Competition Catalogue",
            schema=COMPETITIONS_SCHEMA,
            source="vgi-cricket",
            sql=_COMPETITIONS_SQL,
        ),
        _COMPETITIONS_SQL,
        "List supported competition codes",
    )

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """The catalogue is static."""
        return TableCardinality(estimate=32, max=64)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Emit the static catalogue."""
        _emit(loaders.competitions(), params.output_schema, out)


_CURRENT_MATCHES_SQL = (
    "SELECT league_name, name, status, summary FROM cricket.main.all_current_matches() WHERE state = 'in'"
)


@init_single_worker
@bind_fixed_schema
class CurrentMatchesScan(TableFunctionGenerator[None]):
    """Scan behind ``current_matches``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = ESPN_MATCH_SCHEMA
    Meta = _scan_meta(
        "all_current_matches",
        "Live, recent, and upcoming matches from ESPN (scan behind current_matches)",
        _backing_tags(
            table="current_matches",
            category="live",
            title="Current Matches",
            schema=ESPN_MATCH_SCHEMA,
            source=_ESPN,
            sql=_CURRENT_MATCHES_SQL,
        ),
        _CURRENT_MATCHES_SQL,
        "Matches in progress right now",
    )

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """The scorepanel lists a few dozen matches."""
        return TableCardinality(estimate=30, max=500)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit the scorepanel."""
        _emit(loaders.current_matches(), params.output_schema, out)


_RECENT_SQL = (
    "SELECT start_date, event_name, team1, team2, winner FROM cricket.main.all_current_recent_matches() "
    "ORDER BY start_date DESC"
)


@init_single_worker
@bind_fixed_schema
class CurrentRecentMatchesScan(TableFunctionGenerator[None]):
    """Scan behind ``current_recent_matches``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = MATCH_SCHEMA
    Meta = _scan_meta(
        "all_current_recent_matches",
        "Matches played in the last seven days from Cricsheet (scan behind current_recent_matches)",
        _backing_tags(
            table="current_recent_matches",
            category="matches",
            title="Recent Matches",
            schema=MATCH_SCHEMA,
            source=_CRICSHEET,
            sql=_RECENT_SQL,
        ),
        _RECENT_SQL,
        "Matches from the last seven days",
    )

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """A week of world cricket is a few dozen matches."""
        return TableCardinality(estimate=40, max=1_000)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Load and emit recently played matches."""
        _emit(loaders.recent_matches(), params.output_schema, out)


_TEAM_RANKINGS_SQL = (
    "SELECT format, rank, team, rating FROM cricket.main.all_current_team_rankings() WHERE rank <= 3 "
    "ORDER BY format, rank"
)


@init_single_worker
@bind_fixed_schema
class CurrentTeamRankingsScan(TableFunctionGenerator[None]):
    """Scan behind ``current_team_rankings``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = TEAM_RANKINGS_SCHEMA
    Meta = _scan_meta(
        "all_current_team_rankings",
        "ICC team rankings for every format (scan behind current_team_rankings)",
        _backing_tags(
            table="current_team_rankings",
            category="rankings",
            title="Current ICC Team Rankings",
            schema=TEAM_RANKINGS_SCHEMA,
            source=_ICC,
            sql=_TEAM_RANKINGS_SQL,
        ),
        _TEAM_RANKINGS_SQL,
        "Top three teams in every format",
    )

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About 230 team-format rows."""
        return TableCardinality(estimate=230, max=500)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit every format's team rankings."""
        _emit(loaders.all_team_rankings(), params.output_schema, out)


_PLAYER_RANKINGS_SQL = (
    "SELECT format, discipline, player, country, rating FROM cricket.main.all_current_player_rankings() "
    "WHERE rank = 1 ORDER BY format, discipline"
)


@init_single_worker
@bind_fixed_schema
class CurrentPlayerRankingsScan(TableFunctionGenerator[None]):
    """Scan behind ``current_player_rankings``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = PLAYER_RANKINGS_SCHEMA
    Meta = _scan_meta(
        "all_current_player_rankings",
        "ICC player rankings for every format and discipline (scan behind current_player_rankings)",
        _backing_tags(
            table="current_player_rankings",
            category="rankings",
            title="Current ICC Player Rankings",
            schema=PLAYER_RANKINGS_SCHEMA,
            source=_ICC,
            sql=_PLAYER_RANKINGS_SQL,
        ),
        _PLAYER_RANKINGS_SQL,
        "Number-one ranked player in every format and discipline",
    )

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """Fifteen tables of about 100 players."""
        return TableCardinality(estimate=1_510, max=1_800)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit every player-ranking table."""
        _emit(loaders.all_player_rankings(), params.output_schema, out)


_PEOPLE_SQL = "SELECT person_id, name, key_cricinfo FROM cricket.main.all_people() WHERE name = 'V Kohli'"


@init_single_worker
@bind_fixed_schema
class PeopleScan(TableFunctionGenerator[None]):
    """Scan behind ``people``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = PEOPLE_SCHEMA
    Meta = _scan_meta(
        "all_people",
        "Cricsheet people register with cross-source ids (scan behind people)",
        _backing_tags(
            table="people",
            category="reference",
            title="People Register",
            schema=PEOPLE_SCHEMA,
            source=_CRICSHEET,
            sql=_PEOPLE_SQL,
        ),
        _PEOPLE_SQL,
        "Look up one player's identifiers",
    )

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About 17,000 registered people."""
        return TableCardinality(estimate=17_500, max=60_000)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Load and emit the people register."""
        _emit(loaders.people(), params.output_schema, out)


_VENUES_SQL = "SELECT venue_id, unique_name FROM cricket.main.all_venues() WHERE unique_name LIKE 'Lord%'"


@init_single_worker
@bind_fixed_schema
class VenuesScan(TableFunctionGenerator[None]):
    """Scan behind ``venues``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = VENUES_SCHEMA
    Meta = _scan_meta(
        "all_venues",
        "Cricsheet venue register (scan behind venues)",
        _backing_tags(
            table="venues",
            category="reference",
            title="Venue Register",
            schema=VENUES_SCHEMA,
            source=_CRICSHEET,
            sql=_VENUES_SQL,
        ),
        _VENUES_SQL,
        "Find a ground's identifiers",
    )

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About a thousand registered venue names."""
        return TableCardinality(estimate=1_500, max=10_000)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Load and emit the venue register."""
        _emit(loaders.venues(), params.output_schema, out)


_ECB_TOURNAMENTS_SQL = (
    "SELECT tournament_id, name, match_types FROM cricket.main.all_ecb_tournaments() "
    "WHERE name LIKE 'The Hundred%' ORDER BY tournament_id DESC"
)


@init_single_worker
@bind_fixed_schema
class EcbTournamentsScan(TableFunctionGenerator[None]):
    """Scan behind ``ecb_tournaments``."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = ECB_TOURNAMENTS_SCHEMA
    Meta = _scan_meta(
        "all_ecb_tournaments",
        "Every ECB tournament (scan behind ecb_tournaments)",
        _backing_tags(
            table="ecb_tournaments",
            category="ecb",
            title="ECB Tournaments",
            schema=ECB_TOURNAMENTS_SCHEMA,
            source=_ECB,
            sql=_ECB_TOURNAMENTS_SQL,
        ),
        _ECB_TOURNAMENTS_SQL,
        "Find The Hundred tournament ids",
    )

    @classmethod
    def cardinality(cls, params: object) -> TableCardinality:
        """About 560 tournaments."""
        return TableCardinality(estimate=561, max=2_000)

    @classmethod
    def process(cls, params: ProcessParams[None], state: None, out: OutputCollector) -> None:
        """Fetch and emit ECB tournaments."""
        _emit(loaders.ecb_tournaments(), params.output_schema, out)


FUNCTIONS = [
    MatchesFunction,
    InningsSummaryFunction,
    BattingScorecardFunction,
    BowlingScorecardFunction,
    DeliveriesFunction,
    StandingsFunction,
    BattingStatsFunction,
    BowlingStatsFunction,
    ScheduleFunction,
    LiveBattingFunction,
    LiveBowlingFunction,
    LiveCommentaryFunction,
    TeamRankingsFunction,
    PlayerRankingsFunction,
    EcbFixturesFunction,
    EcbStandingsFunction,
    CompetitionsScan,
    CurrentMatchesScan,
    CurrentRecentMatchesScan,
    CurrentTeamRankingsScan,
    CurrentPlayerRankingsScan,
    PeopleScan,
    VenuesScan,
    EcbTournamentsScan,
]
