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
  build: {
    // Default de Vite es 'assets', que colisionaría con la API real del
    // backend (GET/POST /assets ya existe en api/main.py para el CRUD de
    // activos del tenant) una vez que FastAPI sirve este build de producción
    // desde el mismo origen (ver api/main.py, mount de StaticFiles). Un
    // nombre de carpeta distinto evita esa ambigüedad de raíz en vez de
    // depender del orden de registro de rutas para desambiguar.
    assetsDir: 'static-assets',
  },
})
