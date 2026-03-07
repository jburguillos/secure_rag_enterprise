"""Secure RAG API entrypoint."""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.feedback import router as feedback_router
from app.api.health import router as health_router
from app.api.ingest import router as ingest_router
from app.api.query import router as query_router
from app.api.runs import router as runs_router
from app.db.init_db import init_db
from app.observability.logging import setup_logging
from app.observability.metrics import REQUEST_COUNT, REQUEST_LATENCY

setup_logging()

app = FastAPI(title="Secure RAG API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    path = request.url.path
    REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
    REQUEST_LATENCY.labels(request.method, path).observe(elapsed)
    return response


app.include_router(health_router)
app.include_router(ingest_router)
app.include_router(query_router)
app.include_router(feedback_router)
app.include_router(runs_router)
