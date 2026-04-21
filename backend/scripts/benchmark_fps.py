#!/usr/bin/env python3
"""NW-1004: YOLOv8n FPS benchmark gate.

Runs YOLOv8n inference on synthetic 640x480 frames at imgsz 640 / 416 / 320
for 60 sustained seconds each. Picks the highest imgsz that clears the
>=12 FPS headroom bar. If none clear it, falls back to 320 per plan.

Writes backend/scripts/benchmark_results.json and prints a markdown
results table plus the recommended INFERENCE_IMGSZ value.

Run from repo root:
    python backend/scripts/benchmark_fps.py
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
from ultralytics import YOLO

TARGET_CLASSES = [0, 1, 2, 3, 5, 7]  # person, bicycle, car, motorcycle, bus, truck
FRAME_W, FRAME_H = 640, 480
RESOLUTIONS = (640, 416, 320)
SUSTAINED_SEC = 60.0
WARMUP_ITERS = 10
FPS_HEADROOM = 12.0
CONF = 0.4
MODEL_NAME = "yolov8n.pt"

RESULTS_PATH = Path(__file__).parent / "benchmark_results.json"


def synthetic_frame() -> np.ndarray:
    rng = np.random.default_rng(seed=42)
    return rng.integers(0, 255, (FRAME_H, FRAME_W, 3), dtype=np.uint8)


def benchmark(model: YOLO, frame: np.ndarray, imgsz: int) -> dict:
    for _ in range(WARMUP_ITERS):
        model.predict(frame, imgsz=imgsz, classes=TARGET_CLASSES,
                      conf=CONF, verbose=False)

    latencies_ms: list[float] = []
    frames = 0
    t_start = time.perf_counter()
    deadline = t_start + SUSTAINED_SEC
    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        model.predict(frame, imgsz=imgsz, classes=TARGET_CLASSES,
                      conf=CONF, verbose=False)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        frames += 1

    elapsed = time.perf_counter() - t_start
    lat = np.array(latencies_ms)
    return {
        "imgsz": imgsz,
        "frames": frames,
        "elapsed_sec": round(elapsed, 2),
        "mean_fps": round(frames / elapsed, 2),
        "p50_ms": round(float(np.percentile(lat, 50)), 2),
        "p95_ms": round(float(np.percentile(lat, 95)), 2),
        "p99_ms": round(float(np.percentile(lat, 99)), 2),
    }


def pick(results: list[dict]) -> tuple[int, str]:
    # Highest imgsz that clears the FPS bar wins — better accuracy without
    # missing the performance target. None cleared → 320 fallback per plan.
    for r in sorted(results, key=lambda x: -x["imgsz"]):
        if r["mean_fps"] >= FPS_HEADROOM:
            return r["imgsz"], (f"highest imgsz clearing >={FPS_HEADROOM} FPS "
                                f"({r['mean_fps']} FPS sustained)")
    return 320, (f"none cleared >={FPS_HEADROOM} FPS; locked 320 per plan fallback")


def main() -> None:
    print(f"Loading {MODEL_NAME}...")
    model = YOLO(MODEL_NAME)
    device = str(model.device) if hasattr(model, "device") else "auto"
    print(f"Device: {device}")
    frame = synthetic_frame()
    print(f"Synthetic frame: {FRAME_W}x{FRAME_H} uint8\n")

    results: list[dict] = []
    for imgsz in RESOLUTIONS:
        print(f"Benchmarking imgsz={imgsz} for {SUSTAINED_SEC:.0f}s...")
        r = benchmark(model, frame, imgsz)
        results.append(r)
        print(f"  -> {r['mean_fps']} FPS "
              f"(p50 {r['p50_ms']}ms / p95 {r['p95_ms']}ms / p99 {r['p99_ms']}ms)\n")

    chosen, reason = pick(results)

    payload = {
        "model": MODEL_NAME,
        "device": device,
        "frame_w": FRAME_W,
        "frame_h": FRAME_H,
        "sustained_sec": SUSTAINED_SEC,
        "fps_headroom": FPS_HEADROOM,
        "conf": CONF,
        "target_classes": TARGET_CLASSES,
        "results": results,
        "chosen_imgsz": chosen,
        "reason": reason,
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2) + "\n")

    print("## Benchmark results\n")
    print("| imgsz | mean FPS | p50 ms | p95 ms | p99 ms | frames |")
    print("|---|---|---|---|---|---|")
    for r in results:
        print(f"| {r['imgsz']} | {r['mean_fps']} | {r['p50_ms']} | "
              f"{r['p95_ms']} | {r['p99_ms']} | {r['frames']} |")
    print(f"\n**Device:** {device}  \n**Chosen imgsz:** {chosen} — {reason}\n")
    print(f"Set `INFERENCE_IMGSZ={chosen}` in `backend/.env`.")
    print(f"Results written to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
