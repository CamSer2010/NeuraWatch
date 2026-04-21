#!/usr/bin/env python3
"""NW-1101 smoke test: load YOLOv8n and run inference on a synthetic frame.

Exercises InferenceService standalone (no FastAPI) so it can be re-run
without spinning up the web server. Intentionally light — the full
benchmark lives at backend/scripts/benchmark_fps.py.

Run from repo root:
    backend/.venv/bin/python backend/scripts/verify_inference.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.inference_service import InferenceService  # noqa: E402


def main() -> None:
    settings = get_settings()
    service = InferenceService(
        weights_path=settings.model_weights_dir / "yolov8n.pt",
        imgsz=settings.inference_imgsz,
    )
    print(f"Weights: {service.weights_path}")
    print(f"imgsz:   {service.imgsz}")
    print()

    t0 = time.perf_counter()
    service.load()
    print(f"Load:      {(time.perf_counter() - t0) * 1000:.1f} ms")

    frame = np.random.default_rng(seed=42).integers(
        0, 255, (480, 640, 3), dtype=np.uint8
    )
    t0 = time.perf_counter()
    results = service.predict(frame)
    print(f"Inference: {(time.perf_counter() - t0) * 1000:.1f} ms")

    n_det = len(results[0].boxes) if len(results) > 0 else 0
    print(f"Detections on synthetic frame (unfiltered): {n_det}")


if __name__ == "__main__":
    main()
