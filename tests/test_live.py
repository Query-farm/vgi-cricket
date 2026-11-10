"""Opt-in smoke tests against the real upstreams (run with ``pytest -m live``)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from vgi_cricket import api, loaders
from vgi_cricket.competitions import ARCHIVE_CHOICES, COMPETITIONS

FINAL = 1535465

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _real_upstreams(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    for name in (
        "CRICKET_CRICSHEET_BASE_URL",
        "CRICKET_ESPN_SITE_BASE_URL",
        "CRICKET_ESPN_WEB_BASE_URL",
        "CRICKET_ICC_BASE_URL",
        "CRICKET_ECB_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CRICKET_CACHE_DIR", str(tmp_path))


def test_ipl_2026_final_from_cricsheet() -> None:
    rows, _ = loaders.matches("ipl", "2026")
    final = next(row for row in rows if row["match_id"] == FINAL)
    assert final["winner"] == "Royal Challengers Bengaluru"
    assert final["win_by_wickets"] == 5
    assert len(rows) == 74
    batting, _ = loaders.batting_scorecard("ipl", match_id=FINAL)
    kohli = next(row for row in batting if row["batter"] == "V Kohli")
    assert (kohli["runs"], kohli["balls"]) == (75, 42)


def test_computed_standings_agree_with_espn_points() -> None:
    table, _ = loaders.standings("ipl", "2026")
    payload, _ = api.fetch_espn_scoreboard(8048, 2026)
    espn_points = {
        entry["team"]["displayName"]: next(s["value"] for s in entry["stats"] if s["name"] == "matchPoints")
        for entry in payload["standings"]
    }
    assert len(table) == 10
    assert sum(row["played"] for row in table) == 140
    assert {row["team"]: int(row["points"]) for row in table} == espn_points
    assert sum(row["won"] for row in table) == sum(row["lost"] for row in table)


def test_cricsheet_and_espn_cards_agree() -> None:
    cricsheet, _ = loaders.batting_scorecard("ipl", match_id=FINAL)
    espn, _ = loaders.live_batting(FINAL)
    assert sorted(row["runs"] for row in cricsheet) == sorted(row["runs"] for row in espn)
    commentary, _ = loaders.live_commentary(FINAL)
    deliveries, _ = loaders.deliveries("ipl", match_id=FINAL)
    assert len(commentary) == len(deliveries)


def test_current_tables() -> None:
    assert loaders.current_matches()[0]
    assert loaders.recent_matches()[0]
    teams, _ = loaders.all_team_rankings()
    assert {row["format"] for row in teams} == set(api.ICC_FORMATS)
    assert len({(row["format"], row["team"]) for row in teams}) == len(teams)
    players, _ = loaders.all_player_rankings()
    assert len({(row["format"], row["discipline"], row["player_id"]) for row in players}) == len(players)
    people, _ = loaders.people()
    kohli = next(row for row in people if row["person_id"] == "ba607b88")
    assert kohli["key_cricinfo"] == 253802
    assert len(loaders.venues()[0]) > 100
    tournaments, _ = loaders.ecb_tournaments()
    assert len(tournaments) > 500


def test_ecb_official_tables() -> None:
    hundred, _ = loaders.ecb_standings(76753)
    assert hundred[0]["team"] == "Trent Rockets Men"
    assert hundred[0]["points"] == Decimal("24.0")
    county, _ = loaders.ecb_standings(76738)
    assert county[0]["group_name"] == "Division 1"
    fixtures, _ = loaders.ecb_fixtures(76753)
    assert len(fixtures) == 34


def test_every_catalogue_archive_and_espn_league_exists() -> None:
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": api.USER_AGENT}) as client:
        for code in ARCHIVE_CHOICES:
            response = client.head(f"https://cricsheet.org/downloads/{code}_json.zip")
            assert response.status_code == 200, code
    for competition in COMPETITIONS:
        if competition.espn_league_id is not None:
            payload, _ = api.fetch_espn_scoreboard(competition.espn_league_id)
            assert payload.get("leagues"), competition.code
