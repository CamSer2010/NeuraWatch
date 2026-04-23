"""WebSocket endpoint for live frame processing (NW-1203 + NW-1202).

Protocol (mirrors `frontend/design-specs/README.md` §6):

  Client → Server:
    Text   {"type":"frame_meta","seq":<int>,"mode":"webcam"|"upload"}
    Binary JPEG frame bytes.
    Text   {"type":"zone_update","points":[[x,y],...],"zone_version":<int>}
    Text   {"type":"zone_clear","zone_version":<int>}
    Text   {"type":"process_upload","video_id":"<hex>"}   (NW-1202)

  Server → Client:
    Text   {
      "type":"detection_result",
      "seq": <int>,               # echoes the inbound frame_meta seq;
                                  # in upload mode, mirrors frame_idx
      "mode": "webcam" | "upload",
      "detections":[
        {"class":"person","bbox":[x1,y1,x2,y2],"confidence":0.9,"track_id":1}
      ],
      "events": [                 # NW-1303; one entry per boundary crossing
        {"track_id":1,"object_class":"person","event_type":"enter",
         "timestamp":"2026-04-22T18:33:07.123456+00:00",
         "alert_id":"a3f1c2..."}
      ],
      "zone_version": 0,
      "stats": {"fps":12.3, "inference_ms":45.2, "roundtrip_ms":45.2}
      # NW-1202 upload mode only:
      # "pts_ms":    <int>        # source-video timestamp in ms
      # "frame_idx": <int>        # 0-based position in the source video
    }

    Text   {"type":"frame_dropped","seq":<int>}           (webcam only)

    Text   {"type":"processing_complete","total_frames":<int>,
             "alerts_created":<int>}                      (NW-1202)

Close codes:
  1000  Normal closure
  1011  Internal error
  4409  Session conflict (another WS already owns the tracker)

NW-1202 Architecture (intentional deviation from AC phrasing —
PO-directed 2026-04-23):
  - Client uploads file via `POST /upload`, gets back `{video_id,...}`
  - Client plays the file locally via `URL.createObjectURL(file)` —
    no server-side static serving
  - Client sends `process_upload{video_id}` over this WS
  - Server opens `uploads_dir/{video_id}.mp4` with cv2, iterates frames
    exhaustively (no latest-wins — every frame matters for upload),
    runs inference inline via `inference_service.process_frame` off
    the event loop (`asyncio.to_thread`), emits detection_result with
    `pts_ms` + `frame_idx` per frame
  - Client buffers predictions keyed by pts_ms and matches them to
    `<video>.currentTime` on its rAF tick (smooth playback with
    overlay sync, no video seeking)
  - Server emits `processing_complete` sentinel on EOF

Processing-task lifecycle:
  - Starting a second `process_upload` while one is in flight
    cancels the first
  - Client disconnect cancels the task from the `finally` block
  - Tasks never outlive the WS — `release_session` waits for
    cancellation to propagate before returning
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
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
class ProcessUploadMsg:
    """NW-1202: kick off server-side video processing for a prior upload."""

    video_id: str


@dataclass
class IgnoredMsg:
    reason: str


ClientMsg = (
    FrameMetaMsg | ZoneUpdateMsg | ZoneClearMsg | ProcessUploadMsg | IgnoredMsg
)


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

    if msg_type == "process_upload":
        # NW-1202: video_id is the uuid4 hex returned by POST /upload.
        # Guard against path-traversal attempts here (reject anything
        # non-hex) — the processor trusts this field for filesystem
        # reads and the FE has no reason to ever send anything else.
        video_id = parsed.get("video_id", "")
        if (
            not isinstance(video_id, str)
            or not video_id
            or not all(c in "0123456789abcdef" for c in video_id)
        ):
            return IgnoredMsg("invalid or missing video_id on process_upload")
        return ProcessUploadMsg(video_id=video_id)

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
    *,
    pts_ms: int | None = None,
    frame_idx: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    # NW-1202 upload fields land only on upload-mode frames; webcam
    # payloads keep the tighter shape so the wire diff is minimal
    # for consumers that never encounter upload mode.
    if pts_ms is not None:
        payload["pts_ms"] = pts_ms
    if frame_idx is not None:
        payload["frame_idx"] = frame_idx
    return payload


def _frame_dropped(seq: int) -> dict[str, Any]:
    return {"type": "frame_dropped", "seq": seq}


def _processing_complete(total_frames: int, alerts_created: int) -> dict[str, Any]:
    return {
        "type": "processing_complete",
        "total_frames": total_frames,
        "alerts_created": alerts_created,
    }


# ---- Upload processing (NW-1202) --------------------------------------

async def _process_upload(
    websocket: WebSocket,
    video_path: Path,
    inference_service: Any,
    zone_service: ZoneService,
    alert_service: AlertService,
    snapshot_service: SnapshotService,
    db: Any,
    pending_snapshots: set[asyncio.Task[Any]],
) -> None:
    """Read the uploaded video end-to-end, emit detection_result per
    frame, emit processing_complete on EOF.

    Runs as an asyncio task so the WS receive loop stays responsive
    to zone_update / zone_clear / session interruption while
    processing is in progress. cv2 calls are threaded to keep the
    event loop clean.

    Cancellation: raising CancelledError mid-loop is expected — the
    finally drops the cv2 handle cleanly. The caller (either a new
    process_upload arriving or the WS finally block) owns the
    decision to cancel.
    """
    cap = await asyncio.to_thread(cv2.VideoCapture, str(video_path))
    try:
        if not cap.isOpened():
            logger.warning(
                "process_upload: cv2 could not open %s; aborting", video_path
            )
            return

        # Hoist out of the hot loop — FPS is container-level metadata
        # that doesn't change frame-to-frame. cv2.CAP_PROP_FPS is FFI-
        # synchronous, so running it per-frame is a small but needless
        # cost on a 30s clip.
        source_fps = await asyncio.to_thread(cap.get, cv2.CAP_PROP_FPS)
        if source_fps is None or source_fps <= 0:
            source_fps = 0.0

        frame_idx = 0
        alerts_created = 0
        fps_ema = 0.0
        last_tick: float | None = None

        while True:
            ok, frame = await asyncio.to_thread(cap.read)
            if not ok or frame is None:
                break

            # Back-compute pts from frame_idx / source_fps rather than
            # reading CAP_PROP_POS_MSEC (which points to the NEXT
            # frame after a read, not the one we just got). Fallback
            # to a 10fps guess if the container reported no FPS.
            pts_ms = (
                int(round(frame_idx * 1000.0 / source_fps))
                if source_fps > 0
                else frame_idx * 100
            )

            t0 = time.perf_counter()
            detections = await asyncio.to_thread(
                inference_service.process_frame, frame
            )
            roundtrip_ms = (time.perf_counter() - t0) * 1000.0

            # Processing FPS EMA — not per-frame FPS but per-second
            # throughput. Useful for demo narration ("processed at 8
            # FPS") and for the FE's StatusBadge.
            now = time.perf_counter()
            if last_tick is not None:
                dt = now - last_tick
                if dt > 0:
                    instant = 1.0 / dt
                    fps_ema = (
                        instant
                        if fps_ema == 0.0
                        else _FPS_EMA_ALPHA * instant
                        + (1.0 - _FPS_EMA_ALPHA) * fps_ema
                    )
            last_tick = now

            in_zone_flags = zone_service.evaluate(detections)
            events = alert_service.process_frame(detections, in_zone_flags)
            alerts_created += len(events)

            try:
                await websocket.send_json(
                    _detection_result(
                        # `seq` field mirrors frame_idx in upload mode
                        # so clients that rely on monotonic seq (the
                        # NW-1203 stale-drop guard) keep working.
                        frame_idx,
                        "upload",
                        detections,
                        fps_ema,
                        roundtrip_ms,
                        zone_service.zone_version,
                        events,
                        pts_ms=pts_ms,
                        frame_idx=frame_idx,
                    )
                )
            except WebSocketDisconnect:
                # Normal mid-clip disconnect (operator closed the
                # tab, hit Reset Demo, switched source). Swallow so
                # the outer handler's `logger.exception` doesn't fire
                # on a routine exit.
                logger.info("process_upload: client disconnected mid-clip")
                return

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
                    logger.exception(
                        "process_upload: insert_alert failed for alert_id=%s",
                        event.alert_id,
                    )
                    continue
                task = asyncio.create_task(
                    snapshot_service.save_if_new(frame, event)
                )
                pending_snapshots.add(task)
                task.add_done_callback(pending_snapshots.discard)

            frame_idx += 1

        # Reached EOF cleanly — let the FE flip to idle + show the
        # `upload-complete` banner.
        try:
            await websocket.send_json(
                _processing_complete(
                    total_frames=frame_idx, alerts_created=alerts_created
                )
            )
        except WebSocketDisconnect:
            logger.info(
                "process_upload: client disconnected before completion ack"
            )
            return
        logger.info(
            "process_upload: done video=%s frames=%d alerts=%d",
            video_path.name,
            frame_idx,
            alerts_created,
        )
    finally:
        await asyncio.to_thread(cap.release)


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

    # NW-1202: the server-side upload processing task. At most one
    # per connection — a second process_upload cancels the first.
    # Held as a handle so the finally block can cancel-and-await on
    # disconnect, which prevents orphan cv2 handles from outliving
    # the WS.
    process_task: asyncio.Task[None] | None = None

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
                elif isinstance(parsed, ProcessUploadMsg):
                    # NW-1202: kick off (or restart) the processing
                    # task. A second process_upload mid-flight cancels
                    # the first — matches the "latest-wins" spirit of
                    # the webcam path without the size-1 queue.
                    if process_task is not None and not process_task.done():
                        process_task.cancel()
                        try:
                            await process_task
                        except (asyncio.CancelledError, Exception):
                            # CancelledError is the expected path;
                            # unexpected exceptions from the prior
                            # task are already logged inside it.
                            pass
                    uploads_dir: Path = websocket.app.state.uploads_dir
                    video_path = uploads_dir / f"{parsed.video_id}.mp4"
                    if not video_path.exists():
                        logger.warning(
                            "WS: process_upload for unknown video_id=%s",
                            parsed.video_id,
                        )
                        continue
                    # Tracker state from the previous clip (or webcam
                    # session) must NOT bleed into this one — track IDs
                    # would keep incrementing off the old high-water
                    # mark, confusing the FE dedup + the alerts list.
                    inference_service.reset_tracker()
                    alert_service.reset_state()
                    snapshot_service.reset()
                    process_task = asyncio.create_task(
                        _process_upload(
                            websocket=websocket,
                            video_path=video_path,
                            inference_service=inference_service,
                            zone_service=zone_service,
                            alert_service=alert_service,
                            snapshot_service=snapshot_service,
                            db=db,
                            pending_snapshots=pending_snapshots,
                        )
                    )
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
        # NW-1202: cancel the upload processing task FIRST so its
        # in-flight cv2.to_thread reads don't race with session
        # teardown. Awaiting the cancelled task returns once the
        # cancellation has actually propagated through to_thread.
        if process_task is not None and not process_task.done():
            process_task.cancel()
            try:
                await process_task
            except (asyncio.CancelledError, Exception):
                # Cancellation is expected; any other exception is
                # already logged inside _process_upload's loop.
                pass
        # Drain in-flight snapshot tasks before releasing the session.
        # `return_exceptions=True` keeps one broken task from blocking
        # the rest and ensures the session release always runs.
        if pending_snapshots:
            await asyncio.gather(*pending_snapshots, return_exceptions=True)
        inference_service.release_session(session_id)
