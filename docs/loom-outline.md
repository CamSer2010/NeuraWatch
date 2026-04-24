# NW-1602 — Loom walkthrough outline

**Target length:** 10–15 minutes (aim 12, ±3).
**Recording machine:** Apple M4 Max, locally — record against `http://localhost:3000` (Vite dev) or `http://localhost:8000` (single-port build), **not** through the ngrok tunnel. Tunnel adds RTT-bound throttling that misrepresents the pipeline's real FPS (see NW-1504 limitations).
**Tabs to pre-open:** the running app, `docs/qa-run-friday.md`, `docs/nw-1501-soak-results.md`, this file collapsed off-screen as a speaker cue.
**One-time setup before hitting record:** webcam permission already granted in the browser (avoids prompt animation chewing 3 s of the recording), an MP4 ≤ 30 s under ≤ 100 MB ready in Finder.

---

## Segment 1 — Architecture + why (≈ 2 min)

**Show:** ASCII diagram from `README.md#architecture`. Or just narrate over a static IDE pane on the architecture section.

**Talking points:**
- Single-page app + single-process backend. No microservices, no message bus, no cluster. Demo-shaped scope.
- Browser captures 640×480, encodes JPEG, sends over WS. Server-side YOLOv8n + ByteTrack do the inference. Detections flow back over the same WS, plus alerts when bboxes cross a polygon.
- The two structural choices that shape everything else:
  - **One module-level WS singleton** in `frontend/src/services/wsClient.ts` with in-flight backpressure. Next frame doesn't fire until the previous `detection_result` returns. This is the only thing keeping the browser from out-running the model.
  - **One inference worker** in `backend/app/services/frame_processor.py` with a size-1 latest-wins queue. Stale frames get displaced, never queued — so the backend's view of "now" is always the freshest frame the browser sent.
- Per-connection state (Zone / Alert / Snapshot services) so reconnects, source switches, and Reset Demo never leak state across sessions.

**Why this shape, not something else:**
- Single-port FastAPI serving the Vite bundle means **one origin** end-to-end. No CORS, no proxy. ngrok exposes one port, not two.
- SQLite + WAL is enough for a single-operator demo. The /alerts REST reader and the WS-side writer coexist via WAL; no contention.

---

## Segment 2 — Demo flow (≈ 5 min)

Run order: **webcam → zone → enter alert → exit alert → upload mode → Reset Demo**. This is the order the AC specifies and the order the QA checklist runs in.

**Beat 1 — Cold start (≈ 30 s).** Show the empty-state slate: "Connect a webcam to begin." Click **Start webcam**. The `model-loading` banner appears for ~1 s, then the StatusBadge flips to live with a pulsing cyan dot and an FPS readout. Narrate: "First inference takes about a second to warm — that banner is the only thing the operator sees during model warmup."

**Beat 2 — Detections (≈ 30 s).** Walk in front of the camera. Bbox draws with the label "person" + confidence. Pick up a phone showing a car video, point it at the camera — bbox label changes to "vehicle". Three business classes hard-coded: person, vehicle, bicycle. Narrate the COCO collapse (NW-1602: car, motorcycle, bus, truck → vehicle).

**Beat 3 — Zone (≈ 1 min).** Click **Draw zone**. Click ≥3 points on the stage. Rubber-band line follows the cursor. Click **Close Zone** to commit. Narrate: "Polygon is sent to the server with a monotonic version number. Server installs a Shapely polygon and uses it to evaluate every detection's bottom-center anchor."

**Beat 4 — Enter / exit (≈ 1.5 min).** Walk into the zone. ~200 ms later (2-frame debounce at 10 FPS) an alert row appears in the AlertsPanel; bbox flashes violet for 2 s. Click the row — drawer opens with the saved JPEG. Walk out — second alert with amber tint. Narrate the debounce (`DEBOUNCE_FRAMES=2`): brief crossings don't alert, so a person stepping on the boundary doesn't fire 5 events.

**Beat 5 — Tracker stability (≈ 30 s).** Walk slowly across the frame. Track ID stays the same. Walk behind something (occlude the camera with a hand, etc.) — narrate the known limitation here, ByteTrack drops the ID after ~10 frames of occlusion, person reappears with a new ID. Don't apologize for it; explain it as a tracker-buffer limit and frame the +4-weeks fix as a re-id head on top.

**Beat 6 — Upload mode (≈ 1 min).** Switch source → Upload. Pick the prepared MP4. Narrate the architecture: "Client uploads the file once; server keeps a copy. Client plays the video from a local blob URL — zero re-fetch. Server processes the saved file at wall-clock 10 FPS and emits detections keyed by `pts_ms`. Client buffers and interpolates between predictions to smooth the 10 FPS overlay onto 60 Hz playback." Show the overlays moving with the video.

**Beat 7 — Reset Demo (≈ 30 s).** Click **Reset Demo** → confirm. Alerts wiped, saved frames unlinked, tracker IDs reset. Narrate: "This is a history-wipe affordance, not an error-recovery CTA. Errors have their own panels. We deliberately don't conflate the two."

---

## Segment 3 — Two hard decisions + tradeoffs (≈ 3 min)

The brief asks for two. These are the two with real tradeoffs.

### Decision 1: Server-side YOLO vs in-browser inference (≈ 1.5 min)

**Considered:** `tensorflow.js` or `onnxruntime-web` in the browser. Would have been "no backend at all" for inference.

**Chose:** Server-side. **Why:**
- Model + weights stay on the server — no 6 MB weights download per first-load, no version-mismatch surface, no exposing the model to clients.
- Ultralytics + ByteTrack is dramatically more mature on Python than on the browser. ByteTrack has no production-quality JS port. Re-implementing it would have been a week.
- Debugging a Python inference loop is far easier than debugging a WebGL shader path.
- One fewer JS bundle to ship; FE bundle stays under 200 KB gzipped.

**Cost paid:** every frame round-trips. The whole WS / backpressure / latest-wins design exists to keep that round-trip from collapsing. In-browser would have skipped that entirely.

**+4 weeks:** offer both paths. Browser-side for a "quick share" demo where the operator doesn't want to run a backend; server-side for the production path.

### Decision 2: ngrok local-tunnel vs cloud deploy (≈ 1.5 min)

**Considered:** Cloud Run / Fly / a small EC2 with the model pre-loaded.

**Chose:** ngrok local-tunnel. **Why:**
- Cloud GPU spot pricing is unpredictable; cloud CPU FPS at YOLOv8n's load is ~15-25 FPS, not the 50+ we get on M4 Max.
- Cloud deploy has a fat debug surface (Dockerfile, build pipeline, IAM, region selection, model-weight upload). For a demo deadline, this is exactly the wrong place to spend time.
- ngrok preserves whatever FPS the laptop achieves locally — the real thing, not a degraded copy.

**Cost paid:** Through-tunnel FPS is RTT-bound to ~8 FPS from CDMX (free-tier US edge POP). This isn't a bandwidth or pipeline issue — it's the in-flight backpressure serializing frames over a 100-130 ms RTT. Documented in the README. **Workaround:** demo on localhost, share via ngrok only as a "yes this is reachable" affordance.

**+4 weeks:** add a cloud deploy as a backup path, not a replacement. NW-1701 was explicitly cut for that reason — doubling the failure surface for no demo benefit.

---

## Segment 4 — What broke + how I fixed it (≈ 2.5 min)

Pick **one or two** stories. Don't dump the full bug list — pick the most interesting + the most architecturally instructive.

### Story 1 — Reset Demo broke alerting (≈ 1.5 min)

**Symptom:** After clicking Reset Demo and starting the webcam again, alerts stopped firing entirely. Drawing a fresh zone, walking into it — nothing.

**Investigation:**
- Server logs showed no `enter` events. AlertService never saw an in-zone flag flip true. So either zone wasn't installed, or detections weren't reaching it.
- Detections were arriving (logs confirmed). So zone wasn't installed.
- Server's `ZoneService.set_zone` log line never appeared after the reset → server never received a `zone_update`.

**Root cause:** Frontend has a `lastSentZoneVersionRef` that suppresses duplicate flushes during a session. After Reset Demo, `state.zoneVersion` resets to 0. The user draws a new first zone, which becomes version 1. **But the ref still held the version from the previous session — also 1.** Equality guard short-circuited. No flush. Server's fresh `ZoneService` had no polygon.

**Fix:** Reset the ref to 0 inside the WS lifecycle effect, on every new connect. PR #28. The fix is also a strict superset — closes the same edge case for plain stop/start cycles, source switches, and `ws/close retry=false` exits.

**Lesson to narrate:** Refs survive renders. State doesn't. When you mix the two, every pin the ref holds must be invalidated on the same boundary that resets the state. Pick one boundary; reset everything that crosses it.

### Story 2 — Upload mode finished in 10 seconds for a 29-second video (≈ 1 min — optional, only if Story 1 wraps fast)

Brief version: M4 Max ran inference at 30 FPS even though we benchmarked at 12 FPS as the floor. Sampling alone wasn't enough — frames flowed too fast, the FE prediction buffer evicted early predictions before playback reached them, "Processing complete" fired before the video was halfway through. Fix: wall-clock pacing on the server (`asyncio.sleep` to 100 ms between sends) so processing always takes exactly as long as the source video. Hardware-agnostic.

---

## Segment 5 — What +4 weeks would add (≈ 1.5 min)

Group by category, not a flat list:

- **Reliability:** exp-backoff WS reconnect (currently 1-shot retry per spec); jittered watchdog timeout; multi-camera support.
- **UX:** configurable tracked classes in the UI (NW-1702 was cut deliberately — pure gold-plating for a demo); upload progress bar with `frame N / total`; alert search + filtering; alert detail drawer with adjacent-frame scrubbing.
- **ML:** re-id head on top of ByteTrack for occlusion-tolerant tracking (NW-1105 was the backup ticket, never landed); class-confidence calibration (current `0.4` floor is one global value); model-version upgrade path.
- **Infra:** cloud deploy as a second reachability path (NW-1701 was cut to avoid doubling the failure surface for the demo); frontend test harness (Vitest + RTL — never wired); per-event audit log.
- **Scale:** see delivery notes (`docs/delivery-notes.md`) for the 200-feed sketch.

---

## Segment 6 — Wrap (≈ 30 s)

- Repo: link.
- Loom: link.
- README has setup, demo instructions, performance, known gaps. `docs/` has this outline, the soak results, the QA run, the delivery notes.
- "Happy to dig into any of these in detail."

---

## Backup notes (don't say unless asked)

- **If FPS looks wrong on the recording machine:** check `INFERENCE_IMGSZ` in `backend/.env`. Default 640. Drop to 416 if M4 Max isn't the recording hardware.
- **If detection is sparse:** confidence threshold is 0.4. Real demo lighting often needs 0.3.
- **If the WS keeps disconnecting:** kill any other process on port 8000 — uvicorn's `--reload` plus a stale tab can race.
- **If Reset Demo doesn't clear an alert:** the snapshot might still be in the JPEG-write task; reload the page once.
- **If the upload video stalls:** check the ProcessUploadMsg in the WS messages tab — if it never fires, the WS connect race is back. Open and close the upload again.
