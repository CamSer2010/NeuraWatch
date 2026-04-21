# Technical Design Document: NeuraWatch

## Real-Time Object Detection & Zone-Based Alert System

**Version:** 1.0
**Status:** Draft
**Last Updated:** 2026-04-20

---

## Table of Contents

1. [Overview](#1-overview)
2. [Goals and Non-Goals](#2-goals-and-non-goals)
3. [System Architecture](#3-system-architecture)
4. [Detailed Technical Design](#4-detailed-technical-design)
5. [Data Model](#5-data-model)
6. [API Contracts](#6-api-contracts)
7. [Key Technical Decisions](#7-key-technical-decisions)
8. [Performance Design](#8-performance-design)
9. [Security and Reliability](#9-security-and-reliability)
10. [Deployment Strategy](#10-deployment-strategy)
11. [Testing Strategy](#11-testing-strategy)
12. [Risks and Mitigations](#12-risks-and-mitigations)
13. [Feasibility Review](#13-feasibility-review)
14. [Improvements Over Original Plan](#14-improvements-over-original-plan)
15. [Implementation Roadmap](#15-implementation-roadmap)

---

## 1. Overview

### 1.1 Problem Statement

Build a browser-accessible web application that accepts video input (webcam or uploaded file), runs real-time object detection using pre-trained YOLO weights, enables zone-based spatial alerts via user-drawn polygons, and provides a dashboard for reviewing historical alert events with saved frame snapshots.

### 1.2 Project Codename

**NeuraWatch**

### 1.3 Deadline

Friday EOD (CDMX time, UTC-6) — 2026-04-24

### 1.4 Deliverables

| Deliverable | Description |
|---|---|
| Deployed demo | Browser-accessible app via shareable link |
| GitHub repo | Clean commits, readable README |
| Loom video | 10–15 min walkthrough of architecture, decisions, failures, and future |
| One-page doc | Production-readiness assessment, top 5 improvements, scaling to 200 feeds |

---

## 2. Goals and Non-Goals

### 2.1 Goals

- Accept webcam stream or uploaded video file as input
- Run real-time object detection at ≥10 FPS on a reasonable laptop
- Detect and track `person`, `vehicle` (car/truck/bus/motorcycle), and `bicycle`
- Let users draw a polygon zone and generate alerts on enter/exit transitions
- Persist alerts to a database with timestamp, class, event type, and saved frame
- Expose a dashboard with live feed, recent alerts, and clickable alert detail
- Ship a working deployed demo by Friday EOD

### 2.2 Non-Goals

- Training or fine-tuning any model
- Multi-camera support or camera orchestration
- User authentication or multi-tenancy
- Production-grade horizontal scaling
- Long-term durable storage or backup
- Accurate re-identification across extended occlusions
- Mobile-optimized UI (desktop-first is acceptable)

---

## 3. System Architecture

### 3.1 High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        BROWSER (React + TS)                  │
│                                                              │
│  ┌──────────┐  ┌──────────────┐  ┌───────────┐  ┌────────┐  │
│  │ Video    │  │ Canvas       │  │ Polygon   │  │ Alerts │  │
│  │ Source   │  │ Overlay      │  │ Editor    │  │ Dash   │  │
│  │ Panel    │  │ (detections) │  │ (zone)    │  │ board  │  │
│  └────┬─────┘  └──────▲───────┘  └─────┬─────┘  └───▲────┘  │
│       │               │                │             │       │
│       ▼               │                ▼             │       │
│  ┌────────────────────────────────────────────────────┐      │
│  │         WebSocket Client (binary frames)           │      │
│  └────────────────────┬───────────────────────────────┘      │
│                       │                                      │
└───────────────────────┼──────────────────────────────────────┘
                        │ WS (binary + JSON)
                        ▼
┌──────────────────────────────────────────────────────────────┐
│                   BACKEND (Python + FastAPI)                  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                  WebSocket Handler                     │  │
│  │  Receives frames + zone config, returns detections     │  │
│  └────────────┬───────────────────────────────────────────┘  │
│               │                                              │
│  ┌────────────▼──────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ InferenceService  │  │ ZoneService  │  │ AlertService │  │
│  │ (YOLO + Tracker)  ├─▶│ (PiP checks, ├─▶│ (persist,    │  │
│  │                   │  │  transitions)│  │  snapshots)  │  │
│  └───────────────────┘  └──────────────┘  └──────┬───────┘  │
│                                                   │          │
│  ┌────────────────────────────────────────────────▼───────┐  │
│  │   VideoProcessingService (server-side upload handler)  │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────┐  ┌────────────────┐                        │
│  │ SQLite (DB)  │  │ Disk Storage   │                        │
│  │ alerts table │  │ /frames/*.jpg  │                        │
│  └──────────────┘  └────────────────┘                        │
│                                                              │
│  REST API: GET /alerts, GET /alerts/{id}, GET /frames/{f}   │
│  REST API: POST /upload-video (server-side processing)      │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 Stack Summary

| Layer | Technology | Rationale |
|---|---|---|
| Frontend | React + TypeScript + Vite | Fast iteration, strong typing, modern tooling |
| Backend | Python + FastAPI | Async-native, excellent ML ecosystem, WebSocket support |
| Inference | Ultralytics YOLOv8n/YOLOv11n | Pre-trained, built-in tracker, minimal setup |
| Tracking | ByteTrack (via Ultralytics) | Stable IDs, no extra dependency |
| Zone Logic | Shapely + custom state machine | Robust point-in-polygon, clean separation |
| Database | SQLite via aiosqlite | Zero-ops, async-compatible, sufficient for demo |
| Frame Storage | Local disk (`/storage/frames/`) | Simple, fast writes, served via static files |
| Deployment | Local + ngrok tunnel | Full laptop GPU/CPU, avoids cloud inference limits |

---

## 4. Detailed Technical Design

### 4.1 Backend Services

#### 4.1.1 InferenceService (`services/inference_service.py`)

**Responsibility:** Load YOLO model, run detection + tracking, normalize classes.

```python
# Pseudocode
class InferenceService:
    def __init__(self, model_name: str = "yolov8n.pt"):
        self.model = YOLO(model_name)
        self.class_map = {
            0: "person",
            1: "bicycle",
            2: "vehicle",  # car
            3: "vehicle",  # motorcycle
            5: "vehicle",  # bus
            7: "vehicle",  # truck
        }
        self.target_classes = list(self.class_map.keys())

    def process_frame(self, frame: np.ndarray) -> list[Detection]:
        results = self.model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=self.target_classes,
            imgsz=640,
            conf=0.4,
            verbose=False,
        )
        return self._parse_results(results)
```

**Key design choices:**
- Filter to target COCO classes at inference time (`classes` parameter) to reduce noise and improve FPS
- Use `persist=True` for tracker state across frames
- Normalize COCO class IDs to business categories (`person`, `vehicle`, `bicycle`)
- Confidence threshold at 0.4 (tunable) — balances false positives vs missed detections

#### 4.1.2 ZoneService (`services/zone_service.py`)

**Responsibility:** Point-in-polygon evaluation, per-track state transitions, debounce.

```python
class ZoneService:
    def __init__(self, debounce_frames: int = 3):
        self.polygon: Optional[Polygon] = None  # Shapely Polygon
        self.track_states: dict[int, TrackZoneState] = {}
        self.debounce_frames = debounce_frames

    def set_zone(self, points: list[tuple[float, float]]):
        """Set or update the active polygon zone (normalized coords 0-1)."""
        self.polygon = Polygon(points)

    def evaluate(self, detections: list[Detection], frame_shape: tuple) -> list[ZoneEvent]:
        """Check each detection against the zone, emit events on transitions."""
        events = []
        for det in detections:
            anchor = self._get_anchor(det, frame_shape)
            is_inside = self.polygon.contains(Point(anchor))
            event = self._update_state(det.track_id, is_inside, det)
            if event:
                events.append(event)
        return events
```

**Debounce strategy:**
- Maintain a counter per track: how many consecutive frames the state has been different from the recorded state
- Only trigger a transition after `debounce_frames` consecutive frames confirm the new state
- Default: 3 frames (~300ms at 10 FPS) — prevents flicker-triggered false alerts

**Anchor point:** Bottom-center of bounding box — best approximates "feet on ground" for people and vehicles.

**Coordinate normalization:**
- **Critical improvement over original plan:** All polygon coordinates must be stored as normalized values (0.0–1.0 relative to frame dimensions), not pixel values. This prevents misalignment when:
  - Display canvas size ≠ inference frame size
  - Browser window is resized
  - Video resolution changes between webcam and upload modes

#### 4.1.3 AlertService (`services/alert_service.py`)

**Responsibility:** Persist alert events, save frame snapshots asynchronously.

```python
class AlertService:
    async def create_alert(
        self,
        event: ZoneEvent,
        frame: np.ndarray,
    ) -> Alert:
        frame_filename = f"{event.timestamp_ms}_{event.track_id}_{event.event_type}.jpg"
        frame_path = FRAMES_DIR / frame_filename

        # Save frame asynchronously to avoid blocking inference
        await asyncio.to_thread(cv2.imwrite, str(frame_path), frame)

        alert = Alert(
            timestamp=event.timestamp,
            track_id=event.track_id,
            object_class=event.object_class,
            event_type=event.event_type,
            frame_path=frame_filename,
        )
        await self.db.insert_alert(alert)
        return alert
```

**Key improvement:** Frame saving is done via `asyncio.to_thread` to avoid blocking the inference pipeline. Disk I/O for JPEG encoding + write should never stall frame processing.

#### 4.1.4 VideoProcessingService (`services/video_service.py`)

> **⚠️ New component not in original plan — addresses a key feasibility gap.**

**Responsibility:** Process uploaded video files server-side instead of streaming frames through WebSocket.

**Why this is needed:**
The original plan routes uploaded video frames through the browser → WebSocket → backend path. This is:
- Wasteful: the video file must be decoded in the browser, frames encoded to base64/binary, sent over the wire, then decoded again in Python
- Slow: adds unnecessary latency and bandwidth overhead
- Fragile: browser tab must stay open for the entire video duration

**Better approach:**
1. Frontend uploads the video file via `POST /upload-video`
2. Backend processes it server-side with OpenCV (`cv2.VideoCapture`)
3. Backend streams annotated results back via WebSocket (detection overlays only, not raw frames)
4. Frontend renders results synchronized with local video playback

```python
class VideoProcessingService:
    async def process_video(self, file_path: Path, zone: Optional[Polygon], ws: WebSocket):
        cap = cv2.VideoCapture(str(file_path))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_interval = 1.0 / min(fps, 15)  # Cap at 15 FPS processing

        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            detections = self.inference.process_frame(frame)
            events = self.zone.evaluate(detections, frame.shape) if zone else []
            # Stream results (not frames) back to frontend
            await ws.send_json({
                "detections": [d.to_dict() for d in detections],
                "events": [e.to_dict() for e in events],
                "frame_number": int(cap.get(cv2.CAP_PROP_POS_FRAMES)),
            })
            await asyncio.sleep(frame_interval)
        cap.release()
```

### 4.2 Frontend Components

#### 4.2.1 Component Tree

```
<App>
  ├── <Header />           — App title, status indicators
  ├── <MainView>
  │   ├── <VideoSourcePanel />   — Webcam/upload toggle
  │   ├── <LiveFeedCanvas />     — Video + detection overlay + polygon overlay
  │   │   ├── <video> element
  │   │   ├── <canvas> detection layer
  │   │   └── <PolygonEditor />  — Click-to-draw polygon tool
  │   └── <StatusBar />          — FPS, connection status, model info
  └── <AlertsDashboard>
      ├── <AlertsList />         — Scrollable recent alerts
      └── <AlertDetailModal />   — Full alert info + saved frame preview
```

#### 4.2.2 WebSocket Client (`services/wsClient.ts`)

```typescript
// Key design: use binary frames for webcam, JSON messages for metadata
class DetectionWebSocket {
  private ws: WebSocket;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000; // doubles on each retry

  async sendFrame(frame: Blob, zoneConfig?: ZoneConfig): Promise<void> {
    // Send frame as binary
    this.ws.send(frame);
    // Send zone config as JSON (only when changed)
    if (zoneConfig && this.zoneChanged) {
      this.ws.send(JSON.stringify({ type: "zone_update", zone: zoneConfig }));
    }
  }

  private handleReconnect(): void {
    if (this.reconnectAttempts < this.maxReconnectAttempts) {
      setTimeout(() => {
        this.reconnectAttempts++;
        this.reconnectDelay *= 2;
        this.connect();
      }, this.reconnectDelay);
    }
  }
}
```

**Key improvements over original plan:**
- Use **binary WebSocket messages** for frames instead of base64-encoded JSON — reduces bandwidth by ~33%
- Send zone config **only when changed**, not with every frame
- Implement exponential backoff reconnection strategy
- Track connection state for UI status indicators

#### 4.2.3 PolygonEditor (`components/PolygonEditor.tsx`)

**Interaction model:**
1. Click on canvas to add vertices
2. Click near first vertex or double-click to close polygon
3. "Clear Zone" button resets polygon
4. Polygon persists across source switches (webcam ↔ upload)

**Coordinate handling:**
- Store all coordinates normalized (0–1 range)
- Convert to pixel coordinates only at render time
- Send normalized coordinates to backend

```typescript
interface PolygonPoint {
  x: number; // 0.0 to 1.0, relative to frame width
  y: number; // 0.0 to 1.0, relative to frame height
}

function normalizePoint(
  canvasX: number, canvasY: number,
  canvasWidth: number, canvasHeight: number
): PolygonPoint {
  return {
    x: canvasX / canvasWidth,
    y: canvasY / canvasHeight,
  };
}
```

#### 4.2.4 AlertsDashboard

**Data fetching strategy:**
- **Real-time alerts:** Pushed through the existing WebSocket connection (piggybacked on detection responses) — no polling needed
- **Historical alerts:** Fetched via `GET /alerts?limit=50` on dashboard mount and on manual refresh
- **Alert detail:** Fetched via `GET /alerts/{id}` on click, with frame loaded from `GET /frames/{filename}`

**Improvement over original plan:** Eliminate REST polling for recent alerts. Since we already have a WebSocket connection, new alerts are pushed in real-time as part of the detection response payload.

### 4.3 WebSocket Protocol

#### 4.3.1 Webcam Mode

```
Client → Server:
  Binary message: JPEG-encoded frame (canvas.toBlob("image/jpeg", 0.8))
  Text message:   {"type": "zone_update", "points": [[x1,y1], [x2,y2], ...]}
  Text message:   {"type": "zone_clear"}

Server → Client:
  Text message:   {
    "type": "detection_result",
    "detections": [
      {"track_id": 1, "class": "person", "bbox": [x1,y1,x2,y2], "confidence": 0.87}
    ],
    "events": [
      {"track_id": 1, "class": "person", "event_type": "enter", "alert_id": 42}
    ],
    "stats": {"fps": 12.3, "inference_ms": 45}
  }
```

#### 4.3.2 Upload Mode

```
Client → Server:
  POST /upload-video  (multipart file upload)
  WS text message:    {"type": "start_processing", "video_id": "abc123"}
  WS text message:    {"type": "zone_update", "points": [...]}

Server → Client:
  Text message:       {
    "type": "detection_result",
    "frame_number": 142,
    "detections": [...],
    "events": [...]
  }
  Text message:       {"type": "processing_complete", "total_frames": 3600}
```

---

## 5. Data Model

### 5.1 SQLite Schema

```sql
-- Core alerts table
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT (datetime('now')),
    track_id INTEGER NOT NULL,
    object_class TEXT NOT NULL CHECK(object_class IN ('person', 'vehicle', 'bicycle')),
    event_type TEXT NOT NULL CHECK(event_type IN ('enter', 'exit')),
    frame_path TEXT NOT NULL,
    confidence REAL,                    -- NEW: detection confidence score
    zone_name TEXT DEFAULT 'default',   -- NEW: future multi-zone support
    source_type TEXT DEFAULT 'webcam'   -- NEW: 'webcam' or 'upload'
);

-- Index for dashboard queries
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_class ON alerts(object_class);

-- Optional: session tracking for multi-user awareness
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at DATETIME NOT NULL DEFAULT (datetime('now')),
    source_type TEXT NOT NULL,
    status TEXT DEFAULT 'active'
);
```

**Improvements over original plan:**
- Added `confidence` column — useful for filtering and debugging
- Added `zone_name` — enables future multi-zone support without schema migration
- Added `source_type` — tracks whether alert came from webcam or uploaded video
- Added indexes for common query patterns
- Added CHECK constraints for data integrity
- Added optional `sessions` table for operational visibility

### 5.2 Pydantic Models

```python
class Detection(BaseModel):
    track_id: int
    object_class: Literal["person", "vehicle", "bicycle"]
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 (normalized)
    confidence: float

class ZoneEvent(BaseModel):
    track_id: int
    object_class: str
    event_type: Literal["enter", "exit"]
    timestamp: datetime
    timestamp_ms: int  # epoch ms, used for unique filenames

class Alert(BaseModel):
    id: Optional[int] = None
    timestamp: datetime
    track_id: int
    object_class: str
    event_type: str
    frame_path: str
    confidence: Optional[float] = None

class DetectionResponse(BaseModel):
    detections: list[Detection]
    events: list[ZoneEvent]
    stats: ProcessingStats
```

---

## 6. API Contracts

### 6.1 REST Endpoints

#### `GET /health`
Health check with model status.

**Response:**
```json
{
  "status": "healthy",
  "model_loaded": true,
  "model_name": "yolov8n",
  "uptime_seconds": 3421
}
```

#### `GET /alerts`
List recent alerts with pagination.

**Query Parameters:**
| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 50 | Max alerts to return |
| `offset` | int | 0 | Pagination offset |
| `class` | string | null | Filter by object class |
| `event_type` | string | null | Filter by enter/exit |

**Response:**
```json
{
  "alerts": [
    {
      "id": 42,
      "timestamp": "2026-04-21T14:32:01Z",
      "track_id": 7,
      "object_class": "person",
      "event_type": "enter",
      "frame_path": "1713712321000_7_enter.jpg",
      "confidence": 0.87
    }
  ],
  "total": 156,
  "limit": 50,
  "offset": 0
}
```

#### `GET /alerts/{id}`
Single alert detail.

#### `GET /frames/{filename}`
Serve saved frame image as static file.

#### `POST /upload-video`
Upload a video file for server-side processing.

**Request:** `multipart/form-data` with `file` field.
**Response:**
```json
{
  "video_id": "abc123",
  "filename": "traffic_clip.mp4",
  "duration_seconds": 120,
  "fps": 30,
  "status": "ready"
}
```

#### `DELETE /alerts`
Clear all alerts (useful during demo/testing).

### 6.2 WebSocket Endpoint

#### `WS /ws/detect`

Bidirectional WebSocket for real-time frame processing. See Section 4.3 for protocol details.

---

## 7. Key Technical Decisions

### 7.1 Decision Matrix

| # | Decision | Chosen Approach | Alternative Considered | Rationale |
|---|---|---|---|---|
| 1 | Inference location | Server-side (Python) | Browser (TF.js/ONNX.js) | YOLO ecosystem, tracking, zone logic all in Python; avoids WASM perf issues |
| 2 | Model weights | YOLOv8n (nano) | YOLOv8s, YOLOv11n | Best FPS on CPU; upgrade to v11n if compatible and faster |
| 3 | WebSocket format | Binary frames | Base64 in JSON | ~33% less bandwidth; native browser Blob support |
| 4 | Uploaded video processing | Server-side with OpenCV | Browser → WS → backend | Eliminates redundant decode/encode cycle; more robust |
| 5 | Zone library | Shapely | Manual ray-casting | Battle-tested, handles edge cases, minimal overhead |
| 6 | Database | SQLite + aiosqlite | PostgreSQL, MongoDB | Zero ops, async support, sufficient for demo scale |
| 7 | Deployment | ngrok tunnel (local) | Render/Railway/Fly.io | Guarantees FPS target; free cloud GPU is unreliable |
| 8 | Alert delivery | WebSocket push | REST polling | Already have WS connection; lower latency, no polling overhead |
| 9 | Coordinate system | Normalized (0–1) | Pixel coordinates | Resolution-independent; prevents resize bugs |
| 10 | Frame saving | Async (thread pool) | Synchronous | Non-blocking; inference throughput preserved |

### 7.2 Two Hard Decisions (Loom Talking Points)

**Hard Decision 1: Server-side inference vs browser-side**

- Browser-side (ONNX.js / TF.js) would eliminate the WebSocket bandwidth problem entirely
- But: tracking libraries don't exist in JS, zone logic would need porting, WASM YOLO performance is ~3x slower
- Tradeoff: accepted the bandwidth cost of sending frames to the server in exchange for a mature Python ML ecosystem and faster time to ship

**Hard Decision 2: ngrok vs cloud deployment**

- Cloud deployment (Render/Railway) would provide a stable URL and look more "production"
- But: free-tier instances have 512MB–1GB RAM, no GPU, cold starts, and YOLO on CPU at that tier likely can't sustain 10 FPS
- Tradeoff: chose ngrok for demo reliability (guaranteed FPS on local hardware) at the cost of requiring the host machine to stay online

---

## 8. Performance Design

### 8.1 FPS Budget

Target: **≥10 FPS end-to-end** on a 2020+ laptop with no discrete GPU.

| Stage | Budget (ms) | Notes |
|---|---|---|
| Frame capture + encode (browser) | ~10ms | canvas.toBlob JPEG at 0.8 quality |
| WebSocket send/receive | ~5ms | Local or LAN; higher over ngrok |
| Frame decode (server) | ~2ms | cv2.imdecode from JPEG bytes |
| YOLO inference | ~50–70ms | YOLOv8n at 640px on CPU |
| Tracking | ~2ms | ByteTrack is lightweight |
| Zone evaluation | ~1ms | Shapely point-in-polygon |
| JSON serialization | ~1ms | |
| **Total per frame** | **~71–91ms** | **~11–14 FPS** ✅ |

### 8.2 Optimization Levers (in priority order)

1. **Input resolution:** Resize to 640px max dimension before inference (default YOLO behavior)
2. **Class filtering:** Only process target COCO classes (0, 1, 2, 3, 5, 7) — reduces NMS overhead
3. **Frame skipping:** If inference takes >100ms, skip the next frame to maintain perceived smoothness
4. **JPEG quality:** Reduce to 0.7 if bandwidth is a bottleneck
5. **Confidence threshold:** Raise from 0.4 to 0.5 to reduce post-processing work
6. **Model swap:** Try YOLOv11n if it's faster on the target hardware
7. **ONNX Runtime:** Export to ONNX format and use `onnxruntime` for potential 20–30% speedup on CPU

### 8.3 Backpressure Handling

```
Frontend frame capture loop:
  - Capture frame
  - If previous frame response not yet received → skip this frame
  - Send frame
  - Wait for response
  - Render detections
```

This ensures the frontend never queues frames faster than the backend can process them. The perceived FPS equals the actual inference FPS.

---

## 9. Security and Reliability

### 9.1 Security Considerations (Demo Scope)

| Concern | Mitigation |
|---|---|
| CORS | Configure FastAPI CORS middleware to allow frontend origin |
| File upload size | Limit uploaded videos to 100MB via middleware |
| Path traversal (frame serving) | Serve frames via FastAPI `StaticFiles` with restricted base directory |
| WebSocket abuse | Limit to 1 concurrent WebSocket per IP (simple counter) |
| ngrok exposure | Use ngrok's basic auth or auth token for demo access |

### 9.2 Error Handling Strategy

- **WebSocket disconnection:** Frontend shows "Reconnecting..." banner, attempts exponential backoff (1s, 2s, 4s, 8s, 16s, give up)
- **Model load failure:** Backend returns 503 on health check, frontend shows clear error state
- **Invalid video upload:** Return 422 with descriptive error message
- **Frame save failure:** Log error, continue processing (alert saved without frame, `frame_path` set to null)
- **Polygon with <3 points:** Frontend disables zone; backend ignores invalid polygons

### 9.3 Resource Cleanup

- **Uploaded videos:** Delete after processing completes (or after 1 hour)
- **Old frame snapshots:** Optional cleanup cron for frames older than 24 hours
- **Tracker state:** Reset when video source changes or after 5 minutes of no detections
- **WebSocket:** Server-side timeout after 30 seconds of no frames received

---

## 10. Deployment Strategy

### 10.1 Primary: Local + ngrok

```bash
# Terminal 1: Backend
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Terminal 2: Frontend
cd frontend
npm run build && npx serve dist -l 3000

# Terminal 3: ngrok tunnel
ngrok http 3000 --scheme=https
```

**Nginx or Caddy reverse proxy** (optional): If both frontend and backend need to be behind a single ngrok URL, use a reverse proxy that routes `/api/*` and `/ws/*` to port 8000 and everything else to port 3000.

**Simpler alternative:** Run the FastAPI backend serve the built frontend static files directly:

```python
# In main.py
app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="frontend")
```

This means only one port (8000) and one ngrok tunnel needed. **This is the recommended approach.**

### 10.2 Backup: Docker + Cloud

If time permits after core demo is stable:

```dockerfile
# Multi-stage Dockerfile
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/ .
RUN npm ci && npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/ ./backend/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist
RUN pip install -r backend/requirements.txt
EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Deploy to Render (free tier) or Railway. Document any FPS limitations.

### 10.3 Environment Variables

```env
# Backend
MODEL_NAME=yolov8n.pt          # YOLO model weights
CONFIDENCE_THRESHOLD=0.4        # Detection confidence cutoff
DEBOUNCE_FRAMES=3               # Zone transition debounce
MAX_UPLOAD_SIZE_MB=100           # Upload limit
FRAMES_DIR=storage/frames        # Snapshot directory
DATABASE_URL=sqlite:///storage/neurawatch.db

# Frontend (build-time)
VITE_WS_URL=ws://localhost:8000/ws/detect
VITE_API_URL=http://localhost:8000
```

---

## 11. Testing Strategy

### 11.1 Test Pyramid

```
           ┌────────┐
           │  E2E   │  Manual: webcam + upload full-flow
          ┌┴────────┴┐
          │Integration│ API tests: WebSocket, REST endpoints
         ┌┴──────────┴┐
         │   Unit      │ Services: inference, zone, alert
        └──────────────┘
```

### 11.2 Unit Tests (pytest)

| Module | Test Cases |
|---|---|
| `inference_service` | Class normalization maps correctly; unknown classes are filtered; empty frame handled |
| `zone_service` | Point inside polygon → True; point outside → False; enter transition fires event; exit transition fires event; debounce prevents flicker; polygon with <3 points rejected; normalized coords handled correctly |
| `alert_service` | Alert is persisted; frame is saved; concurrent alerts don't collide filenames |
| `db` | Insert alert; query recent alerts; pagination works; filtering by class works |

### 11.3 Integration Tests

| Test | Description |
|---|---|
| WebSocket round-trip | Send a frame, receive detection JSON |
| Upload → process → alerts | Upload video, trigger zone events, verify alerts in DB |
| REST alert endpoints | Create alerts, list them, fetch detail, fetch frame |
| Static file serving | Saved frames are accessible via HTTP |

### 11.4 Manual QA Checklist

- [ ] Webcam stream works in Chrome and Firefox
- [ ] Uploaded MP4 video plays and is processed
- [ ] Person detection appears with correct labels
- [ ] Vehicle detection works for car, truck, bus, motorcycle
- [ ] Bicycle detection works
- [ ] Track IDs remain stable for a walking person
- [ ] Polygon can be drawn by clicking vertices
- [ ] Polygon can be closed (click first vertex or double-click)
- [ ] Polygon can be cleared/reset
- [ ] Enter event fires when person walks into zone
- [ ] Exit event fires when person walks out of zone
- [ ] No duplicate alerts for same object staying in zone
- [ ] Alert appears in dashboard list in real-time
- [ ] Clicking alert shows saved frame in modal
- [ ] Alerts persist after page refresh
- [ ] FPS indicator shows ≥10 FPS on laptop
- [ ] App works via ngrok link from a different device
- [ ] Uploaded video processed server-side (no browser decode overhead)

---

## 12. Risks and Mitigations

### 12.1 Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | FPS below 10 on target hardware | Medium | High | Use nano model, lower resolution, frame skip, ONNX export |
| R2 | Tracking instability causes false alerts | Medium | Medium | Debounce (3 frames), bottom-center anchor, tune confidence |
| R3 | WebSocket latency over ngrok | Low | Medium | Compress frames, limit FPS, run backend locally |
| R4 | Browser getUserMedia denied/unsupported | Low | Medium | Clear error UI, fallback to upload-only mode |
| R5 | SQLite concurrent write contention | Low | Low | Demo is single-user; use WAL mode for safety |
| R6 | Large uploaded video OOMs server | Low | Medium | Limit upload size, process frame-by-frame (no full load) |
| R7 | Model download fails on first run | Low | Medium | Pre-download weights in setup script, cache in repo or Docker |
| R8 | Time crunch — can't finish all features | Medium | High | Prioritize core loop first; dashboard and polish are last |

### 12.2 Contingency Plans

- **If FPS is too low:** Drop to 7–8 FPS and document the limitation; most reviewers won't count frames
- **If tracking is too unstable:** Disable debounce, accept some false alerts, explain in Loom
- **If cloud deployment is needed and local won't work:** Use Google Colab + ngrok as a GPU-backed alternative
- **If time runs out before dashboard:** Serve alerts as a simple HTML table rendered by FastAPI (Jinja2), skip React dashboard

---

## 13. Feasibility Review

### 13.1 Assessment of Original Plan

The original PROJECT_PLAN.md is **well-structured and largely feasible**. Below is a detailed assessment:

#### ✅ What's Solid

| Aspect | Assessment |
|---|---|
| Stack choice (FastAPI + React + YOLO) | Excellent — optimal for speed-to-ship and ML compatibility |
| Phase ordering | Correct — backend inference first, then frontend, then integration |
| Nano model selection | Right call — YOLOv8n is the fastest path to 10 FPS on CPU |
| SQLite decision | Perfect for demo scope — zero ops overhead |
| ngrok deployment | Pragmatic — guarantees FPS that cloud free tiers can't |
| Class normalization | Well thought out — COCO IDs to business categories |
| Testing checklist | Comprehensive and covers all requirements |

#### ⚠️ Feasibility Concerns

| # | Concern | Severity | Detail |
|---|---|---|---|
| F1 | Uploaded video via WebSocket | **High** | Streaming decoded frames from browser → WS → backend for uploaded files is wasteful. The video is already a file — process it server-side. |
| F2 | Base64 frame encoding | **Medium** | Plan implies JSON-based WebSocket. Base64 adds 33% overhead. Use binary WebSocket messages. |
| F3 | No coordinate normalization mentioned | **Medium** | Polygon coordinates will break when canvas size ≠ frame size unless normalized. |
| F4 | No backpressure handling | **Medium** | If frontend sends frames faster than backend processes them, the WebSocket buffer grows unbounded. |
| F5 | Synchronous frame saving | **Low** | Saving JPEG to disk during inference blocks the processing loop. Should be async. |
| F6 | No CORS configuration mentioned | **Low** | Will cause immediate dev friction. Trivial to add but easy to forget. |
| F7 | No health check endpoint | **Low** | Useful for deployment validation and monitoring. |
| F8 | No reconnection strategy detailed | **Low** | WebSocket drops are inevitable, especially over ngrok. |

#### ❌ Missing from Original Plan

| # | Missing Element | Impact |
|---|---|---|
| M1 | Server-side video processing path | Must-have for uploaded videos |
| M2 | WebSocket protocol specification | Needed for frontend-backend contract |
| M3 | Backpressure / flow control design | Prevents memory issues under load |
| M4 | Coordinate normalization strategy | Prevents polygon misalignment bugs |
| M5 | Docker support | Nice-to-have for reproducible setup |
| M6 | Confidence threshold configuration | Affects detection quality significantly |
| M7 | Error handling patterns | Needed for demo robustness |
| M8 | Resource cleanup strategy | Prevents disk/memory issues in long sessions |

---

## 14. Improvements Over Original Plan

### 14.1 Critical Improvements (Must Implement)

#### 1. Server-Side Video Processing

**Original:** Frontend sends uploaded video frames through WebSocket
**Improved:** Backend processes uploaded videos directly with OpenCV

**Impact:** Eliminates redundant decode/encode cycle, reduces bandwidth, enables offline processing, improves reliability.

#### 2. Binary WebSocket Messages

**Original:** Implied JSON with base64-encoded frames
**Improved:** Binary messages for frames, text messages for metadata

**Impact:** ~33% bandwidth reduction for frame data.

#### 3. Normalized Coordinate System

**Original:** Not specified
**Improved:** All polygon coordinates stored as 0–1 normalized values

**Impact:** Prevents polygon misalignment across different resolutions and display sizes.

#### 4. Backpressure / Flow Control

**Original:** Not addressed
**Improved:** Frontend waits for response before sending next frame

**Impact:** Prevents memory growth, ensures perceived FPS = actual FPS.

#### 5. Async Frame Saving

**Original:** Not specified (implied synchronous)
**Improved:** Frame snapshots saved via `asyncio.to_thread`

**Impact:** Inference pipeline never blocked by disk I/O.

### 14.2 Recommended Improvements (Should Implement If Time Allows)

#### 6. Use Shapely for Point-in-Polygon

Instead of manual ray-casting, use `shapely.geometry.Polygon.contains()`. Battle-tested, handles edge cases (points on edges, degenerate polygons), minimal overhead.

#### 7. Single-Port Deployment

Serve built frontend static files from FastAPI directly. One port, one ngrok tunnel, simpler setup.

#### 8. Health Check Endpoint

`GET /health` returning model status, uptime, and active connections. Essential for deployment validation.

#### 9. Alert Push via WebSocket

Push new alerts through the existing WebSocket connection instead of requiring REST polling. Already have the connection — use it.

#### 10. Docker Compose for Development

```yaml
services:
  backend:
    build: ./backend
    ports: ["8000:8000"]
    volumes: ["./backend/storage:/app/storage"]
  frontend:
    build: ./frontend
    ports: ["3000:3000"]
```

### 14.3 Stretch Improvements (Nice to Have)

#### 11. Supervision Library Integration

[Roboflow's `supervision`](https://github.com/roboflow/supervision) library provides:
- `sv.PolygonZone` — production-grade zone detection
- `sv.ByteTrack` — tracking with built-in state management
- `sv.BoundingBoxAnnotator` — clean visualization for saved frames

Could replace significant custom code in ZoneService and frame annotation.

#### 12. ONNX Runtime Export

Export YOLOv8n to ONNX and use `onnxruntime` for inference. Potential 20-30% speedup on CPU without changing any other code.

#### 13. WebSocket Compression

Enable `permessage-deflate` WebSocket extension for additional bandwidth savings. FastAPI/Starlette supports this natively.

---

## 15. Implementation Roadmap

### 15.1 Day-by-Day Plan

> Assuming start Monday 2026-04-20, deadline Friday 2026-04-24 EOD CDMX.

#### Day 1 (Monday): Foundation + Inference Pipeline

| Task | Time | Notes |
|---|---|---|
| Create repo, scaffold backend + frontend | 1h | Phase 1 |
| Integrate YOLOv8n, test on sample images | 1.5h | Validate FPS |
| Implement class normalization + tracking | 1h | |
| Build InferenceService with clean API | 1h | |
| Set up WebSocket endpoint (echo test) | 0.5h | |
| **Goal:** Backend can process frames and return detections | | |

#### Day 2 (Tuesday): Frontend + Live Connection

| Task | Time | Notes |
|---|---|---|
| Webcam capture with getUserMedia | 1h | |
| WebSocket client with binary frames | 1h | |
| Canvas overlay for bounding boxes + labels | 1.5h | |
| FPS counter and connection status | 0.5h | |
| Upload video + server-side processing | 1.5h | New path |
| **Goal:** Live detection overlay working for webcam and upload | | |

#### Day 3 (Wednesday): Zones + Alerts

| Task | Time | Notes |
|---|---|---|
| PolygonEditor component | 1.5h | Click-to-draw |
| ZoneService with Shapely + debounce | 1.5h | |
| AlertService + SQLite schema | 1h | |
| Frame snapshot saving (async) | 0.5h | |
| Wire zone events through WebSocket | 1h | |
| **Goal:** Enter/exit alerts fire and persist with saved frames | | |

#### Day 4 (Thursday): Dashboard + Polish

| Task | Time | Notes |
|---|---|---|
| AlertsList component with real-time push | 1h | |
| AlertDetailModal with frame preview | 1h | |
| REST endpoints for alerts + frames | 1h | |
| Performance tuning pass | 1h | Measure and optimize |
| Bug fixes from end-to-end testing | 1.5h | |
| **Goal:** Complete feature set working end-to-end | | |

#### Day 5 (Friday): Deploy + Deliverables

| Task | Time | Notes |
|---|---|---|
| Single-port deployment setup | 0.5h | FastAPI serves frontend |
| ngrok tunnel + remote verification | 0.5h | |
| README with setup + architecture | 1h | |
| One-page delivery doc | 1h | |
| Loom outline + record | 1.5h | |
| Final QA pass | 1h | |
| **Goal:** All deliverables shipped | | |

### 15.2 Critical Path

```
InferenceService → WebSocket handler → Frontend canvas overlay
                                           ↓
                                    PolygonEditor → ZoneService → AlertService
                                                                      ↓
                                                               Dashboard UI
                                                                      ↓
                                                               Deployment + Docs
```

If any item on this path blocks, everything downstream shifts. The highest risk is Day 1 inference performance — if FPS is too low on the target hardware, investigate ONNX export immediately.

---

## Appendix A: Dependency List

### Backend (`requirements.txt`)

```
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
ultralytics>=8.1.0
opencv-python-headless>=4.9.0
numpy>=1.26.0
shapely>=2.0.0
aiosqlite>=0.20.0
python-multipart>=0.0.9
websockets>=12.0
pydantic>=2.6.0
```

### Frontend (`package.json` key deps)

```json
{
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "typescript": "^5.4.0",
    "vite": "^5.2.0",
    "@vitejs/plugin-react": "^4.2.0"
  }
}
```

---

## Appendix B: COCO Class ID Reference

| COCO ID | Class Name | NeuraWatch Category |
|---|---|---|
| 0 | person | `person` |
| 1 | bicycle | `bicycle` |
| 2 | car | `vehicle` |
| 3 | motorcycle | `vehicle` |
| 5 | bus | `vehicle` |
| 7 | truck | `vehicle` |

All other COCO classes are filtered out at inference time.

---

## Appendix C: File Structure

```
neurawatch/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app, CORS, static files, startup
│   │   ├── config.py            # Environment config (Pydantic Settings)
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── routes_ws.py     # WebSocket endpoint
│   │   │   ├── routes_alerts.py # REST alert endpoints
│   │   │   └── routes_upload.py # Video upload endpoint
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── inference_service.py
│   │   │   ├── zone_service.py
│   │   │   ├── alert_service.py
│   │   │   └── video_service.py  # Server-side video processing
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   └── schemas.py       # Pydantic models
│   │   └── db.py                # SQLite async database layer
│   ├── storage/
│   │   └── frames/              # Saved alert frame snapshots
│   ├── tests/
│   │   ├── test_inference.py
│   │   ├── test_zone.py
│   │   ├── test_alerts.py
│   │   └── test_api.py
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── components/
│   │   │   ├── VideoSourcePanel.tsx
│   │   │   ├── LiveFeedCanvas.tsx
│   │   │   ├── PolygonEditor.tsx
│   │   │   ├── AlertsList.tsx
│   │   │   ├── AlertDetailModal.tsx
│   │   │   └── StatusBar.tsx
│   │   ├── hooks/
│   │   │   ├── useWebSocket.ts
│   │   │   ├── useWebcam.ts
│   │   │   └── useAlerts.ts
│   │   ├── services/
│   │   │   ├── wsClient.ts
│   │   │   └── api.ts
│   │   └── types/
│   │       └── index.ts
│   ├── package.json
│   ├── tsconfig.json
│   └── vite.config.ts
├── docs/
│   ├── delivery-notes.md        # One-page doc
│   └── loom-outline.md          # Loom talking points
├── docker-compose.yml           # Optional
├── Dockerfile                   # Optional
├── .gitignore
└── README.md
```

---

## Appendix D: Scaling Considerations (for One-Page Doc)

**What it would take to scale to 200 concurrent video feeds:**

| Area | Current (Demo) | At Scale (200 feeds) |
|---|---|---|
| Compute | Single process, CPU inference | GPU cluster (NVIDIA T4/A10), batch inference, multiple workers |
| Load balancing | None | NGINX/HAProxy routing WS connections to inference workers |
| Database | SQLite (single file) | PostgreSQL with connection pooling (PgBouncer) |
| Frame storage | Local disk | S3/GCS with CDN for frame serving |
| Message passing | In-process | Redis Pub/Sub or Kafka for alert event streaming |
| Tracking state | In-memory dict | Redis for distributed tracker state per session |
| Monitoring | Console logs | Prometheus + Grafana, structured logging (JSON) |
| Model serving | Ultralytics Python | Triton Inference Server or TorchServe for GPU batching |
| Cost estimate | $0 (local laptop) | ~$2,000–5,000/month (cloud GPU instances + storage) |
