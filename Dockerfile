# Vigia — backend FastAPI (api.main:app)
#
# Empaqueta SOLO el servicio HTTP (orchestrator/ + agents/ + api/ + db/ + auth/
# + tools/ + eval/, todo lo que pyproject.toml declara como paquete instalable).
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
