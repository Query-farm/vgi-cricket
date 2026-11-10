"""Small, read-only HTTP client for the cricket worker's public upstream sources.

Every outbound request in the package goes through this module. It fetches on
demand only, identifies itself with an honest User-Agent, retries 429 and
transient 5xx responses with bounded backoff, and caches responses in process
for a per-source freshness window so repeated queries do not hammer upstreams.
Cricsheet archives are additionally kept in a local disk cache and revalidated
with ``If-Modified-Since``/``If-None-Match``; nothing is ever re-served to third
parties.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from vgi_cricket import __version__

USER_AGENT = f"vgi-cricket/{__version__} (+https://github.com/Query-farm/vgi-cricket)"

DEFAULT_CRICSHEET_BASE_URL = "https://cricsheet.org"
DEFAULT_ESPN_SITE_BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/cricket"
DEFAULT_ESPN_WEB_BASE_URL = "https://site.web.api.espn.com/apis/site/v2/sports/cricket"
DEFAULT_ICC_BASE_URL = "https://assets-icc.sportz.io/cricket/v1"
# The public client id embedded in icc-cricket.com's own pages. It is a site
# implementation detail, so it can be overridden if ICC rotates it.
DEFAULT_ICC_CLIENT_ID = "tPZJbRgIub3Vua93/DWtyQ=="
DEFAULT_ECB_BASE_URL = "https://cricketapi-ecb.pulselive.com"

# ESPN resolves a cricket event id regardless of which valid cricket league id
# appears in the summary/play-by-play path, so one stable routing id is used.
ESPN_ROUTING_LEAGUE_ID = 8048

TIMEOUT = httpx.Timeout(30.0, connect=10.0)
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
MAX_RETRY_DELAY = 30.0
QUERY_TIMEOUT = 120.0
MAX_CONCURRENT_REQUESTS = 2
MEMORY_CACHE_BYTES = 256 * 1024 * 1024
STALE_IF_ERROR = 86_400
ECB_PAGE_SIZE = 100
ECB_MAX_PAGES = 20
ECB_SALVAGE_SIZE = 10

# Freshness windows in seconds, chosen to keep request rates low.
CRICSHEET_TTL = 3_600
REGISTER_TTL = 86_400
ESPN_LIVE_TTL = 30
ESPN_SCHEDULE_TTL = 300
ICC_TTL = 6 * 3_600
ECB_TTL = 600
ECB_TOURNAMENTS_TTL = 86_400

ICC_FORMATS = ("test", "odi", "t20", "odiw", "t20w")
ICC_KINDS = ("team", "bat", "bowl", "allrounder")
_CRICSHEET_CODE = re.compile(r"^[a-z0-9_]+$")

logger = logging.getLogger(__name__)
_request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)


class CricketApiError(RuntimeError):
    """A failed response from an upstream cricket source."""

    def __init__(self, source: str, status: int, body: str) -> None:
        super().__init__(f"{source} returned HTTP {status}: {body[:400]}")
        self.source = source
        self.status = status


@dataclass(frozen=True, slots=True)
class Fetched:
    """One upstream body plus the freshness window the worker applies to it."""

    body: bytes
    ttl: int

    def json(self) -> Any:
        """Decode the body as JSON."""
        return json.loads(self.body)


@dataclass(slots=True)
class _Entry:
    body: bytes
    expires: float
    stale_until: float
    last_modified: str | None
    etag: str | None


class _MemoryCache:
    """A byte-bounded LRU of upstream responses."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._size = 0
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> _Entry | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def store(self, key: str, entry: _Entry) -> None:
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._size -= len(previous.body)
            if len(entry.body) > self._limit:
                return
            self._entries[key] = entry
            self._size += len(entry.body)
            while self._size > self._limit and self._entries:
                _, evicted = self._entries.popitem(last=False)
                self._size -= len(evicted.body)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._size = 0


_cache = _MemoryCache(MEMORY_CACHE_BYTES)
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def clear_cache() -> None:
    """Drop every in-process cached response (used by tests)."""
    _cache.clear()


def _key_lock(key: str) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


def _env_url(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


def cricsheet_base_url() -> str:
    """Return the Cricsheet root, optionally overridden for testing."""
    return _env_url("CRICKET_CRICSHEET_BASE_URL", DEFAULT_CRICSHEET_BASE_URL)


def espn_site_base_url() -> str:
    """Return the ESPN site API cricket root, optionally overridden for testing."""
    return _env_url("CRICKET_ESPN_SITE_BASE_URL", DEFAULT_ESPN_SITE_BASE_URL)


def espn_web_base_url() -> str:
    """Return the ESPN site.web API cricket root, optionally overridden for testing."""
    return _env_url("CRICKET_ESPN_WEB_BASE_URL", DEFAULT_ESPN_WEB_BASE_URL)


def icc_base_url() -> str:
    """Return the ICC rankings feed root, optionally overridden for testing."""
    return _env_url("CRICKET_ICC_BASE_URL", DEFAULT_ICC_BASE_URL)


def ecb_base_url() -> str:
    """Return the ECB PulseLive API root, optionally overridden for testing."""
    return _env_url("CRICKET_ECB_BASE_URL", DEFAULT_ECB_BASE_URL)


def cache_dir() -> Path | None:
    """Return the Cricsheet disk-cache directory, or None when disabled."""
    configured = os.environ.get("CRICKET_CACHE_DIR")
    if configured is not None:
        if configured.strip().lower() in {"", "0", "off", "none", "false"}:
            return None
        return Path(configured).expanduser()
    root = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(root) / "vgi-cricket"


@contextmanager
def _client(client: httpx.Client | None):
    """Reuse one connection pool for every request in a logical fetch."""
    if client is not None:
        yield client
        return
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as owned:
        yield owned


def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
    """Return a bounded Retry-After delay or exponential backoff with jitter."""
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(raw)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    delay = (retry_at - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError, OverflowError):
                    delay = -1.0
            if delay >= 0:
                return min(delay, MAX_RETRY_DELAY)
    base = min(0.25 * 2**attempt, MAX_RETRY_DELAY)
    return min(base + random.uniform(0, base * 0.25), MAX_RETRY_DELAY)


def _decode(content: bytes) -> bytes:
    """Undo gzip bodies that some upstreams send without a Content-Encoding header."""
    if content[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(content)
        except (OSError, EOFError):
            return content
    return content


def _request(
    source: str,
    url: str,
    params: Mapping[str, str | int],
    *,
    client: httpx.Client,
    deadline: float,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    """Issue one GET, retrying transient failures within the query deadline."""
    for attempt in range(MAX_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{source} query exceeded {QUERY_TIMEOUT:g} seconds")
        started = time.monotonic()
        try:
            if not _request_slots.acquire(timeout=remaining):
                raise TimeoutError(f"{source} query exceeded {QUERY_TIMEOUT:g} seconds")
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{source} query exceeded {QUERY_TIMEOUT:g} seconds")
                response = client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json, */*", "User-Agent": USER_AGENT, **(headers or {})},
                    timeout=httpx.Timeout(min(60.0, remaining), connect=min(10.0, remaining)),
                    follow_redirects=True,
                )
            finally:
                _request_slots.release()
        except httpx.TransportError:
            logger.warning(
                "cricket_api_transport_error source=%s attempt=%d duration_ms=%d",
                source,
                attempt + 1,
                round((time.monotonic() - started) * 1_000),
            )
            if attempt + 1 == MAX_ATTEMPTS:
                raise
            time.sleep(min(_retry_delay(None, attempt), max(0.0, deadline - time.monotonic())))
            continue
        logger.debug(
            "cricket_api_response source=%s status=%d attempt=%d duration_ms=%d",
            source,
            response.status_code,
            attempt + 1,
            round((time.monotonic() - started) * 1_000),
        )
        if response.status_code not in RETRYABLE_STATUSES or attempt + 1 == MAX_ATTEMPTS:
            return response
        delay = min(_retry_delay(response, attempt), max(0.0, deadline - time.monotonic()))
        logger.warning(
            "cricket_api_retry source=%s status=%d attempt=%d delay_seconds=%.3f",
            source,
            response.status_code,
            attempt + 1,
            delay,
        )
        time.sleep(delay)
    raise AssertionError("retry loop exited without a response")


def _cache_key(url: str, params: Mapping[str, str | int]) -> str:
    return url + "?" + "&".join(f"{key}={params[key]}" for key in sorted(params))


def _disk_paths(key: str) -> tuple[Path, Path] | None:
    root = cache_dir()
    if root is None:
        return None
    digest = hashlib.sha256(key.encode()).hexdigest()[:32]
    return root / f"{digest}.bin", root / f"{digest}.json"


def _disk_load(key: str) -> _Entry | None:
    paths = _disk_paths(key)
    if paths is None:
        return None
    body_path, meta_path = paths
    try:
        meta = json.loads(meta_path.read_text())
        body = body_path.read_bytes()
    except (OSError, ValueError):
        return None
    if meta.get("key") != key or meta.get("size") != len(body):
        return None
    return _Entry(
        body=body,
        expires=0.0,
        stale_until=0.0,
        last_modified=meta.get("last_modified"),
        etag=meta.get("etag"),
    )


def _disk_store(key: str, entry: _Entry) -> None:
    paths = _disk_paths(key)
    if paths is None:
        return
    body_path, meta_path = paths
    try:
        body_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = body_path.with_suffix(".tmp")
        temporary.write_bytes(entry.body)
        temporary.replace(body_path)
        meta_path.write_text(
            json.dumps(
                {
                    "key": key,
                    "size": len(entry.body),
                    "last_modified": entry.last_modified,
                    "etag": entry.etag,
                }
            )
        )
    except OSError as error:
        logger.warning("cricket_disk_cache_write_failed error=%s", error)


def fetch(
    source: str,
    url: str,
    params: Mapping[str, str | int] | None = None,
    *,
    ttl: int,
    disk: bool = False,
    client: httpx.Client | None = None,
    deadline: float | None = None,
) -> Fetched:
    """GET ``url`` through the in-process (and optional disk) cache."""
    query = dict(params or {})
    key = _cache_key(url, query)
    with _key_lock(key):
        now = time.monotonic()
        entry = _cache.get(key)
        if entry is not None and entry.expires > now:
            return Fetched(entry.body, ttl)
        if entry is None and disk:
            entry = _disk_load(key)
        conditional: dict[str, str] = {}
        if entry is not None:
            if entry.etag:
                conditional["If-None-Match"] = entry.etag
            if entry.last_modified:
                conditional["If-Modified-Since"] = entry.last_modified

        query_deadline = deadline if deadline is not None else time.monotonic() + QUERY_TIMEOUT
        try:
            with _client(client) as http:
                response = _request(
                    source, url, query, client=http, deadline=query_deadline, headers=conditional
                )
        except (httpx.TransportError, TimeoutError):
            if entry is not None and (entry.stale_until > now or disk):
                logger.warning("cricket_api_serving_stale source=%s reason=transport", source)
                return Fetched(entry.body, ttl)
            raise

        if response.status_code == 304 and entry is not None:
            refreshed = _Entry(
                body=entry.body,
                expires=time.monotonic() + ttl,
                stale_until=time.monotonic() + ttl + STALE_IF_ERROR,
                last_modified=response.headers.get("last-modified") or entry.last_modified,
                etag=response.headers.get("etag") or entry.etag,
            )
            _cache.store(key, refreshed)
            return Fetched(refreshed.body, ttl)
        if not response.is_success:
            if entry is not None and response.status_code >= 500 and (entry.stale_until > now or disk):
                logger.warning("cricket_api_serving_stale source=%s status=%d", source, response.status_code)
                return Fetched(entry.body, ttl)
            raise CricketApiError(
                source, response.status_code, _decode(response.content).decode(errors="replace")
            )

        fresh = _Entry(
            body=_decode(response.content),
            expires=time.monotonic() + ttl,
            stale_until=time.monotonic() + ttl + STALE_IF_ERROR,
            last_modified=response.headers.get("last-modified"),
            etag=response.headers.get("etag"),
        )
        _cache.store(key, fresh)
        if disk:
            _disk_store(key, fresh)
        return Fetched(fresh.body, ttl)


def _json_object(fetched: Fetched, source: str) -> dict[str, Any]:
    try:
        payload = fetched.json()
    except ValueError as error:
        raise ValueError(f"{source} returned a body that is not JSON") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{source} returned JSON that is not an object")
    return payload


# --------------------------------------------------------------------------- Cricsheet


def validate_cricsheet_code(code: str) -> str:
    """Reject archive names that are malformed or would pull the 149 MB all-matches zip."""
    if not _CRICSHEET_CODE.match(code):
        raise ValueError(f"invalid Cricsheet competition code {code!r}")
    if code == "all" or code.startswith("all_"):
        raise ValueError(
            "the all-matches Cricsheet archive is deliberately not supported; choose a competition"
        )
    return code


def fetch_cricsheet_archive(code: str, *, client: httpx.Client | None = None) -> Fetched:
    """Download (or revalidate) one Cricsheet ``<code>_json.zip`` archive."""
    validate_cricsheet_code(code)
    return fetch(
        "Cricsheet",
        f"{cricsheet_base_url()}/downloads/{code}_json.zip",
        ttl=CRICSHEET_TTL,
        disk=True,
        client=client,
    )


def fetch_cricsheet_register(name: str, *, client: httpx.Client | None = None) -> Fetched:
    """Download (or revalidate) a Cricsheet register CSV: ``people`` or ``venues``."""
    if name not in {"people", "venues"}:
        raise ValueError("Cricsheet register must be 'people' or 'venues'")
    return fetch(
        "Cricsheet",
        f"{cricsheet_base_url()}/register/{name}.csv",
        ttl=REGISTER_TTL,
        disk=True,
        client=client,
    )


# --------------------------------------------------------------------------- ESPN


def fetch_espn_scorepanel(*, client: httpx.Client | None = None) -> tuple[dict[str, Any], int]:
    """Fetch ESPN's cricket scorepanel: every live, recent, and upcoming match."""
    fetched = fetch("ESPN", f"{espn_web_base_url()}/scorepanel", ttl=ESPN_LIVE_TTL, client=client)
    payload = _json_object(fetched, "ESPN scorepanel")
    if not isinstance(payload.get("scores"), list):
        raise ValueError("ESPN scorepanel response has no scores array")
    return payload, fetched.ttl


def fetch_espn_scoreboard(
    league_id: int, season: int | None = None, *, client: httpx.Client | None = None
) -> tuple[dict[str, Any], int]:
    """Fetch one ESPN cricket league's scoreboard, optionally for a whole season."""
    if league_id <= 0:
        raise ValueError("league_id must be positive")
    params: dict[str, str | int] = {}
    if season is not None:
        params["season"] = season
    fetched = fetch(
        "ESPN",
        f"{espn_site_base_url()}/{league_id}/scoreboard",
        params,
        ttl=ESPN_SCHEDULE_TTL,
        client=client,
    )
    payload = _json_object(fetched, "ESPN scoreboard")
    if not isinstance(payload.get("events", []), list):
        raise ValueError("ESPN scoreboard response has a malformed events array")
    return payload, fetched.ttl


def fetch_espn_summary(event_id: int, *, client: httpx.Client | None = None) -> tuple[dict[str, Any], int]:
    """Fetch the ESPN match summary (header, rosters with per-innings figures)."""
    if event_id <= 0:
        raise ValueError("event_id must be positive")
    fetched = fetch(
        "ESPN",
        f"{espn_site_base_url()}/{ESPN_ROUTING_LEAGUE_ID}/summary",
        {"event": event_id},
        ttl=ESPN_LIVE_TTL,
        client=client,
    )
    payload = _json_object(fetched, "ESPN summary")
    if not isinstance(payload.get("rosters", []), list):
        raise ValueError("ESPN summary response has a malformed rosters array")
    return payload, fetched.ttl


def fetch_espn_playbyplay(
    event_id: int, page: int, *, client: httpx.Client | None = None
) -> tuple[dict[str, Any], int]:
    """Fetch one 25-ball page of ESPN ball-by-ball commentary."""
    if event_id <= 0 or page <= 0:
        raise ValueError("event_id and page must be positive")
    fetched = fetch(
        "ESPN",
        f"{espn_web_base_url()}/{ESPN_ROUTING_LEAGUE_ID}/playbyplay",
        {"event": event_id, "page": page},
        ttl=ESPN_LIVE_TTL,
        client=client,
    )
    payload = _json_object(fetched, "ESPN play-by-play")
    commentary = payload.get("commentary")
    if not isinstance(commentary, dict) or not isinstance(commentary.get("items", []), list):
        raise ValueError("ESPN play-by-play response has no commentary items")
    return commentary, fetched.ttl


# --------------------------------------------------------------------------- ICC


def fetch_icc_rankings(
    comp_type: str, kind: str, *, client: httpx.Client | None = None
) -> tuple[dict[str, Any], int]:
    """Fetch one ICC rankings table (team, bat, bowl or allrounder) for one format."""
    if comp_type not in ICC_FORMATS:
        raise ValueError(f"ICC format must be one of {', '.join(ICC_FORMATS)}")
    if kind not in ICC_KINDS:
        raise ValueError(f"ICC ranking type must be one of {', '.join(ICC_KINDS)}")
    fetched = fetch(
        "ICC rankings",
        f"{icc_base_url()}/ranking",
        {
            "client_id": os.environ.get("CRICKET_ICC_CLIENT_ID", DEFAULT_ICC_CLIENT_ID),
            "feed_format": "json",
            "lang": "en",
            "comp_type": comp_type,
            "type": kind,
        },
        ttl=ICC_TTL,
        client=client,
    )
    payload = _json_object(fetched, "ICC rankings")
    data = payload.get("data")
    block = data.get("bat-rank") if isinstance(data, dict) else None
    if not isinstance(block, dict) or not isinstance(block.get("rank"), list):
        raise ValueError("ICC rankings response has no rank array")
    return block, fetched.ttl


# --------------------------------------------------------------------------- ECB


def _ecb_page(
    path: str,
    params: dict[str, str | int],
    *,
    size: int,
    page: int,
    ttl: int,
    client: httpx.Client,
    deadline: float,
) -> dict[str, Any]:
    fetched = fetch(
        "ECB",
        f"{ecb_base_url()}/{path}",
        {**params, "pageSize": size, "page": page},
        ttl=ttl,
        client=client,
        deadline=deadline,
    )
    payload = _json_object(fetched, "ECB")
    if not isinstance(payload.get("content"), list):
        raise ValueError("ECB response has no content array")
    return payload


def _ecb_salvage(
    path: str,
    params: dict[str, str | int],
    *,
    page: int,
    ttl: int,
    client: httpx.Client,
    deadline: float,
) -> tuple[list[Any], int | None]:
    """Re-read a page ECB refuses in smaller slices.

    ECB 404s a whole page over one bad record, and its gateway answers 504 when
    an uncached large page takes longer than about 15 seconds at the origin.
    Small slices avoid both. Returns the rows plus the page count (in
    ``ECB_PAGE_SIZE`` units) when a slice reported one.
    """
    rows: list[Any] = []
    skipped = 0
    pages: int | None = None
    for chunk in range(
        page * ECB_PAGE_SIZE // ECB_SALVAGE_SIZE, (page + 1) * ECB_PAGE_SIZE // ECB_SALVAGE_SIZE
    ):
        try:
            payload = _ecb_page(
                path, params, size=ECB_SALVAGE_SIZE, page=chunk, ttl=ttl, client=client, deadline=deadline
            )
            rows.extend(payload["content"])
            info = payload.get("pageInfo") if isinstance(payload.get("pageInfo"), dict) else {}
            entries = info.get("numEntries")
            if isinstance(entries, int):
                pages = -(-entries // ECB_PAGE_SIZE)
            if not payload["content"]:
                break
            continue
        except CricketApiError as error:
            if error.status != 404:
                raise
        for single in range(chunk * ECB_SALVAGE_SIZE, (chunk + 1) * ECB_SALVAGE_SIZE):
            try:
                rows.extend(
                    _ecb_page(path, params, size=1, page=single, ttl=ttl, client=client, deadline=deadline)[
                        "content"
                    ]
                )
            except CricketApiError as error:
                if error.status != 404:
                    raise
                skipped += 1
    if skipped:
        logger.warning("ecb_records_skipped path=%s page=%d skipped=%d", path, page, skipped)
    return rows, pages


def _ecb_pages(path: str, params: dict[str, str | int], ttl: int, client: httpx.Client | None) -> list[Any]:
    content: list[Any] = []
    deadline = time.monotonic() + QUERY_TIMEOUT
    pages: int | None = None
    with _client(client) as http:
        for page in range(ECB_MAX_PAGES):
            try:
                payload = _ecb_page(
                    path, params, size=ECB_PAGE_SIZE, page=page, ttl=ttl, client=http, deadline=deadline
                )
            except CricketApiError as error:
                bad_record = error.status == 404 and pages is not None and page < pages
                if not (bad_record or error.status == 504):
                    raise
                if error.status == 504:
                    logger.warning("ecb_page_timeout path=%s page=%d retrying_in_slices", path, page)
                salvaged, sliced_pages = _ecb_salvage(
                    path, params, page=page, ttl=ttl, client=http, deadline=deadline
                )
                content.extend(salvaged)
                pages = pages if pages is not None else sliced_pages
                if not salvaged or pages is None or page + 1 >= pages:
                    return content
                continue
            rows = payload["content"]
            content.extend(rows)
            info = payload.get("pageInfo") if isinstance(payload.get("pageInfo"), dict) else {}
            pages = info.get("numPages") if isinstance(info.get("numPages"), int) else None
            if not rows or pages is None or page + 1 >= pages:
                return content
    raise RuntimeError(f"ECB pagination exceeded {ECB_MAX_PAGES} pages after {len(content)} rows")


def fetch_ecb_tournaments(*, client: httpx.Client | None = None) -> tuple[list[Any], int]:
    """Fetch every ECB PulseLive tournament, 100 per page."""
    return _ecb_pages("tournaments", {}, ECB_TOURNAMENTS_TTL, client), ECB_TOURNAMENTS_TTL


def fetch_ecb_fixtures(tournament_id: int, *, client: httpx.Client | None = None) -> tuple[list[Any], int]:
    """Fetch every fixture in one ECB tournament."""
    if tournament_id <= 0:
        raise ValueError("tournament_id must be positive")
    return _ecb_pages("fixtures", {"tournamentIds": tournament_id}, ECB_TTL, client), ECB_TTL


def fetch_ecb_standings(
    tournament_id: int, *, client: httpx.Client | None = None
) -> tuple[dict[str, Any], int]:
    """Fetch the official ECB standings for one tournament."""
    if tournament_id <= 0:
        raise ValueError("tournament_id must be positive")
    fetched = fetch(
        "ECB", f"{ecb_base_url()}/tournaments/{tournament_id}/standings", ttl=ECB_TTL, client=client
    )
    payload = _json_object(fetched, "ECB standings")
    if not isinstance(payload.get("groups", []), list):
        raise ValueError("ECB standings response has a malformed groups array")
    return payload, fetched.ttl
