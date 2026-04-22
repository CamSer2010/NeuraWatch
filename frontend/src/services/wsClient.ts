/**
 * Live-detection WebSocket client (NW-1203).
 *
 * Module-level singleton per ratified plan decision #12 — no factored
 * hook, no class instance. The whole app talks to ONE backend over
 * ONE WebSocket.
 *
 * Protocol contract: frontend/design-specs/README.md §6.
 *   C→S: `{type:"frame_meta",seq,mode}` text, then a binary JPEG.
 *        Also `sendMessage({...})` for future zone_update / reset.
 *   S→C: `{type:"detection_result",seq,mode,detections,events,zone_version,stats}` text.
 *        Or `{type:"frame_dropped",seq}` when the server displaced our
 *        frame via its size-1 queue (latest-wins).
 *
 * Deliberate deviations from spec §6 sample code:
 *   - `frame_meta` includes a `mode` field (webcam|upload). NW-1202
 *     upload path uses it; stashed on the client via `connectWs`.
 *   - The 2s watchdog force-closes the socket on timeout rather than
 *     dispatching `ws/close` directly. onclose always follows; owning
 *     the dispatch there avoids a double-fire race.
 *   - On receive, the wire field `class` is renamed to `objectClass`
 *     before entering app state. Keeps TS consumers free of the
 *     `{ class: cls }` destructuring tax (`class` is a reserved word).
 *
 * Backpressure (spec §6):
 *   - `inFlight` boolean blocks sendFrame while waiting on a response.
 *   - 2s watchdog: no ack → force-close → onclose retries once.
 *   - Stale responses (seq <= lastSeq) dropped.
 */

import type { Action, Detection, DetectionResult, ObjectClass } from '../types'

type Dispatch = (action: Action) => void
type Mode = 'webcam' | 'upload'

const WATCHDOG_MS = 2000
const RECONNECT_DELAY_MS = 1000
const MAX_RETRIES = 1
const CLOSE_NORMAL = 1000
const CLOSE_WATCHDOG = 4000

let ws: WebSocket | null = null
let wsUrl = ''
let currentMode: Mode = 'webcam'
let dispatchFn: Dispatch | null = null

let seq = 0
let lastSeq = 0
let inFlight = false
let watchdog: ReturnType<typeof setTimeout> | null = null
let retryCount = 0

export function connectWs(
  url: string,
  dispatch: Dispatch,
  mode: Mode = 'webcam',
): void {
  wsUrl = url
  dispatchFn = dispatch
  currentMode = mode
  seq = 0
  lastSeq = 0
  inFlight = false
  retryCount = 0
  _open()
}

/** User-initiated stop. Does not retry.
 *
 * Invariant: `disconnectWs` clears `wsUrl` and `dispatchFn` BEFORE
 * closing the socket. Any pending retry `setTimeout` (scheduled in
 * `sock.onclose`) sees `wsUrl === ''` and bails — no ghost reconnect
 * after the user stops the webcam. */
export function disconnectWs(): void {
  _clearWatchdog()
  inFlight = false
  seq = 0
  lastSeq = 0
  retryCount = 0

  const sock = ws
  ws = null
  dispatchFn = null
  wsUrl = ''

  if (sock !== null) {
    try {
      sock.close(CLOSE_NORMAL, 'client stop')
    } catch {
      // Already closing; ignore.
    }
  }
}

/**
 * Send a JPEG frame. Non-blocking; silently drops when the socket is
 * not OPEN or a previous frame is still in flight. Most rAF ticks
 * hit the inFlight guard — that's the backpressure working.
 */
export function sendFrame(blob: Blob): void {
  if (ws === null || ws.readyState !== WebSocket.OPEN) return
  if (inFlight) return

  seq += 1
  inFlight = true

  ws.send(JSON.stringify({ type: 'frame_meta', seq, mode: currentMode }))
  ws.send(blob)

  _clearWatchdog()
  watchdog = setTimeout(() => {
    // No ack in 2s. Force-close; onclose owns the retry dispatch.
    inFlight = false
    try {
      ws?.close(CLOSE_WATCHDOG, 'watchdog timeout')
    } catch {
      // ignore
    }
  }, WATCHDOG_MS)
}

/**
 * Send a JSON control message (zone_update, zone_clear, reset...).
 * NW-1301 / NW-1405 use this. Silently drops when the socket isn't
 * OPEN so callers can fire-and-forget during reconnect windows.
 */
export function sendMessage(msg: object): void {
  if (ws === null || ws.readyState !== WebSocket.OPEN) return
  ws.send(JSON.stringify(msg))
}

// ---- internals ---------------------------------------------------------

function _toDetection(raw: {
  class: ObjectClass
  bbox: [number, number, number, number]
  confidence: number
  track_id: number | null
}): Detection {
  return {
    objectClass: raw.class,
    bbox: raw.bbox,
    confidence: raw.confidence,
    track_id: raw.track_id,
  }
}

function _open(): void {
  if (wsUrl === '' || dispatchFn === null) return

  let sock: WebSocket
  try {
    sock = new WebSocket(wsUrl)
  } catch (err) {
    console.error('[wsClient] constructor failed', err)
    dispatchFn({ type: 'ws/close', retry: false })
    return
  }

  sock.binaryType = 'blob'
  ws = sock

  sock.onopen = () => {
    retryCount = 0
    dispatchFn?.({ type: 'ws/open' })
  }

  sock.onmessage = (event) => {
    if (typeof event.data !== 'string') return

    let parsed: unknown
    try {
      parsed = JSON.parse(event.data)
    } catch {
      return
    }

    if (parsed === null || typeof parsed !== 'object') return
    const msg = parsed as { type?: string }

    if (msg.type === 'detection_result') {
      const raw = parsed as {
        type: 'detection_result'
        seq: number
        mode: 'webcam' | 'upload'
        detections: Array<{
          class: ObjectClass
          bbox: [number, number, number, number]
          confidence: number
          track_id: number | null
        }>
        events: unknown[]
        zone_version: number
        stats: { fps: number; inference_ms: number; roundtrip_ms?: number }
      }
      if (raw.seq <= lastSeq) return // stale
      lastSeq = raw.seq
      inFlight = false
      _clearWatchdog()

      const payload: DetectionResult = {
        type: 'detection_result',
        seq: raw.seq,
        mode: raw.mode,
        detections: raw.detections.map(_toDetection),
        events: raw.events,
        zone_version: raw.zone_version,
        stats: raw.stats,
      }
      dispatchFn?.({ type: 'ws/frame', payload })
      return
    }

    if (msg.type === 'frame_dropped') {
      // Server displaced this seq (latest-wins on the size-1 queue).
      // Clear in-flight so we can send the next frame immediately,
      // instead of waiting for the 2s watchdog to tick.
      inFlight = false
      _clearWatchdog()
      return
    }
  }

  sock.onerror = (event) => {
    // onclose always follows onerror in the browser; dispatch there.
    console.error('[wsClient] error', event)
  }

  sock.onclose = (event) => {
    _clearWatchdog()
    inFlight = false
    const wasCurrent = ws === sock
    if (wasCurrent) ws = null

    // 1000 = normal close, client-initiated via disconnectWs(). No
    // dispatch — the camera-stop flow is driving state transitions.
    if (event.code === CLOSE_NORMAL) return

    // Unexpected close. Try one auto-reconnect; if that fails too,
    // flip to `error` per spec (Reset Demo required).
    if (wasCurrent && retryCount < MAX_RETRIES && dispatchFn !== null) {
      retryCount += 1
      dispatchFn({ type: 'ws/close', retry: true })
      setTimeout(() => {
        // `wsUrl` may have been cleared by `disconnectWs` during the
        // delay. If so, the retry is a no-op — the invariant holds.
        if (wsUrl !== '' && dispatchFn !== null) _open()
      }, RECONNECT_DELAY_MS)
      return
    }

    dispatchFn?.({ type: 'ws/close', retry: false })
  }
}

function _clearWatchdog(): void {
  if (watchdog !== null) {
    clearTimeout(watchdog)
    watchdog = null
  }
}
