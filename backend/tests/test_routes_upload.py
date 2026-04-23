"""NW-1202 AC: POST /upload — multipart receive + metadata probe.

Tests synthesize small MP4s with `cv2.VideoWriter` and post them
via httpx multipart. No FastAPI lifespan, no YOLO — the router
only needs `app.state.uploads_dir` and `app.state.max_upload_size_mb`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes_upload import router as upload_router


@dataclass
class Harness:
    app: FastAPI
    client: AsyncClient
    uploads_dir: Path


@pytest.fixture
async def harness(tmp_path: Path):
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    app = FastAPI()
    app.state.uploads_dir = uploads_dir
    app.state.max_upload_size_mb = 100
    app.include_router(upload_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield Harness(app=app, client=client, uploads_dir=uploads_dir)


def _synthesize_mp4(
    path: Path, *, width: int = 320, height: int = 240, fps: float = 10.0, frames: int = 20
) -> None:
    """Produce a tiny playable MP4 via OpenCV.

    `mp4v` is widely available and what our probe path expects.
    Small dims + a handful of frames keep each test under 50 KB,
    so the 100 MB limit test can use a separately-inflated fixture.
    """
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError("cv2.VideoWriter failed to open — codec missing?")
    try:
        for i in range(frames):
            # Greyscale ramp so each frame differs — helps catch
            # freeze-frame regressions if someone optimizes the
            # processing loop incorrectly.
            frame = np.full((height, width, 3), i * (255 // max(frames, 1)), dtype=np.uint8)
            writer.write(frame)
    finally:
        writer.release()


# ---- happy path --------------------------------------------------------


@pytest.mark.anyio
async def test_upload_returns_expected_metadata_shape(
    harness: Harness, tmp_path: Path
) -> None:
    src = tmp_path / "clip.mp4"
    _synthesize_mp4(src, width=320, height=240, fps=10.0, frames=20)

    with src.open("rb") as fh:
        resp = await harness.client.post(
            "/upload",
            files={"file": ("clip.mp4", fh, "video/mp4")},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Exact AC shape.
    expected_keys = {
        "video_id",
        "source_fps",
        "duration_sec",
        "width",
        "height",
        "processed_fps",
        "total_frames",
    }
    assert set(body.keys()) == expected_keys

    # Metadata fidelity — cv2 reads back what VideoWriter encoded,
    # within a few-frame tolerance depending on the container's
    # header accuracy.
    assert body["width"] == 320
    assert body["height"] == 240
    assert 9.0 <= body["source_fps"] <= 11.0
    # mp4v sometimes undercounts by 1 frame when the writer doesn't
    # flush a final I-frame. Loose bound covers the edge case.
    assert 18 <= body["total_frames"] <= 21
    assert body["processed_fps"] <= 10.0  # cap honored
    assert body["duration_sec"] > 0


@pytest.mark.anyio
async def test_upload_saves_file_under_uploads_dir(
    harness: Harness, tmp_path: Path
) -> None:
    src = tmp_path / "clip.mp4"
    _synthesize_mp4(src)

    with src.open("rb") as fh:
        resp = await harness.client.post(
            "/upload", files={"file": ("clip.mp4", fh, "video/mp4")}
        )

    video_id = resp.json()["video_id"]
    saved = harness.uploads_dir / f"{video_id}.mp4"
    assert saved.exists()
    assert saved.stat().st_size == src.stat().st_size


@pytest.mark.anyio
async def test_upload_generates_unique_video_ids(
    harness: Harness, tmp_path: Path
) -> None:
    src = tmp_path / "clip.mp4"
    _synthesize_mp4(src)

    video_ids: set[str] = set()
    for _ in range(3):
        with src.open("rb") as fh:
            resp = await harness.client.post(
                "/upload", files={"file": ("clip.mp4", fh, "video/mp4")}
            )
        video_ids.add(resp.json()["video_id"])

    assert len(video_ids) == 3


# ---- rejection paths ---------------------------------------------------


@pytest.mark.anyio
async def test_upload_rejects_oversized_with_413(tmp_path: Path) -> None:
    """Override the size cap down to 1 MB so a few-MB dummy blob trips
    the guard fast, rather than generating 100 MB on every CI run."""
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    app = FastAPI()
    app.state.uploads_dir = uploads_dir
    app.state.max_upload_size_mb = 1
    app.include_router(upload_router)

    # 2 MB of zeros — not a real MP4, but the size check trips
    # BEFORE the probe runs, so content doesn't matter here.
    oversized = b"\x00" * (2 * 1024 * 1024)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/upload", files={"file": ("big.mp4", oversized, "video/mp4")}
        )

    assert resp.status_code == 413
    assert "1 MB" in resp.json()["detail"]
    # Partial file must be cleaned up — no stragglers in uploads_dir.
    assert list(uploads_dir.glob("*.mp4")) == []


@pytest.mark.anyio
async def test_upload_rejects_non_video_with_400(
    harness: Harness
) -> None:
    """A text payload masquerading as an MP4 — cv2 can't open it,
    server returns 400 and deletes the saved file."""
    resp = await harness.client.post(
        "/upload",
        files={"file": ("fake.mp4", b"not actually a video", "video/mp4")},
    )

    assert resp.status_code == 400
    assert "readable video" in resp.json()["detail"].lower() or "video" in resp.json()["detail"].lower()
    # Rejected file is not persisted.
    assert list(harness.uploads_dir.glob("*.mp4")) == []


@pytest.mark.anyio
async def test_upload_requires_file_field(harness: Harness) -> None:
    """FastAPI returns 422 when the multipart `file` field is absent."""
    resp = await harness.client.post("/upload")
    assert resp.status_code == 422
