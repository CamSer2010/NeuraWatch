# NeuraWatch

Real-time video analytics web app that detects and tracks objects from a webcam or uploaded clip, fires polygon-zone entry/exit alerts, and stores annotated snapshots for review in a live dashboard.

---

## Overview

**Problem.** A security/monitoring operator wants to know when a person, vehicle, or bicycle crosses into a specific region of a camera view — and to have an auditable record with a snapshot at the moment of the event.

**What NeuraWatch does.** Streams a 640×480 video source (webcam via `getUserMedia`, or an uploaded MP4) through a local YOLOv8n + ByteTrack pipeline at ≥10 FPS. The operator draws a polygon on the live feed; any tracked object whose bottom-center anchor enters or exits the polygon fires a debounced event, written to SQLite and rendered into a newest-first alerts panel with a saved JPEG.

**What it doesn't do.** No multi-camera feeds, no cloud storage, no user accounts, no configurable classes in the UI (the three business classes — `person`, `vehicle`, `bicycle` — are fixed). See [Known gaps](#known-gaps) for the full list of deliberate cuts.

**Target classes.** Built-in COCO taxonomy collapsed to the three NeuraWatch classes: `person` (COCO 0), `bicycle` (COCO 1), `vehicle` (COCO 2/3/5/7 — car, motorcycle, bus, truck).

---

## Stack

- **Backend:** Python 3.11+ + FastAPI, Ultralytics YOLOv8n + ByteTrack, Shapely, SQLite (`aiosqlite` + WAL mode)
- **Frontend:** React 18 + TypeScript + Vite, single `useReducer` in `App.tsx`, module-level WS singleton
- **Transport:** WebSocket (binary JPEG frames + JSON metadata; `frame_meta` → JPEG → `detection_result`)
- **Deployment:** FastAPI serves the built Vite bundle on one port (8000), exposed via ngrok (see [Single-port deployment](#single-port-deployment-nw-1504))

---

## Repo layout

```
backend/
  app/                   FastAPI + services (inference, tracker, zones, alerts)
  scripts/               benchmark + soak scripts
  storage/               SQLite DB, saved frames, cached weights
  tests/                 pytest (126 tests, httpx + ASGITransport)
  requirements.txt       Python deps
  .env.example           copy to .env
frontend/
  src/                   components, services/wsClient.ts, App.tsx reducer
  design-specs/          hi-fi implementation contract (tokens, 13 components, 7 states)
  package.json           npm deps
docs/                    NW-1501 soak, Friday QA, Loom outline, delivery notes
PROJECT_PLAN.md          ratified plan with architecture decisions
JIRA_TICKETS.md          backlog with acceptance criteria
TECHNICAL_DESIGN_DOCUMENT.md
CONTRIBUTING.md          branch/PR/commit conventions
```

---

## Architecture

```
┌────────────┐  JPEG+meta   ┌─────────────────┐  inference  ┌─────────┐
│  Browser   │─────────────▶│  FastAPI /ws    │────────────▶│ YOLOv8n │
│ (getUM /   │◀─────────────│  FrameProcessor │◀────────────│  +      │
│  upload)   │  detections  │  + ZoneService  │             │ ByteTrack│
└────────────┘   + events   │  + AlertService │             └─────────┘
                            └────────┬────────┘
                                     │
                            ┌────────▼────────┐
                            │ aiosqlite (WAL) │
                            │ + saved frames/ │
                            └─────────────────┘
```

- **One WebSocket per session.** Module-level client in `frontend/src/services/wsClient.ts` with in-flight backpressure (the next frame never sends until the previous `detection_result` returns; if 2 s pass with no ack, the watchdog force-closes the socket and `onclose` dispatches a single auto-reconnect).
- **One inference worker.** `FrameProcessor` owns a size-1 queue; fresh frames displace stale ones ("latest-wins") so the backend never falls behind.
- **Per-connection state.** Fresh `ZoneService`, `AlertService`, and `SnapshotService` on every WS connect — so resets / source-switches / reconnects never leak state across sessions.
- **Two distinct modes on the same `/ws/detect` endpoint:**
  - **Webcam** — browser captures at 640×480, sends JPEGs on every rAF tick (backpressure drops most).
  - **Upload** — client posts the file once to `POST /upload`, plays it locally from `URL.createObjectURL(file)`, and the server reads the saved copy with OpenCV at a wall-clock-paced 10 FPS, emitting detections keyed by `pts_ms` + `frame_idx`. Client interpolates between predictions to smooth 10 FPS overlays onto 60 Hz playback.
- **Coordinate contract.** All bboxes + polygon vertices live in normalized 0–1 space. Never store pixel coords.

Deeper detail: [TECHNICAL_DESIGN_DOCUMENT.md](./TECHNICAL_DESIGN_DOCUMENT.md).

---

## Setup (fresh clone)

Prerequisites:
- **Python 3.11+** (tested on 3.14)
- **Node 20+**
- **ngrok** with an authtoken (only needed for remote-device demo; see below)

### Backend

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# One-time: copy the env template and keep the defaults for a standard run.
# INFERENCE_IMGSZ=640 is locked by the NW-1004 benchmark — see Performance below.
cp .env.example .env

# Boot the API on :8000 (lifespan loads YOLO weights + warms the tracker).
# First run downloads ~6 MB of YOLOv8n weights into backend/storage/models/.
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Smoke-check: `curl http://localhost:8000/health` → `{"status":"ok"}`.

### Frontend (dev mode — Vite on port 3000)

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. The dev server hits the backend on `:8000` directly (CORS allows it). For the single-port / ngrok path, see [Single-port deployment](#single-port-deployment-nw-1504).

### Run the tests

```bash
cd backend && .venv/bin/pytest
# 126 passed

cd ../frontend && npm run build
# tsc -b && vite build → ✓ built in ~300ms
```

---

## Demo instructions

A 2–3 minute demo covers the whole feature set in this order:

1. **Open the app** at `http://localhost:3000` (dev mode) or `http://localhost:8000` (single-port build).
2. **Click "Start webcam"**, grant camera permission. The `model-loading` banner appears briefly while the first inference runs; the feed goes `live` (pulsing cyan dot in the StatusBar) with bboxes + labels on detected objects.
3. **Click "Draw zone"**, click ≥3 points on the stage, then **Close Zone**. The polygon fills; the server installs it.
4. **Walk into the zone.** After ~200 ms (2-frame debounce at 10 FPS) an `enter` alert row appears in the AlertsPanel with a violet bbox tint; click the row to see the saved JPEG in the drawer.
5. **Walk out of the zone.** An `exit` alert fires with an amber tint.
6. **Switch source → Upload**. Pick an MP4 under 30 s and ≤ 100 MB. The server processes it server-side while the video plays locally; overlays sync to the video's `currentTime`; a "Processing complete" banner lands at EOF.
7. **Reset Demo.** Click the button in the StatusBar. Confirm dialog → alerts + saved frames + tracker IDs wiped, UI returns to cold-boot state. (Reset Demo is a history-wipe affordance, not an error-recovery CTA — by design.)

Full 18-item manual checklist: [`docs/qa-run-friday.md`](./docs/qa-run-friday.md) (lands with NW-1503).

Loom walkthrough outline (recording order + narration notes): `docs/loom-outline.md` (lands with NW-1602).

---

## Single-port deployment (NW-1504)

For the demo, FastAPI serves the built Vite bundle on port 8000 and ngrok exposes that one port. No CORS dance, no separate frontend server — the SPA and the API share an origin.

### One-time ngrok setup

```bash
# Claim a free account at https://dashboard.ngrok.com, then:
ngrok config add-authtoken <YOUR_TOKEN>
```

### Build and serve

```bash
# 1. Build the frontend bundle → frontend/dist
cd frontend
npm install        # first time only
npm run build

# 2. Start FastAPI on :8000 (serves dist/ + /api + /ws on one port)
cd ../backend
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# 3. In a second terminal, expose :8000 over HTTPS
ngrok http 8000
```

Open the `https://<random>.ngrok-free.app` URL ngrok prints. The frontend derives its WebSocket and REST URLs from `window.location`, so the same build works on `http://localhost:8000` and on the ngrok hostname without a rebuild.

### Re-tunnel after a restart

Free-tier ngrok rotates the public hostname every time the tunnel restarts. After a reboot or a tunnel drop, just re-run:

```bash
ngrok http 8000
```

…and share the new URL. The FastAPI server and the `frontend/dist` bundle do **not** need to be rebuilt.

### Demo limitations

- **`getUserMedia` (webcam) requires HTTPS.** ngrok supplies HTTPS, so remote browsers work. Plain `http://localhost:8000` works locally because browsers treat `localhost` as a secure origin.
- **Inference runs on the host laptop.** The ngrok URL only works while the laptop is on and the uvicorn + ngrok processes are alive.
- **Free-tier ngrok URLs rotate on restart.** The link from yesterday's demo will not work today.
- **Free-tier ngrok has a per-minute request cap.** Fine for a single demo browser; do not stress-test through the tunnel.
- **Through-tunnel FPS is RTT-bound to ~8 FPS, not bandwidth-bound.** The WS client uses in-flight backpressure (one frame outstanding at a time; see `frontend/src/services/wsClient.ts`), so effective FPS ≈ 1 / round-trip-time. Free-tier ngrok routes traffic through a US edge POP, which from CDMX adds ~100–130 ms RTT per frame → ~8 FPS ceiling. Bandwidth is not the bottleneck (a 640×480 JPEG ≈ 50 KB, so 10 FPS is only ≈ 4 Mbps). Paid ngrok does **not** fix this — edge POP locations are shared across tiers unless you buy fixed-region enterprise. **For the NW-1501 ≥10 FPS AC, run the soak test against `http://localhost:8000` directly** (the ticket's AC specifies "webcam path at locked `imgsz`", not "through a tunnel"). The tunnel is the shareability affordance, not the FPS surface.

---

## Performance

NeuraWatch targets **≥10 FPS end-to-end**. Inference resolution is locked by a pre-flight benchmark (NW-1004) — the `imgsz` gate must clear **≥12 FPS sustained** before any feature work starts.

**Benchmark run 2026-04-21** — Apple M4 Max, CPU (MPS not engaged), Python 3.14, Ultralytics 8.4.40, torch 2.11.0, synthetic 640×480 frames, 60 s sustained per resolution:

| `imgsz` | mean FPS | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|
| 640 | 58.43 | 16.9 | 17.91 | 21.33 |
| 416 | 88.56 | 11.22 | 11.7 | 12.64 |
| 320 | 118.89 | 8.32 | 8.78 | 10.2 |

**Locked: `INFERENCE_IMGSZ=640`** — highest `imgsz` clearing the headroom bar, best accuracy available without risking the 10 FPS target. Raw results committed at [`backend/scripts/benchmark_results.json`](./backend/scripts/benchmark_results.json).

**End-to-end soak (NW-1501)** — same laptop, full `/ws/detect` pipeline (JPEG decode → FrameProcessor → YOLO → ByteTrack → ZoneService → AlertService → response):

- **Headless 10-min soak:** 38.67 FPS mean, 23,165 frames, per-minute min 29.58 FPS, zero timeouts, p50→p95 spread ≈ 1% (no thermal-throttle signal). Driven by the synthetic-frame WS client at `backend/scripts/soak_fps.py`.
- **Live webcam 10-min soak:** 32.8 FPS mean, min 30 FPS, ten 1-minute readings off the StatusBar EMA. No fan engagement, no FPS trending.

Full soak log + per-minute buckets: [`docs/nw-1501-soak-results.md`](./docs/nw-1501-soak-results.md) (lands with NW-1501).

### When to re-run the benchmark

The benchmark is a **pre-flight gate**, not a runtime check. The app reads `INFERENCE_IMGSZ` from `.env` and runs against whatever value is there — you do **not** need to benchmark every time you start the app.

| Situation | Re-run? |
|---|---|
| Same laptop, pulling new code | **No** — trust the locked value |
| Cloning to test UI or small changes | **No** — benchmark isn't on the startup path |
| Different demo machine (laptop swap, cloud instance, VM) | **Yes** — results are hardware-specific |
| Same machine, swapping CPU ↔ GPU / MPS / CUDA | **Yes** |
| Major version bump of `torch` or `ultralytics` | **Yes** |
| End-to-end FPS looks wrong in the NW-1501 soak test | **Yes — diagnose before tuning** |

**Practical rule:** run the benchmark once per hardware class, trust the locked value otherwise, always verify with the NW-1501 soak test before recording the Loom.

### Re-run the benchmark

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/benchmark_fps.py
```

Expect ~3 minutes of sustained inference plus a one-time weights download. After the run, update `INFERENCE_IMGSZ` in `backend/.env` with the newly-chosen value.

### Re-run the end-to-end soak

The soak driver and its results dir land with NW-1501. Once that's merged:

```bash
# Terminal A
cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal B — 60 s smoke or 10 min thermals check
backend/.venv/bin/python backend/scripts/soak_fps.py --seconds 60
backend/.venv/bin/python backend/scripts/soak_fps.py --seconds 600
```

Results land at `backend/scripts/soak_results/<timestamp>-<dur>.json` with a companion `latest.json`. Pass condition: mean ≥10 FPS AND no per-minute bucket below 9 FPS.

---

## Known gaps

Deliberate cuts, ordered by when they were decided. Every item is documented, not hidden.

- **No frontend automated test harness.** The project never wired Vitest or React Testing Library — regression coverage lives in the manual Friday QA run ([`docs/qa-run-friday.md`](./docs/qa-run-friday.md)) and in the pre-commit reviews captured in merged PR bodies. Adding a test runner was out of scope for the Friday deadline.
- **No `ws-reconnect` exponential backoff.** The WS client retries once on unexpected close and then flips to `error`. Spec-compliant (design-specs §System States: "auto-retry once"), but a production app would want jittered backoff with more attempts.
- **No configurable tracked classes in the UI (NW-1702 CUT).** The three business classes are hard-coded. Pure gold-plating for a demo; adding a picker would have added settings-state surface without closing a ticket.
- **No cloud-hosted backup deployment (NW-1701 CUT).** ngrok is the only remote-reach affordance. A second deploy path would have doubled the failure surface with no demo benefit.
- **ByteTrack drops track IDs on long occlusions.** Bundled Ultralytics tracker limitation; a walker who disappears for ~10+ frames (behind a pillar, etc.) gets a new ID when they reappear. Narrated in the Loom; NW-1105 holds the backup re-id fix if dry-runs surface it as distracting.
- **Through-tunnel FPS ≈ 8 from CDMX.** RTT-bound, not fixable on free ngrok. The tunnel is the shareability path; localhost is the FPS-demonstration path. Detailed writeup above in [Demo limitations](#demo-limitations).
- **No upload progress bar.** "Uploading…" label only, per PO-directed NW-1202 Tier B scope. Files ≤ 100 MB make a bar's delta small enough that a busy affordance suffices.
- **No built-in multi-camera support.** One WS connection, one source, one operator. Multi-feed is a v2 redesign (fan-out inference workers, separate per-feed zone state).

---

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for branching, commit, and PR conventions.

## Plan and scope

- [PROJECT_PLAN.md](./PROJECT_PLAN.md) — ratified plan, architecture decisions, day-by-day sequencing
- [JIRA_TICKETS.md](./JIRA_TICKETS.md) — backlog with tightened acceptance criteria
- [TECHNICAL_DESIGN_DOCUMENT.md](./TECHNICAL_DESIGN_DOCUMENT.md) — deeper technical reference
- [frontend/design-specs/README.md](./frontend/design-specs/README.md) — hi-fi UI handoff (tokens, 13 components, 7 states)
