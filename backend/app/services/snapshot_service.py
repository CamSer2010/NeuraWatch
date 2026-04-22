"""Event-frame snapshot persistence (NW-1402).

One `SnapshotService` per WebSocket connection. When AlertService
emits a `ZoneEvent`, the handler calls `save_if_new(frame, event)`:

1. Dedup by `(track_id, event_type)` — AC says only the first frame
   per (track, kind) is kept. The demo-shaped rationale: ByteTrack
   track IDs persist across ~3 s of occlusion, so oscillation-level
   re-entries are the same subject in the same visual context. If
   the operator redraws the zone (a deliberate "new question") we
   reset the dedup cache so the new window can capture fresh frames.

2. `cv2.imwrite` happens on a thread via `asyncio.to_thread` so the
   WS handler's receive loop doesn't stall on disk I/O — the AC
   calls this out explicitly ("never blocks inference").

3. On success, stamp the resulting path onto the alerts row via
   `update_frame_path` (NW-1401 helper). If imwrite fails or the
   row was reset-wiped mid-write, the helper logs and moves on.

Filename format: `{timestamp_ms}_{track_id}_{event_type}.jpg` per AC.
Timestamp is parsed from the `ZoneEvent.timestamp` ISO string so the
filename is stable across the wire push, the DB insert, and the
saved-frame lookup in NW-1404.

Notes:
- Saves the RAW frame (BGR ndarray). NW-1404 re-draws the bbox on
  top in amber per design-specs §5 Alert Detail Drawer. Keeping the
  backend snapshot unannotated means the UI owns the visual — and
  we don't have to decide which bbox to burn in when multiple
  detections share a frame.
- `frames_dir` creation is NOT this service's job — `app/main.py`
  already creates it at startup per NW-1401/1402 AC.
- Dedup cache is unbounded. Demo-bounded; cleared on reset.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import aiosqlite
import cv2
import numpy as np

from ..db import update_frame_path
from ..models.schemas import ZoneEvent

logger = logging.getLogger(__name__)


class SnapshotService:
    """Per-connection snapshot writer + DB stamp.

    Not thread-safe; the WS handler serializes calls via the event
    loop.
    """

    def __init__(self, db: aiosqlite.Connection, frames_dir: Path) -> None:
        self._db = db
        self._frames_dir = frames_dir
        self._saved_keys: set[tuple[int, str]] = set()

    async def save_if_new(
        self, frame: np.ndarray, event: ZoneEvent
    ) -> Path | None:
        """Save a JPEG snapshot for `event` if we haven't already saved
        one for this `(track_id, event_type)` pair.

        Returns the resolved path on success, `None` when deduped or
        when imwrite failed. The WS handler calls this fire-and-forget
        style — the return value is mostly for tests.
        """
        key = (event.track_id, event.event_type)
        if key in self._saved_keys:
            return None
        # Record the key BEFORE the thread hop — if two snapshots for
        # the same (track_id, event_type) somehow fire on consecutive
        # frames, the second one bails without queueing up a duplicate
        # imwrite. Cheap insurance against concurrency surprises.
        self._saved_keys.add(key)

        filename = _make_filename(event)
        path = self._frames_dir / filename

        # cv2.imwrite returns bool. Run on a thread so ~30ms of JPEG
        # encoding + disk write doesn't stall the event loop.
        success = await asyncio.to_thread(cv2.imwrite, str(path), frame)
        if not success:
            logger.warning(
                "SnapshotService: cv2.imwrite failed for %s (alert_id=%s)",
                path,
                event.alert_id,
            )
            # Drop the cache entry so a retry path could still fire.
            # In practice the handler doesn't retry, but leaving a
            # dead key in the dedup set would mask a second try.
            self._saved_keys.discard(key)
            return None

        await update_frame_path(
            self._db, alert_id=event.alert_id, frame_path=str(path)
        )
        return path

    def reset(self) -> None:
        """Clear the dedup cache.

        Called alongside `AlertService.reset_state()` on zone changes
        and on `/session/reset` (NW-1405). The next matching event
        after a reset will produce a fresh snapshot even if we've
        already saved one for the same `(track_id, event_type)` in
        the prior window.
        """
        if self._saved_keys:
            logger.debug(
                "SnapshotService: reset dedup cache (dropped %d keys)",
                len(self._saved_keys),
            )
        self._saved_keys.clear()


def _make_filename(event: ZoneEvent) -> str:
    """`{timestamp_ms}_{track_id}_{event_type}.jpg`.

    Parses the event's ISO 8601 timestamp into epoch-ms so the
    filename is sortable chronologically, matches what you'd see in
    `ls -l storage/frames/`, and survives timezone offsets cleanly.
    """
    dt = datetime.fromisoformat(event.timestamp)
    timestamp_ms = int(dt.timestamp() * 1000)
    return f"{timestamp_ms}_{event.track_id}_{event.event_type}.jpg"
