"""Shared fixtures: recorded upstream responses served from a local HTTP server."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.fixture_server import FIXTURES, environment, serve
from vgi_cricket import api
from vgi_cricket.cricsheet import Match


@pytest.fixture(scope="session")
def fixture_server() -> Iterator[int]:
    """Port of a running fixture server for the whole session."""
    with serve() as server:
        yield server.server_port


@pytest.fixture
def upstreams(fixture_server: int, monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    """Point every upstream base URL at the fixture server and start with a cold cache."""
    for key, value in environment(fixture_server).items():
        monkeypatch.setenv(key, value)
    api.clear_cache()
    yield fixture_server
    api.clear_cache()


@pytest.fixture(autouse=True)
def _cold_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Never let a test write to the user's real disk cache."""
    monkeypatch.setenv("CRICKET_CACHE_DIR", "off")
    api.clear_cache()
    yield
    api.clear_cache()


def load_json(name: str):
    """Decode one fixture file."""
    return json.loads((FIXTURES / name).read_text())


def cricsheet_match(match_id: int, code: str = "ipl") -> Match:
    """Load one recorded Cricsheet match."""
    path: Path = FIXTURES / "cricsheet" / code / f"{match_id}.json"
    return Match(match_id, json.loads(path.read_text()))
