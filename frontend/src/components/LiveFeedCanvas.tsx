import { useEffect, useRef, useState } from 'react'

import type { Detection, Point } from '../types'
import './LiveFeedCanvas.css'

/**
 * Live detection + zone overlay (NW-1204 + NW-1301).
 *
 * A single 640×480 canvas stacked above the `<video>` in WebcamView's
 * stage. Ratified decision #12: one canvas overlay for the whole
 * live view. This component now draws:
 *   - Detection bboxes + labels (NW-1204)
 *   - Polygon zone — closed (committed) or in-progress (NW-1301)
 *   - Rubber-band preview from the last vertex to the cursor
 *     while in drawing mode
 *
 * All drawing happens in a single rAF loop that reads the latest
 * payload from refs so per-detection-result rerenders don't rebuild
 * the loop.
 *
 * Design-specs §Live Monitoring:
 *   - Detection bbox: 2px solid cyan, radius 2; label mono 10/600,
 *     `--ink-on-cyan` on cyan fill, above the bbox (confidence hidden
 *     when bbox width < 80 px)
 *   - Closed polygon: fill `rgba(24,225,217,0.06)`, stroke cyan 2px
 *   - Drawing vertices: 5px filled cyan + 2px `--bg-1` stroke ring
 *   - Rubber-band preview: dashed 6-4, stroke 1.5, cyan; hidden on
 *     mouseleave
 *
 * Close-zone UX (spec §3): Close Zone button is the ONLY commit
 * path — no double-click, no click-near-first-vertex. This component
 * never synthesizes a close; the toolbar dispatches.
 *
 * Coordinate contract (ratified decision #5): every point (bbox,
 * polygon vertex, rubber-band endpoint) lives in 0–1 normalized
 * against the 640×480 processed frame. Rendering multiplies by
 * CANVAS_W/H; input handlers divide by the canvas getBoundingClientRect
 * size (which can be CSS-scaled).
 *
 * Sizing: intrinsic canvas.width/height are set imperatively in a
 * mount-only effect (NOT via JSX attrs) so per-render reconciliation
 * can't clobber the HiDPI backing store.
 */

export interface LiveFeedCanvasProps {
  detections: Detection[]
  lastZoneVersion: number

  /** When true, enable rAF + detection rendering (spec §System
   * States: bboxes only in `live`). Polygon rendering runs
   * independently via `cameraActive`-driven gates below. */
  active: boolean

  // --- NW-1301 zone props ---
  /** Mirrors `AppState.cameraActive` — the polygon is only meaningful
   * while a source is attached. Used to gate the rAF loop so the
   * polygon stays visible during `disconnected` (source still paused
   * on last frame) without requiring full `live`. */
  cameraActive: boolean
  zoneDrawing: boolean
  zonePoints: Point[]
  zoneClosed: boolean
  onAddPoint: (p: Point) => void
  onRemoveLastPoint: () => void
  onCloseZone: () => void
  onCancelDraw: () => void
}

const CANVAS_W = 640
const CANVAS_H = 480

const BBOX_WIDTH = 2
const BBOX_RADIUS = 2

const LABEL_FONT = '600 10px "JetBrains Mono", ui-monospace, monospace'
const LABEL_PAD_X = 4
const LABEL_HEIGHT = 14

const CONF_MIN_BBOX_WIDTH = 80

const ARIA_DEBOUNCE_MS = 500

// Polygon visual constants — sourced from design-specs §Live
// Monitoring · drawing polygon.
const POLYGON_STROKE_WIDTH = 2
const POLYGON_FILL_RGBA = 'rgba(24, 225, 217, 0.06)'
const RUBBER_BAND_DASH: [number, number] = [6, 4]
const RUBBER_BAND_WIDTH = 1.5
const VERTEX_FILL_RADIUS = 2.5 // 5px diameter filled dot
const VERTEX_RING_RADIUS = 4 // extra ring for the --bg-1 stroke

const MIN_VERTICES_FOR_CLOSE = 3

export function LiveFeedCanvas({
  detections,
  lastZoneVersion,
  active,
  cameraActive,
  zoneDrawing,
  zonePoints,
  zoneClosed,
  onAddPoint,
  onRemoveLastPoint,
  onCloseZone,
  onCancelDraw,
}: LiveFeedCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  // Refs into the rAF loop: fresh props on every render write-through
  // here, and the loop reads these instead of closed-over values so
  // its identity stays stable across detection arrivals.
  const detectionsRef = useRef<Detection[]>(detections)
  const zonePointsRef = useRef<Point[]>(zonePoints)
  const zoneDrawingRef = useRef<boolean>(zoneDrawing)
  const zoneClosedRef = useRef<boolean>(zoneClosed)
  detectionsRef.current = detections
  zonePointsRef.current = zonePoints
  zoneDrawingRef.current = zoneDrawing
  zoneClosedRef.current = zoneClosed

  // Cursor position (normalized 0–1) for rubber-band preview. Kept
  // in a ref because pointermove fires far more often than React can
  // meaningfully re-render — the rAF loop polls this every frame.
  const cursorRef = useRef<Point | null>(null)

  const [ariaLabel, setAriaLabel] = useState<string>('No detections')

  // Resolve CSS custom properties to concrete colors once — canvas
  // 2D context can't consume `var(--cyan)`.
  const resolvedColorsRef = useRef<{
    cyan: string
    ink: string
    bg1: string
  } | null>(null)
  useEffect(() => {
    const style = getComputedStyle(document.documentElement)
    resolvedColorsRef.current = {
      cyan: style.getPropertyValue('--cyan').trim() || '#18E1D9',
      ink: style.getPropertyValue('--ink-on-cyan').trim() || '#051F1E',
      bg1: style.getPropertyValue('--bg-1').trim() || '#0F1620',
    }
  }, [])

  // Size the canvas for HiDPI once on mount.
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

  // rAF draw loop. Starts when there's anything to render:
  //   - active (bboxes streaming)
  //   - cameraActive + drawing (vertices + rubber-band)
  //   - cameraActive + closed (committed polygon overlay)
  // Otherwise clears once and stays idle.
  const shouldRender =
    active || (cameraActive && (zoneDrawing || zoneClosed))

  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null) return
    const ctx = canvas.getContext('2d')
    if (ctx === null) return

    if (!shouldRender) {
      ctx.clearRect(0, 0, CANVAS_W, CANVAS_H)
      return
    }

    let raf = 0
    let stopped = false

    const draw = () => {
      if (stopped) return
      ctx.clearRect(0, 0, CANVAS_W, CANVAS_H)

      const colors = resolvedColorsRef.current
      const cyan = colors?.cyan ?? '#18E1D9'
      const ink = colors?.ink ?? '#051F1E'
      const bg1 = colors?.bg1 ?? '#0F1620'

      // --- Polygon layer (drawn UNDER the bboxes) -----------------
      // Bboxes sit over the polygon so a detection inside the zone
      // is fully visible; the fill is intentionally faint anyway.
      const pts = zonePointsRef.current
      const closed = zoneClosedRef.current
      const drawing = zoneDrawingRef.current

      if (pts.length > 0 && (drawing || closed)) {
        ctx.save()

        if (closed && pts.length >= 2) {
          // Solid outlined + faintly filled polygon.
          ctx.beginPath()
          ctx.moveTo(pts[0][0] * CANVAS_W, pts[0][1] * CANVAS_H)
          for (let i = 1; i < pts.length; i++) {
            ctx.lineTo(pts[i][0] * CANVAS_W, pts[i][1] * CANVAS_H)
          }
          ctx.closePath()
          ctx.fillStyle = POLYGON_FILL_RGBA
          ctx.fill()
          ctx.lineWidth = POLYGON_STROKE_WIDTH
          ctx.strokeStyle = cyan
          ctx.stroke()
        }

        if (drawing) {
          // In-progress stroke: solid cyan polyline between placed
          // vertices, plus dashed rubber-band from the last vertex to
          // the current cursor.
          if (pts.length >= 2) {
            ctx.beginPath()
            ctx.moveTo(pts[0][0] * CANVAS_W, pts[0][1] * CANVAS_H)
            for (let i = 1; i < pts.length; i++) {
              ctx.lineTo(pts[i][0] * CANVAS_W, pts[i][1] * CANVAS_H)
            }
            ctx.lineWidth = POLYGON_STROKE_WIDTH
            ctx.strokeStyle = cyan
            ctx.setLineDash([])
            ctx.stroke()
          }

          const cursor = cursorRef.current
          if (cursor !== null && pts.length >= 1) {
            const last = pts[pts.length - 1]
            ctx.beginPath()
            ctx.moveTo(last[0] * CANVAS_W, last[1] * CANVAS_H)
            ctx.lineTo(cursor[0] * CANVAS_W, cursor[1] * CANVAS_H)
            ctx.lineWidth = RUBBER_BAND_WIDTH
            ctx.strokeStyle = cyan
            ctx.setLineDash(RUBBER_BAND_DASH)
            ctx.stroke()
            ctx.setLineDash([])
          }

          // Vertices — filled cyan dot with a bg-1 ring so they sit
          // cleanly over whatever's in the video frame.
          for (const [nx, ny] of pts) {
            const x = nx * CANVAS_W
            const y = ny * CANVAS_H
            ctx.beginPath()
            ctx.arc(x, y, VERTEX_RING_RADIUS, 0, Math.PI * 2)
            ctx.fillStyle = bg1
            ctx.fill()
            ctx.beginPath()
            ctx.arc(x, y, VERTEX_FILL_RADIUS, 0, Math.PI * 2)
            ctx.fillStyle = cyan
            ctx.fill()
          }
        }

        ctx.restore()
      }

      // --- Detection layer (drawn OVER the polygon) ---------------
      if (active) {
        ctx.lineWidth = BBOX_WIDTH
        ctx.strokeStyle = cyan
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
          const aboveY = y1 - LABEL_HEIGHT
          const labelY = aboveY >= 0 ? aboveY : y2
          const labelX = x1

          ctx.fillStyle = cyan
          ctx.fillRect(labelX, labelY, labelW, LABEL_HEIGHT)

          ctx.fillStyle = ink
          ctx.fillText(
            labelText,
            labelX + LABEL_PAD_X,
            labelY + LABEL_HEIGHT / 2,
          )
        }
      }

      raf = requestAnimationFrame(draw)
    }

    raf = requestAnimationFrame(draw)
    return () => {
      stopped = true
      cancelAnimationFrame(raf)
      ctx.clearRect(0, 0, CANVAS_W, CANVAS_H)
    }
  }, [shouldRender, active])

  // Debounced aria-label for the detection summary.
  useEffect(() => {
    const id = setTimeout(() => {
      setAriaLabel(summarizeForAria(detections))
    }, ARIA_DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [detections])

  // Window-level keydown while drawing so keys work even if the
  // canvas itself isn't focused. Escape / Enter / Backspace per
  // design-specs §Accessibility.
  useEffect(() => {
    if (!zoneDrawing) return
    const onKey = (e: KeyboardEvent) => {
      // Don't hijack typing — though no text inputs exist today, an
      // upload filename field or Alert Detail copy action will want
      // Backspace/Enter for themselves later.
      const target = e.target as HTMLElement | null
      if (target !== null) {
        const tag = target.tagName
        if (
          tag === 'INPUT' ||
          tag === 'TEXTAREA' ||
          target.isContentEditable
        ) {
          return
        }
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancelDraw()
      } else if (e.key === 'Enter') {
        if (zonePointsRef.current.length >= MIN_VERTICES_FOR_CLOSE) {
          e.preventDefault()
          onCloseZone()
        }
      } else if (e.key === 'Backspace') {
        if (zonePointsRef.current.length > 0) {
          e.preventDefault()
          onRemoveLastPoint()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [zoneDrawing, onCancelDraw, onCloseZone, onRemoveLastPoint])

  // Pointer handlers — only active while drawing. Converts client
  // coords → normalized 0–1 against the canvas CSS box.
  function toNormalized(e: React.PointerEvent<HTMLCanvasElement>): Point {
    const canvas = e.currentTarget
    const rect = canvas.getBoundingClientRect()
    const nx = (e.clientX - rect.left) / rect.width
    const ny = (e.clientY - rect.top) / rect.height
    return [clamp01(nx), clamp01(ny)]
  }

  function handlePointerDown(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!zoneDrawing) return
    if (e.button !== 0) return // left click only
    onAddPoint(toNormalized(e))
  }

  function handlePointerMove(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!zoneDrawing) return
    cursorRef.current = toNormalized(e)
  }

  function handlePointerLeave() {
    if (!zoneDrawing) return
    cursorRef.current = null
  }

  return (
    <canvas
      ref={canvasRef}
      className="live-feed-canvas"
      data-drawing={zoneDrawing ? 'true' : 'false'}
      role="img"
      aria-label={ariaLabel}
      data-zone-version={lastZoneVersion}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
    />
  )
}

function clamp01(v: number): number {
  if (v < 0) return 0
  if (v > 1) return 1
  return v
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
