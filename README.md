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

Full setup instructions land with the backend (**NW-1002**) and frontend (**NW-1003**) scaffolds. End-to-end demo instructions and the ngrok re-tunnel command ship with **NW-1601**.

Prerequisites:
- Python 3.11+
- Node 20+
- ngrok (with authtoken)

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for branching, commit, and PR conventions.

## Plan and scope

- [PROJECT_PLAN.md](./PROJECT_PLAN.md) — ratified plan, architecture decisions, day-by-day sequencing
- [JIRA_TICKETS.md](./JIRA_TICKETS.md) — backlog with tightened acceptance criteria
- [TECHNICAL_DESIGN_DOCUMENT.md](./TECHNICAL_DESIGN_DOCUMENT.md) — deeper technical reference
