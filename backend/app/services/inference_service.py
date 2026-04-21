"""YOLOv8n model loader and inference driver.

Owns the single long-lived model instance. Loaded once at FastAPI
startup via app/main.py lifespan and shared across requests via
`app.state.inference_service`.

Scope discipline:
  NW-1101 — this file — load + basic predict + verbose=False.
  NW-1102 — adds class normalization (COCO ID -> person/vehicle/bicycle)
            and class filtering at inference time. `predict()` will
            grow `classes` and `conf` parameters then.
  NW-1103 — swaps predict() for model.track() with ByteTrack persistence.
  NW-1104 — wraps this service behind a unified frame-processing API
            returning a structured Detection list.
"""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
from ultralytics import YOLO

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


class InferenceService:
    """One model, one process. Not thread-safe; the WS handler serializes frames."""

    def __init__(self, weights_path: Path, imgsz: int) -> None:
        self.weights_path = weights_path
        self.imgsz = imgsz
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

        # Warmup removes the ~200ms cold-start spike on the first real
        # WS frame. Matches the 640x480 capture size that NW-1201 will use.
        dummy = np.zeros((480, 640, 3), dtype=np.uint8)
        self._model.predict(dummy, imgsz=self.imgsz, verbose=False)

    def predict(self, frame: np.ndarray) -> Any:
        """Run detection on a single HWC BGR frame.

        Returns raw Ultralytics `Results`. NW-1102 will add `classes` and
        `conf` filtering; NW-1103 swaps this for `model.track()`.
        """
        if self._model is None:
            raise RuntimeError(
                "InferenceService.load() must complete before predict()"
            )
        return self._model.predict(
            frame,
            imgsz=self.imgsz,
            verbose=False,
        )

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
