"""Parsing and derivation for Cricsheet ball-by-ball JSON archives (data version 1.x).

Cricsheet publishes one JSON document per match inside per-competition zip
archives. Everything here is pure: it takes archive bytes or decoded match
documents and returns plain row dictionaries keyed by the Arrow schema names.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Dismissals credited to the bowler.
BOWLER_WICKETS = frozenset({"bowled", "caught", "caught and bowled", "lbw", "stumped", "hit wicket"})
# Departures that do not count as a dismissal for batting averages or all-out.
NOT_OUT_DEPARTURES = frozenset({"retired hurt", "retired not out"})
TWO_PLACES = Decimal("0.01")
THREE_PLACES = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class Match:
    """One decoded Cricsheet match document."""

    match_id: int
    data: Mapping[str, Any]

    @property
    def info(self) -> Mapping[str, Any]:
        """The match's ``info`` block."""
        value = self.data.get("info")
        return value if isinstance(value, Mapping) else {}

    @property
    def season(self) -> str | None:
        """Cricsheet's season label, normalised to text (for example ``2026`` or ``2025/26``)."""
        value = self.info.get("season")
        return None if value is None else str(value)

    @property
    def balls_per_over(self) -> int:
        """Balls per over for this match (5 for The Hundred's sets, else 6)."""
        value = self.info.get("balls_per_over")
        return value if isinstance(value, int) and value > 0 else 6

    @property
    def teams(self) -> list[str]:
        """The two team names in Cricsheet's order."""
        value = self.info.get("teams")
        return [str(team) for team in value] if isinstance(value, list) else []

    @property
    def innings(self) -> list[Mapping[str, Any]]:
        """Innings blocks in batting order, including super overs."""
        value = self.data.get("innings")
        return [inning for inning in value if isinstance(inning, Mapping)] if isinstance(value, list) else []

    def registry_id(self, name: str | None) -> str | None:
        """Cricsheet's stable person identifier for a name in this match."""
        if not name:
            return None
        registry = self.info.get("registry")
        people = registry.get("people") if isinstance(registry, Mapping) else None
        value = people.get(name) if isinstance(people, Mapping) else None
        return str(value) if value else None


def iter_archive(body: bytes) -> Iterator[Match]:
    """Yield every match document in a Cricsheet ``*_json.zip`` archive."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for name in sorted(archive.namelist()):
            stem = name.rsplit("/", 1)[-1]
            if not stem.endswith(".json") or not stem[:-5].isdigit():
                continue
            data = json.loads(archive.read(name))
            if isinstance(data, dict):
                yield Match(int(stem[:-5]), data)


def season_matches(label: str | None, season: str | None) -> bool:
    """Whether a match's season label satisfies a requested season.

    ``2025`` matches the label ``2025`` and split-year labels such as
    ``2025/26``; an exact split-year label also matches.
    """
    if season is None:
        return True
    if label is None:
        return False
    requested = season.strip()
    return label == requested or label.startswith(f"{requested}/")


def select(
    matches: Iterable[Match], *, season: str | None = None, match_id: int | None = None
) -> list[Match]:
    """Filter matches by season label and/or id."""
    return [
        value
        for value in matches
        if (match_id is None or value.match_id == match_id) and season_matches(value.season, season)
    ]


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _ratio(numerator: int, denominator: int, scale: Decimal = TWO_PLACES) -> Decimal | None:
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(scale, rounding=ROUND_HALF_UP)


def overs_display(balls: int, balls_per_over: int) -> str:
    """Render a legal-ball count in cricket notation, for example 23 balls -> ``3.5``."""
    return f"{balls // balls_per_over}.{balls % balls_per_over}"


def _overs_to_balls(value: Any, balls_per_over: int) -> int | None:
    """Convert Cricsheet's ``overs`` notation (``18.4`` = 18 overs 4 balls) to balls."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value)
    whole, _, part = text.partition(".")
    try:
        return int(whole) * balls_per_over + (int(part) if part else 0)
    except ValueError:
        return None


def _names(values: Any) -> str | None:
    if not isinstance(values, list) or not values:
        return None
    return ", ".join(str(value) for value in values)


def _deliveries(inning: Mapping[str, Any]) -> Iterator[tuple[int, int, Mapping[str, Any]]]:
    """Yield ``(over, ball_in_over_sequence, delivery)`` for one innings."""
    overs = inning.get("overs")
    if not isinstance(overs, list):
        return
    for over in overs:
        if not isinstance(over, Mapping):
            continue
        number = _int(over.get("over")) or 0
        deliveries = over.get("deliveries")
        if not isinstance(deliveries, list):
            continue
        for sequence, delivery in enumerate(deliveries, start=1):
            if isinstance(delivery, Mapping):
                yield number, sequence, delivery


def _extras(delivery: Mapping[str, Any]) -> Mapping[str, Any]:
    value = delivery.get("extras")
    return value if isinstance(value, Mapping) else {}


def _runs(delivery: Mapping[str, Any]) -> Mapping[str, Any]:
    value = delivery.get("runs")
    return value if isinstance(value, Mapping) else {}


def _wickets(delivery: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = delivery.get("wickets")
    return [wicket for wicket in value if isinstance(wicket, Mapping)] if isinstance(value, list) else []


def is_legal(delivery: Mapping[str, Any]) -> bool:
    """A delivery that counts toward the over (neither a wide nor a no-ball)."""
    extras = _extras(delivery)
    return not extras.get("wides") and not extras.get("noballs")


def conceded(delivery: Mapping[str, Any]) -> int:
    """Runs charged to the bowler: bat runs plus wides and no-balls, never byes or leg byes."""
    extras = _extras(delivery)
    return (
        (_int(_runs(delivery).get("batter")) or 0)
        + (_int(extras.get("wides")) or 0)
        + (_int(extras.get("noballs")) or 0)
    )


def _other_team(match: Match, team: str | None) -> str | None:
    others = [value for value in match.teams if value != team]
    return others[0] if others else None


# --------------------------------------------------------------------------- match rows


@dataclass(slots=True)
class InningsTotal:
    """Aggregated totals for one innings."""

    number: int
    team: str | None
    runs: int = 0
    wickets: int = 0
    legal_balls: int = 0
    extras: int = 0
    byes: int = 0
    leg_byes: int = 0
    wides: int = 0
    no_balls: int = 0
    penalties: int = 0
    super_over: bool = False
    declared: bool = False
    forfeited: bool = False
    target_runs: int | None = None
    target_balls: int | None = None
    squad_size: int = 11

    @property
    def all_out(self) -> bool:
        """Whether every available batter was dismissed."""
        return self.wickets >= min(10, max(self.squad_size - 1, 1))


def innings_totals(match: Match) -> list[InningsTotal]:
    """Compute per-innings totals from the delivery record."""
    bpo = match.balls_per_over
    players = match.info.get("players")
    totals: list[InningsTotal] = []
    for number, inning in enumerate(match.innings, start=1):
        team = inning.get("team")
        team_name = str(team) if team is not None else None
        squad = players.get(team_name) if isinstance(players, Mapping) else None
        target = inning.get("target") if isinstance(inning.get("target"), Mapping) else {}
        penalty = inning.get("penalty_runs") if isinstance(inning.get("penalty_runs"), Mapping) else {}
        total = InningsTotal(
            number=number,
            team=team_name,
            super_over=bool(inning.get("super_over")),
            declared=bool(inning.get("declared")),
            forfeited=bool(inning.get("forfeited")),
            target_runs=_int(target.get("runs")),
            target_balls=_overs_to_balls(target.get("overs"), bpo),
            squad_size=len(squad) if isinstance(squad, list) and squad else 11,
        )
        pre_post = (_int(penalty.get("pre")) or 0) + (_int(penalty.get("post")) or 0)
        total.runs += pre_post
        total.penalties += pre_post
        total.extras += pre_post
        for _, _, delivery in _deliveries(inning):
            runs = _runs(delivery)
            extras = _extras(delivery)
            total.runs += _int(runs.get("total")) or 0
            total.extras += _int(runs.get("extras")) or 0
            total.byes += _int(extras.get("byes")) or 0
            total.leg_byes += _int(extras.get("legbyes")) or 0
            total.wides += _int(extras.get("wides")) or 0
            total.no_balls += _int(extras.get("noballs")) or 0
            total.penalties += _int(extras.get("penalty")) or 0
            if is_legal(delivery):
                total.legal_balls += 1
            total.wickets += sum(
                1 for wicket in _wickets(delivery) if wicket.get("kind") not in NOT_OUT_DEPARTURES
            )
        totals.append(total)
    return totals


def _score(total: InningsTotal, bpo: int) -> str:
    wickets = "" if total.all_out else f"/{total.wickets}"
    suffix = "d" if total.declared else ""
    return f"{total.runs}{wickets}{suffix} ({overs_display(total.legal_balls, bpo)} ov)"


def _venue_id(info: Mapping[str, Any]) -> str | None:
    registry = info.get("registry")
    venues = registry.get("venues") if isinstance(registry, Mapping) else None
    value = venues.get(info.get("venue")) if isinstance(venues, Mapping) else None
    return str(value) if value else None


def match_row(match: Match, competition: str) -> dict[str, Any]:
    """Flatten a match's info block into one ``matches`` row."""
    info = match.info
    event = info.get("event") if isinstance(info.get("event"), Mapping) else {}
    outcome = info.get("outcome") if isinstance(info.get("outcome"), Mapping) else {}
    by = outcome.get("by") if isinstance(outcome.get("by"), Mapping) else {}
    toss = info.get("toss") if isinstance(info.get("toss"), Mapping) else {}
    officials = info.get("officials") if isinstance(info.get("officials"), Mapping) else {}
    dates = [value for value in (_date(item) for item in info.get("dates") or []) if value]
    teams = match.teams
    bpo = match.balls_per_over
    totals = [total for total in innings_totals(match) if not total.super_over]

    def team_score(team: str | None) -> str | None:
        scores = [_score(total, bpo) for total in totals if total.team == team]
        return " & ".join(scores) if scores else None

    if outcome.get("winner"):
        result = "win"
    elif outcome.get("result"):
        result = str(outcome["result"])
    else:
        result = None
    winner = outcome.get("winner") or outcome.get("eliminator") or outcome.get("bowl_out")
    if outcome.get("eliminator"):
        decided_by = "super over"
    elif outcome.get("bowl_out"):
        decided_by = "bowl out"
    else:
        decided_by = None
    team1 = teams[0] if teams else None
    team2 = teams[1] if len(teams) > 1 else None
    return {
        "match_id": match.match_id,
        "competition": competition,
        "event_name": event.get("name"),
        "season": match.season,
        "match_number": _int(event.get("match_number")),
        "stage": event.get("stage"),
        "event_group": None if event.get("group") is None else str(event.get("group")),
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "match_type": info.get("match_type"),
        "gender": info.get("gender"),
        "team_type": info.get("team_type"),
        "scheduled_overs": _int(info.get("overs")),
        "balls_per_over": bpo,
        "team1": team1,
        "team2": team2,
        "team1_score": team_score(team1),
        "team2_score": team_score(team2),
        "venue": info.get("venue"),
        "venue_id": _venue_id(info),
        "city": info.get("city"),
        "toss_winner": toss.get("winner"),
        "toss_decision": toss.get("decision"),
        "result": result,
        "winner": winner,
        "win_by_runs": _int(by.get("runs")),
        "win_by_wickets": _int(by.get("wickets")),
        "win_by_innings": bool(by.get("innings")) if by else None,
        "decided_by": decided_by,
        "method": outcome.get("method"),
        "player_of_match": _names(info.get("player_of_match")),
        "umpires": _names(officials.get("umpires")),
    }


def innings_rows(match: Match, competition: str) -> list[dict[str, Any]]:
    """One row per innings (including super overs) with totals and extras."""
    bpo = match.balls_per_over
    rows = []
    for total in innings_totals(match):
        rows.append(
            {
                "match_id": match.match_id,
                "competition": competition,
                "season": match.season,
                "innings_number": total.number,
                "batting_team": total.team,
                "bowling_team": _other_team(match, total.team),
                "runs": total.runs,
                "wickets": total.wickets,
                "legal_balls": total.legal_balls,
                "overs": overs_display(total.legal_balls, bpo),
                "run_rate": _ratio(total.runs * 6, total.legal_balls),
                "extras": total.extras,
                "byes": total.byes,
                "leg_byes": total.leg_byes,
                "wides": total.wides,
                "no_balls": total.no_balls,
                "penalties": total.penalties,
                "all_out": total.all_out,
                "declared": total.declared,
                "forfeited": total.forfeited,
                "super_over": total.super_over,
                "target_runs": total.target_runs,
            }
        )
    return rows


# --------------------------------------------------------------------------- scorecards


@dataclass(slots=True)
class _BatterLine:
    name: str
    position: int
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    dots: int = 0
    is_out: bool = False
    dismissal_kind: str | None = None
    bowler: str | None = None
    fielders: str | None = None


@dataclass(slots=True)
class _BowlerLine:
    name: str
    order: int
    legal_balls: int = 0
    conceded: int = 0
    wickets: int = 0
    dots: int = 0
    fours: int = 0
    sixes: int = 0
    wides: int = 0
    no_balls: int = 0
    maidens: int = 0
    overs: dict[int, list[int]] = field(default_factory=dict)


def _batting_lines(inning: Mapping[str, Any]) -> list[_BatterLine]:
    lines: dict[str, _BatterLine] = {}

    def line(name: Any) -> _BatterLine | None:
        if not name:
            return None
        key = str(name)
        if key not in lines:
            lines[key] = _BatterLine(key, len(lines) + 1)
        return lines[key]

    for _, _, delivery in _deliveries(inning):
        batter = line(delivery.get("batter"))
        line(delivery.get("non_striker"))
        runs = _runs(delivery)
        if batter is not None:
            bat_runs = _int(runs.get("batter")) or 0
            batter.runs += bat_runs
            if not _extras(delivery).get("wides"):
                batter.balls += 1
                if bat_runs == 0:
                    batter.dots += 1
            if not runs.get("non_boundary"):
                batter.fours += 1 if bat_runs == 4 else 0
                batter.sixes += 1 if bat_runs == 6 else 0
        for wicket in _wickets(delivery):
            out = line(wicket.get("player_out"))
            if out is None:
                continue
            kind = wicket.get("kind")
            out.dismissal_kind = None if kind is None else str(kind)
            out.is_out = kind not in NOT_OUT_DEPARTURES
            if kind in BOWLER_WICKETS:
                out.bowler = None if delivery.get("bowler") is None else str(delivery.get("bowler"))
            fielders = wicket.get("fielders")
            if isinstance(fielders, list):
                names = [str(f.get("name")) for f in fielders if isinstance(f, Mapping) and f.get("name")]
                out.fielders = ", ".join(names) or None
    return list(lines.values())


def _bowling_lines(inning: Mapping[str, Any], balls_per_over: int) -> list[_BowlerLine]:
    lines: dict[str, _BowlerLine] = {}
    for over, _, delivery in _deliveries(inning):
        name = delivery.get("bowler")
        if not name:
            continue
        key = str(name)
        if key not in lines:
            lines[key] = _BowlerLine(key, len(lines) + 1)
        bowler = lines[key]
        extras = _extras(delivery)
        charged = conceded(delivery)
        bat_runs = _int(_runs(delivery).get("batter")) or 0
        legal = is_legal(delivery)
        bowler.conceded += charged
        bowler.wides += _int(extras.get("wides")) or 0
        bowler.no_balls += _int(extras.get("noballs")) or 0
        if not _runs(delivery).get("non_boundary"):
            bowler.fours += 1 if bat_runs == 4 else 0
            bowler.sixes += 1 if bat_runs == 6 else 0
        if legal:
            bowler.legal_balls += 1
            if (_int(_runs(delivery).get("total")) or 0) == 0:
                bowler.dots += 1
        bowler.wickets += sum(1 for wicket in _wickets(delivery) if wicket.get("kind") in BOWLER_WICKETS)
        tally = bowler.overs.setdefault(over, [0, 0])
        tally[0] += 1 if legal else 0
        tally[1] += charged
    for bowler in lines.values():
        bowler.maidens = sum(
            1 for legal, runs in bowler.overs.values() if legal >= balls_per_over and runs == 0
        )
    return list(lines.values())


def batting_rows(match: Match, competition: str) -> list[dict[str, Any]]:
    """One row per batter per innings, in batting order (super overs excluded)."""
    rows = []
    start = match_row_dates(match)
    for number, inning in enumerate(match.innings, start=1):
        if inning.get("super_over"):
            continue
        team = None if inning.get("team") is None else str(inning.get("team"))
        for value in _batting_lines(inning):
            rows.append(
                {
                    "match_id": match.match_id,
                    "competition": competition,
                    "season": match.season,
                    "start_date": start,
                    "innings_number": number,
                    "batting_team": team,
                    "bowling_team": _other_team(match, team),
                    "batting_position": value.position,
                    "batter": value.name,
                    "batter_id": match.registry_id(value.name),
                    "runs": value.runs,
                    "balls": value.balls,
                    "fours": value.fours,
                    "sixes": value.sixes,
                    "dots": value.dots,
                    "strike_rate": _ratio(value.runs * 100, value.balls),
                    "is_out": value.is_out,
                    "dismissal_kind": value.dismissal_kind,
                    "dismissed_by": value.bowler,
                    "fielders": value.fielders,
                }
            )
    return rows


def bowling_rows(match: Match, competition: str) -> list[dict[str, Any]]:
    """One row per bowler per innings, in bowling order (super overs excluded)."""
    rows = []
    bpo = match.balls_per_over
    start = match_row_dates(match)
    for number, inning in enumerate(match.innings, start=1):
        if inning.get("super_over"):
            continue
        batting = None if inning.get("team") is None else str(inning.get("team"))
        for value in _bowling_lines(inning, bpo):
            rows.append(
                {
                    "match_id": match.match_id,
                    "competition": competition,
                    "season": match.season,
                    "start_date": start,
                    "innings_number": number,
                    "bowling_team": _other_team(match, batting),
                    "batting_team": batting,
                    "bowling_order": value.order,
                    "bowler": value.name,
                    "bowler_id": match.registry_id(value.name),
                    "legal_balls": value.legal_balls,
                    "overs": overs_display(value.legal_balls, bpo),
                    "maidens": value.maidens,
                    "runs_conceded": value.conceded,
                    "wickets": value.wickets,
                    "economy": _ratio(value.conceded * 6, value.legal_balls),
                    "dots": value.dots,
                    "fours_conceded": value.fours,
                    "sixes_conceded": value.sixes,
                    "wides": value.wides,
                    "no_balls": value.no_balls,
                }
            )
    return rows


def match_row_dates(match: Match) -> date | None:
    """The match's first scheduled date."""
    dates = match.info.get("dates")
    return _date(dates[0]) if isinstance(dates, list) and dates else None


def delivery_rows(match: Match, competition: str) -> list[dict[str, Any]]:
    """One row per delivery, including wides, no-balls, and super-over balls."""
    rows = []
    for number, inning in enumerate(match.innings, start=1):
        team = None if inning.get("team") is None else str(inning.get("team"))
        bowling = _other_team(match, team)
        super_over = bool(inning.get("super_over"))
        legal_count = 0
        for over, sequence, delivery in _deliveries(inning):
            runs = _runs(delivery)
            extras = _extras(delivery)
            wickets = _wickets(delivery)
            legal = is_legal(delivery)
            legal_count += 1 if legal else 0
            first = wickets[0] if wickets else {}
            fielders = first.get("fielders") if isinstance(first.get("fielders"), list) else []
            rows.append(
                {
                    "match_id": match.match_id,
                    "competition": competition,
                    "season": match.season,
                    "innings_number": number,
                    "super_over": super_over,
                    "batting_team": team,
                    "bowling_team": bowling,
                    "over_number": over + 1,
                    "ball_in_over": sequence,
                    "legal_ball_number": legal_count if legal else None,
                    "batter": delivery.get("batter"),
                    "batter_id": match.registry_id(delivery.get("batter")),
                    "non_striker": delivery.get("non_striker"),
                    "bowler": delivery.get("bowler"),
                    "bowler_id": match.registry_id(delivery.get("bowler")),
                    "runs_batter": _int(runs.get("batter")) or 0,
                    "runs_extras": _int(runs.get("extras")) or 0,
                    "runs_total": _int(runs.get("total")) or 0,
                    "wides": _int(extras.get("wides")) or 0,
                    "no_balls": _int(extras.get("noballs")) or 0,
                    "byes": _int(extras.get("byes")) or 0,
                    "leg_byes": _int(extras.get("legbyes")) or 0,
                    "penalty": _int(extras.get("penalty")) or 0,
                    "is_legal": legal,
                    "is_boundary": not runs.get("non_boundary") and (_int(runs.get("batter")) or 0) in (4, 6),
                    "is_wicket": bool(wickets),
                    "player_out": first.get("player_out"),
                    "wicket_kind": first.get("kind"),
                    "fielders": ", ".join(
                        str(value.get("name"))
                        for value in fielders
                        if isinstance(value, Mapping) and value.get("name")
                    )
                    or None,
                }
            )
    return rows


# --------------------------------------------------------------------------- standings


@dataclass(slots=True)
class _TableLine:
    team: str
    group: str | None
    played: int = 0
    won: int = 0
    lost: int = 0
    tied: int = 0
    drawn: int = 0
    no_result: int = 0
    runs_for: int = 0
    balls_for: int = 0
    runs_against: int = 0
    balls_against: int = 0


def is_league_stage(match: Match) -> bool:
    """League-stage matches carry no knockout ``stage`` label in Cricsheet."""
    event = match.info.get("event")
    return not (isinstance(event, Mapping) and event.get("stage"))


def standings_rows(matches: Iterable[Match], competition: str, season: str | None) -> list[dict[str, Any]]:
    """Compute a league table with net run rate from league-stage results.

    Super-over and bowl-out winners take the win. Net run rate uses each
    side's full allocation of balls when it is bowled out, the revised
    allocation from a chase target when one is recorded, and excludes no-result
    and abandoned matches and super overs.
    """
    from vgi_cricket.competitions import points_for_win

    win_points = points_for_win(competition)
    lines: dict[tuple[str | None, str], _TableLine] = {}
    for match in matches:
        if not is_league_stage(match) or len(match.teams) != 2:
            continue
        info = match.info
        event = info.get("event") if isinstance(info.get("event"), Mapping) else {}
        group = None if event.get("group") is None else str(event.get("group"))
        outcome = info.get("outcome") if isinstance(info.get("outcome"), Mapping) else {}
        winner = outcome.get("winner") or outcome.get("eliminator") or outcome.get("bowl_out")
        result = outcome.get("result")
        team_lines = [lines.setdefault((group, team), _TableLine(team, group)) for team in match.teams]
        for value in team_lines:
            value.played += 1
            if winner:
                if value.team == winner:
                    value.won += 1
                else:
                    value.lost += 1
            elif result == "tie":
                value.tied += 1
            elif result == "draw":
                value.drawn += 1
            else:
                value.no_result += 1
        if not winner and result not in {"tie", "draw"}:
            continue
        scheduled = _int(info.get("overs"))
        bpo = match.balls_per_over
        for total in innings_totals(match):
            if total.super_over or total.team not in match.teams:
                continue
            balls = total.legal_balls
            allocation = total.target_balls or (scheduled * bpo if scheduled else None)
            if total.all_out and allocation:
                balls = allocation
            batting = lines[(group, total.team)]
            fielding = lines[(group, _other_team(match, total.team) or "")]
            batting.runs_for += total.runs
            batting.balls_for += balls
            fielding.runs_against += total.runs
            fielding.balls_against += balls

    def nrr(value: _TableLine) -> Decimal | None:
        if value.balls_for <= 0 or value.balls_against <= 0:
            return None
        rate = Decimal(value.runs_for * 6) / Decimal(value.balls_for) - Decimal(
            value.runs_against * 6
        ) / Decimal(value.balls_against)
        return rate.quantize(THREE_PLACES, rounding=ROUND_HALF_UP)

    rows: list[dict[str, Any]] = []
    for line in lines.values():
        points = Decimal(line.won * win_points) + Decimal(line.tied + line.no_result + line.drawn) * (
            Decimal(win_points) / 2
        )
        rows.append(
            {
                "competition": competition,
                "season": season,
                "group_name": line.group,
                "team": line.team,
                "played": line.played,
                "won": line.won,
                "lost": line.lost,
                "tied": line.tied,
                "drawn": line.drawn,
                "no_result": line.no_result,
                "points": points.quantize(Decimal("0.1")),
                "net_run_rate": nrr(line),
                "runs_for": line.runs_for,
                "balls_for": line.balls_for,
                "runs_against": line.runs_against,
                "balls_against": line.balls_against,
            }
        )
    rows.sort(
        key=lambda row: (
            row["group_name"] or "",
            -row["points"],
            -(row["net_run_rate"] if row["net_run_rate"] is not None else Decimal("-999")),
            -row["won"],
            row["team"],
        )
    )
    position = 0
    previous_group: object = object()
    for row in rows:
        if row["group_name"] != previous_group:
            previous_group = row["group_name"]
            position = 0
        position += 1
        row["position"] = position
    return rows


# --------------------------------------------------------------------------- aggregates


def batting_stat_rows(matches: Iterable[Match], competition: str, season: str | None) -> list[dict[str, Any]]:
    """Aggregate batting figures per player across the selected matches."""
    players: dict[str, dict[str, Any]] = {}
    for match in matches:
        appeared = _appearances(match)
        for name, team in appeared.items():
            key = match.registry_id(name) or name
            record = players.setdefault(key, _batting_seed(key, name, competition, season))
            record["_matches"].add(match.match_id)
            record["_teams"].add(team)
        for row in batting_rows(match, competition):
            key = row["batter_id"] or row["batter"]
            record = players.setdefault(key, _batting_seed(key, row["batter"], competition, season))
            record["_matches"].add(match.match_id)
            if row["batting_team"]:
                record["_teams"].add(row["batting_team"])
            record["innings"] += 1
            record["not_outs"] += 0 if row["is_out"] else 1
            record["runs"] += row["runs"]
            record["balls"] += row["balls"]
            record["fours"] += row["fours"]
            record["sixes"] += row["sixes"]
            record["fifties"] += 1 if 50 <= row["runs"] < 100 else 0
            record["hundreds"] += 1 if row["runs"] >= 100 else 0
            record["ducks"] += 1 if row["runs"] == 0 and row["is_out"] else 0
            best = (row["runs"], not row["is_out"])
            if record["_best"] is None or best > record["_best"]:
                record["_best"] = best
    rows = []
    for record in players.values():
        best = record.pop("_best")
        record["matches"] = len(record.pop("_matches"))
        record["teams"] = ", ".join(sorted(team for team in record.pop("_teams") if team))
        outs = record["innings"] - record["not_outs"]
        record["highest_score"] = best[0] if best else None
        record["highest_not_out"] = best[1] if best else None
        record["average"] = _ratio(record["runs"], outs)
        record["strike_rate"] = _ratio(record["runs"] * 100, record["balls"])
        rows.append(record)
    rows.sort(key=lambda row: (-row["runs"], row["player"] or ""))
    return rows


def _batting_seed(key: str, name: str, competition: str, season: str | None) -> dict[str, Any]:
    return {
        "competition": competition,
        "season": season,
        "player_id": key if key != name else None,
        "player": name,
        "_matches": set(),
        "_teams": set(),
        "_best": None,
        "innings": 0,
        "not_outs": 0,
        "runs": 0,
        "balls": 0,
        "fours": 0,
        "sixes": 0,
        "fifties": 0,
        "hundreds": 0,
        "ducks": 0,
    }


def _appearances(match: Match) -> dict[str, str]:
    players = match.info.get("players")
    appeared: dict[str, str] = {}
    if isinstance(players, Mapping):
        for team, names in players.items():
            if isinstance(names, list):
                for name in names:
                    appeared[str(name)] = str(team)
    return appeared


def bowling_stat_rows(matches: Iterable[Match], competition: str, season: str | None) -> list[dict[str, Any]]:
    """Aggregate bowling figures per player across the selected matches."""
    players: dict[str, dict[str, Any]] = {}
    bpo_by_player: dict[str, int] = {}
    for match in matches:
        for row in bowling_rows(match, competition):
            key = row["bowler_id"] or row["bowler"]
            record = players.setdefault(
                key,
                {
                    "competition": competition,
                    "season": season,
                    "player_id": row["bowler_id"],
                    "player": row["bowler"],
                    "_matches": set(),
                    "_teams": set(),
                    "_best": None,
                    "innings": 0,
                    "legal_balls": 0,
                    "maidens": 0,
                    "runs_conceded": 0,
                    "wickets": 0,
                    "dots": 0,
                    "four_wickets": 0,
                    "five_wickets": 0,
                },
            )
            bpo_by_player[key] = match.balls_per_over
            record["_matches"].add(match.match_id)
            if row["bowling_team"]:
                record["_teams"].add(row["bowling_team"])
            record["innings"] += 1
            record["legal_balls"] += row["legal_balls"]
            record["maidens"] += row["maidens"]
            record["runs_conceded"] += row["runs_conceded"]
            record["wickets"] += row["wickets"]
            record["dots"] += row["dots"]
            record["four_wickets"] += 1 if row["wickets"] == 4 else 0
            record["five_wickets"] += 1 if row["wickets"] >= 5 else 0
            best = (row["wickets"], -row["runs_conceded"])
            if record["_best"] is None or best > record["_best"]:
                record["_best"] = best
    rows = []
    for key, record in players.items():
        best = record.pop("_best")
        record["matches"] = len(record.pop("_matches"))
        record["teams"] = ", ".join(sorted(record.pop("_teams")))
        bpo = bpo_by_player.get(key, 6)
        record["overs"] = overs_display(record["legal_balls"], bpo)
        record["best_figures"] = f"{best[0]}/{-best[1]}" if best else None
        record["average"] = _ratio(record["runs_conceded"], record["wickets"])
        record["economy"] = _ratio(record["runs_conceded"] * 6, record["legal_balls"])
        record["strike_rate"] = _ratio(record["legal_balls"], record["wickets"])
        rows.append(record)
    rows.sort(key=lambda row: (-row["wickets"], row["runs_conceded"], row["player"] or ""))
    return rows


# --------------------------------------------------------------------------- registers


_PEOPLE_KEYS = (
    "key_cricinfo",
    "key_cricbuzz",
    "key_bcci",
    "key_pulse",
    "key_opta",
    "key_bigbash",
    "key_cricketarchive",
    "key_nvplay",
)


def people_rows(text: str) -> list[dict[str, Any]]:
    """Parse Cricsheet's ``people.csv`` register."""
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        if not record.get("identifier"):
            continue
        row: dict[str, Any] = {
            "person_id": record["identifier"],
            "name": record.get("name") or None,
            "unique_name": record.get("unique_name") or None,
        }
        for key in _PEOPLE_KEYS:
            row[key] = record.get(key) or None
        row["key_cricinfo"] = _int(row["key_cricinfo"])
        rows.append(row)
    return rows


def venue_rows(text: str) -> list[dict[str, Any]]:
    """Parse Cricsheet's ``venues.csv`` register."""
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        if not record.get("identifier"):
            continue
        rows.append(
            {
                "venue_id": record["identifier"],
                "name": record.get("name") or None,
                "unique_name": record.get("unique_name") or None,
                "key_cricinfo": _int(record.get("key_cricinfo")),
            }
        )
    return rows
