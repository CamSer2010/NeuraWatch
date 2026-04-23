import type { Dispatch } from 'react'

import type { Action, AppStatus } from '../types'
import { FpsReadout } from './FpsReadout'
import { ResetDemoButton } from './ResetDemoButton'
import { StatusBadge } from './StatusBadge'
import './StatusBar.css'

/**
 * App header — scaffolded in NW-1003, extended in NW-1502, wired for
 * NW-1405 Reset Demo.
 *
 * Center group (per design-specs §Live Monitoring): StatusBadge +
 * FpsReadout. Right actions slot holds the Reset Demo button, per
 * every AppHeader snapshot in the handoff spec.
 *
 * `status` drives the badge visual and also gates the FPS value:
 * the readout only shows a number while `status === 'live'`; every
 * other state renders the em-dash placeholder so the header width
 * doesn't collapse on reconnect or during model-loading.
 *
 * Reset Demo disable signals (alertsCount / hasZone / cameraActive)
 * are passed through so the button can dim itself when there's
 * nothing to wipe (idle screenshot in design-specs).
 */
export interface StatusBarProps {
  status: AppStatus
  fps: number | null
  dispatch: Dispatch<Action>
  alertsCount: number
  hasZone: boolean
  cameraActive: boolean
}

export function StatusBar({
  status,
  fps,
  dispatch,
  alertsCount,
  hasZone,
  cameraActive,
}: StatusBarProps) {
  return (
    <header className="status-bar">
      <div className="status-bar__brand">
        <span className="status-bar__logo" aria-hidden="true" />
        <span className="status-bar__name">NeuraWatch</span>
      </div>
      <div className="status-bar__center">
        <StatusBadge status={status} />
        <FpsReadout fps={fps} active={status === 'live'} />
      </div>
      <div className="status-bar__actions">
        <ResetDemoButton
          dispatch={dispatch}
          status={status}
          alertsCount={alertsCount}
          hasZone={hasZone}
          cameraActive={cameraActive}
        />
      </div>
    </header>
  )
}
