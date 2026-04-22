import type { Dispatch } from 'react'

import type { Action } from '../types'
import '../styles/buttons.css'
import './PolygonToolbar.css'

/**
 * Polygon toolbar (NW-1301).
 *
 * Control strip below the feed. Spec §2 Live Monitoring · default
 * names this the "Zone" group of the control strip. Three buttons:
 *
 *   - **Draw / Redraw** — enters drawing mode. Label flips to
 *     "Redraw" once a polygon is committed, per spec §Control strip.
 *   - **Close Zone** — primary cyan; disabled until ≥3 points placed
 *     (spec §3). Only visible while drawing.
 *   - **Clear** — wipes the polygon. Disabled when there's nothing to
 *     clear (no drawing and no committed polygon).
 *
 * All state flows through props; the toolbar dispatches plain zone/*
 * actions. Sending `zone_update` / `zone_clear` to the backend is
 * handled by an effect in App.tsx that watches `zoneVersion`.
 *
 * Visibility: the toolbar is rendered by WebcamView when the camera
 * is active. Spec §System States also shows the "Draw a zone to
 * enable alerts" hint while there's no committed polygon — that
 * pill lives next to these buttons in WebcamView.
 */

export interface PolygonToolbarProps {
  dispatch: Dispatch<Action>
  drawing: boolean
  closed: boolean
  points: number
}

const MIN_VERTICES_FOR_CLOSE = 3

export function PolygonToolbar({
  dispatch,
  drawing,
  closed,
  points,
}: PolygonToolbarProps) {
  const canClose = drawing && points >= MIN_VERTICES_FOR_CLOSE
  const canClear = drawing || closed || points > 0
  const drawLabel = closed ? 'Redraw zone' : 'Draw zone'

  return (
    <div
      className="polygon-toolbar"
      role="toolbar"
      aria-label="Polygon zone controls"
    >
      <button
        type="button"
        className="btn"
        onClick={() => dispatch({ type: 'zone/start-draw' })}
        disabled={drawing}
      >
        {drawLabel}
      </button>

      {drawing && (
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => dispatch({ type: 'zone/close' })}
          disabled={!canClose}
          aria-label={
            canClose
              ? 'Close zone'
              : `Close zone — need at least ${MIN_VERTICES_FOR_CLOSE} vertices`
          }
        >
          Close zone
        </button>
      )}

      <button
        type="button"
        className="btn"
        onClick={() => dispatch({ type: 'zone/clear' })}
        disabled={!canClear}
      >
        Clear
      </button>
    </div>
  )
}
