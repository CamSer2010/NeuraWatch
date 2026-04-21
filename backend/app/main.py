"""NeuraWatch FastAPI entry point.

Dev run (from `backend/`):
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Scaffold only — no model load, no routes beyond /health. Model load
lands in NW-1101; WS / alerts / upload routers land in NW-1203 / 1403 / 1202.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.health import router as health_router
from .config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Ensure storage dirs exist before any router can write to them
    # (NW-1402 snapshot save, NW-1202 upload land here).
    settings.frames_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    # Placeholder; NW-1101 wires the YOLO model load here and flips the flag.
    app.state.model_loaded = False
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="NeuraWatch", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    return app


app = create_app()
