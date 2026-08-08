"""API service (composition root).

Assembles the FastAPI application: routers, CORS, Prometheus metrics endpoint,
and the lifecycle of the real-time hub. Business logic lives in the service
layer; this module only wires and exposes it.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import make_asgi_app

from pulsesearch_common.logging import configure_logging

from .dependencies import get_live_hub
from .routers import health, live, rag, search

log = configure_logging("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub = get_live_hub()
    hub.start(asyncio.get_running_loop())
    log.info("api ready")
    try:
        yield
    finally:
        hub.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="PulseSearch API",
        version="0.1.0",
        description="Real-time hybrid search + grounded RAG over a live CDC index.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(search.router)
    app.include_router(rag.router)
    app.include_router(live.router)

    # Expose Prometheus metrics for scraping.
    app.mount("/metrics", make_asgi_app())

    return app


app = create_app()
