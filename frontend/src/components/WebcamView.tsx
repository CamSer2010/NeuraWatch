import type { Dispatch } from 'react'
import { useEffect, useRef } from 'react'

import { WS_URL } from '../config'
import { connectWs, disconnectWs, sendFrame } from '../services/wsClient'
import type { Action, AppState } from '../types'
import '../styles/buttons.css'
import './WebcamView.css'

// rAF drives the capture loop; the wsClient in-flight boolean is the
// actual backpressure gate — most frames are dropped before send.
// JPEG quality matches the backend benchmark (ratified decision #3).
const CAPTURE_QUALITY = 0.6

/**
 * Webcam input surface (NW-1201).
 *
 * Three-canvas taxonomy (one is a `<video>`, two are canvases):
 *   1. visible `<video>`  — the 640×480 webcam feed
 *   2. capture `<canvas>` — offscreen 640×480, NW-1203 draws JPEG
 *                           frames into it on the WS send cadence
 *   3. display canvas     — NW-1204 LiveFeedCanvas, overlays bboxes
 *                           + polygon on top of the <video>. Lives
 *                           in a separate component.
 *
 * Ratified plan decisions honored:
 *   #3  Locked capture dimensions everywhere (`--capture-w`, `--capture-h`).
 *   #12 No `useWebcam` hook; owns its DOM refs directly and dispatches
 *       into the App-level reducer.
 *
 * Design-specs mapping:
 *   - `camera-denied` UI state (§System States) rendered inline with
 *     recovery instructions and a retry button.
 *   - Primary CTA uses the cyan/accent token; text is `--ink-on-cyan`.
 *   - When the upload source lands in NW-1202, the denied recovery
 *     can include a "Use upload instead" CTA per spec. The TODO
 *     comment marks the landing site.
 */

export interface WebcamViewProps {
  state: AppState
  dispatch: Dispatch<Action>
}

export function WebcamView({ state, dispatch }: WebcamViewProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const captureCanvasRef = useRef<HTMLCanvasElement>(null)
  const retryButtonRef = useRef<HTMLButtonElement>(null)
  const streamRef = useRef<MediaStream | null>(null)

  // Focus the Retry button on transition into an alert state so
  // keyboard users aren't stranded on an unmounted Start button.
  useEffect(() => {
    if (state.status === 'camera-denied' || state.status === 'error') {
      retryButtonRef.current?.focus()
    }
  }, [state.status])

  // WS lifecycle: connect when the webcam is active, disconnect when
  // it's not. The wsClient is a module-level singleton so repeated
  // connect/disconnect calls are idempotent within the app.
  useEffect(() => {
    if (state.cameraActive) {
      connectWs(WS_URL, dispatch, 'webcam')
      return () => {
        disconnectWs()
      }
    }
    return undefined
  }, [state.cameraActive, dispatch])

  // Frame capture loop. Pulls each video frame into the 640×480
  // capture canvas, encodes JPEG, hands it to wsClient.sendFrame.
  // The client's inFlight boolean drops most frames; effective rate
  // equals whatever the server sustains. Stops on camera/WS errors.
  useEffect(() => {
    if (!state.cameraActive) return
    if (state.status === 'error' || state.status === 'disconnected') return

    const video = videoRef.current
    const canvas = captureCanvasRef.current
    if (video === null || canvas === null) return
    const ctx = canvas.getContext('2d')
    if (ctx === null) return

    let raf = 0
    let stopped = false

    const tick = () => {
      if (stopped) return
      // HAVE_CURRENT_DATA (2) or higher — the video has a usable frame.
      if (video.readyState >= 2) {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
        canvas.toBlob(
          (blob) => {
            if (blob !== null && !stopped) sendFrame(blob)
          },
          'image/jpeg',
          CAPTURE_QUALITY,
        )
      }
      raf = requestAnimationFrame(tick)
    }

    raf = requestAnimationFrame(tick)
    return () => {
      stopped = true
      cancelAnimationFrame(raf)
    }
  }, [state.cameraActive, state.status])

  // Tear down MediaStream tracks on unmount — without this, some
  // browsers leave the webcam LED on after navigation.
  useEffect(() => {
    return () => {
      stopTracks(streamRef.current)
      streamRef.current = null
    }
  }, [])

  async function startCamera() {
    dispatch({ type: 'media/requesting' })
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 480 },
      })
      streamRef.current = stream

      // Watch for mid-stream revocation (user toggles permission in
      // site settings without reloading). Without this, the feed goes
      // black while `cameraActive` stays true in state.
      const [track] = stream.getVideoTracks()
      if (track !== undefined) {
        track.addEventListener('ended', () => {
          stopTracks(streamRef.current)
          streamRef.current = null
          dispatch({
            type: 'media/error',
            message: 'Camera was disconnected. Check permissions and retry.',
          })
        })
      }

      const video = videoRef.current
      if (video !== null) {
        video.srcObject = stream
        // Await play() so any autoplay-policy failure surfaces here
        // rather than being swallowed.
        await video.play()
      }

      dispatch({ type: 'media/ready' })
    } catch (err) {
      // Don't leave a half-started stream in place on failure.
      stopTracks(streamRef.current)
      streamRef.current = null
      dispatch(classifyGumError(err))
    }
  }

  function stopCamera() {
    stopTracks(streamRef.current)
    streamRef.current = null
    const video = videoRef.current
    if (video !== null) {
      video.srcObject = null
    }
    dispatch({ type: 'media/stop' })
  }

  const showIdle =
    !state.cameraActive &&
    !state.cameraRequesting &&
    state.status !== 'camera-denied' &&
    state.status !== 'error'

  const showRequesting = state.cameraRequesting
  const showDenied = state.status === 'camera-denied'
  const showError = state.status === 'error'

  // The stage hosts both the <video> and the overlay slates/alert panels.
  // Slates sit above the <video> via z-index (not opacity) so the video
  // keeps decoding visibly once started — some browsers throttle
  // opacity-hidden videos which would starve the capture canvas at WS time.
  return (
    <section className="webcam-view" aria-label="Webcam input">
      <div
        className="webcam-view__stage"
        data-active={state.cameraActive ? 'true' : 'false'}
      >
        <video
          ref={videoRef}
          className="webcam-view__video"
          width={640}
          height={480}
          playsInline
          muted
          autoPlay={false}
        />

        {showIdle && (
          <div className="webcam-view__slate">
            <p className="webcam-view__eyebrow">NW-1201 · Webcam input</p>
            <h2 className="webcam-view__title">Connect a webcam to begin.</h2>
            <p className="webcam-view__lede">
              NeuraWatch captures at 640×480 and processes frames locally.
              Your browser will ask for camera permission.
            </p>
            <button
              type="button"
              className="btn btn--primary"
              onClick={startCamera}
            >
              Start webcam
            </button>
          </div>
        )}

        {showRequesting && (
          <div className="webcam-view__slate" role="status">
            <p className="webcam-view__eyebrow">Requesting</p>
            <h2 className="webcam-view__title">
              Waiting for camera permission…
            </h2>
            <p className="webcam-view__lede">
              Approve the browser prompt to continue.
            </p>
          </div>
        )}

        {showDenied && (
          <DeniedPanel
            error={state.cameraError}
            onRetry={startCamera}
            retryRef={retryButtonRef}
          />
        )}

        {showError && (
          <ErrorPanel
            error={state.cameraError}
            onRetry={startCamera}
            retryRef={retryButtonRef}
          />
        )}
      </div>

      {/* Offscreen capture canvas. NW-1203 uses it for JPEG encoding. */}
      <canvas
        ref={captureCanvasRef}
        className="webcam-view__capture"
        width={640}
        height={480}
      />

      {state.cameraActive && (
        <div className="webcam-view__controls">
          <button
            type="button"
            className="btn btn--danger"
            onClick={stopCamera}
          >
            Stop webcam
          </button>
        </div>
      )}
    </section>
  )
}

interface PanelProps {
  error: string | null
  onRetry: () => void
  retryRef: React.Ref<HTMLButtonElement>
}

function DeniedPanel({ error, onRetry, retryRef }: PanelProps) {
  return (
    <div
      className="webcam-view__alert webcam-view__alert--denied"
      role="alert"
      aria-live="assertive"
    >
      <p className="webcam-view__eyebrow webcam-view__eyebrow--red">
        Permission denied
      </p>
      <h2 className="webcam-view__title">Camera access was blocked.</h2>
      <p className="webcam-view__lede">
        {error ?? 'Your browser blocked camera access for this page.'}
      </p>
      <ol className="webcam-view__steps">
        <li>Click the camera / lock icon in your browser’s address bar.</li>
        <li>Set the Camera permission to “Allow”.</li>
        <li>Press Retry below.</li>
        {/* TODO(NW-1202): once upload lands, add a "Use upload instead" CTA. */}
      </ol>
      <button
        ref={retryRef}
        type="button"
        className="btn btn--primary"
        onClick={onRetry}
      >
        Retry
      </button>
    </div>
  )
}

function ErrorPanel({ error, retryRef }: PanelProps) {
  // `error` status is pinned per spec §System States — recovery
  // requires explicit Reset Demo (NW-1405). Retry would silently
  // no-op today (reducer doesn't clear `error` on media/ready), so
  // we surface that honestly instead of staging theater.
  return (
    <div
      className="webcam-view__alert webcam-view__alert--error"
      role="alert"
      aria-live="assertive"
    >
      <p className="webcam-view__eyebrow webcam-view__eyebrow--red">Error</p>
      <h2 className="webcam-view__title">Something went wrong.</h2>
      <p className="webcam-view__lede">
        {error ?? 'The live pipeline hit an error it cannot recover from on its own.'}
      </p>
      <p className="webcam-view__lede">
        Reset Demo will be wired in NW-1405. For now, refresh the page to reset state.
      </p>
      <button
        ref={retryRef}
        type="button"
        className="btn btn--primary"
        disabled
        aria-disabled="true"
      >
        Waiting for Reset Demo
      </button>
    </div>
  )
}

function stopTracks(stream: MediaStream | null) {
  if (stream === null) return
  for (const track of stream.getTracks()) {
    track.stop()
  }
}

function classifyGumError(err: unknown): Action {
  if (err instanceof DOMException) {
    // Design-specs: only `NotAllowedError` maps to camera-denied;
    // other DOMExceptions go to the generic 'error' state.
    if (
      err.name === 'NotAllowedError' ||
      err.name === 'PermissionDeniedError'
    ) {
      return {
        type: 'media/denied',
        message:
          'Camera permission was denied. Grant access in your browser and retry.',
      }
    }
    if (
      err.name === 'NotFoundError' ||
      err.name === 'DevicesNotFoundError'
    ) {
      return {
        type: 'media/error',
        message: 'No camera was found on this device.',
      }
    }
    if (
      err.name === 'NotReadableError' ||
      err.name === 'TrackStartError'
    ) {
      return {
        type: 'media/error',
        message:
          'The camera is in use by another application. Close it and retry.',
      }
    }
    if (
      err.name === 'OverconstrainedError' ||
      err.name === 'ConstraintNotSatisfiedError'
    ) {
      return {
        type: 'media/error',
        message:
          'This camera does not support a 640×480 feed. Try a different device.',
      }
    }
    if (err.name === 'SecurityError') {
      return {
        type: 'media/error',
        message:
          'Camera requires a secure (https) context. Run on https or localhost.',
      }
    }
    return {
      type: 'media/error',
      message: err.message !== '' ? err.message : 'Camera error.',
    }
  }
  if (err instanceof Error) {
    return { type: 'media/error', message: err.message }
  }
  return { type: 'media/error', message: 'Unknown camera error.' }
}
