"""YOLOv8n model loader + detection-and-tracking driver.

Owns the single long-lived model instance. Loaded once at FastAPI
startup via app/main.py lifespan and shared across requests via
`app.state.inference_service`.

Scope progression:
  NW-1101 — load + basic predict + verbose=False.
  NW-1102 — class filter + normalization + structured Detection output.
  NW-1103 — swap predict() for model.track() with ByteTrack persistence.
            Fills Detection.track_id. Session guard + reset_tracker().
  NW-1104 — this file — process_frame() produces wire-ready
            WireDetections with normalized 0-1 bboxes and swallows
            inference errors. FrameProcessor (sibling module) owns
            the worker thread + size-1 queue + latest-wins semantics.

ByteTrack defaults (Ultralytics bundled bytetrack.yaml):
  track_high_thresh: 0.25   — first-association confidence floor
  track_low_thresh:  0.10   — second-association floor (for lost tracks)
  new_track_thresh:  0.25   — confidence needed to open a new track
  track_buffer:      30     — frames a lost track is kept for re-id.
                              At 10 FPS this is 3s — comfortably covers
                              the NW-1103 AC's 0.5s occlusion (5 frames).
  match_thresh:      0.8    — IoU threshold for association
  fuse_score:        True   — combine detection+track score for matching
"""
from __future__ import annotations

import hashlib
import logging
import urllib.request
from pathlib import Path

import numpy as np
from ultralytics import YOLO

from ..models.schemas import Detection, ObjectClass, WireDetection

logger = logging.getLogger(__name__)

# Pinned to the Ultralytics v8.4.0 asset release. The library version
# (`ultralytics==8.4.40` in requirements.txt) is intentionally one
# patch series ahead — Ultralytics keeps the 8.4.x patch line asset-
# compatible, and the library bump pulls fixes without changing weights.
# Bump both together if the asset release itself moves.
_WEIGHTS_URL = (
    "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8n.pt"
)
_WEIGHTS_SHA256 = "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36"
_DOWNLOAD_TIMEOUT_SEC = 60
_SHA_CHUNK = 1 << 16

# COCO class ID -> NeuraWatch category.
#   0  person        -> person
#   1  bicycle       -> bicycle
#   2  car           -> vehicle
#   3  motorcycle    -> vehicle
#   5  bus           -> vehicle
#   7  truck         -> vehicle
# All other COCO classes are filtered out at inference time via the
# Ultralytics `classes=` argument, reducing NMS work and wire volume.
_CLASS_MAP: dict[int, ObjectClass] = {
    0: "person",
    1: "bicycle",
    2: "vehicle",
    3: "vehicle",
    5: "vehicle",
    7: "vehicle",
}
_TARGET_CLASSES: list[int] = list(_CLASS_MAP.keys())

_TRACKER_CONFIG = "bytetrack.yaml"


class InferenceService:
    """One model, one process.

    Not thread-safe: ByteTrack state lives in `model.predictor.trackers`,
    so interleaving frames from two sources corrupts IDs. The
    single-session guard (claim_session/release_session) is the
    application-level half of that invariant; NW-1203's WS handler
    is expected to honor it.
    """

    def __init__(
        self,
        weights_path: Path,
        imgsz: int,
        conf_threshold: float,
    ) -> None:
        self.weights_path = weights_path
        self.imgsz = imgsz
        self.conf_threshold = conf_threshold
        self._model: YOLO | None = None
        # Single-active-session guard per NW-1103 AC. The claim is
        # advisory — NW-1203's WS handler calls claim/release; concurrent
        # claims from a different session_id are rejected to keep
        # ByteTrack state coherent.
        self._active_session: str | None = None
        # Track-call kwargs cached once; Ultralytics accepts them via **.
        self._track_kwargs: dict = {
            "persist": True,
            "tracker": _TRACKER_CONFIG,
            "imgsz": imgsz,
            "classes": _TARGET_CLASSES,
            "conf": conf_threshold,
            "verbose": False,
        }

    # -------- lifecycle ---------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Ensure correct weights on disk, load the model, warm tracker+NMS."""
        self.weights_path.parent.mkdir(parents=True, exist_ok=True)

        # Self-heal a corrupt partial download from a previous start.
        if self.weights_path.exists() and not self._verify_sha256():
            print(
                f"Weights at {self.weights_path} fail SHA256 check; re-downloading."
            )
            self.weights_path.unlink()

        if not self.weights_path.exists():
            self._download_weights()
            if not self._verify_sha256():
                self.weights_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Downloaded weights SHA256 mismatch; expected {_WEIGHTS_SHA256}"
                )

        self._model = YOLO(str(self.weights_path))
        print(f"YOLOv8n loaded on device={self._model.device}, imgsz={self.imgsz}")

        # Warmup: runs the same track() path predict() will use, priming
        # forward + NMS + ByteTrack init. Reset the tracker afterwards so
        # real frame #1 doesn't inherit any ID state from the dummy frame.
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        self._model.track(dummy, **self._track_kwargs)
        self.reset_tracker()

    # -------- inference ---------------------------------------------------

    def predict(self, frame: np.ndarray) -> list[Detection]:
        """Run detection + tracking on a single HWC BGR frame.

        Despite the name `predict`, this internally calls `model.track()` —
        it's the tracking path, not plain detection. Name retained for
        NW-1101/1102 call-site compatibility.

        Returns `Detection`s with **pixel** bbox in the input-frame
        coordinate space. Used by the smoke script (verify_inference.py)
        and by `process_frame()` which normalizes for the wire.
        """
        if self._model is None:
            raise RuntimeError(
                "InferenceService.load() must complete before predict()"
            )
        results = self._model.track(frame, **self._track_kwargs)
        return _parse_results(results)

    def process_frame(self, frame: np.ndarray) -> list[WireDetection]:
        """Production API: inference + normalization + error isolation.

        This is the method NW-1203's WS handler calls (via
        `FrameProcessor`). Bboxes are normalized 0-1 against
        `frame.shape[:2]` so the WS payload matches ratified
        decision #5 without further transformation on the wire.

        Any exception inside inference or parsing is caught and logged;
        the caller gets an empty list back instead of a propagated
        error. One bad frame cannot crash the WS loop.
        """
        try:
            if self._model is None:
                return []
            if frame.ndim != 3 or frame.shape[2] != 3:
                logger.warning(
                    "process_frame: unexpected frame shape %s; dropping",
                    frame.shape,
                )
                return []

            h, w = frame.shape[:2]
            if w <= 0 or h <= 0:
                return []

            pixel_dets = self.predict(frame)
            return [
                WireDetection(
                    object_class=d.object_class,
                    bbox=(
                        d.bbox[0] / w,
                        d.bbox[1] / h,
                        d.bbox[2] / w,
                        d.bbox[3] / h,
                    ),
                    confidence=d.confidence,
                    track_id=d.track_id,
                )
                for d in pixel_dets
            ]
        except Exception:
            logger.exception("process_frame failed; returning empty list")
            return []

    # -------- session guard (NW-1103 AC, NW-1203 consumer) ----------------

    @property
    def active_session(self) -> str | None:
        return self._active_session

    def claim_session(self, session_id: str) -> bool:
        """Become the active session. Idempotent for the same session_id.

        Returns False iff a different session currently holds the claim.
        Callers (NW-1203 WS handler) should refuse the connection in that
        case to keep ByteTrack state coherent. The refusal is also logged
        here so the backend has a trace even if the caller forgets.
        """
        if (
            self._active_session is not None
            and self._active_session != session_id
        ):
            print(
                f"InferenceService: claim refused for {session_id!r}; "
                f"active session is {self._active_session!r}"
            )
            return False
        self._active_session = session_id
        return True

    def release_session(self, session_id: str) -> None:
        """Release the claim if `session_id` matches; also resets ByteTrack.

        Mismatched releases are no-ops so a late disconnect from an already-
        evicted session can't clear an active one.
        """
        if self._active_session == session_id:
            self._active_session = None
            self.reset_tracker()

    def reset_tracker(self) -> None:
        """Clear ByteTrack state (used by NW-1405 POST /session/reset too).

        Ultralytics stores per-task tracker instances on
        `model.predictor.trackers`. Calling `.reset()` on each wipes
        assigned IDs so the next frame starts from a clean slate.
        """
        if self._model is None:
            return
        predictor = getattr(self._model, "predictor", None)
        trackers = getattr(predictor, "trackers", None) if predictor else None
        if not trackers:
            return
        for tracker in trackers:
            reset = getattr(tracker, "reset", None)
            if callable(reset):
                reset()

    # -------- internals ---------------------------------------------------

    def _download_weights(self) -> None:
        print(f"Downloading YOLOv8n weights -> {self.weights_path}")
        with urllib.request.urlopen(
            _WEIGHTS_URL, timeout=_DOWNLOAD_TIMEOUT_SEC
        ) as response:
            self.weights_path.write_bytes(response.read())
        size_kb = self.weights_path.stat().st_size // 1024
        print(f"  downloaded {size_kb} KB")

    def _verify_sha256(self) -> bool:
        h = hashlib.sha256()
        with self.weights_path.open("rb") as f:
            for chunk in iter(lambda: f.read(_SHA_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest() == _WEIGHTS_SHA256


def _parse_results(results) -> list[Detection]:
    """Convert raw Ultralytics Results into our Detection list.

    Ultralytics always returns one Results object per input image; we
    pass a single frame so index 0 is the whole batch. Tensor → NumPy
    is batched per-attribute rather than per-box to avoid 3 GPU/CPU
    roundtrips per detection on the 10 FPS hot path.
    """
    if not results:
        return []
    r = results[0]
    if r.boxes is None or len(r.boxes) == 0:
        return []

    # Pull everything off the tensor in one go (.cpu() is a no-op on
    # CPU tensors; .numpy() gives us fast Python iteration).
    cls_ids = r.boxes.cls.cpu().numpy().astype(int)
    confs = r.boxes.conf.cpu().numpy()
    xyxy = r.boxes.xyxy.cpu().numpy()

    # Track IDs may be absent on the very first frame before ByteTrack
    # assigns them, or if tracking was skipped. Guard against None.
    if r.boxes.id is not None:
        track_ids: list[int | None] = (
            r.boxes.id.cpu().numpy().astype(int).tolist()
        )
    else:
        track_ids = [None] * len(cls_ids)

    out: list[Detection] = []
    for cls_id, conf, box, tid in zip(cls_ids, confs, xyxy, track_ids):
        object_class = _CLASS_MAP.get(int(cls_id))
        if object_class is None:
            # Defensive — should not happen when classes= is passed
            # at inference time, but protects against a mismatch between
            # _TARGET_CLASSES and _CLASS_MAP.
            continue
        out.append(
            Detection(
                object_class=object_class,
                bbox=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                confidence=float(conf),
                track_id=int(tid) if tid is not None else None,
            )
        )
    return out
