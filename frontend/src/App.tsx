import { useEffect, useReducer } from 'react'

import { AlertsPanel } from './components/AlertsPanel'
import { StatusBar } from './components/StatusBar'
import { WebcamView } from './components/WebcamView'
import { fetchRecentAlerts } from './services/alertsClient'
import { appReducer, initialAppState } from './types'
import './App.css'

export function App() {
  const [state, dispatch] = useReducer(appReducer, initialAppState)

  // NW-1404: bootstrap the persisted alerts list on mount. Failure
  // here is non-fatal — WS-pushed alerts still populate the panel
  // for the current session. Logging to the console is enough for
  // the demo; the panel's empty-state copy handles the UX.
  useEffect(() => {
    let cancelled = false
    fetchRecentAlerts(50)
      .then((alerts) => {
        if (cancelled) return
        dispatch({ type: 'alerts/bootstrap', alerts })
      })
      .catch((err) => {
        console.warn('[App] alerts bootstrap failed', err)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="app">
      <StatusBar
        status={state.status}
        fps={state.stats?.fps ?? null}
        dispatch={dispatch}
        alertsCount={state.alerts.length}
        hasZone={state.zoneClosed || state.zonePoints.length > 0}
        cameraActive={state.cameraActive}
      />
      <main className="app__main">
        {/* NW-1202 VideoSourceSelector lands to the left of WebcamView. */}
        <WebcamView state={state} dispatch={dispatch} />
        <AlertsPanel
          alerts={state.alerts}
          selectedAlertId={state.selectedAlertId}
          dispatch={dispatch}
        />
      </main>
    </div>
  )
}
