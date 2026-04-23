/**
 * Runtime configuration.
 *
 * Two deployment shapes and one switch between them:
 *   1. Vite dev server on :3000 → backend lives on a different origin
 *      (localhost:8000). Fall back to hardcoded URLs; CORS on the
 *      backend lists :3000 + :5173 so fetches / WS upgrades succeed.
 *   2. FastAPI serves built `dist/` on a single port (NW-1504), also
 *      exposed through ngrok. The SPA and the API share an origin —
 *      derive URLs from `window.location` so the same bundle works on
 *      localhost:8000 and on the ngrok hostname without a rebuild.
 *
 * The switch: if the page is loaded from Vite's dev port (3000), we're
 * in case 1 — hardcode. Everything else is same-origin. Keeps dev flow
 * unchanged and avoids a build-time VITE_* env the user has to set.
 */

// 3000 is this project's configured Vite port (NW-1003); 5173 is
// Vite's default fallback, included so a `npm run dev` started with
// `strictPort: false` (or a teammate on a different config) still hits
// the dev-mode branch instead of trying same-origin against 5173.
const DEV_PORTS = new Set(['3000', '5173'])

function isViteDev(): boolean {
  if (typeof window === 'undefined') return false
  return DEV_PORTS.has(window.location.port)
}

function sameOriginApi(): string {
  return `${window.location.protocol}//${window.location.host}`
}

function sameOriginWs(): string {
  const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${wsProto}//${window.location.host}/ws/detect`
}

export const WS_URL = isViteDev()
  ? 'ws://localhost:8000/ws/detect'
  : sameOriginWs()

export const API_BASE = isViteDev()
  ? 'http://localhost:8000'
  : sameOriginApi()
