"""Tests for the SQL-facing catalog shape and discovery metadata."""

from __future__ import annotations

import json

from vgi_cricket import functions as f
from vgi_cricket.competitions import BY_CODE, COMPETITIONS
from vgi_cricket.worker import CRICKET_CATALOG


def _key_of(table, kind: str) -> list[tuple[str, ...]]:
    return [
        tuple(name for path in record.definition.column_paths for name in path)
        for record in table.constraints
        if record.definition.kind == kind
    ]


def test_catalog_surface() -> None:
    schema = CRICKET_CATALOG.schemas[0]
    assert CRICKET_CATALOG.name == "cricket"
    assert schema.path == ["main"]
    assert {function.Meta.name for function in schema.functions} == {
        "matches",
        "innings_summary",
        "batting_scorecard",
        "bowling_scorecard",
        "deliveries",
        "standings",
        "batting_stats",
        "bowling_stats",
        "schedule",
        "live_batting_scorecard",
        "live_bowling_scorecard",
        "live_commentary",
        "team_rankings",
        "player_rankings",
        "ecb_fixtures",
        "ecb_standings",
        "all_competitions",
        "all_current_matches",
        "all_current_recent_matches",
        "all_current_team_rankings",
        "all_current_player_rankings",
        "all_people",
        "all_venues",
        "all_ecb_tournaments",
    }
    assert {table.name: table.function for table in schema.tables} == {
        "competitions": f.CompetitionsScan,
        "current_matches": f.CurrentMatchesScan,
        "current_recent_matches": f.CurrentRecentMatchesScan,
        "current_team_rankings": f.CurrentTeamRankingsScan,
        "current_player_rankings": f.CurrentPlayerRankingsScan,
        "people": f.PeopleScan,
        "venues": f.VenuesScan,
        "ecb_tournaments": f.EcbTournamentsScan,
    }


def test_table_keys_are_declared_and_exist_in_schemas() -> None:
    expected = {
        "competitions": ("competition",),
        "current_matches": ("event_id",),
        "current_recent_matches": ("match_id",),
        "current_team_rankings": ("format", "team"),
        "current_player_rankings": ("format", "discipline", "player_id"),
        "people": ("person_id",),
        "venues": ("venue_id",),
        "ecb_tournaments": ("tournament_id",),
    }
    for table in CRICKET_CATALOG.schemas[0].tables:
        key = expected[table.name]
        assert _key_of(table, "PRIMARY_KEY") == [key]
        assert _key_of(table, "NOT_NULL") == [(column,) for column in key]
        names = set(table.function.FIXED_SCHEMA.names)
        assert set(key) <= names
        assert set(table.column_comments) == names


def test_every_table_has_agent_metadata() -> None:
    for table in CRICKET_CATALOG.schemas[0].tables:
        for key in ("vgi.title", "vgi.doc_llm", "vgi.doc_md", "vgi.keywords", "vgi.example_queries"):
            assert table.tags.get(key), (table.name, key)
        for example in json.loads(table.tags["vgi.example_queries"]):
            assert f"cricket.main.{table.name}" in example["sql"]


def test_catalog_discovery_contract_is_public_and_machine_readable() -> None:
    tags = CRICKET_CATALOG.tags
    examples = json.loads(tags["vgi.executable_examples"])
    tasks = json.loads(tags["vgi.agent_test_tasks"])
    assert examples
    assert all({"name", "description", "sql", "expected_result"} <= example.keys() for example in examples)
    assert tasks
    assert all(set(task) == {"name", "prompt"} for task in tasks)
    assert len({task["name"] for task in tasks}) == len(tasks)
    assert "ODC-By" in tags["vgi.copyright"]
    assert "Not affiliated" in tags["attribution"]
    assert tags["vgi.subtitle"]


def test_competition_catalogue_is_consistent() -> None:
    assert len(BY_CODE) == len(COMPETITIONS)
    assert all(not code.startswith("all") for code in BY_CODE)
    assert BY_CODE["hnd_male"].points_for_win == 4
    assert BY_CODE["ipl"].espn_league_id == 8048
    assert BY_CODE["ipl"].cricsheet_url == "https://cricsheet.org/downloads/ipl_json.zip"


def test_positional_and_named_arguments() -> None:
    parameters = {p.name: p for p in f.MatchesFunction.get_metadata().parameters}
    assert set(parameters) == {"competition", "season"}
    assert parameters["competition"].required
    assert not parameters["season"].required
    parameters = {p.name: p for p in f.DeliveriesFunction.get_metadata().parameters}
    assert set(parameters) == {"competition", "match_id", "season"}


def test_composed_examples_reach_schema_and_driving_function() -> None:
    schema = CRICKET_CATALOG.schemas[0]
    schema_sql = [example["sql"] for example in json.loads(schema.tags["vgi.example_queries"])]
    drivers = {
        f.KNOCKOUT_TOP_SCORERS: f.MatchesFunction,
        f.GROUND_FIRST_INNINGS_AVERAGES: f.InningsSummaryFunction,
        f.POWERPLAY_VS_STANDINGS: f.DeliveriesFunction,
    }
    assert set(drivers) == set(f.COMPOSED_EXAMPLES)
    for (_, sql), function in drivers.items():
        assert sql in schema_sql
        # The vgi-lint agent simulator only shows the first five examples of each object.
        assert sql in [example.sql for example in function.Meta.examples[:5]]
        assert sql in [example["sql"] for example in json.loads(function.Meta.tags["vgi.example_queries"])]
