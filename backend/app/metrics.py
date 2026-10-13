"""Prometheus metrics for LLM calls and extraction outcomes.

Everything here is process-local and uses the default registry, so each API
process exposes its own series at ``GET /metrics``. Run a single uvicorn worker
(as the compose file does) or switch ``prometheus_client`` to multiprocess mode
before scaling out. Labels are deliberately low-cardinality: provider, model,
operation, and outcome. No tenant, transcript, or user identifiers appear.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer

from prometheus_client import Counter, Histogram, MetricsHandler


def start_worker_metrics(port: int = 8001, addr: str = "0.0.0.0"):  # noqa: S104 - internal container network
    """Expose worker-process counters on the internal network, with API-equivalent auth."""
    from app.config import base_settings

    settings = base_settings()
    token = settings.metrics_token.get_secret_value()
    if not token and (settings.auth_mode == "oidc" or settings.environment != "development"):
        return None

    class ProtectedMetricsHandler(MetricsHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            bearer = self.headers.get("Authorization", "")
            supplied = bearer[7:] if bearer.startswith("Bearer ") else ""
            if token and not secrets.compare_digest(supplied, token):
                self.send_error(401)
                return
            super().do_GET()

    server = ThreadingHTTPServer((addr, port), ProtectedMetricsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


LLM_REQUESTS = Counter(
    "aoi_llm_requests_total",
    "LLM calls by outcome: success, error (provider failure), or invalid_output.",
    ["provider", "model", "operation", "outcome"],
)
LLM_LATENCY = Histogram(
    "aoi_llm_request_duration_seconds",
    "Wall-clock latency of one LLM call, including output parsing.",
    ["provider", "model", "operation"],
    buckets=(0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120),
)
LLM_TOKENS = Counter(
    "aoi_llm_tokens_total",
    "Tokens reported by the provider, split by direction (input or output).",
    ["provider", "model", "operation", "direction"],
)
EXTRACTIONS = Counter(
    "aoi_extractions_total",
    "Extractions by mode (full or live), outcome (completed or failed), and failure reason.",
    ["mode", "outcome", "reason"],
)


class LLMCall:
    """Handle yielded by :func:`track_llm_call` for recording call details."""

    def __init__(self, provider: str, model: str, operation: str) -> None:
        self._labels = (provider, model, operation)
        self.outcome: str | None = None

    def record_usage(self, input_tokens: int | None, output_tokens: int | None) -> None:
        for direction, count in (("input", input_tokens), ("output", output_tokens)):
            if count:
                LLM_TOKENS.labels(*self._labels, direction).inc(count)


@contextmanager
def track_llm_call(provider: str, model: str, operation: str) -> Iterator[LLMCall]:
    """Time one LLM call and count it as success, error, or invalid_output.

    Set ``call.outcome = "invalid_output"`` before re-raising when the provider
    answered but the response failed parsing or schema validation.
    """
    call = LLMCall(provider, model, operation)
    started = time.perf_counter()
    try:
        yield call
    except BaseException:
        call.outcome = call.outcome or "error"
        raise
    finally:
        LLM_LATENCY.labels(provider, model, operation).observe(time.perf_counter() - started)
        LLM_REQUESTS.labels(provider, model, operation, call.outcome or "success").inc()


def record_extraction(outcome: str, reason: str = "none", *, mode: str = "full") -> None:
    EXTRACTIONS.labels(mode, outcome, reason).inc()

DOCUMENT_PAGES = Counter(
    "aoi_ingest_document_pages_total",
    "Pages (or images) rendered for vision extraction, by file type.",
    ["file_type"],
)
VIDEO_FRAMES = Counter(
    "aoi_ingest_video_frames_total",
    "Keyframes sampled from screen recordings, by sampling mode (scene or interval).",
    ["mode"],
)
