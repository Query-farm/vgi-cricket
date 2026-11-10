"""Flattening for ESPN's undocumented cricket site API (scorepanel, scoreboard, summary, play-by-play)."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_TAGS = re.compile(r"<[^>]+>")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def to_int(value: Any) -> int | None:
    """Parse an integer-like value without treating booleans as integers."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, OverflowError):
        return None


def to_rate(value: Any) -> Decimal | None:
    """Parse a rate to two decimal places; ESPN's '-' marker becomes NULL."""
    if value is None or isinstance(value, bool) or value in ("", "-"):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result.quantize(Decimal("0.01")) if result.is_finite() else None


def to_timestamp(value: Any) -> datetime | None:
    """Parse ESPN's minute-precision ISO timestamps (``2026-05-31T14:00Z``)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def clean_text(value: Any) -> str | None:
    """Strip markup and entities from commentary text."""
    if not isinstance(value, str):
        return None
    text = html.unescape(_TAGS.sub("", value)).strip()
    return text or None


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def event_row(event: Mapping[str, Any], league: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Flatten one ESPN scoreboard/scorepanel event into a match row."""
    competition = _mapping(_list(event.get("competitions"))[0] if _list(event.get("competitions")) else {})
    status = _mapping(event.get("status") or competition.get("status"))
    status_type = _mapping(status.get("type"))
    venue = _mapping(competition.get("venue"))
    address = _mapping(venue.get("address"))
    season = _mapping(event.get("season"))
    sides: dict[str, Mapping[str, Any]] = {}
    for competitor in _list(competition.get("competitors")):
        competitor = _mapping(competitor)
        side = competitor.get("homeAway")
        if side in ("home", "away"):
            sides[str(side)] = competitor
    home = sides.get("home", {})
    away = sides.get("away", {})
    winner = next(
        (
            _mapping(side.get("team")).get("displayName")
            for side in (home, away)
            if _truthy(side.get("winner"))
        ),
        None,
    )
    league = _mapping(league)
    league_id = to_int(league.get("id")) or to_int(season.get("type"))
    return {
        "event_id": to_int(event.get("id")),
        "league_id": league_id,
        "league_name": league.get("name"),
        "season_year": to_int(season.get("year")),
        "start_time": to_timestamp(event.get("date")),
        "end_time": to_timestamp(event.get("endDate")),
        "name": event.get("name"),
        "short_name": event.get("shortName"),
        "description": competition.get("description") or event.get("description"),
        "match_class": _mapping(competition.get("class")).get("generalClassCard"),
        "venue": venue.get("fullName"),
        "city": address.get("city") or None,
        "country": address.get("country") or None,
        "state": status_type.get("state"),
        "status": status_type.get("description"),
        "summary": clean_text(status.get("summary") or status_type.get("detail")),
        "home_team": _mapping(home.get("team")).get("displayName"),
        "home_team_id": to_int(_mapping(home.get("team")).get("id")),
        "home_score": home.get("score") or None,
        "away_team": _mapping(away.get("team")).get("displayName"),
        "away_team_id": to_int(_mapping(away.get("team")).get("id")),
        "away_score": away.get("score") or None,
        "winner": winner,
    }


def scorepanel_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per match currently listed on ESPN's cricket scorepanel."""
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for block in _list(payload.get("scores")):
        block = _mapping(block)
        leagues = _list(block.get("leagues"))
        league = _mapping(leagues[0]) if leagues else {}
        for event in _list(block.get("events")):
            row = event_row(_mapping(event), league)
            if row["event_id"] is not None and row["event_id"] not in seen:
                seen.add(row["event_id"])
                rows.append(row)
    return rows


def scoreboard_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per match on an ESPN league scoreboard."""
    leagues = _list(payload.get("leagues"))
    league = _mapping(leagues[0]) if leagues else {}
    league = {**league, "name": _strip_year(league.get("name"))}
    rows = [event_row(_mapping(event), league) for event in _list(payload.get("events"))]
    return [row for row in rows if row["event_id"] is not None]


def _strip_year(name: Any) -> Any:
    """ESPN appends the season year to league names on season scoreboards."""
    if isinstance(name, str):
        return re.sub(r"\s+\d{4}(/\d{2,4})?$", "", name)
    return name


def _stats(period: Mapping[str, Any]) -> tuple[dict[str, Any], Mapping[str, Any]]:
    statistics = _mapping(period.get("statistics"))
    values: dict[str, Any] = {}
    for category in _list(statistics.get("categories")):
        for stat in _list(_mapping(category).get("stats")):
            stat = _mapping(stat)
            if stat.get("name"):
                values[str(stat["name"])] = stat.get("value")
    return values, statistics


def _roster_players(payload: Mapping[str, Any]):
    for roster in _list(payload.get("rosters")):
        roster = _mapping(roster)
        team = _mapping(roster.get("team"))
        for player in _list(roster.get("roster")):
            player = _mapping(player)
            athlete = _mapping(player.get("athlete"))
            for period in _list(player.get("linescores")):
                period = _mapping(period)
                values, statistics = _stats(period)
                yield team, athlete, to_int(period.get("period")), values, statistics


def live_batting_rows(event_id: int, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Batting card rows from an ESPN summary: one row per batter per innings."""
    rows = []
    for team, athlete, period, values, statistics in _roster_players(payload):
        if not to_int(values.get("batted")) or period is None:
            continue
        batting = _mapping(statistics.get("batting"))
        out = _mapping(batting.get("outDetails"))
        dismissal = clean_text(out.get("shortText")) or clean_text(values.get("dismissalCard"))
        rows.append(
            {
                "event_id": event_id,
                "innings_number": period,
                "team": team.get("displayName"),
                "team_id": to_int(team.get("id")),
                "batting_position": to_int(values.get("battingPosition")),
                "player": athlete.get("battingName") or athlete.get("displayName"),
                "player_id": to_int(athlete.get("id")),
                "runs": to_int(values.get("runs")),
                "balls_faced": to_int(values.get("ballsFaced")),
                "minutes": to_int(values.get("minutes")),
                "fours": to_int(values.get("fours")),
                "sixes": to_int(values.get("sixes")),
                "strike_rate": to_rate(values.get("strikeRate")),
                "is_out": bool(to_int(values.get("outs"))),
                "dismissal": dismissal,
                "is_batting": bool(batting.get("active")),
            }
        )
    rows = [row for row in rows if row["player_id"] is not None and row["player"]]
    rows.sort(key=lambda row: (row["innings_number"], row["batting_position"] or 99))
    return rows


def live_bowling_rows(event_id: int, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Bowling card rows from an ESPN summary: one row per bowler per innings."""
    rows = []
    for team, athlete, period, values, _ in _roster_players(payload):
        if not to_int(values.get("inningsBowled")) or period is None:
            continue
        rows.append(
            {
                "event_id": event_id,
                "innings_number": period,
                "team": team.get("displayName"),
                "team_id": to_int(team.get("id")),
                "player": athlete.get("battingName") or athlete.get("displayName"),
                "player_id": to_int(athlete.get("id")),
                "overs": None if values.get("overs") is None else str(values.get("overs")),
                "balls": to_int(values.get("balls")),
                "maidens": to_int(values.get("maidens")),
                "runs_conceded": to_int(values.get("conceded")),
                "wickets": to_int(values.get("wickets")),
                "economy": to_rate(values.get("economyRate")),
                "dots": to_int(values.get("dots")),
                "fours_conceded": to_int(values.get("foursConceded")),
                "sixes_conceded": to_int(values.get("sixesConceded")),
                "wides": to_int(values.get("wides")),
                "no_balls": to_int(values.get("noballs")),
            }
        )
    rows = [row for row in rows if row["player_id"] is not None and row["player"]]
    rows.sort(key=lambda row: (row["innings_number"], -(row["balls"] or 0), row["player"]))
    return rows


def commentary_rows(event_id: int, items: list[Any]) -> list[dict[str, Any]]:
    """One row per ball from ESPN play-by-play commentary items."""
    rows = []
    for item in items:
        item = _mapping(item)
        over = _mapping(item.get("over"))
        innings = _mapping(item.get("innings"))
        bowler = _mapping(_mapping(item.get("bowler")).get("athlete"))
        batter = _mapping(_mapping(item.get("batsman")).get("athlete"))
        play_type = _mapping(item.get("playType")).get("description")
        sequence = to_int(item.get("sequence"))
        if sequence is None:
            continue
        overs_value = over.get("overs")
        rows.append(
            {
                "event_id": event_id,
                "sequence": sequence,
                "innings_number": to_int(innings.get("number")) or to_int(item.get("period")),
                "over_number": to_int(over.get("number")),
                "ball_in_over": to_int(over.get("ball")),
                "overs": None if overs_value is None else str(overs_value),
                "batting_team": _mapping(item.get("team")).get("displayName"),
                "bowler": bowler.get("displayName"),
                "bowler_id": to_int(bowler.get("id")),
                "batter": batter.get("displayName"),
                "batter_id": to_int(batter.get("id")),
                "play_type": play_type,
                "runs": to_int(item.get("scoreValue")),
                "is_wicket": bool(_mapping(item.get("dismissal")).get("dismissal")) or play_type == "out",
                "innings_runs": to_int(innings.get("runs")),
                "innings_wickets": to_int(innings.get("wickets")),
                "short_text": clean_text(item.get("shortText")),
                "text": clean_text(item.get("text")),
            }
        )
    return rows
