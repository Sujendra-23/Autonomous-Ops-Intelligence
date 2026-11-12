"""The example receivers must accept exactly what the backend sends."""

import importlib.util
import json
import shutil
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.integrations import webhooks
from app.models.integration import WebhookDelivery

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "webhook-receivers"
VECTOR = json.loads((EXAMPLES / "vector.json").read_text())


def _load_receiver():
    spec = importlib.util.spec_from_file_location(
        "aoi_receiver", EXAMPLES / "python" / "receiver.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


receiver = _load_receiver()
RAW = VECTOR["raw_body"].encode()
SENT = int(VECTOR["timestamp"])


def test_vector_matches_what_the_backend_signs():
    headers = webhooks.signed_headers(
        RAW, VECTOR["headers"]["X-AOI-Event-ID"], VECTOR["secret"], SENT
    )
    assert headers == VECTOR["headers"]
    event = json.loads(RAW)
    assert RAW == json.dumps(event, sort_keys=True, separators=(",", ":")).encode()


def test_python_receiver_accepts_the_backend_signature():
    assert receiver.verify(
        RAW,
        VECTOR["timestamp"],
        VECTOR["headers"]["X-AOI-Signature"],
        VECTOR["secret"],
        now=SENT + 5,
    )


@pytest.mark.parametrize(
    "case",
    ["tampered body", "wrong secret", "stale", "future", "bad timestamp", "no signature", "empty"],
)
def test_python_receiver_rejects_bad_requests(case):
    body, timestamp, signature, secret, now = (
        RAW,
        VECTOR["timestamp"],
        VECTOR["headers"]["X-AOI-Signature"],
        VECTOR["secret"],
        SENT,
    )
    if case == "tampered body":
        body = RAW.replace(b"high", b"low")
    elif case == "wrong secret":
        secret = "x" * 32
    elif case == "stale":
        now = SENT + 301
    elif case == "future":
        now = SENT - 301
    elif case == "bad timestamp":
        timestamp = "soon"
    elif case == "no signature":
        signature = None
    elif case == "empty":
        signature = "sha256="
    assert not receiver.verify(body, timestamp, signature, secret, now=now)


async def test_backend_delivery_reaches_the_python_receiver_once(monkeypatch):
    secret = "s3cret" * 8
    handled = []
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        receiver.make_handler(secret, receiver.SeenEvents(), handler=handled.append),
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/events"
    config = Settings(_env_file=None)
    config.webhook_url, config.webhook_secret = SecretStr(url), SecretStr(secret)
    monkeypatch.setattr(webhooks, "get_settings", lambda: config)

    def pending_row(event_id):
        return WebhookDelivery(
            id=event_id,
            destination=url,
            event_type="task.created",
            payload={"id": str(event_id), "type": "task.created", "data": {"title": "Café"}},
            status="pending",
            attempts=0,
            next_attempt_at=datetime.now(UTC),
        )

    try:
        event_id = uuid.uuid4()
        # The same event delivered twice, as at-least-once delivery allows.
        for _ in range(2):
            row = pending_row(event_id)
            session = MagicMock(scalar=AsyncMock(side_effect=[row, None]), commit=AsyncMock())
            assert await webhooks.deliver_webhooks(session) == 1
            assert row.status == "delivered"
    finally:
        server.shutdown()
    assert [event["id"] for event in handled] == [str(event_id)]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_receiver_suite_passes():
    result = subprocess.run(  # noqa: S603
        ["node", "--test"],  # noqa: S607
        cwd=EXAMPLES / "node",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
