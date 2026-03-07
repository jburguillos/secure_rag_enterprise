"""Prometheus metrics."""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

REQUEST_COUNT = Counter("secure_rag_http_requests_total", "Total API requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("secure_rag_http_request_latency_seconds", "API request latency", ["method", "path"])
QUERY_COUNT = Counter("secure_rag_query_total", "Total query requests", ["status"])
INGEST_COUNT = Counter("secure_rag_ingest_total", "Total ingestion runs", ["source", "status"])


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
