import './StatusBar.css'

/**
 * Placeholder StatusBar for the NW-1003 scaffold.
 *
 * NW-1502 wires real content: connection badge, FPS indicator, and
 * the Reset Demo button (POST /session/reset per NW-1405).
 */
export function StatusBar() {
  return (
    <header className="status-bar">
      <h1 className="status-bar__title">NeuraWatch</h1>
      <span className="status-bar__badge">scaffold</span>
      <div className="status-bar__actions">
        {/* NW-1502: connection badge + FPS indicator + Reset Demo button land here. */}
      </div>
    </header>
  )
}
