# Handoff: NeuraWatch — Real-Time Object Monitoring UI

## Overview
NeuraWatch is a browser-based real-time object detection & zone-monitoring tool. Users connect a webcam or upload a video, draw a polygonal zone on the feed, and receive `enter` / `exit` alerts whenever tracked objects (people, vehicles, bicycles) cross the zone. Detections are rendered as bounding boxes over a live video feed; alerts stream into a right-side panel in newest-first order and can be expanded to show the saved frame and event metadata.

This handoff covers the **frontend UI layer only**. Backend (YOLO + ByteTrack + WebSocket server) is specified separately — the FE must conform to the WS protocol described in §6.

---

## About the Design Files

The files in `design_handoff_neurawatch/` are **design references created in HTML** — a long-scroll specification document (`Frontend Handoff Spec.html` + `handoff.css`) showing tokens, components, annotated screens, and interaction contracts. **They are not production code to copy directly.**

Your task is to **recreate these designs in the target codebase's environment** — if the project already uses React + TypeScript + Vite (as the spec assumes), implement there. If it uses a different stack, translate the tokens, components, and interactions into that stack's idioms using its existing libraries and patterns. If no frontend environment exists yet, bootstrap with **React + TypeScript + Vite**.

**The HTML spec itself is rendered for reading** — visible only as a handoff document. Do not bundle it into the app.

---

## Fidelity

**High-fidelity (hifi).** The spec defines exact hex values, font stacks, pixel spacing, border radii, and interaction timing. Recreate the UI pixel-perfectly. Every value below should land as a named CSS custom property or token constant — no magic numbers in component code.

---

## Stack (recommended)

- **Framework:** React 18 + TypeScript
- **Build:** Vite
- **State:** Single `useReducer` at App root (no Redux, no Zustand for MVP)
- **Styling:** CSS Modules or vanilla CSS with custom properties (no CSS-in-JS, no Tailwind unless already in repo)
- **Canvas:** Native `<canvas>` + `requestAnimationFrame` for detection overlay (no react-konva)
- **WS:** Native `WebSocket` wrapped in a module-level singleton (`services/wsClient.ts`)
- **Fonts:** Google Fonts — Space Grotesk, Inter, JetBrains Mono

---

## Design Tokens

Ship as CSS custom properties on `:root`.

### Color
```css
:root {
  /* Surfaces (darkest → lightest) */
  --bg-0: #0A0F14;   /* page floor */
  --bg-1: #0F1620;   /* app surface, alerts panel */
  --bg-2: #131C27;   /* buttons, hover rows, chips */
  --bg-3: #1A2431;   /* modals, drawer, insets */
  --bg-4: #22303F;   /* dropdown, highest */

  /* Ink (primary → subtle) */
  --ink-0: #F2F6FA;  /* headings, values */
  --ink-1: #C9D3DE;  /* body copy */
  --ink-2: #8FA0B3;  /* help text, captions */
  --ink-3: #5F7082;  /* eyebrows, timestamps */
  --ink-4: #3E4C5C;  /* disabled */

  /* Accents */
  --cyan:    #18E1D9;  /* primary — live state, bboxes, CTAs, focus */
  --cyan-d:  #0FB3AC;  /* cyan hover */
  --amber:   #F5A524;  /* in-zone, exit events, disconnected */
  --violet:  #A78BFA;  /* enter events, processing */
  --green:   #22C55E;  /* success */
  --red:     #F43F5E;  /* error */

  /* Hairlines */
  --line:    rgba(255,255,255,0.06);
  --line-2:  rgba(255,255,255,0.10);
  --line-3:  rgba(255,255,255,0.18);
}
```

### Typography
- **Display:** `Space Grotesk`, system-ui, sans-serif — headings, titles, UI labels
- **Body:** `Inter`, system-ui, sans-serif — paragraph, buttons
- **Mono:** `JetBrains Mono`, ui-monospace, monospace — FPS, track IDs, timestamps, eyebrows

| Style | Family | Size / Line | Weight | Usage |
|---|---|---|---|---|
| Display L | Space Grotesk | 48 / 1.1 | 500 | Empty-state hero |
| Display M | Space Grotesk | 32 / 1.15 | 500 | Drawer title |
| Title | Space Grotesk | 18 / 1.3 | 500 | Panel titles ("Alerts") |
| Label | Space Grotesk | 15 / 1.3 | 500 | App name, step numbers |
| Body | Inter | 14 / 1.5 | 400 | Paragraph |
| UI / Button | Inter | 13 / 1.3 | 500 | Buttons, class names |
| Mono · data | JetBrains Mono | 12 / 1.4 | 500 | FPS, IDs, confidence |
| Mono · eyebrow | JetBrains Mono | 11 / 1.4 | 500 · letter-spacing 0.14em · uppercase | Section eyebrows |

Titles use `letter-spacing: -0.005em`. Body copy uses `letter-spacing: 0`. Never apply letter-spacing to body copy below 14px.

### Spacing · Radii · Shadows
```css
:root {
  --sp-1: 4px;  --sp-2: 8px;  --sp-3: 12px;
  --sp-4: 16px; --sp-5: 20px; --sp-6: 24px; --sp-8: 32px;

  --r-sm: 2px;   /* bboxes */
  --r-md: 6px;   /* buttons, chips, badges */
  --r-lg: 8px;   /* cards, drawer, modal */
  --r-xl: 12px;  /* feed panel */

  --shadow-sm:    0 2px 8px rgba(0,0,0,0.25);
  --shadow-md:    0 8px 24px rgba(0,0,0,0.4);
  --shadow-focus: 0 0 0 2px var(--cyan);
}
```

### Motion
```css
:root {
  --d-fast:  140ms;   /* hover, button press */
  --d-base:  200ms;   /* drawer, panel show/hide */
  --d-slow:  2000ms;  /* new-alert tint fade */
  --d-pulse: 1400ms;  /* status-dot pulse */
}
```

No spring physics. No transitions longer than 2s. `prefers-reduced-motion` disables status-dot pulse and new-alert tint.

---

## Components (13 primitives)

All components live in `frontend/src/components/`. File names:

| # | Component | File | Props / State |
|---|---|---|---|
| 01 | `<AppHeader>` | `AppHeader.tsx` | `status, fps, sourceLabel, onReset` |
| 02 | `<StatusBadge>` | `StatusBadge.tsx` | `state` (6 states) |
| 03 | `<VideoSourceSelector>` | `VideoSourcePanel.tsx` | `source, onChange, onUpload` |
| 04 | `<LiveFeedPanel>` | `LiveFeedCanvas.tsx` | `mode, fps, zoneState, detections` |
| 05 | `<DetectionCanvas>` | `LiveFeedCanvas.tsx` | `detections, polygon, zoneVersion` (rAF-driven) |
| 06 | `<PolygonEditor>` | `PolygonEditor.tsx` | `points, onAdd, onClose, onClear` |
| 07 | `<PolygonToolbar>` | `PolygonEditor.tsx` | `canClose, onDraw, onClose, onClear` |
| 08 | `<MonitoringControls>` | `VideoSourcePanel.tsx` | `running, onStart, onStop` |
| 09 | `<AlertsPanel>` | `AlertsPanel.tsx` | `alerts, selectedId, onSelect` (virtualized) |
| 10 | `<AlertListItem>` | `AlertsPanel.tsx` | `alert, isNew, selected` |
| 11 | `<AlertDetailDrawer>` | `AlertsPanel.tsx` | `alertId, open, onClose` (focus-trapped dialog) |
| 12 | `<EmptyState>` | `EmptyState.tsx` | `onSelectWebcam, onSelectUpload` |
| 13 | `<SystemNotice>` | `StatusBar.tsx` | `tone, message, dismissMs` |

---

## Screens

### 1. Empty / Setup (route `/`)
- Shown when no source connected. StatusBadge is `idle`.
- Hero: "Connect a source to begin monitoring." (Display L 48px)
- 3-step guide cards (bg-2, radius 8, padding 24). Only Step 01 eyebrow is cyan; 02/03 are ink-3.
- Two full-width CTAs: **Use webcam** (primary cyan) + **Upload video** (secondary). Upload accepts `video/mp4,video/quicktime`, ≤30s, ≤100MB.

### 2. Live Monitoring · default (route `/monitor`)
- Layout: **70/30 split** — feed column `1fr`, alerts column fixed `360px`.
- App header 64px tall, padding-x 24. Center group: StatusBadge + FPS readout + source tag.
- Feed panel: 4:3 aspect, radius 12, padding 20 around, with floating badges (Source/Model/FPS) at 14,14 offset from the panel.
- Detection boxes: 2px solid cyan, radius 2. Label mono 10/600, ink `#051F1E` on cyan fill, positioned above the bbox.
- Polygon overlay: fill `rgba(24,225,217,0.06)`, stroke cyan 2px. Vertices as 4px cyan dots.
- Control strip below feed: Source segmented · Monitoring (Pause/Stop) · Zone (Redraw/Clear).
- Alerts panel: right column, `border-left: var(--line-2)`, header 56px.

### 3. Live Monitoring · drawing polygon
- Cursor `crosshair` over feed. StatusBadge switches to `processing` with label "Drawing zone".
- Rubber-band preview: dashed `6 4`, stroke 1.5, cyan. Follows cursor on `mousemove`. Hidden on `mouseleave`.
- Vertices: 5px filled cyan + 2px `--bg-1` stroke ring.
- Bottom-center hint pill: "Click to add vertex · Close Zone when ready · Esc to cancel".
- **Close Zone** button: primary cyan, disabled until `points.length >= 3`. On click: appends first point, emits `zone_update` with bumped `zone_version`.

### 4. Live Monitoring · alert firing
- In-zone bbox: border switches to amber, width 3px. 200ms outer glow `0 0 16px rgba(245,165,36,0.5)` at event time.
- Feed-panel inset ring pulse: `box-shadow: inset 0 0 0 2px rgba(245,165,36,0.5)` fading to transparent over 200ms.
- New alert row: bg `rgba(24,225,217,0.06)` for 2s (token `--d-slow`), timestamp colored cyan. Fades linearly.

### 5. Alert Detail Drawer
- Slides in from right over alerts panel. `transform: translateX(100%) → 0` @ 200ms ease-out. Alerts list behind dims to 30% opacity.
- Fixed width 440px. Header: eyebrow "ALERT DETAIL" + display title "Event #xxxx · enter". Close button (✕).
- Saved frame at top: 4:3 aspect, radius 8, with bbox re-drawn in amber.
- Metadata grid 2×3: Event · Class · Track ID · Confidence · Timestamp (span 2) · Source (span 2).
- Actions: **Download frame** (blob from `/frames/{filename}`) + **Copy event JSON** (`navigator.clipboard.writeText`).
- Esc closes. Focus trapped. On close, focus returns to invoking row.

---

## System States (7 states in MVP)

All managed by `useReducer` in `App.tsx`.

| State | Trigger | Visual |
|---|---|---|
| `idle` | App boot, no source | StatusBadge idle (static ink-2 dot) · Empty state |
| `model-loading` | WS open, first `stats` not yet received | Spinner dot · Start disabled · "Loading model" |
| `live` | First `detection_result` received | Pulsing cyan dot · FPS shown |
| `processing` | Upload mode active | Violet pulsing · progress bar `frame N / total` |
| `disconnected` | WS `close` or 2s watchdog | Amber dot · feed pauses last frame · auto-retry once |
| `camera-denied` | `NotAllowedError` from `getUserMedia` | Red dot · recovery CTA to upload instead |
| `error` | WS code 1011 or `/health` fail | Red dot · "Reset Demo" CTA required |

### Reducer actions
```ts
export type AppStatus =
  | 'idle' | 'model-loading' | 'live' | 'processing'
  | 'disconnected' | 'camera-denied' | 'error';

export type Action =
  | { type: 'ws/open' }
  | { type: 'ws/first-frame' }              // → live
  | { type: 'ws/close'; retry: boolean }    // → disconnected | error
  | { type: 'upload/start'; videoId: string } // → processing
  | { type: 'upload/complete' }             // → idle
  | { type: 'media/denied' }                // → camera-denied
  | { type: 'zone/update'; points: Point[]; version: number }
  | { type: 'zone/clear' }
  | { type: 'alert/new'; alert: Alert }
  | { type: 'session/reset' };              // → idle, clears alerts + zone
```

### Sub-states (not status changes, but UI affordances)
- **No polygon drawn** — detections run but alerts paused. Show inline hint "⚑ Draw a zone to enable alerts".
- **No alerts yet** — empty state in alerts panel with dashed circle placeholder.

---

## WebSocket Protocol

### Singleton client (`services/wsClient.ts`)
Module-level singleton with in-flight boolean + 2s watchdog.

```ts
let inFlight = false;
let lastSeq = 0;
let seq = 0;
let watchdog: ReturnType<typeof setTimeout>;

function sendFrame(jpegBlob: Blob) {
  if (inFlight) return;                   // drop: server still processing
  inFlight = true;
  seq++;

  ws.send(JSON.stringify({ type: 'frame_meta', seq }));
  ws.send(jpegBlob);

  clearTimeout(watchdog);
  watchdog = setTimeout(() => {
    inFlight = false;
    dispatch({ type: 'ws/close', retry: true });
  }, 2000);
}

ws.onmessage = (e) => {
  if (typeof e.data === 'string') {
    const msg = JSON.parse(e.data);
    if (msg.type === 'detection_result') {
      if (msg.seq <= lastSeq) return;     // drop stale
      lastSeq = msg.seq;
      inFlight = false;
      clearTimeout(watchdog);
      dispatch({ type: 'ws/frame', payload: msg });
    }
  }
};
```

### Payload → UI mapping
| WS payload | → UI element | Notes |
|---|---|---|
| `detections[].bbox [x1,y1,x2,y2]` | `<DetectionCanvas>` box | Normalized 0–1 vs 640×480 processed frame |
| `detections[].track_id` | Label above bbox (prefix `#`) | Mono 10px |
| `detections[].class` | Label above bbox | Lowercased; `motorcycle` → `moto` if tight |
| `detections[].confidence` | Label suffix `· 0.94` | Hide when bbox width < 80px |
| `events[].event_type` | `<AlertListItem>` chip | `enter`→violet, `exit`→amber |
| `events[].alert_id` | row key + dedup | Dedup against REST-fetched list |
| `stats.fps` | Header FPS readout | Update every 500ms tick (EMA) |
| `stats.inference_ms` | Dev tooltip | Hidden unless `?debug=1` |
| `zone_version` | Polygon render gate | Only render detections whose frame matches current zone_version |
| `pts_ms` (upload) | `<video>.currentTime` | Step `currentTime = pts_ms/1000` |
| `processing_complete` | Status → idle + toast | `<SystemNotice>`: "Processing complete · N alerts" |

### Coordinate contract
**Single source of truth: 0–1 normalized against 640×480 processed frame.** Never store pixel coords in app state. Always normalize on send, denormalize on render.

```ts
function renderBbox(bbox: [number,number,number,number], canvasW: number, canvasH: number) {
  const [x1, y1, x2, y2] = bbox;
  ctx.strokeRect(x1 * canvasW, y1 * canvasH, (x2 - x1) * canvasW, (y2 - y1) * canvasH);
}

function capturePoint(e: MouseEvent, canvasEl: HTMLCanvasElement): [number, number] {
  const r = canvasEl.getBoundingClientRect();
  return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
}
```

---

## Responsive

| Breakpoint | Behavior |
|---|---|
| `≥ 1440px` | **Target.** 70/30 split. Alerts 360px. Drawer 440px overlays alerts. |
| `1200 – 1439px` | 70/30 holds. Alerts min 320px. Feed-badge labels abbreviate below 1260px. |
| `900 – 1199px` | Alerts collapse to drawer. Trigger via header button with unread badge. |
| `640 – 899px` | Stack vertically: header → feed → controls → alerts full-width. 44px tap targets. |
| `< 640px` | **Out of scope.** Block with message: "NeuraWatch requires a viewport of at least 640 px." |

---

## Accessibility (WCAG AA minimum)

- **Status never color-only.** Every badge has color + text + icon/dot + animation state (pulse vs. static).
- **Focus rings** on every interactive element: `outline: 2px solid var(--cyan); outline-offset: 2px;`
- **AlertDetailDrawer** is `role="dialog" aria-modal="true"`. Focus-trapped. Esc closes.
- **AlertsPanel** has `aria-live="polite"`. New alert rows announced.
- **DetectionCanvas** has `role="img"` with aria-label updated (debounced) like "Detections: 3 people, 1 vehicle".
- **PolygonEditor**: Click = add; Esc = cancel; Enter = close; Backspace = remove last.
- **`prefers-reduced-motion`** disables status-dot pulse, new-alert tint fade, and sets all transition durations to 0.01ms.

---

## Interactions & Behavior Summary

- **Source switching** auto-clears the polygon and bumps `zone_version`.
- **Reset Demo** shows a confirm dialog, then dispatches `session/reset` — clears alerts, clears zone, disconnects WS, returns to idle.
- **Relative timestamps** in alert rows: `just now` / `12 s` / `1 m` / after 1h use absolute `14:28:03`. Full ISO on hover (`title` attr).
- **New-alert tint** plays for 2000ms then fades. Not played in reduced-motion mode.
- **FPS readout** refreshes every 500ms from an EMA of server `stats.fps` — not per-frame, to avoid layout thrash.

---

## Assets

No bitmap assets required for MVP. All icons and shapes are CSS/SVG. The NeuraWatch logo is a 24×24 linear-gradient(135deg, `--cyan`, `--cyan-d`) square with an inset border square at `inset: 5px`.

Fonts load from Google Fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

---

## Files in this bundle

- `README.md` — this document
- `Frontend Handoff Spec.html` — the long-scroll design reference with pin-annotated screens
- `handoff.css` — stylesheet for the spec document (reference only; not for production import)

Open `Frontend Handoff Spec.html` in a browser to see every screen annotated with numbered pins and accompanying legends. Treat it as a visual source of truth; this README is the implementation contract.

---

## Delivery Checklist (must-ship)

- [ ] `<AppHeader>` with 6-state StatusBadge + FPS readout + Reset Demo (confirm dialog)
- [ ] `<EmptyState>` with 3-step guide + webcam/upload selector
- [ ] `<LiveFeedPanel>` (4:3 aspect) with rAF `<DetectionCanvas>`, bbox + label rendering, in-zone retint
- [ ] `<PolygonEditor>` — click-add, rubber-band, Close Zone (≥3 pts), Clear, auto-clear on source switch
- [ ] `<AlertsPanel>` — newest-first list, new-tint for 2s, click opens `<AlertDetailDrawer>`
- [ ] `<AlertDetailDrawer>` with saved frame + metadata grid + Download/Copy actions
- [ ] WS singleton + `useReducer` in `App.tsx` (no separate hooks)
- [ ] All 7 states + WCAG AA + `prefers-reduced-motion` support

**Deferred / post-MVP:** multi-zone, class filters, drag-to-edit vertices, alert search, user accounts, settings panel, shareable event clips, multi-feed operator view.
