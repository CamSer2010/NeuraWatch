import { StatusBar } from './components/StatusBar'
import './App.css'

export function App() {
  return (
    <div className="app">
      <StatusBar />
      <main className="app__main">
        {/* NW-1201 VideoSourcePanel + NW-1204 LiveFeedCanvas land here. */}
        {/* NW-1404 AlertsPanel side-panel lands here. */}
        <p className="app__placeholder">
          NeuraWatch scaffold. Feature components arrive in NW-1201 through NW-1405.
        </p>
      </main>
    </div>
  )
}
