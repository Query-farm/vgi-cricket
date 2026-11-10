"""A local HTTP server that impersonates every upstream with recorded, trimmed real responses."""

from __future__ import annotations

import io
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def archive_bytes(code: str) -> bytes | None:
    """Zip the recorded Cricsheet match files for ``code`` the way Cricsheet does."""
    folder = FIXTURES / "cricsheet" / code
    if not folder.is_dir():
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.glob("*.json")):
            archive.writestr(path.name, path.read_bytes())
        archive.writestr("README.txt", "Fixture archive built from Cricsheet match files (ODC-By 1.0).\n")
    return buffer.getvalue()


class FixtureHandler(BaseHTTPRequestHandler):
    """Route upstream-shaped paths to fixture files."""

    requests: list[str] = []

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _send(self, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Last-Modified", "Tue, 03 Nov 2026 18:09:07 GMT")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name: str) -> None:
        path = FIXTURES / name
        if not path.is_file():
            self.send_error(404)
            return
        self._send(path.read_bytes(), "text/csv" if name.endswith(".csv") else "application/json")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        path = parsed.path
        type(self).requests.append(self.path)
        if not self.headers.get("User-Agent", "").startswith("vgi-cricket/"):
            self.send_error(403, "missing honest User-Agent")
            return
        parts = [part for part in path.split("/") if part]
        if path.startswith("/cricsheet/downloads/") and path.endswith("_json.zip"):
            body = archive_bytes(parts[-1].removesuffix("_json.zip"))
            if body is None:
                self.send_error(404)
                return
            self._send(body, "application/zip")
        elif path.startswith("/cricsheet/register/"):
            self._file(f"cricsheet/{parts[-1]}")
        elif path == "/espn-web/scorepanel":
            self._file("espn_scorepanel.json")
        elif path.startswith("/espn-site/") and path.endswith("/scoreboard"):
            self._file("espn_scoreboard_8048_2026.json")
        elif path.startswith("/espn-site/") and path.endswith("/summary"):
            self._file(f"espn_summary_{query.get('event')}.json")
        elif path.startswith("/espn-web/") and path.endswith("/playbyplay"):
            self._file(f"espn_playbyplay_{query.get('event')}_p{query.get('page')}.json")
        elif path == "/icc/ranking":
            if not query.get("client_id"):
                self.send_error(401)
                return
            self._file("icc_team_test.json" if query.get("type") == "team" else "icc_allrounder_test.json")
        elif path == "/ecb/tournaments":
            self._file("ecb_tournaments.json")
        elif path == "/ecb/fixtures":
            self._file(f"ecb_fixtures_{query.get('tournamentIds')}.json")
        elif path.startswith("/ecb/tournaments/") and path.endswith("/standings"):
            self._file(f"ecb_standings_{parts[2]}.json")
        else:
            self.send_error(404)


def environment(port: int) -> dict[str, str]:
    """Environment variables pointing every upstream at the fixture server."""
    root = f"http://127.0.0.1:{port}"
    return {
        "CRICKET_CRICSHEET_BASE_URL": f"{root}/cricsheet",
        "CRICKET_ESPN_SITE_BASE_URL": f"{root}/espn-site",
        "CRICKET_ESPN_WEB_BASE_URL": f"{root}/espn-web",
        "CRICKET_ICC_BASE_URL": f"{root}/icc",
        "CRICKET_ECB_BASE_URL": f"{root}/ecb",
        "CRICKET_CACHE_DIR": "off",
    }


@contextmanager
def serve() -> Iterator[ThreadingHTTPServer]:
    """Run the fixture server on an ephemeral port."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
