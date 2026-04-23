"""NW-1403 AC: REST endpoints for alerts + frames.

Tests run against a minimal FastAPI app built per test with a tmp
aiosqlite DB and tmp frames dir — no YOLO lifespan load. httpx
`AsyncClient` + `ASGITransport` drives requests without needing a
live uvicorn process. An async pytest fixture owns setup + teardown
so each test body is just the assertions.

Covers:
- `GET /alerts` default limit + pagination
- `GET /alerts/{alert_id}` hit + 404 miss
- `frame_path` transformed to basename in responses
- `GET /frames/{filename}` serves a real JPEG
- `/frames/..path-traversal` is blocked by Starlette's StaticFiles
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient

from app.api.routes_alerts import router as alerts_router
from app.db import init_db, insert_alert, open_db


@dataclass
class Harness:
    """Bundle the test fixture hands to each case.

    Exposed as a dataclass so tests can do `h.client.get(...)` /
    `h.frames_dir / "x.jpg"` without unpacking three names each time.
    """

    app: FastAPI
    client: AsyncClient
    frames_dir: Path


@pytest.fixture
async def harness(tmp_path: Path):
    """Build a minimal test app + AsyncClient + tmp frames dir.

    Setup:
      - opens a fresh aiosqlite DB under `tmp_path/alerts.sqlite3`
      - applies NW-1401 DDL
      - mounts the alerts router + a /frames StaticFiles handle
      - wraps the app in ASGITransport so no uvicorn is needed

    Teardown:
      - closes the httpx client (via async-with)
      - closes the aiosqlite connection
    """
    db_path = tmp_path / "alerts.sqlite3"
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    db = await open_db(db_path)
    await init_db(db)

    app = FastAPI()
    app.state.db = db
    app.include_router(alerts_router)
    app.mount("/frames", StaticFiles(directory=frames_dir), name="frames")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            yield Harness(app=app, client=client, frames_dir=frames_dir)
        finally:
            await db.close()


async def _seed(
    app: FastAPI,
    *,
    count: int,
    object_class: str = "person",
    event_type: str = "enter",
    frame_dir: Path | None = None,
) -> list[str]:
    """Insert N alerts with monotonically-increasing timestamps.

    Returns the list of alert_ids in insertion order. When
    `frame_dir` is provided, writes a dummy file per alert and stamps
    the frame_path on the row so response-shape tests can assert the
    basename transform.
    """
    db = app.state.db
    alert_ids: list[str] = []
    for i in range(count):
        alert_id = uuid.uuid4().hex
        alert_ids.append(alert_id)
        frame_path: str | None = None
        if frame_dir is not None:
            # absolute path — mirrors what NW-1402's cv2.imwrite produces
            filename = f"1776920000{i:03d}_{i+1}_{event_type}.jpg"
            p = frame_dir / filename
            p.write_bytes(b"\xff\xd8\xff")  # minimal JPEG SOI marker, not valid but fine for tests
            frame_path = str(p)
        await insert_alert(
            db,
            alert_id=alert_id,
            timestamp=f"2026-04-22T10:00:{i:02d}+00:00",
            track_id=i + 1,
            object_class=object_class,
            event_type=event_type,
            frame_path=frame_path,
        )
    return alert_ids


# ---- GET /alerts -------------------------------------------------------

@pytest.mark.anyio
async def test_list_alerts_default_returns_newest_first(harness: Harness) -> None:
    await _seed(harness.app, count=3)
    resp = await harness.client.get("/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    # Newest first: insertion order reversed.
    timestamps = [row["timestamp"] for row in body]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.anyio
async def test_list_alerts_pagination(harness: Harness) -> None:
    await _seed(harness.app, count=5)
    page_one = (await harness.client.get("/alerts?limit=2&offset=0")).json()
    page_two = (await harness.client.get("/alerts?limit=2&offset=2")).json()
    page_three = (await harness.client.get("/alerts?limit=2&offset=4")).json()

    assert [r["track_id"] for r in page_one] == [5, 4]
    assert [r["track_id"] for r in page_two] == [3, 2]
    assert [r["track_id"] for r in page_three] == [1]


@pytest.mark.anyio
async def test_list_alerts_rejects_bad_limit(harness: Harness) -> None:
    """FastAPI's Query(..., ge, le) returns 422 on out-of-range."""
    assert (await harness.client.get("/alerts?limit=0")).status_code == 422
    assert (await harness.client.get("/alerts?limit=501")).status_code == 422
    assert (await harness.client.get("/alerts?offset=-1")).status_code == 422


@pytest.mark.anyio
async def test_list_alerts_empty_db_returns_empty_array(harness: Harness) -> None:
    resp = await harness.client.get("/alerts")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_frame_path_is_basenamed_in_response(harness: Harness) -> None:
    """DB stores absolute path; API returns just the filename so the
    client can fetch via /frames/{filename}."""
    await _seed(harness.app, count=1, frame_dir=harness.frames_dir)
    row = (await harness.client.get("/alerts")).json()[0]
    # Should NOT be an absolute path.
    assert "/" not in row["frame_path"]
    assert row["frame_path"].endswith(".jpg")


@pytest.mark.anyio
async def test_null_frame_path_stays_null(harness: Harness) -> None:
    await _seed(harness.app, count=1)  # no frame_dir → frame_path NULL
    row = (await harness.client.get("/alerts")).json()[0]
    assert row["frame_path"] is None


# ---- GET /alerts/{alert_id} --------------------------------------------

@pytest.mark.anyio
async def test_get_alert_hit(harness: Harness) -> None:
    [alert_id] = await _seed(harness.app, count=1, frame_dir=harness.frames_dir)
    resp = await harness.client.get(f"/alerts/{alert_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["alert_id"] == alert_id
    assert body["track_id"] == 1
    # basename transform honored on the detail route too
    assert "/" not in body["frame_path"]


@pytest.mark.anyio
async def test_get_alert_404_on_missing(harness: Harness) -> None:
    resp = await harness.client.get("/alerts/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "alert not found"


# ---- GET /frames/{filename} --------------------------------------------

@pytest.mark.anyio
async def test_frames_serves_jpeg(harness: Harness) -> None:
    """Round-trip via StaticFiles: write a real JPEG with cv2,
    request it via the REST API, compare bytes."""
    import cv2

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    filename = "test_frame.jpg"
    cv2.imwrite(str(harness.frames_dir / filename), frame)

    resp = await harness.client.get(f"/frames/{filename}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert len(resp.content) == (harness.frames_dir / filename).stat().st_size


@pytest.mark.anyio
async def test_frames_404_on_missing(harness: Harness) -> None:
    resp = await harness.client.get("/frames/does-not-exist.jpg")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_frames_blocks_path_traversal(harness: Harness, tmp_path: Path) -> None:
    """Starlette's StaticFiles normalizes the request path and
    refuses anything that escapes the mounted directory. We spot-
    check a couple of the classic traversal attempts."""
    # Drop a file OUTSIDE the frames dir that a traversal would hit.
    secret = tmp_path / "secret.txt"
    secret.write_text("should-never-be-served")

    for attack in (
        "/frames/../secret.txt",
        "/frames/..%2Fsecret.txt",
        "/frames/%2e%2e/secret.txt",
    ):
        resp = await harness.client.get(attack)
        assert resp.status_code in (404, 400), (
            f"path traversal `{attack}` returned {resp.status_code}"
        )
        # And the secret bytes MUST NOT be in the body.
        assert b"should-never-be-served" not in resp.content
