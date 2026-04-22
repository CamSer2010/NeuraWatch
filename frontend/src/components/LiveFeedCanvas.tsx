import { useEffect, useRef, useState } from 'react'

import type { Detection } from '../types'
import './LiveFeedCanvas.css'

/**
 * Live detection overlay (NW-1204).
 *
 * A single 640×480 canvas stacked above the `<video>` in WebcamView's
 * stage. Draws bounding boxes + labels for every detection in a rAF
 * loop that reads the latest payload from a ref — not from props — so
 * the loop keeps steady cadence regardless of WS arrival rate.
 *
 * Design-specs §Live Monitoring:
 *   - 2px solid cyan stroke, radius 2
 *   - Label: mono 10/600, `--ink-on-cyan` on cyan fill, above the bbox
 *   - Hide the `· NN%` confidence suffix when bbox width < 80 px
 *
 * Separator glyph: the design-specs handoff uses `·` (U+00B7, middle
 * dot) in its label mock (`person · #4732 · IN · 0.93`). We keep the
 * same glyph for consistency across live and in-zone states — JIRA
 * AC's plain-space format is superseded by the handoff.
 *
 * JIRA NW-1204 AC asks for per-class colors (person=green,
 * vehicle=orange, bicycle=blue). The later design-specs handoff says
 * "2px solid cyan" for live bboxes and reserves amber for the in-zone
 * retint (NW-1303). Design-specs wins per project convention; class
 * is still in the label text, so color was redundant.
 *
 * Coordinate contract (ratified decision #5): bboxes are normalized
 * 0–1 against the 640×480 processed frame. Stage is locked 640×480
 * (--capture-w/h), so `x * CANVAS_W` is the whole story.
 *
 * zone_version gate (spec §WebSocket Protocol): "Only render
 * detections whose frame matches current zone_version". NW-1301 adds
 * a client-side zoneVersion; until then the client's implicit value
 * is 0, which matches every server frame (zone_version defaults 0).
 * `lastZoneVersion` is threaded through now so the follow-up ticket
 * only touches the compare, not the plumbing.
 *
 * Pointer events are off — NW-1301's polygon tool owns mouse input
 * on its own canvas layer.
 *
 * Sizing: the intrinsic `canvas.width/height` are set imperatively in
 * the mount effect (NOT as JSX attributes). React writing JSX-sourced
 * `width={640}` on every re-render would otherwise clobber the
 * HiDPI-scaled backing store whenever a parent rerenders — which at
 * 15 FPS is every time a detection_result arrives.
 */

export interface LiveFeedCanvasProps {
  detections: Detection[]
  lastZoneVersion: number
  /** Mirrors `AppState.cameraActive && status === 'live'` — drives
   * the rAF lifecycle and lets us blank the canvas on stop without
   * racing the parent's unmount. */
  active: boolean
}

const CANVAS_W = 640
const CANVAS_H = 480

const BBOX_WIDTH = 2
const BBOX_RADIUS = 2

const LABEL_FONT = '600 10px "JetBrains Mono", ui-monospace, monospace'
const LABEL_PAD_X = 4
const LABEL_HEIGHT = 14 // font 10 + 2px padding top/bottom

// Hide `· 94%` confidence suffix when the bbox is narrower than this,
// per design-specs §Payload → UI mapping.
const CONF_MIN_BBOX_WIDTH = 80

// Debounce matches the header FPS tick (500 ms) so assistive tech
// doesn't get spammed when detection counts flicker frame-to-frame.
const ARIA_DEBOUNCE_MS = 500

export function LiveFeedCanvas({
  detections,
  lastZoneVersion,
  active,
}: LiveFeedCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const detectionsRef = useRef<Detection[]>(detections)
  // NW-1301: compare to client zone version here; render only when
  // `zoneVersionRef.current === clientZoneVersion`.
  const zoneVersionRef = useRef<number>(lastZoneVersion)

  // Mirror the latest props into refs — the rAF loop reads these so
  // we don't tear down and rebuild the loop on every frame.
  detectionsRef.current = detections
  zoneVersionRef.current = lastZoneVersion

  const [ariaLabel, setAriaLabel] = useState<string>('No detections')

  // Resolve CSS custom properties to concrete colors once — canvas
  // 2D context can't consume `var(--cyan)`. TODO: if tokens.css ever
  // adds a light/dark theme toggle at runtime, re-resolve on change.
  const resolvedColorsRef = useRef<{ stroke: string; ink: string } | null>(null)
  useEffect(() => {
    const style = getComputedStyle(document.documentElement)
    resolvedColorsRef.current = {
      stroke: style.getPropertyValue('--cyan').trim() || '#18E1D9',
      ink: style.getPropertyValue('--ink-on-cyan').trim() || '#051F1E',
    }
  }, [])

  // Size the canvas for HiDPI once on mount. Kept separate from the
  // rAF effect so `active` toggles don't reset the backing store.
  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null) return
    const ctx = canvas.getContext('2d')
    if (ctx === null) return
    const dpr = Math.max(1, window.devicePixelRatio || 1)
    canvas.width = CANVAS_W * dpr
    canvas.height = CANVAS_H * dpr
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }, [])

  // rAF draw loop. Starts when `active` flips true, stops on false.
  // Reads detections from refs, so identity is stable and a new
  // detection_result doesn't re-create the loop.
  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null) return
    const ctx = canvas.getContext('2d')
    if (ctx === null) return

    if (!active) {
      ctx.clearRect(0, 0, CANVAS_W, CANVAS_H)
      return
    }

    let raf = 0
    let stopped = false

    const draw = () => {
      if (stopped) return
      ctx.clearRect(0, 0, CANVAS_W, CANVAS_H)

      const colors = resolvedColorsRef.current
      const stroke = colors?.stroke ?? '#18E1D9'
      const ink = colors?.ink ?? '#051F1E'

      ctx.lineWidth = BBOX_WIDTH
      ctx.strokeStyle = stroke
      ctx.font = LABEL_FONT
      ctx.textBaseline = 'middle'

      for (const det of detectionsRef.current) {
        const [nx1, ny1, nx2, ny2] = det.bbox
        const x1 = nx1 * CANVAS_W
        const y1 = ny1 * CANVAS_H
        const x2 = nx2 * CANVAS_W
        const y2 = ny2 * CANVAS_H
        const w = x2 - x1
        const h = y2 - y1
        if (w <= 0 || h <= 0) continue

        // Rounded-corner stroke. roundRect is in ChromeN, FF > 112,
        // Safari > 16 — within our demo target baseline.
        ctx.beginPath()
        ctx.roundRect(x1, y1, w, h, BBOX_RADIUS)
        ctx.stroke()

        const trackSuffix =
          det.track_id !== null && det.track_id !== undefined
            ? ` · #${det.track_id}`
            : ''
        const confSuffix =
          w >= CONF_MIN_BBOX_WIDTH
            ? ` · ${Math.round(det.confidence * 100)}%`
            : ''
        const labelText = `${det.objectClass}${trackSuffix}${confSuffix}`

        const textWidth = ctx.measureText(labelText).width
        const labelW = textWidth + LABEL_PAD_X * 2
        // Label sits above the bbox. If the bbox hugs the top edge
        // and the stripe would clip, flip it below the bottom stroke
        // instead — never overlaps the subject inside the bbox.
        const aboveY = y1 - LABEL_HEIGHT
        const labelY = aboveY >= 0 ? aboveY : y2
        const labelX = x1

        ctx.fillStyle = stroke
        ctx.fillRect(labelX, labelY, labelW, LABEL_HEIGHT)

        ctx.fillStyle = ink
        ctx.fillText(labelText, labelX + LABEL_PAD_X, labelY + LABEL_HEIGHT / 2)
      }

      raf = requestAnimationFrame(draw)
    }

    raf = requestAnimationFrame(draw)
    return () => {
      stopped = true
      cancelAnimationFrame(raf)
      ctx.clearRect(0, 0, CANVAS_W, CANVAS_H)
    }
  }, [active])

  // Debounced aria-label. React owns the attribute (via JSX) so the
  // summary survives across re-renders without being clobbered. If
  // the parent unmounts within the debounce window, the cleanup
  // cancels the pending update — acceptable because the canvas is
  // gone anyway.
  useEffect(() => {
    const id = setTimeout(() => {
      setAriaLabel(summarizeForAria(detections))
    }, ARIA_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [detections])

  return (
    <canvas
      ref={canvasRef}
      className="live-feed-canvas"
      role="img"
      aria-label={ariaLabel}
      data-zone-version={lastZoneVersion}
    />
  )
}

function summarizeForAria(detections: Detection[]): string {
  if (detections.length === 0) return 'No detections'
  const counts = new Map<string, number>()
  for (const d of detections) {
    counts.set(d.objectClass, (counts.get(d.objectClass) ?? 0) + 1)
  }
  const parts: string[] = []
  for (const [cls, n] of counts) {
    parts.push(`${n} ${pluralize(cls, n)}`)
  }
  return `Detections: ${parts.join(', ')}`
}

function pluralize(cls: string, n: number): string {
  if (n === 1) return cls
  if (cls === 'person') return 'people'
  return `${cls}s`
}
