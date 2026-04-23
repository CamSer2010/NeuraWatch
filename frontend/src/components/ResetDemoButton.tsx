import type { Dispatch } from 'react'
import { useRef, useState } from 'react'

import { API_BASE } from '../config'
import { disconnectWs } from '../services/wsClient'
import type { Action, AppStatus } from '../types'
import '../styles/buttons.css'

/**
 * Reset Demo CTA (NW-1405).
 *
 * Hits `POST /session/reset` — which wipes DB alerts, saved JPEGs,
 * and ByteTrack IDs — then dispatches `session/reset` so the reducer
 * mirrors the server-side wipe. The WebcamView's effects observe
 * `cameraActive` flipping false and tear down the MediaStream +
 * WebSocket; the result is the cold-boot appearance design-specs
 * §Interactions promises ("disconnects WS, returns to idle").
 *
 * Disable logic:
 *   - `busy` — request in flight (AC: "Button disabled while request
 *     in flight"). Guards against double-click blowing away a second
 *     round of state the user didn't intend.
 *   - `nothingToReset` — nothing worth wiping. Matches the idle
 *     screenshot in design-specs (btn.danger with opacity 0.4). If
 *     the user is already at cold boot, the button is visual noise
 *     disguised as a live control.
 *
 * Confirm copy is the AC-verbatim string. Native `window.confirm()`
 * is blocking and non-themable; accepted because the spec explicitly
 * calls for a confirm dialog and shipping a custom modal costs more
 * than the aesthetic loss is worth.
 */

const CONFIRM_COPY =
  'Reset all alerts, snapshots, and tracker state? This cannot be undone.'

export interface ResetDemoButtonProps {
  dispatch: Dispatch<Action>
  status: AppStatus
  alertsCount: number
  hasZone: boolean
  cameraActive: boolean
}

export function ResetDemoButton({
  dispatch,
  status,
  alertsCount,
  hasZone,
  cameraActive,
}: ResetDemoButtonProps) {
  const [busy, setBusy] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)

  const nothingToReset =
    status === 'idle' &&
    alertsCount === 0 &&
    !hasZone &&
    !cameraActive

  const disabled = busy || nothingToReset

  const handleClick = async () => {
    if (!window.confirm(CONFIRM_COPY)) return

    setBusy(true)
    try {
      const res = await fetch(`${API_BASE}/session/reset`, { method: 'POST' })
      if (!res.ok) throw new Error(`POST /session/reset → ${res.status}`)

      // Close the WS imperatively BEFORE dispatching. Without this,
      // a `detection_result` in flight can deliver between the fetch
      // resolving and React running the reducer, reseeding the
      // state we're about to wipe and flipping status back to
      // 'live'. The reducer's `session/reset` case resets
      // `cameraActive`, which would eventually trigger the
      // WebcamView cleanup effect that calls disconnectWs — but
      // "eventually" is a render tick, and a frame can arrive in
      // that window. Closing here closes the window.
      disconnectWs()
      dispatch({ type: 'session/reset' })

      // Focus stays on the button but it's about to go disabled
      // (nothingToReset → true). Some AT/browsers drop focus to
      // <body> silently on disabled-transition; blur explicitly so
      // the next Tab lands somewhere predictable.
      btnRef.current?.blur()
    } catch (err) {
      console.error('[ResetDemoButton] reset failed', err)
      // No toast system — a blocking alert is honest about the fact
      // that server-side state may still be dirty and is louder than
      // a silent console entry an operator wouldn't notice.
      window.alert('Reset failed. Check the backend and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <button
      ref={btnRef}
      type="button"
      className="btn btn--danger"
      onClick={handleClick}
      disabled={disabled}
      aria-disabled={disabled}
      aria-busy={busy}
      title={nothingToReset ? 'Nothing to reset yet' : undefined}
    >
      {busy ? 'Resetting…' : 'Reset Demo'}
    </button>
  )
}
