"""NW-1402 AC: event-frame snapshot persistence.

Tests exercise the real `cv2.imwrite` against a tmp_path frames dir
and a tmp aiosqlite DB. No mocks for the I/O path — imwrite of a
blank 640×480 frame is ~1 ms on disk, well within per-test budget.

Shape of a good test for this service:
- Call `save_if_new(frame, event)` with a synthetic ZoneEvent.
- Assert the file exists at the expected path.
- Assert the DB row's `frame_path` got stamped.
- Assert re-calling for the same `(track_id, event_type)` is a no-op.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from app.db import (
    get_alert_by_id,
    init_db,
    insert_alert,
    open_db,
)
from app.models.schemas import ZoneEvent
from app.services.snapshot_service import SnapshotService, _make_filename


def _fake_frame() -> np.ndarray:
    """640×480 BGR black frame. Real bytes, real imwrite path."""
    return np.zeros((480, 640, 3), dtype=np.uint8)


def _event(
    track_id: int = 1,
    event_type: str = "enter",
    alert_id: str = "ev-alert-id-0000000000000000000",
    timestamp: str = "2026-04-22T10:00:00+00:00",
    object_class: str = "person",
) -> ZoneEvent:
    return ZoneEvent(
        track_id=track_id,
        object_class=object_class,  # type: ignore[arg-type]
        event_type=event_type,  # type: ignore[arg-type]
        timestamp=timestamp,
        alert_id=alert_id,
    )


async def _make_service(tmp_path: Path) -> tuple[SnapshotService, Path]:
    """Fresh DB + frames dir + SnapshotService for one test."""
    db_path = tmp_path / "alerts.sqlite3"
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    db = await open_db(db_path)
    await init_db(db)
    service = SnapshotService(db=db, frames_dir=frames_dir)
    return service, frames_dir


# ---- Filename format ---------------------------------------------------

def test_filename_uses_epoch_ms_track_and_event_type() -> None:
    """`{timestamp_ms}_{track_id}_{event_type}.jpg` per AC. Use a
    timestamp with a known UTC offset so the math is reviewable."""
    event = _event(
        track_id=42,
        event_type="enter",
        timestamp="2026-04-22T10:00:00+00:00",
    )
    # 2026-04-22T10:00:00Z → 1776232800 s → 1776852000000 ms
    expected_ms = 1776852000000
    assert _make_filename(event) == f"{expected_ms}_42_enter.jpg"


def test_filename_preserves_sub_second_resolution() -> None:
    event = _event(
        track_id=1,
        event_type="exit",
        timestamp="2026-04-22T10:00:00.123000+00:00",
    )
    expected_ms = 1776852000123
    assert _make_filename(event) == f"{expected_ms}_1_exit.jpg"


# ---- save_if_new happy path --------------------------------------------

@pytest.mark.anyio
async def test_save_writes_file_and_stamps_frame_path(tmp_path: Path) -> None:
    service, frames_dir = await _make_service(tmp_path)
    try:
        event = _event(alert_id="persisted-1")
        await insert_alert(
            service._db,  # type: ignore[attr-defined]  # intentional test reach
            alert_id="persisted-1",
            timestamp=event.timestamp,
            track_id=event.track_id,
            object_class=event.object_class,
            event_type=event.event_type,
        )

        path = await service.save_if_new(_fake_frame(), event)
        assert path is not None
        assert path.exists()
        assert path.parent == frames_dir

        row = await get_alert_by_id(
            service._db,  # type: ignore[attr-defined]
            alert_id="persisted-1",
        )
        assert row is not None
        assert row["frame_path"] == str(path)
    finally:
        await service._db.close()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_filename_has_expected_shape(tmp_path: Path) -> None:
    """End-to-end: the file actually written matches _make_filename."""
    service, frames_dir = await _make_service(tmp_path)
    try:
        event = _event(
            track_id=7,
            event_type="exit",
            timestamp="2026-04-22T10:00:05+00:00",
            alert_id="shape-check",
        )
        await insert_alert(
            service._db,  # type: ignore[attr-defined]
            alert_id="shape-check",
            timestamp=event.timestamp,
            track_id=event.track_id,
            object_class=event.object_class,
            event_type=event.event_type,
        )
        path = await service.save_if_new(_fake_frame(), event)
        assert path is not None
        assert path.name == f"{1776852005000}_7_exit.jpg"
    finally:
        await service._db.close()  # type: ignore[attr-defined]


# ---- Dedup -------------------------------------------------------------

@pytest.mark.anyio
async def test_duplicate_track_and_event_type_is_skipped(tmp_path: Path) -> None:
    """AC: 'Only the first frame per (track_id, event_type) is saved;
    duplicates skipped.'"""
    service, frames_dir = await _make_service(tmp_path)
    try:
        first = _event(
            alert_id="first",
            timestamp="2026-04-22T10:00:00+00:00",
        )
        second = _event(
            alert_id="second",
            timestamp="2026-04-22T10:00:10+00:00",  # different timestamp
        )
        for e in (first, second):
            await insert_alert(
                service._db,  # type: ignore[attr-defined]
                alert_id=e.alert_id,
                timestamp=e.timestamp,
                track_id=e.track_id,
                object_class=e.object_class,
                event_type=e.event_type,
            )

        path1 = await service.save_if_new(_fake_frame(), first)
        path2 = await service.save_if_new(_fake_frame(), second)

        assert path1 is not None
        assert path2 is None  # deduped
        # Only one file on disk.
        files = list(frames_dir.iterdir())
        assert len(files) == 1
        assert files[0] == path1

        # Second alert's frame_path should be NULL — no file to stamp.
        row = await get_alert_by_id(
            service._db,  # type: ignore[attr-defined]
            alert_id="second",
        )
        assert row is not None
        assert row["frame_path"] is None
    finally:
        await service._db.close()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_concurrent_same_key_dispatch_dedupes(tmp_path: Path) -> None:
    """Two `save_if_new` tasks for the SAME (track_id, event_type),
    dispatched in the same event-loop tick, must not both write.

    Relies on the service adding the dedup key BEFORE the
    `asyncio.to_thread` hop — the second coroutine, scheduled later
    on the same single-threaded loop, sees the key already present
    and bails. Locks in that invariant against future refactors that
    might be tempted to move the `add` past the thread hop.
    """
    import asyncio

    service, frames_dir = await _make_service(tmp_path)
    try:
        e1 = _event(
            alert_id="concurrent-a",
            timestamp="2026-04-22T10:00:00+00:00",
        )
        e2 = _event(
            alert_id="concurrent-b",
            timestamp="2026-04-22T10:00:00.500000+00:00",  # different ms
        )
        for e in (e1, e2):
            await insert_alert(
                service._db,  # type: ignore[attr-defined]
                alert_id=e.alert_id,
                timestamp=e.timestamp,
                track_id=e.track_id,
                object_class=e.object_class,
                event_type=e.event_type,
            )

        results = await asyncio.gather(
            service.save_if_new(_fake_frame(), e1),
            service.save_if_new(_fake_frame(), e2),
        )
        # Exactly one write survived.
        saved = [r for r in results if r is not None]
        deduped = [r for r in results if r is None]
        assert len(saved) == 1
        assert len(deduped) == 1
        assert len(list(frames_dir.iterdir())) == 1
    finally:
        await service._db.close()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_same_track_different_event_type_is_independent(
    tmp_path: Path,
) -> None:
    """Dedup is keyed on (track_id, event_type). A track's enter and
    a track's exit are separate keys and both get saved."""
    service, frames_dir = await _make_service(tmp_path)
    try:
        enter = _event(
            event_type="enter",
            alert_id="enter-1",
            timestamp="2026-04-22T10:00:00+00:00",
        )
        exit_ = _event(
            event_type="exit",
            alert_id="exit-1",
            timestamp="2026-04-22T10:00:05+00:00",
        )
        for e in (enter, exit_):
            await insert_alert(
                service._db,  # type: ignore[attr-defined]
                alert_id=e.alert_id,
                timestamp=e.timestamp,
                track_id=e.track_id,
                object_class=e.object_class,
                event_type=e.event_type,
            )

        p1 = await service.save_if_new(_fake_frame(), enter)
        p2 = await service.save_if_new(_fake_frame(), exit_)
        assert p1 is not None
        assert p2 is not None
        assert p1 != p2
        assert len(list(frames_dir.iterdir())) == 2
    finally:
        await service._db.close()  # type: ignore[attr-defined]


# ---- Reset -------------------------------------------------------------

@pytest.mark.anyio
async def test_reset_clears_dedup_cache(tmp_path: Path) -> None:
    """After reset, a second save for the SAME (track, event_type)
    fires again. Matches zone-change semantics in the WS handler."""
    service, frames_dir = await _make_service(tmp_path)
    try:
        e1 = _event(
            alert_id="before",
            timestamp="2026-04-22T10:00:00+00:00",
        )
        e2 = _event(
            alert_id="after",
            timestamp="2026-04-22T10:00:10+00:00",
        )
        for e in (e1, e2):
            await insert_alert(
                service._db,  # type: ignore[attr-defined]
                alert_id=e.alert_id,
                timestamp=e.timestamp,
                track_id=e.track_id,
                object_class=e.object_class,
                event_type=e.event_type,
            )

        await service.save_if_new(_fake_frame(), e1)
        service.reset()
        path_after = await service.save_if_new(_fake_frame(), e2)
        assert path_after is not None
        assert len(list(frames_dir.iterdir())) == 2
    finally:
        await service._db.close()  # type: ignore[attr-defined]


# ---- Failure paths -----------------------------------------------------

@pytest.mark.anyio
async def test_imwrite_failure_leaves_frame_path_null(tmp_path: Path) -> None:
    """If cv2.imwrite returns False (disk full, bad path, etc.) the
    DB row's frame_path must NOT be stamped. The dedup cache entry
    is also dropped so a retry could succeed."""
    service, frames_dir = await _make_service(tmp_path)
    try:
        event = _event(alert_id="write-will-fail")
        await insert_alert(
            service._db,  # type: ignore[attr-defined]
            alert_id="write-will-fail",
            timestamp=event.timestamp,
            track_id=event.track_id,
            object_class=event.object_class,
            event_type=event.event_type,
        )

        with patch(
            "app.services.snapshot_service.cv2.imwrite", return_value=False
        ):
            path = await service.save_if_new(_fake_frame(), event)

        assert path is None
        # No file on disk.
        assert list(frames_dir.iterdir()) == []
        # DB row's frame_path untouched.
        row = await get_alert_by_id(
            service._db,  # type: ignore[attr-defined]
            alert_id="write-will-fail",
        )
        assert row is not None
        assert row["frame_path"] is None
        # Dedup cache was discarded — a retry can succeed.
        path_retry = await service.save_if_new(_fake_frame(), event)
        assert path_retry is not None
    finally:
        await service._db.close()  # type: ignore[attr-defined]
