import { useEffect, useReducer } from 'react'

import { AlertsPanel } from './components/AlertsPanel'
import { StatusBar } from './components/StatusBar'
import { VideoSourcePanel } from './components/VideoSourcePanel'
import { VideoUploadView } from './components/VideoUploadView'
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
        <div className="app__source">
          <VideoSourcePanel
            source={state.videoSource}
            dispatch={dispatch}
            // Don't let a mid-upload toggle tear down state. Also
            // blocked mid-webcam once cameraActive is true — the
            // operator should stop the webcam first to avoid a
            // silent MediaStream leak.
            disabled={
              state.uploadPhase === 'uploading' ||
              state.uploadPhase === 'processing' ||
              state.cameraActive
            }
          />
          {state.videoSource === 'webcam' ? (
            <WebcamView state={state} dispatch={dispatch} />
          ) : (
            <VideoUploadView state={state} dispatch={dispatch} />
          )}
        </div>
        <AlertsPanel
          alerts={state.alerts}
          selectedAlertId={state.selectedAlertId}
          dispatch={dispatch}
        />
      </main>
    </div>
  )
}
