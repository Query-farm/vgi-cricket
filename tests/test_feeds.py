"""Tests for ESPN, ICC, and ECB response flattening against recorded responses."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from tests.conftest import load_json
from vgi_cricket import ecb, espn, icc

FINAL = 1535465


def test_scorepanel_rows() -> None:
    rows = espn.scorepanel_rows(load_json("espn_scorepanel.json"))
    assert rows
    assert all(row["event_id"] for row in rows)
    assert len({row["event_id"] for row in rows}) == len(rows)
    first = rows[0]
    assert first["league_id"] == 18522
    assert first["league_name"] == "Abu Dhabi T10"
    assert first["state"] in {"pre", "in", "post"}
    assert first["start_time"].tzinfo is not None


def test_scoreboard_rows_for_the_ipl_final() -> None:
    rows = espn.scoreboard_rows(load_json("espn_scoreboard_8048_2026.json"))
    final = next(row for row in rows if row["event_id"] == FINAL)
    assert final["league_id"] == 8048
    assert final["league_name"] == "Indian Premier League"
    assert final["season_year"] == 2026
    assert final["description"] == "Final"
    assert final["start_time"] == datetime(2026, 5, 31, 14, 0, tzinfo=UTC)
    assert final["winner"] == "Royal Challengers Bengaluru"
    assert final["home_score"] == "161/5 (18/20 ov, target 156)"
    assert final["away_score"] == "155/8"
    assert final["summary"] == "RCB won by 5 wkts (12b rem)"
    assert final["match_class"] == "Twenty20"


def test_live_batting_rows() -> None:
    rows = espn.live_batting_rows(FINAL, load_json("espn_summary_1535465.json"))
    kohli = next(row for row in rows if row["player"] == "V Kohli")
    assert (kohli["runs"], kohli["balls_faced"], kohli["fours"], kohli["sixes"]) == (75, 42, 9, 3)
    assert kohli["player_id"] == 253802
    assert kohli["is_out"] is False
    assert kohli["dismissal"] == "not out"
    assert kohli["strike_rate"] == Decimal("178.57")
    assert sum(row["runs"] for row in rows if row["innings_number"] == 1) == 150
    keeper_catch = next(row for row in rows if row["player"] == "TH David")
    assert keeper_catch["dismissal"] == "c †Buttler b Arshad Khan"


def test_live_bowling_rows() -> None:
    rows = espn.live_bowling_rows(FINAL, load_json("espn_summary_1535465.json"))
    salam = next(row for row in rows if row["player"] == "Rasikh Salam")
    assert (salam["balls"], salam["runs_conceded"], salam["wickets"]) == (24, 27, 3)
    assert salam["economy"] == Decimal("6.75")
    assert {row["innings_number"] for row in rows} == {1, 2}
    assert sum(row["wickets"] for row in rows if row["innings_number"] == 1) == 8


def test_commentary_rows() -> None:
    items = load_json("espn_playbyplay_1535465_p1.json")["commentary"]["items"]
    rows = espn.commentary_rows(FINAL, items)
    assert [row["overs"] for row in rows] == ["0.1", "0.2", "0.3"]
    assert rows[2]["play_type"] == "four"
    assert rows[2]["runs"] == 4
    assert rows[0]["bowler"] and rows[0]["batter"]
    assert "<" not in (rows[0]["text"] or "")


def test_espn_value_parsers() -> None:
    assert espn.to_rate("-") is None
    assert espn.to_rate("14.666") == Decimal("14.67")
    assert espn.to_int("") is None
    assert espn.to_int(True) is None
    assert espn.to_timestamp("bad") is None
    assert espn.clean_text("<b>Hi</b> &amp; bye") == "Hi & bye"
    assert espn.clean_text(None) is None


def test_icc_team_rows() -> None:
    block = load_json("icc_team_test.json")["data"]["bat-rank"]
    rows = icc.team_rows("test", block)
    assert rows[0]["rank"] == 1
    assert rows[0]["team"] == "South Africa"
    assert rows[0]["rating"] == 124
    assert rows[0]["rank_date"] == date(2026, 11, 9)
    assert [row["rank"] for row in rows] == sorted(row["rank"] for row in rows)


def test_icc_player_rows_resolve_ties() -> None:
    block = load_json("icc_allrounder_test.json")["data"]["bat-rank"]
    rows = icc.player_rows("test", "allrounder", block)
    assert rows[-1]["rank"] == rows[-2]["rank"]
    assert rows[-1]["rating"] == rows[-2]["rating"]
    assert all(row["player_id"] for row in rows)
    assert rows[0]["discipline"] == "allrounder"


def test_ecb_tournaments() -> None:
    rows = ecb.tournament_rows(load_json("ecb_tournaments.json")["content"])
    assert rows == sorted(rows, key=lambda row: -row["tournament_id"])
    nz = next(row for row in rows if row["tournament_id"] == 76749)
    assert nz["match_types"] == "ODI:3, T20I:5"
    assert nz["total_matches"] == 8


def test_ecb_fixtures() -> None:
    rows = ecb.fixture_rows(load_json("ecb_fixtures_76753.json")["content"])
    final = next(row for row in rows if row["fixture_id"] == 113374)
    assert final["team1"] == "Trent Rockets Men"
    assert final["team1_score"] == "158/8 (20.0)"
    assert final["team2_score"] == "162/5 (19.3)"
    assert final["result"] == "Manchester Super Giants Men win by 5 wickets"
    assert final["start_time"] == datetime(2026, 8, 16, 17, 0, tzinfo=UTC)
    assert final["match_state"] == "C"


def test_ecb_standings_drop_contact_details() -> None:
    rows = ecb.standings_rows(76753, load_json("ecb_standings_76753.json"))
    top = rows[0]
    assert (top["team"], top["won"], top["lost"], top["points"]) == (
        "Trent Rockets Men",
        6,
        2,
        Decimal("24.0"),
    )
    assert top["net_run_rate"] == Decimal("0.740")
    county = ecb.standings_rows(76738, load_json("ecb_standings_76738.json"))
    assert county[0]["group_name"] == "Division 1"
    assert county[0]["batting_bonus"] == 24
    # ECB repeats the draw count (6) in `tied`; 7 won + 1 lost + 6 drawn = 14 played, so no ties.
    assert (county[0]["played"], county[0]["drawn"], county[0]["tied"]) == (14, 6, 0)
    assert all(
        row["won"] + row["lost"] + row["tied"] + row["drawn"] + row["no_result"] == row["played"]
        for row in county
    )
    assert county[0]["net_run_rate"] is None
    assert "redacted" not in json.dumps(county, default=str)
