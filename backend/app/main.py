"""NeuraWatch FastAPI entry point.

Dev run (from `backend/`):
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

WS / alerts / upload routers land in NW-1203 / 1403 / 1202.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.health import router as health_router
from .api.routes_alerts import router as alerts_router
from .api.routes_session import router as session_router
from .api.routes_upload import router as upload_router
from .api.routes_ws import router as ws_router
from .config import BACKEND_ROOT, get_settings
from .db import init_db, open_db
from .services.frame_processor import FrameProcessor
from .services.inference_service import InferenceService

# Configure root logger so INFO-level messages from `app.*` services
# surface in the uvicorn terminal. Uvicorn's default only configures
# its own loggers, leaving root handler-less — Python then falls back
# to WARNING-or-above on stderr, which silently drops our
# `logger.info(...)` breadcrumbs (zone installs/clears, session
# claims, etc.).
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)

# Cap known-chatty third-party loggers at WARNING so the terminal
# stays focused on app.* breadcrumbs. Ultralytics is the loudest —
# it logs per-prediction timing at INFO — and would otherwise drown
# out exactly the signals this bootstrap was added to surface.
for _noisy in ("ultralytics", "PIL", "matplotlib", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


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

    # NW-1401: one aiosqlite connection for the whole process. WAL
    # journal mode lets the /alerts REST reader (NW-1403) and the
    # WS-side writer (NW-1402) coexist without lock contention.
    db = await open_db(settings.database_path)
    await init_db(db)
    app.state.db = db

    # NW-1405 POST /session/reset reads this to locate the JPEGs it
    # unlinks. Sharing via app.state (vs re-reading get_settings in
    # the handler) mirrors the routes_alerts pattern and keeps tests
    # from having to clear the lru_cache on settings overrides.
    app.state.frames_dir = settings.frames_dir
    # NW-1202: same pattern for the WS upload processing loop — it
    # opens files from here by video_id and must see the real dir.
    app.state.uploads_dir = settings.uploads_dir
    app.state.max_upload_size_mb = settings.max_upload_size_mb

    try:
        yield
    finally:
        # Shutdown order matters: stop the FrameProcessor first so
        # NW-1402's DB writes drain before the connection closes.
        # Reversing this would surface as AttributeError / closed-db
        # logs every shutdown once the NW-1402 wiring lands.
        frame_processor.stop()
        await db.close()


def create_app() -> FastAPI:
    settings = get_settings()
    # Ensure the frames dir exists BEFORE `StaticFiles(...)` is
    # constructed — some Starlette versions check the directory at
    # mount time, not on first request. The lifespan hook also mkdirs
    # but runs after create_app; this defensive mkdir is idempotent.
    settings.frames_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="NeuraWatch", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(ws_router)
    app.include_router(alerts_router)
    app.include_router(session_router)
    app.include_router(upload_router)

    # NW-1403: serve saved event frames at /frames/{filename}. The
    # `directory=` arg pins StaticFiles to `backend/storage/frames/` —
    # path traversal like `/frames/../etc/passwd` is blocked by
    # Starlette. mkdir in the lifespan guarantees the dir exists
    # before the app starts serving.
    app.mount(
        "/frames",
        StaticFiles(directory=settings.frames_dir),
        name="frames",
    )

    # NW-1504: single-port deploy. When `frontend/dist` exists, serve
    # the built SPA at `/` so one uvicorn + one ngrok tunnel covers the
    # whole app. Mount LAST — StaticFiles at `/` would otherwise shadow
    # every API route and the `/frames` mount above. When the dist dir
    # is absent (fresh clone, backend-only tests), this silently no-ops
    # and the frontend keeps working via the Vite dev server on :3000.
    dist_dir = BACKEND_ROOT.parent / "frontend" / "dist"
    if dist_dir.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=dist_dir, html=True),
            name="spa",
        )
        logging.getLogger("app.main").info(
            "NW-1504: serving SPA bundle from %s", dist_dir
        )
    else:
        logging.getLogger("app.main").info(
            "NW-1504: frontend/dist not found — skipping SPA mount "
            "(run `npm run build` for single-port deploy)"
        )
    return app


app = create_app()
