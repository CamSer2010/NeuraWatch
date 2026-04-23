# Jira Ticket Skeleton: NeuraWatch

**Status:** Ratified after PO + staff backend + staff frontend review rounds (2026-04-21).
Acceptance criteria tightened to measurable lines. Scope cuts applied. See `PROJECT_PLAN.md` for the ratified decisions that drive these tickets.

## How to Use This File

- Use this as the starter backlog for Jira
- Create the Epic tickets first, then create the child Stories and Tasks under each Epic
- Keep implementation tickets small enough to finish in less than 1 day where possible
- Add estimates, assignees, and sprint targets after team alignment

## Ticket Naming Convention

- Project key: `NW`
- Ticket format: `NW-####`
- Recommended convention:
  - Epics use round-number ranges such as `NW-1000`, `NW-1100`, `NW-1200`
  - Child tickets under each Epic use the same range, for example `NW-1001` to `NW-1004`

## Suggested Jira Structure

### Recommended Labels

- `backend`
- `frontend`
- `ml`
- `tracking`
- `zone-alerts`
- `database`
- `performance`
- `deployment`
- `documentation`
- `demo`

## Epic NW-1000: Project Setup and Foundations

**Epic Goal:** Establish the repository, app scaffolding, and local development workflow so implementation can proceed without setup friction.
**Epic Description:** This Epic covers the initial project foundation for the Python backend, TypeScript frontend, environment setup, and basic repo conventions.

### NW-1001

**Type:** Story
**Title:** Initialize monorepo structure for backend and frontend
**Goal:** Create the initial project structure for a Python backend and TypeScript frontend.
**Description:**
Set up the repository structure with separate `backend` and `frontend` folders, plus a shared top-level README and docs area.

**Acceptance Criteria**
- Repo contains `backend/` and `frontend/`
- Basic folder conventions match `PROJECT_PLAN.md` folder structure section
- Initial README exists with project summary
- Local setup instructions documented at a high level

### NW-1002

**Type:** Task
**Title:** Set up FastAPI backend scaffold
**Goal:** Create the minimal backend app structure required to start development.
**Description:**
Add the FastAPI app entry point, route organization, service folders, and dependency file.

**Acceptance Criteria**
- FastAPI app starts locally on port 8000
- Folder structure matches `backend/app/{api,services,models}` layout
- `requirements.txt` pins: `fastapi`, `uvicorn[standard]`, `ultralytics`, `opencv-python-headless`, `shapely`, `aiosqlite`, `pydantic`, `python-multipart`
- `GET /health` returns `{"status":"healthy","model_loaded":<bool>}`

### NW-1003

**Type:** Task
**Title:** Set up React + TypeScript frontend scaffold with Vite
**Goal:** Create the frontend foundation using a fast development setup.
**Description:**
Initialize a React app with TypeScript and a folder layout matching `PROJECT_PLAN.md`.

**Acceptance Criteria**
- `npm run dev` starts Vite on port 3000
- TypeScript strict mode enabled
- Folder layout: `src/{components,services,types}` + `App.tsx` + `main.tsx` (no `hooks/` or `pages/`)
- App shell renders an empty layout with StatusBar placeholder

### NW-1004

**Type:** Task
**Title:** Pre-flight YOLOv8n FPS benchmark gate
**Goal:** Lock inference resolution before any feature work begins.
**Description:**
Benchmark YOLOv8n on the actual demo laptop at `imgsz` values 640 / 416 / 320, 60s sustained each. Lock the lowest resolution that clears ≥12 FPS sustained headroom. If none: lock 320 and accept it.

**Acceptance Criteria**
- Benchmark script lives under `backend/scripts/benchmark_fps.py`
- Output: three mean-FPS numbers for 640 / 416 / 320, plus the chosen `imgsz`
- Chosen `imgsz` recorded in README + backend config
- Gate passes before NW-1101 starts

## Epic NW-1100: Backend Inference and Tracking

**Epic Goal:** Build the backend computer vision pipeline that detects relevant classes, assigns stable track IDs, and exposes structured results to the rest of the system.
**Epic Description:** This Epic covers model integration, class normalization, tracking, and the reusable backend service that processes frames.

### NW-1101

**Type:** Story
**Title:** Integrate pre-trained YOLO model for backend inference
**Goal:** Load and run YOLOv8n on frames in Python.

**Acceptance Criteria**
- `yolov8n.pt` is pre-downloaded and cached in repo-ignored location; version pinned
- Backend runs inference on a sample frame at the locked `imgsz` from NW-1004
- Ultralytics `verbose=False` set (stdout logging alone costs 1-2 FPS)
- Model load happens once at startup, not per request

### NW-1102

**Type:** Task
**Title:** Implement class normalization for required object categories
**Goal:** Map raw COCO class IDs into `person`, `vehicle`, `bicycle`.

**Acceptance Criteria**
- COCO IDs 0 → `person`, 1 → `bicycle`, 2/3/5/7 → `vehicle`
- All other classes filtered out via Ultralytics `classes=[0,1,2,3,5,7]` parameter at inference time
- Class mapping documented in code comment
- Unit test confirms the mapping

### NW-1103

**Type:** Story
**Title:** Add multi-object tracking with stable track IDs
**Goal:** Track detected objects across frames using Ultralytics ByteTrack.

**Acceptance Criteria**
- `model.track(persist=True, tracker="bytetrack.yaml")` used
- Track ID on a walking person remains the same across 90 consecutive frames under normal lighting
- Survives a 0.5s occlusion (≈15 frames @ 10 FPS) in ≥50% of attempts
- ByteTrack defaults documented inline in code
- Single global model instance guarded against concurrent sessions (single active WS only)

### NW-1104

**Type:** Task
**Title:** Build backend frame processing service
**Goal:** Encapsulate inference + tracking behind a single service.

**Acceptance Criteria**
- `InferenceService.process_frame(frame: np.ndarray)` returns `list[Detection]`
- `Detection` fields: `track_id: int, object_class: str, bbox: [x1,y1,x2,y2]` normalized 0–1 against processed frame, `confidence: float`
- Inference runs in a dedicated worker thread with a size-1 queue; latest-wins dropping
- Errors logged but never crash the WS handler

### NW-1105 (BACKUP, not MVP)

**Type:** Task
**Title:** Loosen ByteTrack defaults to reduce ID switches during brief detection gaps
**Goal:** Reduce duplicate `enter` alerts caused by the tracker losing a single person's ID across short detection dropouts (partial occlusion, motion blur, brief confidence dips).

**Context (observed during NW-1402 QA on 2026-04-22):** A single person walking into and out of a zone produced 3 consecutive `enter` events (track_ids 1, 3, 6) with no `exit` events in between before the real exit fired. Root cause is ByteTrack orphaning the track when YOLO detection drops for more than `track_buffer=30` frames; a subsequent re-detection opens a fresh ID, which NW-1303's first-sighting-inside rule correctly emits as a new `enter`. The alert pipeline (NW-1302/1303/1304/1402) is working correctly — the limitation is upstream in the tracker.

**Acceptance Criteria**
- Ship a custom `backend/storage/models/neurawatch_tracker.yaml` based on Ultralytics' bundled `bytetrack.yaml`, with:
  - `track_buffer: 90` (9 s @ 10 FPS) — lost tracks stay "re-id-able" longer
  - `track_high_thresh` / `new_track_thresh` tuned to discourage opening brand-new IDs for a briefly-lost subject
- Plumb the path into `InferenceService` via the existing `_TRACKER_CONFIG` constant (or a settings override) without breaking the NW-1103 session-guard path
- Manual verification: walking into a zone, turning sideways for ~1 s, continuing in and back out must produce exactly 1 `enter` and 1 `exit` in the DB for the same `track_id` ≥50 % of attempts (matches the NW-1103 occlusion AC, just at a bigger occlusion budget)
- Backend pytest suite still green (no new tests required, but the swap must not regress `test_inference_parsing.py` / `test_session_guard.py`)

**Apply when:** the MVP demo reveals the duplicate-enter pattern as distracting during the Loom narration. Skip if the NW-1602 dry-run of the Loom narrates the limitation cleanly as a "known tradeoff + what +4 weeks would add" talking point — in that case, keep the ByteTrack defaults.

**Do NOT attempt** swapping to a ReID-augmented tracker (BoT-SORT with OSNet, StrongSORT, etc.) before Friday — those have their own weights download, FPS cost, and test surface. Out of scope for the deadline.

## Epic NW-1200: Frontend Video Input and Live Feed

**Epic Goal:** Provide the user-facing video experience, including webcam input, uploaded video support, frame streaming, and live overlay rendering.

### NW-1201

**Type:** Story
**Title:** Implement webcam input in browser
**Goal:** Let users select and stream webcam video in the app.

**Acceptance Criteria**
- `getUserMedia({video: {width: 640, height: 480}})` constrains capture to exactly 640×480
- Webcam video displays in the app at CSS 640×480 (no autosize)
- Camera permission denial shows the `camera-denied` UI state with recovery instructions
- Capture canvas is 640×480; display canvas is 640×480; no letterboxing

### NW-1202

**Type:** Story
**Title:** Implement uploaded video file input
**Goal:** Let users upload a video file and play it in the app.

**Acceptance Criteria**
- User can upload an MP4 via `POST /upload` (multipart)
- Server-side response includes `{video_id, source_fps, duration_sec, width, height, processed_fps, total_frames}`
- Local `<video>` element is paused after upload success; not allowed to play freely
- `video.currentTime` stepped via `pts_ms / 1000` on each WS `detection_result`
- `upload-in-progress` and `upload-complete` UI states shipped
- Uploaded videos limited to 100MB via middleware

### NW-1203

**Type:** Story
**Title:** Stream frames from frontend to backend over WebSocket
**Goal:** Send frames and receive live inference results in near real time.

**Acceptance Criteria**
- Single WS client singleton in `services/wsClient.ts`
- Outbound frame pattern: `{type:"frame_meta", seq:<int>}` JSON → binary JPEG blob
- Monotonic `seq: int` stamped per frame; in-flight boolean blocks next capture until ack arrives (or 2s watchdog fires)
- Responses with `seq ≤ lastRenderedSeq` are dropped
- Frame format: 640×480 JPEG quality 0.6 via `canvas.toBlob(cb, "image/jpeg", 0.6)`
- `ws-disconnected` UI state shipped; one reconnect retry attempted

### NW-1204

**Type:** Story
**Title:** Render live detection overlays on video feed
**Goal:** Display bounding boxes, labels, and track IDs over the live video.

**Acceptance Criteria**
- Single canvas overlay (not three stacked layers), draws bboxes + polygon in one rAF loop
- Label format: `${class} #${track_id} ${Math.round(confidence*100)}%`
- Bbox center deviates ≤4 px from subject center at rest
- Canvas redraws every animation frame via rAF (not per-WS-message)
- Class-colored strokes: person=green, vehicle=orange, bicycle=blue (documented)

### NW-1205

**Type:** Task
**Title:** Ship the 7 required UI states
**Goal:** Make all demo-critical states visible and handled.

**Acceptance Criteria**
- `camera-denied` — message + "retry camera" button
- `ws-disconnected` — banner + one reconnect attempt
- `model-loading` — shown for ~3s after WS connect, until first `detection_result`
- `no-polygon` — hint "Draw a zone to enable alerts"; zone logic disabled
- `no-alerts` — "No alerts yet" in alerts panel
- `upload-in-progress` — progress indicator
- `upload-complete` — "Processing complete" banner

## Epic NW-1300: Zone Drawing and Alert Logic

**Epic Goal:** Enable spatial monitoring by letting users define a polygon zone and generate alerts when tracked objects cross the boundary.

### NW-1301

**Type:** Story
**Title:** Build polygon zone drawing tool on live video
**Goal:** Let the user define one polygonal alert zone visually.

**Acceptance Criteria**
- Click-to-add vertices on the overlay canvas
- Rubber-band preview line from last vertex to cursor while drawing
- "Close Zone" button enabled only at ≥3 points
- "Clear" button resets polygon
- **No** double-click-to-close, **no** click-near-first-vertex-to-close
- Polygon vertices sent as normalized 0–1 against 640×480
- `zone_update` messages carry monotonic `zone_version: int`
- Polygon auto-clears on source switch (webcam ↔ upload)
- Zone logic disabled until polygon is closed; `no-polygon` UI state visible

### NW-1302

**Type:** Task
**Title:** Implement point-in-polygon zone evaluation
**Goal:** Determine whether a tracked object is inside or outside the defined zone.

**Acceptance Criteria**
- Uses `shapely.geometry.Polygon.contains(Point(anchor))`
- Anchor = bottom-center of bbox, in the same 0–1 normalized coordinate space as the polygon
- Polygon object cached per `zone_version`; not reconstructed per frame
- Polygons with <3 points are ignored safely
- Each `detection_result` echoes the `zone_version` that was active at eval time

### NW-1303

**Type:** Story
**Title:** Trigger enter and exit alerts on zone boundary transitions
**Goal:** Create alerts only when tracked objects cross the polygon boundary.

**Acceptance Criteria**
- Enter: `outside → inside` transition fires exactly one event
- Exit: `inside → outside` transition fires exactly one event
- No repeat alerts while an object stays in the same state
- Event payload: `{track_id, object_class, event_type, timestamp, alert_id}`
- Events pushed through the existing WS connection (no REST polling)

### NW-1304

**Type:** Task
**Title:** Add 2-frame debounce to reduce false alerts near polygon edges
**Goal:** Prevent edge-jitter from spamming enter/exit events.

**Acceptance Criteria**
- `DEBOUNCE_FRAMES` env var, default 2
- Object oscillating across boundary ≤1 frame per side → 0 alerts
- Clean crossing sustained ≥2 consecutive frames on the new side → exactly 1 alert
- Per-track debounce counter reset on `/session/reset`

## Epic NW-1400: Alert Persistence and Dashboard

**Epic Goal:** Persist alert events and expose them through a side-panel so users can review what happened and inspect saved frames.

### NW-1401

**Type:** Story
**Title:** Create alert storage schema in SQLite
**Goal:** Persist alert metadata for retrieval in the dashboard.

**Acceptance Criteria**
- `alerts` table has exactly 6 fields: `id, timestamp, track_id, object_class, event_type, frame_path`
- No `confidence`, `zone_name`, `source_type` columns. No `sessions` table.
- Index on `timestamp DESC`
- DDL lives in `backend/app/db.py` with inline comment
- Insert + SELECT last-20 round-trip verified

### NW-1402

**Type:** Story
**Title:** Save event frame snapshot when alert is triggered
**Goal:** Preserve a visual record for each alert.

**Acceptance Criteria**
- Snapshot written via `asyncio.to_thread(cv2.imwrite, ...)` — never blocks inference
- Filename format: `{timestamp_ms}_{track_id}_{event_type}.jpg`
- Path stored in `alerts.frame_path`
- Missing `storage/frames/` directory created at startup
- Only the first frame per (track_id, event_type) is saved; duplicates skipped

### NW-1403

**Type:** Task
**Title:** Expose REST API for alerts and frames
**Goal:** Provide the frontend access to persisted alert data.

**Acceptance Criteria**
- `GET /alerts?limit=50&offset=0` returns paginated list
- `GET /alerts/{id}` returns single alert detail
- `GET /frames/{filename}` serves JPEG via `StaticFiles`, mounted under `/frames` with restricted base dir
- Response schemas documented in Pydantic models

### NW-1404

**Type:** Story
**Title:** Build alerts side-panel (list + selected frame)
**Goal:** Show recent alerts and their saved frames in one combined panel.

**Acceptance Criteria**
- Single component (no modal, no routing)
- Left: scrollable list of last 20 alerts, desc by timestamp
- Row format: `HH:MM:SS | class | enter|exit`
- Right: selected alert's saved frame (fetched via `GET /frames/{filename}`) + metadata
- New alerts appear via WS push within 500ms of the event
- Initial REST fetch on mount; de-dupe by `alert.id`
- Empty state: "No alerts yet"
- Frame loading state shown while image fetches

**Note:** This ticket collapses the original NW-1404 (recent list) and NW-1405 (detail modal) into a single side-panel component.

### NW-1405

**Type:** Task
**Title:** Add session reset (Reset Demo button + endpoint)
**Goal:** Let the presenter clear state cleanly during a live demo.

**Acceptance Criteria**
- `POST /session/reset` clears: all `alerts` rows, all files in `storage/frames/`, Ultralytics tracker `persist` state
- "Reset Demo" button visible in StatusBar (top right), destructive variant
- Native `confirm()` dialog: "Reset all alerts, snapshots, and tracker state? This cannot be undone."
- On 200 response, frontend dispatches `{type: "RESET"}` clearing detections, alerts, polygon
- Button disabled while request in flight

## Epic NW-1500: Performance, QA, and Deployment

**Epic Goal:** Make the application demo-ready by validating quality and exposing it through a shareable browser link.

### NW-1501

**Type:** Story
**Title:** Hit sustained ≥10 FPS on webcam path
**Goal:** Meet the spec's FPS floor on the locked resolution.

**Acceptance Criteria**
- Sustained mean FPS ≥10 over 60s on webcam path at locked `imgsz` from NW-1004
- Measured via `processing_stats` rolling window, shown in StatusBar
- Resolution + model noted in README
- Thursday-evening 10-minute sustained soak test + thermals check completes with no degradation below 9 FPS

### NW-1502

**Type:** Task
**Title:** Add StatusBar with connection + FPS indicators
**Goal:** Make runtime state visible for the demo.

**Acceptance Criteria**
- Connection badge: `connected` | `connecting` | `disconnected`
- FPS value rounded to nearest integer, updated at 1Hz
- Reset Demo button lives here (right-aligned)
- Shipped as a single `StatusBar.tsx` component

### NW-1503

**Type:** Story
**Title:** Execute end-to-end manual QA checklist
**Goal:** Validate the main demo scenarios work reliably.

**Acceptance Criteria**
- TDD §11.4 manual checklist executed end-to-end
- Every item either PASS or has a documented known-gap note
- Run log committed as `docs/qa-run-friday.md`
- Any demo-blocker resolved before 4pm CDMX

### NW-1504

**Type:** Story
**Title:** Ship single-port deployment with ngrok
**Goal:** Make the app reachable from any browser via a shareable link.

**Acceptance Criteria**
- FastAPI serves built Vite `dist/` via `StaticFiles(directory="frontend/dist", html=True)`
- App runs on one port (8000); one ngrok tunnel exposes it
- ngrok authtoken configured; re-tunnel command documented in README
- Link tested from a non-dev device (phone hotspot OK): webcam prompt works, one alert fires, Reset works
- Demo limitations noted in README

## Epic NW-1600: Final Deliverables

**Epic Goal:** Package the project into a clean final submission with documentation and demo collateral.

### NW-1601

**Type:** Story
**Title:** Write project README
**Goal:** Deliver a repo that is understandable and runnable by another person.

**Acceptance Criteria**
- Sections: Overview, Stack, Setup, Architecture, Demo instructions, ngrok re-tunnel command, Known gaps
- Setup steps reproduce a working app on a fresh clone
- Chosen `imgsz` from NW-1004 noted
- "Known gaps" lists anything cut in the Thursday panic order

### NW-1602

**Type:** Task
**Title:** Prepare Loom walkthrough outline
**Goal:** Make the final video fast to record.

**Acceptance Criteria**
- Outline covers: architecture + why; two hard decisions + tradeoffs (server-side vs browser inference; ngrok vs cloud); what broke + how fixed; what +4 weeks would add
- Demo flow planned in logical order (webcam → zone → alert → upload → reset)
- Talking points fit in 10–15 min
- Committed as `docs/loom-outline.md`

### NW-1603

**Type:** Story
**Title:** Draft one-page engineering summary document
**Goal:** Deliver the requested summary.

**Acceptance Criteria**
- ≤500 words, fits on one page
- Three labeled sections with explicit headings: "Production-ready vs Hacky", "Top 5 improvements", "Scaling to 200 feeds"
- Committed as `docs/delivery-notes.md`

### NW-1604

**Type:** Task
**Title:** Final repo cleanup
**Goal:** Ensure the repo is presentable before submission.

**Acceptance Criteria**
- `.gitignore` excludes `node_modules/`, `__pycache__/`, `storage/frames/*`, `*.pt`, `.env`
- No temporary artifacts in the tree
- Final repo includes README + `docs/delivery-notes.md` + `docs/loom-outline.md` + `docs/qa-run-friday.md`
- **Do not rewrite commit history.**

## CUT Tickets (removed from scope; not deferred, not stretch)

These were explicitly cut after the polished-plan review and should not be implemented:

### NW-1701 (CUT) — Cloud-hosted backup deployment
**Reason:** Doubles failure surface. ngrok is the primary; a second deploy path adds debug time with no demo benefit.

### NW-1702 (CUT) — Configurable tracked classes in the UI
**Reason:** Not in spec. Pure gold-plating. Fixed class set (`person`, `vehicle`, `bicycle`) is required.

## Suggested Prioritization

### Tuesday 04-21 (must complete today)

- NW-1001 monorepo scaffold
- NW-1002 FastAPI scaffold
- NW-1003 Vite scaffold
- **NW-1004 FPS benchmark gate** (must pass before anything below)
- NW-1101 YOLO integration
- NW-1102 class normalization
- NW-1103 tracking
- NW-1104 frame processing service
- NW-1201 webcam input
- NW-1203 WS stream
- NW-1204 live overlays
- NW-1502 StatusBar (connection indicator only, FPS if time)

### Wednesday 04-22

- NW-1301 polygon editor
- NW-1302 point-in-polygon
- NW-1303 enter/exit events
- NW-1304 debounce
- NW-1401 SQLite schema
- NW-1402 snapshot save
- NW-1403 REST alerts
- NW-1404 alerts side-panel
- NW-1205 UI states (at least `no-polygon`, `no-alerts`)

### Thursday 04-23

- NW-1202 upload mode
- NW-1405 session reset
- NW-1205 (remaining UI states)
- NW-1501 FPS soak test
- NW-1504 ngrok + second-device verification
- NW-1105 tracker tuning — **backup plan**; apply only if Loom dry-run shows the duplicate-enter pattern is distracting

### Friday 04-24

- NW-1503 manual QA
- NW-1601 README
- NW-1602 Loom outline + record
- NW-1603 one-page doc
- NW-1604 repo cleanup
- Submit

## Suggested First Sprint / First Working Session (Tuesday AM)

1. **NW-1004** — Benchmark gate. If this fails, lock 320 imgsz and proceed; do not block the day.
2. **NW-1001 / NW-1002 / NW-1003** — Scaffolding (parallel).
3. **NW-1101 / NW-1102** — YOLO load + class normalization.
4. **NW-1201 / NW-1203** — Webcam capture + WS client.

End of Tuesday target: boxes on canvas, live, at benchmarked FPS.
