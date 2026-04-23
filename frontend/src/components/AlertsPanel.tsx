import type { AnimationEvent, Dispatch } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { fetchAlertById, frameUrl } from '../services/alertsClient'
import type { Action, Alert } from '../types'
import './AlertsPanel.css'

/**
 * Alerts side-panel (NW-1404 + design-fidelity follow-up).
 *
 * Vertical layout inside the 30% right column of the main monitor
 * view: scrollable list on top, selected alert's saved frame +
 * metadata on bottom. Vertical split is a deliberate deviation from
 * the JIRA AC's "Left list / Right frame" — at the target viewport
 * (≥1440) the alerts column is ~430 px, too narrow for a horizontal
 * split that includes a readable 4:3 preview.
 *
 * Design-specs §2 + §4 + §Interactions honoured:
 *   - Panel `border-left: var(--line-2)`, 56 px header
 *   - 3-col row grid `56px 1fr auto` with thumbnail + (chip + class
 *     + sub-line) + time
 *   - Row tint `rgba(24,225,217,0.06)` for 2 s on arrival (token
 *     `--d-slow`), timestamp cyan during the tint, linear fade
 *   - Selected row: cyan inset bar via `box-shadow: inset 3px 0 0`
 *   - Relative timestamps ("just now" / "Ns" / "Nm"); absolute
 *     `HH:MM:SS` after 1 h. Full ISO on hover via `title=`
 *
 * Data flow:
 *   - App.tsx's useEffect fires `fetchRecentAlerts` on mount and
 *     dispatches `alerts/bootstrap`. This component never fetches
 *     the list — it's a pure projection of `state.alerts`.
 *   - WS pushes land via `ws/frame` with `isNew: true` on each
 *     merged event. Each row's `onAnimationEnd` dispatches
 *     `alerts/clear-new` when its 2 s CSS tint completes.
 *   - Clicking a row dispatches `alerts/select`. If the selected
 *     alert's `frame_path` is still unknown (WS push arrived before
 *     NW-1402's snapshot committed), we call `fetchAlertById` and
 *     dispatch `alerts/enrich` to backfill.
 */

export interface AlertsPanelProps {
  alerts: Alert[]
  selectedAlertId: string | null
  dispatch: Dispatch<Action>
}

const LIST_VISIBLE_COUNT = 20 // AC: "last 20 alerts"
// Animation name in AlertsPanel.css we listen for on the row to
// know when the 2 s tint has completed. Tied to the keyframe name
// rather than a React setTimeout so bursts don't reset each other's
// timers and `prefers-reduced-motion` (which shortens --d-slow to
// 0.01 ms) still fires the clear event promptly.
const NEW_TINT_ANIMATION_NAME = 'alerts-panel-tint'

// Tick frequency for the relative-timestamp display. 1 s is the
// highest resolution any of the format buckets needs ("Ns"); faster
// would just burn cycles. Below the 1-hour threshold we tick; once
// every visible row has crossed into absolute-time territory the
// ticker will keep running but its output becomes stable, so we
// don't bother gating.
const RELATIVE_TICK_MS = 1000
const ONE_HOUR_MS = 60 * 60 * 1000

/**
 * Lightweight "now" ticker used only by the alerts panel. Returns a
 * monotonically-advancing millisecond value and re-renders the
 * consumer every `RELATIVE_TICK_MS`.
 */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

export function AlertsPanel({
  alerts,
  selectedAlertId,
  dispatch,
}: AlertsPanelProps) {
  const selected = useMemo(
    () => alerts.find((a) => a.alert_id === selectedAlertId) ?? null,
    [alerts, selectedAlertId],
  )

  // Shared "now" bumped every second — drives relative timestamps on
  // every visible row without each one running its own interval.
  const now = useNow(RELATIVE_TICK_MS)

  // Lazy detail fetch: when the user picks a row whose frame_path
  // is undefined (WS push only), request the full row and enrich.
  const [frameLoading, setFrameLoading] = useState(false)
  const [frameError, setFrameError] = useState<string | null>(null)
  useEffect(() => {
    if (selected === null) {
      setFrameLoading(false)
      setFrameError(null)
      return
    }
    if (selected.frame_path !== undefined) {
      // Already enriched (or confirmed-null from a prior REST fetch).
      setFrameLoading(false)
      setFrameError(null)
      return
    }
    let cancelled = false
    setFrameLoading(true)
    setFrameError(null)
    fetchAlertById(selected.alert_id)
      .then((row) => {
        if (cancelled) return
        if (row === null) {
          // Alert got reset-wiped between click and fetch — surface
          // it honestly instead of spinning forever.
          setFrameError('Alert is no longer available.')
          return
        }
        dispatch({ type: 'alerts/enrich', alert: row })
      })
      .catch((err) => {
        if (cancelled) return
        console.error('[AlertsPanel] fetchAlertById failed', err)
        setFrameError('Could not load alert detail.')
      })
      .finally(() => {
        if (!cancelled) setFrameLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selected, dispatch])

  const visibleAlerts = alerts.slice(0, LIST_VISIBLE_COUNT)

  return (
    <aside className="alerts-panel" aria-label="Recent alerts">
      <header className="alerts-panel__header">
        <h2 className="alerts-panel__title">Alerts</h2>
        <span className="alerts-panel__count" aria-live="polite">
          {alerts.length === 0 ? '—' : `${alerts.length} total`}
        </span>
      </header>

      {alerts.length === 0 ? (
        <div className="alerts-panel__empty" role="status">
          <p className="alerts-panel__empty-title">No alerts yet</p>
          <p className="alerts-panel__empty-body">
            Draw a zone and cross it to trigger an alert.
          </p>
        </div>
      ) : (
        <ul className="alerts-panel__list" role="list">
          {visibleAlerts.map((alert) => (
            <AlertRow
              key={alert.alert_id}
              alert={alert}
              selected={alert.alert_id === selectedAlertId}
              now={now}
              onSelect={() =>
                dispatch({ type: 'alerts/select', alertId: alert.alert_id })
              }
              onTintEnd={() =>
                dispatch({
                  type: 'alerts/clear-new',
                  alertIds: [alert.alert_id],
                })
              }
            />
          ))}
        </ul>
      )}

      <AlertDetail alert={selected} loading={frameLoading} error={frameError} />
    </aside>
  )
}

interface AlertRowProps {
  alert: Alert
  selected: boolean
  now: number
  onSelect: () => void
  /** Fires when the row's own 2 s "new-alert" tint animation ends.
   * Dispatched regardless of whether `isNew` is still true — the
   * reducer's `alerts/clear-new` is idempotent. */
  onTintEnd: () => void
}

function AlertRow({
  alert,
  selected,
  now,
  onSelect,
  onTintEnd,
}: AlertRowProps) {
  const relativeTime = formatRelative(alert.timestamp, now)
  const fullIso = alert.timestamp

  const handleAnimationEnd = (e: AnimationEvent<HTMLButtonElement>) => {
    // Two CSS animations run concurrently on a new row (bg tint +
    // timestamp color). Only fire the clear on the primary tint so
    // we don't dispatch twice.
    if (e.animationName === NEW_TINT_ANIMATION_NAME) {
      onTintEnd()
    }
  }

  return (
    <li
      className="alerts-panel__row"
      data-selected={selected ? 'true' : 'false'}
      data-new={alert.isNew ? 'true' : 'false'}
      data-event={alert.event_type}
    >
      <button
        type="button"
        className="alerts-panel__row-btn"
        onClick={onSelect}
        onAnimationEnd={handleAnimationEnd}
        aria-current={selected ? 'true' : undefined}
        aria-label={`${alert.object_class} ${alert.event_type}, ${relativeTime}`}
      >
        <AlertThumb alert={alert} />
        <span className="alerts-panel__meta">
          <span className="alerts-panel__type-row">
            <span
              className={`alerts-panel__event alerts-panel__event--${alert.event_type}`}
            >
              {alert.event_type}
            </span>
            <span className="alerts-panel__class">{alert.object_class}</span>
          </span>
          <span className="alerts-panel__sub">
            #{alert.track_id}
          </span>
        </span>
        <span className="alerts-panel__time" title={fullIso}>
          {relativeTime}
        </span>
      </button>
    </li>
  )
}

/**
 * 56×42 thumbnail. When the alert has a `frame_path`, shows the
 * saved JPEG (browser caches it on the first hit). Otherwise a
 * gradient placeholder — same pattern the design-specs mock uses
 * so empty rows don't visually collapse.
 */
function AlertThumb({ alert }: { alert: Alert }) {
  if (alert.frame_path) {
    return (
      <img
        src={frameUrl(alert.frame_path)}
        alt=""
        className="alerts-panel__thumb"
        width={56}
        height={42}
        loading="lazy"
      />
    )
  }
  return <span className="alerts-panel__thumb alerts-panel__thumb--empty" aria-hidden="true" />
}

interface AlertDetailProps {
  alert: Alert | null
  loading: boolean
  error: string | null
}

function AlertDetail({ alert, loading, error }: AlertDetailProps) {
  if (alert === null) {
    return (
      <section className="alerts-panel__detail" aria-label="Alert detail">
        <p className="alerts-panel__detail-placeholder">
          Select an alert to see its saved frame.
        </p>
      </section>
    )
  }

  return (
    <section className="alerts-panel__detail" aria-label="Alert detail">
      <div className="alerts-panel__frame-wrap">
        {loading ? (
          <div className="alerts-panel__frame-state" role="status">
            Loading frame…
          </div>
        ) : error !== null ? (
          <div className="alerts-panel__frame-state alerts-panel__frame-state--error">
            {error}
          </div>
        ) : alert.frame_path ? (
          <img
            src={frameUrl(alert.frame_path)}
            alt={`Frame for ${alert.object_class} ${alert.event_type}`}
            className="alerts-panel__frame"
            width={320}
            height={240}
          />
        ) : (
          <div className="alerts-panel__frame-state">
            No frame saved for this alert.
          </div>
        )}
      </div>

      <dl className="alerts-panel__meta-grid">
        <div className="alerts-panel__meta-row">
          <dt>Class</dt>
          <dd>{alert.object_class}</dd>
        </div>
        <div className="alerts-panel__meta-row">
          <dt>Event</dt>
          <dd>{alert.event_type}</dd>
        </div>
        <div className="alerts-panel__meta-row">
          <dt>Track</dt>
          <dd>#{alert.track_id}</dd>
        </div>
        <div className="alerts-panel__meta-row">
          <dt>Time</dt>
          <dd title={alert.timestamp}>{formatHhmmss(alert.timestamp)}</dd>
        </div>
      </dl>

      <AlertActions alert={alert} />
    </section>
  )
}

interface AlertActionsProps {
  alert: Alert
}

/**
 * Download + Copy actions for the selected alert.
 *
 * Download: `fetch → Blob → createObjectURL → anchor.click → revoke`.
 * Using a plain `<a download>` with a cross-origin `href` would let
 * the browser override our suggested filename (and, on Safari, just
 * navigate). The blob round-trip is the only reliable cross-origin
 * pattern.
 *
 * Copy: `navigator.clipboard.writeText`. Gracefully surfaces a
 * "Copied!" state for 1.5 s. The JSON is the canonical wire shape
 * — same keys the server emits — so downstream tooling (grep, jq)
 * sees exactly what the backend sent.
 */
function AlertActions({ alert }: AlertActionsProps) {
  const [copyState, setCopyState] = useState<'idle' | 'ok' | 'err'>('idle')
  const [downloadState, setDownloadState] = useState<'idle' | 'busy' | 'err'>(
    'idle',
  )
  // Reset transient button labels on alert change so clicking between
  // rows doesn't strand a stale "Copied!" message on the wrong entry.
  // Uses React 18's "adjust state during render" pattern
  // (https://react.dev/reference/react/useState#storing-information-from-previous-renders)
  // — legal because the ref flip guards against the infinite loop,
  // and `setState` during render of the SAME component is allowed.
  // Do not "fix" this into a useEffect — that would cause a second
  // render pass every time the selected row changes.
  const lastSelectedRef = useRef<string | null>(null)
  if (lastSelectedRef.current !== alert.alert_id) {
    lastSelectedRef.current = alert.alert_id
    if (copyState !== 'idle') setCopyState('idle')
    if (downloadState !== 'idle') setDownloadState('idle')
  }

  const canDownload = Boolean(alert.frame_path)

  const handleDownload = async () => {
    if (!alert.frame_path) return
    setDownloadState('busy')
    let url: string | null = null
    let anchor: HTMLAnchorElement | null = null
    try {
      const res = await fetch(frameUrl(alert.frame_path))
      if (!res.ok) throw new Error(`fetch ${res.status}`)
      const blob = await res.blob()
      url = URL.createObjectURL(blob)
      anchor = document.createElement('a')
      anchor.href = url
      // Belt-and-suspenders: `frame_path` is already basenamed by
      // the REST handler (NW-1403 _present), but a stray slash
      // would turn into a directory prefix in the browser's save
      // dialog on some platforms — pop it defensively.
      anchor.download = alert.frame_path.split('/').pop() ?? alert.frame_path
      document.body.appendChild(anchor)
      anchor.click()
      setDownloadState('idle')
    } catch (err) {
      console.error('[AlertsPanel] download failed', err)
      setDownloadState('err')
      window.setTimeout(() => setDownloadState('idle'), 1500)
    } finally {
      // Always clean up — even if fetch/createObjectURL throws
      // partway, leaking the object URL would accumulate in a
      // long-lived session and orphan DOM anchors.
      anchor?.remove()
      if (url !== null) URL.revokeObjectURL(url)
    }
  }

  const handleCopy = async () => {
    try {
      // Strip the UI-only `isNew` flag so the clipboard payload is
      // the canonical event shape that matches what the server
      // emits on the wire + persists in SQLite.
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const { isNew: _isNew, ...canonical } = alert
      await navigator.clipboard.writeText(JSON.stringify(canonical, null, 2))
      setCopyState('ok')
      window.setTimeout(() => setCopyState('idle'), 1500)
    } catch (err) {
      console.error('[AlertsPanel] clipboard write failed', err)
      setCopyState('err')
      window.setTimeout(() => setCopyState('idle'), 1500)
    }
  }

  return (
    <div className="alerts-panel__actions">
      <button
        type="button"
        className="btn"
        onClick={handleDownload}
        disabled={!canDownload || downloadState === 'busy'}
        title={canDownload ? undefined : 'No saved frame for this alert'}
      >
        {downloadState === 'busy'
          ? 'Downloading…'
          : downloadState === 'err'
            ? 'Download failed'
            : 'Download frame'}
      </button>
      <button
        type="button"
        className="btn"
        onClick={handleCopy}
      >
        {copyState === 'ok'
          ? 'Copied!'
          : copyState === 'err'
            ? 'Copy failed'
            : 'Copy JSON'}
      </button>
    </div>
  )
}

/**
 * Parse ISO 8601 into HH:MM:SS in the viewer's local timezone.
 * Used in the detail pane metadata grid where absolute-time is
 * useful regardless of recency. `Date.toLocaleTimeString` with
 * `en-GB` forces 24-hour format.
 */
function formatHhmmss(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/**
 * Design-specs §Interactions: alert rows show relative time
 * (`just now` / `Ns` / `Nm`) for the first hour, absolute
 * `HH:MM:SS` thereafter. `now` is threaded in from a parent 1 s
 * ticker so every visible row re-renders together.
 */
function formatRelative(iso: string, now: number): string {
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return iso
  const deltaMs = now - t
  // Clock-skew defense: server-stamped timestamps can come in ~ms
  // ahead of the client's `Date.now()` (or far ahead if the client
  // clock is out of sync). Treat any future-tense delta as "just
  // now" explicitly so a refactor of the 10 s threshold doesn't
  // accidentally expose negative values as `-5 s`.
  if (deltaMs < 0) return 'just now'
  if (deltaMs < 10_000) return 'just now'
  if (deltaMs < 60_000) return `${Math.floor(deltaMs / 1000)} s`
  if (deltaMs < ONE_HOUR_MS) return `${Math.floor(deltaMs / 60_000)} m`
  return formatHhmmss(iso)
}
