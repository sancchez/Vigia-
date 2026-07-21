# Despliegue de Vigia — de "corre en mi laptop" a un servidor real

Sigue el estilo de `docs/cumplimiento.md`/`docs/produccion-readiness.md`:
documenta lo que se construyó y se probó de verdad en esta sesión, no un
plan teórico. Cierra el bloqueador #1 de `docs/produccion-readiness.md`
("Persistencia de proceso") en la medida en que código/config pueden
cerrarlo — la parte que falta (crear la cuenta de hosting, conectar el
repo, pagar si hace falta) es explícitamente tuya, no de Claude. Ver la
sección final "Lo que Claude NO puede hacer por ti" antes de asumir que
algo de esto ya está desplegado.

## Un solo servicio, no dos (cambio de esta sesión)

Hasta la sesión anterior, desplegar Vigia de verdad significaba correr
**dos** servicios: el backend FastAPI y el frontend (React/Vite) como sitio
estático aparte (`vigia-frontend` en `render.yaml`, o un host estático
separado tipo Vercel/Netlify si se usaba Railway). Dos servicios es más
setup, más superficie de configuración, y en la mayoría de plataformas más
costo (dos servicios facturables en vez de uno) — no ideal para un
presupuesto ajustado.

Esta sesión combinó ambos en **un solo contenedor deployable**:

- `Dockerfile` ahora es un build multi-stage: una etapa `node:22-slim`
  (`frontend-build`) corre `npm ci && npm run build` sobre `frontend/`, y la
  etapa final (`python:3.12-slim`, la misma de siempre) copia el resultado
  (`frontend/dist/`) a `/app/frontend/dist` dentro de la imagen final. La
  etapa de Node **nunca llega** a la imagen que corre en producción — solo
  su resultado.
- `api/main.py` (bloque final del archivo, "Servir el build de producción
  del frontend") detecta `frontend/dist/` al arrancar y, si existe, activa
  dos cosas: (1) un mount de `StaticFiles` en `/static-assets` para los
  JS/CSS con hash que genera Vite, y (2) una ruta catch-all
  (`GET /{full_path:path}`, registrada al final del archivo a propósito,
  después de cada ruta real de la API) que sirve `index.html` para
  cualquier GET que no sea ni una ruta real de la API ni un archivo real
  del build — necesario para que las rutas de React Router (`/login`,
  `/dashboard`) funcionen con un hard refresh o un link directo, no solo
  con navegación client-side.
- **Colisión de nombres real, no hipotética, que hubo que resolver:** el
  default de Vite pone los JS/CSS con hash bajo `dist/assets/` — pero
  `/assets` ya es una ruta real de la API (`GET/POST /assets`, CRUD de
  activos del tenant). Montar `StaticFiles` ahí habría creado una colisión
  de raíz entre el frontend y la API. Se resolvió cambiando
  `frontend/vite.config.ts` (`build.assetsDir: 'static-assets'`) en vez de
  intentar depender del orden de registro de rutas para desambiguar algo
  que es más simple evitar de raíz.
- **Protección contra path traversal real:** el catch-all resuelve
  `full_path` dentro de `frontend/dist/` con `Path.resolve()` y confirma con
  `is_relative_to()` que el resultado sigue siendo un descendiente real de
  ese directorio antes de servirlo — probado en vivo contra el contenedor
  real con `curl --path-as-is ".../../../../etc/passwd"` y una variante con
  path traversal URL-encoded (`%2e%2e/...`): ambos cayeron con seguridad al
  fallback de `index.html`, nunca expusieron nada del filesystem del host.
- **Los 404 reales de la API se preservan:** una lista de los prefijos de
  ruta reales (`auth`, `assets`, `scans`, `findings`, `reports`, `tenant`,
  `health`, `me`, `scan`, ...) hace que un typo de ruta de API (ej.
  `GET /auth/typo-de-ruta`) siga devolviendo `404` JSON real
  (`{"detail":"Not Found"}`), no `index.html` servido silenciosamente.
  Verificado en vivo contra el contenedor real, junto con que una ruta real
  de la API que sí existe pero requiere auth (ej. `GET /scans/no-existe` sin
  token) siga devolviendo `401` JSON real, sin que el catch-all se meta en
  el medio.
- **Activación condicional, sin romper el dev local:** todo este bloque solo
  se registra si `frontend/dist/` existe de verdad en disco al arrancar el
  proceso. El flujo de dev local documentado en `HANDOFF.md`
  (`uvicorn` + `vite --port ...` como dos procesos separados, sin build) no
  cambia en nada — sin un build presente, ninguna ruta nueva se agrega y el
  comportamiento es idéntico al de antes de esta sesión. `scripts/demo.ps1`
  tampoco se tocó.
- **`VITE_API_URL` ya no hace falta apuntarla a una URL absoluta** cuando
  el frontend se sirve desde el mismo origen que la API (que es el caso por
  defecto ahora): `frontend/src/api.ts` construye cada request como
  `` `${API_URL}${path}` ``, y `API_URL` cae a `""` si `VITE_API_URL` es una
  cadena vacía — eso produce paths relativos (`/auth/login`, no
  `https://.../auth/login`), que el navegador resuelve automáticamente
  contra el origen actual. El `Dockerfile` ya construye el frontend con
  `ARG VITE_API_URL=""` por default por esta razón. Se deja como `ARG`
  configurable (no hardcodeado) por si en algún escenario futuro el
  frontend de esta misma imagen se sirviera desde un origen distinto al
  backend — no es el caso del deploy combinado que este documento describe,
  pero no cuesta nada dejar la puerta abierta sin comprometer el default
  simple de hoy.

## Qué existe ahora, verificado en esta sesión

- **`Dockerfile`** (raíz del repo, multi-stage: `node:22-slim` para el
  frontend + `python:3.12-slim` para el runtime) — **construido y corrido
  de verdad esta sesión** (`docker build -t vigia-combined:test .` +
  `docker run -p 48210:8000 ...`), no solo "el build terminó sin error".
  Verificado con HTTP real contra el contenedor corriendo:
  - `GET /health` → `200` real (JSON con el aviso legal completo).
  - `GET /` → `200` real con el **HTML real del frontend** (`<title>Vigia —
    panel</title>`, referencias a `/static-assets/index-*.js` y `.css`
    reales, no un placeholder).
  - `GET /dashboard` (ruta de cliente de React Router, no un archivo real)
    → `200` con el mismo `index.html` — confirma que el fallback de SPA
    funciona para un link directo/hard-refresh, no solo navegación
    client-side.
  - `GET /static-assets/index-<hash>.js` → `200`, `Content-Type:
    text/javascript`.
  - `GET /favicon.svg` (viene de `frontend/public/`, copiado a la raíz del
    build por Vite) → `200`, `Content-Type: image/svg+xml`.
  - `GET /auth/typo-nonexistent` → `404` JSON real (`{"detail":"Not
    Found"}`), no `index.html`.
  - `GET /scans/no-existe` sin token → `401` JSON real
    (`{"detail":"Not authenticated"}`) — la ruta real de la API sigue
    ganando sobre el catch-all.
  - `GET /static-assets/no-existe.js` → `404` JSON real (el propio
    `StaticFiles` de Starlette, no el catch-all).
  - `POST /auth/register` real contra ese mismo contenedor confirmó, de
    nuevo, que el schema (`db/schema.sql`) se aplica solo (token JWT real
    devuelto, `200`) — mismo comportamiento que ya estaba verificado antes
    de esta sesión, repetido aquí para confirmar que el cambio de Dockerfile
    no rompió nada del arranque del backend.
  - Dos intentos de path traversal (`curl --path-as-is
    ".../../../../etc/passwd"` y una variante URL-encoded `%2e%2e/...`)
    cayeron con seguridad al fallback de `index.html`, sin exponer nada del
    filesystem del contenedor.
  Contenedor e imagen de prueba se detuvieron y se borraron al terminar la
  verificación (`docker stop/rm`, `docker rmi`) — no queda nada corriendo ni
  ninguna imagen local de esta sesión.
- **`.dockerignore`** — evita copiar `frontend/node_modules/`, `frontend/dist/`
  (se reconstruye dentro del contenedor, no se reutiliza el del host),
  `tests/`, `.git/`, bases de datos locales, etc. al contexto de build. No
  necesitó cambios esta sesión — ya excluía `frontend/node_modules/` y
  `frontend/dist/` sin excluir el resto de `frontend/` (código fuente,
  `package.json`), que es justo lo que la nueva etapa `frontend-build`
  necesita copiar.
- **`railway.json`** y **`render.yaml`** — config real para ambas
  plataformas, escrita contra su documentación actual (no adivinada — ver
  la sección de cada plataforma abajo para qué se verificó puntualmente).
  `render.yaml` se simplificó esta sesión: el servicio `vigia-frontend`
  (sitio estático aparte) se eliminó — ahora un solo servicio
  (`vigia-backend`, que ya sirve el frontend) es todo lo que hace falta.
  `railway.json` no necesitó cambios estructurales (ya era un único servicio
  basado en `Dockerfile`) — el cambio real está en que ese mismo Dockerfile
  ahora produce una imagen que sirve ambas cosas. Ninguno de los dos se
  probó desplegando de verdad (eso requeriría crear una cuenta y no es algo
  que Claude puede hacer, ver el cierre de este documento) — lo que sí está
  verificado es que el `Dockerfile` que ambos usan corre y responde
  `/health` **y** la UI real de verdad, en un solo contenedor.
- **`scripts/demo.ps1`** — sigue sin tocarse, sigue siendo el flujo de *dev
  local* (dos procesos separados, sin build) documentado en `HANDOFF.md` —
  no se probó de nuevo esta sesión porque no cambió, y el objetivo de esta
  sesión fue el flujo de *producción* (un contenedor), no el de desarrollo.
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
| `ANTHROPIC_API_KEY` | `agents/_llm.py` | **Sí (o `OPENAI_API_KEY`), antes del primer cliente de pago** | Sin ella, `agents/_llm.py` cae al fallback `claude -p` (CLI) — no apto para carga concurrente real (ver Item 1 de `HANDOFF.md`), y **no disponible en absoluto dentro del contenedor desplegado** (requiere la CLI de Claude Code instalada y autenticada, que este `Dockerfile` no instala a propósito — ver Corrida 19 de `eval/live_run_report.md`: la CLI solo funciona en una máquina con Claude Code instalado y con sesión iniciada, nunca en Railway/Render). Para producción real hace falta esta key **o** `OPENAI_API_KEY` — ver las tres filas siguientes. |
| `VIGIA_CLAUDE_CLI_TIMEOUT` | `agents/_llm.py` | No | Solo aplica al fallback de CLI de arriba — irrelevante si `ANTHROPIC_API_KEY` está configurada. Default 300 (segundos). |
| `CIBERSEGURIDAD_LLM_MODEL` | `agents/_llm.py` | No | Default `claude-sonnet-4-5-20250929`. |
| `OPENAI_API_KEY` | `agents/_llm.py` | No (alternativa a `ANTHROPIC_API_KEY`) | **Opción barata agregada en Corrida 19** (ver `eval/live_run_report.md`). A diferencia de Anthropic, OpenAI **no tiene fallback de CLI** — sin esta key, si `VIGIA_LLM_PROVIDER=openai` (o se auto-detecta), `agents/_llm.py` falla explícito (`LLMNoDisponibleError`) en vez de degradar. |
| `VIGIA_LLM_PROVIDER` | `agents/_llm.py` | No | `anthropic` \| `openai`. **Opcional a propósito** — si no se configura, se auto-detecta: `ANTHROPIC_API_KEY` presente gana (no rompe el default de nadie), si no `OPENAI_API_KEY` presente, si ninguna está presente cae al fallback de CLI de Anthropic (comportamiento idéntico al de antes de que esta variable existiera). Configúrala solo si tenés ambas keys y querés forzar cuál se usa. |
| `VIGIA_OPENAI_MODEL` | `agents/_llm.py` | No | Default `gpt-5.4-mini` — precio real confirmado en `developers.openai.com/api/docs/pricing` en Corrida 19: **$0.75 / $4.50 por millón de tokens (input/output)**. Costo real estimado por reporte (asumiendo ~4 llamadas por ciclo completo — priorización, remediación, reportería, cumplimiento — de ~4.000 tokens de entrada y ~1.000 de salida cada una): `(4000×0.75 + 1000×4.50)/1e6 × 4 ≈ $0.03` por reporte completo, contra ~$0.11 con la API real de Claude Sonnet 5 (`$3/$15` por millón, precio con descuento de introducción vigente hasta 2026-08-31) — **~3.6× más barato**. Se comparó también contra Gemini 2.5 Flash-Lite (`$0.10/$0.40` por millón) y DeepSeek V4 Flash (`$0.14/$0.28` por millón, cache-miss), ambos más baratos aún pero **no implementados esta sesión** — ver `eval/live_run_report.md` Corrida 19 para el detalle completo de la comparación y por qué se eligió OpenAI (mejor soporte de LangChain vía `langchain-openai`, igual patrón que `langchain-anthropic` ya usado en este módulo, y calidad de salida en español/JSON estructurado más probada que las alternativas más baratas). |
| `CORS_ORIGINS` | `api/main.py` | No, con el deploy combinado de esta sesión | Lista separada por comas de orígenes permitidos. Con el frontend servido desde el MISMO origen que la API (ver sección "Un solo servicio, no dos" arriba), las llamadas del panel ya no son cross-origin, así que esta variable deja de ser estrictamente necesaria para que el panel funcione. Se mantiene útil para golpear la API desde otro origen (Postman, un script, o el flujo de dev local de `HANDOFF.md` con `uvicorn`+`vite` como procesos separados, donde SÍ sigue haciendo falta apuntarla al puerto del frontend). |
| `VIGIA_SCAN_INTERVAL_HOURS` | `api/scheduler.py` | No | Default 6. Escaneo pasivo recurrente sobre todos los assets activos. |
| `VIGIA_CERTSTREAM_ENABLED` | `api/certstream_listener.py` | No | Default `true`. Ponerla en `false` si no se quiere ni intentar conectar (evita el ruido de logs de reconexión si no hay feed configurado). |
| `VIGIA_CERTSTREAM_URL` | `api/certstream_listener.py` | No (pero ver nota abajo) | El feed público histórico (`wss://certstream.calidog.io`) está descontinuado (confirmado en Corrida 13) — sin esta variable apuntando a una instancia propia de `certstream-server-go`, el listener no recibe nada. Ver sección dedicada abajo. |
| `VIGIA_CERTSTREAM_REFRESH_MINUTES` | `api/certstream_listener.py` | No | Default 30. |
| `GOOGLE_SAFE_BROWSING_API_KEY` | `tools/antisuplantacion.py` | No | Mejora la anti-suplantación si se configura; el módulo funciona sin ella. |
| `PORT` | Inyectada por Railway/Render, leída por el `CMD` del `Dockerfile` | Automática | No la configures a mano — cada plataforma la inyecta sola. Localmente (`docker run`) cae a 8000 si no está seteada. |
| `SUPABASE_URL` / `SUPABASE_KEY` | — | No | Documentadas en `.env.example` pero **inertes hoy** (confirmado por `grep`, ningún `os.getenv` las lee) — intención futura, no funcionalidad actual. No las configures esperando que hagan algo. |
| `APP_ENV` / `LOG_LEVEL` | — | No | Mismo caso: inertes hoy. |
| `WOMPI_PUBLIC_KEY` / `WOMPI_PRIVATE_KEY` / `WOMPI_EVENTS_SECRET` | — | No | Inertes — integración de pagos en pausa a propósito (ver `HANDOFF.md`, "Cosas que NO hacer sin preguntar primero"). |
| `VITE_API_URL` | Frontend (`frontend/src/api.ts`), tiempo de **build**, no runtime | No, con el deploy combinado (default `""`) | Antes de esta sesión, obligatoria (URL absoluta del backend, porque frontend y backend eran servicios distintos con orígenes distintos). Con el `Dockerfile` combinado, el `ARG VITE_API_URL=""` por default hace que `frontend/src/api.ts` arme paths **relativos** (`/auth/login`, no `https://.../auth/login`) — el navegador los resuelve solos contra el mismo origen que sirvió el HTML, sin necesitar saber la URL pública del backend de antemano. Solo hace falta pasar `--build-arg VITE_API_URL=https://...` si en algún escenario futuro el frontend se sirviera desde un origen DISTINTO al backend (no es el caso del deploy combinado de este documento). Como sigue siendo de build (Vite la inyecta al compilar, no al arrancar), cambiarla obligaría a un rebuild de la imagen, no solo un restart. |

## Desplegar en Railway — pasos concretos

Railway soporta un servicio desde `Dockerfile` directamente (confirmado
contra `docs.railway.com/reference/config-as-code` antes de escribir
`railway.json`) — no hace falta convertir nada a Nixpacks/buildpacks. Con el
`Dockerfile` combinado de esta sesión (multi-stage, node + python), **este
único servicio ya sirve backend y frontend juntos** — no hace falta un
segundo servicio ni una plataforma aparte para el frontend.

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
   ("Settings" → "Networking" → "Generate Domain"). Abrí esa URL directo en
   el navegador — es el panel real (React) servido por el mismo servicio, no
   solo la API. No hace falta configurar `VITE_API_URL` (queda vacía por
   default, ver la tabla de variables arriba) ni desplegar nada más: este
   único servicio ya es el deploy completo.

## Desplegar en Render — pasos concretos

Render soporta un `render.yaml` (Blueprint) para desplegar servicios Docker
(confirmado contra `render.com/docs/blueprint-spec` antes de escribirlo,
incluyendo el formato real de `envVars`/`sync: false` y la diferencia entre
el campo moderno `runtime` y el `env` más viejo, que la doc marca como
desalentado). Esta sesión simplificó `render.yaml` a **un solo servicio**
(`vigia-backend`) — antes definía un segundo servicio (`vigia-frontend`,
sitio estático de Render) que ya no hace falta porque el `Dockerfile`
combinado sirve el frontend desde el mismo contenedor.

1. **Cuenta y repo — esto lo haces tú, no Claude.** Entra a
   [render.com](https://render.com), crea una cuenta (o inicia sesión con
   GitHub), autoriza el acceso al repo. Mismo límite que con Railway: ningún
   agente de esta sesión puede hacer esto por ti.
2. **New → Blueprint**, selecciona el repo. Render detecta `render.yaml` en
   la raíz automáticamente y muestra el único servicio que define
   (`vigia-backend`) para revisar antes de crear.
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
5. **`CORS_ORIGINS`**: `render.yaml` trae un valor de ejemplo
   (`https://vigia-backend.onrender.com`, el propio dominio del servicio) —
   ya no hay una URL de un segundo servicio de frontend que resolver por
   dependencia circular, porque no existe ese segundo servicio. No hace
   falta configurar `VITE_API_URL`: el `Dockerfile` la deja vacía por
   default (ver tabla de variables arriba), y con un solo servicio el
   frontend y la API comparten origen de todas formas.
6. **Deploy.** Render construye el servicio (etapa `node` para el frontend +
   etapa `python` para el runtime, ver `Dockerfile`). `healthCheckPath:
   /health` en `vigia-backend` hace que Render espere esa respuesta antes de
   marcar el deploy como live. Abrí la URL que Render asigna directo en el
   navegador para ver el panel real — es el mismo servicio.

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
