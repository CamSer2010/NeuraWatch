# NW-1503 — Friday QA run log

**Date:** 2026-04-24
**Deadline:** 2026-04-24 EOD CDMX
**Cut-off for blocker fixes:** 4:00 pm CDMX (per NW-1503 AC)
**Base commit:** `ac715b5` (main at NW-1205 merge)

## Environment

| Item | Value |
|---|---|
| CPU | Apple M4 Max |
| OS | macOS 26.4.1 |
| Python | 3.14 |
| torch | 2.11.0 |
| ultralytics | 8.4.40 |
| Model | YOLOv8n (`yolov8n.pt`) |
| `INFERENCE_IMGSZ` | 640 |
| Capture resolution | 640×480 |
| Browsers (target) | Chrome (current stable), Firefox (current stable) |
| Local URL | `http://localhost:3000` (Vite dev) / `http://localhost:8000` (single-port) |
| Tunnel URL | via `ngrok http 8000` (rotates each restart) |

## How to run the suite

```bash
# Terminal A — backend
cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal B — frontend (for items exercising the full app)
cd frontend && npm run dev

# Terminal C (only for ngrok items)
ngrok http 8000
```

Work top-to-bottom. Mark each checkbox `PASS` when satisfied, or leave a dated note explaining the gap. Stop-the-bus criteria: any core-loop failure (items 1, 3, 7, 9, 10, 12, 15) is a demo-blocker and must be fixed before 4:00 pm CDMX.

---

## Checklist (TDD §11.4)

### 1. Webcam stream works in Chrome and Firefox

**Steps:** Open http://localhost:3000 → Start webcam → grant permission. Confirm the 640×480 feed renders inside the stage. Repeat in both browsers.

- [ ] Chrome — feed visible, no console errors
- [ ] Firefox — feed visible, no console errors

**Known gaps:** None expected. Safari is out of scope for the demo (spec calls out Chrome + Firefox only).

---

### 2. Uploaded MP4 video plays and is processed

**Steps:** Switch source → Upload video → pick a <30 s MP4 → "Upload & process". Confirm (a) the video begins playing after ~1 s head-start, (b) overlays appear over the playing video.

- [ ] Video plays locally (blob URL — no server static serving)
- [ ] Overlays match the moving objects (interpolation was verified in PR #26)
- [ ] "Processing complete" banner appears at EOF
- [ ] "Re-process video" button re-runs inference without re-upload

**Architectural verification (pre-filled):** Client plays `URL.createObjectURL(file)` locally; server opens `uploads_dir/{video_id}.mp4` server-side via cv2 and streams detections keyed by `pts_ms`+`frame_idx`. Confirmed in `backend/app/api/routes_ws.py:_process_upload` and `frontend/src/components/VideoUploadView.tsx`.

---

### 3. Person detection appears with correct labels

**Steps:** Stand in front of the webcam. Confirm a bbox is drawn with label "person" + confidence %.

- [ ] Bbox appears
- [ ] Label reads `person` (not `Person`, not a COCO ID)
- [ ] Confidence % matches `conf_threshold >= 0.4`

**Architectural verification (pre-filled):** `backend/app/services/inference_service.py:62` maps COCO id 0 → `"person"`. Only the six target IDs (0, 1, 2, 3, 5, 7) are kept; others are filtered.

---

### 4. Vehicle detection works for car, truck, bus, motorcycle

**Steps:** Point the webcam at a phone/monitor playing a street-scene video, or walk outside to verify against real vehicles.

- [ ] Car detected → label `vehicle`
- [ ] Truck detected → label `vehicle`
- [ ] Bus detected → label `vehicle`
- [ ] Motorcycle detected → label `vehicle`

**Architectural verification (pre-filled):** COCO ids 2 (car), 3 (motorcycle), 5 (bus), 7 (truck) all collapse to `"vehicle"` per `inference_service.py:65-68`. Sub-class distinction is deliberately dropped (spec fixes the three business classes).

---

### 5. Bicycle detection works

**Steps:** Hold a bicycle (or a phone displaying one) in frame.

- [ ] Bbox appears with label `bicycle`

**Architectural verification (pre-filled):** COCO id 1 → `"bicycle"` per `inference_service.py:64`.

---

### 6. Track IDs remain stable for a walking person

**Steps:** Open DevTools → `state.detections` (via React DevTools) OR watch bbox labels if they include track IDs. Walk slowly across the frame left→right. `track_id` should remain the same integer.

- [ ] `track_id` stable across frames for steady motion

**Known gap (documented in memory `project_tracker_limitation_loom_talking_point.md`):** ByteTrack drops IDs on brief occlusions (~10+ frames missing). This is a known limitation of the bundled tracker. Narrate it in the Loom if it surfaces ("the person re-enters and gets a new ID — a short-term tracker limit we'd address with a longer re-id window in v1.1"). NW-1105 is the backup fix if demos reveal it as distracting; don't pre-apply.

---

### 7. Polygon can be drawn by clicking vertices

**Steps:** With camera running, click **Draw zone** → click 3+ points on the stage. Rubber-band line follows the cursor.

- [ ] Clicks add vertices (not dragged, clicked)
- [ ] Rubber-band preview renders between last vertex and cursor
- [ ] Bottom hint reads "Click to add vertex · Close Zone when ready · Esc to cancel"

---

### 8. Polygon can be closed (click first vertex or double-click)

**Steps:** After 3+ vertices, click **Close Zone** OR double-click on the stage.

- [ ] Zone closes into a filled polygon
- [ ] PolygonToolbar shows `points: N · closed` (or equivalent)
- [ ] `zone_update` message sent to the server (verify in DevTools Network → WS → Messages)

---

### 9. Polygon can be cleared/reset

**Steps:** With a closed zone, click **Clear zone**.

- [ ] Polygon disappears from stage
- [ ] `zone_clear` message sent with an incremented `zone_version`
- [ ] Bottom hint returns to "⚑ Draw a zone to enable alerts"

---

### 10. Enter event fires when person walks into zone

**Steps:** Draw a zone covering half the stage. Stand outside the zone, then walk into it (or move an object from outside-to-inside).

- [ ] An alert row appears in the AlertsPanel within ~200 ms of crossing
- [ ] Bbox flashes violet for ~2 s (enter tint per design spec §4)
- [ ] Server log shows `enter` event insertion

**Debounce note (pre-filled):** `DEBOUNCE_FRAMES=2` (`backend/app/services/alert_service.py`). A person must be in-zone for 2 consecutive frames before the event fires — ~200 ms at 10 FPS. Brief crossings (< 2 frames) don't alert; that's intentional.

---

### 11. Exit event fires when person walks out of zone

**Steps:** After an enter event, walk out of the zone.

- [ ] A second alert row appears for the same `track_id` with `event_type: exit`
- [ ] Bbox flashes amber for ~2 s (exit tint per design spec §4)

---

### 12. No duplicate alerts for same object staying in zone

**Steps:** Walk into the zone. Stand still for 10–15 s. Only one `enter` row should appear — no repeats every N frames.

- [ ] Exactly one `enter` row for a steady in-zone object
- [ ] `ALERTS_MAX` cap not hit during the stationary period

**Architectural verification (pre-filled):** AlertService tracks per-`track_id` in-zone state across frames; only emits on the `not-in-zone → in-zone` transition edge. Debounce (N=2) further prevents flicker-driven duplicates. See `backend/app/services/alert_service.py`.

---

### 13. Alert appears in dashboard list in real-time

**Steps:** While an event fires, watch the AlertsPanel without refreshing.

- [ ] Row appears at the top of the list within < 1 s of the event
- [ ] New rows have the 2-s new-tint (per design spec §4)
- [ ] Newest-first ordering preserved

---

### 14. Clicking alert shows saved frame in modal

**Steps:** Click any alert row.

- [ ] AlertDetailDrawer opens
- [ ] Saved JPEG renders (from `/frames/<alert_id>.jpg`)
- [ ] Metadata grid shows track_id, object_class, event_type, timestamp
- [ ] Esc or backdrop click closes the drawer; focus returns to the row

**Known gap (documented):** SnapshotService dedupes by `(track_id, event_type)` during a session. If an alert fires before the snapshot write completes, the drawer can briefly show a placeholder before the JPEG lands — reload the page once and the image appears.

---

### 15. Alerts persist after page refresh

**Steps:** Fire at least one event. Refresh the tab.

- [ ] AlertsPanel rehydrates the alerts list on mount (GET `/alerts?limit=50`)
- [ ] Saved frame JPEGs still reachable via `/frames/<id>`
- [ ] Polygon state does NOT persist — the zone is session-scoped (by design)

**Architectural verification (pre-filled):** SQLite (`backend/storage/neurawatch.db`, WAL mode) persists `alerts` rows. `GET /alerts` runs on mount via `AlertsPanel.tsx`. Reset Demo (NW-1405) clears both rows and JPEGs; refresh alone does not.

---

### 16. FPS indicator shows ≥10 FPS on laptop

**Steps:** Start webcam. Watch the FPS readout in the StatusBar. Sustain ≥10 for at least 60 s.

- [ ] Sustained mean ≥10 FPS on localhost

**Pre-filled evidence:** NW-1501 headless soak ran the full pipeline at **38.67 FPS mean over 10 minutes** (23,165 frames, per-minute min 29.58 FPS; see `docs/nw-1501-soak-results.md`). The webcam path has ~1 ms of added browser-side encode overhead that does not change the story — headroom is ~4× the AC floor on this hardware. **Browser-observed confirmation is still required** to close the AC literally; space left here for the live readings.

| t (s) | StatusBar FPS | Notes |
|---|---|---|
| 10 |  |  |
| 20 |  |  |
| 30 |  |  |
| 40 |  |  |
| 50 |  |  |
| 60 |  |  |

---

### 17. App works via ngrok link from a different device

**Steps:** Build frontend (`cd frontend && npm run build`), boot uvicorn on `:8000`, `ngrok http 8000`, open the HTTPS URL on a phone hotspot. Grant camera permission.

- [ ] Page loads on the phone
- [ ] Webcam prompt appears; feed starts after permission granted
- [ ] At least one alert fires when crossing a zone
- [ ] Reset Demo clears state end-to-end

**Known gap (documented in README):** Through-tunnel FPS is RTT-bound to ~8 FPS from CDMX (US edge POP). This is not a bandwidth or pipeline issue — WS in-flight backpressure serializes frames, and paid ngrok does not fix it. See README "Demo limitations". The NW-1501 ≥10 FPS AC is satisfied on the localhost path (item 16); the ngrok test here verifies reachability + the core loop works, not the FPS floor.

---

### 18. Uploaded video processed server-side (no browser decode overhead)

**Steps:** Confirm by code inspection + observable behavior.

- [x] Client uploads the raw file once via `POST /upload` (see `frontend/src/services/uploadClient.ts`)
- [x] Client plays the file locally from `URL.createObjectURL(file)` — no `<video src="http://backend/...">` (see `VideoUploadView.tsx`)
- [x] Server opens `uploads_dir/{video_id}.mp4` with cv2 and iterates frames in `_process_upload` (see `routes_ws.py:321`)
- [x] Server emits per-frame detections keyed by `pts_ms` + `frame_idx`; client buffers and matches against `<video>.currentTime` (see `VideoUploadView.tsx` + `LiveFeedCanvas.tsx:_interpolateDetections`)

**All four conditions met by inspection.** The architecture was explicitly re-architected in PR #26 (NW-1202 Tier B) per PO direction on 2026-04-23.

---

## Cross-cutting observations

### Regression checks to confirm during the run

- [ ] Reset Demo → start webcam → draw new zone → alert fires (PR #28 regression: zone-sync ref reset on reconnect)
- [ ] `model-loading` banner appears for ~1–3 s after Start webcam, then disappears at first detection (PR #30 + NW-1205)
- [ ] `disconnected` banner appears if the backend is killed mid-session, auto-clears on reconnect (PR #30 + NW-1205)

### Backend test suite

Run once at the start of QA as a regression gate:

```bash
cd backend && .venv/bin/pytest
# expect: 126 passed
```

- [ ] 126/126 backend pytest pass

### Frontend build

```bash
cd frontend && npm run build
# expect: tsc -b && vite build → ✓ built in <500ms
```

- [ ] Frontend build passes TypeScript strict + Vite production bundle

---

## Known gaps (documented, not blockers)

1. **ByteTrack ID drops on occlusion** — see item 6. Narrate in the Loom.
2. **ngrok FPS ~8 from CDMX** — see item 17. Use localhost for FPS demonstration; tunnel for "shareable link" narrative.
3. **Snapshot briefly placeholder before JPEG lands** — see item 14. Reload once; already caveated in the AlertDetailDrawer docstring.
4. **No frontend automated test harness** — project never wired Vitest/RTL. Regression coverage for this PR series (upload mode, zone-sync fix, banners) lives in this manual run, not in CI.

## Demo-blocker decision record

If any core-loop item (1, 3, 7, 9, 10, 12, 15) fails, log below and fix before 4:00 pm CDMX:

| Time | Item | Symptom | Fix | Merged |
|---|---|---|---|---|
|  |  |  |  |  |

If 4:00 pm passes with an unresolved blocker, narrate it explicitly in the Loom and in the delivery notes (NW-1603) rather than silently ship broken.
