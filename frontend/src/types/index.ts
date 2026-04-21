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
 *   NW-1201 — this file — status + camera flags + media/* actions.
 *   NW-1202 — source + upload state.
 *   NW-1203 — ws/open, ws/first-frame, ws/close, ws/frame.
 *   NW-1205 — derived model-loading handling.
 *   NW-1301 — zone + points + zone_version.
 *   NW-1303/1404 — alerts.
 *   NW-1405 — session/reset.
 */

/** 7 top-level states the whole app can be in, per design-specs. */
export type AppStatus =
  | 'idle'           // App boot, no source connected
  | 'model-loading'  // WS open, first stats not yet received (NW-1203)
  | 'live'           // Actively receiving detections
  | 'processing'     // Upload mode running (NW-1202)
  | 'disconnected'   // WS close or 2s watchdog tripped (NW-1203)
  | 'camera-denied'  // NotAllowedError from getUserMedia
  | 'error'          // WS 1011, /health fail, non-permission media error

export interface AppState {
  status: AppStatus

  /**
   * Camera sub-state, kept alongside `status` because a running
   * MediaStream doesn't always map 1:1 to AppStatus (e.g. webcam
   * is on but WS hasn't opened yet → status stays 'idle').
   */
  cameraActive: boolean
  cameraRequesting: boolean
  /** Human-readable message for the camera-denied / error UI. */
  cameraError: string | null
}

export const initialAppState: AppState = {
  status: 'idle',
  cameraActive: false,
  cameraRequesting: false,
  cameraError: null,
}

/**
 * Action union. `media/*` names mirror the spec's namespace/verb
 * convention. Future tickets extend this union (ws/*, upload/*,
 * zone/*, alert/*, session/*).
 *
 * Deliberate deviation from design-specs §System States:
 *   - `media/denied` and `media/error` carry a `message` payload.
 *     The spec's sample reducer lists payload-free actions but we
 *     need human-readable recovery copy for the alert panels.
 *   - `media/error` exists (not in the spec's sample list). Non-
 *     permission getUserMedia failures (NotFoundError,
 *     NotReadableError, OverconstrainedError, SecurityError) route
 *     to the top-level `error` status. Spec's `error` semantic
 *     widens to include pre-WS media failures.
 */
export type Action =
  | { type: 'media/requesting' }
  | { type: 'media/ready' }
  | { type: 'media/denied'; message: string }
  | { type: 'media/error'; message: string }
  | { type: 'media/stop' }

export function appReducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'media/requesting':
      return {
        ...state,
        cameraRequesting: true,
        cameraError: null,
      }

    case 'media/ready':
      return {
        ...state,
        cameraActive: true,
        cameraRequesting: false,
        cameraError: null,
        // Recover from `camera-denied` on success. Do NOT auto-recover
        // from `error` — spec requires an explicit Reset Demo path for
        // that state (§System States).
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
        // Clear a prior camera-denied pin once the user has stopped.
        // Leave `error` pinned (requires explicit Reset Demo).
        status: state.status === 'camera-denied' ? 'idle' : state.status,
      }

    default:
      // Exhaustive union should keep this unreachable, but a `default`
      // branch protects the tree if an unknown action ever slips past
      // the types (e.g. from a future WS thunk).
      return state
  }
}
