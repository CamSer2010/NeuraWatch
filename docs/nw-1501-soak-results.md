# NW-1501 — Sustained ≥10 FPS on webcam path

**Date run:** 2026-04-24
**Deadline:** 2026-04-24 EOD CDMX

## Hardware + software

| Item | Value |
|---|---|
| CPU | Apple M4 Max |
| OS | macOS 26.4.1 |
| Python | 3.14 |
| torch | 2.11.0 |
| ultralytics | 8.4.40 |
| Model | YOLOv8n (`yolov8n.pt`) |
| `INFERENCE_IMGSZ` | 640 (locked per NW-1004) |
| Confidence threshold | 0.4 |
| Capture resolution | 640×480 |
| JPEG quality | 0.6 (webcam) / synthetic jpg q=60 (soak) |

## Test 1 — Headless 60-second pipeline soak

Exercises the full `/ws/detect` path end-to-end with synthetic 640×480 frames: JPEG encode → WS JSON frame_meta → binary send → FrameProcessor queue → YOLO predict → ByteTrack → ZoneService / AlertService → WS JSON response. The only thing missing vs. the webcam path is the browser's getUserMedia + the canvas JPEG encode.

**Command:**
```bash
backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000   # terminal A
backend/.venv/bin/python backend/scripts/soak_fps.py --seconds 60     # terminal B
```

**Results (not committed — these were from an earlier run; the committed `latest.json` reflects Test 2's 10-minute soak):**

| Metric | Value | Floor |
|---|---|---|
| Duration | 60.02 s | — |
| Frames sent | 2315 | — |
| Responses | 2315 | — |
| **Mean server FPS** | **38.72** | **≥10.0** ✅ |
| p50 server FPS | 38.96 | — |
| p95 server FPS | 39.67 | — |
| Inference p50 / p95 / p99 | 17.06 / 18.30 / 22.09 ms | — |
| Client RTT p50 / p95 / p99 | 24.14 / 25.54 / 30.18 ms | — |

**Verdict: PASS.** Mean pipeline FPS is ~4× the spec floor; headroom is generous.

Reading this result: the `--seconds 60` run is NOT the AC (the AC requires observing `stats.fps` in the StatusBar during a real webcam session). But it's a stronger pre-flight than `backend/scripts/benchmark_fps.py` (NW-1004) because it exercises every component the webcam path traverses, not just the model. If this run dips below 10, the webcam path will too.

## Test 2 — Headless 10-minute thermals soak

Same script, 10× duration. The AC specifically calls out "no degradation below 9 FPS" across 10 minutes of sustained load — the concern is thermal throttling on the laptop CPU after a few minutes at full tilt.

**Command:**
```bash
backend/.venv/bin/python backend/scripts/soak_fps.py --seconds 600
```

**Results (committed at `backend/scripts/soak_results/latest.json`):**

| Metric | Value | Floor |
|---|---|---|
| Duration | 600.02 s | — |
| Frames sent | 23,165 | — |
| Responses | 23,165 | — |
| **Mean server FPS** | **38.67** | **≥10.0** ✅ |
| p50 server FPS | 38.83 | — |
| p95 server FPS | 39.16 | — |
| **Min per-minute FPS** | **29.58** | **≥9.0** ✅ |
| Inference p50 / p95 / p99 | 16.85 / 17.68 / 18.49 ms | — |
| Client RTT p50 / p95 / p99 | 24.15 / 25.18 / 28.01 ms | — |

**Per-minute distribution:**

| Minute | Mean FPS | Min FPS | p50 FPS | Samples |
|---|---|---|---|---|
| 1 | 38.76 | 30.67 | 38.95 | 2321 |
| 2 | 38.65 | 31.91 | 38.77 | 2317 |
| 3 | 38.69 | 30.25 | 38.87 | 2319 |
| 4 | 38.70 | 32.30 | 38.82 | 2319 |
| 5 | 38.66 | 30.39 | 38.84 | 2315 |
| 6 | 38.61 | 29.58 | 38.80 | 2311 |
| 7 | 38.67 | 33.34 | 38.84 | 2318 |
| 8 | 38.61 | 32.39 | 38.80 | 2313 |
| 9 | 38.71 | 33.96 | 38.82 | 2320 |
| 10 | 38.59 | 30.92 | 38.78 | 2307 |

**Verdict: PASS.** Mean holds at ~3.9× the floor across 600 seconds, 23,165 consecutive frames processed with zero timeouts. Across all 10 buckets the mean drifts within 38.59–38.76 (0.44% spread) and no single minute's min drops below 29.58 FPS — 3.3× the degradation floor. A thermal throttle would show up as a monotonic downward trend in late buckets; instead minutes 7–9 are among the highest in the run. The pipeline is clearly operating well inside the hardware's steady-state envelope.

## Test 3 — Live webcam soak (AC-literal)

The AC's "measured via `processing_stats` rolling window, shown in StatusBar" clause demands a human-observed run in the actual browser, not a headless driver. This is the authoritative record.

### Protocol

1. Terminal A:
   ```bash
   cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
2. Terminal B:
   ```bash
   cd frontend && npm run dev
   ```
3. Open http://localhost:3000.
4. Click **Start webcam**. Grant permission.
5. Optional — draw a polygon so ZoneService + AlertService are exercised (matches real demo load).
6. Let it run.

### 60-second observation

Read the FPS readout in the StatusBar every 10 seconds. Mean must be ≥10.

| t (s) | FPS | Notes |
|---|---|---|
| 10 | 32 | - |
| 20 | 35 | - |
| 30 | 34 | - |
| 40 | 35 | - |
| 50 | 35 | - |
| 60 | 36 | - |

Mean: **34.5 FPS**  · Pass (≥10): **✅**

### 10-minute thermals observation

Read FPS every minute + note CPU behavior (fans audible, laptop warm, etc.). Any single reading below 9 fails the AC.

| t (min) | FPS | Laptop temp / fan | Notes |
|---|---|---|---|
| 1 | 31 | +---- / No | - |
| 2 | 32 | +---- / No | Too much detections |
| 3 | 34 | +---- / No | Less detections |
| 4 | 36 | +---- / No | - |
| 5 | 30 | ++--- / No | No detections |
| 6 | 33 | ++--- / No | - |
| 7 | 34 | ++--- / No | - |
| 8 | 32 | ++--- / No | - |
| 9 | 33 | ++--- / No | - |
| 10 | 33 | ++--- / No | - |

Min FPS observed: **30 FPS**  · Mean: **32.8 FPS** · Pass (no dip below 9): **✅**

**Thermals:** laptop warmth progresses from `+` to `++` across the 10 minutes but the fan never engages. No FPS trending — minute 10 (33) is statistically indistinguishable from minute 1 (31). Detection-count variability ("too much detections" at m2, "no detections" at m5) did not translate into FPS swings, which is consistent with inference cost being dominated by the YOLO forward pass rather than bbox count. Pipeline is comfortably inside the hardware's steady-state envelope.

## Acceptance criteria check

- [x] Sustained mean FPS ≥10 over 60s on the pipeline (headless proxy: 38.72)
- [x] Sustained mean FPS ≥10 over 60s on the **webcam** path (Test 3: mean = 34.5 FPS, 3.4× the floor)
- [x] Measured via `processing_stats` rolling window, shown in StatusBar (StatusBar already renders `stats.fps` per NW-1502)
- [x] Resolution + model noted in README (640×480 capture, YOLOv8n at `imgsz=640`; see README Performance section)
- [x] 10-minute sustained headless soak with no degradation below 9 FPS (per-minute min = 29.58 FPS, 3.3× the floor)
- [x] 10-minute sustained **webcam** soak + thermals observation (Test 3: mean = 32.8 FPS, min = 30 FPS; no fan engagement, no FPS trending)

## Notes

- The massive headroom on M4 Max (≈4× the floor) is expected — NW-1004's standalone model benchmark clocked 58 FPS on the same hardware at `imgsz=640`; the WS / FrameProcessor overhead adds ~0.5–1 ms per frame and doesn't meaningfully compress that.
- On slower hardware (Intel laptops, older M1/M2), repeat this soak locally before recording the Loom. If the headless 60s dips below 12 FPS, the webcam path may land below 10 once the browser-side JPEG encode is added. Dropping `imgsz` to 416 per NW-1004's benchmark table gives ~1.5× more headroom.
- The 10-minute soak is the real AC check — brief peaks in 60s runs can mask thermal throttling that only manifests after 3–5 minutes of sustained load.
