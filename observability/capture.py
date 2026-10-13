"""Local-only drill target and Alertmanager receiver, using the standard library."""

import json
import os
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

EVENTS = []


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/metrics":
            body = b"# TYPE aoi_drill_target gauge\naoi_drill_target 1\n"
            content_type = "text/plain; version=0.0.4"
        elif self.path == "/events":
            body = json.dumps(EVENTS).encode()
            content_type = "application/json"
        elif self.path == "/ready":
            body = b"ready"
            content_type = "text/plain"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path.lstrip("/") not in {
            "ai-oncall",
            "ai-triage",
            "platform-oncall",
            "platform-triage",
        }:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > 1_000_000:
            self.send_error(413)
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except ValueError:
            self.send_error(400)
            return
        event = {
            "received_at": datetime.now(UTC).isoformat(),
            "path": self.path,
            "payload": payload,
        }
        EVENTS.append(event)
        print(json.dumps(event), flush=True)
        self.send_response(200)
        self.end_headers()


if __name__ == "__main__":
    HTTPServer(
        ("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler
    ).serve_forever()
