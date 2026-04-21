import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Port 3000 is the NW-1003 AC. `strictPort` makes Vite fail loudly
// instead of silently moving to 3001 if something else is bound.
//
// No dev proxy by design: NW-1203's WS client connects directly to
// ws://localhost:8000/ws/detect, and the FastAPI backend CORS is
// already pre-wired for :3000 (see backend/app/config.py).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    strictPort: true,
  },
})
