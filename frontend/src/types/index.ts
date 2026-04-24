/**
 * App-level state and reducer.
 *
 * Per ratified plan decision #12: one `useReducer` in `App.tsx`, no
 * factored-out `useWebSocket` / `useWebcam` / `useAlerts` hooks.
 *
 * Shape mirrors `frontend/design-specs/README.md` §System States:
 *   - `status` is the top-level AppStatus union (7 values)
 *   - action types use the spec's `namespace/verb` convention
 *
 * Scope discipline:
 *   NW-1201 — status + camera flags + media/* actions.
 *   NW-1202 — source + upload state.
 *   NW-1203 — this update — detections, stats, events, zone_version + ws/* actions.
 *   NW-1205 — derived model-loading handling.
 *   NW-1301 — zone + points + zone_version writer.
 *   NW-1303/1404 — alerts.
 *   NW-1405 — session/reset.
 */

/** 7 top-level states the whole app can be in, per design-specs. */
export type AppStatus =
  | 'idle'           // App boot, no source connected
  | 'model-loading'  // WS open, first detection_result not yet received
  | 'live'           // Actively receiving detections
  | 'processing'     // Upload mode running (NW-1202)
  | 'disconnected'   // WS close or 2s watchdog tripped (one retry pending)
  | 'camera-denied'  // NotAllowedError from getUserMedia
  | 'error'          // WS 1011, /health fail, non-permission media error,
                     // or second consecutive WS failure (Reset required)

/** Three classes NeuraWatch detects; mirrors backend `ObjectClass`. */
export type ObjectClass = 'person' | 'vehicle' | 'bicycle'

/**
 * One detected object. `bbox` is normalized 0–1 against the 640×480
 * processed frame, per ratified decision #5.
 *
 * The wire field name is `class` (spec §6); wsClient.ts renames it
 * to `objectClass` before it enters app state so consumers aren't
 * forced into `const { class: cls } = det` destructuring.
 */
export interface Detection {
  objectClass: ObjectClass
  bbox: [number, number, number, number]
  confidence: number
  track_id: number | null
}

export interface DetectionStats {
  fps: number
  inference_ms: number
  /** Alias for inference_ms — covers submit→resolve, not pure model time. */
  roundtrip_ms?: number
}

/** A polygon vertex, normalized 0–1 against the 640×480 processed
 * frame. Same coordinate space as `Detection.bbox` (ratified
 * decision #5) so point-in-polygon math in NW-1302 is trivial. */
export type Point = [number, number]

/** One zone-boundary transition as it rides the WebSocket in
 * `detection_result.events` (NW-1303). The field names match the
 * wire exactly — no rename layer like `Detection` has, because
 * `object_class` isn't a reserved word in JS. */
export interface ZoneEventWire {
  track_id: number
  object_class: ObjectClass
  event_type: 'enter' | 'exit'
  timestamp: string
  alert_id: string
}

/**
 * One alert as the frontend tracks it (NW-1404).
 *
 * Superset of `ZoneEventWire`: includes the DB-backed `id` +
 * `frame_path` from the REST API when known, and an ephemeral
 * `isNew` flag that drives the 2 s tint animation on the row.
 *
 * An alert first appears via WS push with only the wire fields
 * populated; clicking the row (or the next REST fetch) enriches
 * the entry with `frame_path`. `id` is optional because it's
 * generated on INSERT and may not survive a /session/reset wipe —
 * use `alert_id` as the stable dedup key.
 */
export interface Alert extends ZoneEventWire {
  id?: number
  frame_path?: string | null
  isNew?: boolean
}

/** Shape of the server-sent `detection_result` payload, after the
 * wire→app mapping in wsClient.ts. */
export interface DetectionResult {
  type: 'detection_result'
  seq: number
  mode: 'webcam' | 'upload'
  detections: Detection[]
  events: ZoneEventWire[]   // NW-1303 populates
  zone_version: number      // NW-1301 populates
  stats: DetectionStats
  // NW-1202: upload-mode frames carry the source-video timestamp
  // and zero-based frame index. Absent on webcam frames — clients
  // that only handle webcam can ignore these fields.
  pts_ms?: number
  frame_idx?: number
}

/** NW-1202: metadata returned by POST /upload. */
export interface UploadMetadata {
  video_id: string
  source_fps: number
  duration_sec: number
  width: number
  height: number
  processed_fps: number
  total_frames: number
}

/** NW-1202: sentinel pushed on `{type:"processing_complete"}`.
 * Server stops emitting detection_result after this. */
export interface ProcessingComplete {
  type: 'processing_complete'
  total_frames: number
  alerts_created: number
}

export interface AppState {
  status: AppStatus

  // Camera sub-state. A running MediaStream doesn't always map 1:1 to
  // AppStatus (webcam ready + WS open-but-pre-first-frame → 'model-loading';
  // webcam ready + WS not open → 'idle'), so we carry these flags alongside.
  cameraActive: boolean
  cameraRequesting: boolean
  cameraError: string | null

  /** Latest detections from the server, to be rendered by NW-1204. */
  detections: Detection[]
  stats: DetectionStats | null

  /** Last-received zone_version; NW-1204 gates detection rendering on
   * this matching the client's current zone version (spec §6). */
  lastZoneVersion: number

  /** Most recent frame's events. Retained for NW-1204's in-zone
   * bbox retint. The persisted alerts list lives in `alerts` below. */
  lastEvents: ZoneEventWire[]

  // --- Alerts (NW-1404) ---
  //
  // Newest-first, deduped by `alert_id`. Populated by:
  //   - `alerts/bootstrap` on mount (REST GET /alerts)
  //   - `ws/frame` merging new events in with `isNew: true`
  //   - `alerts/enrich` stamping `frame_path` after a lazy
  //     REST detail fetch triggered by a row click
  // Capped at ALERTS_MAX to keep the in-memory working set bounded
  // across a long-running session.
  alerts: Alert[]
  /** `alert_id` of the row rendered in the detail pane, or null when
   * no selection has been made yet. */
  selectedAlertId: string | null
  /** True while a Load-More REST page is in flight; the AlertsPanel
   * footer disables the button + swaps copy. */
  alertsLoading: boolean
  /** Set false the moment a Load-More page returns fewer rows than
   * `ALERTS_PAGE_SIZE` — the server is out of older alerts so the
   * AlertsPanel hides the Load More button. Bootstrap also sets it
   * (true if the initial fetch returned a full page, false otherwise).
   * Resets to `true` on `session/reset` so the next session can
   * paginate again from a cold history. */
  alertsHasMore: boolean

  // --- Zone polygon (NW-1301) ---
  //
  // One polygon, stored as normalized 0–1 coords. `zoneClosed` reflects
  // whether the user has committed the shape (Close Zone button); until
  // then the `points` are an in-progress sketch and alerts are paused
  // (`no-polygon` sub-state per spec §System States).
  //
  // `zoneVersion` is a monotonic counter bumped every time the polygon
  // changes (close, clear). We send it to the server on each `zone_update`
  // / `zone_clear`, and the server echoes it on every subsequent
  // `detection_result`. NW-1204's render gate compares `lastZoneVersion`
  // (server echo) against `zoneVersion` (client truth); mismatches mean
  // the server's events belong to an older polygon and should be muted.
  zoneDrawing: boolean
  zonePoints: Point[]
  zoneClosed: boolean
  zoneVersion: number

  // --- Video source + upload (NW-1202) ---
  //
  // `videoSource` drives which surface is active in the left column —
  // WebcamView when 'webcam', VideoUploadView when 'upload'. Defaults
  // to 'webcam' on boot to preserve the NW-1201 flow for operators
  // who never touch the selector.
  //
  // `uploadedVideo` holds the metadata the server returned + the
  // client-side blob URL the `<video>` element plays from (Option 1
  // architecture — no server-side static serving). Null before an
  // upload succeeds; cleared on session/reset or source switch.
  //
  // `uploadPhase` tracks the upload/processing lifecycle independent
  // of top-level status: 'idle' → 'uploading' → 'ready' (server
  // acked + processing started) → 'complete' (server emitted
  // processing_complete). Separate from `status` because status
  // already maps to badge color and we want the banner-level
  // 'upload-in-progress' / 'upload-complete' states from spec §System
  // States without overloading the main state machine.
  videoSource: VideoSource
  uploadedVideo: UploadedVideo | null
  uploadPhase: UploadPhase
  uploadError: string | null

  /** NW-1202: prediction buffer keyed by pts_ms. LiveFeedCanvas
   * matches the latest entry ≤ `<video>.currentTime * 1000` on
   * each rAF tick to render overlays in sync with smooth client-
   * side playback. Bounded to `UPLOAD_BUFFER_MAX` so long clips
   * don't grow unbounded. Empty in webcam mode. */
  uploadPredictions: PredictionFrame[]
}

/** One server-emitted detection_result retained in the prediction
 * buffer. Same fields as DetectionResult minus the bookkeeping we
 * don't need for overlay rendering. */
export interface PredictionFrame {
  pts_ms: number
  frame_idx: number
  detections: Detection[]
}

export type VideoSource = 'webcam' | 'upload'
export type UploadPhase =
  | 'idle'        // No upload in progress
  | 'uploading'   // POST /upload in flight
  | 'ready'       // Server has the file + metadata; processing is starting
  | 'processing'  // Server is streaming detection_result frames
  | 'complete'    // Server emitted processing_complete

export interface UploadedVideo {
  metadata: UploadMetadata
  /** `URL.createObjectURL(file)` — must be revoked on reset / new
   * upload to avoid leaking memory across long demo sessions. */
  blobUrl: string
}

export const initialAppState: AppState = {
  status: 'idle',
  cameraActive: false,
  cameraRequesting: false,
  cameraError: null,
  detections: [],
  stats: null,
  lastZoneVersion: 0,
  lastEvents: [],
  zoneDrawing: false,
  zonePoints: [],
  zoneClosed: false,
  zoneVersion: 0,
  alerts: [],
  selectedAlertId: null,
  alertsLoading: false,
  alertsHasMore: true,
  videoSource: 'webcam',
  uploadedVideo: null,
  uploadPhase: 'idle',
  uploadError: null,
  uploadPredictions: [],
}

/** Upper bound on `state.uploadPredictions.length`. 3000 entries at
 * the server's 10 FPS target processing rate = ~5 minutes of
 * buffered overlays. An earlier cap of 200 caused the first slice
 * of long videos to lose overlays after processing finished: the
 * ring buffer wrapped past pts values the operator hadn't scrubbed
 * back to yet. Binary-searching a 3000-element array is still
 * microseconds per rAF tick, and memory cost is ~200 KB (Detection
 * arrays are small), so the generous cap is free at demo scale. */
export const UPLOAD_BUFFER_MAX = 3000

/** Upper bound on `state.alerts.length` to keep the working set
 * finite across long sessions. Sized for a paginated UI: with
 * `ALERTS_PAGE_SIZE = 50` the operator can Load More twenty times
 * before WS-pushed alerts start evicting the oldest manually-loaded
 * page. The NW-1404 AC's "last 20 alerts" user-visible count is
 * still respected — the panel scrolls; this just bounds memory. */
export const ALERTS_MAX = 1000

/** Page size for `GET /alerts?limit=…` calls — both the initial
 * bootstrap and every Load-More click. Mirrors the server's clamp
 * (max 500) but stays small so a slow network or a saturated DB
 * doesn't stall the UI for long on a single page. The reducer uses
 * this to decide whether the just-returned page was full (= more
 * to fetch) or partial (= server is empty, hide the button). */
export const ALERTS_PAGE_SIZE = 50

/**
 * Action union. `media/*` and `ws/*` names mirror the spec's
 * namespace/verb convention. Future tickets extend this union
 * (upload/*, zone/*, alert/*, session/*).
 *
 * Deliberate deviations from design-specs §System States:
 *   - `media/denied` and `media/error` carry a `message` payload for
 *     a11y recovery copy. Spec's sample is payload-free.
 *   - `media/error` exists (not in the spec's sample list). Non-
 *     permission `getUserMedia` failures route to `error`.
 *   - `ws/first-frame` collapsed into `ws/frame` — the reducer derives
 *     the model-loading → live transition from current status rather
 *     than a separate action.
 */
export type Action =
  | { type: 'media/requesting' }
  | { type: 'media/ready' }
  | { type: 'media/denied'; message: string }
  | { type: 'media/error'; message: string }
  | { type: 'media/stop' }
  | { type: 'ws/open' }
  | { type: 'ws/close'; retry: boolean }
  | { type: 'ws/frame'; payload: DetectionResult }
  | { type: 'zone/start-draw' }
  | { type: 'zone/add-point'; point: Point }
  | { type: 'zone/remove-last-point' }
  | { type: 'zone/close' }
  | { type: 'zone/clear' }
  | { type: 'zone/cancel-draw' }
  | { type: 'alerts/bootstrap'; alerts: Alert[] }
  | { type: 'alerts/enrich'; alert: Alert }
  | { type: 'alerts/select'; alertId: string | null }
  | { type: 'alerts/clear-new'; alertIds: string[] }
  // Load-More REST pagination. Start sets `alertsLoading=true`, success
  // appends the new page + recomputes `alertsHasMore` from the page
  // size, error just clears the loading flag (the user can retry).
  | { type: 'alerts/load-more-start' }
  | { type: 'alerts/load-more-success'; alerts: Alert[] }
  | { type: 'alerts/load-more-error' }
  | { type: 'session/reset' }
  // NW-1202 upload lifecycle. No explicit 'upload/processing' action
  // — the ready → processing transition is derived inside `ws/frame`
  // when the first upload-mode detection_result lands (see reducer).
  | { type: 'source/set'; source: VideoSource }
  | { type: 'upload/start' }
  | { type: 'upload/success'; video: UploadedVideo }
  | { type: 'upload/error'; message: string }
  | { type: 'upload/complete'; totalFrames: number; alertsCreated: number }
  | { type: 'upload/restart' }

export function appReducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'media/requesting':
      return { ...state, cameraRequesting: true, cameraError: null }

    case 'media/ready':
      return {
        ...state,
        cameraActive: true,
        cameraRequesting: false,
        cameraError: null,
        // Recover from `camera-denied` on success. Do NOT auto-recover
        // from `error` — spec requires an explicit Reset Demo path.
        status: state.status === 'camera-denied' ? 'idle' : state.status,
      }

    case 'media/denied':
      return {
        ...state,
        status: 'camera-denied',
        cameraActive: false,
        cameraRequesting: false,
        cameraError: action.message,
      }

    case 'media/error':
      return {
        ...state,
        status: 'error',
        cameraActive: false,
        cameraRequesting: false,
        cameraError: action.message,
      }

    case 'media/stop':
      return {
        ...state,
        cameraActive: false,
        cameraRequesting: false,
        cameraError: null,
        detections: [],
        stats: null,
        lastZoneVersion: 0,
        lastEvents: [],
        // `alerts` and `selectedAlertId` INTENTIONALLY survive a
        // media/stop. The DB rows persist (NW-1405's Reset Demo is
        // the only path that wipes persistence), and scrolling past
        // history while the camera is off is useful. The next
        // webcam start re-bootstraps from REST; its dedup handles
        // any overlap.
        // Stopping the source invalidates the zone — spec §Interactions:
        // "Source switching auto-clears the polygon and bumps
        // zone_version". Stop is the degenerate case of a source
        // switch (to nothing). We bump the version here so the
        // outbound-zone effect fires a `zone_clear` on the socket
        // before it closes.
        zoneDrawing: false,
        zonePoints: [],
        zoneClosed: false,
        zoneVersion: state.zoneVersion + 1,
        // Stopping the webcam removes the source — we're back to 'idle'
        // per spec §System States ("App boot, no source"). Exception:
        // 'error' stays pinned; spec requires explicit Reset Demo
        // (NW-1405) to clear it.
        //
        // `disconnectWs()` (called from WebcamView's cleanup effect
        // when cameraActive flips false) closes the socket with code
        // 1000; the wsClient onclose handler deliberately does NOT
        // dispatch on normal closes — so this reducer case is the
        // only place status transitions on stop.
        status: state.status === 'error' ? 'error' : 'idle',
      }

    case 'ws/open':
      // `idle` OR `disconnected` → `model-loading`. Without the
      // `disconnected` case, reconnects park in amber until the first
      // frame lands, which is the wrong signal (we're NOT disconnected
      // anymore; we're waiting for inference).
      return {
        ...state,
        status:
          state.status === 'idle' || state.status === 'disconnected'
            ? 'model-loading'
            : state.status,
      }

    case 'ws/close':
      return {
        ...state,
        status: action.retry ? 'disconnected' : 'error',
        detections: [],
        stats: null,
        // lastZoneVersion / lastEvents retained; the zone geometry
        // doesn't change across a WS hiccup.
      }

    case 'ws/frame': {
      // Belt-and-suspenders: ignore frames while in a pinned error
      // state (shouldn't happen — the socket would be closed — but
      // guards against onmessage/onclose ordering races).
      if (state.status === 'camera-denied' || state.status === 'error') {
        return state
      }
      // Merge new events into the alerts list. Dedup by alert_id
      // against whatever's already there (REST bootstrap may have
      // seeded the same alert). New arrivals get `isNew: true` so
      // the UI can play the 2 s tint per design-specs §4.
      const knownIds = new Set(state.alerts.map((a) => a.alert_id))
      const incoming = action.payload.events
        .filter((ev) => !knownIds.has(ev.alert_id))
        .map<Alert>((ev) => ({ ...ev, isNew: true }))
      const mergedAlerts =
        incoming.length > 0
          ? [...incoming, ...state.alerts].slice(0, ALERTS_MAX)
          : state.alerts

      // NW-1202: in upload mode, frames carry pts_ms and detections
      // go into the prediction buffer instead of `state.detections`.
      // LiveFeedCanvas picks the matching entry on each rAF tick
      // keyed by `<video>.currentTime`, so `state.detections` would
      // show a stale-most-recent overlay that doesn't match playback.
      const isUpload =
        action.payload.mode === 'upload' &&
        typeof action.payload.pts_ms === 'number' &&
        typeof action.payload.frame_idx === 'number'

      const nextPredictions = isUpload
        ? [
            ...state.uploadPredictions,
            {
              pts_ms: action.payload.pts_ms as number,
              frame_idx: action.payload.frame_idx as number,
              detections: action.payload.detections,
            },
          ].slice(-UPLOAD_BUFFER_MAX)
        : state.uploadPredictions

      // Status + phase transitions depend on mode:
      //   webcam   — model-loading|disconnected → live; else unchanged
      //   upload   — flip status → 'processing' + phase → 'processing'
      //              on the FIRST frame (the 'ready' → 'processing'
      //              edge). Subsequent frames leave both unchanged.
      let nextStatus = state.status
      let nextPhase = state.uploadPhase
      if (action.payload.mode === 'upload') {
        if (state.uploadPhase === 'ready') {
          nextPhase = 'processing'
          nextStatus = 'processing'
        }
      } else {
        if (
          state.status === 'model-loading' ||
          state.status === 'disconnected'
        ) {
          nextStatus = 'live'
        }
      }

      return {
        ...state,
        status: nextStatus,
        uploadPhase: nextPhase,
        detections: isUpload ? [] : action.payload.detections,
        stats: action.payload.stats,
        lastZoneVersion: action.payload.zone_version,
        lastEvents: action.payload.events,
        alerts: mergedAlerts,
        uploadPredictions: nextPredictions,
      }
    }

    case 'zone/start-draw':
      // Entering drawing mode discards any previously-closed polygon
      // (spec §Control strip: "Redraw" replaces the committed shape).
      // When there WAS a committed polygon, bump the version
      // immediately so the outbound-zone effect fires a zone_clear on
      // the server before the user starts placing new points. Without
      // this, a Draw-then-Esc sequence would leave the server holding
      // a polygon the client believes is gone.
      return {
        ...state,
        zoneDrawing: true,
        zonePoints: [],
        zoneClosed: false,
        zoneVersion: state.zoneClosed
          ? state.zoneVersion + 1
          : state.zoneVersion,
      }

    case 'zone/add-point':
      if (!state.zoneDrawing) return state
      return { ...state, zonePoints: [...state.zonePoints, action.point] }

    case 'zone/remove-last-point':
      if (!state.zoneDrawing || state.zonePoints.length === 0) return state
      return { ...state, zonePoints: state.zonePoints.slice(0, -1) }

    case 'zone/close':
      // Spec §3: Close Zone button disabled until points.length >= 3.
      // Reducer also refuses below the threshold so stray key/click
      // paths can't sneak past the UI gate.
      if (!state.zoneDrawing || state.zonePoints.length < 3) return state
      return {
        ...state,
        zoneDrawing: false,
        zoneClosed: true,
        zoneVersion: state.zoneVersion + 1,
        // Keep the open-ring shape in state. Canvas closes it
        // visually via ctx.closePath(); Shapely (NW-1302) accepts
        // open rings. Duplicating the first vertex here would inflate
        // the wire payload and let the `>= 3` close guard accept a
        // degenerate 2-unique-vertex polygon (3 wire points).
      }

    case 'zone/clear':
      // Idempotent — clearing a nonexistent zone still bumps the
      // version so the server always sees a clear message after Clear
      // is clicked (handles the case where the user clicks Clear
      // during drawing before a close).
      return {
        ...state,
        zoneDrawing: false,
        zonePoints: [],
        zoneClosed: false,
        zoneVersion: state.zoneVersion + 1,
      }

    case 'zone/cancel-draw':
      // Esc pressed during drawing — drop the in-progress sketch
      // without touching any previously-committed polygon. Does NOT
      // bump zoneVersion because nothing committed changed.
      if (!state.zoneDrawing) return state
      return { ...state, zoneDrawing: false, zonePoints: [] }

    case 'alerts/bootstrap': {
      // REST GET /alerts populated the list on mount. Merge with any
      // WS-arrived alerts the reducer has already accumulated.
      //
      // Spread order is REST-first, then `existing` — the REST row is
      // authoritative for every DB-backed field (id, frame_path,
      // timestamp, track_id, object_class, event_type). The only
      // thing the WS-sourced entry owns is the ephemeral `isNew`
      // flag, which we preserve explicitly. Without this shape, an
      // existing row's `undefined`/stale values would silently
      // clobber the REST truth.
      const known = new Map(state.alerts.map((a) => [a.alert_id, a]))
      const merged: Alert[] = []
      for (const bootstrapped of action.alerts) {
        const existing = known.get(bootstrapped.alert_id)
        if (existing !== undefined) {
          merged.push({ ...existing, ...bootstrapped, isNew: existing.isNew })
          known.delete(bootstrapped.alert_id)
        } else {
          merged.push(bootstrapped)
        }
      }
      // Any remaining WS entries that aren't in the REST payload
      // yet (race: event arrived before the DB insert committed)
      // stay at the top.
      const leftover = Array.from(known.values())
      const combined = [...leftover, ...merged]
      combined.sort((a, b) =>
        a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0,
      )
      // hasMore is decided from the REST page (not the merged list).
      // A full page (== ALERTS_PAGE_SIZE) means the server probably has
      // older rows; a partial page means the table is shallower than
      // the page size. Counting the merged list would conflate WS-only
      // entries with the REST page and falsely signal "more available".
      return {
        ...state,
        alerts: combined.slice(0, ALERTS_MAX),
        alertsHasMore: action.alerts.length >= ALERTS_PAGE_SIZE,
      }
    }

    case 'alerts/load-more-start':
      return { ...state, alertsLoading: true }

    case 'alerts/load-more-success': {
      // Append the older page to the bottom of the existing list.
      // Dedup by alert_id in case a WS push raced with the REST fetch
      // and the same row landed via both paths. Existing entries win
      // — they may already carry an `isNew` flag we don't want to
      // strip. A full page means more remain on the server.
      const known = new Set(state.alerts.map((a) => a.alert_id))
      const fresh = action.alerts.filter((a) => !known.has(a.alert_id))
      const combined = [...state.alerts, ...fresh]
      // Sort defensively — REST is already newest-first per row, but
      // a WS push during the load could have left the merged list
      // out of order at the boundary.
      combined.sort((a, b) =>
        a.timestamp < b.timestamp ? 1 : a.timestamp > b.timestamp ? -1 : 0,
      )
      return {
        ...state,
        alerts: combined.slice(0, ALERTS_MAX),
        alertsLoading: false,
        // hasMore reflects whether the server's response was a FULL
        // page. Using `combined.length` here would falsely set false
        // whenever every fresh row was already in `state.alerts` via
        // WS — which happens routinely when an alert fires faster
        // than the operator clicks Load More.
        alertsHasMore: action.alerts.length >= ALERTS_PAGE_SIZE,
      }
    }

    case 'alerts/load-more-error':
      // Don't flip hasMore — the user can retry. Just clear the
      // loading flag so the button re-enables.
      return { ...state, alertsLoading: false }

    case 'alerts/enrich':
      // Lazy detail fetch came back — stamp `id` / `frame_path` on
      // the matching row. No-op if the alert has been evicted from
      // the bounded list (rare; only on very long sessions).
      return {
        ...state,
        alerts: state.alerts.map((a) =>
          a.alert_id === action.alert.alert_id
            ? { ...a, ...action.alert, isNew: a.isNew }
            : a,
        ),
      }

    case 'alerts/select':
      // Selecting a row also implicitly acknowledges its "new" tint
      // — the operator has seen it, so drop the flag immediately.
      return {
        ...state,
        selectedAlertId: action.alertId,
        alerts:
          action.alertId === null
            ? state.alerts
            : state.alerts.map((a) =>
                a.alert_id === action.alertId ? { ...a, isNew: false } : a,
              ),
      }

    case 'alerts/clear-new':
      // Called after the 2 s tint animation finishes for a batch of
      // rows. Idempotent: rows whose `isNew` is already false stay
      // unchanged.
      if (action.alertIds.length === 0) return state
      return {
        ...state,
        alerts: state.alerts.map((a) =>
          action.alertIds.includes(a.alert_id) ? { ...a, isNew: false } : a,
        ),
      }

    case 'source/set': {
      // NW-1202 + design-specs §Interactions: "Source switching
      // auto-clears the polygon and bumps zone_version". If the user
      // is in the middle of drawing, that in-progress sketch also
      // goes. Revoke any existing uploaded-video blob URL so we
      // don't leak memory across repeated toggles.
      if (state.videoSource === action.source) return state
      if (state.uploadedVideo !== null) {
        try {
          URL.revokeObjectURL(state.uploadedVideo.blobUrl)
        } catch {
          // Firefox raises on a double-revoke; safe to swallow.
        }
      }
      return {
        ...state,
        videoSource: action.source,
        uploadedVideo: null,
        uploadPhase: 'idle',
        uploadError: null,
        zoneDrawing: false,
        zonePoints: [],
        zoneClosed: false,
        zoneVersion: state.zoneVersion + 1,
      }
    }

    case 'upload/start':
      return {
        ...state,
        uploadPhase: 'uploading',
        uploadError: null,
      }

    case 'upload/success':
      // Defensive: revoke any prior blob URL before overwriting.
      // Today's UI gates the picker on `uploadedVideo === null` so
      // this reducer path is only reached from a clean `null` —
      // but the "every blobUrl write revokes the previous one"
      // invariant should hold at the reducer level, not rely on a
      // component gate. One-line guard for a future "re-upload
      // without a source toggle" flow.
      if (state.uploadedVideo !== null) {
        try {
          URL.revokeObjectURL(state.uploadedVideo.blobUrl)
        } catch {
          // ignore
        }
      }
      // Server has the file + metadata; caller is expected to send
      // `process_upload` on the WS immediately after. We mark phase
      // as 'ready' (distinct from 'processing') so the UI can show
      // "Upload complete · starting processing…" for the beat
      // between success and the first detection_result arriving.
      return {
        ...state,
        uploadedVideo: action.video,
        uploadPhase: 'ready',
        uploadError: null,
      }

    case 'upload/error':
      return {
        ...state,
        uploadPhase: 'idle',
        uploadError: action.message,
      }

    case 'upload/complete':
      // Server finished processing. Upload phase flips to 'complete'
      // (drives the banner); status returns to 'idle'. `<video>`
      // keeps its blobUrl so the user can scrub back through the
      // clip with overlays still matching via the prediction buffer.
      //
      // If the operator was mid-draw when completion landed, drop
      // the in-progress sketch — the status badge flipping to
      // 'idle' while zoneDrawing stayed true surfaced a contextual
      // mismatch (badge says idle, cursor is still a crosshair).
      // A committed polygon (`zoneClosed`) survives so alerts land
      // correctly if the operator scrubs back through the clip.
      return {
        ...state,
        uploadPhase: 'complete',
        status: 'idle',
        detections: [],
        zoneDrawing: false,
        zonePoints: state.zoneClosed ? state.zonePoints : [],
      }

    case 'upload/restart':
      // Re-run processing on the same uploaded clip. Flow:
      //   1. Clear prediction buffer so stale bboxes from the prior
      //      run don't bleed into the start of the new one.
      //   2. Flip phase back to 'ready' so the sender effect in
      //      VideoUploadView re-fires `process_upload` on the
      //      existing (still-open) WebSocket.
      //   3. Set status='processing' immediately for UX continuity —
      //      the user clicked a "re-run" button, surface that.
      //   4. Committed polygon survives (operator typically re-runs
      //      to validate the same zone); in-progress sketches go.
      //
      // Alerts from the prior run stay in the panel — server emits
      // unique alert_ids each time, so dedup-by-alert_id appends new
      // events instead of overwriting. Operator can tell runs apart
      // by timestamp. Wiping alerts requires session/reset.
      if (state.uploadedVideo === null) return state
      return {
        ...state,
        uploadPhase: 'ready',
        status: 'processing',
        uploadPredictions: [],
        detections: [],
        lastEvents: [],
        zoneDrawing: false,
        zonePoints: state.zoneClosed ? state.zonePoints : [],
      }

    case 'session/reset':
      // NW-1202: revoke the uploaded-video blob URL before the state
      // forgets it. Idempotent revoke — safe on already-revoked URLs.
      if (state.uploadedVideo !== null) {
        try {
          URL.revokeObjectURL(state.uploadedVideo.blobUrl)
        } catch {
          // ignore
        }
      }
      // NW-1405: Full wipe. The HTTP handler has already cleared DB
      // rows, unlinked frame JPEGs, and reset the ByteTrack IDs; the
      // reducer mirrors that on the client side so the UI returns to
      // a cold-boot appearance.
      //
      // `cameraActive` flips false so WebcamView's WS-lifecycle effect
      // tears down the socket (per design-specs §Interactions:
      // "disconnects WS, returns to idle"). The MediaStream track
      // teardown lives inline in the Reset button handler — the
      // reducer can't call imperative APIs. The 'idle' status guards
      // a subsequent media/ready from auto-recovering an 'error' state
      // (see media/ready's comment); resetting from 'error' through
      // session/reset is the one path that CAN clear the pin.
      //
      // No explicit zone_clear is sent on the wire here (unlike
      // stopCamera, which does): per-WS-connection ZoneService is
      // recreated on reconnect, so the server has no zone state to
      // ghost. If a future ticket keeps any app-level prefs on state
      // (theme, panel width) that should SURVIVE reset, reshape this
      // return to spread `initialAppState` then re-attach the
      // preserved scalars — today's amnesia path wipes them all.
      return {
        ...initialAppState,
      }

    default:
      // Exhaustive union should keep this unreachable.
      return state
  }
}
