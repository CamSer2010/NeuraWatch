import type { Dispatch } from 'react'
import { useEffect, useRef, useState } from 'react'

import { WS_URL } from '../config'
import {
  connectWs,
  disconnectWs,
  sendMessage,
} from '../services/wsClient'
import { uploadVideo } from '../services/uploadClient'
import type { Action, AppState } from '../types'
import '../styles/buttons.css'
import { LiveFeedCanvas } from './LiveFeedCanvas'
import { PolygonToolbar } from './PolygonToolbar'
import './VideoUploadView.css'
import './WebcamView.css'

/**
 * Upload-mode view (NW-1202).
 *
 * Mirrors WebcamView's stage pattern (4:3 aspect + overlay canvas +
 * polygon toolbar) but replaces the `<video>` source with a blob URL
 * created from the operator's uploaded file. Option-1 architecture —
 * no server-side static serving of the video, zero re-fetch after
 * upload completes.
 *
 * Lifecycle:
 *   1. Operator picks a file → `<input type=file>`
 *   2. "Upload & process" clicked → POST /upload → `upload/success`
 *   3. WS connects → `process_upload{video_id}` sent
 *   4. Server streams `detection_result{pts_ms}`; reducer buffers
 *      them into `state.uploadPredictions`
 *   5. `<video>` plays at natural rate; LiveFeedCanvas's rAF matches
 *      `currentTime * 1000` to the buffer on every tick
 *   6. Server emits `processing_complete` → reducer flips
 *      `uploadPhase:'complete'`, status back to 'idle'. Video keeps
 *      the blob URL so the operator can scrub back through the clip
 *      with the same overlay timeline.
 *
 * Zone drawing is identical to webcam mode — polygon vertices are
 * normalized 0–1 against the stage, the server evaluates them
 * against the native video frame regardless of source type.
 *
 * Deliberate skip: no upload progress bar (spec §System States
 * calls for `frame N / total`; PO-directed Tier B scope keeps the
 * simpler "Uploading…" label — the bandwidth delta on a 100 MB
 * ceiling is small enough that a simple busy affordance suffices).
 */

export interface VideoUploadViewProps {
  state: AppState
  dispatch: Dispatch<Action>
}

export function VideoUploadView({ state, dispatch }: VideoUploadViewProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [pickedFile, setPickedFile] = useState<File | null>(null)

  const uploaded = state.uploadedVideo
  const phase = state.uploadPhase

  // WS lifecycle: open once we have an uploaded video, tear down
  // when it clears (source switch / session reset / unmount). The
  // effect depends on `uploaded` ONLY — not on `uploadPhase`. An
  // earlier iteration included phase in the deps, which caused a
  // disconnect/reconnect every time the first detection_result
  // flipped phase 'ready' → 'processing'. The fresh socket never
  // got a second process_upload (the sender effect only runs while
  // phase === 'ready'), so the server's processing task
  // WebSocketDisconnect-bailed after a single frame. Result:
  // exactly one frame of overlays before the video kept playing
  // with stale bboxes. Deps intentionally `[uploaded, dispatch]`
  // only — adding phase would reintroduce the one-frame bug.
  useEffect(() => {
    if (uploaded === null) return
    connectWs(WS_URL, dispatch, 'upload')
    return () => {
      disconnectWs()
    }
  }, [uploaded, dispatch])

  // Once we're in the 'ready' phase (server has the file; WS is
  // connecting), fire `process_upload` exactly once, retrying until
  // the socket is OPEN. `sendMessage` now returns true iff the send
  // landed — we stop the interval on first success instead of
  // relying on a downstream phase flip to silence ourselves. Without
  // this, the interval would keep firing at 100 ms until the first
  // `detection_result` arrived, sending up to ~10 duplicate
  // `process_upload` messages on a slow connect.
  useEffect(() => {
    if (phase !== 'ready' || uploaded === null) return
    const videoId = uploaded.metadata.video_id
    // Try immediately — if the WS opened quickly there's no wait.
    if (sendMessage({ type: 'process_upload', video_id: videoId })) {
      return
    }
    const id = setInterval(() => {
      if (sendMessage({ type: 'process_upload', video_id: videoId })) {
        clearInterval(id)
      }
    }, 100)
    return () => clearInterval(id)
  }, [phase, uploaded])

  // When an uploaded video is loaded, start playing it. Paused
  // playback would still let the overlay buffer match pts_ms = 0,
  // but operators expect autoplay after they click "Upload & process".
  // Muted is required by autoplay policies in every modern browser.
  useEffect(() => {
    const v = videoRef.current
    if (v === null || uploaded === null) return
    v.muted = true
    // `play()` is async and can reject on autoplay-blocked — we swallow
    // because the user can click the built-in video controls to resume.
    void v.play().catch(() => undefined)
  }, [uploaded])

  // Unmount safety net: if the component tears down without going
  // through `session/reset` or `source/set` (e.g. hot-reload, parent
  // tree remount), revoke any live blob URL so it doesn't leak.
  // Duplicates with reducer-level revocation — both are idempotent.
  useEffect(() => {
    return () => {
      if (uploaded !== null) {
        try {
          URL.revokeObjectURL(uploaded.blobUrl)
        } catch {
          // ignore
        }
      }
    }
  }, [uploaded])

  // Also sync the outbound zone to the WS (same pattern as WebcamView).
  const lastSentZoneVersionRef = useRef<number>(0)
  useEffect(() => {
    if (state.zoneVersion === lastSentZoneVersionRef.current) return
    if (state.zoneVersion === 0) return
    lastSentZoneVersionRef.current = state.zoneVersion
    if (state.zoneClosed && state.zonePoints.length >= 3) {
      sendMessage({
        type: 'zone_update',
        points: state.zonePoints,
        zone_version: state.zoneVersion,
      })
    } else {
      sendMessage({ type: 'zone_clear', zone_version: state.zoneVersion })
    }
  }, [state.zoneVersion, state.zoneClosed, state.zonePoints])

  const handlePick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0] ?? null
    setPickedFile(file)
  }

  const handleSubmit = async () => {
    if (pickedFile === null) return
    dispatch({ type: 'upload/start' })
    try {
      const metadata = await uploadVideo(pickedFile)
      const blobUrl = URL.createObjectURL(pickedFile)
      dispatch({
        type: 'upload/success',
        video: { metadata, blobUrl },
      })
      // Clear the input so the same filename can be re-picked later.
      if (fileInputRef.current) fileInputRef.current.value = ''
      setPickedFile(null)
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Upload failed'
      dispatch({ type: 'upload/error', message })
    }
  }

  const showPicker = uploaded === null
  const showStage = uploaded !== null
  const bannerText = _bannerText(state)

  return (
    <section className="webcam-view" aria-label="Video upload">
      <div
        className="webcam-view__stage"
        data-active={showStage ? 'true' : 'false'}
      >
        {showStage && uploaded !== null && (
          <video
            ref={videoRef}
            className="webcam-view__video"
            width={640}
            height={480}
            src={uploaded.blobUrl}
            playsInline
            muted
            controls
            // Loop so operators can re-watch the clip after
            // processing_complete without re-uploading.
            loop
          />
        )}

        <LiveFeedCanvas
          detections={state.detections}
          lastZoneVersion={state.lastZoneVersion}
          active={phase === 'processing'}
          cameraActive={showStage}
          zoneDrawing={state.zoneDrawing}
          zonePoints={state.zonePoints}
          zoneClosed={state.zoneClosed}
          onAddPoint={(p) => dispatch({ type: 'zone/add-point', point: p })}
          onRemoveLastPoint={() =>
            dispatch({ type: 'zone/remove-last-point' })
          }
          onCloseZone={() => dispatch({ type: 'zone/close' })}
          onCancelDraw={() => dispatch({ type: 'zone/cancel-draw' })}
          uploadPredictions={state.uploadPredictions}
          videoRef={videoRef}
        />

        {state.zoneDrawing && (
          <p className="webcam-view__hint" role="status">
            Click to add vertex · Close Zone when ready · Esc to cancel
          </p>
        )}

        {showStage &&
          !state.zoneDrawing &&
          !state.zoneClosed && (
            <p
              className="webcam-view__hint webcam-view__hint--nozone"
              role="status"
            >
              ⚑ Draw a zone to enable alerts
            </p>
          )}

        {showPicker && (
          <div className="webcam-view__slate">
            <p className="webcam-view__eyebrow">NW-1202 · Upload mode</p>
            <h2 className="webcam-view__title">
              Upload an MP4 to process a recorded clip.
            </h2>
            <p className="webcam-view__lede">
              NeuraWatch streams the clip through the same detection
              pipeline as the live webcam. Max 100 MB.
            </p>
            <input
              ref={fileInputRef}
              type="file"
              accept="video/mp4"
              className="video-upload-view__file"
              onChange={handlePick}
              disabled={phase === 'uploading'}
            />
            <button
              type="button"
              className="btn btn--primary"
              onClick={handleSubmit}
              disabled={pickedFile === null || phase === 'uploading'}
            >
              {phase === 'uploading' ? 'Uploading…' : 'Upload & process'}
            </button>
            {state.uploadError !== null && (
              <p
                className="webcam-view__lede video-upload-view__err"
                role="alert"
              >
                {state.uploadError}
              </p>
            )}
          </div>
        )}

        {bannerText !== null && (
          <p
            className="video-upload-view__banner"
            data-phase={phase}
            role="status"
          >
            {bannerText}
          </p>
        )}
      </div>

      {showStage && (
        <div className="webcam-view__controls">
          <PolygonToolbar
            dispatch={dispatch}
            drawing={state.zoneDrawing}
            closed={state.zoneClosed}
            points={state.zonePoints.length}
          />
        </div>
      )}
    </section>
  )
}

function _bannerText(state: AppState): string | null {
  if (state.uploadPhase === 'processing' && state.uploadedVideo !== null) {
    const total = state.uploadedVideo.metadata.total_frames
    const lastIdx =
      state.uploadPredictions.length > 0
        ? state.uploadPredictions[state.uploadPredictions.length - 1].frame_idx
        : 0
    if (total > 0) {
      return `Processing · frame ${lastIdx + 1} / ${total}`
    }
    return `Processing · frame ${lastIdx + 1}`
  }
  if (state.uploadPhase === 'complete') {
    return 'Processing complete — video now loops for review'
  }
  return null
}
