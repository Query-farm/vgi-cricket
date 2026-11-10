"""Tests for Cricsheet parsing and derived scorecards, standings, and aggregates."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import FIXTURES, cricsheet_match
from tests.fixture_server import archive_bytes
from vgi_cricket import cricsheet as cs

FINAL = 1535465
SUPER_OVER_TIE = 1529281
NO_RESULT = 1527685
RAIN_AFFECTED = 1529293


def test_iter_archive_reads_every_match_and_skips_readme() -> None:
    body = archive_bytes("ipl")
    assert body is not None
    ids = [match.match_id for match in cs.iter_archive(body)]
    assert ids == sorted([1473469, NO_RESULT, SUPER_OVER_TIE, RAIN_AFFECTED, FINAL])


def test_match_row_for_the_ipl_2026_final() -> None:
    row = cs.match_row(cricsheet_match(FINAL), "ipl")
    assert row["match_id"] == FINAL
    assert row["season"] == "2026"
    assert row["stage"] == "Final"
    assert row["start_date"] == date(2026, 5, 31)
    assert row["team1"] == "Gujarat Titans"
    assert row["team1_score"] == "155/8 (20.0 ov)"
    assert row["team2_score"] == "161/5 (18.0 ov)"
    assert row["result"] == "win"
    assert row["winner"] == "Royal Challengers Bengaluru"
    assert row["win_by_wickets"] == 5
    assert row["win_by_runs"] is None
    assert row["player_of_match"] == "V Kohli"
    assert row["toss_decision"] == "field"
    assert row["venue_id"] == "dea6641b"


def test_super_over_tie_records_the_eliminator() -> None:
    row = cs.match_row(cricsheet_match(SUPER_OVER_TIE), "ipl")
    assert row["result"] == "tie"
    assert row["winner"] == "Kolkata Knight Riders"
    assert row["decided_by"] == "super over"
    innings = cs.innings_rows(cricsheet_match(SUPER_OVER_TIE), "ipl")
    assert any(value["super_over"] for value in innings)
    regular = [value for value in innings if not value["super_over"]]
    assert regular[0]["runs"] == regular[1]["runs"]


def test_no_result_and_rain_rule_matches() -> None:
    assert cs.match_row(cricsheet_match(NO_RESULT), "ipl")["result"] == "no result"
    rain = cs.match_row(cricsheet_match(RAIN_AFFECTED), "ipl")
    assert rain["method"] == "D/L"
    assert rain["win_by_runs"] == 9


def test_innings_totals_reconcile_with_scorecards() -> None:
    match = cricsheet_match(FINAL)
    innings = cs.innings_rows(match, "ipl")
    assert [(value["runs"], value["wickets"], value["overs"]) for value in innings] == [
        (155, 8, "20.0"),
        (161, 5, "18.0"),
    ]
    assert innings[1]["target_runs"] == 156
    assert innings[0]["run_rate"] == Decimal("7.75")
    batting = cs.batting_rows(match, "ipl")
    bowling = cs.bowling_rows(match, "ipl")
    for value in innings:
        number = value["innings_number"]
        bat_runs = sum(row["runs"] for row in batting if row["innings_number"] == number)
        assert bat_runs + value["extras"] == value["runs"]
        charged = sum(row["runs_conceded"] for row in bowling if row["innings_number"] == number)
        assert charged == value["runs"] - value["byes"] - value["leg_byes"] - value["penalties"]
        balls = sum(row["legal_balls"] for row in bowling if row["innings_number"] == number)
        assert balls == value["legal_balls"]
        assert sum(row["is_out"] for row in batting if row["innings_number"] == number) == value["wickets"]


def test_batting_card_details() -> None:
    batting = cs.batting_rows(cricsheet_match(FINAL), "ipl")
    kohli = next(row for row in batting if row["batter"] == "V Kohli")
    assert (kohli["runs"], kohli["balls"], kohli["fours"], kohli["sixes"]) == (75, 42, 9, 3)
    assert kohli["is_out"] is False
    assert kohli["batter_id"] == "ba607b88"
    assert kohli["strike_rate"] == Decimal("178.57")
    opener = next(row for row in batting if row["batting_position"] == 1 and row["innings_number"] == 1)
    assert opener["dismissal_kind"] == "caught"
    assert opener["dismissed_by"] == "B Kumar"
    assert opener["fielders"] == "JM Sharma"


def test_bowling_card_details() -> None:
    bowling = cs.bowling_rows(cricsheet_match(FINAL), "ipl")
    salam = next(row for row in bowling if row["bowler"] == "Rasikh Salam")
    assert (salam["overs"], salam["runs_conceded"], salam["wickets"]) == ("4.0", 27, 3)
    assert salam["economy"] == Decimal("6.75")
    assert all(row["maidens"] == 0 for row in bowling)


def test_deliveries_cover_every_ball() -> None:
    rows = cs.delivery_rows(cricsheet_match(FINAL), "ipl")
    assert len(rows) == 233
    assert sum(row["runs_total"] for row in rows) == 316
    assert sum(row["is_wicket"] for row in rows) == 13
    first = rows[0]
    assert (first["over_number"], first["ball_in_over"], first["legal_ball_number"]) == (1, 1, 1)
    wide = next(row for row in rows if row["wides"])
    assert wide["is_legal"] is False
    assert wide["legal_ball_number"] is None
    assert max(row["legal_ball_number"] or 0 for row in rows if row["innings_number"] == 1) == 120


def test_standings_from_fixture_matches() -> None:
    matches = [cricsheet_match(value) for value in (SUPER_OVER_TIE, NO_RESULT, RAIN_AFFECTED, FINAL)]
    rows = cs.standings_rows(matches, "ipl", "2026")
    by_team = {row["team"]: row for row in rows}
    # The final is a knockout and is excluded; the super-over winner takes the win.
    assert sum(row["played"] for row in rows) == 6
    assert by_team["Kolkata Knight Riders"]["won"] >= 1
    assert by_team["Kolkata Knight Riders"]["points"] >= Decimal("2.0")
    no_result_teams = [row for row in rows if row["no_result"]]
    assert len(no_result_teams) == 2
    assert all(row["points"] == Decimal("1.0") * row["no_result"] + 2 * row["won"] for row in rows)
    assert [row["position"] for row in rows] == list(range(1, len(rows) + 1))
    assert all(row["season"] == "2026" for row in rows)


def test_nrr_charges_all_out_side_full_allocation() -> None:
    def ball(runs: int = 1, kind: str | None = None) -> dict:
        value: dict = {
            "batter": "A",
            "bowler": "B",
            "non_striker": "C",
            "runs": {"batter": runs, "extras": 0, "total": runs},
        }
        if kind:
            value["wickets"] = [{"player_out": "A", "kind": kind}]
        return value

    first = {"team": "X", "overs": [{"over": 0, "deliveries": [ball(6) for _ in range(6)]}]}
    second = {"team": "Y", "overs": [{"over": 0, "deliveries": [ball(0, "bowled") for _ in range(6)]}]}
    match = cs.Match(
        1,
        {
            "info": {
                "teams": ["X", "Y"],
                "overs": 1,
                "players": {"X": ["A"] * 11, "Y": ["A"] * 7},
                "outcome": {"winner": "X", "by": {"runs": 36}},
            },
            "innings": [first, second],
        },
    )
    totals = cs.innings_totals(match)
    assert totals[1].all_out
    rows = {row["team"]: row for row in cs.standings_rows([match], "ipl", None)}
    assert rows["X"]["net_run_rate"] == Decimal("36.000")
    assert rows["Y"]["balls_for"] == 6


def test_retired_hurt_is_not_a_dismissal() -> None:
    inning = {
        "team": "X",
        "overs": [
            {
                "over": 0,
                "deliveries": [
                    {
                        "batter": "A",
                        "bowler": "B",
                        "non_striker": "C",
                        "runs": {"batter": 0, "extras": 0, "total": 0},
                        "wickets": [{"player_out": "A", "kind": "retired hurt"}],
                    }
                ],
            }
        ],
    }
    match = cs.Match(2, {"info": {"teams": ["X", "Y"], "dates": ["2026-01-01"]}, "innings": [inning]})
    batting = cs.batting_rows(match, "test")
    assert batting[0]["is_out"] is False
    assert batting[0]["dismissal_kind"] == "retired hurt"
    assert batting[0]["dismissed_by"] is None
    assert cs.innings_rows(match, "test")[0]["wickets"] == 0


def test_season_aggregates() -> None:
    matches = [cricsheet_match(value) for value in (SUPER_OVER_TIE, NO_RESULT, RAIN_AFFECTED, FINAL)]
    batting = cs.batting_stat_rows(matches, "ipl", "2026")
    kohli = next(row for row in batting if row["player"] == "V Kohli")
    assert kohli["player_id"] == "ba607b88"
    assert kohli["highest_score"] >= 75
    assert kohli["teams"] == "Royal Challengers Bengaluru"
    assert batting == sorted(batting, key=lambda row: (-row["runs"], row["player"]))
    bowling = cs.bowling_stat_rows(matches, "ipl", "2026")
    top = bowling[0]
    assert top["wickets"] == max(row["wickets"] for row in bowling)
    assert top["best_figures"].count("/") == 1
    salam = next(row for row in bowling if row["player"] == "Rasikh Salam")
    assert salam["five_wickets"] == 0


def test_season_label_matching() -> None:
    assert cs.season_matches("2026", None)
    assert cs.season_matches("2026", "2026")
    assert cs.season_matches("2025/26", "2025")
    assert cs.season_matches("2025/26", "2025/26")
    assert not cs.season_matches("2025/26", "2026")
    assert not cs.season_matches(None, "2026")
    matches = [cricsheet_match(value) for value in (1473469, FINAL)]
    assert [value.match_id for value in cs.select(matches, season="2025")] == [1473469]
    assert [value.match_id for value in cs.select(matches, match_id=FINAL)] == [FINAL]


def test_overs_notation() -> None:
    assert cs.overs_display(23, 6) == "3.5"
    assert cs.overs_display(100, 5) == "20.0"


def test_registers_parse() -> None:
    people = cs.people_rows((FIXTURES / "cricsheet" / "people.csv").read_text())
    kohli = next(row for row in people if row["name"] == "V Kohli")
    assert kohli["person_id"] == "ba607b88"
    assert kohli["key_cricinfo"] == 253802
    venues = cs.venue_rows((FIXTURES / "cricsheet" / "venues.csv").read_text())
    assert venues
    assert all(row["venue_id"] for row in venues)


def test_malformed_documents_are_tolerated() -> None:
    match = cs.Match(3, {"info": "bad", "innings": "bad"})
    assert match.teams == []
    assert match.balls_per_over == 6
    assert cs.innings_rows(match, "x") == []
    assert cs.match_row(match, "x")["result"] is None


@pytest.mark.parametrize("code", ["all", "all_male", "../etc", "IPL"])
def test_unsafe_codes_are_rejected(code: str) -> None:
    from vgi_cricket.api import validate_cricsheet_code

    with pytest.raises(ValueError):
        validate_cricsheet_code(code)
