"""Flattening for the ECB PulseLive cricket API (tournaments, fixtures, standings).

Team records in this feed can carry club contact details; only the identity,
name, and abbreviation are ever copied into results.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _decimal(value: Any, places: str) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result.quantize(Decimal(places)) if result.is_finite() else None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    # ECB uses +0100 offsets without a colon.
    if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def tournament_rows(content: list[Any]) -> list[dict[str, Any]]:
    """One row per ECB tournament."""
    rows = []
    for entry in content:
        entry = _mapping(entry)
        tournament_id = _int(entry.get("id"))
        if tournament_id is None:
            continue
        counts = _mapping(entry.get("matchesByType"))
        rows.append(
            {
                "tournament_id": tournament_id,
                "name": entry.get("name"),
                "tournament_type": entry.get("tournamentType"),
                "match_types": ", ".join(f"{key}:{counts[key]}" for key in sorted(counts)) or None,
                "total_matches": sum(_int(value) or 0 for value in counts.values()) if counts else None,
                "provisional": entry.get("provisional")
                if isinstance(entry.get("provisional"), bool)
                else None,
            }
        )
    rows.sort(key=lambda row: -row["tournament_id"])
    return rows


def _innings_text(innings: list[Any]) -> str | None:
    parts = []
    for inning in innings:
        inning = _mapping(inning)
        runs = _int(inning.get("runs"))
        if runs is None:
            continue
        wickets = _int(inning.get("wkts"))
        score = f"{runs}" if inning.get("allOut") or wickets is None else f"{runs}/{wickets}"
        if inning.get("declared"):
            score += "d"
        if inning.get("overProgress"):
            score += f" ({inning['overProgress']})"
        parts.append(score)
    return " & ".join(parts) or None


def fixture_rows(content: list[Any]) -> list[dict[str, Any]]:
    """One row per fixture."""
    rows = []
    for entry in content:
        entry = _mapping(entry)
        schedule = _mapping(entry.get("scheduleEntry"))
        match = _mapping(schedule.get("matchId") or entry.get("matchId"))
        fixture_id = _int(match.get("id"))
        if fixture_id is None:
            continue
        venue = _mapping(schedule.get("venue"))
        tournament = _mapping(entry.get("tournamentId"))
        team1 = _mapping(schedule.get("team1"))
        team2 = _mapping(schedule.get("team2"))
        rows.append(
            {
                "fixture_id": fixture_id,
                "tournament_id": _int(tournament.get("id")),
                "tournament_name": tournament.get("name") or entry.get("tournamentLabel"),
                "label": entry.get("label"),
                "description": schedule.get("description") or None,
                "start_time": _timestamp(schedule.get("matchDate") or entry.get("timestamp")),
                "end_time": _timestamp(entry.get("endTimestamp")),
                "match_type": schedule.get("matchType"),
                "match_state": schedule.get("matchState"),
                "venue": venue.get("fullName"),
                "venue_city": venue.get("city"),
                "team1": _mapping(team1.get("team")).get("fullName"),
                "team1_id": _int(_mapping(team1.get("team")).get("id")),
                "team1_score": _innings_text(team1.get("innings") or []),
                "team2": _mapping(team2.get("team")).get("fullName"),
                "team2_id": _int(_mapping(team2.get("team")).get("id")),
                "team2_score": _innings_text(team2.get("innings") or []),
                "result": _mapping(schedule.get("matchStatus")).get("text"),
                "overs_limit": _int(schedule.get("oversLimit") or entry.get("oversLimit")),
            }
        )
    rows.sort(key=lambda row: (row["start_time"] or datetime.max.replace(tzinfo=UTC), row["fixture_id"]))
    return rows


def standings_rows(tournament_id: int, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per team per group in the official ECB table."""
    tournament = _mapping(payload.get("tournamentId"))
    rows = []
    for group in payload.get("groups") or []:
        group = _mapping(group)
        for entry in group.get("standings") or []:
            entry = _mapping(entry)
            team = _mapping(entry.get("team"))
            team_id = _int(team.get("id"))
            if team_id is None:
                continue
            form = "".join(
                str(_mapping(value).get("outcome") or "")[:1] for value in entry.get("recentForm") or []
            )
            rows.append(
                {
                    "tournament_id": _int(tournament.get("id")) or tournament_id,
                    "tournament_name": tournament.get("name"),
                    "group_name": group.get("groupName") or None,
                    "position": _int(entry.get("position")),
                    "team": team.get("fullName"),
                    "team_abbreviation": team.get("abbreviation"),
                    "team_id": team_id,
                    "played": _int(entry.get("played")),
                    "won": _int(entry.get("won")),
                    "lost": _int(entry.get("lost")),
                    "tied": _int(entry.get("tied")),
                    "drawn": _int(entry.get("drawn")),
                    "no_result": _int(entry.get("noResult")),
                    "batting_bonus": _int(entry.get("battingBonus")),
                    "bowling_bonus": _int(entry.get("bowlingBonus")),
                    "deductions": _decimal(entry.get("deductions"), "0.1"),
                    "points": _decimal(entry.get("points"), "0.1"),
                    "net_run_rate": _decimal(entry.get("netRunRate"), "0.001"),
                    "runs_for": _int(entry.get("totalRunsFor")),
                    "balls_for": _int(entry.get("totalBallsFor")),
                    "runs_against": _int(entry.get("totalRunsAgainst")),
                    "balls_against": _int(entry.get("totalBallsAgainst")),
                    "recent_form": form or None,
                }
            )
    return rows
