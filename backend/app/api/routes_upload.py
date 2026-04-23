"""Video upload endpoint (NW-1202).

Receives a multipart MP4, caps it at the per-settings size limit,
saves it to `settings.uploads_dir/{video_id}.mp4`, probes metadata
via OpenCV, and returns the shape the frontend's `VideoSourcePanel`
expects.

Architectural note — intentional Option-1 asymmetry:
- The backend keeps its own copy of the file *only* for inference.
  The frontend plays back from `URL.createObjectURL(file)` on the
  client's original in-memory `File`, so there's no `/uploads/{id}`
  StaticFiles mount and no round-trip re-fetch. This halves the
  wire cost and sidesteps CORS/mixed-content on the ngrok path.
- Consequence: if the client reloads mid-demo, the video reference
  is gone client-side even though the server still has the file.
  Deliberate demo-scale tradeoff — session/reset cleans up the
  server's copy when the operator wipes state between Loom takes.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import cv2
from fastapi import APIRouter, HTTPException, Request, UploadFile

logger = logging.getLogger(__name__)

router = APIRouter(tags=["upload"])

# Processed-fps ceiling. NW-1004's benchmark locked CPU inference at
# ~10 FPS; running the upload loop faster would only add queue wait
# with no visible benefit. The value is a *hint* in the response — the
# WS processing loop runs as fast as inference allows, which is
# typically already ≤ 10 FPS.
_PROCESSED_FPS_CAP = 10.0

# Chunk size for streaming the upload to disk. 1 MiB keeps per-chunk
# overhead negligible without holding the whole file in memory.
_CHUNK_BYTES = 1 * 1024 * 1024


@router.post("/upload")
async def upload_video(request: Request, file: UploadFile) -> dict[str, Any]:
    """Save an uploaded MP4 and return its metadata.

    Enforcement pattern:
    - Size is verified as bytes arrive, not up-front. Content-Length
      is advisory — clients can lie or stream without it — so we
      accumulate and abort as soon as the cap is exceeded, then
      delete the partial file.
    - OpenCV opens the file to probe FPS / duration / WxH / total
      frames. A file that cv2 can't open isn't a usable demo clip —
      we reject with 400 rather than hand the WS handler a file it
      can't process.

    Returns the exact shape the AC specifies:
        {video_id, source_fps, duration_sec, width, height,
         processed_fps, total_frames}
    """
    uploads_dir: Path = request.app.state.uploads_dir
    max_upload_mb: int = request.app.state.max_upload_size_mb
    max_bytes = max_upload_mb * 1024 * 1024

    video_id = uuid.uuid4().hex
    dest = uploads_dir / f"{video_id}.mp4"

    # Stream the upload to disk with incremental size check. Don't
    # trust UploadFile.size / Content-Length — either can be absent
    # or wrong on proxied requests.
    bytes_written = 0
    try:
        with dest.open("wb") as sink:
            while True:
                chunk = await file.read(_CHUNK_BYTES)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"Upload exceeds {max_upload_mb} MB limit"
                        ),
                    )
                sink.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        logger.exception("upload: write failed; deleted partial %s", dest)
        raise HTTPException(status_code=500, detail="upload write failed")

    # Probe with OpenCV off the event loop — VideoCapture open +
    # metadata reads hit the disk synchronously.
    try:
        metadata = await asyncio.to_thread(_probe_video, dest)
    except _VideoProbeError as err:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(err)) from err

    logger.info(
        "upload: saved video_id=%s bytes=%d %dx%d fps=%.2f frames=%d",
        video_id,
        bytes_written,
        metadata["width"],
        metadata["height"],
        metadata["source_fps"],
        metadata["total_frames"],
    )

    return {
        "video_id": video_id,
        "source_fps": metadata["source_fps"],
        "duration_sec": metadata["duration_sec"],
        "width": metadata["width"],
        "height": metadata["height"],
        "processed_fps": min(
            metadata["source_fps"] if metadata["source_fps"] > 0 else _PROCESSED_FPS_CAP,
            _PROCESSED_FPS_CAP,
        ),
        "total_frames": metadata["total_frames"],
    }


class _VideoProbeError(Exception):
    """Raised when OpenCV can't open or understand the uploaded file."""


def _probe_video(path: Path) -> dict[str, Any]:
    """Extract FPS / dims / frame count via cv2.VideoCapture.

    Runs in a thread pool — callers must wrap with `to_thread`.
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise _VideoProbeError("File is not a readable video")
    try:
        source_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        # Some MP4 containers (variable-frame-rate or h264 without
        # accurate headers) return 0 for total_frames. Duration is
        # derived; a 0/0 here means we can't gate the processing loop
        # on progress, but we still allow the upload through.
        duration_sec = (
            total_frames / source_fps if source_fps > 0 and total_frames > 0 else 0.0
        )
        if width <= 0 or height <= 0:
            raise _VideoProbeError("Video has invalid dimensions")
        return {
            "source_fps": round(source_fps, 3),
            "width": width,
            "height": height,
            "total_frames": total_frames,
            "duration_sec": round(duration_sec, 3),
        }
    finally:
        cap.release()
