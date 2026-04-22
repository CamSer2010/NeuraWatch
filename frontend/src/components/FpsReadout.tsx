import { useEffect, useRef, useState } from 'react'

import './FpsReadout.css'

/**
 * FPS readout (NW-1502).
 *
 * Small mono readout that lives next to `<StatusBadge>` in the app
 * header. Backend already EMAs `stats.fps` (alpha=0.2) before it
 * hits the wire, but detection_result messages arrive at 10-15 Hz —
 * re-rendering the header number that fast both wastes paints and
 * makes the value illegible. Spec §Payload → UI mapping: "Update
 * every 500ms tick to avoid jitter."
 *
 * Deviation from JIRA AC ("updated at 1Hz"): the later design-specs
 * handoff locked 500ms; the spec wins by project convention. Integer
 * rounding kept per JIRA AC — decimals jitter visually even with the
 * EMA, and "26 FPS" reads fine in a demo.
 *
 * Only renders a value when `active` is true (spec §System States:
 * "FPS shown" only appears in `live`). Everywhere else we show a
 * single em-dash so the header width is stable across states.
 */

export interface FpsReadoutProps {
  fps: number | null
  /** Whether to run the tick and show a numeric value. True during
   * `live`; false for every other AppStatus so the readout collapses
   * to the placeholder without flickering on reconnect. */
  active: boolean
}

const TICK_MS = 500

export function FpsReadout({ fps, active }: FpsReadoutProps) {
  const latestRef = useRef<number | null>(fps)
  const [displayed, setDisplayed] = useState<number | null>(null)

  // Mirror the live prop into a ref so the interval reads the newest
  // value without forcing us to re-create the timer on every payload.
  latestRef.current = fps

  useEffect(() => {
    if (!active) {
      setDisplayed(null)
      return
    }
    // Snap to the current value immediately so the first tick isn't
    // a 500 ms blank window while we wait for setInterval.
    setDisplayed(latestRef.current)
    const id = setInterval(() => {
      setDisplayed(latestRef.current)
    }, TICK_MS)
    return () => clearInterval(id)
  }, [active])

  const valueText =
    displayed !== null && Number.isFinite(displayed)
      ? String(Math.round(displayed))
      : '—'

  return (
    <span
      className="fps-readout"
      aria-label={
        displayed !== null && Number.isFinite(displayed)
          ? `Frames per second: ${Math.round(displayed)}`
          : 'Frames per second unavailable'
      }
    >
      <span className="fps-readout__label">FPS</span>
      <span className="fps-readout__val">{valueText}</span>
    </span>
  )
}
