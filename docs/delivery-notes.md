# NeuraWatch — Delivery Notes

## Production-ready vs Hacky

**Production-ready.** WebSocket contract is explicit and versioned (`frontend/design-specs/README.md` §6). In-flight backpressure + 2-second watchdog + latest-wins on the size-1 inference queue stops clients from out-running the server. ZoneService / AlertService / SnapshotService are recreated per connection — Reset Demo, source switches, and reconnects never leak state. AlertService debounces (`DEBOUNCE_FRAMES=2`) so jitter doesn't duplicate alerts. SQLite runs in WAL mode so REST reader and WS writer coexist. 126 backend pytest tests via httpx + ASGITransport. TypeScript strict on the frontend. The 10-minute soak holds 38.67 FPS mean / 29.58 FPS per-minute min on M4 Max — ~3× the AC floor.

**Hacky.** No frontend test harness; regression coverage is the manual Friday QA run plus pre-commit PR reviews. One in-process inference worker. Hard-coded class taxonomy. One-shot WS reconnect, no exponential backoff. No auth or rate limiting. SnapshotService writes are best-effort (logged, not retried). ByteTrack drops IDs on ~10+-frame occlusions — narrated in the Loom, not fixed. ngrok free-tier on one laptop is the only remote-reach path; if the laptop sleeps, the demo dies.

## Top 5 improvements

1. **Frontend test harness (Vitest + RTL).** Three regressions this week — upload-mode first-frame-only, autoplay timing, Reset Demo zone-resync — would all have been caught by reducer + WS-effect tests.
2. **Re-id head on top of ByteTrack.** Bundled ByteTrack drops IDs after ~3 s of occlusion. An OSNet-style embedding keyed on the bbox crop re-associates across longer gaps.
3. **Inference worker pool + multi-camera.** Producer/consumer queue per camera, fixed worker pool, GPU batching when >1 frame is ready in the same tick.
4. **Cloud deploy backup.** Mirror the single-port FastAPI image to Cloud Run / Fly behind a stable domain so a sleeping laptop or rotating ngrok URL doesn't kill the demo.
5. **Alerts dashboard with search, filter, retention.** Newest-first is demo-shaped. Operators want class filters, time scrubbing, retention windows, CSV export.

## Scaling to 200 feeds

Today's shape is 1-feed: one WebSocket, one worker, one SQLite, one operator. 200 feeds needs three shifts.

**Inference fan-out.** YOLOv8n on an A10 sustains ~120 inferences/sec; 200 feeds × 10 FPS = 2,000/sec. ~17 A10s without batching, or 4 H100s with dynamic batches of 8–16 frames. Stand up Triton (or Modal / Ray Serve) behind a job queue; FrameProcessor becomes a publisher. Per-feed FPS stays at 10 — scale by adding GPUs.

**State and storage.** Per-connection services move from Python objects to Redis structures keyed by `feed_id`. SQLite → Postgres partitioned on `feed_id` (or per-region DBs). Saved frames → S3 with signed URLs. `/alerts` paginates server-side.

**Transport and observability.** WebSocket stays for dashboard ↔ gateway, but feed ingestion switches to NATS / Redis Streams. One gateway per region multiplexes 50–100 feeds onto a small WS pool. Backpressure shifts from a per-connection boolean to a per-feed token bucket sized by recent inference latency. Per-feed FPS, p99 latency, alert rate, and drop rate land on a Grafana board operators actually watch.
