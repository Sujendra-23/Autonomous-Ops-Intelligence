"""Receiver for Autonomous Operational Intelligence webhooks. Standard library only.

    WEBHOOK_SECRET=<same value as the backend> python receiver.py [port]

It verifies the HMAC signature over the exact raw request body, rejects stale timestamps,
acknowledges duplicate event IDs without handling them twice, and returns 2xx quickly.
Replace `handle_event` with your own logic.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 1_000_000
SEEN_LIMIT = 10_000


def sign(raw_body: bytes, timestamp: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256)
    return "sha256=" + digest.hexdigest()


def verify(
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
    secret: str,
    *,
    now: float | None = None,
    tolerance: int = TOLERANCE_SECONDS,
) -> bool:
    """True only for a fresh timestamp and a matching signature of the raw bytes."""
    try:
        sent_at = int(timestamp or "")
    except ValueError:
        return False
    if abs((time.time() if now is None else now) - sent_at) > tolerance:
        return False
    expected = sign(raw_body, str(sent_at), secret)
    return hmac.compare_digest(expected.encode(), (signature or "").encode())


class SeenEvents:
    """Bounded in-memory dedupe. Use a database unique key on the event ID in production."""

    def __init__(self, limit: int = SEEN_LIMIT) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._limit = limit
        self._lock = threading.Lock()

    def seen(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._ids

    def add(self, event_id: str) -> None:
        with self._lock:
            self._ids[event_id] = None
            while len(self._ids) > self._limit:
                self._ids.popitem(last=False)


def handle_event(event: dict) -> None:
    kind, data = event["type"], event.get("data", {})
    if kind == "task.created":
        print(f"new task {data.get('id')}: {data.get('title')} (owner {data.get('owner')})")
    elif kind == "task.updated":
        print(f"task {data.get('id')} is now {data.get('status')}")
    elif kind == "meeting.completed":
        print(f"meeting {data.get('title')!r} finished extraction")
    else:
        print(f"ignoring unknown event type {kind}")


def make_handler(secret: str, seen: SeenEvents, handler=handle_event):
    class Receiver(BaseHTTPRequestHandler):
        def _reply(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                return self._reply(413, {"error": "body too large"})
            raw = self.rfile.read(length)  # verify these exact bytes, never re-serialized JSON
            if not verify(
                raw,
                self.headers.get("X-AOI-Timestamp"),
                self.headers.get("X-AOI-Signature"),
                secret,
            ):
                return self._reply(401, {"error": "invalid signature or timestamp"})
            try:
                event = json.loads(raw)
                event_id = self.headers.get("X-AOI-Event-ID") or event["id"]
            except (ValueError, KeyError):
                return self._reply(400, {"error": "malformed event"})
            if seen.seen(event_id):
                return self._reply(200, {"status": "duplicate"})
            try:
                handler(event)
            except Exception:
                # A 5xx makes the sender retry. Never echo internals back.
                return self._reply(500, {"error": "handler failed"})
            seen.add(event_id)
            self._reply(200, {"status": "ok"})

        def log_message(self, fmt, *args) -> None:  # keep signatures and bodies out of logs
            print(f"{self.address_string()} {fmt % args}")

    return Receiver


def main() -> None:
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if len(secret) < 32:
        sys.exit("Set WEBHOOK_SECRET to the backend's secret (at least 32 characters)")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(secret, SeenEvents()))
    print(f"listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
