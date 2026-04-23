"""Session reset endpoint (NW-1405).

Single handler: `POST /session/reset`. Wipes everything a demo
operator would want gone between takes — DB alerts, saved JPEG
frames, and the ByteTrack ID state in the model singleton — so the
next "Start webcam" click looks like a cold boot.

Scope boundaries:
- We do NOT touch per-WS-connection service state
  (`ZoneService`, `AlertService`, `SnapshotService`). Those are
  recreated per connection; the design-specs §Interactions flow has
  the client disconnect the WS immediately after the `session/reset`
  action, so the next connection gets fresh instances naturally.
- We do NOT clear the in-memory weights — only the tracker IDs. The
  YOLO model stays loaded so the next detection is sub-second.

Frame dir wipe only removes `*.jpg` (the extension NW-1402 writes).
An operator who parked unrelated files alongside the snapshots
keeps them; the endpoint is deliberately scoped to what NeuraWatch
itself produced.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

from ..db import clear_alerts

logger = logging.getLogger(__name__)

router = APIRouter(tags=["session"])


@router.post("/session/reset")
async def reset_session(request: Request) -> dict[str, Any]:
    """Clear alerts DB rows, saved frame snapshots, and tracker IDs.

    Returns counts so the frontend (and anyone curling the endpoint
    for smoke-checking) can confirm the reset actually did work.
    Never 500s on a partial failure — snapshot unlinks run best-
    effort and the delete count reflects what actually landed.

    **Assumes the caller disconnected the WS first.** Design-specs
    §Interactions ratifies the flow: "Reset Demo shows a confirm
    dialog, then dispatches `session/reset` — clears alerts, clears
    zone, disconnects WS, returns to idle." The frontend's
    `session/reset` reducer flips `cameraActive` false which tears
    down the socket; this handler does NOT coordinate with a live
    WS writer and racing it would leave orphan rows/JPEGs.
    """
    db = request.app.state.db
    inference_service = getattr(request.app.state, "inference_service", None)
    frames_dir = request.app.state.frames_dir

    alerts_deleted = await clear_alerts(db)

    frames_deleted = 0
    frame_errors = 0
    if frames_dir.exists():
        for jpg in frames_dir.glob("*.jpg"):
            try:
                jpg.unlink()
                frames_deleted += 1
            except OSError:
                logger.exception(
                    "session/reset: failed to unlink %s; continuing", jpg
                )
                frame_errors += 1

    tracker_reset = False
    if inference_service is not None:
        try:
            inference_service.reset_tracker()
            tracker_reset = True
        except Exception:
            logger.exception(
                "session/reset: inference_service.reset_tracker() raised"
            )

    # Raise log level when unlinks failed so operator eyeballs catch
    # it; response body still carries the count for programmatic UIs.
    log = logger.warning if frame_errors > 0 else logger.info
    log(
        "session/reset: alerts=%d frames=%d errors=%d tracker=%s",
        alerts_deleted,
        frames_deleted,
        frame_errors,
        tracker_reset,
    )

    return {
        "alerts_deleted": alerts_deleted,
        "frames_deleted": frames_deleted,
        "frame_errors": frame_errors,
        "tracker_reset": tracker_reset,
    }
