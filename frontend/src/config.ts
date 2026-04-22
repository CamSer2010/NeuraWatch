/**
 * Runtime configuration.
 *
 * Hardcoded for NW-1203 dev. NW-1504 deployment will rewrite these
 * from build-time env (`VITE_WS_URL`, `VITE_API_BASE`) or derive them
 * from `window.location` so the FastAPI-serves-dist + ngrok path
 * reuses the current origin.
 */

export const WS_URL = 'ws://localhost:8000/ws/detect'
export const API_BASE = 'http://localhost:8000'
