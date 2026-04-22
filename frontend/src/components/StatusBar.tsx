import type { AppStatus } from '../types'
import { FpsReadout } from './FpsReadout'
import { StatusBadge } from './StatusBadge'
import './StatusBar.css'

/**
 * App header — scaffolded in NW-1003, extended in NW-1502.
 *
 * Center group (per design-specs §Live Monitoring): StatusBadge +
 * FpsReadout. Right actions slot stays reserved for Reset Demo,
 * which lands with NW-1405 alongside the session/reset endpoint.
 *
 * `status` drives the badge visual and also gates the FPS value:
 * the readout only shows a number while `status === 'live'`; every
 * other state renders the em-dash placeholder so the header width
 * doesn't collapse on reconnect or during model-loading.
 */
export interface StatusBarProps {
  status: AppStatus
  fps: number | null
}

export function StatusBar({ status, fps }: StatusBarProps) {
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
        {/* NW-1405: Reset Demo button lands here. */}
      </div>
    </header>
  )
}
