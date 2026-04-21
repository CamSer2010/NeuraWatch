"""NeuraWatch FastAPI entry point.

Dev run (from `backend/`):
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

WS / alerts / upload routers land in NW-1203 / 1403 / 1202.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.health import router as health_router
from .config import get_settings
from .services.frame_processor import FrameProcessor
from .services.inference_service import InferenceService


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Ensure storage dirs exist before anything touches them
    # (NW-1402 snapshots, NW-1202 uploads, NW-1101 weights).
    settings.frames_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.model_weights_dir.mkdir(parents=True, exist_ok=True)

    # NW-1101: load YOLO once at startup. load() blocks (disk I/O
    # + ~6MB weights download on first run); running it off the
    # event loop keeps startup idiomatic.
    inference_service = InferenceService(
        weights_path=settings.model_weights_dir / "yolov8n.pt",
        imgsz=settings.inference_imgsz,
        conf_threshold=settings.confidence_threshold,
    )
    await asyncio.to_thread(inference_service.load)
    app.state.inference_service = inference_service

    # NW-1104: FrameProcessor owns the worker thread + size-1 queue
    # so the NW-1203 WS handler can submit frames without blocking
    # its receive loop. Latest-wins dropping is enforced here.
    frame_processor = FrameProcessor(inference_service)
    frame_processor.start()
    app.state.frame_processor = frame_processor

    try:
        yield
    finally:
        frame_processor.stop()


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
