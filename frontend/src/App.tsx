import { useReducer } from 'react'

import { StatusBar } from './components/StatusBar'
import { WebcamView } from './components/WebcamView'
import { appReducer, initialAppState } from './types'
import './App.css'

export function App() {
  const [state, dispatch] = useReducer(appReducer, initialAppState)

  return (
    <div className="app">
      <StatusBar status={state.status} />
      <main className="app__main">
        {/* NW-1202 VideoSourceSelector lands to the left of WebcamView. */}
        {/* NW-1404 AlertsPanel + NW-1405 Reset button land to the right. */}
        <WebcamView state={state} dispatch={dispatch} />
      </main>
    </div>
  )
}
