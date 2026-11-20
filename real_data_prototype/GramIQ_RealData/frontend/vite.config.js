import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev` -> http://localhost:3000, de API gaat via de proxy naar de backend op poort 8000.
// `npm run build` -> dist/, dat de backend zelf serveert op http://127.0.0.1:8000 (zie start.py).
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 800 },   // recharts is groot; één bestand is prima voor een lokaal dashboard
  server: {
    port: 3000,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
