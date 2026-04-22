"""SQLite schema + async CRUD for persisted alerts (NW-1401).

Single `alerts` table, no migrations framework. Applied at app
startup via `init_db()` — `CREATE TABLE IF NOT EXISTS` + `CREATE
INDEX IF NOT EXISTS`, so a cold start finds an empty DB and a warm
start is a no-op.

Schema (7 columns):

    id           INTEGER PK autoincrement
    alert_id     TEXT UNIQUE NOT NULL   -- uuid4 hex from AlertService
    timestamp    TEXT NOT NULL          -- ISO 8601 UTC from AlertService
    track_id     INTEGER NOT NULL
    object_class TEXT NOT NULL          -- 'person' | 'vehicle' | 'bicycle'
    event_type   TEXT NOT NULL          -- 'enter' | 'exit'
    frame_path   TEXT                   -- NULLable; NW-1402 writes after snapshot

    INDEX idx_alerts_timestamp_desc ON alerts(timestamp DESC)

**Deviation from PROJECT_PLAN §Persistence (PO-approved).** The plan
enumerates 6 fields. We carry a 7th — `alert_id` — because NW-1303's
WS event payload already stamps one at emission time, and making it
the DB dedup key is cheaper than the alternatives (REST ↔ WS dedup
via `(track_id, timestamp, event_type)` is fragile on rapid same-
track multi-events; adding post-insert round-trip to stamp the DB
`id` onto the WS push changes the order of observable events and
complicates NW-1404). `alert_id` is load-bearing, not gold-plating.

Access pattern: a single `aiosqlite.Connection` is held on
`app.state.db` for the whole process lifetime (opened in the
lifespan hook). Good enough for a demo where writes are bounded by
detection FPS (<20/s) and readers are the `/alerts` REST handler.
If we ever horizontally scale we'd swap this for a connection pool.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


# Single DDL block; split on ';' so aiosqlite `executescript` can
# fire them atomically. Each statement is `IF NOT EXISTS` so cold
# and warm starts are both no-ops.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id     TEXT UNIQUE NOT NULL,
    timestamp    TEXT NOT NULL,
    track_id     INTEGER NOT NULL,
    object_class TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    frame_path   TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_timestamp_desc
    ON alerts (timestamp DESC);
"""


async def open_db(db_path: Path) -> aiosqlite.Connection:
    """Open an aiosqlite connection with sensible defaults.

    Callers own the connection and must `await conn.close()` when
    done (handled by the FastAPI lifespan hook in production).

    WAL journal mode lets readers and writers coexist without lock
    contention on a single-file demo DB — the cost is a second file
    on disk, which we don't care about.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode = WAL;")
    await conn.execute("PRAGMA foreign_keys = ON;")
    return conn


async def init_db(conn: aiosqlite.Connection) -> None:
    """Apply the DDL idempotently."""
    await conn.executescript(_SCHEMA_SQL)
    await conn.commit()
    logger.info("DB: alerts schema applied at %s", conn)


# ---- CRUD --------------------------------------------------------------


async def insert_alert(
    conn: aiosqlite.Connection,
    *,
    alert_id: str,
    timestamp: str,
    track_id: int,
    object_class: str,
    event_type: str,
    frame_path: str | None = None,
) -> int:
    """Insert one alert row. Returns the DB `id`.

    `alert_id` is UNIQUE; re-inserting the same id raises
    `aiosqlite.IntegrityError`. Callers (NW-1402 snapshot-save) are
    expected to dedupe upstream — duplicate-by-design is an error.
    """
    cursor = await conn.execute(
        """
        INSERT INTO alerts
            (alert_id, timestamp, track_id, object_class, event_type, frame_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (alert_id, timestamp, track_id, object_class, event_type, frame_path),
    )
    await conn.commit()
    row_id = cursor.lastrowid
    await cursor.close()
    if row_id is None:
        # Shouldn't happen on a successful INSERT, but mypy doesn't
        # know that; raise rather than return a garbage int.
        raise RuntimeError("insert_alert: sqlite returned no lastrowid")
    return row_id


async def list_recent_alerts(
    conn: aiosqlite.Connection,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return the last-N alerts, newest first.

    Uses the `idx_alerts_timestamp_desc` index for the ORDER BY.
    `offset` is provided for NW-1403 pagination; default 0.
    """
    async with conn.execute(
        """
        SELECT id, alert_id, timestamp, track_id, object_class, event_type, frame_path
          FROM alerts
         ORDER BY timestamp DESC
         LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ) as cursor:
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_alert_by_id(
    conn: aiosqlite.Connection, *, alert_id: str
) -> dict[str, Any] | None:
    """Lookup by the public `alert_id` (uuid4 hex), not the DB PK.

    Used by NW-1403 `/alerts/{alert_id}` and by NW-1402 to update
    `frame_path` after the async snapshot write completes.
    """
    async with conn.execute(
        """
        SELECT id, alert_id, timestamp, track_id, object_class, event_type, frame_path
          FROM alerts
         WHERE alert_id = ?
        """,
        (alert_id,),
    ) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def update_frame_path(
    conn: aiosqlite.Connection, *, alert_id: str, frame_path: str
) -> None:
    """Stamp the saved frame path onto an existing alert row.

    NW-1402 will call this after `cv2.imwrite` completes on the
    background thread. Silently no-ops if the alert_id doesn't exist
    — the row was deleted by /session/reset or was never persisted.
    A 0-row match is logged at WARNING so NW-1402 debugging isn't
    archaeology when a snapshot stamps a vanished alert.
    """
    cursor = await conn.execute(
        "UPDATE alerts SET frame_path = ? WHERE alert_id = ?",
        (frame_path, alert_id),
    )
    await conn.commit()
    if cursor.rowcount == 0:
        logger.warning(
            "DB: update_frame_path matched 0 rows for alert_id=%s (stamp dropped)",
            alert_id,
        )
    await cursor.close()


# ---- reset (NW-1405 will call) -----------------------------------------


async def clear_alerts(conn: aiosqlite.Connection) -> int:
    """Drop every alert row. Returns the delete count.

    NW-1405 calls this from the `/session/reset` handler alongside
    wiping `storage/frames/`. Landed here (vs its own module) so the
    DELETE and the table definition live side by side.

    Also resets the AUTOINCREMENT counter so the next insert starts
    at `id=1` rather than wherever the pre-reset max left it. Any UI
    surfacing the DB `id` (or a screenshot of the alerts panel)
    behaves the same post-reset as on a fresh boot.
    """
    cursor = await conn.execute("DELETE FROM alerts")
    deleted = cursor.rowcount
    await cursor.close()
    # `sqlite_sequence` is only created by SQLite after the first
    # AUTOINCREMENT insert. Guard on `deleted > 0` — if we actually
    # wiped rows then the table exists; otherwise it may not, and
    # touching it would raise OperationalError on a never-written DB.
    if deleted > 0:
        await conn.execute(
            "DELETE FROM sqlite_sequence WHERE name = 'alerts'"
        )
    await conn.commit()
    return deleted
