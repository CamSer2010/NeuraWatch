import type { AppStatus } from '../types'
import { StatusBadge } from './StatusBadge'
import './StatusBar.css'

/**
 * Scaffold header. NW-1502 promotes this into the design-specs
 * `AppHeader` with `FpsReadout` and the Reset Demo button
 * (NW-1405) in the right slot. The StatusBadge itself is already
 * the spec-defined component and will carry over unchanged.
 */
export interface StatusBarProps {
  status: AppStatus
}

export function StatusBar({ status }: StatusBarProps) {
  return (
    <header className="status-bar">
      <div className="status-bar__brand">
        <span className="status-bar__logo" aria-hidden="true" />
        <span className="status-bar__name">NeuraWatch</span>
      </div>
      <div className="status-bar__center">
        <StatusBadge status={status} />
      </div>
      <div className="status-bar__actions">
        {/* NW-1502: FPS readout + Reset Demo button land here. */}
      </div>
    </header>
  )
}
