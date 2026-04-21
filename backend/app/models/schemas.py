"""Shared Pydantic schemas.

Populated incrementally:
  NW-1102 — Detection (this file, below).
  NW-1303 — ZoneEvent (enter/exit transitions).
  NW-1402 — Alert (DB-persisted form).
  NW-1202 — UploadMetadata.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

ObjectClass = Literal["person", "vehicle", "bicycle"]


class Detection(BaseModel):
    """One detected object after class normalization.

    Returned by `InferenceService.predict()`. NW-1103 populates
    `track_id` via `model.track()`; NW-1203 normalizes `bbox` to
    0-1 on the way out to the WebSocket wire.
    """

    object_class: ObjectClass
    bbox: tuple[float, float, float, float]
    """Pixel xyxy in the **input-frame** space.

    Ultralytics' `boxes.xyxy` is already mapped back from its internal
    640×640 letterboxed inference canvas to the original input frame
    dimensions (e.g. 480×640 for the NW-1201 webcam capture), so
    consumers can draw these coords directly on the source frame
    without re-deriving the letterbox transform.

    NW-1203 normalizes to 0-1 against the 640×480 processed frame
    per ratified decision #5.
    """

    confidence: float
    track_id: Optional[int] = None
