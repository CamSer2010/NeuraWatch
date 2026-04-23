"""Shared Pydantic schemas.

Populated incrementally:
  NW-1102 — Detection (pixel xyxy, internal).
  NW-1104 — WireDetection (normalized 0-1, external / WS wire form).
  NW-1303 — ZoneEvent (enter/exit transitions).
  NW-1402 — Alert (DB-persisted form).
  NW-1202 — UploadMetadata.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

ObjectClass = Literal["person", "vehicle", "bicycle"]


class Detection(BaseModel):
    """One detected object after class normalization — **internal**.

    Returned by `InferenceService.predict()` and `_parse_results`.
    """

    object_class: ObjectClass
    bbox: tuple[float, float, float, float]
    """Pixel xyxy in the **input-frame** space.

    Ultralytics' `boxes.xyxy` is already mapped back from its internal
    640×640 letterboxed inference canvas to the original input frame
    dimensions, so consumers can draw these coords directly on the
    source frame without re-deriving the letterbox transform.

    `WireDetection` carries the 0-1 normalized form (NW-1104).
    """

    confidence: float
    track_id: Optional[int] = None


class WireDetection(BaseModel):
    """One detected object in the shape the WebSocket wire expects.

    Identical fields to `Detection` except `bbox` is normalized 0-1
    against the processed frame dimensions (NW-1104 AC, ratified
    decision #5). Kept as a separate type so `Detection` (pixel-space)
    and `WireDetection` (normalized) can't silently collapse into each
    other and cause the 30-minute "why don't my bboxes line up" bug.

    Produced by `InferenceService.process_frame()`. Consumed by
    NW-1203's WS handler with no further transformation.
    """

    object_class: ObjectClass = Field(
        description="Normalized category: person | vehicle | bicycle."
    )
    bbox: tuple[float, float, float, float] = Field(
        description=(
            "Normalized xyxy in [0, 1]: (x1, y1, x2, y2) relative to the "
            "processed frame's (width, height). Multiply by the frame size "
            "to recover pixel coords."
        )
    )
    confidence: float = Field(
        description="Detection confidence, 0-1.",
    )
    track_id: Optional[int] = Field(
        default=None,
        description=(
            "ByteTrack ID; None on the first frame before association "
            "or when tracking is disabled."
        ),
    )


class ProcessedFrame(BaseModel):
    """FrameProcessor.submit() return value.

    Carries the original frame's `seq` back to the caller so NW-1203's
    WS handler can build its detection_result envelope without
    maintaining a seq→frame map on the side.
    """

    seq: int = Field(description="Monotonic frame sequence the caller submitted.")
    detections: list[WireDetection] = Field(default_factory=list)


EventType = Literal["enter", "exit"]


class ZoneEvent(BaseModel):
    """One zone boundary transition (NW-1303).

    Emitted by `AlertService.process_frame()` when a tracked object's
    in-zone state changes between consecutive frames. Serialized into
    the `detection_result.events` array and pushed through the existing
    WS connection — no REST polling per AC.

    NW-1401/1402 will persist these to SQLite alongside a snapshot
    frame. The fields here are the minimal shape the DB needs, plus
    `alert_id` so the client can dedup against REST-fetched history
    once NW-1403 lands.
    """

    track_id: int = Field(
        description="ByteTrack ID of the object that crossed the zone."
    )
    object_class: ObjectClass = Field(
        description="Normalized category: person | vehicle | bicycle.",
    )
    event_type: EventType = Field(
        description="'enter' for outside→inside, 'exit' for inside→outside.",
    )
    timestamp: str = Field(
        description="ISO 8601 UTC string stamped at event detection time.",
    )
    alert_id: str = Field(
        description=(
            "Stable per-event identifier (uuid4 hex). Persists across "
            "the WS push and the NW-1403 REST fetch so the client can "
            "dedupe."
        ),
    )


class Alert(BaseModel):
    """One persisted alert row as returned by the NW-1403 REST API.

    Mirrors the 7 columns of the `alerts` table (NW-1401) with one
    transformation: `frame_path` is normalized to JUST the filename
    so the client can fetch the image via `GET /frames/{filename}`.
    The DB still stores the absolute path that `cv2.imwrite` produced
    in NW-1402 — the route handler strips it to the basename before
    returning.

    `frame_path` is nullable. It lands `None` in three cases:
      - The WS emitted the event but NW-1402 skipped the snapshot
        because another event for the same (track_id, event_type)
        already saved one this session.
      - The snapshot write failed (cv2.imwrite returned False).
      - The row pre-dates NW-1402 (unlikely in a fresh deployment).

    NW-1404's alerts panel gracefully shows a placeholder when
    `frame_path` is null.
    """

    id: int = Field(description="DB primary key; ephemeral across /session/reset.")
    alert_id: str = Field(
        description="Stable uuid4 hex assigned by AlertService; client dedup key."
    )
    timestamp: str = Field(
        description="ISO 8601 UTC — same string as the WS event payload."
    )
    track_id: int
    object_class: ObjectClass
    event_type: EventType
    frame_path: Optional[str] = Field(
        default=None,
        description=(
            "Basename of the saved JPEG (e.g. `1776920585551_1_enter.jpg`). "
            "Fetch the image via `GET /frames/{frame_path}`. Null when no "
            "snapshot exists for this alert."
        ),
    )
