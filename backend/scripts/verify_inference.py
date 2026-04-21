#!/usr/bin/env python3
"""NW-1101/1102/1103 smoke test: load YOLOv8n and run inference on a frame.

By default, uses a synthetic random frame (useful for exercising the
load + predict path without any external inputs). Pass `--image PATH`
to feed a real JPEG/PNG instead — handy for confirming the full
detection + tracking pipeline before NW-1501's end-to-end soak test.
Pass `--save PATH` to also write an annotated copy of the frame.

Exercises InferenceService standalone (no FastAPI). The sustained FPS
benchmark lives at backend/scripts/benchmark_fps.py.

Run from repo root:
    backend/.venv/bin/python backend/scripts/verify_inference.py
    backend/.venv/bin/python backend/scripts/verify_inference.py --image photo.jpg
    backend/.venv/bin/python backend/scripts/verify_inference.py \\
        --image photo.jpg --save /tmp/annotated.jpg
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models.schemas import Detection  # noqa: E402
from app.services.inference_service import InferenceService  # noqa: E402

# BGR for OpenCV. Anticipates the frontend's class palette.
# TODO(NW-1204): unify this with the canonical frontend palette once
# the UI ticket picks its final colors; drift will show up visually.
_CLASS_COLORS: dict[str, tuple[int, int, int]] = {
    "person": (0, 255, 0),      # green
    "vehicle": (0, 165, 255),   # orange
    "bicycle": (255, 0, 0),     # blue
}


def _load_frame(image_path: Path | None) -> np.ndarray:
    if image_path is None:
        return np.random.default_rng(seed=42).integers(
            0, 255, (480, 640, 3), dtype=np.uint8
        )
    if not image_path.exists():
        raise SystemExit(f"Image not found: {image_path}")
    frame = cv2.imread(str(image_path))
    if frame is None:
        raise SystemExit(
            f"Could not decode image (unsupported format?): {image_path}"
        )
    return frame


def _annotate(
    frame: np.ndarray, detections: list[Detection]
) -> np.ndarray:
    out = frame.copy()
    for d in detections:
        x1, y1, x2, y2 = (int(v) for v in d.bbox)
        color = _CLASS_COLORS.get(d.object_class, (255, 255, 255))
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = f"{d.object_class} {d.confidence:.2f}"
        if d.track_id is not None:
            label += f" #{d.track_id}"
        cv2.putText(
            out,
            label,
            (x1, max(y1 - 6, 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            2,
        )
    return out


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--image",
        type=Path,
        default=None,
        help=(
            "Path to an image file (any format OpenCV's imread accepts). "
            "Defaults to a synthetic random frame."
        ),
    )
    parser.add_argument(
        "--save",
        type=Path,
        default=None,
        help="Optional path to save an annotated copy of the frame.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    settings = get_settings()
    service = InferenceService(
        weights_path=settings.model_weights_dir / "yolov8n.pt",
        imgsz=settings.inference_imgsz,
        conf_threshold=settings.confidence_threshold,
    )
    print(f"Weights:         {service.weights_path}")
    print(f"imgsz:           {service.imgsz}")
    print(f"conf_threshold:  {service.conf_threshold}")
    print(
        "Source:          "
        f"{args.image if args.image else 'synthetic 640x480 random'}"
    )
    print()

    t0 = time.perf_counter()
    service.load()
    print(f"Load:      {(time.perf_counter() - t0) * 1000:.1f} ms")

    frame = _load_frame(args.image)
    if args.image is not None:
        h, w = frame.shape[:2]
        print(f"Frame:     {w}x{h}")

    t0 = time.perf_counter()
    detections = service.predict(frame)
    print(f"Inference: {(time.perf_counter() - t0) * 1000:.1f} ms")

    note = " (0 expected on random noise)" if args.image is None else ""
    print(f"Detections: {len(detections)}{note}")
    for d in detections:
        line = (
            f"  - class={d.object_class} conf={d.confidence:.3f} "
            f"track_id={d.track_id} "
            f"bbox=({d.bbox[0]:.1f},{d.bbox[1]:.1f})"
            f"->({d.bbox[2]:.1f},{d.bbox[3]:.1f})"
        )
        print(line)

    if args.save is not None:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(args.save), _annotate(frame, detections))
        print(f"\nAnnotated: {args.save}")


if __name__ == "__main__":
    main()
