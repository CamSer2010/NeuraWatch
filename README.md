# NeuraWatch

Real-time video analytics web app that uses a pre-trained YOLO model to detect and track objects from webcam or uploaded video, trigger polygon zone entry/exit alerts, and store event snapshots for review in a live dashboard.

## Stack

- **Backend:** Python + FastAPI, Ultralytics YOLOv8n + ByteTrack, Shapely, SQLite (`aiosqlite`)
- **Frontend:** React + TypeScript + Vite
- **Transport:** WebSocket (binary JPEG frames + JSON metadata)
- **Deployment:** FastAPI serves the built frontend on a single port, exposed via ngrok

## Repo layout

```
backend/       # FastAPI app, inference, zone logic, SQLite (populated in NW-1002)
frontend/      # React + TS single-canvas UI            (populated in NW-1003)
docs/          # Delivery notes, Loom outline, QA log   (populated in NW-1601 / 1602 / 1603)
.github/       # PR template
PROJECT_PLAN.md
JIRA_TICKETS.md
TECHNICAL_DESIGN_DOCUMENT.md
CONTRIBUTING.md
```

## Local setup

Full setup instructions land with the backend (**NW-1002**) and frontend (**NW-1003**) scaffolds. Full end-to-end demo instructions ship with **NW-1601**.

Prerequisites:
- Python 3.11+ (tested on 3.14)
- Node 20+
- ngrok (with authtoken)

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

## Performance

NeuraWatch targets **≥10 FPS end-to-end**. Inference resolution is locked by a pre-flight benchmark (NW-1004) — the `imgsz` gate must clear **≥12 FPS sustained** before any feature work starts.

**Benchmark run 2026-04-21** — Apple M4 Max, CPU (MPS not engaged), Python 3.14, Ultralytics 8.4.40, torch 2.11.0, synthetic 640×480 frames, 60s sustained per resolution:

| `imgsz` | mean FPS | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|
| 640 | 58.43 | 16.9 | 17.91 | 21.33 |
| 416 | 88.56 | 11.22 | 11.7 | 12.64 |
| 320 | 118.89 | 8.32 | 8.78 | 10.2 |

**Locked: `INFERENCE_IMGSZ=640`** — highest `imgsz` clearing the headroom bar, best accuracy available without risking the 10 FPS target. Raw results committed at [`backend/scripts/benchmark_results.json`](./backend/scripts/benchmark_results.json).

### When to re-run

The benchmark is a **pre-flight gate**, not a runtime check. The app reads `INFERENCE_IMGSZ` from `.env` and runs against whatever value is there — you do **not** need to benchmark every time you start the app.

| Situation | Re-run? |
|---|---|
| Same laptop, pulling new code | **No** — trust the locked value |
| Cloning to test UI or small changes | **No** — benchmark isn't on the startup path |
| Different demo machine (laptop swap, cloud instance, VM) | **Yes** — results are hardware-specific |
| Same machine, swapping CPU ↔ GPU / MPS / CUDA | **Yes** |
| Major version bump of `torch` or `ultralytics` | **Yes** |
| End-to-end FPS looks wrong in the NW-1501 soak test | **Yes — diagnose before tuning** |

The authoritative "does this demo hit 10 FPS?" check is **NW-1501's 60-second soak test** on the full pipeline (capture → WebSocket → inference → overlay), not this standalone benchmark. The benchmark proves the model can do it in isolation; the soak test proves the whole app can.

**Practical rule:** run the benchmark once per hardware class, trust the locked value otherwise, always verify with the NW-1501 soak test before recording the Loom.

### Re-run the benchmark

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/benchmark_fps.py
```

Expect ~3 minutes of sustained inference plus a one-time weights download. After the run, update `INFERENCE_IMGSZ` in `backend/.env` with the newly-chosen value.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for branching, commit, and PR conventions.

## Plan and scope

- [PROJECT_PLAN.md](./PROJECT_PLAN.md) — ratified plan, architecture decisions, day-by-day sequencing
- [JIRA_TICKETS.md](./JIRA_TICKETS.md) — backlog with tightened acceptance criteria
- [TECHNICAL_DESIGN_DOCUMENT.md](./TECHNICAL_DESIGN_DOCUMENT.md) — deeper technical reference
