"""Flattening for the ICC rankings feed that powers icc-cricket.com."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _ranks(entries: list[Any]) -> list[tuple[int | None, Mapping[str, Any]]]:
    """Resolve ICC's ``=`` tie marker to the rank of the entry above it."""
    resolved: list[tuple[int | None, Mapping[str, Any]]] = []
    previous: int | None = None
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        raw = str(entry.get("no", "")).strip()
        rank = previous if raw.startswith("=") and _int(raw.lstrip("=")) is None else _int(raw.lstrip("="))
        previous = rank
        resolved.append((rank, entry))
    return resolved


def team_rows(comp_type: str, block: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per ranked team."""
    rank_date = _date(block.get("rank_date"))
    rows = []
    for rank, entry in _ranks(block.get("rank") or []):
        name = entry.get("team_name") or entry.get("Country")
        if not name:
            continue
        rows.append(
            {
                "format": comp_type,
                "rank_date": _date(entry.get("rankdate")) or rank_date,
                "rank": rank,
                "team": name,
                "team_abbreviation": entry.get("shortname"),
                "team_id": _int(entry.get("team_id") or entry.get("Country_id")),
                "matches": _int(entry.get("Matches")),
                "points": _int(entry.get("Points")),
                "rating": _int(entry.get("Rating")),
                "rank_change": entry.get("change"),
            }
        )
    return rows


def player_rows(comp_type: str, discipline: str, block: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per ranked player."""
    rank_date = _date(block.get("rank_date"))
    rows = []
    for rank, entry in _ranks(block.get("rank") or []):
        player_id = _int(entry.get("Player_id"))
        if player_id is None:
            continue
        rows.append(
            {
                "format": comp_type,
                "discipline": discipline,
                "rank_date": _date(entry.get("rankdate")) or rank_date,
                "rank": rank,
                "player": entry.get("Player-name"),
                "player_id": player_id,
                "country": entry.get("Country_name") or entry.get("team_name"),
                "country_abbreviation": entry.get("Country"),
                "team_id": _int(entry.get("team_id") or entry.get("Country_id")),
                "rating": _int(entry.get("Points")),
                "career_best": entry.get("careerbest") or None,
                "rank_change": entry.get("change"),
            }
        )
    return rows
