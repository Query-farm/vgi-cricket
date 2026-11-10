"""Hermetic end-to-end coverage through Haybarn and the VGI transport."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.fixture_server import FixtureHandler, environment, serve

CURRENT_TABLES = (
    "competitions",
    "current_matches",
    "current_recent_matches",
    "current_team_rankings",
    "current_player_rankings",
    "people",
    "venues",
    "ecb_tournaments",
)


def test_haybarn_attaches_and_queries_every_current_table() -> None:
    project = Path(__file__).resolve().parent.parent
    location = f"uv run --project {project} vgi-cricket"
    counts = ", ".join(f"(SELECT count(*) FROM cricket.main.{name}) AS {name}" for name in CURRENT_TABLES)
    sql = (
        f"INSTALL vgi FROM community; LOAD vgi; ATTACH 'cricket' (TYPE vgi, LOCATION '{location}'); "
        f"SELECT {counts}; "
        "SELECT winner, win_by_wickets FROM cricket.main.matches('ipl', season := '2026') "
        "WHERE match_id = 1535465; "
        "SELECT sum(runs) AS kohli FROM cricket.main.batting_scorecard('ipl', match_id := 1535465) "
        "WHERE batter = 'V Kohli';"
    )
    FixtureHandler.requests.clear()
    with serve() as server:
        result = subprocess.run(
            ["uvx", "haybarn-cli", ":memory:", "-csv", "-c", sql],
            cwd=project,
            env={**os.environ, **environment(server.server_port)},
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )

    lines = result.stdout.splitlines()
    header = lines.index(",".join(CURRENT_TABLES))
    counts_row = dict(
        zip(CURRENT_TABLES, (int(value) for value in lines[header + 1].split(",")), strict=True)
    )
    assert counts_row["competitions"] == 32
    assert counts_row["current_matches"] == 4
    assert counts_row["current_recent_matches"] == 2
    assert counts_row["current_team_rankings"] == 50
    assert counts_row["current_player_rankings"] == 75
    assert counts_row["people"] >= 20
    assert counts_row["venues"] >= 1
    assert counts_row["ecb_tournaments"] == 4
    assert "Royal Challengers Bengaluru,5" in lines
    assert lines[lines.index("kohli") + 1] == "75"
    assert all("cricsheet.org" not in request for request in FixtureHandler.requests)
