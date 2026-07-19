# Despliegue de Vigia — de "corre en mi laptop" a un servidor real

Sigue el estilo de `docs/cumplimiento.md`/`docs/produccion-readiness.md`:
documenta lo que se construyó y se probó de verdad en esta sesión, no un
plan teórico. Cierra el bloqueador #1 de `docs/produccion-readiness.md`
("Persistencia de proceso") en la medida en que código/config pueden
cerrarlo — la parte que falta (crear la cuenta de hosting, conectar el
repo, pagar si hace falta) es explícitamente tuya, no de Claude. Ver la
sección final "Lo que Claude NO puede hacer por ti" antes de asumir que
algo de esto ya está desplegado.

## Qué existe ahora, verificado en esta sesión

- **`Dockerfile`** (raíz del repo) — empaqueta el backend FastAPI
  (`api.main:app`) sobre `python:3.12-slim`, instala desde `pyproject.toml`
  (`pip install .`). **Construido y corrido de verdad** (`docker build --load
  -t vigia-backend:test .` + `docker run -p 48199:8000 ...`), no solo
  "el build terminó sin error": `GET /health` respondió `200` real contra el
  contenedor corriendo, y un `POST /auth/register` real contra ese mismo
  contenedor confirmó que el schema (`db/schema.sql`) se aplica solo, sin
  ningún paso de migración manual — `db/connection.py::get_conn()` ya lo
  hacía de forma idempotente antes de esta sesión (SQLite vía
  `executescript()` en cada conexión, Postgres una vez por `DATABASE_URL`
  vía el lock de `_pg_schema_ready`), así que un contenedor nuevo arrancando
  desde cero no necesita ni un comando extra ni un entrypoint de migración.
  Contenedor e imagen de prueba se detuvieron y se borraron al terminar la
  verificación (`docker stop/rm`, `docker rmi`) — no queda nada corriendo ni
  ninguna imagen local de esta sesión.
- **`.dockerignore`** — evita copiar `frontend/node_modules/`, `tests/`,
  `.git/`, bases de datos locales, etc. al contexto de build.
- **`railway.json`** y **`render.yaml`** — config real para ambas
  plataformas, escrita contra su documentación actual (no adivinada — ver
  la sección de cada plataforma abajo para qué se verificó puntualmente).
  Ninguno de los dos se probó desplegando de verdad (eso requeriría crear
  una cuenta y no es algo que Claude puede hacer, ver el cierre de este
  documento) — lo que sí está verificado es que el `Dockerfile` que ambos
  usan corre y responde `/health` de verdad.
- **`scripts/demo.ps1`** — un solo comando levanta backend + frontend,
  espera `/health` real antes de declarar éxito, y advierte (sin fallar) si
  `docker ps` no responde. **Corrido de punta a punta en esta sesión**:
  detectó Docker corriendo, arrancó ambos jobs, esperó la respuesta real de
  `/health` (200) y del frontend (200), y limpió los jobs de PowerShell al
  terminar la verificación.
- **`scripts/seed_demo.py`** — siembra un tenant `Vigia Demo`
  (`demo@vigia.local` / `DemoVigia2026`), 3 assets, y los 10 hallazgos reales
  de ZAP baseline contra Juice Shop ya congelados en
  `eval/cumplimiento_fixture_juiceshop.json` (los mismos usados para
  verificar Item 6 de `HANDOFF.md`). **Probado de punta a punta contra una
  base de datos SQLite real** (no la de desarrollo, una descartable): primera
  corrida (crea), segunda corrida (idempotente, no duplica), `--reset`
  (borra y recrea), y un smoke test HTTP real (login real, `GET /findings`
  devolvió los 10 hallazgos, `GET /assets` devolvió los 3 assets) contra un
  `uvicorn` real apuntando a esa base de datos sembrada.
- **Migración a Postgres** (`db/connection.py`, `DATABASE_URL=postgresql://...`)
  — hecha en la sesión inmediatamente anterior a esta, no en esta (ver
  `docs/produccion-readiness.md` sección 2 y `eval/live_run_report.md`
  Corrida 14). Se menciona aquí porque es la pieza que hace que "hosting con
  disco persistente" no sea la única forma de no perder datos — Postgres
  administrado (Railway/Render, un clic) es la alternativa real.

## Lo que esto NO resuelve todavía

- El escaneo activo en vivo (ZAP/Nuclei/Trivy/Grype, `tools/scan.py`) invoca
  el binario `docker` del host por subprocess. El contenedor del backend
  desplegado en Railway/Render **no tiene Docker dentro** (ni debería —
  Docker-in-Docker en un PaaS gestionado es una complicación real, no un
  `RUN` más). El código ya degrada con gracia sin él
  (`tools/_shared.py::ToolNotInstalledError`, confirmado leyendo el código
  antes de escribir esto) — el servicio arranca y responde bien, solo el
  escaneo activo/pasivo contra herramientas externas queda inactivo hasta
  resolver esto por separado (opciones: exponer el socket de Docker del host
  al contenedor si el plan del hosting lo permite, o mover esas herramientas
  a un worker aparte con Docker real). No es un bug de esta sesión, es una
  limitación de arquitectura conocida que vale la pena anotar antes de
  prometer "escaneo activo" en un demo desplegado.
- El feed de CertStream self-hosted (`certstream-server-go`, ver
  `eval/live_run_report.md` Corrida 13) solo se probó en un contenedor local
  de esta máquina. Si se quiere `VIGIA_CERTSTREAM_URL` apuntando a un feed
  real en producción, **ese contenedor necesita su propio despliegue
  separado** (ver sección dedicada más abajo) — no basta con desplegar el
  backend de Vigia.
- El paquete Python `certstream` (el cliente que `api/certstream_listener.py`
  importa en tiempo de ejecución) **no está en `pyproject.toml`** — en el
  contenedor de esta sesión el listener se deshabilitó solo con un mensaje
  claro (`"Paquete 'certstream' no instalado..."`, confirmado en los logs
  reales del contenedor de prueba), sin tumbar el resto de la API. Si se
  quiere este feature activo en el deploy real, hay que agregar `certstream`
  como dependencia (no se hizo en esta sesión — fuera del alcance de las 5
  tareas de esta corrida, que eran empaquetado/hosting, no cerrar gaps de
  dependencias de un feature ya implementado).
- Nada de esto se probó desplegado de verdad en Railway o Render — solo
  local, en Docker real. La primera vez que corra contra la infraestructura
  real de cualquiera de las dos plataformas puede sacar a la luz algo que
  Docker local no expone (límites de memoria más estrictos, timeouts de
  red distintos, etc.) — normal en un primer deploy, no una señal de que
  este trabajo esté mal hecho.

## Referencia completa de variables de entorno

Todas las que el código real lee de `os.environ`/`os.getenv` (confirmado por
`grep` sobre todo `*.py`, no de memoria) — incluye las que
`docs/produccion-readiness.md` ya había señalado como faltantes en
`.env.example` (ese archivo sigue sin tocarse, fuera de alcance explícito de
esta sesión; esta tabla es la referencia real hasta que alguien lo actualice).

| Variable | Dónde se usa | Obligatoria en producción | Notas |
|---|---|---|---|
| `JWT_SECRET` | `auth/jwt_auth.py` | **Sí** | Sin ella cae a un secreto de desarrollo hardcodeado — inaceptable para un cliente real. Generar con `py -c "import secrets; print(secrets.token_hex(32))"`. |
| `DATABASE_URL` | `db/connection.py` | Recomendada | `sqlite:///./ciberseguridad.db` (default) o `postgresql://usuario:pass@host:puerto/db`. Ver sección de cada plataforma para las implicancias reales de dejarlo en SQLite. |
| `ANTHROPIC_API_KEY` | `agents/_llm.py` | **Sí, antes del primer cliente de pago** | Sin ella, `agents/_llm.py` cae al fallback `claude -p` (CLI) — no apto para carga concurrente real (ver Item 1 de `HANDOFF.md`), y probablemente ni siquiera disponible dentro del contenedor desplegado (requiere la CLI de Claude Code instalada y autenticada, que este `Dockerfile` no instala a propósito). |
| `VIGIA_CLAUDE_CLI_TIMEOUT` | `agents/_llm.py` | No | Solo aplica al fallback de CLI de arriba — irrelevante si `ANTHROPIC_API_KEY` está configurada. Default 300 (segundos). |
| `CIBERSEGURIDAD_LLM_MODEL` | `agents/_llm.py` | No | Default `claude-sonnet-4-5-20250929`. |
| `CORS_ORIGINS` | `api/main.py` | **Sí** | Lista separada por comas de orígenes permitidos. Debe apuntar al dominio real del frontend desplegado, no a `localhost`. |
| `VIGIA_SCAN_INTERVAL_HOURS` | `api/scheduler.py` | No | Default 6. Escaneo pasivo recurrente sobre todos los assets activos. |
| `VIGIA_CERTSTREAM_ENABLED` | `api/certstream_listener.py` | No | Default `true`. Ponerla en `false` si no se quiere ni intentar conectar (evita el ruido de logs de reconexión si no hay feed configurado). |
| `VIGIA_CERTSTREAM_URL` | `api/certstream_listener.py` | No (pero ver nota abajo) | El feed público histórico (`wss://certstream.calidog.io`) está descontinuado (confirmado en Corrida 13) — sin esta variable apuntando a una instancia propia de `certstream-server-go`, el listener no recibe nada. Ver sección dedicada abajo. |
| `VIGIA_CERTSTREAM_REFRESH_MINUTES` | `api/certstream_listener.py` | No | Default 30. |
| `GOOGLE_SAFE_BROWSING_API_KEY` | `tools/antisuplantacion.py` | No | Mejora la anti-suplantación si se configura; el módulo funciona sin ella. |
| `PORT` | Inyectada por Railway/Render, leída por el `CMD` del `Dockerfile` | Automática | No la configures a mano — cada plataforma la inyecta sola. Localmente (`docker run`) cae a 8000 si no está seteada. |
| `SUPABASE_URL` / `SUPABASE_KEY` | — | No | Documentadas en `.env.example` pero **inertes hoy** (confirmado por `grep`, ningún `os.getenv` las lee) — intención futura, no funcionalidad actual. No las configures esperando que hagan algo. |
| `APP_ENV` / `LOG_LEVEL` | — | No | Mismo caso: inertes hoy. |
| `WOMPI_PUBLIC_KEY` / `WOMPI_PRIVATE_KEY` / `WOMPI_EVENTS_SECRET` | — | No | Inertes — integración de pagos en pausa a propósito (ver `HANDOFF.md`, "Cosas que NO hacer sin preguntar primero"). |
| `VITE_API_URL` | Frontend (`frontend/src/api.ts`), tiempo de **build**, no runtime | **Sí, en el build del frontend** | Debe apuntar a la URL pública real del backend desplegado. Como es de build (Vite la inyecta al compilar, no al arrancar), cambiarla obliga a un rebuild del frontend, no solo un restart. |

## Desplegar en Railway — pasos concretos

Railway soporta un servicio desde `Dockerfile` directamente (confirmado
contra `docs.railway.com/reference/config-as-code` antes de escribir
`railway.json`) — no hace falta convertir nada a Nixpacks/buildpacks.

1. **Cuenta y repo — esto lo haces tú, no Claude.** Entra a
   [railway.com](https://railway.com), crea una cuenta (o inicia sesión con
   GitHub), y autoriza el acceso al repo `ciberseguridad` (público, así que
   no hace falta dar acceso privado). Ningún agente de esta sesión puede
   crear esta cuenta ni autorizar el OAuth de GitHub por ti — es una acción
   explícitamente fuera de los límites de esta tarea.
2. **New Project → Deploy from GitHub repo**, selecciona este repo. Railway
   detecta `railway.json` solo y usa `build.builder: "DOCKERFILE"` (ver el
   archivo en la raíz del repo) — no hace falta configurar nada de build a
   mano en el dashboard.
3. **Variables de entorno**: en la pestaña "Variables" del servicio, agrega
   cada fila de la tabla de arriba marcada como obligatoria (`JWT_SECRET`,
   `ANTHROPIC_API_KEY`, `CORS_ORIGINS`) y las que quieras ajustar del resto.
   No configures `PORT` — Railway la inyecta sola.
4. **Persistencia de datos — decisión real, no automática:**
   - Opción A (más simple): dejar `DATABASE_URL` sin configurar (cae a
     SQLite local dentro del contenedor). **Ojo:** en Railway, el filesystem
     de un servicio normal es efímero entre redeploys salvo que se agregue
     un [Volume](https://docs.railway.com/volumes/reference) — sin uno,
     cada redeploy borra el SQLite. Si se agrega un Volume, montarlo en
     `/app` (o en la ruta donde viva `ciberseguridad.db`) y setear
     `DATABASE_URL=sqlite:////ruta/del/volume/ciberseguridad.db`.
   - Opción B (recomendada para algo que sobreviva de verdad): en el mismo
     proyecto de Railway, "+ New" → "Database" → "PostgreSQL" (un clic,
     administrado). Railway te da la variable `DATABASE_URL` lista para
     copiar al servicio del backend — pégala tal cual en las variables del
     paso 3.
5. **Deploy.** Railway construye la imagen con el `Dockerfile` del repo y la
   corre. `railway.json` ya configura `healthcheckPath: "/health"`, así que
   Railway no marca el deploy como listo hasta que ese endpoint responda
   200 — mismo principio que `scripts/demo.ps1` usa localmente.
6. **Dominio**: Railway asigna un subdominio `*.up.railway.app` gratis
   ("Settings" → "Networking" → "Generate Domain"). Usa esa URL como
   `CORS_ORIGINS` (paso 3) y como base de `VITE_API_URL` al desplegar el
   frontend (ver más abajo).
7. **Frontend**: Railway también puede desplegar un sitio estático, pero
   está pensado principalmente para servicios — para el frontend suele ser
   más simple usar Render (ver abajo) o cualquier host estático (Vercel,
   Netlify, GitHub Pages) apuntando `VITE_API_URL` a la URL del paso 6.
   `render.yaml` de este repo ya incluye el servicio de frontend si prefieres
   mantener todo en una sola plataforma.

## Desplegar en Render — pasos concretos

Render sí soporta un `render.yaml` (Blueprint) con ambos servicios —
backend Docker y frontend estático — en un solo archivo (confirmado contra
`render.com/docs/blueprint-spec` antes de escribirlo, incluyendo el formato
real de `envVars`/`sync: false` y la diferencia entre el campo moderno
`runtime` y el `env` más viejo, que la doc marca como desalentado).

1. **Cuenta y repo — esto lo haces tú, no Claude.** Entra a
   [render.com](https://render.com), crea una cuenta (o inicia sesión con
   GitHub), autoriza el acceso al repo. Mismo límite que con Railway: ningún
   agente de esta sesión puede hacer esto por ti.
2. **New → Blueprint**, selecciona el repo. Render detecta `render.yaml` en
   la raíz automáticamente y muestra los dos servicios que define
   (`vigia-backend`, `vigia-frontend`) para revisar antes de crear.
3. **Variables marcadas `sync: false`** en `render.yaml`
   (`JWT_SECRET`, `ANTHROPIC_API_KEY`, `VIGIA_CERTSTREAM_URL`) te las va a
   pedir el propio asistente de creación del Blueprint — pégalas ahí, nunca
   quedan en el archivo committeado.
4. **Persistencia de datos — mismo dilema que Railway, con sus propios
   números reales:**
   - El plan **free** de un Web Service en Render tiene filesystem efímero
     — un SQLite local se pierde en cada redeploy/restart/spin-down
     (confirmado en la documentación de Render, no supuesto). Los Persistent
     Disks de Render **no están disponibles en el plan free**, solo en
     planes pagos.
   - `render.yaml` incluye un bloque `databases:` comentado al final —
     descoméntalo para que Render cree un Postgres administrado gratis. Ese
     Postgres free **expira a los 30 días** (con 14 días de gracia para
     pasarlo a pago antes de que Render borre los datos) — confirmado en el
     changelog real de Render. Para un piloto corto (demo extendida, primer
     prospecto) alcanza; para un cliente de pago real, hay que pasar el plan
     de la base de datos a uno pago antes del día 30.
   - Si descomentas el bloque `databases:`, actualiza el `envVars` de
     `vigia-backend` en `render.yaml` para que `DATABASE_URL` use
     `fromDatabase: {name: vigia-db, property: connectionString}` en vez del
     valor SQLite fijo que trae por default — Render resuelve esa referencia
     sola al desplegar.
5. **`CORS_ORIGINS` / `VITE_API_URL`**: `render.yaml` ya trae valores de
   ejemplo (`https://vigia-frontend.onrender.com` /
   `https://vigia-backend.onrender.com`) — Render asigna esas URLs reales la
   primera vez que se crea cada servicio (hay una dependencia circular
   inevitable la primera vez: no puedes saber la URL del otro servicio hasta
   que existe). Después del primer deploy, entra a cada servicio, copia su
   URL real, y actualiza la variable del otro en el dashboard (no hace falta
   tocar `render.yaml` de nuevo, se puede editar la variable directamente).
6. **Deploy.** Render construye ambos servicios. `healthCheckPath: /health`
   en `vigia-backend` hace que Render espere esa respuesta antes de marcar
   el deploy como live.

## Si quieres CertStream en vivo en producción (Item 5 de HANDOFF.md)

El listener de CertStream (`api/certstream_listener.py`) que corre dentro
del backend de Vigia **no genera datos por sí solo** — necesita conectarse
a un feed real de Certificate Transparency. El feed público gratuito está
muerto (Corrida 13), así que hace falta:

1. **Desplegar `certstream-server-go` por separado.** Es otro contenedor
   Docker (`0rickyy0/certstream-server-go`, la misma imagen que se probó
   local en Corrida 13), no un endpoint de Vigia — necesita su propio
   servicio en Railway/Render (o cualquier host con Docker), con su config
   default (`config.sample.yaml`, 45 CT logs reales) o una propia.
2. **Apuntar `VIGIA_CERTSTREAM_URL`** del backend de Vigia a la URL
   `ws://` (o `wss://` si el host da TLS) de ese servicio recién desplegado
   — mismo patrón que se probó local (`ws://localhost:48182` apuntando al
   contenedor de prueba de esa corrida).
3. **Agregar la dependencia Python `certstream`** a `pyproject.toml` (no
   incluida hoy — ver la sección "Lo que esto NO resuelve todavía" arriba).
   Sin ella, el listener se deshabilita solo con un log claro, no crashea,
   pero tampoco hace nada.
4. Sin estos 3 pasos, Item 5 sigue siendo "mecanismo verificado, no
   funcionalidad activa" en el deploy real — igual que ya lo documentaba
   `docs/produccion-readiness.md` sección 6 antes de esta sesión.

## Lo que Claude NO puede hacer por ti

Por diseño de esta tarea (no por limitación técnica evitable): ningún agente
de esta sesión creó, ni va a crear, una cuenta en Railway, Render, o
cualquier otro proveedor de hosting, y ninguno introdujo ni va a introducir
información de pago en ningún lado. Si en algún punto de este documento
parece que "falta un paso", ese paso es exactamente uno de estos:

- Crear la cuenta (Railway/Render) y conectar el repo de GitHub.
- Pegar los valores de las variables marcadas como obligatorias en el
  dashboard de cada plataforma (`JWT_SECRET`, `ANTHROPIC_API_KEY`,
  `CORS_ORIGINS`).
- Decidir y ejecutar la opción de persistencia de datos (Volume, Postgres
  administrado, o pasar a un plan pago) — es una decisión de costo/beneficio
  del negocio, no algo que un agente deba decidir en automático.
- Si se quiere CertStream en vivo: desplegar el segundo servicio
  (`certstream-server-go`) y decidir su propio plan/costo.

Todo lo demás — que el `Dockerfile` construya y corra, que el schema se
aplique solo, que exista una config real por plataforma, que la demo tenga
un solo comando y datos reproducibles sin depender de Docker/ZAP en vivo —
ya está hecho y verificado en esta sesión.
