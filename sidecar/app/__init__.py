"""Parrot sidecar — FastAPI app factory.

Wires the engine: environment bootstrap (Windows HF-cache fix) → logging with
secret redaction → the REST + WS surface (ipc-contract.md). Heavy ML is imported
lazily on first model access, so `create_app()` and `/healthz` stay instant.
"""

import asyncio
import contextlib
import logging
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config
from .core import db
from .core.logging import configure_logging, redact
from .routers import (
    audio,
    engine,
    generate,
    health,
    history,
    profiles,
    settings,
    setup,
    transcribe,
    ws,
)
from .services import generation_progress, model_manager, setup_manager
from .services import transcribe as transcribe_svc
from .services.errors import ServiceError

log = logging.getLogger(__name__)


def _app_version() -> str:
    try:
        return version("parrot-sidecar")
    except PackageNotFoundError:
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        try:
            with pyproject.open("rb") as f:
                return tomllib.load(f)["project"]["version"]
        except Exception:
            return "0.0.0"


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    db.init_db()  # idempotent schema create
    loop = asyncio.get_running_loop()
    setup_manager.bind_loop(loop)  # model-download SSE publish from worker threads
    transcribe_svc.bind_loop(loop)  # whisper-model-download SSE publish from worker threads
    generation_progress.bind_loop(loop)  # synthesis-progress SSE publish from the GPU thread
    try:
        yield
    finally:
        model_manager.flush()  # unload model + free VRAM on shutdown


def create_app() -> FastAPI:
    config.prepare_environment()  # MUST run before any huggingface_hub import
    configure_logging()

    app = FastAPI(title="Parrot sidecar", version=_app_version(), lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "X-Audio-Id",
            "X-Gen-Time",
            "X-Audio-Path",
            "X-Seed",
            "X-Audio-Duration",
            "Content-Length",
        ],
    )

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        # Form/JSON validation (FastAPI's default 422) is re-raised as a 400 with
        # a single string `detail`, per ipc-contract.md §2.
        errs = exc.errors()
        first = errs[0] if errs else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        msg = first.get("msg", "Invalid request.")
        detail = f"{loc}: {msg}" if loc else msg
        return JSONResponse(status_code=400, content={"detail": redact(detail)})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error")
        return JSONResponse(status_code=500, content={"detail": redact(str(exc))})

    app.include_router(health.router)
    app.include_router(engine.router)
    app.include_router(generate.router)
    app.include_router(profiles.router)
    app.include_router(history.router)
    app.include_router(setup.router)
    app.include_router(settings.router)
    app.include_router(transcribe.router)
    app.include_router(audio.router)
    app.include_router(ws.router)
    return app
