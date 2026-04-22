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

/** Shape of the server-sent `detection_result` payload, after the
 * wire→app mapping in wsClient.ts. */
export interface DetectionResult {
  type: 'detection_result'
  seq: number
  mode: 'webcam' | 'upload'
  detections: Detection[]
  events: unknown[]      // NW-1303 populates
  zone_version: number   // NW-1301 populates
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

  /** Raw events from the last frame. NW-1303 will narrow the type and
   * push these into a persisted alerts list. */
  lastEvents: unknown[]
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
}

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
        // Clear a prior camera-denied pin once the user has stopped.
        // Leave `error` pinned (explicit Reset Demo per spec).
        status: state.status === 'camera-denied' ? 'idle' : state.status,
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

    case 'ws/frame':
      // Belt-and-suspenders: ignore frames while in a pinned error
      // state (shouldn't happen — the socket would be closed — but
      // guards against onmessage/onclose ordering races).
      if (state.status === 'camera-denied' || state.status === 'error') {
        return state
      }
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
      }

    default:
      // Exhaustive union should keep this unreachable.
      return state
  }
}
