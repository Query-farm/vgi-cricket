"""Serve one unchanged Cricsheet match in an explicitly incomplete replay archive."""

import hashlib
import io
import json
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


def start_replay() -> ThreadingHTTPServer:
    """Serve the single supported IPL archive path on an ephemeral loopback port."""
    data_dir = Path(__file__).parent / "data"
    provenance = json.loads((data_dir / "provenance.json").read_text())
    match = (data_dir / provenance["member"]).read_bytes()
    if hashlib.sha256(match).hexdigest() != provenance["member_sha256"]:
        raise ValueError("Recorded match digest does not match provenance.json.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(provenance["member"], match)
        archive.writestr("README.txt", "One-match notebook subset from Cricsheet, ODC-By 1.0.\n")
    payload = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        """Reject all paths other than the notebook's one recorded request."""

        def log_message(self, *_args: object) -> None:
            """Keep HTTP access logs out of notebook output."""

        def do_GET(self) -> None:
            """Return the selected archive or an explicit missing-recording error."""
            supported = self.path == "/downloads/ipl_json.zip"
            body = payload if supported else b"Replay supports only the one-match IPL archive."
            self.send_response(200 if supported else 404)
            self.send_header("Content-Type", "application/zip" if supported else "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server
