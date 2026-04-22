"""WebSocket endpoint for live frame processing (NW-1203).

Protocol (mirrors `frontend/design-specs/README.md` §6):

  Client → Server:
    Text   {"type":"frame_meta","seq":<int>,"mode":"webcam"|"upload"}
    Binary JPEG frame bytes.
    (NW-1301 adds "zone_update"; NW-1405 adds a "reset" signal.)

  Server → Client:
    Text   {
      "type":"detection_result",
      "seq": <int>,               # echoes the inbound frame_meta seq
      "mode": "webcam" | "upload",# echoes the inbound frame_meta mode
      "detections":[
        {"class":"person","bbox":[x1,y1,x2,y2],"confidence":0.9,"track_id":1}
      ],
      "events": [                 # NW-1303; one entry per boundary crossing this frame
        {"track_id":1,"object_class":"person","event_type":"enter",
         "timestamp":"2026-04-22T18:33:07.123456+00:00",
         "alert_id":"a3f1c2..."}
      ],
      "zone_version": 0,          # NW-1301 populates
      "stats": {
        "fps": 12.3,
        "inference_ms": 45.2,     # backend submit -> resolve time
        "roundtrip_ms": 45.2      # alias for demo narration clarity
      }
      # Reserved for NW-1202 upload mode; currently absent:
      # "pts_ms": <int>           # source-video timestamp in ms
    }

    Text   {"type":"frame_dropped","seq":<int>}
      Emitted when a submission was superseded in the size-1 queue
      (FrameProcessor latest-wins). Lets the client clear `inFlight`
      without waiting for its 2s watchdog.

    # Reserved (NW-1202):
    # Text   {"type":"processing_complete","total_frames":<int>}

Close codes:
  1000  Normal closure
  1011  Internal error
  4409  Session conflict (another WS already owns the tracker)

The handler composes three NW-1104 primitives:
  - `app.state.inference_service.claim_session` to enforce one active
    connection (ByteTrack state otherwise interleaves and breaks tracks).
  - `app.state.frame_processor.submit` for size-1 queue + latest-wins
    cancellation.
  - `release_session` runs on **every exit path where claim succeeded**.
    The 4409 refusal path early-returns BEFORE the try/finally so
    `release_session` is correctly NOT called then.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..db import insert_alert
from ..models.schemas import WireDetection, ZoneEvent
from ..services.alert_service import AlertService
from ..services.snapshot_service import SnapshotService
from ..services.zone_service import ZoneService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ws"])

# Moving-average weight for backend FPS. 0.2 = moderate smoothing;
# responsive to real drops, ignores single-frame jitter. The frontend
# may re-EMA its own 500ms tick per spec; that's fine — double-
# smoothing just further stabilizes the value.
_FPS_EMA_ALPHA = 0.2

# Protect the event loop: drop outsized JPEGs (typo, malicious, or a
# FE regression that forgets to downscale). 4 MB is ~2.5× a worst-case
# 1080p JPEG at 90 quality — anything larger is not a NeuraWatch frame.
_MAX_JPEG_BYTES = 4 * 1024 * 1024

_CLOSE_NORMAL = 1000
_CLOSE_INTERNAL_ERROR = 1011
_CLOSE_SESSION_CONFLICT = 4409

_VALID_MODES = ("webcam", "upload")


# ---- Parsed client messages --------------------------------------------

@dataclass
class FrameMetaMsg:
    seq: int
    mode: str  # "webcam" | "upload"


@dataclass
class ZoneUpdateMsg:
    points: list[list[float]]
    zone_version: int


@dataclass
class ZoneClearMsg:
    # NW-1302: clear carries the post-clear zone_version so the
    # server can keep its monotonic echo in sync with the client. A
    # clear without a version would force the server to invent one,
    # which could diverge from whatever the client thinks is current.
    zone_version: int


@dataclass
class IgnoredMsg:
    reason: str


ClientMsg = FrameMetaMsg | ZoneUpdateMsg | ZoneClearMsg | IgnoredMsg


def _parse_text_message(text: str) -> ClientMsg:
    """Parse an inbound text message into a tagged dataclass.

    NW-1301 will extend ZoneUpdateMsg handling; NW-1405 may add a
    ResetMsg. Keep this routing table explicit so each ticket lands
    in one obvious place.
    """
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        return IgnoredMsg("invalid json")

    if not isinstance(parsed, dict):
        return IgnoredMsg("not an object")

    msg_type = parsed.get("type")

    if msg_type == "frame_meta":
        try:
            seq = int(parsed.get("seq", 0))
        except (TypeError, ValueError):
            return IgnoredMsg("invalid seq")
        mode = parsed.get("mode", "webcam")
        if mode not in _VALID_MODES:
            mode = "webcam"
        return FrameMetaMsg(seq=seq, mode=mode)

    if msg_type == "zone_update":
        # NW-1301 consumes this; for NW-1203 we parse + ignore so the
        # shape is tested in the wild before the zone ticket lands.
        points = parsed.get("points", [])
        try:
            zone_version = int(parsed.get("zone_version", 0))
        except (TypeError, ValueError):
            return IgnoredMsg("invalid zone_version")
        if not isinstance(points, list):
            return IgnoredMsg("zone_update points not a list")
        return ZoneUpdateMsg(points=points, zone_version=zone_version)

    if msg_type == "zone_clear":
        try:
            zone_version = int(parsed.get("zone_version", 0))
        except (TypeError, ValueError):
            return IgnoredMsg("invalid zone_version on zone_clear")
        return ZoneClearMsg(zone_version=zone_version)

    return IgnoredMsg(f"unknown type {msg_type!r}")


# ---- Outbound payloads -------------------------------------------------

def _serialize_detection(d: WireDetection) -> dict[str, Any]:
    """Spec wire shape: top-level field is `class`, not `object_class`.

    `class` is a Python reserved word so WireDetection uses
    `object_class`; the rename lives at the JSON boundary only.
    """
    return {
        "class": d.object_class,
        "bbox": list(d.bbox),
        "confidence": d.confidence,
        "track_id": d.track_id,
    }


def _serialize_event(e: ZoneEvent) -> dict[str, Any]:
    """NW-1303 wire shape. Pydantic's `model_dump()` gives the right
    keys already (no field renames), but going through a helper keeps
    the JSON boundary explicit and easy to version later."""
    return {
        "track_id": e.track_id,
        "object_class": e.object_class,
        "event_type": e.event_type,
        "timestamp": e.timestamp,
        "alert_id": e.alert_id,
    }


def _detection_result(
    seq: int,
    mode: str,
    detections: list[WireDetection],
    fps: float,
    roundtrip_ms: float,
    zone_version: int,
    events: list[ZoneEvent],
) -> dict[str, Any]:
    return {
        "type": "detection_result",
        "seq": seq,
        "mode": mode,
        "detections": [_serialize_detection(d) for d in detections],
        "events": [_serialize_event(e) for e in events],  # NW-1303
        "zone_version": zone_version,  # NW-1302: echo ZoneService's view
        "stats": {
            "fps": round(fps, 2),
            # `inference_ms` is the spec-mandated field name. Historical
            # naming — it covers submit -> resolve (queue wait + inference
            # + serialize), not pure model time. `roundtrip_ms` is the
            # honest alias; both shipped so demo narration can pick.
            "inference_ms": round(roundtrip_ms, 2),
            "roundtrip_ms": round(roundtrip_ms, 2),
        },
    }


def _frame_dropped(seq: int) -> dict[str, Any]:
    return {"type": "frame_dropped", "seq": seq}


# ---- WebSocket handler -------------------------------------------------

@router.websocket("/ws/detect")
async def detect_ws(websocket: WebSocket) -> None:
    """Per-connection live-detection WebSocket."""
    await websocket.accept()

    inference_service = websocket.app.state.inference_service
    frame_processor = websocket.app.state.frame_processor
    db = websocket.app.state.db
    settings = get_settings()

    # Claim the tracker before entering the try block. The early-return
    # on refusal BYPASSES the finally, which is intentional — we don't
    # own the session so we must not release it.
    session_id = str(uuid.uuid4())
    if not inference_service.claim_session(session_id):
        logger.warning(
            "WS: refusing connection %s; session is held by %s",
            session_id,
            inference_service.active_session,
        )
        await websocket.close(
            code=_CLOSE_SESSION_CONFLICT, reason="Session conflict"
        )
        return

    pending_meta: FrameMetaMsg | None = None
    fps_ema: float = 0.0
    last_frame_ts: float | None = None
    # One ZoneService per WS connection (NW-1302). Fresh polygon-less
    # state on every reconnect so the next client isn't handed the
    # previous one's zone.
    zone_service = ZoneService()
    # AlertService (NW-1303) keeps per-track in-zone state across
    # frames and emits enter/exit events on transitions. Also per-
    # connection so track IDs from one session don't leak into the
    # next. NW-1304: debounce_frames threaded from settings (env var
    # `DEBOUNCE_FRAMES`, default 2) suppresses boundary jitter.
    alert_service = AlertService(
        debounce_frames=settings.debounce_frames,
    )
    # NW-1402 snapshot writer — dedup by (track_id, event_type), saves
    # JPEG off the event loop via asyncio.to_thread, stamps the path
    # back onto the alerts row.
    snapshot_service = SnapshotService(db=db, frames_dir=settings.frames_dir)
    # Track in-flight snapshot tasks so we can drain them in the
    # finally block. Without this, a WebSocketDisconnect mid-snapshot
    # lets `asyncio` GC-warn about pending tasks being destroyed and
    # occasionally truncates the JPEG on disk.
    pending_snapshots: set[asyncio.Task[Any]] = set()

    try:
        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            text = msg.get("text")
            if text is not None:
                parsed = _parse_text_message(text)
                if isinstance(parsed, FrameMetaMsg):
                    pending_meta = parsed
                elif isinstance(parsed, ZoneUpdateMsg):
                    if zone_service.set_zone(
                        parsed.points, parsed.zone_version
                    ):
                        # Zone geometry changed — forget per-track
                        # history so anyone already inside the new
                        # polygon fires a fresh `enter` on the next
                        # frame. See AlertService.reset_state docstring.
                        # SnapshotService dedup is scoped to the same
                        # window: redrawing the zone means we want
                        # fresh frames for anyone re-entering.
                        alert_service.reset_state()
                        snapshot_service.reset()
                elif isinstance(parsed, ZoneClearMsg):
                    zone_service.clear_zone(parsed.zone_version)
                    alert_service.reset_state()
                    snapshot_service.reset()
                elif isinstance(parsed, IgnoredMsg):
                    logger.debug("WS: ignored text (%s)", parsed.reason)
                continue

            data = msg.get("bytes")
            if data is None:
                continue

            if pending_meta is None:
                logger.debug("WS: binary without frame_meta; dropping")
                continue

            if len(data) > _MAX_JPEG_BYTES:
                logger.warning(
                    "WS: dropping oversized frame (%d bytes) for seq=%d",
                    len(data),
                    pending_meta.seq,
                )
                pending_meta = None
                continue

            meta = pending_meta
            pending_meta = None

            # Decode JPEG -> HWC BGR ndarray (cv2 default is BGR).
            arr = np.frombuffer(data, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is None:
                logger.warning(
                    "WS: JPEG decode failed for seq=%d", meta.seq
                )
                continue

            t0 = time.perf_counter()
            try:
                pf = await frame_processor.submit(frame, meta.seq)
            except asyncio.CancelledError:
                # Latest-wins displaced this frame. Tell the client
                # right away so it can clear `inFlight` without waiting
                # for its 2s watchdog.
                await websocket.send_json(_frame_dropped(meta.seq))
                continue
            roundtrip_ms = (time.perf_counter() - t0) * 1000.0

            # FPS EMA. First measured frame seeds the average; subsequent
            # frames smooth. `last_frame_ts == None` on the very first
            # iteration → stays at 0 until we have a delta.
            now = time.perf_counter()
            if last_frame_ts is not None:
                dt = now - last_frame_ts
                if dt > 0:
                    instant = 1.0 / dt
                    fps_ema = (
                        instant
                        if fps_ema == 0.0
                        else _FPS_EMA_ALPHA * instant
                        + (1.0 - _FPS_EMA_ALPHA) * fps_ema
                    )
            last_frame_ts = now

            # NW-1302: evaluate detections against the current polygon.
            # NW-1303: feed the parallel flags through AlertService
            # to convert steady-state membership into transition events.
            in_zone_flags = zone_service.evaluate(pf.detections)
            events = alert_service.process_frame(pf.detections, in_zone_flags)

            # Push events to the FE first so perceived latency stays
            # at pure inference time — the downstream persistence
            # (insert_alert + snapshot) runs after the send.
            await websocket.send_json(
                _detection_result(
                    pf.seq,
                    meta.mode,
                    pf.detections,
                    fps_ema,
                    roundtrip_ms,
                    zone_service.zone_version,
                    events,
                )
            )

            # NW-1402: persist each event and dispatch a snapshot save.
            # insert_alert is awaited (DB write is fast, typically <1ms);
            # snapshot_service.save_if_new is fire-and-forget via
            # create_task so ~30ms of JPEG encode + disk write doesn't
            # delay the next frame receive. Each task handles its own
            # errors via SnapshotService / update_frame_path logging.
            for event in events:
                try:
                    await insert_alert(
                        db,
                        alert_id=event.alert_id,
                        timestamp=event.timestamp,
                        track_id=event.track_id,
                        object_class=event.object_class,
                        event_type=event.event_type,
                    )
                except Exception:
                    # A failed insert means the row doesn't exist —
                    # don't dispatch the snapshot stamp against a
                    # missing alert_id.
                    logger.exception(
                        "WS: insert_alert failed for alert_id=%s; "
                        "skipping snapshot",
                        event.alert_id,
                    )
                    continue
                # Capture a REFERENCE to the current frame ndarray.
                # `frame` is rebound by `cv2.imdecode` on every iter
                # (fresh ndarray each time) so the background task
                # holds its own buffer even after the loop moves on.
                # If a future change recycles a buffer, this line will
                # need an explicit `frame.copy()` — flagging here so
                # we don't regress silently.
                task = asyncio.create_task(
                    snapshot_service.save_if_new(frame, event)
                )
                pending_snapshots.add(task)
                task.add_done_callback(pending_snapshots.discard)

    except WebSocketDisconnect:
        logger.info("WS: client disconnected (session=%s)", session_id)
    except Exception:
        logger.exception("WS: unexpected error (session=%s)", session_id)
        try:
            await websocket.close(
                code=_CLOSE_INTERNAL_ERROR, reason="Internal error"
            )
        except Exception:
            # Already closed; nothing more to do.
            pass
    finally:
        # Drain in-flight snapshot tasks before releasing the session.
        # `return_exceptions=True` keeps one broken task from blocking
        # the rest and ensures the session release always runs.
        if pending_snapshots:
            await asyncio.gather(*pending_snapshots, return_exceptions=True)
        inference_service.release_session(session_id)
