"""YOLOv8n model loader and inference driver.

Owns the single long-lived model instance. Loaded once at FastAPI
startup via app/main.py lifespan and shared across requests via
`app.state.inference_service`.

Scope progression:
  NW-1101 — load + basic predict + verbose=False.
  NW-1102 — this file — class filter + normalization (COCO → person/
            vehicle/bicycle) + structured Detection output.
  NW-1103 — swap predict() for model.track() with ByteTrack persistence.
  NW-1104 — wraps this service behind a unified frame-processing API
            keyed by seq/frame_id.
"""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

import numpy as np
from ultralytics import YOLO

from ..models.schemas import Detection, ObjectClass

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


class InferenceService:
    """One model, one process. Not thread-safe; the WS handler serializes frames."""

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

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Ensure correct weights on disk, load the model, warm it up."""
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

        # Warmup with the same filter shape predict() uses — removes the
        # cold-start spike on WS frame #1 and primes the NMS path too.
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        self._model.predict(
            dummy,
            imgsz=self.imgsz,
            classes=_TARGET_CLASSES,
            conf=self.conf_threshold,
            verbose=False,
        )

    def predict(self, frame: np.ndarray) -> list[Detection]:
        """Run detection on a single HWC BGR frame.

        Returns normalized `Detection`s (class -> person/vehicle/bicycle,
        bbox in pixel xyxy over the original frame). Non-target classes
        are filtered at inference time. NW-1103 swaps this for
        `model.track()` and fills Detection.track_id.
        """
        if self._model is None:
            raise RuntimeError(
                "InferenceService.load() must complete before predict()"
            )
        results = self._model.predict(
            frame,
            imgsz=self.imgsz,
            classes=_TARGET_CLASSES,
            conf=self.conf_threshold,
            verbose=False,
        )
        return _parse_results(results)

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

    out: list[Detection] = []
    for cls_id, conf, box in zip(cls_ids, confs, xyxy):
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
            )
        )
    return out
