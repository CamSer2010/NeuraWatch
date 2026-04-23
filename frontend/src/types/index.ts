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
}

/** Upper bound on `state.alerts.length` to keep the working set
 * finite across long sessions. Matches the NW-1404 AC's "last 20
 * alerts" user-visible count with a 5× headroom so the dedup logic
 * doesn't discard alerts that are still rendered after a scroll. */
export const ALERTS_MAX = 100

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
  | { type: 'session/reset' }

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
      return {
        ...state,
        status:
          state.status === 'model-loading' || state.status === 'disconnected'
            ? 'live'
            : state.status,
        detections: action.payload.detections,
        stats: action.payload.stats,
        lastZoneVersion: action.payload.zone_version,
        lastEvents: action.payload.events,
        alerts: mergedAlerts,
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
      return { ...state, alerts: combined.slice(0, ALERTS_MAX) }
    }

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

    case 'session/reset':
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
