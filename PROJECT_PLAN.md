# NeuraWatch Project Plan — Ratified

**Status:** Polished and ratified by PO + staff backend + staff frontend review rounds.
**Shipping window:** Tue 2026-04-21 → Fri 2026-04-24 EOD CDMX. Solo build.
**Supplementary references:** `TECHNICAL_DESIGN_DOCUMENT.md` (deep technical reference; cuts in this plan are authoritative over the TDD), `JIRA_TICKETS.md` (backlog).

## Goal

Build a working web application that:

- Accepts either a webcam stream or an uploaded video file
- Runs real-time object detection with a pre-trained open-source model
- Detects and tracks three classes:
  - `person`
  - `vehicle` (aggregated from `car`, `truck`, `bus`, `motorcycle`)
  - `bicycle`
- Lets the user draw a polygon zone on the video
- Triggers alerts when tracked objects enter or exit the zone
- Logs alerts to a database with timestamp, object class, event type, and saved frame
- Shows a dashboard with live feed + recent alerts list + alert detail
- Runs at a minimum of 10 FPS on a reasonable laptop
- Is accessible through a browser via a shareable link

## MVP Definition

One browser-accessible app served from a single FastAPI process behind one ngrok tunnel:

- **Input:** webcam (getUserMedia @ 640×480) + uploaded video file
- **Detection:** YOLOv8n + ByteTrack, pre-trained, three classes (`person`, `vehicle`, `bicycle`)
- **Zone:** one user-drawn polygon, click-to-add + explicit Close/Clear buttons, auto-cleared on source switch
- **Alerts:** enter/exit with 2-frame debounce, persisted to SQLite with saved frame, pushed to UI via WebSocket
- **Dashboard:** single side-panel (alert list left, selected frame right)
- **Ops:** Reset Demo button
- **Docs:** README, 10–15 min Loom, one-page delivery doc

**Thursday-night panic-cut order (in this order):**

1. Uploaded-video mode — webcam only
2. Alert detail frame preview — list only; detail opens image in new tab
3. Debounce tuning — hard-code 2 frames
4. FPS indicator — connection-status only

## Stack

- Backend: Python + FastAPI + `aiosqlite`
- Inference: Ultralytics YOLOv8n + ByteTrack
- Frame processing: OpenCV
- Zone math: Shapely (point-in-polygon)
- Frontend: React + TypeScript + Vite
- Database: SQLite (6 fields, one table)
- Saved frames: local disk under `backend/storage/frames/`
- Realtime transport: WebSocket (binary JPEG + JSON metadata)
- Deployment: FastAPI serves built Vite `dist/` on the same port; single ngrok tunnel (with authtoken)

## Ratified Architecture Decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Inference | Server-side Python, YOLOv8n, ByteTrack via Ultralytics |
| 2 | Transport | Single WS protocol for webcam + upload; server drives cadence |
| 3 | Frame format | 640×480 JPEG q=0.6, binary WS; canvas locked 640×480 everywhere |
| 4 | Payload direction | JSON detections only (no annotated JPEGs returned); FE draws in rAF |
| 5 | Coordinates | Bboxes + polygon both normalized 0–1 against 640×480 processed frame |
| 6 | Flow control | Monotonic `seq` per frame; FE in-flight boolean, 2s watchdog; server drops stale |
| 7 | Zone sync | `zone_version` int on every frame; server echoes on each `detection_result`; no ack-gating |
| 8 | Upload mode | `POST /upload` → `{video_id, source_fps, duration_sec, width, height, processed_fps, total_frames}`; server pushes 10–15 FPS with `pts_ms`; FE pauses `<video>` and steps `currentTime = pts_ms/1000`; terminal `processing_complete` |
| 9 | Debounce | 2 frames (~200ms at 10 FPS), `DEBOUNCE_FRAMES` env var |
| 10 | Reset | `POST /session/reset` (clears DB + `storage/frames/*` + tracker state); visible "Reset Demo" button in StatusBar with `confirm()` |
| 11 | Persistence | SQLite (aiosqlite). **6 fields only:** `id, timestamp, track_id, object_class, event_type, frame_path`. Snapshot write via `asyncio.to_thread` |
| 12 | Frontend structure | Single canvas overlay, single WS singleton + `useReducer` in App (no separate `useWebSocket`/`useWebcam`/`useAlerts` hooks) |
| 13 | Polygon UX | click-to-add → rubber-band preview → "Close Zone" button (≥3 pts) → "Clear"; no double-click, no click-near-first-vertex |
| 14 | UI states shipped | `camera-denied`, `ws-disconnected`, `no-polygon` (zone disabled + hint), `no-alerts`, `upload-in-progress`, `upload-complete`, `model-loading` (first ~3s) |
| 15 | Deployment | FastAPI serves built Vite dist on same port, single ngrok tunnel with authtoken |

## WebSocket Protocol

### Webcam mode

```text
Client → Server:
  {type: "frame_meta", seq: <int>}   (JSON, precedes binary)
  <binary>                           (JPEG 640×480 q=0.6)
  {type: "zone_update", points: [[x,y], ...], zone_version: <int>}
  {type: "zone_clear"}

Server → Client:
  {
    type: "detection_result",
    seq: <int>,
    mode: "webcam",
    detections: [{track_id, class, bbox: [x1,y1,x2,y2], confidence}],
    events:     [{track_id, class, event_type, alert_id}],
    zone_version: <int>,
    stats: {fps, inference_ms}
  }
  {type: "zone_ack", zone_version: <int>}     (informational; FE does not block)
```

### Upload mode

```text
Client → Server:
  POST /upload  (multipart)
  → {video_id, source_fps, duration_sec, width, height, processed_fps, total_frames}

  WS:
  {type: "start_processing", video_id: "<id>"}
  {type: "zone_update", points: [...], zone_version: <int>}

Server → Client:
  {
    type: "detection_result",
    seq: <int>,
    mode: "upload",
    video_id: "<id>",
    frame_number: <int>,
    pts_ms: <int>,            # FE sets video.currentTime = pts_ms / 1000
    detections: [...],
    events: [...],
    zone_version: <int>
  }
  {type: "processing_complete", total_frames: <int>}
```

**Flow-control rules:**

- Client sends next frame only when `inFlight === false`. Watchdog resets `inFlight` after 2s if no ack.
- Server keeps a size-1 frame queue; latest-wins; drops stale frames silently.
- Client ignores any `detection_result` whose `seq` ≤ last rendered `seq`.
- `pts_ms` is monotonic — client drops stale upload-mode messages by both `seq` and `pts_ms`.

## Data Model

### SQLite schema — `alerts` table (6 fields only)

```sql
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT (datetime('now')),
    track_id INTEGER NOT NULL,
    object_class TEXT NOT NULL,
    event_type TEXT NOT NULL,
    frame_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC);
```

No `confidence`, `zone_name`, `source_type`, or `sessions` table. Ship the minimum.

## REST Surface

| Method | Path | Purpose |
|---|---|---|
| POST | `/upload` | Upload video for server-side processing; returns metadata |
| GET | `/alerts?limit=50` | Recent alerts list |
| GET | `/alerts/{id}` | Single alert detail |
| GET | `/frames/{filename}` | Saved snapshot file (`StaticFiles`) |
| POST | `/session/reset` | Clear DB + `storage/frames/*` + tracker state |
| GET | `/health` | Server-side health (model loaded, uptime) |

## Ordered Implementation Plan

### Tuesday 04-21 — foundation + live webcam

- **AM GATE (must pass before any feature code):** YOLOv8n benchmark on the actual demo laptop at 640 / 416 / 320 imgsz, 60s sustained each. Lock the lowest resolution that clears ≥12 FPS headroom. If none: lock 320 and accept it.
- AM: monorepo scaffold; FastAPI + SQLite (6 fields); WS endpoint skeleton with `seq` + `zone_version`; `InferenceService` returns JSON detections
- PM: webcam capture 640×480; WS singleton + useReducer; in-flight boolean + 2s watchdog; rAF canvas draws bboxes + labels + track IDs
- **EOD target:** live webcam → boxes on canvas, end-to-end, at benchmarked FPS
- **Cut if behind:** skip FPS indicator; connection-status only

### Wednesday 04-22 — zones + alerts

- AM: `PolygonEditor` (click-to-add, rubber-band, Close ≥3, Clear, auto-clear on source switch); `ZoneService` (Shapely, bottom-center anchor, 2-frame debounce); zone_version plumbing
- PM: `AlertService` with `asyncio.to_thread` snapshot; REST `GET /alerts`, `GET /alerts/{id}`, `GET /frames/{filename}`; WS alert push + initial REST fetch; side-panel (list left / selected frame right) with de-dupe by `alert.id`
- **EOD target:** enter/exit alerts persist with saved frames and display live
- **Cut if behind:** alert detail frame preview — ship list only, detail opens image in new tab

### Thursday 04-23 — upload mode + polish + soak test

- AM: `POST /upload` + server-side OpenCV frame loop; WS `detection_result` with `pts_ms`; FE pauses local `<video>` and steps `currentTime`; terminal `processing_complete`
- PM: Reset Demo button + `POST /session/reset`; ship all 7 UI states; StatusBar (connection + FPS)
- Evening: **10-min sustained webcam soak test + thermals check**; ngrok authtoken + second-device verification; capture a backup happy-path screencap in case Friday breaks
- **Cut if behind:** uploaded-video mode (note as known gap in README)

### Friday 04-24 — ship

- AM: manual QA checklist; fix top 3 visible issues only
- Noon: README, one-page delivery doc (`docs/delivery-notes.md`), Loom outline
- 2pm: Loom dry-run (no record)
- 4pm: demo-ready checklist fully green (see DoD below)
- 4–6pm: record Loom, push repo, submit
- **Cut if behind:** skip Loom polish, record one take, ship

## Risks and Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Tuesday benchmark doesn't hit 10 FPS headroom | Medium | High | Drop to 320 imgsz; if still failing, ship at 7–8 FPS and document — most reviewers won't count frames |
| R2 | ngrok free-tier session timeout or random subdomain dies between QA and submission | Medium | High | Pre-configure authtoken + reserved subdomain; document re-tunnel command in README |
| R3 | Laptop thermals degrade FPS over a 10-min Loom record | Medium | Medium | Thursday evening soak test; have backup recording in the can |
| R4 | Frame save on alert stalls inference loop | Low | High | Snapshot write via `asyncio.to_thread` (ratified) |
| R5 | Upload-mode `pts_ms` arrives out-of-order → `<video>` seeks backward | Low | Medium | BE enforces monotonic `pts_ms`; FE drops stale messages by `seq` and `pts_ms` |
| R6 | Zone vs bbox coord-space mismatch → alerts never fire | Low | High | Both normalized 0–1 against 640×480 processed frame; one integration test Wed with known polygon + known expected enter |
| R7 | Upload mode consumes half of Thursday and threatens webcam polish | Medium | High | Panic-cut upload first if behind by Wed EOD |
| R8 | Loom + one-pager underestimated | Medium | Medium | Half-day budget Friday; Loom outline drafted Thursday night |

## Keep / Cut / Defer

### KEEP (from TDD §14 improvements)

- #1 Binary WS messages
- #3 Normalized 0–1 coordinates (mandatory — prevents polygon misalignment bugs)
- #4 Backpressure / in-flight flag
- #5 Async frame save via `asyncio.to_thread`
- #6 Shapely for point-in-polygon
- #7 Single-port FastAPI-serves-Vite-dist deployment
- #8 Health-check endpoint (server-side only; not used by UI)
- #9 WS alert push (already have the connection)

### CUT (do not ship)

- **NW-1701** cloud backup deploy — doubles failure surface; ngrok is primary
- **NW-1702** configurable tracked classes — gold-plating, not in spec
- **TDD §4.1.4** server-side-only uploaded-video path with redundant browser decode — collapsed into one unified WS protocol
- **TDD §5.1 extras:** `confidence`, `zone_name`, `source_type` columns, `sessions` table
- **TDD §9** WebSocket-per-IP limiter, upload size middleware, ngrok basic auth
- **TDD §11.2 / §11.3** full pytest pyramid — replaced with §11.4 manual checklist
- **Improvement #10** Docker / docker-compose
- **Improvement #11** Roboflow `supervision` library
- **Improvement #12** ONNX Runtime export
- **Improvement #13** WS `permessage-deflate`
- Nginx / Caddy reverse proxy alternative (single-port serves it)
- Exponential-backoff reconnect beyond 1 retry
- `DELETE /alerts` endpoint (replaced by `/session/reset`)
- React Router / modal for alert detail (side-panel instead)
- `useWebSocket` / `useWebcam` / `useAlerts` hooks (single reducer in App)

### DEFER (only if Thursday night is clean)

- Second tracker backend (BoT-SORT) as fallback
- ONNX export
- Cloud backup deploy
- Additional zone support
- Alert filtering / search in the dashboard

## Definition of "Demo-Ready" — Fri 4pm CDMX

All 8 must be green before Loom record:

1. Cold-boot → documented dev command starts app on one port in ≤15s
2. ngrok link opens on a second device; webcam permission prompt works
3. Live feed ≥10 FPS sustained for 60s; StatusBar shows connection + FPS
4. Draw 4-vertex polygon → Close → walk in → exactly 1 `enter` alert within 1s → walk out → exactly 1 `exit`
5. Upload ≤30s MP4 → `processing_complete` received → ≥1 alert during playback
6. Alerts panel shows both alerts correctly; click entry → saved frame renders in right pane
7. Reset Demo → confirm → alerts list empty, `storage/frames/` empty, polygon cleared
8. README, `docs/delivery-notes.md`, Loom outline committed; `git status` clean; repo pushed to GitHub

## Folder Structure

```text
project-root/
  backend/
    app/
      api/
        routes_ws.py
        routes_alerts.py
        routes_upload.py
        routes_session.py
      services/
        inference_service.py
        zone_service.py
        alert_service.py
        video_service.py
      models/
        schemas.py
      db.py
      main.py
    storage/
      frames/
    requirements.txt
  frontend/
    src/
      components/
        VideoSourcePanel.tsx
        LiveFeedCanvas.tsx
        PolygonEditor.tsx
        AlertsPanel.tsx
        StatusBar.tsx
      services/
        wsClient.ts
        api.ts
      types/
        index.ts
      App.tsx
      main.tsx
    package.json
    vite.config.ts
  docs/
    delivery-notes.md
    loom-outline.md
    qa-run-friday.md
  README.md
```

## Final Deliverables

1. **GitHub repo** — clean commits, readable README (overview, stack, setup, architecture, demo instructions, ngrok re-tunnel command, known gaps)
2. **Deployed demo** — local FastAPI + Vite served via one port, exposed via ngrok (authtoken + reserved subdomain)
3. **Loom video (10–15 min)** — architecture + why; two hard decisions + tradeoffs (server-side vs browser inference; ngrok vs cloud); what broke + how fixed; what +4 weeks would add
4. **One-page doc (`docs/delivery-notes.md`, ≤500 words)** — three labeled sections: Production-ready vs Hacky; Top 5 improvements; Scaling to 200 feeds
