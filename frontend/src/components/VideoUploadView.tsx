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

  // Ref tracks the last zoneVersion flushed over the current WS
  // session. Reset to 0 on every new connect (see the WS lifecycle
  // effect below) so Reset Demo doesn't leave it pinned at a stale
  // value — the zone-sync effect's equality guard would otherwise
  // swallow the first zone_update of the next session when its
  // version happens to coincide with the stuck ref.
  const lastSentZoneVersionRef = useRef<number>(0)

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
    lastSentZoneVersionRef.current = 0
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

  // Autoplay — DELIBERATELY GATED on a server head-start (NW-1202
  // follow-up, 2026-04-23).
  //
  // The naïve "play as soon as upload/success lands" approach let
  // `video.currentTime` race ahead of the server's first
  // detection_result (the WS handshake + first inference costs
  // ~400-800 ms). Because the server's wall-clock throttle keeps
  // processing at exactly 1× real-time, the buffer stays
  // permanently behind currentTime by that startup delta — the FE
  // falls into `_interpolateDetections`'s sticky-last branch and
  // overlays visibly lag moving objects by whatever the startup gap
  // was. Pausing the video lets the buffer accumulate ahead of
  // currentTime, and resuming from there looks synced — which is
  // exactly what the operator reported as the repro.
  //
  // Fix: wait for a prediction buffer head-start before calling
  // play(). ~10 entries at the 10 FPS server rate = ~1 s of runway.
  // After that the buffer stays ahead of currentTime because
  // processing == playback speed (1× real-time via the throttle).
  //
  // Fire-once-per-upload via a ref keyed on video_id so manual
  // pause/resume (built-in video controls) doesn't re-trigger the
  // autoplay after the user has taken control.
  const PLAY_HEAD_START_PREDICTIONS = 10
  const autoPlayedForRef = useRef<string | null>(null)
  useEffect(() => {
    const v = videoRef.current
    if (v === null || uploaded === null) return
    v.muted = true
    if (autoPlayedForRef.current === uploaded.metadata.video_id) return
    if (state.uploadPredictions.length < PLAY_HEAD_START_PREDICTIONS) return
    // Defensive: if the user beat us to the native play control,
    // don't fire a redundant play(). Harmless on a playing element
    // (returns a resolved promise) but makes the skip-intent explicit.
    if (!v.paused) {
      autoPlayedForRef.current = uploaded.metadata.video_id
      return
    }
    autoPlayedForRef.current = uploaded.metadata.video_id
    // `play()` is async; reject on autoplay-blocked is swallowed so
    // the operator can still click the built-in video control.
    void v.play().catch(() => undefined)
  }, [uploaded, state.uploadPredictions.length])

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
  // `lastSentZoneVersionRef` itself is declared near the top of the
  // component so the WS lifecycle effect can reset it on reconnect.
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

  const handleReprocess = () => {
    // Pause + rewind, but deliberately DO NOT call play() here.
    // Playing immediately would reintroduce the startup-lag bug
    // (video racing ahead of the server's first re-processing
    // frame). Resetting the head-start guard lets the gated
    // autoplay effect re-engage as soon as the buffer re-fills
    // to PLAY_HEAD_START_PREDICTIONS.
    const v = videoRef.current
    if (v !== null) {
      try {
        v.pause()
        v.currentTime = 0
      } catch {
        // Some browsers throw if metadata isn't loaded yet; rare
        // at this point since the first play already happened.
      }
    }
    autoPlayedForRef.current = null
    // Reducer wipes the prediction buffer + flips phase to 'ready'.
    // The sender effect (phase==='ready') then re-fires
    // `process_upload` on the still-open WS, which the server
    // handles by cancelling any prior task, resetting tracker +
    // alert + snapshot state, and starting a fresh pass.
    dispatch({ type: 'upload/restart' })
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
            aria-label="Uploaded clip playback, controlled by the application"
            // PO-directed 2026-04-23: the operator shall not
            // interact with the uploaded clip directly (no pause,
            // no scrub, no mute). Playback is driven entirely by
            // the gated autoplay effect + Re-process button; native
            // controls are omitted to enforce that flow. Side
            // effect: removes the "user-beat-us-to-play" race the
            // autoplay gate previously had to defend against, since
            // no native play widget exists for the user to click.
            //
            // Not looped. On EOF the video pauses at the last frame
            // and the operator can click "Re-process video" to
            // re-run.
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
          {phase === 'complete' && (
            <button
              type="button"
              className="btn btn--primary"
              onClick={handleReprocess}
            >
              Re-process video
            </button>
          )}
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
    return 'Processing complete · video paused on last frame'
  }
  return null
}
