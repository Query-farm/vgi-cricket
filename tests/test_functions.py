"""In-process tests of every table function against the fixture server."""

from __future__ import annotations

from types import SimpleNamespace

import pyarrow as pa
import pytest

from vgi_cricket import functions as f
from vgi_cricket import loaders

FINAL = 1535465


class Collector:
    """Minimal stand-in for the VGI output collector."""

    def __init__(self) -> None:
        self.batches: list[pa.RecordBatch] = []
        self.cache_controls: list[object] = []
        self.finished = False

    def emit(self, batch: pa.RecordBatch, *, cache_control: object = None) -> None:
        self.batches.append(batch)
        self.cache_controls.append(cache_control)

    def finish(self) -> None:
        self.finished = True


def run(function: type, args: object = None, schema: pa.Schema | None = None) -> pa.Table:
    out = Collector()
    params = SimpleNamespace(args=args, output_schema=schema or function.FIXED_SCHEMA)
    function.process(params, None, out)
    assert out.finished
    assert out.cache_controls[0].ttl > 0
    return pa.Table.from_batches(out.batches)


def cricsheet_args(**kwargs):
    return f.MatchSelectionArgs(
        competition="ipl", match_id=kwargs.get("match_id"), season=kwargs.get("season")
    )


@pytest.mark.usefixtures("upstreams")
def test_cricsheet_functions() -> None:
    matches = run(f.MatchesFunction, f.CompetitionSeasonArgs(competition="ipl", season="2026"))
    assert matches.num_rows == 4
    assert matches.column("match_id").to_pylist()[0] == FINAL

    innings = run(f.InningsSummaryFunction, cricsheet_args(match_id=FINAL))
    assert innings.column("runs").to_pylist() == [155, 161]

    batting = run(f.BattingScorecardFunction, cricsheet_args(match_id=FINAL))
    assert batting.num_rows == 17
    bowling = run(f.BowlingScorecardFunction, cricsheet_args(season="2026"))
    assert bowling.num_rows > 20

    deliveries = run(f.DeliveriesFunction, cricsheet_args(match_id=FINAL))
    assert deliveries.num_rows == 233

    standings = run(f.StandingsFunction, f.StandingsArgs(competition="ipl", season=None))
    assert set(standings.column("season").to_pylist()) == {"2026"}

    stats = run(f.BattingStatsFunction, f.CompetitionSeasonArgs(competition="ipl", season=None))
    assert stats.column("season").null_count == stats.num_rows
    wickets = run(f.BowlingStatsFunction, f.CompetitionSeasonArgs(competition="ipl", season="2025"))
    assert wickets.num_rows > 0


@pytest.mark.usefixtures("upstreams")
def test_unknown_match_returns_no_rows() -> None:
    assert run(f.BattingScorecardFunction, cricsheet_args(match_id=1)).num_rows == 0


@pytest.mark.usefixtures("upstreams")
def test_projection_is_honoured() -> None:
    schema = pa.schema([f.MATCH_SCHEMA.field("winner"), f.MATCH_SCHEMA.field("match_id")])
    table = run(f.MatchesFunction, f.CompetitionSeasonArgs(competition="ipl", season="2026"), schema)
    assert table.schema.names == ["winner", "match_id"]


@pytest.mark.usefixtures("upstreams")
def test_espn_functions() -> None:
    schedule = run(f.ScheduleFunction, f.ScheduleArgs(league="ipl", season_year=2026))
    assert FINAL in schedule.column("event_id").to_pylist()
    assert run(f.ScheduleFunction, f.ScheduleArgs(league="8048", season_year=None)).num_rows == 3
    batting = run(f.LiveBattingFunction, f.EventArgs(event_id=FINAL))
    assert batting.num_rows == 17
    bowling = run(f.LiveBowlingFunction, f.EventArgs(event_id=FINAL))
    assert sum(bowling.column("wickets").to_pylist()) == 13
    every = run(f.LiveCommentaryFunction, f.CommentaryArgs(event_id=FINAL, page=None))
    assert every.num_rows == 6
    assert every.column("sequence").to_pylist() == sorted(every.column("sequence").to_pylist())
    one = run(f.LiveCommentaryFunction, f.CommentaryArgs(event_id=FINAL, page=1))
    assert one.num_rows == 3


@pytest.mark.usefixtures("upstreams")
def test_icc_and_ecb_functions() -> None:
    teams = run(f.TeamRankingsFunction, f.TeamRankingArgs(format="test"))
    assert teams.column("team").to_pylist()[0] == "South Africa"
    players = run(f.PlayerRankingsFunction, f.PlayerRankingArgs(format="test", discipline="allrounder"))
    assert players.num_rows == 5
    fixtures = run(f.EcbFixturesFunction, f.TournamentArgs(tournament_id=76753))
    assert 113374 in fixtures.column("fixture_id").to_pylist()
    table = run(f.EcbStandingsFunction, f.TournamentArgs(tournament_id=76753))
    assert table.column("team").to_pylist()[0] == "Trent Rockets Men"


@pytest.mark.usefixtures("upstreams")
@pytest.mark.parametrize(
    ("function", "minimum"),
    [
        (f.CompetitionsScan, 30),
        (f.CurrentMatchesScan, 1),
        (f.CurrentRecentMatchesScan, 2),
        (f.CurrentTeamRankingsScan, 50),
        (f.CurrentPlayerRankingsScan, 75),
        (f.PeopleScan, 20),
        (f.VenuesScan, 1),
        (f.EcbTournamentsScan, 4),
    ],
)
def test_table_scans(function: type, minimum: int) -> None:
    assert run(function).num_rows >= minimum


@pytest.mark.usefixtures("upstreams")
def test_loader_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="needs match_id or season"):
        loaders.deliveries("ipl")
    with pytest.raises(ValueError, match="no ESPN league id"):
        loaders.resolve_league("sat")
    with pytest.raises(ValueError, match="bat, bowl, or allrounder"):
        loaders.player_rankings("odi", "team")
    assert loaders.resolve_league("bbl") == 8044
    assert loaders.resolve_league(" 21282 ") == 21282
    monkeypatch.setattr(loaders, "MAX_COMMENTARY_PAGES", 1)
    with pytest.raises(ValueError, match="commentary pages"):
        loaders.live_commentary(FINAL)


def test_latest_season() -> None:
    assert loaders.latest_season([]) is None


def test_every_function_has_complete_metadata() -> None:
    for function in f.FUNCTIONS:
        tags = function.Meta.tags
        for key in (
            "vgi.title",
            "vgi.doc_llm",
            "vgi.doc_md",
            "vgi.example_queries",
            "vgi.result_columns_schema",
        ):
            assert tags.get(key), (function.Meta.name, key)
        assert function.Meta.examples, function.Meta.name
