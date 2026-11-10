"""Tests for the HTTP boundary: headers, retries, caching, and error handling."""

from __future__ import annotations

import gzip
import json

import httpx
import pytest

from vgi_cricket import __version__, api


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sends_honest_user_agent_and_caches_in_process() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"scores": []})

    with _client(handler) as client:
        api.fetch_espn_scorepanel(client=client)
        payload, ttl = api.fetch_espn_scorepanel(client=client)
    assert len(calls) == 1
    assert ttl == api.ESPN_LIVE_TTL
    assert payload == {"scores": []}
    agent = calls[0].headers["user-agent"]
    assert agent == f"vgi-cricket/{__version__} (+https://github.com/Query-farm/vgi-cricket)"
    assert str(calls[0].url) == f"{api.DEFAULT_ESPN_WEB_BASE_URL}/scorepanel"


def test_retries_transient_failures_and_honours_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = iter([429, 503, 200])
    delays: list[float] = []
    monkeypatch.setattr(api.time, "sleep", delays.append)

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        headers = {"Retry-After": "2"} if status == 429 else {}
        return httpx.Response(status, json={"scores": []} if status == 200 else {}, headers=headers)

    with _client(handler) as client:
        api.fetch_espn_scorepanel(client=client)
    assert delays[0] == 2.0
    assert len(delays) == 2


def test_retry_after_http_date_and_cap() -> None:
    response = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    # A Retry-After date in the past falls back to jittered exponential backoff.
    assert 0 < api._retry_delay(response, 0) <= 0.3125
    assert api._retry_delay(httpx.Response(429, headers={"Retry-After": "9999"}), 0) == api.MAX_RETRY_DELAY
    assert 0 < api._retry_delay(None, 1) <= api.MAX_RETRY_DELAY
    assert 0 < api._retry_delay(httpx.Response(503, headers={"Retry-After": "soon"}), 0) <= 1


def test_transport_errors_retry_then_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with _client(handler) as client, pytest.raises(httpx.ConnectError):
        api.fetch_espn_scorepanel(client=client)


def test_failure_is_descriptive() -> None:
    with (
        _client(lambda request: httpx.Response(404, text="no such league")) as client,
        pytest.raises(api.CricketApiError, match="ESPN returned HTTP 404: no such league"),
    ):
        api.fetch_espn_scoreboard(999, client=client)


def test_undeclared_gzip_bodies_are_decoded() -> None:
    body = gzip.compress(json.dumps({"scores": [1]}).encode())

    with _client(lambda request: httpx.Response(200, content=body)) as client:
        payload, _ = api.fetch_espn_scorepanel(client=client)
    assert payload == {"scores": [1]}
    assert api._decode(b"\x1f\x8bnot gzip") == b"\x1f\x8bnot gzip"


def test_malformed_payloads_are_rejected() -> None:
    with _client(lambda request: httpx.Response(200, json={"nope": 1})) as client:
        with pytest.raises(ValueError, match="no scores"):
            api.fetch_espn_scorepanel(client=client)
        with pytest.raises(ValueError, match="no rank"):
            api.fetch_icc_rankings("odi", "bat", client=client)
        with pytest.raises(ValueError, match="no commentary"):
            api.fetch_espn_playbyplay(1, 1, client=client)
    api.clear_cache()
    with (
        _client(lambda request: httpx.Response(200, content=b"<html>")) as client,
        pytest.raises(ValueError, match="not JSON"),
    ):
        api.fetch_espn_scorepanel(client=client)
    api.clear_cache()
    with (
        _client(lambda request: httpx.Response(200, json=[1])) as client,
        pytest.raises(ValueError, match="not an object"),
    ):
        api.fetch_espn_scorepanel(client=client)


def test_argument_validation() -> None:
    with pytest.raises(ValueError):
        api.fetch_espn_scoreboard(0)
    with pytest.raises(ValueError):
        api.fetch_espn_summary(0)
    with pytest.raises(ValueError):
        api.fetch_espn_playbyplay(1, 0)
    with pytest.raises(ValueError, match="ICC format"):
        api.fetch_icc_rankings("odis", "bat")
    with pytest.raises(ValueError, match="ranking type"):
        api.fetch_icc_rankings("odi", "keeper")
    with pytest.raises(ValueError):
        api.fetch_ecb_fixtures(0)
    with pytest.raises(ValueError):
        api.fetch_ecb_standings(-1)
    with pytest.raises(ValueError, match="people"):
        api.fetch_cricsheet_register("teams")
    with pytest.raises(ValueError, match="deliberately not supported"):
        api.fetch_cricsheet_archive("all")


def test_icc_request_carries_public_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRICKET_ICC_CLIENT_ID", "rotated")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": {"bat-rank": {"rank": []}}})

    with _client(handler) as client:
        api.fetch_icc_rankings("t20w", "allrounder", client=client)
    params = seen[0].url.params
    assert params["client_id"] == "rotated"
    assert params["comp_type"] == "t20w"
    assert params["type"] == "allrounder"
    assert params["feed_format"] == "json"


def test_disk_cache_revalidates_with_last_modified(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRICKET_CACHE_DIR", str(tmp_path))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("if-modified-since") == "Tue, 03 Nov 2026 18:09:07 GMT":
            return httpx.Response(304)
        return httpx.Response(
            200, content=b"zip-bytes", headers={"Last-Modified": "Tue, 03 Nov 2026 18:09:07 GMT"}
        )

    with _client(handler) as client:
        first = api.fetch_cricsheet_archive("ipl", client=client)
        api.clear_cache()
        second = api.fetch_cricsheet_archive("ipl", client=client)
    assert first.body == second.body == b"zip-bytes"
    assert len(requests) == 2
    assert "if-modified-since" not in requests[0].headers
    assert requests[1].headers["if-modified-since"] == "Tue, 03 Nov 2026 18:09:07 GMT"
    assert str(requests[0].url) == "https://cricsheet.org/downloads/ipl_json.zip"
    assert list(tmp_path.glob("*.bin"))


def test_disk_cache_serves_stale_copy_when_upstream_fails(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRICKET_CACHE_DIR", str(tmp_path))
    healthy = True

    def handler(request: httpx.Request) -> httpx.Response:
        if healthy:
            return httpx.Response(200, content=b"people", headers={"ETag": '"v1"'})
        return httpx.Response(503, text="down")

    with _client(handler) as client:
        api.fetch_cricsheet_register("people", client=client)
        api.clear_cache()
        healthy = False
        stale = api.fetch_cricsheet_register("people", client=client)
    assert stale.body == b"people"


def test_cache_dir_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("CRICKET_CACHE_DIR", "off")
    assert api.cache_dir() is None
    monkeypatch.setenv("CRICKET_CACHE_DIR", str(tmp_path))
    assert api.cache_dir() == tmp_path
    monkeypatch.delenv("CRICKET_CACHE_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert api.cache_dir() == tmp_path / "vgi-cricket"


def test_memory_cache_is_byte_bounded() -> None:
    cache = api._MemoryCache(10)
    entry = api._Entry(b"123456", 0.0, 0.0, None, None)
    cache.store("a", entry)
    cache.store("b", entry)
    assert cache.get("a") is None
    assert cache.get("b") is entry
    cache.store("huge", api._Entry(b"x" * 11, 0.0, 0.0, None, None))
    assert cache.get("huge") is None


def test_ecb_pagination_salvages_a_refused_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        size = int(request.url.params["pageSize"])
        page = int(request.url.params["page"])
        if size == 100:
            if page == 1:
                return httpx.Response(404)
            return httpx.Response(
                200, json={"pageInfo": {"numPages": 2}, "content": [{"id": n} for n in range(100)]}
            )
        if size == 10:
            if page == 12:
                return httpx.Response(404)
            return httpx.Response(200, json={"pageInfo": {}, "content": [{"id": 1000 + page}]})
        if page == 125:
            return httpx.Response(404)
        return httpx.Response(200, json={"pageInfo": {}, "content": [{"id": 5000 + page}]})

    with _client(handler) as client:
        content, ttl = api.fetch_ecb_tournaments(client=client)
    assert ttl == api.ECB_TOURNAMENTS_TTL
    assert len(content) == 100 + 9 + 9
    assert {"id": 5125} not in content


def test_ecb_pagination_slices_a_gateway_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # ECB's gateway answers 504 when an uncached 100-row page is slow at the
    # origin, even on the first page (before the page count is known).
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        size = int(request.url.params["pageSize"])
        page = int(request.url.params["page"])
        if size == 100:
            return httpx.Response(504, text="upstream request timeout")
        start = page * size
        rows = [{"id": n} for n in range(start, min(start + size, 25))]
        return httpx.Response(200, json={"pageInfo": {"numEntries": 25}, "content": rows})

    with _client(handler) as client:
        content, _ = api.fetch_ecb_tournaments(client=client)
    assert content == [{"id": n} for n in range(25)]


def test_ecb_pagination_reports_unexpected_failures() -> None:
    with (
        _client(lambda request: httpx.Response(404)) as client,
        pytest.raises(api.CricketApiError, match="ECB returned HTTP 404"),
    ):
        api.fetch_ecb_fixtures(1, client=client)
    with (
        _client(lambda request: httpx.Response(200, json={"content": "x"})) as client,
        pytest.raises(ValueError, match="no content"),
    ):
        api.fetch_ecb_fixtures(1, client=client)


def test_env_overrides_every_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CRICKET_CRICSHEET_BASE_URL",
        "CRICKET_ESPN_SITE_BASE_URL",
        "CRICKET_ESPN_WEB_BASE_URL",
        "CRICKET_ICC_BASE_URL",
        "CRICKET_ECB_BASE_URL",
    ):
        monkeypatch.setenv(name, "http://fixture.test/root/")
    assert api.cricsheet_base_url() == "http://fixture.test/root"
    assert api.espn_site_base_url() == "http://fixture.test/root"
    assert api.espn_web_base_url() == "http://fixture.test/root"
    assert api.icc_base_url() == "http://fixture.test/root"
    assert api.ecb_base_url() == "http://fixture.test/root"
