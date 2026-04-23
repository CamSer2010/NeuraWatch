import type { Dispatch } from 'react'

import type { Action, VideoSource } from '../types'
import './VideoSourcePanel.css'

/**
 * Source-switcher segmented control (NW-1202 + design-specs §03).
 *
 * Two buttons — Webcam | Upload. Click swaps `state.videoSource`,
 * which triggers the reducer's source/set case: polygon cleared,
 * zone_version bumped, any prior uploaded-video blob URL revoked.
 * The large view below (WebcamView or VideoUploadView) swaps on the
 * same state flip.
 *
 * Disable rule: toggling source is NOT allowed while an upload is
 * in flight or while the server is actively processing a clip. The
 * ratified single-active-session guard at the backend would refuse
 * a concurrent webcam claim anyway; the UI surface just mirrors
 * that so the operator sees why.
 */
export interface VideoSourcePanelProps {
  source: VideoSource
  dispatch: Dispatch<Action>
  disabled: boolean
}

export function VideoSourcePanel({
  source,
  dispatch,
  disabled,
}: VideoSourcePanelProps) {
  const change = (next: VideoSource) => {
    if (disabled || next === source) return
    dispatch({ type: 'source/set', source: next })
  }

  return (
    <div
      className="video-source-panel"
      role="radiogroup"
      aria-label="Video source"
      aria-disabled={disabled}
    >
      <button
        type="button"
        role="radio"
        aria-checked={source === 'webcam'}
        className="video-source-panel__btn"
        data-active={source === 'webcam' ? 'true' : 'false'}
        disabled={disabled}
        onClick={() => change('webcam')}
      >
        Webcam
      </button>
      <button
        type="button"
        role="radio"
        aria-checked={source === 'upload'}
        className="video-source-panel__btn"
        data-active={source === 'upload' ? 'true' : 'false'}
        disabled={disabled}
        onClick={() => change('upload')}
      >
        Upload
      </button>
    </div>
  )
}
