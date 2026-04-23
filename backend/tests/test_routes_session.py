"""NW-1405 AC: POST /session/reset clears alerts, frames, tracker state.

Tests run against a minimal FastAPI app built per test with a tmp
aiosqlite DB, a tmp frames dir, and a stub inference service.
No YOLO lifespan load — the real tracker reset path is exercised
in test_inference_service.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes_session import router as session_router
from app.db import init_db, insert_alert, list_recent_alerts, open_db


class StubInferenceService:
    """Drop-in replacement for InferenceService.

    Only needs `reset_tracker()` — the only method the endpoint
    calls. Counts invocations so tests can assert the handler
    actually hit it.
    """

    def __init__(self) -> None:
        self.reset_calls: int = 0

    def reset_tracker(self) -> None:
        self.reset_calls += 1


@dataclass
class Harness:
    app: FastAPI
    client: AsyncClient
    frames_dir: Path
    inference: StubInferenceService
    alert_ids: list[str] = field(default_factory=list)


@pytest.fixture
async def harness(tmp_path: Path):
    db_path = tmp_path / "alerts.sqlite3"
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    db = await open_db(db_path)
    await init_db(db)

    inference = StubInferenceService()

    app = FastAPI()
    app.state.db = db
    app.state.frames_dir = frames_dir
    app.state.inference_service = inference
    app.include_router(session_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            yield Harness(
                app=app,
                client=client,
                frames_dir=frames_dir,
                inference=inference,
            )
        finally:
            await db.close()


async def _seed_with_frames(h: Harness, count: int) -> list[str]:
    """Insert N alerts, each with a matching JPEG file on disk."""
    ids: list[str] = []
    for i in range(count):
        alert_id = uuid.uuid4().hex
        filename = f"1776920000{i:03d}_{i+1}_enter.jpg"
        p = h.frames_dir / filename
        p.write_bytes(b"\xff\xd8\xff")
        await insert_alert(
            h.app.state.db,
            alert_id=alert_id,
            timestamp=f"2026-04-22T10:00:{i:02d}+00:00",
            track_id=i + 1,
            object_class="person",
            event_type="enter",
            frame_path=str(p),
        )
        ids.append(alert_id)
    return ids


@pytest.mark.anyio
async def test_reset_clears_alerts_frames_and_tracker(harness: Harness) -> None:
    await _seed_with_frames(harness, count=3)
    # Precondition sanity check — setup did land.
    assert len(await list_recent_alerts(harness.app.state.db, limit=50)) == 3
    assert len(list(harness.frames_dir.glob("*.jpg"))) == 3

    resp = await harness.client.post("/session/reset")

    assert resp.status_code == 200
    body = resp.json()
    assert body["alerts_deleted"] == 3
    assert body["frames_deleted"] == 3
    assert body["frame_errors"] == 0
    assert body["tracker_reset"] is True

    # Postcondition: DB + dir empty, tracker hit exactly once.
    assert await list_recent_alerts(harness.app.state.db, limit=50) == []
    assert list(harness.frames_dir.glob("*.jpg")) == []
    assert harness.inference.reset_calls == 1


@pytest.mark.anyio
async def test_reset_is_idempotent(harness: Harness) -> None:
    """Calling reset on an already-empty state returns zeroes, not 500."""
    resp = await harness.client.post("/session/reset")
    assert resp.status_code == 200
    assert resp.json() == {
        "alerts_deleted": 0,
        "frames_deleted": 0,
        "frame_errors": 0,
        "tracker_reset": True,
    }
    assert harness.inference.reset_calls == 1


@pytest.mark.anyio
async def test_reset_preserves_non_jpg_files(harness: Harness) -> None:
    """An operator who parked a note or a different extension in
    `storage/frames/` should not have it nuked. Scope is JPEGs only."""
    (harness.frames_dir / "keep.txt").write_text("leave me alone")
    (harness.frames_dir / "also_keep.png").write_bytes(b"\x89PNG")
    await _seed_with_frames(harness, count=1)

    resp = await harness.client.post("/session/reset")

    assert resp.status_code == 200
    assert resp.json()["frames_deleted"] == 1
    assert (harness.frames_dir / "keep.txt").exists()
    assert (harness.frames_dir / "also_keep.png").exists()


@pytest.mark.anyio
async def test_reset_survives_missing_inference_service(tmp_path: Path) -> None:
    """If the app is started with no inference_service on state (e.g.
    a degraded dev mode), the endpoint must still clear DB + frames
    rather than 500. The response surfaces tracker_reset=False so the
    client can spot it."""
    db_path = tmp_path / "alerts.sqlite3"
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    db = await open_db(db_path)
    await init_db(db)

    app = FastAPI()
    app.state.db = db
    app.state.frames_dir = frames_dir
    # Deliberately: no app.state.inference_service.
    app.include_router(session_router)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post("/session/reset")
        assert resp.status_code == 200
        body = resp.json()
        assert body["tracker_reset"] is False
    finally:
        await db.close()


@pytest.mark.anyio
async def test_reset_survives_missing_frames_dir(tmp_path: Path) -> None:
    """If `frames_dir` disappeared between lifespan mkdir and reset
    (rare — manual `rm -rf`, flaky mount), the endpoint returns 200
    with `frames_deleted=0` rather than 500. The guard is a branch
    in the handler; this test locks that contract."""
    db_path = tmp_path / "alerts.sqlite3"
    frames_dir = tmp_path / "frames"
    # Deliberately do NOT create frames_dir.

    db = await open_db(db_path)
    await init_db(db)

    app = FastAPI()
    app.state.db = db
    app.state.frames_dir = frames_dir
    app.state.inference_service = StubInferenceService()
    app.include_router(session_router)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post("/session/reset")
        assert resp.status_code == 200
        body = resp.json()
        assert body["frames_deleted"] == 0
        assert body["frame_errors"] == 0
    finally:
        await db.close()


@pytest.mark.anyio
async def test_reset_autoincrement_rolls_back(harness: Harness) -> None:
    """Post-reset, the next inserted alert gets id=1 again.
    clear_alerts is documented to reset sqlite_sequence; this test
    locks that contract end-to-end through the HTTP surface."""
    await _seed_with_frames(harness, count=5)
    await harness.client.post("/session/reset")

    await _seed_with_frames(harness, count=1)
    rows = await list_recent_alerts(harness.app.state.db, limit=1)
    assert rows[0]["id"] == 1
