import type { AnimationEvent, Dispatch } from 'react'
import { useEffect, useMemo, useState } from 'react'

import { fetchAlertById, frameUrl } from '../services/alertsClient'
import type { Action, Alert } from '../types'
import './AlertsPanel.css'

/**
 * Alerts side-panel (NW-1404).
 *
 * Vertical layout inside the 30% right column of the main monitor
 * view: scrollable list on top, selected alert's saved frame +
 * metadata on bottom. The original JIRA AC said "Left: list /
 * Right: frame" — at the target viewport (≥1440) the alerts column
 * is ~430 px, too narrow for a horizontal split that includes a
 * readable 4:3 preview. Going vertical keeps everything at spec
 * dimensions; flagged as a deliberate deviation in the commit/PR.
 *
 * Design-specs §2 Live Monitoring · default + §4 Alert firing:
 *   - Panel `border-left: var(--line-2)`, 56 px header.
 *   - Row tint `rgba(24,225,217,0.06)` for 2 s on arrival (token
 *     `--d-slow`), timestamp in cyan during the tint, linear fade.
 *   - Hover / selected rows use the standard ink/bg tokens.
 *
 * Data flow:
 *   - App.tsx's useEffect fires `fetchRecentAlerts` on mount and
 *     dispatches `alerts/bootstrap`. This component never fetches
 *     the list — it's a pure projection of `state.alerts`.
 *   - WS pushes land via `ws/frame` with `isNew: true` on each
 *     merged event. We schedule a `alerts/clear-new` after 2 s so
 *     the tint fades.
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

export function AlertsPanel({
  alerts,
  selectedAlertId,
  dispatch,
}: AlertsPanelProps) {
  const selected = useMemo(
    () => alerts.find((a) => a.alert_id === selectedAlertId) ?? null,
    [alerts, selectedAlertId],
  )

  // Each row dispatches `alerts/clear-new` when its own tint
  // animation completes (see `AlertRow` below). No parent-level
  // timer — bursts don't reset each other's clocks, and the
  // browser-clock fade stays in lockstep with the keyframe.

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
        {/* Only the count is aria-live; the outer aside would
         * otherwise make screen readers re-read the panel label
         * on every new arrival. */}
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

      <AlertDetail
        alert={selected}
        loading={frameLoading}
        error={frameError}
      />
    </aside>
  )
}

interface AlertRowProps {
  alert: Alert
  selected: boolean
  onSelect: () => void
  /** Fires when the row's own 2 s "new-alert" tint animation ends.
   * Dispatched regardless of whether `isNew` is still true — the
   * reducer's `alerts/clear-new` is idempotent. */
  onTintEnd: () => void
}

function AlertRow({ alert, selected, onSelect, onTintEnd }: AlertRowProps) {
  const hhmmss = formatHhmmss(alert.timestamp)

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
        aria-label={`${alert.object_class} ${alert.event_type} at ${hhmmss}`}
      >
        <span className="alerts-panel__time">{hhmmss}</span>
        <span className="alerts-panel__class">{alert.object_class}</span>
        <span
          className={`alerts-panel__event alerts-panel__event--${alert.event_type}`}
        >
          {alert.event_type}
        </span>
      </button>
    </li>
  )
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
            alt={`Frame for ${alert.object_class} ${alert.event_type} at ${formatHhmmss(alert.timestamp)}`}
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

      <dl className="alerts-panel__meta">
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
          <dd>{formatHhmmss(alert.timestamp)}</dd>
        </div>
      </dl>
    </section>
  )
}

/**
 * Parse ISO 8601 into HH:MM:SS in the viewer's local timezone. The
 * spec's alert row format is "HH:MM:SS | class | enter|exit" — two
 * colons, no date, no timezone. `Date.toLocaleTimeString` with the
 * en-GB locale forces 24-hour format regardless of system locale,
 * which keeps the demo readable on machines set to 12-hour clocks.
 */
function formatHhmmss(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso // pass through unparseable
  return d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
