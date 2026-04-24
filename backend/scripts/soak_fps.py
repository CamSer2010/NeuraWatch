#!/usr/bin/env python3
"""NW-1501: headless end-to-end FPS soak gate.

Drives synthetic 640x480 JPEG frames through the live `/ws/detect`
pipeline for a configurable duration (default 60s) and reports the
server-observed FPS + latency distribution, with per-minute buckets
so the AC clause "no degradation below 9 FPS across 10 minutes" is
directly answered rather than inferred from aggregate percentiles.

This complements `benchmark_fps.py` (which measures raw model
inference in isolation, NW-1004) by exercising the FULL path every
frame actually travels: WS JSON framing, binary JPEG decode,
FrameProcessor queue, InferenceService, back to the client. A
regression in any of those layers shows up here but not in the
NW-1004 gate.

**What this soak is and isn't.** Synthetic uint8 noise is a
*conservative* transport proxy — it JPEG-encodes 2-3x larger than
a real webcam scene at the same quality setting, so PASS here puts
an upper bound on transport overhead rather than replaying what the
browser actually sends. Noise also rarely clears `conf >= 0.4`, so
ByteTrack / ZoneService / AlertService run mostly on the empty-track
path; populated-track load is exercised by the live webcam run that
Test 3 of `docs/nw-1501-soak-results.md` captures. Treat this script
as a pre-flight gate, not the literal AC.

Prerequisites — backend is running on http://localhost:8000:
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

Run from repo root (in a second terminal):
    backend/.venv/bin/python backend/scripts/soak_fps.py
    backend/.venv/bin/python backend/scripts/soak_fps.py --seconds 600

Output lands at `backend/scripts/soak_results/<timestamp>.json` plus
`latest.json` in the same dir so the canonical record survives a
follow-up `--seconds 60` smoke run.

Exits 0 if mean FPS clears the floor (default 10.0) AND no per-minute
bucket dips below the degradation floor (default 9.0), 1 otherwise —
so the soak can be wired into a pre-demo smoke check.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import websockets

WS_URL = "ws://127.0.0.1:8000/ws/detect"
FRAME_W, FRAME_H = 640, 480
JPEG_QUALITY = 60  # matches frontend/src/components/WebcamView.tsx CAPTURE_QUALITY
# Drop the first N FPS samples from both aggregate + per-minute stats.
# The server's FPS EMA (alpha=0.2, seeded at 0 in routes_ws.py) needs
# a handful of real frames before it stabilizes; without this skip,
# the first minute's min_fps would always be 0.0 and trip the
# degradation gate on every run.
EMA_WARMUP_SAMPLES = 5
FPS_FLOOR = 10.0
# Degradation floor from NW-1501 AC: "no degradation below 9 FPS" over
# the 10-minute soak. Enforced per-minute; a single dip trips the fail.
FPS_DEGRADATION_FLOOR = 9.0
# Short runs can't stabilize the server-side EMA (alpha=0.2 in routes_ws)
# — its first ~5 samples skew low. Below 15s of load, the aggregate
# mean lies about steady-state throughput. Guard rather than silently
# report a misleading number.
MIN_SOAK_SECONDS = 15.0
RESULTS_DIR = Path(__file__).parent / "soak_results"


def _synthetic_jpeg(rng: np.random.Generator) -> bytes:
    """Encode a random 640x480 BGR frame to JPEG.

    Using pure noise rather than a fixed image guarantees the JPEG
    encoder cannot pick trivially-compressible bytes — payload size
    stays representative of a real webcam frame.
    """
    frame = rng.integers(0, 255, (FRAME_H, FRAME_W, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    return buf.tobytes()


async def _soak(duration_s: float, ws_url: str) -> dict:
    rng = np.random.default_rng(seed=1501)

    server_fps: list[float] = []
    server_inf_ms: list[float] = []
    client_rtt_ms: list[float] = []
    # Per-minute buckets: one list per 60s window so we can surface
    # "no dip below 9 FPS in minute 7" directly. The AC's thermals
    # claim lives here — aggregate percentiles alone can hide a short
    # sustained drop.
    minute_fps: list[list[float]] = [[]]
    fps_seen = 0
    t_stable_start = 0.0  # reseeded once warmup ends
    responses = 0
    seq = 0

    async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
        t_start = time.perf_counter()
        deadline = t_start + duration_s
        send_t: dict[int, float] = {}

        while time.perf_counter() < deadline:
            seq += 1
            # Mirror the frontend send order: JSON frame_meta, then
            # the binary JPEG. Any deviation would fail the wsClient
            # protocol contract and the backend would drop the frame.
            await ws.send(
                json.dumps({"type": "frame_meta", "seq": seq, "mode": "webcam"})
            )
            blob = _synthetic_jpeg(rng)
            send_t[seq] = time.perf_counter()
            await ws.send(blob)

            # Wait for the detection_result ack — mirrors the in-flight
            # backpressure the real client enforces (wsClient.sendFrame
            # will not fire the next frame until the current one is
            # acked). Same effective FPS ceiling, same failure modes.
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    raise RuntimeError(f"no response for seq={seq} in 5s")
                if not isinstance(msg, str):
                    continue
                try:
                    parsed = json.loads(msg)
                except json.JSONDecodeError:
                    continue

                mtype = parsed.get("type")
                if mtype == "frame_dropped":
                    # Latest-wins displaced this frame. Not an error;
                    # count it toward the responses tally and move on.
                    t_sent = send_t.pop(parsed.get("seq", seq), None)
                    if t_sent is not None:
                        client_rtt_ms.append((time.perf_counter() - t_sent) * 1000.0)
                    responses += 1
                    break
                if mtype == "detection_result":
                    stats = parsed.get("stats", {})
                    if isinstance(stats, dict):
                        if isinstance(stats.get("fps"), (int, float)):
                            fps = float(stats["fps"])
                            # Skip EMA warmup before recording anywhere.
                            # `fps_seen` counts raw samples off the wire;
                            # the first EMA_WARMUP_SAMPLES are the EMA's
                            # seed-to-steady-state ramp and carry no
                            # information about steady-state throughput.
                            fps_seen += 1
                            if fps_seen > EMA_WARMUP_SAMPLES:
                                server_fps.append(fps)
                                # Bucket by integer minute since the
                                # FIRST stable sample (not t_start) so
                                # warmup doesn't shift the boundaries.
                                bucket = int(
                                    (time.perf_counter() - t_stable_start) // 60
                                )
                                while len(minute_fps) <= bucket:
                                    minute_fps.append([])
                                minute_fps[bucket].append(fps)
                            elif fps_seen == EMA_WARMUP_SAMPLES:
                                # Mark the start of stable-state recording.
                                t_stable_start = time.perf_counter()
                        if isinstance(stats.get("inference_ms"), (int, float)):
                            server_inf_ms.append(float(stats["inference_ms"]))
                    t_sent = send_t.pop(parsed.get("seq", seq), None)
                    if t_sent is not None:
                        client_rtt_ms.append((time.perf_counter() - t_sent) * 1000.0)
                    responses += 1
                    break

        elapsed = time.perf_counter() - t_start

    # `server_fps` already excludes EMA warmup (see fps_seen guard in
    # the receive loop). No further trimming needed here.
    stable_fps = server_fps

    def _pct(xs: list[float], p: float) -> float:
        if not xs:
            return 0.0
        return round(float(np.percentile(xs, p)), 2)

    # Per-minute summaries. Drop any bucket with <3 samples (the
    # final partial bucket on a non-minute-aligned duration, or an
    # empty one from early teardown) so the min-FPS claim isn't
    # dragged down by a 2-sample tail.
    minute_buckets: list[dict] = []
    for idx, samples in enumerate(minute_fps):
        if len(samples) < 3:
            continue
        minute_buckets.append({
            "minute": idx + 1,
            "samples": len(samples),
            "mean_fps": round(statistics.fmean(samples), 2),
            "min_fps": round(min(samples), 2),
            "p50_fps": _pct(samples, 50),
        })
    min_minute_fps = (
        min(b["min_fps"] for b in minute_buckets) if minute_buckets else 0.0
    )

    return {
        "duration_sec": round(elapsed, 2),
        "frames_sent": seq,
        "responses": responses,
        "mean_client_fps": round(responses / elapsed, 2) if elapsed > 0 else 0.0,
        "mean_server_fps": round(statistics.fmean(stable_fps), 2) if stable_fps else 0.0,
        "p50_server_fps": _pct(stable_fps, 50),
        "p95_server_fps": _pct(stable_fps, 95),
        "min_minute_fps": min_minute_fps,
        "minute_buckets": minute_buckets,
        "server_inference_ms": {
            "p50": _pct(server_inf_ms, 50),
            "p95": _pct(server_inf_ms, 95),
            "p99": _pct(server_inf_ms, 99),
        },
        "client_rtt_ms": {
            "p50": _pct(client_rtt_ms, 50),
            "p95": _pct(client_rtt_ms, 95),
            "p99": _pct(client_rtt_ms, 99),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--seconds", type=float, default=60.0,
        help="soak duration in seconds (default 60; use 600 for the 10-min AC soak)",
    )
    ap.add_argument("--url", default=WS_URL, help="WebSocket URL")
    ap.add_argument(
        "--floor", type=float, default=FPS_FLOOR,
        help=f"pass/fail mean-FPS floor (default {FPS_FLOOR})",
    )
    ap.add_argument(
        "--degradation-floor", type=float, default=FPS_DEGRADATION_FLOOR,
        help=(
            f"min-per-minute FPS floor (default {FPS_DEGRADATION_FLOOR}); "
            "a single minute below this fails the soak"
        ),
    )
    args = ap.parse_args()

    if args.seconds < MIN_SOAK_SECONDS:
        print(
            f"ERROR: --seconds must be >= {MIN_SOAK_SECONDS:.0f} — shorter "
            "runs can't stabilize the server-side FPS EMA and report "
            "misleading numbers."
        )
        return 2

    print(
        f"[NW-1501] soaking {args.url} for {args.seconds:.0f}s "
        f"(mean floor {args.floor} FPS, per-minute floor {args.degradation_floor} FPS)"
    )
    try:
        summary = asyncio.run(_soak(args.seconds, args.url))
    except OSError as exc:
        print(f"ERROR: could not connect to {args.url} — is uvicorn running? ({exc})")
        return 2

    mean_ok = summary["mean_server_fps"] >= args.floor
    per_min_ok = (
        summary["min_minute_fps"] >= args.degradation_floor
        if summary["minute_buckets"]
        else True  # under 1 minute — per-minute check N/A
    )
    passed = mean_ok and per_min_ok
    summary["pass"] = passed
    summary["floor_fps"] = args.floor
    summary["degradation_floor_fps"] = args.degradation_floor

    print("\n## Soak results")
    print(f"  duration            {summary['duration_sec']}s")
    print(f"  frames sent         {summary['frames_sent']}")
    print(f"  responses           {summary['responses']}")
    print(f"  mean client FPS     {summary['mean_client_fps']}")
    print(f"  mean server FPS     {summary['mean_server_fps']}   "
          f"(floor {args.floor})  {'OK' if mean_ok else 'FAIL'}")
    print(f"  p50 server FPS      {summary['p50_server_fps']}")
    print(f"  p95 server FPS      {summary['p95_server_fps']}")
    if summary["minute_buckets"]:
        print(f"  min minute FPS      {summary['min_minute_fps']}   "
              f"(floor {args.degradation_floor})  {'OK' if per_min_ok else 'FAIL'}")
        print("  per-minute buckets:")
        for b in summary["minute_buckets"]:
            mark = "  " if b["min_fps"] >= args.degradation_floor else " !"
            print(f"   {mark} m{b['minute']:>2}  mean={b['mean_fps']}  "
                  f"min={b['min_fps']}  p50={b['p50_fps']}  n={b['samples']}")
    print(f"  inference p50/p95   {summary['server_inference_ms']['p50']} / "
          f"{summary['server_inference_ms']['p95']} ms")
    print(f"  client RTT p50/p95  {summary['client_rtt_ms']['p50']} / "
          f"{summary['client_rtt_ms']['p95']} ms")
    print(f"\n  RESULT: {'PASS' if passed else 'FAIL'}")

    # Preserve the 10-min evidence from being overwritten by a
    # follow-up `--seconds 60` smoke run — timestamp each record,
    # and also update `latest.json` so the doc's link is stable.
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dur_tag = f"{int(round(args.seconds))}s"
    out_path = RESULTS_DIR / f"{stamp}-{dur_tag}.json"
    out_path.write_text(json.dumps(summary, indent=2) + "\n")
    (RESULTS_DIR / "latest.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"  written to {out_path}")
    print(f"  and         {RESULTS_DIR / 'latest.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
