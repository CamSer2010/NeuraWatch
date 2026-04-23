"""REST endpoints for persisted alerts (NW-1403).

Two read-only handlers back the NW-1404 alerts side-panel:

    GET /alerts?limit=50&offset=0   — newest-first paginated list
    GET /alerts/{alert_id}          — single alert detail; 404 if missing

Implementation notes:

- **`{alert_id}` path param is the uuid4 hex, NOT the DB int `id`.**
  AC says `GET /alerts/{id}`; we interpret `id` as the public-facing
  identifier (the one stamped on WS events + used by the client for
  dedup). DB `id` is an implementation detail that changes across
  /session/reset (NW-1405's `clear_alerts` resets AUTOINCREMENT), so
  keying the REST endpoint off it would break client-side caches.

- **`frame_path` is basenamed here**, not in `db.py`. DB keeps the
  absolute path that NW-1402's `cv2.imwrite` produced so /session/
  reset can `pathlib.unlink()` it directly; API returns just the
  filename so the client can fetch via `GET /frames/{filename}`.

- **DB connection comes from `request.app.state.db`.** Mirrors the
  WS handler pattern — no Depends injection because the connection
  lifecycle is app-lifespan-scoped, not per-request.

- **`limit` is clamped at 500** to keep a single fetch from holding
  the DB connection indefinitely on a very full table. 50 default
  matches NW-1403 AC; 500 is 10× that, more than NW-1404 will ever
  render before pagination kicks in.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, Query, Request

from ..db import get_alert_by_id, list_recent_alerts
from ..models.schemas import Alert

router = APIRouter(tags=["alerts"])


@router.get("/alerts", response_model=list[Alert])
async def list_alerts(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """Return the last N alerts, newest first.

    Paginates via `limit` + `offset`. NW-1404 fetches with the
    default `limit=50` on mount and dedups subsequent WS pushes
    against what this endpoint returned.
    """
    db: aiosqlite.Connection = request.app.state.db
    rows = await list_recent_alerts(db, limit=limit, offset=offset)
    return [_present(row) for row in rows]


@router.get("/alerts/{alert_id}", response_model=Alert)
async def get_alert(request: Request, alert_id: str) -> dict[str, Any]:
    """Lookup a single alert by its public `alert_id` (uuid4 hex).

    404 when the row doesn't exist (never persisted, or wiped by
    /session/reset).
    """
    db: aiosqlite.Connection = request.app.state.db
    row = await get_alert_by_id(db, alert_id=alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="alert not found")
    return _present(row)


def _present(row: dict[str, Any]) -> dict[str, Any]:
    """Map a DB row into the API shape.

    Only transformation today: `frame_path` → basename so the client
    hits `GET /frames/{basename}` directly without stripping the
    absolute path itself. Keeping the transform in one function
    makes any future shape evolution (e.g. adding `frame_url`) a
    single-site edit.
    """
    frame_path = row["frame_path"]
    return {
        "id": row["id"],
        "alert_id": row["alert_id"],
        "timestamp": row["timestamp"],
        "track_id": row["track_id"],
        "object_class": row["object_class"],
        "event_type": row["event_type"],
        "frame_path": Path(frame_path).name if frame_path else None,
    }
