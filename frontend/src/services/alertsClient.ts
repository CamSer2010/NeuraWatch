/**
 * REST client for the NW-1403 alerts endpoints.
 *
 * Small and stateless — just typed `fetch` wrappers. No dedup or
 * caching here; the reducer in `types/index.ts` owns that via the
 * `alerts/bootstrap` and `alerts/enrich` actions.
 *
 * Errors propagate to the caller. The AlertsPanel treats a bootstrap
 * failure as non-fatal — the app still works with WS-pushed alerts
 * only — so the component-level handler just logs and continues.
 */

import { API_BASE } from '../config'
import type { Alert } from '../types'

export async function fetchRecentAlerts(
  limit = 50,
  offset = 0,
): Promise<Alert[]> {
  const res = await fetch(
    `${API_BASE}/alerts?limit=${limit}&offset=${offset}`,
  )
  if (!res.ok) {
    throw new Error(`GET /alerts → ${res.status}`)
  }
  return (await res.json()) as Alert[]
}

/**
 * Lazily fetch a single alert's full row (DB id + frame_path).
 *
 * AlertsPanel calls this when the user clicks a row whose
 * `frame_path` is still unknown — the WS push lands before NW-1402's
 * snapshot commits, and we don't want to poll every pushed alert.
 *
 * Returns `null` when the server responds 404 (the alert was wiped
 * by /session/reset between the WS push and the user's click).
 */
export async function fetchAlertById(alertId: string): Promise<Alert | null> {
  const res = await fetch(
    `${API_BASE}/alerts/${encodeURIComponent(alertId)}`,
  )
  if (res.status === 404) return null
  if (!res.ok) {
    throw new Error(`GET /alerts/${alertId} → ${res.status}`)
  }
  return (await res.json()) as Alert
}

/**
 * URL helper so callers don't hand-assemble `/frames/...` paths.
 * Centralizes the `API_BASE` concat so NW-1504's deployment path
 * flip (if any) is one edit.
 */
export function frameUrl(filename: string): string {
  return `${API_BASE}/frames/${encodeURIComponent(filename)}`
}
