# Vigia — un solo servicio deployable: backend FastAPI (api.main:app)
# sirviendo también el build de producción del frontend (React/Vite) como
# archivos estáticos. Antes de esta sesión, un deploy en Railway/Render
# hubiera significado DOS servicios (backend + frontend estático) -- más
# setup, más costo, más superficie de config. Ver docs/despliegue.md y
# api/main.py (bloque final, "Servir el build de producción del frontend")
# para el razonamiento completo del lado de FastAPI.
#
# Build en dos etapas:
#   1. "frontend-build" (node): compila frontend/ a frontend/dist/ (Vite).
#      Esta etapa NUNCA llega a la imagen final -- solo su resultado
#      (frontend/dist/) se copia a la etapa de Python de abajo.
#   2. Etapa final (python:3.12-slim, como antes): empaqueta SOLO el
#      servicio HTTP (orchestrator/ + agents/ + api/ + db/ + auth/ + tools/ +
#      eval/, lo que pyproject.toml declara como paquete instalable) más el
#      resultado del build del frontend.
#
# NO empaqueta ZAP/Nuclei/Trivy/Grype/Semgrep ni el propio Docker: ese
# escaneo activo (`tools/scan.py`) invoca el binario `docker` del host vía
# subprocess, y este contenedor deliberadamente no tiene acceso a él (ver
# docs/despliegue.md, seccion "Lo que NO funciona en un deploy Railway/Render
# sin trabajo adicional"). El codigo ya degrada con gracia cuando `docker` no
# esta en PATH (`tools/_shared.py::ToolNotInstalledError`), asi que el
# servicio arranca y responde bien sin ellos -- solo el escaneo activo real
# contra ZAP/Nuclei queda inactivo hasta que se resuelva por separado
# (montar el socket de Docker del host, o mover esas herramientas a un
# worker con acceso a Docker).
#
# El schema (db/schema.sql) NO necesita un paso de migracion manual en el
# arranque: db/connection.py::get_conn() ya lo aplica de forma idempotente
# en la primera conexion de cada proceso (SQLite via executescript() en cada
# conexion, Postgres una vez por DATABASE_URL vía _pg_schema_ready/lock) --
# ver docstring de db/connection.py. Por eso no hace falta un ENTRYPOINT ni
# comando aparte de migracion antes de uvicorn.

# ---------------------------------------------------------------------------
# Etapa 1 — build del frontend (Vite -> frontend/dist/)
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend-build

WORKDIR /app/frontend

# Copiar solo los manifiestos primero para cachear "npm ci" independiente de
# cambios en el codigo fuente del frontend (mismo principio que la capa de
# pip install de la etapa de Python, abajo).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# VITE_API_URL es de TIEMPO DE BUILD (Vite la inyecta al compilar, no al
# arrancar -- ver docs/despliegue.md). Default vacío a propósito: con el
# backend sirviendo el frontend desde el mismo origen (este mismo
# Dockerfile/contenedor), `frontend/src/api.ts` puede usar paths relativos
# ("" + "/auth/login" = "/auth/login") en vez de necesitar la URL absoluta
# del backend -- más simple, y no se rompe si el dominio público cambia.
# Pasar --build-arg VITE_API_URL=https://... solo hace falta si el frontend
# de esta imagen se sirviera desde un origen DISTINTO al del backend (no es
# el caso de este Dockerfile combinado, pero el ARG queda disponible por si
# se reutiliza esta etapa para ese escenario).
ARG VITE_API_URL=""
ENV VITE_API_URL=${VITE_API_URL}
RUN npm run build

# ---------------------------------------------------------------------------
# Etapa 2 — runtime de Python (backend + frontend estático)
# ---------------------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# Sin gcc/build-essential: psycopg[binary] trae su libpq empaquetada (wheel
# manylinux), no compila desde fuente. curl solo para healthcheck opcional.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copiar primero los metadatos de build para cachear la capa de dependencias
# (pip install) independiente de cambios en el codigo fuente.
COPY pyproject.toml README.md ./

# Copiar el codigo fuente antes de "pip install -e ." -- el build backend
# (setuptools) necesita los paquetes declarados en
# [tool.setuptools.packages.find] presentes en disco para poder instalar en
# modo editable.
COPY orchestrator ./orchestrator
COPY agents ./agents
COPY tools ./tools
COPY eval ./eval
COPY api ./api
COPY db ./db
COPY auth ./auth

RUN pip install --no-cache-dir .

# Resultado del build de la etapa "frontend-build" -- api/main.py lo detecta
# solo (`FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"`,
# que en este contenedor resuelve a /app/frontend/dist) y activa el mount de
# estáticos + el catch-all de SPA. Si este COPY faltara, el backend arranca
# igual (mismo comportamiento que dev local sin build) pero sin servir el
# frontend -- nunca un fallo duro.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Nunca copiar .env real a la imagen -- las variables de entorno se inyectan
# en tiempo de ejecucion (docker run --env-file, o el panel de variables de
# Railway/Render). Ver docs/despliegue.md para la lista completa de
# variables que la app real lee de entorno.

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# $PORT es inyectado por Railway/Render en tiempo de ejecucion; localmente
# (docker run -p 8000:8000 ...) o si no esta seteado, cae a 8000. La forma
# shell (no exec form) es intencional -- es la unica forma en la que Docker
# expande ${PORT:-8000} antes de invocar uvicorn.
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}
