"""NW-1401 AC: 7-column alerts schema + async CRUD round-trip.

Fresh aiosqlite connection per test via `tmp_path`. The `anyio` plugin
drives the async tests; `anyio_backend` is pinned to `asyncio` in
`conftest.py`.
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from app.db import (
    clear_alerts,
    get_alert_by_id,
    init_db,
    insert_alert,
    list_recent_alerts,
    open_db,
    update_frame_path,
)


async def _fresh_db(tmp_path: Path) -> aiosqlite.Connection:
    """Open + init a one-shot DB under a test tmp dir."""
    db_path = tmp_path / "alerts.sqlite3"
    conn = await open_db(db_path)
    await init_db(conn)
    return conn


# ---- Schema ------------------------------------------------------------

@pytest.mark.anyio
async def test_init_db_creates_alerts_table(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
    finally:
        await conn.close()


@pytest.mark.anyio
async def test_schema_has_seven_expected_columns(tmp_path: Path) -> None:
    """Spec enumerates 6 fields; we ship 7 (adds alert_id). PO-approved
    deviation. Pin the column set so future schema drift is loud."""
    conn = await _fresh_db(tmp_path)
    try:
        async with conn.execute("PRAGMA table_info(alerts)") as cursor:
            rows = await cursor.fetchall()
        columns = {row["name"] for row in rows}
    finally:
        await conn.close()
    assert columns == {
        "id",
        "alert_id",
        "timestamp",
        "track_id",
        "object_class",
        "event_type",
        "frame_path",
    }


@pytest.mark.anyio
async def test_timestamp_desc_index_is_present(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        async with conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='alerts'"
        ) as cursor:
            names = {row["name"] for row in await cursor.fetchall()}
    finally:
        await conn.close()
    assert "idx_alerts_timestamp_desc" in names


@pytest.mark.anyio
async def test_init_db_is_idempotent(tmp_path: Path) -> None:
    """Warm restarts must not raise — IF NOT EXISTS on both statements."""
    conn = await _fresh_db(tmp_path)
    try:
        await init_db(conn)  # second call must be a no-op
        await init_db(conn)  # and a third
    finally:
        await conn.close()


# ---- Insert + read round-trip ------------------------------------------

@pytest.mark.anyio
async def test_insert_returns_autoincrement_id(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        a = await insert_alert(
            conn,
            alert_id="a" * 32,
            timestamp="2026-04-22T10:00:00+00:00",
            track_id=1,
            object_class="person",
            event_type="enter",
        )
        b = await insert_alert(
            conn,
            alert_id="b" * 32,
            timestamp="2026-04-22T10:00:01+00:00",
            track_id=2,
            object_class="vehicle",
            event_type="enter",
        )
        assert a == 1
        assert b == 2
    finally:
        await conn.close()


@pytest.mark.anyio
async def test_list_recent_alerts_orders_desc(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        # Intentionally insert oldest first to prove ORDER BY flips order.
        stamps = [
            "2026-04-22T10:00:00+00:00",
            "2026-04-22T10:00:05+00:00",
            "2026-04-22T10:00:10+00:00",
        ]
        for i, ts in enumerate(stamps):
            await insert_alert(
                conn,
                alert_id=f"{i}" * 32,
                timestamp=ts,
                track_id=i,
                object_class="person",
                event_type="enter",
            )
        rows = await list_recent_alerts(conn, limit=20)
        assert [r["timestamp"] for r in rows] == list(reversed(stamps))
    finally:
        await conn.close()


@pytest.mark.anyio
async def test_list_respects_limit_and_offset(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        for i in range(5):
            await insert_alert(
                conn,
                alert_id=f"{i:032d}",
                timestamp=f"2026-04-22T10:00:{i:02d}+00:00",
                track_id=i,
                object_class="person",
                event_type="enter",
            )
        page1 = await list_recent_alerts(conn, limit=2, offset=0)
        page2 = await list_recent_alerts(conn, limit=2, offset=2)
        assert [r["track_id"] for r in page1] == [4, 3]
        assert [r["track_id"] for r in page2] == [2, 1]
    finally:
        await conn.close()


@pytest.mark.anyio
async def test_list_on_empty_db_returns_empty(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        rows = await list_recent_alerts(conn, limit=20)
        assert rows == []
    finally:
        await conn.close()


# ---- Uniqueness --------------------------------------------------------

@pytest.mark.anyio
async def test_alert_id_unique_constraint(tmp_path: Path) -> None:
    """Re-inserting the same alert_id must raise. Callers dedupe
    upstream; collisions signal an upstream bug, not a race."""
    conn = await _fresh_db(tmp_path)
    try:
        await insert_alert(
            conn,
            alert_id="shared-id-32char-padding-xxxxxxx",
            timestamp="2026-04-22T10:00:00+00:00",
            track_id=1,
            object_class="person",
            event_type="enter",
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await insert_alert(
                conn,
                alert_id="shared-id-32char-padding-xxxxxxx",
                timestamp="2026-04-22T10:00:01+00:00",
                track_id=1,
                object_class="person",
                event_type="exit",
            )
    finally:
        await conn.close()


# ---- get_alert_by_id ---------------------------------------------------

@pytest.mark.anyio
async def test_get_alert_by_id_roundtrip(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        await insert_alert(
            conn,
            alert_id="lookup-me",
            timestamp="2026-04-22T10:00:00+00:00",
            track_id=7,
            object_class="bicycle",
            event_type="enter",
            frame_path="storage/frames/x.jpg",
        )
        row = await get_alert_by_id(conn, alert_id="lookup-me")
        assert row is not None
        assert row["track_id"] == 7
        assert row["object_class"] == "bicycle"
        assert row["frame_path"] == "storage/frames/x.jpg"
    finally:
        await conn.close()


@pytest.mark.anyio
async def test_get_alert_by_id_missing_returns_none(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        row = await get_alert_by_id(conn, alert_id="nope")
        assert row is None
    finally:
        await conn.close()


# ---- update_frame_path -------------------------------------------------

@pytest.mark.anyio
async def test_update_frame_path_applies(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        await insert_alert(
            conn,
            alert_id="frame-target",
            timestamp="2026-04-22T10:00:00+00:00",
            track_id=1,
            object_class="person",
            event_type="enter",
        )
        await update_frame_path(
            conn, alert_id="frame-target", frame_path="/absolute/x.jpg"
        )
        row = await get_alert_by_id(conn, alert_id="frame-target")
        assert row is not None
        assert row["frame_path"] == "/absolute/x.jpg"
    finally:
        await conn.close()


@pytest.mark.anyio
async def test_update_frame_path_on_missing_is_noop(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        # Should not raise.
        await update_frame_path(
            conn, alert_id="does-not-exist", frame_path="/x.jpg"
        )
    finally:
        await conn.close()


# ---- clear_alerts ------------------------------------------------------

@pytest.mark.anyio
async def test_clear_alerts_deletes_all_and_returns_count(
    tmp_path: Path,
) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        for i in range(3):
            await insert_alert(
                conn,
                alert_id=f"{i:032d}",
                timestamp=f"2026-04-22T10:00:0{i}+00:00",
                track_id=i,
                object_class="person",
                event_type="enter",
            )
        deleted = await clear_alerts(conn)
        assert deleted == 3
        rows = await list_recent_alerts(conn)
        assert rows == []
    finally:
        await conn.close()


@pytest.mark.anyio
async def test_clear_alerts_on_empty_db_returns_zero(tmp_path: Path) -> None:
    conn = await _fresh_db(tmp_path)
    try:
        assert await clear_alerts(conn) == 0
    finally:
        await conn.close()


@pytest.mark.anyio
async def test_clear_alerts_resets_autoincrement(tmp_path: Path) -> None:
    """Post-reset, next INSERT should produce id=1 again — not where the
    counter left off. Matters for any UI surfacing the DB id."""
    conn = await _fresh_db(tmp_path)
    try:
        for i in range(3):
            await insert_alert(
                conn,
                alert_id=f"pre-{i:028d}",
                timestamp=f"2026-04-22T10:00:0{i}+00:00",
                track_id=i,
                object_class="person",
                event_type="enter",
            )
        await clear_alerts(conn)
        new_id = await insert_alert(
            conn,
            alert_id="post-reset",
            timestamp="2026-04-22T10:10:00+00:00",
            track_id=1,
            object_class="person",
            event_type="enter",
        )
        assert new_id == 1
    finally:
        await conn.close()
