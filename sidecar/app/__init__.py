"""Parrot sidecar — FastAPI app factory.

Phase 1 is a stub: it serves liveness (`/healthz`) and a fixed engine-status
(`/engine/status`) so the Rust supervisor and the Svelte UI can prove the
three-process architecture end to end before any ML is wired in.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config
from .routers import engine, health


def create_app() -> FastAPI:
    app = FastAPI(title="Parrot sidecar", version="0.0.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(engine.router)
    return app
