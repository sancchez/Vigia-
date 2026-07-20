import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Permite servir el dev server detrás de un túnel (localtunnel/*.loca.lt)
    // para demos sin desplegar -- Vite rechaza por defecto cualquier Host
    // que no reconozca como protección contra DNS rebinding. Solo afecta
    // `vite dev`, no el build de producción.
    allowedHosts: ['.loca.lt'],
  },
})
