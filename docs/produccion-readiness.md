# Brecha demo vs. producción — dónde está Vigia realmente

*Análisis de qué falta para (a) mostrar el proyecto funcionando en vivo a un
prospecto y (b) cobrarle a un cliente real por usarlo. No repite lo ya
documentado en `HANDOFF.md` corrida por corrida — sintetiza el estado actual
de infraestructura/operación y traduce cada brecha en un siguiente paso
concreto, no en un consejo genérico.*

## Resumen ejecutivo

**Actualizado 2026-07-19 (Corrida 15):** los párrafos de abajo describían el
estado antes de que existieran CI, tests, Dockerfile y config de hosting —
los cuatro ya existen y están verificados (ver "Checklist production-ready"
más abajo, puntos 3, 4 y 1). Lo que sigue siendo cierto, sin cambios: nada
de esto está desplegado contra la infraestructura real de ningún hosting
todavía — eso requiere que el usuario cree una cuenta y conecte el repo (ver
`docs/despliegue.md`), un paso que ningún agente puede dar por él.

Todo lo construido hasta ahora (pipeline LangGraph, backend multi-tenant,
frontend, Nuclei/ZAP/Semgrep/Trivy/Grype, CertStream, cumplimiento) fue
**probado en vivo pero nunca desplegado** — cada sesión de trabajo levantaba
todo a mano en `localhost` y todo moría cuando la sesión terminaba. Eso ya
tiene una solución empaquetada (`Dockerfile`, `scripts/demo.ps1`,
`scripts/seed_demo.py`, `railway.json`, `render.yaml`) pero **el despliegue
real en sí sigue sin ocurrir** — la única forma de generar razonamiento con
IA sin pagar (`claude -p` vía CLI) sigue explícitamente documentada como no
apta para carga concurrente real. Esto ya no es un problema de "falta
empaquetar" — es que falta que alguien con acceso a una cuenta de hosting dé
el siguiente paso, documentado con detalle en `docs/despliegue.md`.

La buena noticia: la arquitectura ya tomó decisiones que hacen el camino a
producción más corto de lo que parece. `db/connection.py` aísla SQLite
detrás de `get_conn()`/`dict_from_row()` y el schema (`db/schema.sql`) ya
está escrito en un dialecto portable a Postgres a propósito — migrar es un
cambio localizado a un archivo, no una reescritura (ver docstring del
módulo). El backend ya lee `CORS_ORIGINS`, `DATABASE_URL` y `JWT_SECRET` de
entorno, no hardcodeados. La brecha real está en **empaquetado,
persistencia de proceso, y verificación automatizada** — no en el diseño del
sistema.

## Sanity check de auth en endpoints nuevos de esta sesión

Se revisaron los tres endpoints agregados/modificados en esta sesión
(`grep` de `Depends(get_current_user)` sobre cada `@app.` en `api/main.py`):

| Endpoint | Auth |
|---|---|
| `GET /reports/cumplimiento` | ✅ `Depends(get_current_user)` |
| `POST /scan/activo` | ✅ `Depends(get_current_user)` (más el gate propio de `autorizacion_firmada=true` explícito en el body, 403 si falta) — **actualizado**: también valida vía `_tenant_tiene_asset_para_target()` que `target_url` corresponda a un asset del tenant (`POST /assets` primero), 403 si no. Antes de esto, `autorizacion_firmada=true` por sí solo bastaba para escanear cualquier URL — hallazgo real de `agents/revision_ia.py`, ver HANDOFF.md y `tests/test_scan_activo_asset_gate.py`. |
| `GET /scans/{scan_id}` | ✅ `Depends(get_current_user)` |

Sin hallazgos — los tres requieren JWT válido igual que el resto de rutas no
públicas (`/auth/register`, `/auth/login` y `/health` son las únicas
intencionalmente abiertas). El listener de CertStream (`api/certstream_listener.py`)
no expone ningún endpoint HTTP propio — es un daemon thread que solo
consume un feed externo y escribe a `findings`, así que no aplica el mismo
chequeo, pero sí vale anotar la superficie nueva que abre: una conexión
websocket saliente persistente mientras la API esté viva, hacia una URL
configurable por operador (`VIGIA_CERTSTREAM_URL`). Nada de esto acepta
tráfico entrante nuevo, así que el riesgo es bajo, pero es la primera vez
que Vigia mantiene una conexión de red de larga duración fuera del ciclo
request/response — vale tenerlo en el radar si se audita la superficie de
red del servicio en el futuro.

**Gap real encontrado en `.env.example`, no relacionado con auth:** documenta
`VIGIA_SCAN_INTERVAL_HOURS` pero no las tres variables nuevas de CertStream
(`VIGIA_CERTSTREAM_URL`, `VIGIA_CERTSTREAM_REFRESH_MINUTES`,
`VIGIA_CERTSTREAM_ENABLED` — esta última ni siquiera estaba en la lista de
variables que motivó este análisis, se encontró leyendo
`api/certstream_listener.py` directamente). Cualquiera que despliegue Vigia
desde `.env.example` hoy no sabría que esas variables existen. Arreglo
trivial (agregar 3 líneas a `.env.example`), vale la pena hacerlo en la
próxima sesión que toque ese archivo.

**Confirmado inertes:** `SUPABASE_URL`, `SUPABASE_KEY`, `APP_ENV`,
`LOG_LEVEL`, `WOMPI_PUBLIC_KEY`, `WOMPI_PRIVATE_KEY`, `WOMPI_EVENTS_SECRET`
están en `.env.example` pero no aparecen en ningún `os.environ`/`os.getenv`
del código (`grep` sobre todo `*.py`) — documentan intención futura
(Supabase como backend Postgres, Wompi para pagos, pausado a propósito según
`HANDOFF.md`), no funcionalidad actual. No es un bug, pero si alguien las
configura esperando que hagan algo hoy, se va a llevar una sorpresa.

## Checklist "demo-ready" — mostrarlo funcionando a un prospecto

Objetivo: una sesión en vivo (o una llamada por Zoom compartiendo pantalla)
donde el prospecto ve el flujo real — registro, escaneo, hallazgos, reporte
de cumplimiento — sin que se caiga a mitad de la demo. Esfuerzo bajo,
urgencia alta porque no depende de resolver ningún problema arquitectónico,
solo de dejar de reconstruir todo a mano cada vez.

1. ✅ **HECHO y VERIFICADO (2026-07-19, ver `eval/live_run_report.md`
   Corrida 15).** `scripts/demo.ps1` arranca backend + frontend, valida
   `docker ps` (advierte sin fallar si no responde), y espera una respuesta
   real de `/health` (200) antes de declarar el backend listo, y del
   frontend antes de abrir el navegador. Corrido de punta a punta en esta
   sesión (`./scripts/demo.ps1 -SkipBrowser`): detectó Docker corriendo,
   levantó ambos jobs de PowerShell, esperó las respuestas HTTP reales de
   ambos servicios, y se verificó que los jobs quedan limpios al detenerlos.
2. ✅ **HECHO y VERIFICADO (2026-07-19, ver `eval/live_run_report.md`
   Corrida 15).** `scripts/seed_demo.py` crea el tenant `Vigia Demo`
   (`demo@vigia.local` / `DemoVigia2026`), 3 assets, y un scan completado con
   los 10 hallazgos reales de Juice Shop ya congelados en
   `eval/cumplimiento_fixture_juiceshop.json` (los mismos usados para
   verificar Item 6, ver `docs/cumplimiento.md`) — idempotente (no duplica
   si ya existe) y con `--reset` para recrear desde cero. Probado contra una
   base SQLite descartable real: dos corridas normales + una con `--reset`,
   más un smoke test HTTP real (login real, `GET /findings` devolvió los 10
   hallazgos, `GET /assets` devolvió los 3 assets) contra un `uvicorn` real
   apuntando a esa base sembrada. Así la demo no depende de que Docker/ZAP
   funcionen en el momento exacto de la llamada con el prospecto.
3. **Decidir qué mostrar en vivo vs. qué mostrar pre-cargado.** Con el punto
   2 resuelto, la demo puede combinar ambos: mostrar el dashboard con datos
   pre-cargados (siempre funciona) y, si el tiempo/confianza lo permite,
   disparar un `POST /scan` en vivo contra Juice Shop como "miren, esto es
   real, no una captura de pantalla" — pero como acto secundario, no como
   dependencia crítica del flujo principal.
4. 🟡 **Config lista, falta el paso que solo el usuario puede dar.** El
   `Dockerfile`, `railway.json` y `render.yaml` de esta sesión (ver sección
   1 abajo y `docs/despliegue.md`) son exactamente lo que hace falta para un
   despliegue de un solo comando en Railway o Render — verificados
   localmente (build + run + `/health` real contra el `Dockerfile`), pero
   **nadie desplegó todavía contra la infraestructura real de ninguna de las
   dos plataformas** porque eso requiere crear una cuenta y conectar un
   repo, algo que ningún agente de esta sesión puede hacer (ver
   `docs/despliegue.md`, cierre del documento). Ese paso sigue siendo tuyo.
   Mientras tanto, la alternativa mínima sigue siendo un túnel
   (`ngrok`/`cloudflared`) sobre el setup local, apuntando el frontend a esa
   URL en vez de `localhost`.
5. **Un caso de fallo con respuesta preparada.** Docker se ha caído
   múltiples veces esta sesión — asumir que no pasará en la demo es
   optimista. Tener el punto 2 (datos pre-cargados) como fallback
   explícito, y decir en voz alta "esto corre contra Docker en vivo, si se
   cae uso los datos de la corrida de esta mañana" es más profesional que
   improvisar en silencio si Docker Desktop decide no responder.

**No hacía falta para demo-ready** (y de todas formas ya está resuelto por
otras razones): tests automatizados, CI/CD, Postgres, Dockerfile del propio
Vigia (los tres ya existen, ver más abajo), ni resolver la limitación de
`claude -p` bajo carga concurrente (una demo tiene un usuario a la vez).

## Checklist "production-ready" — cobrarle a un cliente real

Objetivo: que un cliente pyme pueda registrarse, agregar sus dominios, y
recibir escaneos recurrentes + alertas de suplantación sin que el sistema
dependa de que alguien tenga una sesión de Claude Code abierta. Esfuerzo
alto — esto sí requiere resolver problemas de arquitectura, no solo de
empaquetado.

### 1. Persistencia de proceso (bloqueador #1)

🟡 **Código/config HECHOS y VERIFICADOS localmente (2026-07-19, ver
`eval/live_run_report.md` Corrida 15 y `docs/despliegue.md`) — el deploy
real contra Railway/Render sigue sin hacerse**, porque requiere crear una
cuenta de hosting y conectar el repo, que es explícitamente responsabilidad
del usuario, no de un agente (ver el cierre de `docs/despliegue.md`). Hasta
que ese paso se dé, el servidor sigue muriendo cuando termina la sesión de
Claude Code — pero ya no falta ningún artefacto de código para que ese
despliegue sea, en palabras del propio pedido de esta tarea, "un paso
conocido" en vez de una incógnita.

- **`Dockerfile`** (raíz del repo, nuevo) — construido y corrido de verdad
  (`docker build --load` + `docker run`), `GET /health` respondió 200 real
  contra el contenedor, y un `POST /auth/register` real confirmó que
  `db/schema.sql` se aplica solo al arrancar (sin paso de migración manual,
  ver `db/connection.py::get_conn()`, ya idempotente desde la migración a
  Postgres de la sesión anterior). Imagen y contenedor de prueba se
  borraron al terminar la verificación.
- **`railway.json`** (nuevo) — `build.builder: "DOCKERFILE"` +
  `deploy.healthcheckPath: "/health"`, escrito contra la documentación real
  de Railway (`docs.railway.com/reference/config-as-code`), no adivinado.
- **`render.yaml`** (nuevo) — Blueprint con `vigia-backend` (Docker) +
  `vigia-frontend` (sitio estático, build de Vite), escrito contra
  `render.com/docs/blueprint-spec` real, incluyendo el aviso explícito de
  que el filesystem del plan free de Render es efímero (SQLite se perdería
  en cada redeploy) y que un Postgres free de Render expira a los 30 días —
  ambos datos confirmados contra la documentación/changelog real de Render,
  no supuestos.
- **Railway**: soporta un servicio desde `Dockerfile` directamente (ver
  `railway.json` de arriba), Volumes para disco persistente si se pospone
  Postgres, y un servicio Postgres administrado con un clic el día que se
  quiera (la migración de código ya está hecha, ver punto 2 abajo). Buen fit
  porque el propio ecosistema Freestyle ya usa un stack similar en otros
  proyectos (`CLAUDE.md` del monorepo: "Backend: FastAPI + Python 3.12").
- **Render**: alternativa directa, mismo `Dockerfile` para el "Web Service"
  + "Static Site" gratis para el build de Vite del frontend, separados (el
  backend ya lee `CORS_ORIGINS` de entorno, así que servir frontend y
  backend en dominios distintos no requiere cambios de código). Persistent
  Disks no disponibles en el plan free (confirmado) — la opción real de
  persistencia sin pasar a un plan pago es el Postgres administrado gratis
  de 30 días que `render.yaml` deja listo (comentado) para descomentar.
- Cualquiera de las dos evita administrar un VPS a mano, que sería trabajo
  adicional sin beneficio dado el tamaño actual del proyecto.
- **Pasos numerados completos para el usuario** (cuenta, repo, variables de
  entorno, decisión de persistencia) en `docs/despliegue.md` — no se
  repiten aquí para no tener dos fuentes de verdad desincronizables.

### 2. Migración SQLite → Postgres

✅ **HECHO y VERIFICADO contra Postgres 16 real (2026-07-19, ver
`eval/live_run_report.md` Corrida 14).** Ya no es una suposición de diseño —
se levantó un Postgres 16 real vía Docker, se corrió `db/schema.sql` contra
él sin tocarlo, se implementó el backend en `db/connection.py`
(`psycopg[binary]>=3.2`, agregado a `pyproject.toml`), se corrió la suite de
pytest completa contra ambos motores, y se verificó un flujo real por HTTP
(no `TestClient`) contra un `uvicorn` real apuntando a ese Postgres real:
registro, login, agregar asset, insertar un finding, leerlo de vuelta, y
aislamiento multi-tenant confirmado entre dos tenants reales.

**Lo que resultó cierto, verificado:** el schema (`db/schema.sql`) corrió
**sin ningún cambio** contra Postgres real, primera vez y de forma
idempotente en una segunda corrida — el dialecto portable que este
documento llevaba tiempo prometiendo era correcto en la práctica, no solo
en el diseño (sin `AUTOINCREMENT`, IDs UUID generados en Python, tipos
`TEXT`/`INTEGER`/`TIMESTAMP` válidos en ambos motores).

**Lo que resultó genuinamente NO portable (encontrado corriendo la suite
real, no adivinado):** no fue el schema SQL en sí, sino el mapeo de tipos
Python de cada driver. `sqlite3` siempre devolvió las columnas `TIMESTAMP`
como `str` crudo; `psycopg` las parsea a `datetime.datetime` real, lo cual
rompía los modelos Pydantic (`created_at: str`) de varios endpoints
(`GET /scans`, `GET /findings`) que leen `row["created_at"]` directo sin
pasar por `dict_from_row()`. Arreglado con un loader custom de psycopg
(`_TimestampStrLoader` en `db/connection.py`) que hace que Postgres devuelva
esas columnas como `str`, igual formato que sqlite3 (truncado a segundos,
ya que Postgres guarda microsegundos por defecto en `CURRENT_TIMESTAMP` y
sqlite no) — cambio localizado a `db/connection.py`, cero cambios en
endpoints o modelos.

**Diseño del backend nuevo:** `_PgConnection` envuelve `psycopg.Connection`
traduciendo placeholders `?` (estilo sqlite3, todo el proyecto los usa así)
a `%s` (estilo psycopg) con un `str.replace` simple — seguro porque se
confirmó por grep que ninguna query real del proyecto usa `?`/`%` literal
dentro de una cadena SQL. `psycopg.rows.dict_row` como `row_factory` hace
que `fetchone()`/`fetchall()` devuelvan `dict`, compatible sin cambios con
`dict_from_row()` (que ya hacía `dict(row)`). El schema se aplica una vez
por proceso por cada `DATABASE_URL` de Postgres (cacheado, con lock) en vez
de en cada conexión como con SQLite, para no pagar el costo de DDL
redundante contra un Postgres remoto en cada request.

**SQLite sigue siendo el default** — quien no configure `DATABASE_URL` (o
lo deje en `sqlite:///...`) no ve ningún cambio de comportamiento; los 44
tests de la suite pasan igual contra ambos motores. `tests/conftest.py`
ahora soporta un override opcional (`VIGIA_PG_TEST_URL`) para correr la
misma suite contra una Postgres real bajo demanda, sin convertir la suite
completa a parametrizarse contra ambos motores en cada corrida (decisión
explícita — ver Corrida 14 para el razonamiento completo).

**Sigue pendiente, no bloqueante:** pooling real de conexiones
(`psycopg_pool`) en vez de una conexión por request — mismo patrón que ya
tenía SQLite, mencionado como mejora futura pero no necesario para que la
migración en sí sea correcta. Tampoco se tocó `.env.example` (fuera de
alcance explícito de la sesión que hizo esta migración).

### 3. Tests automatizados — qué probar primero y por qué

✅ **HECHO (2026-07-19, ver `eval/live_run_report.md` Corrida 9).** Los 5
tests de abajo ya existen en `tests/`, corren de verdad y pasan (`pytest -v`
→ 35 passed, incluye casos adicionales de contraprueba más allá de los 5
mínimos). `pytest`/`pytest-asyncio` estaban declarados en `pyproject.toml`
desde el inicio del proyecto pero cero archivos `test_*.py` existían hasta
esta sesión. Se mantiene el detalle original de la priorización (por qué
cada uno importa) como referencia:

1. ✅ **`agents/_llm.py::_call_via_cli` — el fix de encoding UTF-8.**
   `tests/test_llm_cli_encoding.py`. Ya se rompió una vez de forma
   silenciosa (mojibake en español, ver Item 6 de `HANDOFF.md`) y el propio
   `docs/cumplimiento.md` señalaba que nadie había verificado todavía que
   el fix no rompió `reporteria.py`/`remediacion.py`/`priorizacion.py`
   (los tres comparten este mismo helper). El test fuerza tildes/ñ/em-dash
   a través de un subproceso real (no un mock en memoria) y confirma que
   `_call_via_cli` sigue pasando `encoding="utf-8"` explícito a
   `subprocess.run`.
2. ✅ **`orchestrator/graph.py::gate_autorizacion`.** `tests/test_gate_autorizacion.py`.
   Es el control de seguridad más importante del producto entero — si
   alguna vez deja pasar un escaneo activo sin `autorizacion_firmada=true`,
   Vigia atacaría un objetivo sin permiso, que es exactamente lo que
   `plan-proyecto-ciberseguridad.md` sección 0 prohíbe. El test confirma
   que el grafo bloquea de forma determinista con
   `autorizacion_firmada=False` explícito (y solo con eso, no con la
   ausencia del campo, que también se prueba por separado), que exige el
   booleano `True` exacto (no valores truthy como `1`/`"true"`), y —a nivel
   de integración sobre el grafo compilado real— que `agents/escaneo.py`
   nunca se invoca cuando la puerta está cerrada.
3. ✅ **`agents/cumplimiento.py::_categorizar_hallazgo` / `_texto_buscable`.**
   `tests/test_cumplimiento_categorizacion.py` + fixture
   `eval/cumplimiento_fixture_juiceshop.json` (los 10 hallazgos reales,
   extraídos directo de `ciberseguridad.db`). Ya hubo un bug real de falso
   positivo por incluir `desc` en el texto de clasificación (CSP Header
   categorizado como `inyeccion` por mencionar "XSS" en su descripción, ver
   `docs/cumplimiento.md`) — el test incluye una aserción explícita para
   ese caso exacto y para el otro caso real (Cross-Origin-Embedder-Policy
   vs. `cors_mal_configurado`).
4. ✅ **Flujo de auth end-to-end (`POST /auth/register` → `POST /auth/login`
   → `GET /me` con el JWT).** `tests/test_auth_flow.py`. Es la base de la
   que depende literalmente cada otro endpoint (`Depends(get_current_user)`
   en todos menos 3 rutas, confirmado arriba) — si esto se rompe, rompe
   todo el producto a la vez.
5. ✅ **Aislamiento multi-tenant en `GET /findings` y `GET /reports/cumplimiento`.**
   `tests/test_multi_tenant_isolation.py`. Ya se había verificado
   manualmente una vez (Item 6, Item 5) que los datos de un tenant no se
   filtran a otro — es exactamente el tipo de bug que un cambio futuro
   descuidado (un `WHERE tenant_id = ?` olvidado en una query nueva)
   reintroduciría en silencio sin un test que lo agarre.

Infraestructura compartida en `tests/conftest.py`: cada test corre contra
su propio archivo SQLite temporal (nunca `ciberseguridad.db`), y un fixture
autouse fuerza `LLMNoDisponibleError` por defecto para que ningún test
dispare una llamada real a Claude (esta máquina de desarrollo tiene tanto
`ANTHROPIC_API_KEY` potencial como el binario `claude` instalados).

No hace falta cobertura exhaustiva para vender el primer contrato — estos 5
cubren el control de seguridad más crítico (autorización), el bug que ya
mordió dos veces (encoding), y el supuesto de negocio que un cliente
esperaría que fuera cierto sin preguntarlo (aislamiento de datos).

### 4. CI/CD mínimo

✅ **HECHO (2026-07-19).** `.github/workflows/tests.yml` — `ubuntu-latest`,
Python 3.12, `pip install -e ".[dev]"`, `ruff check .` (ya declarado como
dependencia dev, confirmado antes de agregarlo), `pytest -v`. Corre en cada
push a `main` y en cada PR. Verificado corriendo el mismo flujo en un venv
limpio local antes de comprometer el workflow (ver Corrida 9). A propósito
NO incluye deploy ni matriz de versiones — el pedido explícito era la
diferencia entre "hope nothing broke" y "know in 90 seconds", no una
pipeline completa. Deploy automático a Railway/Render sigue pendiente de
que el hosting mismo esté decidido y probado a mano primero.

### 5. IA real para carga concurrente

`agents/_llm.py` ya soporta `ANTHROPIC_API_KEY` como backend directo — el
fallback de CLI (`claude -p`) es explícitamente para desarrollo/demo, no
para multi-tenant real (documentado en Item 1 de `HANDOFF.md` y confirmado
de nuevo en `docs/cumplimiento.md`). Este es el único punto de la lista que
no requiere escribir código nuevo — solo configurar `ANTHROPIC_API_KEY` como
variable de entorno en el hosting elegido antes de aceptar el primer
cliente de pago. Vale la pena estimar costo real por reporte (tokens de
`priorizacion.py` + `remediacion.py` + `reporteria.py` + `cumplimiento.py`
por escaneo) antes de fijar precio, dado el rango de $49-299 USD/mes que
`docs/market-research.md` sección "Resumen ejecutivo" recomienda como ancla.

### 6. Feed real de CertStream

Documentado ya en Item 5 de `HANDOFF.md` como brecha conocida: el feed
público gratuito (`wss://certstream.calidog.io`) está descontinuado —
vigilancia continua real de suplantación de dominio no funciona hoy sin
levantar una instancia propia de `certstream-server-go`/`-rust`
(self-hosted) y apuntar `VIGIA_CERTSTREAM_URL` a ella. Sin esto, Item 5 es
un mecanismo verificado pero no una funcionalidad activa — vale la pena
resolverlo antes de vender "monitoreo continuo" como parte del pitch, o
ajustar el pitch para no prometerlo hasta que lo esté.

## Top 3 gaps de mayor prioridad (juicio propio, no solo el listado de arriba)

1. 🟡 **Persistencia de proceso / hosting real — código y config HECHOS
   (2026-07-19, Corrida 15), deploy real todavía pendiente.** Todo lo demás
   es discutible en orden, esto no: sin un despliegue que sobreviva a que
   termine la sesión, no existe "producción" en ningún sentido, solo una
   demo cada vez más elaborada. Ya no falta ningún artefacto de código —
   `Dockerfile` construido y corrido de verdad, `railway.json`/`render.yaml`
   escritos contra la documentación real de cada plataforma, ver
   `docs/despliegue.md` — pero nadie ha creado todavía la cuenta ni conectado
   el repo contra la infraestructura real de Railway/Render, porque eso es
   explícitamente responsabilidad del usuario (ver cierre de
   `docs/despliegue.md`). Sigue siendo el ítem con menor riesgo técnico de la
   lista (Railway/Render son productos maduros para exactamente este stack)
   — lo que falta ahora es una decisión y una cuenta, no más trabajo de
   ingeniería.
2. ✅ **HECHO (2026-07-19) — Tests del gate de autorización (`gate_autorizacion`).**
   `tests/test_gate_autorizacion.py`. De todo lo que podría fallar en este
   proyecto, un escaneo activo no autorizado ejecutándose por error era el
   único con consecuencias legales/éticas reales, no solo de producto — ya
   no depende 100% de que nadie rompa esa lógica sin darse cuenta en un
   refactor futuro sin que un test lo agarre primero.
3. **`ANTHROPIC_API_KEY` real antes del primer cliente de pago.** Es
   literalmente configurar una variable de entorno, cero código — pero
   sin esto, cualquier cliente real que use el producto al mismo tiempo que
   otro (o que el propio operador esté usando `claude -p` para otra cosa en
   su máquina) puede degradar o competir por la sesión CLI compartida, que
   ni siquiera está diseñada para ser multi-usuario. Es la brecha con la
   relación costo/riesgo más desbalanceada de toda la lista: resolverla
   cuesta minutos, no resolverla arriesga la experiencia del primer cliente
   real.
