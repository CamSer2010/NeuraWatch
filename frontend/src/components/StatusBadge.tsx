import type { AppStatus } from '../types'
import './StatusBadge.css'

/**
 * StatusBadge — one of the 13 components in design-specs.
 *
 * Renders a color-coded dot + label for the current AppStatus.
 * Extracted as a standalone component in NW-1201 so NW-1502 can slot
 * it into the final AppHeader without a rewrite.
 *
 * Accessibility: the badge is a region with a role="status" on the
 * container; the dot is aria-hidden (decorative). The visible text
 * is announced on its own — no aria-label duplication.
 */

const STATUS_LABELS: Record<AppStatus, string> = {
  idle: 'Idle',
  'model-loading': 'Loading model',
  live: 'Live',
  processing: 'Processing',
  disconnected: 'Disconnected',
  'camera-denied': 'Camera denied',
  error: 'Error',
}

export interface StatusBadgeProps {
  status: AppStatus
}

export function StatusBadge({ status }: StatusBadgeProps) {
  return (
    <span
      className={`status-badge status-badge--${status}`}
      role="status"
    >
      <span className="status-badge__dot" aria-hidden="true" />
      {STATUS_LABELS[status]}
    </span>
  )
}
