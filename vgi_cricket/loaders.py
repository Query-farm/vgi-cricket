"""Glue between the HTTP client and the pure parsers: each loader returns rows plus a freshness TTL."""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

from vgi_cricket import api, cricsheet, ecb, espn, icc
from vgi_cricket.competitions import BY_CODE, competition_rows

Rows = tuple[list[dict[str, Any]], int]

MAX_COMMENTARY_PAGES = 120
_LEAGUE = re.compile(r"^\d{1,9}$")


def _matches(code: str, *, season: str | None = None, match_id: int | None = None) -> list[cricsheet.Match]:
    """Load the selected matches of one Cricsheet archive.

    A ``match_id`` lookup reads only that match's member of the zip.
    """
    body = api.fetch_cricsheet_archive(code).body
    if match_id is not None:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            try:
                raw = archive.read(f"{match_id}.json")
            except KeyError:
                return []
        data = json.loads(raw)
        match = cricsheet.Match(match_id, data if isinstance(data, dict) else {})
        return cricsheet.select([match], season=season)
    return cricsheet.select(cricsheet.iter_archive(body), season=season)


def competitions() -> Rows:
    """The static competition catalogue."""
    return competition_rows(), api.REGISTER_TTL


def matches(code: str, season: str | None = None) -> Rows:
    """One row per match in a Cricsheet archive, newest first."""
    rows = [cricsheet.match_row(match, code) for match in _matches(code, season=season)]
    rows.sort(
        key=lambda row: (row["start_date"] is not None, row["start_date"], row["match_id"]), reverse=True
    )
    return rows, api.CRICSHEET_TTL


def innings(code: str, match_id: int | None = None, season: str | None = None) -> Rows:
    """Innings totals for the selected matches."""
    rows = [
        row
        for match in _matches(code, season=season, match_id=match_id)
        for row in cricsheet.innings_rows(match, code)
    ]
    return rows, api.CRICSHEET_TTL


def batting_scorecard(code: str, match_id: int | None = None, season: str | None = None) -> Rows:
    """Batting cards for the selected matches."""
    rows = [
        row
        for match in _matches(code, season=season, match_id=match_id)
        for row in cricsheet.batting_rows(match, code)
    ]
    return rows, api.CRICSHEET_TTL


def bowling_scorecard(code: str, match_id: int | None = None, season: str | None = None) -> Rows:
    """Bowling cards for the selected matches."""
    rows = [
        row
        for match in _matches(code, season=season, match_id=match_id)
        for row in cricsheet.bowling_rows(match, code)
    ]
    return rows, api.CRICSHEET_TTL


def deliveries(code: str, match_id: int | None = None, season: str | None = None) -> Rows:
    """Ball-by-ball rows; a match or season must be chosen to bound the result."""
    if match_id is None and season is None:
        raise ValueError("deliveries() needs match_id or season; a whole archive can hold millions of balls")
    rows = [
        row
        for match in _matches(code, season=season, match_id=match_id)
        for row in cricsheet.delivery_rows(match, code)
    ]
    return rows, api.CRICSHEET_TTL


def latest_season(selected: list[cricsheet.Match]) -> str | None:
    """The season label of the most recently started match."""
    dated = [(cricsheet.match_row_dates(match), match.season) for match in selected if match.season]
    dated = [value for value in dated if value[0] is not None]
    return max(dated)[1] if dated else None


def standings(code: str, season: str | None = None) -> Rows:
    """Computed league table; NULL season selects the archive's latest season."""
    selected = _matches(code)
    label = season if season is not None else latest_season(selected)
    chosen = cricsheet.select(selected, season=label)
    return cricsheet.standings_rows(chosen, code, label), api.CRICSHEET_TTL


def batting_stats(code: str, season: str | None = None) -> Rows:
    """Per-player batting aggregates."""
    return cricsheet.batting_stat_rows(_matches(code, season=season), code, season), api.CRICSHEET_TTL


def bowling_stats(code: str, season: str | None = None) -> Rows:
    """Per-player bowling aggregates."""
    return cricsheet.bowling_stat_rows(_matches(code, season=season), code, season), api.CRICSHEET_TTL


def recent_matches(window: str = "recently_played_7") -> Rows:
    """Matches Cricsheet has published from the last few days, across all competitions."""
    return matches(window)


def people() -> Rows:
    """Cricsheet's people register."""
    fetched = api.fetch_cricsheet_register("people")
    return cricsheet.people_rows(fetched.body.decode("utf-8-sig")), fetched.ttl


def venues() -> Rows:
    """Cricsheet's venue register."""
    fetched = api.fetch_cricsheet_register("venues")
    return cricsheet.venue_rows(fetched.body.decode("utf-8-sig")), fetched.ttl


def resolve_league(league: str) -> int:
    """Map a competition code or a numeric ESPN league id to an ESPN league id."""
    value = league.strip()
    if _LEAGUE.match(value):
        return int(value)
    competition = BY_CODE.get(value)
    if competition is None or competition.espn_league_id is None:
        mapped = ", ".join(code for code, item in BY_CODE.items() if item.espn_league_id is not None)
        raise ValueError(
            f"{league!r} has no ESPN league id; use a numeric ESPN league id or one of: {mapped}"
        )
    return competition.espn_league_id


def current_matches() -> Rows:
    """ESPN's scorepanel: live, recent, and upcoming matches."""
    payload, ttl = api.fetch_espn_scorepanel()
    return espn.scorepanel_rows(payload), ttl


def schedule(league: str, season_year: int | None = None) -> Rows:
    """An ESPN league's fixtures and results for a season."""
    payload, ttl = api.fetch_espn_scoreboard(resolve_league(league), season_year)
    rows = espn.scoreboard_rows(payload)
    rows.sort(key=lambda row: (row["start_time"] is None, row["start_time"], row["event_id"]))
    return rows, ttl


def live_batting(event_id: int) -> Rows:
    """ESPN batting card."""
    payload, ttl = api.fetch_espn_summary(event_id)
    return espn.live_batting_rows(event_id, payload), ttl


def live_bowling(event_id: int) -> Rows:
    """ESPN bowling card."""
    payload, ttl = api.fetch_espn_summary(event_id)
    return espn.live_bowling_rows(event_id, payload), ttl


def live_commentary(event_id: int, page: int | None = None) -> Rows:
    """ESPN ball-by-ball commentary: one page, or every page when ``page`` is NULL."""
    if page is not None:
        commentary, ttl = api.fetch_espn_playbyplay(event_id, page)
        return espn.commentary_rows(event_id, commentary.get("items") or []), ttl
    rows: list[dict[str, Any]] = []
    commentary, ttl = api.fetch_espn_playbyplay(event_id, 1)
    rows.extend(espn.commentary_rows(event_id, commentary.get("items") or []))
    pages = commentary.get("pageCount") if isinstance(commentary.get("pageCount"), int) else 1
    if pages > MAX_COMMENTARY_PAGES:
        raise ValueError(
            f"match {event_id} has {pages} commentary pages; pass page := N (25 balls per page) "
            f"instead of reading more than {MAX_COMMENTARY_PAGES} pages"
        )
    for number in range(2, pages + 1):
        commentary, _ = api.fetch_espn_playbyplay(event_id, number)
        rows.extend(espn.commentary_rows(event_id, commentary.get("items") or []))
    rows.sort(key=lambda row: row["sequence"])
    return rows, ttl


def team_rankings(comp_type: str) -> Rows:
    """ICC team rankings for one format."""
    block, ttl = api.fetch_icc_rankings(comp_type, "team")
    return icc.team_rows(comp_type, block), ttl


def player_rankings(comp_type: str, discipline: str) -> Rows:
    """ICC player rankings for one format and discipline."""
    if discipline == "team":
        raise ValueError("player rankings discipline must be bat, bowl, or allrounder")
    block, ttl = api.fetch_icc_rankings(comp_type, discipline)
    return icc.player_rows(comp_type, discipline, block), ttl


def all_team_rankings() -> Rows:
    """ICC team rankings for every format."""
    rows: list[dict[str, Any]] = []
    for comp_type in api.ICC_FORMATS:
        rows.extend(team_rankings(comp_type)[0])
    return rows, api.ICC_TTL


def all_player_rankings() -> Rows:
    """ICC player rankings for every format and discipline."""
    rows: list[dict[str, Any]] = []
    for comp_type in api.ICC_FORMATS:
        for discipline in ("bat", "bowl", "allrounder"):
            rows.extend(player_rankings(comp_type, discipline)[0])
    return rows, api.ICC_TTL


def ecb_tournaments() -> Rows:
    """Every ECB PulseLive tournament."""
    content, ttl = api.fetch_ecb_tournaments()
    return ecb.tournament_rows(content), ttl


def ecb_fixtures(tournament_id: int) -> Rows:
    """Fixtures and results of one ECB tournament."""
    content, ttl = api.fetch_ecb_fixtures(tournament_id)
    return ecb.fixture_rows(content), ttl


def ecb_standings(tournament_id: int) -> Rows:
    """Official ECB standings of one tournament."""
    payload, ttl = api.fetch_ecb_standings(tournament_id)
    return ecb.standings_rows(tournament_id, payload), ttl
