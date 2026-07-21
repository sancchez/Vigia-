# Corridas en vivo del pipeline — 2026-07-15 a 2026-07-19

## Corrida 20 (2026-07-21) — Backend + frontend en un solo contenedor deployable

Objetivo del usuario: hoy desplegar en Railway significaba correr backend y
frontend como dos servicios separados — más setup, más superficie de
config, más costo (dos servicios facturables). El usuario es explícitamente
consciente del presupuesto y pidió que el deploy fuera "lo más fácil
posible": un solo servicio/contenedor que sirva ambos.

### 1. Diseño: FastAPI sirve el build de producción del frontend

`api/main.py` ganó un bloque nuevo al final del archivo (después de cada
ruta real de la API, a propósito — en Starlette/FastAPI la primera ruta
registrada que matchea gana, y el catch-all de abajo matchea *cualquier*
string, así que tiene que perder contra las rutas reales si se registra
después de todas ellas):

- `FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"`.
  Si ese directorio no existe (dev local sin build, exactamente el flujo de
  `HANDOFF.md` con `uvicorn` + `vite` como procesos separados), **no se
  registra ninguna ruta nueva** — cero cambio de comportamiento para quien
  no construyó el frontend.
- Si existe: `app.mount("/static-assets", StaticFiles(...))` para los
  JS/CSS con hash de Vite, más una ruta catch-all
  `GET /{full_path:path}` que sirve `index.html` para cualquier GET que no
  sea ni un archivo real del build ni un prefijo de ruta real de la API.

**Colisión de nombres real encontrada al diseñar esto, no en el papel:** el
default de Vite pone los JS/CSS con hash bajo `dist/assets/`. Pero `/assets`
ya es una ruta real de la API (`GET/POST /assets`, CRUD de activos del
tenant, existente desde antes de esta sesión). Montar `StaticFiles` en
`/assets` habría creado una colisión real de raíz entre archivos del
frontend y el endpoint de activos — no algo que dependiera de tener suerte
con el orden de registro de rutas. Se resolvió en la raíz cambiando
`frontend/vite.config.ts` (`build.assetsDir: 'static-assets'`) en vez de
montar ahí y confiar en que Starlette desambiguara bien cada caso.

**Los 404 reales de la API se preservan a propósito:** el catch-all revisa
si el primer segmento del path (`auth`, `assets`, `scans`, `findings`,
`reports`, `tenant`, `health`, `me`, `scan`, ...) corresponde a un prefijo
real de la API. Si sí, pero llegó hasta el catch-all (osea, ninguna ruta
real matcheó antes), se devuelve `404` JSON real en vez de servir
`index.html` — un typo de ruta de API (`GET /auth/typo`) no debe disfrazarse
de HTML válido.

**Protección contra path traversal:** el candidato se resuelve con
`Path.resolve()` y se confirma con `is_relative_to(FRONTEND_DIST)` antes de
servirlo — sin esto, un `full_path` tipo `../../etc/passwd` podría escapar
del directorio del build.

### 2. Dockerfile multi-stage: build real de Node, verificado con `docker build`

`Dockerfile` pasó de una sola etapa (`python:3.12-slim`) a dos: una etapa
`node:22-slim` (`frontend-build`) que corre `npm ci && npm run build` sobre
`frontend/`, y la etapa final de Python (sin cambios de fondo) que agrega
`COPY --from=frontend-build /app/frontend/dist ./frontend/dist`. La etapa
de Node nunca llega a la imagen final — solo su resultado.

**Construido de verdad, no solo "el Dockerfile parece correcto":**
`docker build -t vigia-combined:test .` desde la raíz del repo. Docker
Desktop se había cerrado solo entre sesiones (mismo problema ya conocido de
esta máquina, ver `HANDOFF.md`) — se relanzó manualmente
(`Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'`) y se
esperó a que `docker version` respondiera antes de intentar el build. El
build completó sin error, instaló las ~85 dependencias Python reales del
proyecto (incluyendo las nuevas de Corrida 19, `langchain-openai`/`openai`,
confirmando que este cambio de Dockerfile no rompió nada del trabajo
concurrente del otro agente de esta sesión) y produjo
`frontend/dist/static-assets/index-<hash>.js` real dentro de la imagen.

### 3. Contenedor corrido de verdad, con checks HTTP reales — no solo "el build pasó"

`docker run -d -p 48210:8000 -e JWT_SECRET=... -e CORS_ORIGINS=... vigia-combined:test`,
luego una batería de `curl` reales contra el contenedor corriendo:

| Check | Resultado real |
|---|---|
| `GET /health` | `200`, JSON con el aviso legal completo |
| `GET /` | `200`, HTML real del frontend (`<title>Vigia — panel</title>`, referencias reales a `/static-assets/index-*.js`/`.css`) |
| `GET /dashboard` (ruta de cliente de React Router, no un archivo real) | `200`, mismo `index.html` — fallback de SPA confirmado para link directo/hard-refresh |
| `GET /static-assets/index-<hash>.js` | `200`, `Content-Type: text/javascript` |
| `GET /favicon.svg` (de `frontend/public/`) | `200`, `Content-Type: image/svg+xml` |
| `GET /auth/typo-nonexistent` | `404` JSON real (`{"detail":"Not Found"}`), no `index.html` |
| `GET /scans/no-existe` sin token | `401` JSON real (`{"detail":"Not authenticated"}`) — la ruta real de la API sigue ganando sobre el catch-all |
| `GET /static-assets/no-existe.js` | `404` JSON real del propio `StaticFiles`, no el catch-all |
| `POST /auth/register` real (`Docker Test Co` / `dockertest@example.com`) | `200`, JWT real devuelto — confirma que el schema SQLite se sigue aplicando solo dentro de este contenedor combinado |
| `curl --path-as-is ".../../../../etc/passwd"` | Cayó al fallback de `index.html`, no expuso nada del filesystem |
| `curl "http://.../%2e%2e/%2e%2e/%2e%2e/etc/passwd"` (traversal URL-encoded) | Mismo resultado seguro — `index.html`, sin escape |

Contenedor e imagen de prueba (`vigia-combined-test` / `vigia-combined:test`)
detenidos y borrados al terminar (`docker stop/rm`, `docker rmi`) — no queda
nada corriendo ni ninguna imagen local de esta sesión. Un contenedor de ZAP
huérfano de una sesión anterior (`vigia-zap-<uuid>`, ya `Exited`) apareció en
`docker ps -a` durante esta verificación — no es de esta corrida, se dejó
tal cual sin tocarlo (no es parte de este trabajo, y borrarlo sin contexto
del run que lo generó no correspondía).

### 4. Docs y config de plataforma actualizados

- `frontend/vite.config.ts`: `build.assetsDir: 'static-assets'` (ver punto 1).
- `render.yaml`: se eliminó el servicio `vigia-frontend` (sitio estático
  aparte) — ahora un solo servicio (`vigia-backend`, Docker) es el deploy
  completo. `CORS_ORIGINS` ya no depende de una URL de un segundo servicio
  que resolver por dependencia circular.
- `railway.json`: sin cambios estructurales (ya era un único servicio
  basado en `Dockerfile`) — el cambio real es que ese mismo Dockerfile ahora
  produce una imagen que sirve ambas cosas.
- `docs/despliegue.md`: nueva sección "Un solo servicio, no dos" al
  principio, tabla de variables actualizada (`VITE_API_URL` y
  `CORS_ORIGINS` ya no obligatorias con el deploy combinado, con la
  explicación de por qué), y los pasos de Railway/Render simplificados a un
  solo servicio.

### 5. Regresión: suite existente + typecheck del frontend

- `py -m pytest -q`: **117 passed** (sube de "103+" porque Corrida 19,
  concurrente en esta misma sesión, agregó tests propios de selección de
  proveedor LLM — corrido aquí después de sus cambios para confirmar que
  ninguno de los dos trabajos rompió al otro).
- `npx tsc --noEmit` en `frontend/`: sin salida, sin errores.

### Lo que esto NO resuelve (documentado sin ocultar)

- El escaneo activo real (ZAP/Nuclei vía `docker` del host) sigue sin
  funcionar dentro de un deploy Railway/Render — limitación de arquitectura
  ya documentada antes de esta sesión (`docs/despliegue.md`), no cambiada
  por este trabajo.
- Nada de esto se probó desplegado de verdad en Railway o Render — solo
  local, en Docker real (mismo límite ya documentado en Corrida 15: crear
  una cuenta de hosting no es algo que un agente pueda hacer).
- El `ARG VITE_API_URL=""` por default asume que el frontend siempre se
  sirve desde el mismo origen que el backend (el escenario que este cambio
  existe para resolver). Si alguna vez se quisiera separar de nuevo
  (ej. un CDN aparte para el frontend con el backend en otro dominio), hay
  que volver a pasar `--build-arg VITE_API_URL=https://...` explícito — el
  código lo soporta, simplemente no es el default ya que el objetivo de esta
  sesión era exactamente lo contrario (un solo origen, un solo servicio).



Objetivo del usuario: `agents/_llm.py::call_claude()` solo soportaba
Anthropic (API real, o el fallback `claude -p` que **no funciona en absoluto
una vez desplegado** — Railway/Render no tienen la CLI de Claude Code
instalada ni autenticada). El usuario es consciente del presupuesto y quiso
una alternativa de LLM genuinamente barata, disponible para cuando esto se
despliegue de verdad, sin tocar código para elegirla. Mencionó OpenAI como
candidato pero dejó abierta la puerta a lo que fuera más barato de verdad.

### 1. Research de precios reales (no de memoria de entrenamiento)

Confirmado con `WebFetch`/`WebSearch` contra las páginas de pricing reales
de cada proveedor en esta sesión (2026-07-21):

- **OpenAI** (`developers.openai.com/api/docs/pricing`, redirigida desde
  `platform.openai.com/docs/pricing`): el tier `gpt-4o-mini`/`gpt-4.1-mini`
  que yo recordaba de entrenamiento **ya no existe** en la página actual —
  la línea vigente es `gpt-5.x`. El tier más barato confirmado es
  **`gpt-5.4-nano`: $0.20 / $1.25 por millón de tokens (input/output)**.
  Un escalón arriba, **`gpt-5.4-mini`: $0.75 / $4.50 por millón** — el que
  se eligió para implementar (ver justificación abajo). Cruzado con una
  búsqueda independiente (`pricepertoken.com`, `benchlm.ai`) que confirmó
  los mismos números y que el modelo `gpt-5.4-*` es real y vigente (familia
  `gpt-5.6-{sol,terra,luna}` en GA desde el 9 de julio de 2026, más caros).
- **Google Gemini** (`ai.google.dev/gemini-api/docs/pricing`): tier más
  barato real, **Gemini 2.5 Flash-Lite: $0.10 / $0.40 por millón** (input/
  output), con tier gratuito para uso bajo. Gemini 3.5 Flash-Lite (más
  reciente) es más caro (`$0.30/$2.50`) — 2.5 Flash-Lite sigue siendo el
  más barato disponible en la página de precios vigente.
- **DeepSeek** (research vía `WebSearch`, varias fuentes cruzadas):
  **DeepSeek V4 Flash: $0.14 por millón input (cache-miss), $0.0028 cache-hit,
  $0.28 por millón output** — el más barato de los tres en output.
- **Anthropic** (`claude-api` skill, tabla de modelos cacheada 2026-06-24):
  Claude Sonnet 5 (el que usaría la API real de este proyecto si
  `ANTHROPIC_API_KEY` estuviera configurada) cuesta **$3/$15 por millón**
  ($2/$10 con descuento de introducción hasta 2026-08-31) — 4-8× más caro
  que cualquiera de las tres alternativas de arriba, dependiendo del mix
  input/output real.

**Decisión: se implementó OpenAI**, no la opción más barata en el papel
(DeepSeek/Gemini Flash-Lite), por tres razones concretas, no solo "el
usuario lo mencionó primero":
1. Mismo patrón exacto que ya usa este módulo (`langchain-anthropic` →
   `langchain-openai`, ambos paquetes de LangChain de primera clase, mismo
   contrato `ChatModel.invoke()` con `SystemMessage`/`HumanMessage`) — cero
   código nuevo de integración custom, cero riesgo de un shim OpenAI-compatible
   mal hecho.
2. Calidad de salida en JSON estructurado y español more probada/documentada
   para este tipo de tarea (razonamiento de hallazgos de seguridad) que
   DeepSeek (lab más nuevo, menos historial de uso en producción para este
   caso de uso) — el propio proyecto necesita que `agents/priorizacion.py`,
   `remediacion.py`, `reporteria.py`, `cumplimiento.py` reciban texto en
   español consistente, sin mojibake (ver el bug real de Corrida 8/6
   documentado en `HANDOFF.md` para el fallback de CLI).
3. Sigue siendo 4-8× más barato que la API real de Claude — "genuinamente
   barato" no exigía perseguir el mínimo absoluto de precio, exigía dejar
   de depender de la CLI de Claude Code (que no existe en producción).

Gemini/DeepSeek quedan documentados como alternativas más baratas **no
implementadas esta sesión** — si el volumen de uso real lo justifica más
adelante, agregar un tercer proveedor a `_resolve_provider()` es una
extensión directa del mismo patrón (no un rediseño).

### 2. Diseño: capa de selección de proveedor

Leído `agents/_llm.py` completo y los 6 call sites reales de `call_claude()`
(`priorizacion.py`, `remediacion.py`, `reporteria.py`, `cumplimiento.py`,
`revision_ia.py`, `antisuplantacion.py`, `orquestador.py` — uno más de los
5 que mencionaba la tarea original). Los 7 llaman
`call_claude(system_prompt, user_message)` posicional, sin tocar `model` —
confirmado por `grep`, no supuesto — así que el cambio de firma interna no
los afecta.

UX elegida para `VIGIA_LLM_PROVIDER`: **explícita pero opcional**, con
auto-detección por key presente como fallback (`ANTHROPIC_API_KEY` gana si
ambas están configuradas, para no romper el default de nadie que ya tenía
esa key). Alternativa descartada: auto-detección pura sin variable
explícita — se descartó porque no cubre el caso real de alguien con AMBAS
keys configuradas (ej. una key de prueba de OpenAI y la key real de
Anthropic) que quiere forzar cuál se usa sin borrar ninguna de las dos.

Implementado en `agents/_llm.py`:
- `_resolve_provider()` — nueva función, la lógica de arriba.
- `_client_openai()` / `_call_via_openai_api()` — mismo patrón exacto que
  `_client()`/`_call_via_api()` ya existentes para Anthropic (mismo
  `lru_cache`, mismo manejo de contenido en bloques estructurados, mismo
  estilo de `LLMNoDisponibleError` cuando falta la key).
- `call_claude()` — ahora resuelve el proveedor primero; si es `"openai"`,
  sustituye el `model` default (que sigue siendo el id de Claude,
  `DEFAULT_MODEL`, por compatibilidad de firma) por `DEFAULT_OPENAI_MODEL`
  a menos que el llamador haya pasado un `model` explícito distinto del
  default. Comportamiento con nada configurado: IDÉNTICO al de antes de
  esta sesión (cae a `"anthropic"` → sin `ANTHROPIC_API_KEY` → fallback de
  CLI) — verificado con un test de regresión explícito, no solo por
  inspección.
- `pyproject.toml`: agregada `langchain-openai` como dependencia (ya estaba
  instalada en el entorno de desarrollo por una dependencia transitiva de
  otro paquete, pero no declarada — ahora sí, mismo estilo que
  `langchain-anthropic`).

### 3. Qué se verificó de verdad vs. qué quedó mockeado

**No se obtuvo una key real de OpenAI en esta sesión** — el proyecto no
tiene un mecanismo para crear cuentas de terceros de forma autónoma, y las
reglas de esta tarea explícitamente prohíben entrar información de pago o
crear cuentas sin supervisión directa del usuario. Esto se documenta aquí
sin ambigüedad: **no hay una llamada real de punta a punta contra la API
de OpenAI verificada en esta sesión.**

Lo que SÍ se verificó, con mocks (`tests/test_llm_provider_selection.py`,
14 tests, patrón idéntico a `tests/test_llm_cli_encoding.py`):

- `_resolve_provider()`: auto-detección con solo `ANTHROPIC_API_KEY`, solo
  `OPENAI_API_KEY`, ambas presentes (Anthropic gana), override explícito
  de `VIGIA_LLM_PROVIDER` (incluso con la key "no preferida" presente),
  case/espacios tolerados, valor inválido rechazado con error explícito.
- `call_claude()` con nada configurado: sigue cayendo al fallback de CLI
  de Anthropic exactamente como antes (regresión explícita).
- `call_claude()` con `VIGIA_LLM_PROVIDER=openai` sin `OPENAI_API_KEY`:
  falla explícito, sin fallback de CLI (a diferencia de Anthropic, OpenAI
  no tiene un fallback gratuito de CLI).
- `call_claude()` con `ChatOpenAI` reemplazado por un fake que captura los
  argumentos de construcción y los mensajes de `.invoke()`: confirma que
  usa `DEFAULT_OPENAI_MODEL` cuando no se pide un modelo explícito, que
  respeta un `model=` explícito cuando sí se pide, que arma
  `SystemMessage`/`HumanMessage` correctamente, y que maneja contenido en
  bloques estructurados igual que el camino de Anthropic.
- **Codificación en español**: el fake de OpenAI devuelve texto real con
  tildes/eñe/em-dash (`"contraseña"`, `"—"`) y el test confirma que llega
  intacto (sin la firma de mojibake `"Ã"`) — a diferencia del bug real que
  sí afectaba al fallback de CLI (`subprocess` + `cp1252` en Windows, ver
  `HANDOFF.md`), el camino de LangChain no pasa por decodificación manual
  de bytes de un subprocess, así que el riesgo de corrupción es
  estructuralmente menor aquí. Confirmado, no solo asumido.

**Lo que esto NO prueba:** que la API real de OpenAI acepte estos
parámetros exactos (`model`, `api_key`, `timeout`, `max_retries` en
`ChatOpenAI`), que el modelo `gpt-5.4-mini` responda con la calidad
esperada en español para hallazgos de seguridad reales, ni el
comportamiento real de rate-limiting/latencia. Si se consigue una key real
de OpenAI en una sesión futura, el siguiente paso concreto es repetir la
verificación de Item 1 de `HANDOFF.md` (invocar `priorizacion.py`/
`remediacion.py`/`reporteria.py` con hallazgos de ejemplo reales) pero con
`VIGIA_LLM_PROVIDER=openai`, y confirmar en vivo que el español sale
correcto también contra la red real (no solo contra el mock).

### 4. Documentación actualizada

- `docs/despliegue.md`: tabla de variables de entorno — `OPENAI_API_KEY`,
  `VIGIA_LLM_PROVIDER`, `VIGIA_OPENAI_MODEL` documentadas con el precio real
  y una estimación de costo por reporte completo (`~$0.03` con
  `gpt-5.4-mini` contra `~$0.11` con la API real de Claude Sonnet 5,
  asumiendo ~4 llamadas por ciclo de ~4.000 tokens de entrada/~1.000 de
  salida cada una — números redondos, no una medición real de tokens de
  este proyecto).
- `.env.example`: agregadas las tres variables nuevas con comentarios,
  aclarando que `VIGIA_LLM_PROVIDER` es opcional.

### 5. Tests y verificación de no-regresión

`pytest -q` completo: **117 passed** (103 según la última cifra documentada
en `HANDOFF.md`, más las que se agregaron en Corridas posteriores no
numeradas ahí + las 14 nuevas de esta sesión) — cero regresiones. Se
extendió también la fixture autouse de `tests/conftest.py`
(`_sin_llm_real_por_defecto`) para limpiar `OPENAI_API_KEY` y
`VIGIA_LLM_PROVIDER` del entorno antes de cada test, por la misma razón que
ya limpiaba `ANTHROPIC_API_KEY` — que el shell real de quien corra la suite
no vuelva no-determinista un test que no toca esas variables explícitamente.

**No se tocó `Dockerfile`** (otro agente concurrente lo estaba modificando
esta sesión para otra tarea) ni se hizo `git commit` — cambios dejados sin
commitear para que el orquestador los revise.

## Corrida 18 (2026-07-19/20) — `findings.severidad` valía "info" para ~99% de las filas reales: causa raíz, fix, backfill

Objetivo: bug confirmado por el usuario antes de empezar (no una hipótesis a
verificar): `findings.severidad` en `ciberseguridad.db` valía `"info"` para
1290 de 1298 filas reales en toda la historia del proyecto (los otros 8
casos son de `agents/antisuplantacion.py`/CertStream, que ya usan valores
hardcodeados correctos y nunca fueron parte de este bug). Dos sitios reales
en `api/main.py` leían una clave de nivel superior que ninguna herramienta
real pone ahí -- exactamente el mismo patrón de bug ya cerrado para el
campo `tipo` (ver `docs/cumplimiento.md`), ahora para `severidad`.

### Causa raíz confirmada (dos bugs distintos, no uno)

1. `api/main.py::_correr_escaneo_activo_en_background` (línea ~1186, el
   escaneo activo checkpointed añadido en Corrida 17):
   `str(hallazgo.get("riskdesc", "info")).split(" ")[0].lower()`. Estos
   hallazgos vienen de `tools/zap_api.py::get_alerts()` (`core/view/alerts/`,
   la API HTTP real de ZAP) -- **verificado contra 1280 filas reales ya en
   la DB**: ninguna trae `riskdesc`. Ese endpoint devuelve `risk` directo
   (Title Case: `"High"`/`"Medium"`/`"Low"`/`"Informational"`), un campo
   completamente distinto. El `.get("riskdesc", "info")` caía al default
   literal el 100% de las veces.
2. `api/main.py::scan()` (POST /scan, línea ~890, insertando
   `verified_findings`): `hallazgo.get("severidad") or
   hallazgo.get("severity") or "info"`. Trazado el flujo real:
   `agents/escaneo.py` arma `{"objetivo","herramienta","raw"}`,
   `agents/verificacion.py::_verificar_un_hallazgo` hace `**hallazgo` y
   agrega metadata de verificación -- nunca hay un `severidad`/`severity`
   de nivel superior en ningún punto de esa cadena. La severidad real
   siempre vivió dentro de `raw`, con nombre de campo y vocabulario propio
   de cada herramienta.

### Vocabulario real de cada herramienta, verificado contra salida real (no documentación)

No se asumió ningún nombre de campo ni vocabulario -- se verificó cada uno
corriendo la herramienta de verdad o inspeccionando `raw_json` real ya en
`ciberseguridad.db` (ver `eval/severity_fixture_samples.json` para las
muestras exactas usadas también en los tests nuevos):

| Herramienta | Campo real | Vocabulario real verificado |
|---|---|---|
| ZAP (`core/view/alerts/`, checkpointed) | `risk` | `High`/`Medium`/`Low`/`Informational` (Title Case) -- confirmado contra 1280 filas reales ya en la DB (613 Medium, 456 Low, 210 Informational, 1 High: el hallazgo real de SQL Injection, ver abajo) |
| ZAP (reporte JSON de `zap-baseline.py`/`zap-full-scan.py`, POST /scan) | `riskdesc` (`"Medium (High)"` = `"Risk (Confidence)"`), con `riskcode` numérico como respaldo | Confirmado contra las 10 filas reales de Juice Shop de `docs/cumplimiento.md`: `Medium (High)`, `Medium (Medium)`, `Low (Medium)`, `Low (Low)`, `Informational (Medium)` |
| Nuclei | `info.severity`, minúsculas | Confirmado corriendo `nuclei -jsonl` real contra Juice Shop (localhost:3051) en esta sesión: `info`, `medium` observados con esos nombres exactos (`critical`/`high`/`low`/`unknown` son el mismo schema documentado de nuclei-templates) |
| Semgrep | `extra.severity`, MAYÚSCULAS | Confirmado corriendo `semgrep --config auto --json` real contra este mismo repo: `ERROR`, `WARNING` observados (INFO es el tercer valor del enum oficial, no salió en esta corrida puntual) |
| Trivy | `Vulnerabilities[].Severity`, MAYÚSCULAS | Confirmado corriendo `trivy image` real contra `node:14`: las 5 -- `UNKNOWN`(4) `LOW`(122) `MEDIUM`(730) `HIGH`(564) `CRITICAL`(22) |
| Grype | `matches[].vulnerability.severity`, Title Case | Confirmado corriendo `grype` real contra la MISMA imagen `node:14`: las 6 -- `Unknown`(8) `Negligible`(945) `Low`(115) `Medium`(631) `High`(566) `Critical`(41) -- vocabulario DISTINTO de Trivy pese a ser dos escáneres de CVEs sobre el mismo target exacto (Grype distingue `Negligible` de `Unknown`, Trivy los colapsa en uno solo; casing distinto) |

### El fix

`tools/_shared.py::normalize_severity(raw, herramienta)` (nueva, junto a
`ToolExecutionError` y el resto de helpers compartidos): un mapeo explícito
por herramienta hacia las 5 severidades canónicas de Vigia
(`critical`/`high`/`medium`/`low`/`info` -- confirmado como el vocabulario
real esperado leyendo `db/schema.sql` CHECK constraint,
`frontend/src/components/ScanHistoryChart.tsx::SEVERITY_ORDER` y
`agents/cumplimiento.py`). Nunca devuelve `"info"` silenciosamente para un
valor no reconocido -- cae a `"medium"` (default seguro) y siempre loguea
vía `logging.getLogger("vigia.severity")`, para no repetir la clase exacta
de bug que esta función cierra. Maneja las DOS formas reales de ZAP
(`risk` directo vs. `riskdesc`/`riskcode`) en una sola función.

`api/main.py` línea ~890 y ~1186 ahora llaman a `normalize_severity` en vez
de leer una clave que nunca existe. De paso, en la línea ~1186 se encontró
y arregló un bug hermano exacto en el campo `endpoint`: `(hallazgo.get(
"instances") or [{}])[0].get("uri", "")` también asumía la forma de
reporte JSON (`instances`) sobre hallazgos que en realidad vienen de
`get_alerts()` (que trae `url` directo, sin `instances`) -- **100% de las
1280 filas de escaneo activo checkpointed tenían `endpoint` vacío en la
DB real**, confirmado antes de arreglarlo. Fix de una línea:
`hallazgo.get("url") or (hallazgo.get("instances") or [{}])[0].get("uri", "")`.

`agents/cumplimiento.py::_categorizar_hallazgo` (privado y fallback) se
revisó explícitamente: no usa `severidad` para categorizar (solo `tipo`/
`herramienta`/campos de `raw_json` vía `_texto_buscable()`), así que no
compensaba de ninguna forma la severidad rota -- no hizo falta tocarlo.

### Tests nuevos

`tests/test_severity_normalization.py` (29 casos) + `eval/severity_fixture_samples.json`
(muestras reales, no inventadas, con procedencia documentada por grupo):
un caso parametrizado por cada muestra real de las 5 herramientas (incluida
la fila real de SQL Injection de ZAP), más tests explícitos para: las dos
formas de ZAP, el respaldo por `riskcode`, que un valor no reconocido NUNCA
cae a `"info"` silencioso (cae a `"medium"` y loguea), que una herramienta
reconocida sin el campo esperado tampoco cae a `"info"`, herramienta
desconocida, y `raw=None` sin excepción.

### Backfill real contra `ciberseguridad.db`

`scripts/backfill_severidad.py` (con `--dry-run`, backup automático del
`.db` antes de escribir, excluye a propósito filas de antisuplantación/
CertStream): corrido dos veces (dry-run primero, luego real) contra el
`ciberseguridad.db` real de esta sesión.

**Antes:** `{'high': 4, 'info': 1290, 'medium': 4}` (1298 filas totales).
**Después:** `{'high': 5, 'medium': 619, 'low': 461, 'info': 213}`.
1077 filas actualizadas, 213 ya estaban correctas (severidad nativa
realmente informativa), 8 omitidas a propósito (antisuplantación/
CertStream, ya correctas por diseño). Backup real creado:
`ciberseguridad.db.bak-20260720T022602Z`.

Verificado directamente contra la fila real mencionada en el encargo: el
hallazgo de SQL Injection real de ZAP (scan
`d46c7342-5c2b-4007-92c7-1b96b58d5609`, `pluginId 40018`,
`http://host.docker.internal:3050/rest/products/search?q=%27%28`) pasó de
`severidad='info'` a `severidad='high'`, confirmado con una consulta SQL
directa post-backfill (no solo confiando en el output del script).

### Verificación final

`py -m pytest -q`: 74 tests preexistentes + 29 nuevos = **103 passed**, sin
warnings nuevos. Suite corrida antes y después del backfill (el backfill
solo toca datos, no código, pero se confirmó que la suite sigue verde de
todas formas).

### Efecto colateral positivo no pedido, mencionado para que quede anotado

Con `findings.severidad` ahora confiable, `ScanHistoryChart.tsx` (Corrida
11) va a mostrar por primera vez una distribución real de severidades en
vez de casi todo "Info" -- no se tocó ese componente (ya leía la columna
correctamente, el bug estaba 100% del lado de la escritura), pero vale la
pena que quien revise la demo sepa que el gráfico se va a ver distinto
ahora, y que eso es correcto, no una regresión visual.

## Corrida 17 (2026-07-19) — Contenedor huérfano de ZAP en timeout (Bug 1) + escaneo activo checkpointed vía la API real de ZAP (Bug 2)

Objetivo: HANDOFF.md Item 2 dejaba dos problemas reales sin resolver,
diagnosticados en Corrida 12: (1) un timeout de Python en
`run_zap_active_scan`/`run_zap_baseline` deja el contenedor Docker
huérfano corriendo indefinidamente (`--rm` no lo limpia porque el cliente
`docker run` murió, no el contenedor) y además el `tempfile.TemporaryDirectory()`
del bind-mount se borra en cuanto la excepción de timeout se propaga, así
que un reporte que el contenedor huérfano alcanzara a escribir después es
irrecuperable por diseño; (2) el escaneo activo (`POST /scan/activo`)
bloqueaba el hilo de fondo con un único `subprocess.run(..., timeout=hasta 1800s)`
sin visibilidad intermedia -- Corrida 12 documentó 4 intentos reales que
expiraron sin producir ningún hallazgo.

### Bug 1 — contenedor huérfano + reporte irrecuperable en timeout

**Fix real en `tools/scan.py::_run_zap_script`:**
- El contenedor ahora arranca con `--name vigia-zap-<scan_id o uuid>`
  explícito (antes solo `--rm`, sin forma de referenciarlo después).
- El directorio temporal ya NO usa `tempfile.TemporaryDirectory()` (que
  borra el bind-mount en cuanto la excepción de timeout se propaga) --
  ahora es `tempfile.mkdtemp()` manual, borrado explícitamente en un
  `finally` que corre DESPUÉS de haber decidido si había algo que
  recuperar.
- En `ToolTimeoutError`: se consulta el estado real del contenedor
  (`tools._shared.docker_container_running`, nuevo). Si sigue corriendo,
  se espera una ventana de gracia corta (3s, `_TIMEOUT_GRACE_SECONDS`) por
  si está terminando justo en ese momento (la carrera real que Corrida 12
  planteaba como hipótesis) y se vuelve a chequear. Si de verdad sigue
  corriendo, se fuerza `docker stop`/`docker rm`
  (`tools._shared.docker_force_remove_container`, nuevo) -- nunca queda
  huérfano -- y se relanza el `ToolTimeoutError` real. Si ya terminó
  (dentro o fuera de la gracia), el workdir sigue intacto en ese momento y
  se intenta parsear `zap-report.json` antes de borrar nada; si hay
  reporte usable, se recupera como éxito parcial (con `stderr` explicando
  que fue recuperado tras un timeout del cliente); si no, se propaga
  `ToolTimeoutError` igual -- no es un éxito silencioso con hallazgos
  vacíos.

**Tests nuevos:** `tests/test_zap_timeout_cleanup.py` (7 tests) --
deterministas, sin Docker real (mockean `run_command`,
`docker_container_running`, `docker_force_remove_container`, y colapsan
la ventana de gracia a 0s). Cubren: nombrado del contenedor con/sin
`scan_id`; timeout con contenedor genuinamente corriendo (limpia y
relanza); timeout con contenedor ya terminado pero sin reporte (sigue
siendo timeout real, no éxito falso); la carrera real -- reporte
recuperado del workdir antes de que se borre; el workdir sí se borra
después de recuperar (no queda basura); `docker_force_remove_container`
siempre se invoca en timeout.

**No se pudo verificar en vivo el camino "contenedor genuinamente huérfano
tras timeout real de 15-30 min"** dentro del presupuesto de esta sesión
(hubiera requerido repetir un ZAP real de esa duración) -- la lógica se
verificó con los mocks deterministas de arriba, que ejercitan exactamente
las mismas ramas de código (`docker_container_running`,
`docker_force_remove_container`, orden de limpieza del workdir) que un
timeout real activaría, sustituyendo solo el tiempo de espera real por uno
simulado.

### Bug 2 — escaneo activo checkpointed vía la API real de ZAP

**Investigación real, no solo lectura de docs:** se confirmó en vivo,
contra un daemon de ZAP real (`zap.sh -daemon`, misma imagen Docker, sin
el script de conveniencia `zap-full-scan.py`) y Juice Shop real, que la
API HTTP de ZAP soporta exactamente el patrón que pedía HANDOFF.md:
arrancar spider/AJAX spider/escaneo activo de forma asíncrona
(`.../action/scan/` devuelve un id de inmediato) y consultar progreso con
llamadas cortas y repetidas (`.../view/status/`). Se implementó
`tools/zap_api.py` (nuevo) con ese driver completo, y se cableó en
`api/main.py::_correr_escaneo_activo_en_background` (reemplaza la llamada
a `tools.scan.run_zap_active_scan`) con un callback `on_progress` que
persiste cada checkpoint en `scans.reporte_final` de inmediato -- `GET
/scans/{id}` ahora refleja progreso real ("[spider] Spider clásico: 56%",
"[ascan] Escaneo activo: 12%") en vez de solo `corriendo` durante 20-35
minutos seguidos.

**Hallazgo real no documentado en ningún lado obvio, costó tiempo
diagnosticar:** ZAP decide si una request HTTP entrante es "para su propia
API" comparando el header `Host` contra el puerto con el que arrancó
(`-port 8080` dentro del contenedor) -- si se llega por el puerto mapeado
del host Docker (ej. `localhost:48290`), el `Host` real no matchea y ZAP
trata la petición como un proxy a reenviar, fallando con un 502
"Connection refused" contra sí mismo. Fix: forzar `Host: localhost:8080`
en cada request a la API sin importar el puerto mapeado
(`tools/zap_api.py::_HOST_HEADER`).

**Segundo hallazgo real:** el namespace correcto de la API del AJAX Spider
es `ajaxSpider` (camelCase), NO `spiderAjax` -- ese segundo nombre, más
intuitivo dada la convención de otros endpoints, devuelve
`{"code":"no_implementor"}` y cuesta tiempo real de diagnóstico si no se
sabe de antemano.

**Verificado en vivo, de punta a punta, contra Juice Shop real y a través
del endpoint HTTP real (no solo llamando al módulo Python aislado):**
`uvicorn` real corriendo, tenant real registrado, asset `localhost`
registrado (exento de verificación de propiedad -- Corrida 16), `POST
/scan/activo` real con `ajax_spider: true, minutes: 2`, y `GET
/scans/{id}` sondeado en llamadas HTTP cortas y separadas (no una espera
continua) mostrando progreso real e incremental:

```
[iniciando] Daemon de ZAP listo.
[spider] Spider clásico: 56%
[spider] Spider clásico: 85%
[ajax_spider] AJAX Spider: running, 0 resultado(s)
[ascan] Escaneo activo: 0%
[ascan] Presupuesto de tiempo agotado -- escaneo activo detenido a mitad.
completado — Fases completadas: spider, ajax_spider, ascan. 631 hallazgo(s).
```

631 hallazgos reales confirmados persistidos en la tabla `findings` vía
`GET /scans/{id}` (antes: 0, en los 4 intentos de Corrida 12, porque el
timeout siempre llegaba primero). El contenedor daemon (`vigia-zap-<scan_id>`)
se confirmó limpiado (`docker ps -a` vacío) tanto en el camino feliz como
en un camino de error forzado.

Por separado, con llamadas directas a la API (sin pasar por Vigia) se
confirmó AJAX Spider real con evidencia de proceso (mismo patrón que
diagnosticó Corrida 12: `geckodriver` + `firefox-esr --headless` reales
dentro del contenedor, `com.crawljax` en los logs) -- crawling real
incremental de 0 a 220 resultados observado en polls sucesivos de 5s.

**Dos bugs reales encontrados y arreglados durante la propia verificación
en vivo** (no solo en el camino feliz mockeado):

1. **El reloj del presupuesto de `minutes` arrancaba antes de que el
   daemon estuviera listo.** El arranque en frío del contenedor (reinstala
   add-ons cada vez -- ver más abajo) tardó ~47-57s en un host tranquilo,
   pero **más de 120s en una corrida real bajo contención genuina de
   Docker** (varios contenedores de otros procesos concurrentes en esta
   misma sesión, el mismo tipo de contención que ya había diagnosticado
   Corrida 12) -- confirmado con `docker logs` mostrando instalación de
   add-ons todavía en curso pasados los 120s, contenedor sano, no colgado.
   Contar ese tiempo contra el presupuesto del usuario significaba que un
   `minutes=1` podía terminar con 0% de escaneo real hecho y aun así
   reportar `completado` con 0 hallazgos, engañosamente. Fix: el reloj
   (`inicio`) se captura DESPUÉS de `wait_ready()`, no antes. Cubierto por
   `tests/test_zap_checkpointed_scan.py::test_presupuesto_no_cuenta_el_tiempo_de_arranque_del_daemon`.
2. **Un único `ReadTimeout` transitorio tumbaba el escaneo activo
   completo.** Ocurrió de verdad durante el AJAX Spider (la fase más
   pesada -- levanta Firefox headless) en la primera corrida E2E real de
   esta sesión, bajo la misma contención de recursos de arriba. Antes del
   fix, un solo poll HTTP lento marcaba `estado='error'` aunque el daemon
   siguiera perfectamente vivo -- justo el tipo de fragilidad que un
   diseño "checkpointed" debería evitar. Fix: `tools/zap_api.py::_zap_get`
   reintenta hasta 2 veces con backoff corto ante
   `requests.exceptions.RequestException` antes de propagar. Cubierto por
   `tests/test_zap_checkpointed_scan.py::test_zap_get_reintenta_y_se_recupera_de_un_timeout_transitorio`
   y `test_zap_get_propaga_si_todos_los_reintentos_fallan`.

**Tests nuevos:** `tests/test_zap_checkpointed_scan.py` (9 tests) --
mockean toda la red/Docker, cubren el camino feliz (fases reportadas,
contenedor limpiado), AJAX spider opcional, limpieza del contenedor ante
cualquier excepción a mitad de camino (mismo principio que Bug 1), daemon
que nunca queda listo, los dos bugs de arriba, y los reintentos de
`_zap_get`.

**Limitación real, no resuelta esta sesión (documentada, no oculta):**
cada contenedor daemon arranca "en frío" -- el filesystem del contenedor
no trae los add-ons de ZAP (spiderAjax, selenium, webdriverlinux, retire,
...) preinstalados, así que se reinstalan en cada escaneo nuevo (mismo
costo que ya pagaba el enfoque anterior con `zap-full-scan.py`, no es un
costo nuevo de este módulo, pero tampoco se mejoró). Un volumen Docker
persistente montado en `/home/zap/.ZAP` eliminaría casi todo ese costo a
partir del segundo escaneo -- se dejó fuera de esta sesión porque
compartir ese volumen entre escaneos concurrentes de tenants distintos sin
diseñarlo con cuidado podría filtrar contexto/sesión de un escaneo a otro,
y el tiempo de esta sesión se priorizó en verificar el flujo checkpointed
de punta a punta contra un daemon real.

**Limitación real de arquitectura, también documentada sin resolver:**
esto es "checkpointed" en el sentido de que el progreso se consulta con
llamadas HTTP cortas y se persiste en la DB después de cada una -- pero si
el proceso de la API de Vigia se cae a mitad de un escaneo, el escaneo en
sí no se puede "retomar" desde cero tras un reinicio (el contenedor
quedaría huérfano, aunque las mismas utilidades de limpieza de Bug 1 lo
cubrirían en el próximo timeout que lo detecte). Resumibilidad real
across-restart necesitaría persistir `container_name`/`api_base_url`/id de
scan de ZAP en la fila `scans` y un job de reconciliación al arrancar --
extensión razonable, fuera de alcance de esta sesión.

**Cleanup:** todos los contenedores Docker de esta corrida (Juice Shop,
varios daemons ZAP de prueba, el daemon de la corrida E2E real) se
detuvieron y removieron (`docker rm -f`) al terminar -- `docker ps -a`
solo muestra el contenedor `buildx_buildkit_desktop-linux` de Docker
Desktop, no de esta corrida. Docker Desktop se cerró solo una vez a mitad
de esta sesión (mismo problema ya documentado en HANDOFF.md) y se
relanzó manualmente sin pérdida de progreso.

**Suite de tests:** 74/74 pasan (58 preexistentes al empezar esta corrida
+ 16 nuevos de esta sesión: 7 de Bug 1, 9 de Bug 2), `ruff check .` limpio.

## Corrida 16 (2026-07-19) — Verificación real de propiedad de dominio, cierra un gap de abuso real en `POST /assets`

Objetivo: `POST /assets` dejaba que cualquier tenant autenticado registrara
CUALQUIER dominio como propio -- incluido el de un tercero real, ej.
`microsoft.com` -- sin ninguna prueba de control, y ese registro por sí
solo bastaba para que `_tenant_tiene_asset_para_target()` (Corrida 12) lo
tratara como "autorizado" para `POST /scan/activo`. `POST /scan` no tenía
NI SIQUIERA ese chequeo de propiedad. Es tanto un gap de abuso real de
producción como la causa raíz de un near-miss real de esta sesión (un
agente casi confundió "el activo está registrado" con "está autorizado").

### Diseño elegido

Patrón estándar de la industria (Google Search Console, Detectify, etc.):
token único por asset + prueba de control real vía **DNS TXT**
(`_vigia-challenge.<dominio>`) o **archivo bien-conocido**
(`https://<dominio>/.well-known/vigia-verification.txt`). Se implementaron
**los dos métodos de verdad** (no solo uno -- había margen de tiempo), en
`tools/asset_verification.py` (nuevo, ~230 líneas con el razonamiento
completo en el docstring del módulo):

- DNS vía `dnspython` (`dns.resolver`) -- ya era una dependencia transitiva
  real de este proyecto (`dnstwist` la usa internamente), ahora declarada
  explícita en `pyproject.toml` porque este módulo la importa directo.
- HTTP vía `urllib.request` de la librería estándar -- cero dependencias
  nuevas para ese camino.

**Excepción de localhost/IP privada** (punto 5 de la asignación de esta
sesión, pensado explícitamente, no un hardcode): un asset cuyo host/IP
literal declarado es `localhost` o cae en un rango privado/loopback/
link-local (RFC 1918/4193/3927, vía `ipaddress.ip_address(...).is_private/
is_loopback/is_link_local` de la librería estándar, no una lista hecha a
mano) queda exento de verificación. Razón: escanear "localhost" desde un
proceso dado siempre apunta al proceso de quien ejecuta el escaneo, nunca
al de un tercero -- no hay víctima posible. La exención se decide sobre el
string LITERAL de `assets.valor`, nunca sobre una resolución DNS en vivo
del nombre -- si se resolviera en el momento de decidir, un atacante podría
usar DNS rebinding (apuntar temporalmente un dominio público a una IP
privada) para colarse por la excepción. Un dominio sintético "que suena a
prueba" (`miempresatest.com`, usado en corridas anteriores de este mismo
proyecto) NO se exime -- sigue necesitando verificación real, que es
justo el caso que esta corrida NO pudo demostrar en modo "éxito real" (ver
"Qué quedó mockeado" más abajo).

### Cambios de DB

`db/schema.sql`: 4 columnas nuevas en `assets` (`verificado INTEGER NOT
NULL DEFAULT 0`, `verification_token TEXT`, `verification_method TEXT
CHECK(... IN ('dns_txt','http_file') OR IS NULL)`, `verified_at
TIMESTAMP`). Primera vez que este proyecto necesita agregar columnas a una
tabla YA EXISTENTE de una sesión anterior (a diferencia de
`invitations`/`subscriptions`, que se sumaron como tablas nuevas completas)
-- esto expuso un hallazgo real de portabilidad: `ALTER TABLE ... ADD
COLUMN IF NOT EXISTS` es sintaxis válida en Postgres (>= 9.6) pero
**rechazada por el parser de SQLite** (confirmado empíricamente contra la
versión real empaquetada con este Python, 3.50.4 -- `sqlite3.OperationalError:
near "EXISTS": syntax error`, no una falla silenciosa). Se resolvió
poniendo las columnas nuevas directo en el `CREATE TABLE IF NOT EXISTS
assets` (cubre bases de datos nuevas) y agregando una reconciliación
explícita en `db/connection.py` para bases de datos ya existentes:
`_ensure_sqlite_asset_verification_columns()` (chequea `PRAGMA
table_info(assets)` antes de cada `ALTER TABLE ADD COLUMN`, sin `IF NOT
EXISTS`) para SQLite, y el mismo `ADD COLUMN IF NOT EXISTS` nativo de
Postgres reutilizado dentro de `_get_pg_conn()` (que sí lo soporta). Ambos
caminos probados de verdad: una DB SQLite nueva, y una DB SQLite sintética
creada a mano SIN estas columnas (simulando una `ciberseguridad.db` real de
sesión anterior) -- ambas terminan con las 4 columnas, sin error en
conexiones repetidas (idempotencia confirmada con dos `get_conn()`
consecutivos).

### Endpoints nuevos/cambiados en `api/main.py`

- `POST /assets` ahora genera un `verification_token` real al crear
  cualquier asset (incluso uno exento -- barato, y deja la puerta abierta a
  que un asset hoy exento se reapunte a un dominio público real después).
  `AssetOut` incluye `verificado`, `verification_method`, `verified_at`,
  `exento_de_verificacion` (calculado), e `instrucciones_verificacion`
  (nombre de TXT + valor, URL + contenido del archivo -- para que el
  frontend no tenga que reconstruir nada).
- `POST /assets/{id}/verify` (nuevo) -- ejecuta de verdad la comprobación
  DNS o HTTP pedida (`{"metodo": "dns_txt"|"http_file"}`) y marca
  `verificado=1` si pasa. Idempotente/reintentable. 404 si el asset no es
  de ese tenant, 422 si es tipo `ip` o método inválido, 400 si el asset ya
  está exento (no hace falta verificarlo).
- `_tenant_tiene_asset_para_target()` (Corrida 12) se refactorizó: la
  búsqueda del asset que hace match se extrajo a
  `_buscar_asset_para_target()` (devuelve la fila completa, no solo
  `bool`), y `_tenant_tiene_asset_para_target()` sigue siendo, a propósito,
  SOLO la pregunta de propiedad (sin tocar verificación) para no romper su
  contrato ni los tests que ya la ejercitaban como tal
  (`tests/test_scan_activo_asset_gate.py`, sin cambios en esa parte). La
  pregunta completa nueva vive en `_asset_verificado_para_target()`
  (propiedad Y (verificado O exento)), con un mensaje de error que le dice
  al tenant exactamente qué hacer (nombre del TXT, URL del archivo,
  endpoint a llamar).
- `POST /scan/activo` cambia su chequeo viejo (solo-ownership) por
  `_asset_verificado_para_target()`.
- `POST /scan` **gana el chequeo por primera vez** -- antes de esta sesión
  no verificaba NADA sobre el `target` más allá de la forma del payload; el
  bloqueo de Escaneo Activo (`gate_autorizacion`) seguía intacto, pero
  recon pasivo y anti-suplantación corrían contra cualquier string. Ahora
  rechaza con 403 antes de invocar el grafo si `target` no es un asset
  verificado (o exento) del tenant -- evita también el costo real de
  llamadas a Claude/subfinder/amass contra un target no autorizado.

### Antisuplantación / monitoreo continuo también gateados

- `api/certstream_listener.py::_build_variant_map()` ahora solo construye
  variantes dnstwist para activos `verificado=True` o exentos -- antes, un
  tenant podía registrar el dominio de un tercero real y CertStream
  vigilaría activamente variantes de ese dominio ajeno en su nombre.
- `api/scheduler.py::run_scan_cycle_once()` (el ciclo recurrente de recon
  PASIVO, cada `VIGIA_SCAN_INTERVAL_HOURS`) también se gateó -- no estaba
  en la lista explícita de la asignación de esta sesión, pero es el mismo
  root cause exacto (activo no verificado recibiendo tratamiento como si lo
  estuviera) y el cambio fue de bajo riesgo, así que se cerró igual.
  `run_scan_cycle_once()` ahora devuelve solo el conteo de activos
  PROCESADOS (antes devolvía el total sin filtrar).

### Frontend (`frontend/src/pages/Dashboard.tsx`, `frontend/src/api.ts`)

Funcional, no pulido (a propósito, según la asignación): cada activo
muestra un chip -- "exento (local)" / "verificado (método)" / "sin
verificar" -- y el botón "Escanear ahora" se deshabilita cuando ninguno de
los dos aplica. Un botón "Verificar propiedad" expande un panel con las
instrucciones exactas (nombre+valor del TXT, URL+contenido del archivo) y
un selector + botón "Comprobar ahora" que llama a `POST
/assets/{id}/verify` de verdad. `npx tsc -b` corrido y limpio (exit 0) --
no se levantó el dev server real esta sesión por tiempo, pero el
type-check cubre errores de forma/props que romperían el build.

### Tests (`tests/test_asset_verification.py`, nuevo -- 26 casos + 1 test
existente actualizado en `tests/test_scan_activo_asset_gate.py`)

Cuatro niveles: (1) unidad pura sobre `tools/asset_verification.py`
(exención localhost/IP privada, IP pública NO exenta, dominio sintético NO
exento, generación de token, instrucciones); (2) integración sobre `POST
/assets`/`POST /assets/{id}/verify` reales vía `TestClient` con la
comprobación DNS/HTTP MOCKEADA (documentado explícitamente -- no
controlamos ningún dominio real para escribirle un TXT válido); (3)
integración sobre el gate nuevo de `POST /scan`/`POST /scan/activo`
(bloquea sin verificar, permite verificado, permite exento, aislamiento
multi-tenant); (4) **DNS real, sin mock**, contra `google.com` (dominio
público real que no controlamos) -- confirma que `verificar_dns_txt` hace
una resolución DNS de verdad contra la red real (no simulada) y decodifica
una respuesta real, aunque solo puede demostrar el camino "no encontrado"
(no tenemos forma de escribirle un TXT real a un dominio que no
controlamos); el camino de éxito se cubre aparte con
`dns.resolver.Resolver` mockeado explícitamente, documentado como tal en
el docstring del test. Mismo patrón para `verificar_http_well_known`
(GET real sin mock contra `google.com`, 404 esperado).

**Hallazgo real durante los tests, no cosmético:** un test nuevo usó
`203.0.113.7` como "ejemplo de IP pública" (copiando el mismo valor que ya
usaba `test_scan_activo_asset_gate.py` para un caso no relacionado con
exención) y falló -- `ipaddress.ip_address("203.0.113.7").is_private` es
`True` en Python, porque ese rango es TEST-NET-3 (RFC 5737, reservado para
documentación, no globalmente enrutable). Corregido usando `8.8.8.8` y
`1.1.1.1` (resolutores DNS públicos reales) para ese caso -- documentado en
el test para que no se repita la confusión.

Suite completa: **67 tests pasan** (41 ya existentes sin cambios de
comportamiento + 1 actualizado para reflejar el nuevo gate + 26 nuevos),
`py -m pytest -q` verde contra SQLite (default).

**Verificado también contra Postgres 16 real** (no quedó como pendiente):
se levantó un contenedor `postgres:16-alpine` descartable
(`docker run --name vigia-verif-pgtest ...`, puerto 48293 para no chocar
con el contenedor ZAP que otra tarea en paralelo tenía corriendo en esta
misma sesión), y con `VIGIA_PG_TEST_URL` apuntando a él se corrió primero
`tests/test_asset_verification.py` + `tests/test_scan_activo_asset_gate.py`
(35 tests, verde) y luego la suite COMPLETA (67 tests, verde,
`145.18s`) -- confirma en vivo que `_ensure_sqlite_asset_verification_columns()`
no rompe nada del lado Postgres (esa función ni se ejecuta ahí, ver
`db/connection.py`) y que el `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
nativo agregado a `_get_pg_conn()` para reconciliar una tabla `assets`
Postgres preexistente corre sin error real, no solo "debería funcionar
porque es sintaxis estándar de Postgres >= 9.6". Contenedor de prueba
detenido y eliminado (`docker rm -f`) al terminar, sin tocar
`.env.example` ni ninguna instancia real de producción.

### Qué quedó mockeado vs. qué es real -- resumen honesto

- **Real:** resolución DNS TXT contra la red real (`dns.resolver`, probado
  contra `google.com` en vivo); petición HTTP GET real (`urllib.request`,
  también contra `google.com`); toda la lógica de exención
  (`ipaddress` de la librería estándar, sin mocks en ningún test); el
  endpoint `POST /assets/{id}/verify` completo (lookup, update SQL,
  respuesta) corre real en los tests de integración; el gate en `POST
  /scan`/`POST /scan/activo`/CertStream/scheduler corre real, sin mockear
  la lógica de autorización en sí.
- **Mockeado (documentado explícitamente en cada test):** el RESULTADO de
  `verificar_asset()` en los tests de integración que necesitan un asset
  "ya verificado" para probar el resto del flujo -- porque los dominios de
  prueba (`dominiopublico.test`, `midominiolegitimo.test`, etc.) no pueden
  resolver de verdad a un TXT/archivo que no controlamos. El camino de
  éxito de `verificar_dns_txt` en sí (parseo de una respuesta TXT real que
  SÍ contiene el token) también se mockea aparte, por la misma razón.
- **No probado en esta sesión:** una verificación end-to-end contra un
  dominio real que el operador de Vigia controle de verdad (comprar/usar un
  dominio propio, escribirle el TXT real, y verificarlo por HTTP contra la
  API real) -- requeriría acceso a un registrador de dominios o un dominio
  ya existente del usuario, fuera de lo que un agente puede hacer sin ese
  acceso. Próximo paso concreto si se quiere cerrar el último tramo de
  "probado en vivo, no solo en tests": pedirle al usuario un dominio de
  prueba real (puede ser un subdominio barato) y correr el flujo completo
  una vez con él.

### Hallazgo real de infraestructura de tests, encontrado y arreglado en el camino

Correr la suite completa varias veces seguidas (necesario para confirmar
que los tests nuevos no rompían nada, y que no chocaban con el trabajo
concurrente de otra tarea sobre `POST /scan/activo` en esta misma sesión,
ver más abajo) expuso una intermitencia real: un lote de 5 tests falló de
forma NO determinista (repartidos entre `test_scan_activo_asset_gate.py` y
`test_asset_verification.py`, sin relación temática aparente entre sí) en
una corrida completa, y pasaron limpio al re-correr la suite completa o
cada archivo por separado -- la firma clásica de una condición de carrera
entre tests, no un bug de lógica de producto.

Diagnóstico: `POST /scan/activo` arranca un `threading.Thread` real
(`daemon=True`) que sigue vivo después de que el response HTTP 202 ya
volvió. Los tests que ejercitan el camino "sí se llama al escaneo" solo
esperaban a que el MOCK del escaneo se hubiera invocado (`llamado["zap"] is
True`), no a que el thread completo terminara -- pero ese thread, tras
llamar al mock, todavía tiene que hacer su propio `get_conn()` + `UPDATE
scans` + insertar findings. Si el test retorna en ese punto, `pytest`
empieza a desmontar sus fixtures (`monkeypatch` revierte `DATABASE_URL`) y
arranca el siguiente test (que setea su propio `DATABASE_URL` a un archivo
SQLite temporal distinto) casi de inmediato -- si el thread zombie hace su
`get_conn()` en esa ventana, termina operando contra la base de datos
equivocada (la del test siguiente, o el `DATABASE_URL` sin setear que cae
al default `./ciberseguridad.db`).

Arreglo real, no un `sleep` más largo: se agregó `_esperar_scan_terminado()`
en ambos archivos de test afectados que sondea `GET /scans/{id}` hasta que
`estado` deja de ser `'corriendo'` -- esa transición de estado es
precisamente la última escritura que hace el thread, así que esperarla
garantiza que el thread ya terminó del todo antes de que el test retorne.
Aplicado a los tests de la suite que arrancan el thread real con un mock
que sí "tiene éxito" (los que esperan un mock que falla/nunca se llama no
lo necesitaban, ya cortan en 403 antes de que exista un thread). Confirmado
con 3 corridas limpias consecutivas de la suite completa después del fix.

## Corrida 15 (2026-07-19) — Empaquetado y config de hosting: Dockerfile, Railway/Render, demo de un comando, seed de datos reales

Objetivo: cerrar el bloqueador #1 de `docs/produccion-readiness.md`
("Persistencia de proceso") en la medida en que código/config pueden
cerrarlo. Todo lo construido en las 14 corridas anteriores estaba probado en
vivo pero nunca desplegado — cada sesión levantaba todo a mano y todo moría
al terminar. Esta corrida no despliega nada contra infraestructura real
(crear cuentas de hosting está explícitamente fuera de lo que un agente
puede hacer) — construye y **verifica localmente** todo lo que hace que ese
despliegue, cuando el usuario lo haga, sea "un paso conocido" en vez de una
incógnita. Detalle completo del razonamiento y los pasos para el usuario en
`docs/despliegue.md` (nuevo).

### Dockerfile del backend — construido y corrido de verdad, no solo "el build pasó"

`Dockerfile` (raíz del repo, nuevo) + `.dockerignore` (nuevo). `python:3.12-slim`,
`pip install .` desde `pyproject.toml`, `CMD uvicorn api.main:app --host 0.0.0.0
--port ${PORT:-8000}` (forma shell a propósito, para que `${PORT:-8000}` se
expanda -- Railway/Render inyectan `$PORT` en runtime).

Primer intento de build (`docker build -t vigia-backend:test .`) terminó
"exitosamente" pero **la imagen no aparecía en `docker images`** -- hallazgo
real, no cosmético: este Docker Desktop usa el driver `docker-container` de
buildx por default, que solo deja el resultado en el build cache a menos que
se pase `--load` (o `--push`). `docker build --load -t vigia-backend:test .`
sí la cargó localmente -- confirmado con `docker images`. Sin este detalle,
alguien probando "¿el Dockerfile funciona?" con el comando obvio se hubiera
llevado un falso positivo (build "exitoso", imagen inexistente).

Con la imagen cargada: `docker run -d --name vigia-backend-test -p 48199:8000
-e JWT_SECRET=... vigia-backend:test`, contenedor arriba en segundos.
`curl http://localhost:48199/health` -> `200`, JSON real de la app (no un
stub). `docker logs` confirmó el patrón de degradación esperado sin cambios:
`"Paquete 'certstream' no instalado -- listener de CertStream deshabilitado
... El resto de la API sigue funcionando normal."` -- el mismo comportamiento
ya documentado en Corrida 13, ahora confirmado también dentro de un
contenedor limpio construido desde cero.

**Verificación más importante: el schema se aplica solo, sin ningún paso de
migración manual.** `POST /auth/register` real contra el contenedor recién
levantado (SQLite fresco, archivo que no existía hasta ese momento) devolvió
un JWT válido en el primer intento -- confirma que
`db/connection.py::get_conn()` (ya idempotente desde la migración a Postgres
de Corrida 14) no necesita ningún `ENTRYPOINT` ni comando de migración
aparte antes de `uvicorn`. Contenedor e imagen de prueba se detuvieron y
borraron al terminar (`docker stop/rm vigia-backend-test`,
`docker rmi vigia-backend:test`) -- confirmado con `docker images` que no
queda nada.

### railway.json y render.yaml — escritos contra la documentación real de cada plataforma, no adivinados

Antes de escribir cualquiera de los dos archivos, se hizo `WebFetch` real
contra `docs.railway.com/reference/config-as-code` y
`render.com/docs/blueprint-spec` para confirmar el schema JSON/YAML actual
(el pedido explícito de esta tarea era no inventar formato de config de una
plataforma cuya interfaz real no se había revisado). Hallazgos concretos que
cambiaron lo que se escribió:

- Railway: `build.builder: "DOCKERFILE"` + `build.dockerfilePath` es el campo
  real para construir desde un Dockerfile (no Nixpacks/Railpack por
  default); `deploy.healthcheckPath` + `deploy.healthcheckTimeout` son los
  campos reales para que Railway espere una respuesta antes de marcar el
  deploy como listo -- mismo principio que `scripts/demo.ps1` usa en local.
- Render: el campo moderno es `runtime` (`docker`/`static`), no `env` (la
  doc real lo marca como desalentado). `dockerfilePath`, `healthCheckPath`,
  y la estructura real de `envVars` con `sync: false` (para que Render pida
  el valor en el asistente de creación del Blueprint en vez de committearlo)
  confirmados contra la doc real, no supuestos.
- `WebSearch` adicional (no solo la doc de referencia) confirmó dos datos
  concretos que van directo al texto de `docs/despliegue.md` y a los
  comentarios de `render.yaml`: los Web Services free de Render tienen
  filesystem efímero (un SQLite local se pierde en cada
  redeploy/restart/spin-down) y los Persistent Disks de Render **no están
  disponibles en el plan free**; el Postgres free de Render expira a los 30
  días (14 de gracia para pasar a pago). Ninguno de los dos era un dato que
  se pudiera adivinar razonablemente -- son políticas de negocio de Render,
  no arquitectura.

Ninguno de los dos archivos se probó desplegando de verdad (eso requeriría
crear una cuenta) -- lo que sí está verificado es que ambos apuntan al mismo
`Dockerfile` que se construyó y corrió de verdad arriba.

### scripts/demo.ps1 — corrido de punta a punta, no solo escrito

PowerShell (no bash) porque esta máquina es Windows. Reemplaza los 4
comandos manuales en 2 terminales de `HANDOFF.md` ("Cómo levantar todo").
Verifica `docker ps` primero (warn sin fallar si no responde -- ZAP/Nuclei
ya degradan con gracia sin Docker, confirmado leyendo `tools/_shared.py`
antes de escribir esto), arranca backend y frontend como `Start-Job` de
PowerShell, y espera una respuesta HTTP real de `/health` (backend) y de la
raíz (frontend) antes de declarar éxito -- nunca un `Start-Sleep` fijo a
ciegas.

Corrido de verdad (`./scripts/demo.ps1 -SkipBrowser`): detectó Docker Desktop
corriendo, arrancó ambos jobs, esperó las respuestas reales (backend listo a
los ~6s, frontend a los ~1s), y reportó ambas URLs. Bug cosmético real
encontrado y arreglado en el camino: los mensajes con tildes (`respondió`,
`está`) se mostraban como mojibake (`respondiÃ³`) en la consola de
PowerShell 5.1 de esta máquina por un choque de codificación entre el
archivo (UTF-8 sin BOM) y la code page de la consola -- se quitaron las
tildes de los `Write-Host` (no de los comentarios) para que el output sea
legible en cualquier consola Windows sin depender de configurar `chcp`
primero, relevante porque este script se usa en vivo frente a un prospecto.
`Get-Job | Stop-Job; Get-Job | Remove-Job` confirmó que los puertos quedan
libres al terminar (`curl` a ambos puertos después devolvió conexión
rechazada, como se esperaba).

### scripts/seed_demo.py — probado con datos reales, tres veces

Reutiliza `db.connection.get_conn()` (el mismo acceso a datos que la API
real, no un script paralelo) y `auth.jwt_auth.get_password_hash` -- crea el
tenant `Vigia Demo` (`demo@vigia.local` / `DemoVigia2026`), 3 assets
(`demo-tienda.co`, `app.demo-tienda.co`, `api.demo-tienda.co`), y un scan
"completado" con los 10 hallazgos reales de ZAP baseline contra Juice Shop
que ya vivían congelados en `eval/cumplimiento_fixture_juiceshop.json`
(mismo fixture que usa `tests/test_cumplimiento_categorizacion.py`) -- se
reutilizó ese fixture en vez de inventar hallazgos de demo nuevos, porque el
propio pedido de esta tarea era honestidad de datos ("reusar los hallazgos
reales ya capturados"), y el scan de demo lo documenta explícitamente en su
`reporte_final` (menciona que el target real fue Juice Shop, no
`demo-tienda.co`, para no disfrazar el origen de los datos).

Probado tres veces contra una base SQLite descartable real (no
`ciberseguridad.db` de desarrollo):
1. Primera corrida: crea tenant/usuario/assets/scan/findings, confirma por
   stdout.
2. Segunda corrida (mismo `DATABASE_URL`): detecta que el tenant demo ya
   existe y no hace nada -- idempotente, no duplica.
3. Corrida con `--reset`: borra el tenant demo (y todo lo colgado vía
   `ON DELETE CASCADE` del schema) y lo recrea de cero.

Verificación final, la más importante: un `uvicorn` real (`Start-Job` de
PowerShell) apuntando a esa base sembrada, `POST /auth/login` real con
`demo@vigia.local`/`DemoVigia2026` devolvió un JWT válido, `GET /findings`
con ese JWT devolvió exactamente 10 hallazgos, `GET /assets` devolvió los 3
assets sembrados -- no se asumió que los datos estaban bien solo porque el
script no tiró error, se confirmó leyéndolos de vuelta por HTTP real.

Bug real encontrado y arreglado en el camino: la primera forma de pasar
`DATABASE_URL` en Git Bash (`sqlite:///$(pwd)/archivo.db`, con `pwd` devolviendo
una ruta estilo POSIX `/d/freestyle/...`) hacía que `sqlite3.connect()`
fallara con `unable to open database file` -- no es un bug del script ni de
`db/connection.py`, es una discrepancia real de formato de ruta entre Git
Bash y Windows. Se resolvió usando una ruta relativa
(`sqlite:///./archivo.db`, mismo formato que ya usa `.env.example`), que
funciona igual en ambos entornos.

### docs/despliegue.md (nuevo)

Guía completa en español, mismo estilo que `docs/cumplimiento.md`: qué se
construyó y verificó en esta sesión, tabla de referencia completa de
variables de entorno (incluyendo `VIGIA_CERTSTREAM_URL` y las otras dos
variables de CertStream que `docs/produccion-readiness.md` ya señalaba como
faltantes en `.env.example` -- ese archivo sigue sin tocarse, fuera de
alcance explícito de esta tarea), pasos numerados concretos para Railway y
para Render (cuenta, repo, variables, decisión de persistencia de datos), la
brecha real y sin cerrar de CertStream self-hosted en producción (el paquete
Python `certstream` no está en `pyproject.toml` -- se descubrió leyendo el
log real del contenedor de prueba: el listener se deshabilitó solo con un
mensaje claro, confirmando que el código ya maneja esto con gracia, pero
alguien que quiera el feature vivo en producción necesita agregar esa
dependencia, no se hizo en esta sesión por estar fuera de las 5 tareas
pedidas), y una sección explícita de qué NINGÚN agente de esta sesión hizo
ni puede hacer (crear cuentas, conectar repos, pagar planes).

### Qué queda para el usuario, sin ambigüedad

Ninguna cuenta de Railway/Render fue creada, ningún repo fue conectado a
ninguna plataforma, y no se introdujo información de pago en ningún lado --
ver el límite explícito de esta tarea. `docs/despliegue.md` tiene los pasos
numerados exactos para que el usuario lo haga él mismo cuando decida. La
suite de pytest (`pytest -q`) se corrió de nuevo al final de esta corrida
para confirmar que nada de este trabajo (que no tocó código de aplicación,
solo empaquetado/config/scripts nuevos) rompió nada: 44 passed, igual que al
principio.

## Corrida 14 (2026-07-19) — Migración SQLite → Postgres, verificada contra Postgres 16 real

Objetivo: `docs/produccion-readiness.md` sección 2 llevaba tiempo documentando que
`db/schema.sql` estaba "escrito en un dialecto portable a Postgres a propósito" y que migrar
sería "un cambio localizado a `db/connection.py`" — pero era una suposición de diseño nunca
verificada contra un Postgres real. Esta corrida lo verificó de punta a punta: Postgres 16 real
vía Docker, backend nuevo en `db/connection.py`, el propio `db/schema.sql` corrido contra él sin
tocarlo, la suite de pytest completa corriendo contra ambos motores, y un smoke E2E por HTTP real
contra un server `uvicorn` real apuntando a ese Postgres real.

### Setup real usado

```bash
docker run -d --name vigia-pg-test -e POSTGRES_PASSWORD=vigia_test_pw -e POSTGRES_DB=vigia -p 48189:5432 postgres:16
```

Puerto `48189` elegido por no chocar con ninguno de los ya documentados en `HANDOFF.md`/
`eval/live_run_report.md` (48173/48174/48178-48183/48189 estaba libre). `psycopg[binary]>=3.2`
(psycopg3 moderno, no psycopg2, no SQLAlchemy/ORM — el proyecto sigue usando SQL crudo vía
`conn.execute()`/`dict_from_row()`) agregado a `pyproject.toml`.

### Hallazgo real #1: el schema SÍ es portable, verificado, no asumido

`docker cp db/schema.sql` + `psql -f schema.sql` contra el Postgres real de arriba corrió
**sin ningún error, primera vez, sin tocar una sola línea** — `CREATE TABLE`/`CREATE INDEX IF
NOT EXISTS` x7, todos los `CHECK(... IN (...))`, todos los `REFERENCES ... ON DELETE CASCADE`,
todos los `TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP`. Un segundo `psql -f schema.sql` (mismo
schema, misma DB) confirmó que también es idempotente en Postgres (`NOTICE: relation ... already
exists, skipping`, cero errores) — igual que ya lo era en SQLite vía `executescript()`. La razón
de que esto funcionara sin fricción: el schema nunca usó nada realmente SQLite-específico
(`AUTOINCREMENT` no aparece — los IDs son UUID generados en Python — y `TEXT`/`INTEGER`/
`TIMESTAMP`/`CURRENT_TIMESTAMP` son válidos en ambos motores). El docstring de
`db/connection.py` llevaba razón; ahora está confirmado, no solo declarado.

### Hallazgo real #2: lo que sí era genuinamente no portable — no era el schema, era el mapeo Python

Con el backend nuevo (`_PgConnection` traduciendo `?`→`%s`, `psycopg.rows.dict_row` como
`row_factory`) la suite de pytest completa (`VIGIA_PG_TEST_URL=postgresql://... pytest -q`)
falló 3 de 44 tests con `pydantic_core.ValidationError: Input should be a valid string` sobre
campos `created_at`/`completed_at`. Causa real: `sqlite3` (sin `detect_types`) siempre devolvió
las columnas `TIMESTAMP` como `str` crudo, y varios endpoints de `api/main.py` (`GET /scans`,
`GET /findings`) construyen sus modelos Pydantic (`created_at: str`) leyendo `row["created_at"]`
directo — nunca pasan por `dict_from_row()`. `psycopg`, en cambio, parsea `TIMESTAMP` a
`datetime.datetime` real (más "correcto" en abstracto, pero rompe esos modelos). Un segundo
detalle relacionado: `CURRENT_TIMESTAMP` en Postgres incluye microsegundos por defecto
(`2026-07-19 15:36:19.598042`), sqlite no.

**Arreglo, localizado a `db/connection.py` como pedía el diseño original:** en vez de tocar cada
endpoint o cambiar el tipo de cada campo Pydantic (que hubiera dejado de ser un cambio
localizado), `_get_pg_conn()` registra un loader custom de psycopg (`_TimestampStrLoader`,
`psycopg.adapt.Loader`) para los tipos `timestamp`/`timestamptz` que decodifica el valor crudo a
`str` y trunca a segundos — mismo tipo Python y mismo formato de string que sqlite3 ya devolvía
siempre. Con el loader puesto, los mismos 3 tests pasan igual que en SQLite.

### Verificación real ejecutada (no solo "compila")

1. **`pytest -q` contra SQLite (default, sin `DATABASE_URL`)**: 44 passed — sin regresión.
2. **`pytest -q` contra Postgres real** (`VIGIA_PG_TEST_URL=postgresql://postgres:vigia_test_pw@localhost:48189/vigia`,
   override nuevo en `tests/conftest.py::test_db` — ver sección de diseño abajo): 44 passed,
   mismos tests, mismo código de producción, motor real distinto.
3. **Smoke E2E por HTTP real, no `TestClient`:** servidor `uvicorn api.main:app` real levantado
   en el puerto `48195` con `DATABASE_URL` apuntando al Postgres real de arriba. Script en
   `scratchpad` (no versionado, ver nota abajo) hizo, todo por HTTP real (`requests`, no ASGI
   in-process): `POST /auth/register` para dos tenants reales, `POST /assets` real para cada uno,
   un `INSERT` real de `scans`+`findings` por tenant (mismo patrón de `conn.execute()` que usa
   `POST /scan/activo` en producción — correr ZAP/Nuclei de verdad para esto habría sido
   desproporcionado, ya está verificado en corridas anteriores), y `GET /findings`/`GET /scans`
   reales confirmando: cada tenant ve su propio finding, **ningún tenant ve el finding del otro**
   (aislamiento multi-tenant confirmado contra Postgres real, no solo contra SQLite), y
   `created_at`/`completed_at` llegan como string limpio (`"2026-07-19 15:45:10"`, sin
   microsegundos) gracias al loader custom.
4. **Contenedor de Postgres detenido y eliminado al terminar** (`docker stop && docker rm
   vigia-pg-test`) — no queda nada corriendo, igual que exige `HANDOFF.md`.

### Decisión de diseño: `tests/conftest.py` — override, no migración permanente de la suite

Se agregó `VIGIA_PG_TEST_URL` como variable de entorno opcional: si está seteada, `test_db`
apunta `DATABASE_URL` a esa Postgres real y trunca las 7 tablas antes de cada test (`TRUNCATE
... RESTART IDENTITY CASCADE`, vía el mismo `get_conn()` real) para mantener el mismo
aislamiento por-test que ya daba un archivo SQLite temporal nuevo por `tmp_path`. Sin la
variable seteada (el caso default, CI incluido), el comportamiento es exactamente el de
siempre — SQLite temporal, cero cambios. Se decidió NO convertir la suite completa a
parametrizarse automáticamente contra ambos motores en cada corrida (hubiera sido un rediseño
más grande de lo que pedía esta tarea, y ralentiza CI sin un Postgres real disponible ahí) — el
override sirve para un smoke pass real bajo demanda, que es lo que se necesitaba para confirmar
el backend nuevo.

### Qué NO se hizo / sigue pendiente

- No se agregó pooling de conexiones (`psycopg_pool`) — sigue el mismo patrón de "una conexión
  por request" que ya usaba SQLite, mencionado como mejora futura en el docstring de
  `db/connection.py` pero no implementado esta sesión (fuera de alcance: la tarea era verificar
  portabilidad, no optimizar concurrencia).
- El script de smoke E2E (`pg_e2e_smoke.py`) vive en el scratchpad de esta sesión, no en el
  repo — fue una herramienta de verificación puntual, no un test permanente. Si se quiere
  repetir esta verificación en el futuro sin reconstruirlo, conviene formalizarlo como un test
  real marcado para correr solo con `VIGIA_PG_TEST_URL` seteada.
- No se tocó `.env.example` (fuera de alcance explícito de esta sesión, instrucción previa del
  usuario) — quien migre a Postgres en un despliegue real deberá saber configurar `DATABASE_URL`
  con el prefijo `postgresql://` por su cuenta hasta que alguien documente esto ahí.
- CertStream (Item 5) y `agents/revision_ia.py` no se tocaron en esta corrida — fuera de alcance.

## Corrida 13 (2026-07-19) — Item 5: feed real self-hosted de CertStream, cierra la limitación de Corrida 6

Objetivo único de esta corrida: la Corrida 6 dejó documentado que `api/certstream_listener.py`
funciona de punta a punta (arranque, matching, escritura de findings) pero **nunca recibió un
mensaje real**, porque el feed público histórico `wss://certstream.calidog.io` acepta el
handshake y no transmite nada (servicio descontinuado). Esta corrida no toca la lógica del
listener — solo le da un feed real, self-hosted, y confirma que la ingesta real funciona.

### Servidor self-hosted elegido: `certstream-server-go`

De las tres alternativas ya identificadas en Corrida 6 (`certstream-server-go`,
`certstream-server-rust`, `go-certstream` de LeakIX), se eligió **`certstream-server-go`**
(d-Rickyy-b) porque tiene una imagen Docker real, publicada y documentada en Docker Hub
(`0rickyy0/certstream-server-go`, confirmado que existe antes de usarla, no adivinada) y porque
su formato de mensaje de salida es explícitamente compatible con el formato clásico de Calidog
(`message_type: "certificate_update"`, `data.leaf_cert.all_domains`) — el mismo que ya
parseaba `api/certstream_listener.py::_extraer_dominios()` sin necesitar ningún cambio de código.

**Setup exacto usado (reproducible, para deploy futuro):**

```bash
docker run -d --name vigia-certstream-test -p 48182:8080 0rickyy0/certstream-server-go
```

Sin montar `config.yaml` propio — el proyecto documenta que sin uno usa
`config.sample.yaml` por defecto, y eso fue suficiente: arrancó monitoreando **45 CT logs
reales** (Let's Encrypt, Google Argon/Xenon, Sectigo, TrustAsia, geomys/ipng, etc.) en menos de
1 segundo, sin ninguna configuración manual de logs. Puerto elegido (48182) sigue la
convención de puertos poco comunes del proyecto (ver HANDOFF.md), libre y sin colisión.

Endpoints de websocket que expone (no todos usados aquí, documentado para referencia futura):
`/full-stream` (completo), `/` (lite, el que usó Vigia por defecto vía `certstream.listen_for_events`),
`/domains-only`.

**Variable de entorno real usada para apuntar Vigia al feed propio** (no se tocó `.env.example`,
fuera de alcance por instrucción previa del usuario — documentado aquí en su lugar):

```
VIGIA_CERTSTREAM_URL=ws://localhost:48182
```

### Verificación en vivo, en capas

1. **Docker limpio antes de empezar**: `docker ps -a` sin contenedores (otros agentes de esta
   sesión limpiaron correctamente). Se confirmó de nuevo antes de levantar nada.

2. **Servidor solo, fuera de la app**: a los ~5s de `docker run`, el log del contenedor mostraba
   45 CT logs siendo monitoreados y "Processed 5000 entries" en los primeros ~11 segundos —
   tráfico real, no simulado.

3. **Cliente Python `certstream` (el mismo paquete que usa `api/certstream_listener.py`) contra
   el servidor propio, aislado**: 12 segundos de escucha → **7,493 mensajes reales**
   `certificate_update` con dominios reales (`www.richardsandwell.ph`, `www.himay.ph`, etc.).
   Confirma que el paquete pip `certstream` (cliente) y `certstream-server-go` (servidor) son
   compatibles de protocolo sin ningún adaptador.

4. **`procesar_mensaje_certstream()` real (la función de matching de Vigia) alimentada con
   mensajes reales del feed propio, sin la API arriba**: 15 segundos → **4,885 mensajes reales
   procesados por la lógica de matching real de Vigia, 0 excepciones**. Se usó el mapa de
   variantes real ya existente en `ciberseguridad.db` (4 activos tipo dominio: dos veces
   `miempresatest.com`, `example.com`, `localhost-3050-demo.local` → 22,423 variantes dnstwist
   precomputadas), así que el matching corrió contra dominios reales y variados del mundo real
   (incluyendo TLDs compuestos, subdominios, wildcards `*.` limpiados) sin romperse ni una vez.

5. **La API real completa (`uvicorn api.main:app`, puerto 48183) con
   `VIGIA_CERTSTREAM_URL=ws://localhost:48182`**, logging forzado a INFO (mismo truco de
   Corrida 6, vía `logging.basicConfig` antes de `uvicorn.run` porque `--log-level info` de la
   CLI de uvicorn no propaga a loggers de la app). Log real observado:

   ```
   INFO vigia.certstream Mapa de variantes CertStream (re)construido: 22423 variante(s) sobre 3 dominio(s) vigilados
   INFO vigia.certstream Listener de CertStream iniciado (ws://localhost:48182)
   INFO websocket Websocket connected
   INFO certstream Connection established to CertStream! Listening for events...
   ```

   `GET /health` respondió `200` repetidamente durante todo el run (arranque, ~20s después, y
   ~90s después) mientras el listener consumía el feed real en paralelo — confirma que el
   volumen real de CT logs no bloquea ni degrada la API, igual que probó Corrida 6 con el
   feed muerto (aquí con carga real, no ausencia de carga).

6. **Volumen real sostenido, sin errores, ~90 segundos de observación**: `docker stats` sobre
   el contenedor del servidor mostró **904 MB de tráfico de red recibido** en ese lapso (CT
   logs reales son de alto volumen, como anticipaba el docstring del módulo) y memoria estable
   (63 MiB del servidor Go, 164 MB del proceso Python de Vigia) — sin fugas de memoria ni
   crecimiento descontrolado en la ventana observada. **Cero líneas de error** en el log de
   Vigia (`vigia.certstream`) durante todo el run — ni "Conexión CertStream perdida/fallida" ni
   "No se pudo escribir el finding".

### Qué NO se logró (honesto, no maquillado)

**No hubo un match real nuevo** de una variante de dominio de los activos de prueba durante
la ventana de observación (~2 minutos totales de API real corriendo). Se consultó
`findings` con `tipo='dominio_variante_certstream'` al final: solo aparecen las 2 filas ya
existentes de la Corrida 6 (`created_at 2026-07-19 00:34:08`, el test sintético), ninguna
nueva. Esto es esperable y fue anticipado en el encargo de esta tarea — encontrar un
certificado real emitido para una variante dnstwist específica de `miempresatest.com` en una
ventana de ~2 minutos es estadísticamente poco probable incluso con miles de mensajes/segundo
de CT logs, dado que el universo de dominios nuevos por minuto es enorme comparado con el
espacio pequeño de variantes vigiladas en esta base de datos de prueba. El objetivo real de
la tarea (probar que un feed real no rompe nada — mensajes con formas inesperadas, problemas
de codificación, throughput alto) sí se cumplió, con datos reales, no sintéticos.

### Limpieza

Proceso `uvicorn` (PID de esa corrida) detenido con `taskkill`. Contenedor
`vigia-certstream-test` detenido y eliminado (`docker stop` + `docker rm`) — `docker ps -a`
confirmado vacío al terminar, igual que al empezar. No se hizo `git commit` de ningún cambio
(no hubo cambios de código — solo de configuración de entorno en tiempo de ejecución, no
persistida en ningún archivo del repo).

### Próximo paso real (fuera de alcance de esta tarea, cubierto por la tarea de deployment ya en curso)

Este setup fue deliberadamente temporal (contenedor de prueba, sin volumen de config
persistente, sin política de reinicio). Para producción, `certstream-server-go` necesita
correr de forma continua en algún host (no solo durante una sesión de Claude Code) — con
`--restart unless-stopped` como mínimo, e idealmente su propio `docker-compose` junto a la
API de Vigia, apuntando `VIGIA_CERTSTREAM_URL` a esa instancia permanente en vez de
`localhost`. No se intentó resolver el hosting permanente aquí a propósito — es responsabilidad
de la tarea de preparación de despliegue que ya está en curso en esta misma sesión.

## Corrida 12 (2026-07-19) — Cierre del gap real de `POST /scan/activo` (Task A) + intento real de escaneo activo autenticado (Task B)

### Task A — `POST /scan/activo` validaba autorización pero no propiedad del target

`agents/revision_ia.py` (Corrida 8) había encontrado un hallazgo real: `POST
/scan/activo` exige `autorizacion_firmada=true` explícito (más estricto que
`POST /scan`), pero nunca verificaba que `target_url` fuera un asset que el
tenant hubiera registrado de verdad vía `POST /assets` — a diferencia de
`POST /scan`, que sí pasa por el grafo completo. Cualquier tenant
autenticado podía escanear activamente cualquier URL con solo marcar un
booleano en el body.

**Fix real:** nueva función `api/main.py::_tenant_tiene_asset_para_target()`
— determinista, sin LLM, mismo espíritu que `gate_autorizacion` pero
deliberadamente NO comparte código con ella (contestan preguntas distintas:
"¿el booleano de autorización es `True`?" vs. "¿este target es un asset de
este tenant?"). Se decidió explícitamente NO envolver el grafo completo
(`orchestrator/graph.py`) solo para reusar `gate_autorizacion` desde este
endpoint — `POST /scan/activo` ya no pasa por el grafo (ver docstring de
`ActiveScanRequest`), así que forzar el grafo entero (orquestador/recon/
verificación/priorización/remediación, con llamadas reales a Claude/
subfinder/amass) solo para leer un booleano habría sido un costo real sin
ningún beneficio de seguridad adicional.

Compara por dominio registrable (`tools.antisuplantacion.registrable_domain`,
ya usado por el listener de CertStream) para que un asset `miempresa.com`
también autorice `www.miempresa.com`/`app.miempresa.com`, y por coincidencia
exacta de host para assets tipo `ip`. Rechaza con 403 explícito si no hay
match, antes de arrancar el `threading.Thread` que llama a
`run_zap_active_scan` — el wrapper de ZAP nunca se invoca contra un target
no registrado.

**Tests nuevos:** `tests/test_scan_activo_asset_gate.py` (9 tests), mismo
patrón de dos niveles que `tests/test_gate_autorizacion.py`: unidad pura
sobre `_tenant_tiene_asset_para_target()` (apex exacto, subdominio, IP,
asset de otro tenant, asset inactivo) + integración sobre `POST /scan/activo`
real vía `TestClient` con `run_zap_active_scan` espiado (confirma que jamás
se invoca cuando el target no es un asset del tenant, y que sí se invoca
cuando lo es — contraprueba necesaria contra bloquear de más). Suite
completa: **44/44 tests pasan** (35 preexistentes + 9 nuevos), verificado
antes y después del fix.

### Task B — escaneo activo autenticado real, propiamente autorizado

Objetivo: HANDOFF.md Item 2 e Item 4 marcaban como pendiente probar un
escaneo activo autenticado de verdad (bearer token real + `POST /assets`
real + `POST /scan/activo` real, todo pasando por el gate nuevo de Task A).

**Setup real, de punta a punta:**
1. Docker Desktop no estaba corriendo al empezar (`docker ps` falló) — se
   relanzó manualmente y se esperó a que el daemon respondiera (~2 min).
2. Backend Vigia real levantado (`uvicorn`, puerto 48173).
3. Tenant real registrado vía `POST /auth/register` (`authscan@vigiatest.local`).
4. `POST /assets` registrando `localhost` (tipo `dominio`) para ese tenant —
   confirma que Task A's gate deja pasar un target legítimo, no solo que
   bloquea uno ilegítimo.
5. OWASP Juice Shop levantado real dos veces vía Docker (`bkimminich/juice-shop`,
   puertos 3210 y 3211 — dos instancias independientes para poder correr un
   par autenticado/sin-autenticar en paralelo sin que se pisaran).
6. Usuario real registrado en Juice Shop (`POST /api/Users`) y logueado
   (`POST /rest/user/login`) contra la instancia de puerto 3210 — JWT real
   obtenido, no simulado.
7. `POST /scan/activo` real (bearer token del paso 6, `ajax_spider: true`,
   `minutes: 20`, `autorizacion_firmada: true`) — **pasó el gate nuevo de
   Task A sin problema** (target `localhost:3210` matchea el asset
   `localhost` registrado en el paso 4). En paralelo, el mismo endpoint sin
   `bearer_token` contra la instancia de puerto 3211, para comparación
   directa autenticado-vs-no.

**Resultado real del intento con AJAX Spider (igual que Corrida 4, pero
ahora vía el endpoint real y con el gate nuevo):** ambos escaneos
(autenticado y sin autenticar) corrieron ~30 minutos reales y **ambos
terminaron en `estado: error`** — `ToolTimeoutError`: *"'zap-full-scan' no
terminó dentro del límite de 1800s"*. Cero hallazgos en ambos, no porque el
escaneo autenticado no encontrara nada relevante, sino porque el proceso
nunca llegó a producir un reporte.

**Diagnóstico real (no una suposición), tal como pide HANDOFF.md:**
`docker logs <container>` no imprimió NADA en ningún momento (confirmado
repetidas veces, incluso con `docker logs -f` corriendo 25 minutos en
paralelo sin una sola línea) — `zap-full-scan.py` en este modo no escribe
progreso a stdout/stderr, así que `docker logs` no es la herramienta correcta
para diagnosticar este contenedor. En su lugar, `docker top`/`docker exec ...
ps aux` sí mostró evidencia real de trabajo genuino: un Firefox headless real
con 7-8 procesos de pestaña activos, `geckodriver`, un proceso Java a
~120-600% CPU (multi-core), y — la señal más convincente — la propia app
Juice Shop objetivo llegó a 85-92% de CPU en el momento correspondiente a la
fase de ataque activo. No estaba colgado: estaba haciendo AJAX spidering +
ataque activo de verdad, solo que más lento de lo que el presupuesto de 20
min (+15 min de margen = 30 min efectivos) permite en este host Windows/Docker
Desktop/WSL2 específico — exactamente la hipótesis que Corrida 4 ya había
planteado sin poder confirmarla con evidencia de proceso, ahora confirmada.

**Bug real nuevo, no documentado antes:** al expirar el timeout de Python
(`subprocess.run(..., timeout=1800)` en `tools/_shared.py::run_command`), el
proceso `docker run` del lado del host muere, pero **el contenedor sigue
vivo del lado del daemon de Docker** (`docker ps` lo mostraba "Up 30 minutes"
mucho después de que Vigia ya hubiera marcado el scan como `error`) — nunca
se limpia solo pese a `--rm`, porque `--rm` solo actúa cuando el contenedor
termina por sí mismo, no cuando el cliente que lo lanzó se desconecta. Peor
aún: `tools/scan.py::_run_zap_script` monta un `tempfile.TemporaryDirectory()`
que se borra en cuanto la excepción de timeout se propaga — se confirmó que
el directorio (`vigia-zap-rt9i1ok0`) ya no existía en el host apenas
segundos después del timeout. Aunque el contenedor huérfano hubiera seguido
corriendo hasta terminar el escaneo, jamás habría podido escribir su reporte
en un bind-mount que ya no existe del lado del host — los resultados de un
timeout son irrecuperables por diseño actual, no solo lentos. Se limpiaron
manualmente los contenedores huérfanos (`docker stop`/`docker rm -f`) al
notarlo. Este bug afecta a cualquier llamada a `run_zap_baseline`/
`run_zap_active_scan` que exceda su timeout, no solo a este intento.

**Segundo intento, más acotado (spider clásico en vez de AJAX Spider,
`minutes=5`, la misma configuración que sí completó en Corrida 3):**
mismo resultado — **ambos scans (autenticado y sin autenticar) volvieron a
expirar**, esta vez a los 600s configurados. Lectura honesta: no se investigó
a fondo por qué una configuración que funcionó en una sesión anterior falló
esta vez (la hipótesis más probable es contención de recursos real: para
este momento la máquina ya llevaba ~45 minutos corriendo simultáneamente dos
instancias de Juice Shop más contenedores ZAP con JVM+Firefox sucesivos sin
reinicio del daemon Docker de por medio — no se descarta que el propio host
estuviera bajo presión de memoria/CPU acumulada). Se limpiaron los
contenedores huérfanos de este segundo intento también.

**Números reales de recall/precisión — con la lectura honesta que corresponde:**

Correr `eval/run_eval.py --ground-truth eval/ground_truth.yaml` contra un
JSON de hallazgos vacío (los cuatro intentos de esta corrida no produjeron
ninguno) da, trivialmente, 0 TP / 0 FP / 11 FN / 0% precisión / 0% recall.
**Este número NO es una medición real de la calidad del escaneo autenticado
— es lo que da cualquier corrida sin datos.** No se reporta como si fuera
una medición honesta de "el escaneo autenticado no sirve"; es la
consecuencia de un bloqueador de infraestructura real (timeout), no una
señal sobre el pipeline de detección.

Los únicos números reales y comparables de "autenticado vs. no autenticado"
que existen hoy siguen siendo los de **Corrida 4** (misma app, mismo
`ground_truth.yaml`, mismo `zap-full-scan` con spider clásico, ya con bearer
token real inyectado vía `-config replacer.*`): **9.09% recall / 20%
precisión en ambos casos, idénticos 15 hallazgos con o sin token** — la
autenticación por sí sola, sin que el AJAX Spider realmente alcance las
rutas de la SPA que están detrás de sesión, no cambia nada. Esta corrida
(12) no logró producir un dato nuevo que contradiga o confirme eso con el
AJAX Spider funcionando de punta a punta — el bloqueador de infraestructura
llegó primero. Es la misma conclusión de Corrida 4, ahora reforzada por dos
intentos más (con el gate de autorización real de por medio) que topan con
el mismo límite real de tiempo/recursos en este host específico.

**Lo que SÍ se verificó en vivo y es un resultado real y positivo de esta
corrida:** el nuevo gate de Task A funciona correctamente en el flujo E2E
completo — el `target_url` de ambos intentos (`localhost:3210` y
`localhost:3211`) coincidió correctamente contra el asset `localhost`
registrado, dejando pasar el escaneo autorizado sin falsos rechazos
(contraprueba de "no bloquear de más", igual que ya confirman los tests
unitarios). El gate no fue el bloqueador en ningún momento de esta corrida.

**Cleanup:** todos los contenedores de Docker de esta corrida (2x Juice
Shop, 4x ZAP intentados) fueron detenidos y removidos (`docker rm -f`) al
terminar — `docker ps -a` quedó vacío. Docker Desktop se dejó corriendo
(estaba apagado al empezar la sesión, se relanzó para esta corrida).

**Próximo paso concreto, ya no especulativo:** (1) arreglar el bug de
contenedor huérfano en timeout — `tools/scan.py`/`tools/_shared.py`
necesitan capturar el nombre/ID real del contenedor (`docker run` sin
`--name` no lo expone fácil; usar `--name vigia-zap-<uuid>` explícito y un
`docker stop`/`docker rm` explícito en el bloque `except ToolTimeoutError`)
para que un timeout limpie de verdad en vez de dejar el contenedor corriendo
indefinidamente. (2) Correr el AJAX Spider como su propio job de fondo
verdaderamente desacoplado del timeout del subprocess (ya lo sugería
Corrida 4) en vez de un único `subprocess.run` con timeout fijo, para que
una corrida larga no se pierda por completo si excede el presupuesto.
(3) Repetir este mismo experimento en un host con menos contención (o tras
un reinicio limpio de Docker Desktop) antes de concluir que el AJAX Spider
"nunca" completa en Windows — la posibilidad de contención de recursos
acumulada en esta sesión específica no se descartó.

## Corrida 11 (2026-07-19) — Frontend: reportes descargables, comparar escaneos, invitar equipo

Objetivo: HANDOFF.md tenía tres items transversales pendientes desde hacía
varias sesiones ("Mejorar el frontend más allá del dashboard actual"): (1)
reportes descargables en PDF/DOCX, (2) comparar escaneos en el tiempo, (3)
invitar más usuarios al mismo tenant. Se hicieron las tres, probadas contra
la API real corriendo (no solo "compila").

### 1. Reportes descargables (PDF/DOCX)

Nuevo `tools/report_export.py`: convierte el markdown que ya generan
`agents/reporteria.py` (reporte técnico de un scan) y `agents/cumplimiento.py`
(reporte de cumplimiento) a bytes de PDF/DOCX, 100% servidor. Librerías
elegidas: `fpdf2` y `python-docx` — ya estaban instaladas en el entorno del
proyecto y son puras en Python (sin wkhtmltopdf/GTK-Pango de weasyprint, que
son dolorosos en Windows). Se agregaron explícitamente a `pyproject.toml`
(no estaban declaradas, solo estaban de hecho instaladas). El parser de
markdown es deliberadamente simple (encabezados, listas, negrita, párrafos;
tablas se degradan a texto plano).

Nuevos endpoints en `api/main.py`: `GET /reports/cumplimiento/download?formato=pdf|docx`
y `GET /scans/{scan_id}/report/download?formato=pdf|docx`. Frontend:
botones "PDF"/"DOCX" por escaneo completado en "Actividad reciente" y
"Descargar PDF"/"Descargar DOCX" en la nueva tarjeta "Reporte de
cumplimiento" (`Dashboard.tsx`) — descarga real vía `fetch` + blob +
`<a download>` (no `<a href>` directo, porque hace falta el header
`Authorization`).

**Dos bugs reales encontrados y arreglados probando esto contra reportes
de verdad (no hipotéticos):**
1. `fpdf2.multi_cell()` con `new_x`/`new_y` por defecto deja el cursor
   pegado al margen derecho después de cada línea — la SIGUNDA línea de
   cualquier párrafo reventaba con `FPDFException: Not enough horizontal
   space to render a single character`. Se arregló forzando
   `new_x="LMARGIN", new_y="NEXT"` en cada `multi_cell`.
2. Las fuentes core de fpdf2 solo cubren latin-1 real (ISO-8859-1), que
   NO incluye el em dash (`—`) que Claude usa todo el tiempo en sus
   reportes — sin arreglo, cada em dash se convertía en un `?` suelto
   visible en el PDF final. Se agregó una tabla de sustitución
   (em/en dash, comillas curvas, elipsis) a ASCII antes de la codificación
   latin-1 con `errors="replace"` como último recurso.

**Verificado en vivo:** con la API real corriendo (puerto 48590) y un
tenant con un scan real completado (`demo@vigia.local`, scan
`be4a6b06-70de-4c79-b184-5059bb915aa0` contra `http://localhost:3050`,
10 hallazgos reales de una sesión anterior), se descargaron los 4
combos (scan PDF, scan DOCX, cumplimiento PDF, cumplimiento DOCX) por
HTTP real con JWT real: headers `Content-Type`/`Content-Disposition`
correctos, firma de archivo correcta (`%PDF-1.3`, `PK\x03\x04` de zip/docx),
y contenido verificado leyendo el DOCX con `python-docx` y extrayendo
texto del PDF con `pypdf` — el reporte de cumplimiento real (LLM real,
Ley 2573/ISO 27001) se lee completo y sin caracteres corruptos tras el
fix del em dash. También se probó contra un reporte real gigante (un scan
propio contra `example.com`, `reporte_final` de ~2.05 millones de
caracteres): la conversión a PDF tardó ~37s y produjo un PDF válido de
1.6MB sin reventar — confirma que el conversor no se cae con reportes
grandes, aunque no es instantáneo.

### 2. Comparar escaneos en el tiempo

Nuevo `frontend/src/components/ScanHistoryChart.tsx`: gráfico de barras
apiladas en SVG puro (sin librería de charting nueva — `package.json` no
tenía ninguna). Agregación 100% client-side sobre datos que `GET /scans`
y `GET /findings` ya devolvían — no hizo falta ningún endpoint nuevo.
Muestra hasta los últimos 12 escaneos completados, un color por severidad,
tooltip nativo (`<title>`) por segmento.

**Verificado en vivo:** con el mismo scan real de `demo@vigia.local`
(10 hallazgos, todos severidad `info`), se confirmó vía JS en el DOM que
el SVG renderiza exactamente un `<rect>` de altura completa (160px, el
máximo) en el color gris de "info", con el `<title>` correcto
(`"http://localhost:3050 — Info: 10"`). No se pudo probar visualmente con
más de una severidad distinta en datos 100% reales dentro del tiempo de
esta sesión (los scans reales disponibles en la base compartida son
recientes y de severidad uniforme) — la lógica de apilado por severidad
se revisó por código y es simétrica entre severidades (mismo `reduce`
para las 5), así que el caso de una sola severidad ya ejercita el mismo
camino de código que un caso con varias.

### 3. Invitar usuarios al mismo tenant

`db/schema.sql`: nueva tabla `invitations` (token único, `role` admin/member,
`estado` pendiente/aceptada/revocada, expira a 7 días). Nuevos endpoints en
`api/main.py`: `POST /tenant/invitations` (owner/admin, vía
`require_role` ya existente en `auth/jwt_auth.py` pero sin usar hasta
ahora), `GET /tenant/invitations`, `DELETE /tenant/invitations/{id}`,
`GET /tenant/members`, y `GET /tenant/invitations/preview/{token}`
(público, sin auth, para que el invitado vea a qué negocio se está
uniendo antes de registrarse). `POST /auth/register` ahora acepta
`invite_token` opcional: si viene, NO crea un tenant nuevo — agrega el
usuario al tenant de la invitación con el `role` de la invitación
(`users.email` es `UNIQUE` global en el schema, una cuenta = un tenant,
eso ya lo garantizaba el schema desde antes). Sin envío de email real
(no hay proveedor configurado en el proyecto): el owner/admin comparte el
link (`/?invite=<token>`) manualmente — decisión de alcance explícita,
más honesta que fingir un flujo de email que no existe.

Frontend: nuevo `frontend/src/components/Equipo.tsx` (lista de miembros +
form de invitación + invitaciones pendientes con botón "Revocar", visible
solo si `me.role` es `owner`/`admin`) montado en `Dashboard.tsx`.
`Login.tsx` ahora lee `?invite=<token>` de la URL, llama al preview
público, muestra un banner "Te invitaron a unirte a X como Y", pre-llena
el email (read-only) y oculta el campo "nombre del negocio".

**Verificado en vivo, de punta a punta, con la API y el frontend reales
corriendo (backend :48590, frontend :48591):**
1. Se registró un tenant nuevo (`owner-qa@vigia.local` / "Pyme Test QA")
   por la UI real.
2. Se creó una invitación real para `teammate-qa@vigia.local` (rol
   `member`) desde la tarjeta "Equipo" — la API devolvió el link real
   (`http://localhost:48591/?invite=<token>`).
3. En una pestaña sin sesión (localStorage limpiado a propósito), se abrió
   ese link real: el preview público mostró correctamente "Te invitaron a
   unirte a Pyme Test QA como member", con el email pre-llenado y de solo
   lectura.
4. Se completó el registro con ese `invite_token` — la cuenta nueva quedó
   en el MISMO `tenant_id` que el owner (confirmado leyendo
   `ciberseguridad.db` directamente: misma fila `tenant_id` para ambos
   usuarios), con `role='member'`, y la invitación pasó a
   `estado='aceptada'`.
5. El nuevo miembro vio el mismo dashboard compartido (mismo dominio
   `example.com` registrado por el owner) y la tarjeta "Equipo" mostró
   ambos usuarios (`owner-qa@vigia.local` como OWNER,
   `teammate-qa@vigia.local` como MIEMBRO) — y, correctamente, NO vio el
   formulario de invitación (gate por rol funcionando: solo
   owner/admin lo ven).

**Nota real de esta sesión, no relacionada con el código:** el primer
intento de click en el botón "Unirme" vía coordenadas (`computer
left_click`) no disparó el submit del formulario (sin request de red
alguno) — se resolvió disparando el click por JS
(`button.click()`) y funcionó de inmediato con los mismos datos, así que
fue un problema del driver de automatización de esta sesión, no un bug
de la UI (confirmado también porque el click por coordenadas sí funcionó
sin problema en otros formularios de la misma página en la misma sesión).

## Corrida 10 (2026-07-19) — Cierra la brecha de persistencia de Anti-Suplantación bajo demanda (Item 6)

Objetivo: `docs/cumplimiento.md` y `HANDOFF.md` (Item 6) documentaban una
brecha real, no resuelta a propósito en ese item: `agents/antisuplantacion.py`
(dnstwist + Sherlock bajo demanda, rama opcional del grafo) SÍ produce
señales reales dentro de `antisuplantacion_findings`, pero `api/main.py::scan()`
solo insertaba `verified_findings` (rama técnica) en la tabla `findings` — la
única excepción era `api/certstream_listener.py` (vigilancia continua, no
bajo demanda). Como consecuencia, `GET /findings` y `GET /reports/cumplimiento`
nunca podían ver una señal de suplantación generada por un `POST /scan` con
`antisuplantacion_habilitado=true`. Esta corrida cierra esa brecha.

**Cambio real:** `_extraer_findings_antisuplantacion()` (nueva función en
`api/main.py`) aplana `antisuplantacion_findings` a filas insertables en
`findings`, reutilizando el mismo `scan_id` real que `POST /scan` ya crea
para toda la corrida del grafo — a diferencia de `certstream_listener.py`,
aquí NO hace falta una fila `scans` sintética, porque ya existe una real.
Usa los mismos valores de `tipo` que `agents/antisuplantacion.py::node()` ya
emite (`"dominio_variante"`, `"perfil_red_social"`), que
`agents/cumplimiento.py::_categorizar_hallazgo()` **ya reconocía** desde que
se escribió ese módulo (mapea a `suplantacion_dominio`/`suplantacion_redes_sociales`)
— la brecha era 100% de persistencia, no de categorización, tal como
`docs/cumplimiento.md` ya lo dejaba anotado. No se tocó `agents/cumplimiento.py`.

**Bug real encontrado mientras se escribía el fix (antes de cualquier corrida
en vivo):** `agents/antisuplantacion.py::node()` arma `señales_crudas` a
partir de **todos** los resultados de `run_dnstwist()`, incluyendo la entrada
`fuzzer='*original'` — que es el propio dominio del cliente, no una variante.
`tools/antisuplantacion.py::generate_domain_variants()` (la función que usa
CertStream) sí la excluye explícitamente; `agents/antisuplantacion.py::node()`
no. Sin filtrarlo, este fix habría persistido un falso positivo real: el
dominio legítimo del propio cliente marcado como "posible dominio variante"
de sí mismo. `_extraer_findings_antisuplantacion()` excluye esta entrada
(`fuzzer == "*original"` o `dominio == target`), mismo criterio que ya usa
`api/certstream_listener.py::procesar_mensaje_certstream()`
(`if apex == dominio_base ...: continue`). Encontrado leyendo el código antes
de escribir el fix, no en producción — pero es un hallazgo real y concreto,
no hipotético (se confirmó en vivo más abajo: la entrada `*original` para
`microsoft.com` — ver aviso de alcance abajo — apareció en `señales_crudas`
y habría sido insertada sin este filtro).

### Aviso sobre el primer intento de verificación (target incorrecto, corregido)

El primer intento de verificación en vivo de esta corrida usó `microsoft.com`
como target real — una elección apresurada para tener "un dominio real con
variantes ya registradas" sin pensarlo contra la regla de oro del proyecto
(`plan-proyecto-ciberseguridad.md` sección 0: nunca apuntar contra un tercero
real sin autorización). El coordinador de la sesión lo señaló a tiempo. Para
que quede completo y honesto en el registro:

- El request fue `POST /scan` con `autorizacion_firmada: false` y sin
  `scope.dominios` — el gate determinista (`orchestrator/graph.py::gate_autorizacion`)
  enrutó a `bloqueo_autorizacion`, así que `agents/escaneo.py` (Nuclei/ZAP/
  Semgrep/Trivy/Grype) **nunca se invocó** — confirmado por el propio
  `ScanResponse` (`scan_findings`/`verified_findings` en 0,
  `autorizacion_bloqueo_motivo` presente) y por lista de procesos (sin
  `nuclei`/`zap` corriendo).
- Lo único que corrió fue la rama de Anti-Suplantación: `run_dnstwist()`
  (resolución DNS pública sobre permutaciones del string `"microsoft.com"`,
  no contra la infraestructura real de Microsoft) y `run_sherlock()`
  (consultas de existencia de usuario público contra ~400 plataformas de
  terceros, tampoco contra servidores de Microsoft) — el mecanismo exacto de
  OSINT pasivo para el que existe esta función, pero usando el nombre de una
  empresa real ajena al proyecto como ejemplo, sin necesidad.
- Se insertaron (y luego se eliminaron por completo, junto con el tenant y
  scan de prueba) 449 filas `findings` reales generadas por ese request antes
  de repetir la verificación con un dominio propio/sintético. Ningún dato se
  dejó persistido referenciando esa marca.

Lección para el registro: incluso para OSINT puramente pasivo (sin tocar la
infraestructura del tercero), este proyecto usa únicamente dominios propios o
sintéticos como target de prueba — la misma regla que ya aplicaba a escaneo
activo, extendida aquí explícitamente a anti-suplantación.

### Verificación real (repetida con target correcto) — dominio sintético propio

Tenant nuevo (`antisup-synth-test@vigia.local`) + activo `miempresatest.com`
(mismo dominio de prueba sintético usado en la Corrida 6 para CertStream, por
consistencia). `POST /scan` real contra la API (`uvicorn` en puerto 48181,
detenido al terminar), `target: "miempresatest.com"`,
`antisuplantacion_habilitado: true`, `autorizacion_firmada: false` (no hace
falta autorización para esta rama — no toca `tools/scan.py`).

**Resultado real, sin mocks:** `run_dnstwist()` encontró 2 variantes YA
REGISTRADAS de un dominio que ni siquiera es una empresa real
(`miempres.atest.com`, `miempre.satest.com` — probablemente parking/squatting
automatizado de dominios genéricos, no dirigido a este proyecto) y
`run_sherlock()` encontró 4 perfiles reales con el username `miempresatest`
en Slack, TikTok, YouTube y baby.ru (perfiles placeholder/reservados
automáticamente por esas plataformas, no cuentas activas de nadie — pero
reales, no inventados). La entrada `*original` (`miempresatest.com` mismo)
apareció en las señales crudas y fue correctamente excluida por el fix del
bug de arriba — no se coló como falso positivo.

**6 filas `findings` reales insertadas**, verificado por tres vías
independientes:
1. Consulta directa a `ciberseguridad.db` (`SELECT * FROM findings WHERE
   tenant_id = ...`): 2 `dominio_variante`/`high`, 4 `perfil_red_social`/
   `medium`, todas con `confirmado=0`, todas colgadas del mismo `scan_id`
   real de esta corrida (no una fila `scans` sintética).
2. `GET /findings` con el JWT real del tenant de prueba: las mismas 6 filas,
   con `tipo`/`severidad`/`endpoint` correctos.
3. `GET /reports/cumplimiento` con el mismo JWT: `total_hallazgos: 6`,
   categorizados correctamente sin tocar `agents/cumplimiento.py` —
   las 6 filas cayeron en las categorías de suplantación esperadas, cada
   una con su mapeo a ISO 27001 correspondiente (detalle del mapeo exacto
   en el paquete privado, ver `docs/open-core.md`) — confirma que la
   categorización ya estaba lista desde que se escribió ese módulo, tal
   como documentaba la brecha original.

**Limpieza:** servidor de prueba (puerto 48181) detenido al terminar. El
tenant/scan/findings de la prueba con `microsoft.com` se eliminaron por
completo de la base de datos (ver aviso arriba). Los del tenant sintético
(`miempresatest.com`) se dejaron en `ciberseguridad.db`, igual que el
precedente de `docs/cumplimiento.md` ("quedan disponibles para seguir
probando este módulo... en sesiones futuras").

### Qué sigue quedando fuera de alcance (sin cambios en esta corrida)

- `check_safe_browsing()` (Google Safe Browsing) sigue sin wireado al nodo
  `agents/antisuplantacion.py::node()` — solo dnstwist/Sherlock corren hoy.
  No es parte de la brecha de persistencia que cerraba este item, pero queda
  anotado igual que antes.
- El mismo bug de mojibake UTF-8 en el fallback CLI que documentó
  `docs/cumplimiento.md` sigue sin re-verificarse contra `reporteria.py`/
  `remediacion.py`/`priorizacion.py` corriendo de nuevo — no tocado aquí.

## Corrida 9 (2026-07-19) — Primeros tests automatizados reales del proyecto + CI mínimo

Ítem transversal pendiente desde hace varias corridas (ver
docs/produccion-readiness.md sección 3, y HANDOFF.md "Ajustar y probar más en
general"): `pytest`/`pytest-asyncio` estaban declarados en `pyproject.toml`
como dependencia desde el inicio del proyecto, pero cero archivos
`test_*.py` existían en todo el repo (confirmado de nuevo con
`glob **/test_*.py` antes de empezar). Todo lo "probado" hasta ahora había
sido manual, en vivo, una vez por sesión.

**Qué se escribió, en el orden de prioridad exacto de
`docs/produccion-readiness.md` sección 3:**

1. **`tests/test_gate_autorizacion.py`** — la prueba de mayor prioridad del
   proyecto (control de seguridad más crítico). Dos niveles: (a) unidad pura
   sobre `orchestrator/graph.py::gate_autorizacion()` confirmando que bloquea
   con `autorizacion_firmada=False` explícito (no solo con el campo ausente,
   que es un caso distinto), que permite solo con `True` booleano exacto, y
   que valores "truthy" no booleanos (`1`, `"true"`, `"True"`, `["si"]`) NO
   pasan la puerta (el código usa `is True`, no `bool(...)`); (b) integración
   sobre el `StateGraph` real compilado (`build_graph()`, el mismo que usa
   `api/main.py`), con `agents.escaneo.node` reemplazado por un espía que
   lanza `AssertionError` si se llega a invocar — confirma que la puerta de
   verdad desvía el flujo del grafo completo, no solo que un campo interno
   quedó bien seteado. El resto del pipeline (orquestador, recon,
   verificación, priorización, remediación, reportería) corre de punta a
   punta en este test — recon y Claude se mockean para no depender de
   `subfinder`/`amass`/`claude` reales instalados en esta máquina de
   desarrollo (ver `conftest.py`).
2. **`tests/test_llm_cli_encoding.py`** — regresión del bug de mojibake
   documentado en `docs/cumplimiento.md` (`_call_via_cli` decodificando
   UTF-8 como cp1252 en Windows). Tres pruebas: guardia barata que confirma
   que `subprocess.run` sigue recibiendo `encoding="utf-8"` explícito;
   prueba de punta a punta con un subproceso Python **real** (no un mock que
   devuelve un string ya en memoria) que escribe bytes UTF-8 genuinos con
   tildes/ñ/em-dash a stdout, confirmando que `_call_via_cli` los decodifica
   correctamente; y una prueba de control que decodifica esos mismos bytes
   como `cp1252` para demostrar que sí se corrompen (así se sabe que la
   prueba anterior tiene poder real de detección, no que coincide por
   casualidad). **Nota real:** mientras se escribía este test, otro agente
   trabajando en paralelo en esta misma sesión modificó `_call_via_cli` para
   pasar `user_message` por stdin (`input=user_message`) en vez de como
   argumento de línea de comandos — un fix real y distinto (`WinError 206`
   con payloads grandes, ver docstring del módulo). El test tuvo que
   ajustarse para pasar el kwarg `input` a través del mock de
   `subprocess.run`; una vez ajustado, sigue verificando exactamente lo que
   debía (la decodificación UTF-8), ahora contra la forma real y actual de
   la llamada.
3. **`tests/test_cumplimiento_categorizacion.py`** — se extrajeron los 10
   hallazgos reales de ZAP baseline contra Juice Shop directamente de
   `ciberseguridad.db` (tabla `findings`, tenant de la corrida de
   verificación de Item 6) y se congelaron en
   `eval/cumplimiento_fixture_juiceshop.json`, con la categoría esperada de
   cada uno calculada corriendo `_categorizar_hallazgo()` real y verificada
   contra `docs/cumplimiento.md`. Incluye pruebas explícitas para los dos
   casos exactos del bug de falso positivo ya documentado (CSP Header Not
   Set no debe ser `inyeccion`; Cross-Origin-Embedder-Policy Header Missing
   no debe ser `cors_mal_configurado`) y una prueba estructural directa de
   que `_texto_buscable()` excluye `desc` del texto de clasificación.
4. **`tests/test_auth_flow.py`** — `POST /auth/register` → `POST /auth/login`
   → `GET /me` contra la app real de `api/main.py` con `TestClient`
   (in-process, sin servidor uvicorn), más casos de contraprueba (email
   duplicado → 409, password incorrecto → 401, sin token → 401/403, token
   inválido → 401).
5. **`tests/test_multi_tenant_isolation.py`** — dos tenants registrados de
   verdad vía `POST /auth/register`, hallazgos sembrados directo en la tabla
   `findings` (sin pasar por el grafo — no hace falta invocar Nuclei/ZAP/
   Claude reales para probar un filtro `WHERE tenant_id = ?`), confirmando
   que ni `GET /findings` ni `GET /reports/cumplimiento` filtran datos entre
   tenants.

**Infraestructura compartida (`tests/conftest.py`):** fixture `test_db`
(cada test corre contra su propio archivo SQLite en `tmp_path`, nunca contra
`ciberseguridad.db`), fixture `client` (`TestClient(app)` sin entrar al
`lifespan` real — evita arrancar `api/scheduler.py`/
`api/certstream_listener.py` durante los tests), y un fixture **autouse**
que fuerza `LLMNoDisponibleError` en todo `call_claude()` por defecto (esta
máquina de desarrollo tiene tanto `ANTHROPIC_API_KEY` potencialmente
configurable como el binario `claude` instalado — sin este fixture, correr
la suite podría disparar llamadas reales a Claude).

**Resultado real (`pytest -v`, corrida completa, incluyendo una segunda
corrida en un venv limpio con `pip install -e ".[dev]"` para simular CI de
verdad):**

```
35 passed, 9 warnings in ~11s
```

Los 9 warnings son `StarletteDeprecationWarning` (uso de `httpx` con
`TestClient`, no accionable sin cambiar de framework de testing) y
`InsecureKeyLengthWarning` de PyJWT (el `JWT_SECRET` de prueba en
`conftest.py` es corto a propósito, sin impacto real fuera de los tests) —
ninguno indica un fallo real.

**Bug real encontrado mientras se escribía la suite:** `ruff check .`
sobre el repo completo (antes de acotar el CI a lo mínimo) encontró un
import sin usar preexistente en `orchestrator/state.py`
(`from typing import ..., Any, ...`, nunca referenciado en el archivo) —
no relacionado con los tests, pero se corrigió (cambio de una línea) para
que el CI nuevo empezara en verde desde el primer push, no en rojo por una
deuda preexistente sin relación con este trabajo.

**Ningún bug funcional nuevo apareció en la lógica bajo prueba** (gate de
autorización, encoding de CLI, categorización de cumplimiento, auth,
aislamiento multi-tenant) — las 5 áreas ya estaban correctas de las
correcciones documentadas en sesiones anteriores; estos tests las
convierten en verificación automática permanente en vez de verificación
manual de una sola vez.

**CI mínimo:** `.github/workflows/tests.yml` (nuevo) — `ubuntu-latest`,
Python 3.12, `pip install -e ".[dev]"`, `ruff check .`, `pytest -v`. Corre
en cada push a `main` y en cada PR. A propósito NO incluye deploy ni
matriz de versiones — el pedido explícito era "la diferencia entre 'hope
nothing broke' y 'know in 90 seconds'", no una pipeline completa.

## Corrida 8 (2026-07-19) — Agente de revisión de código con IA (`agents/revision_ia.py`), probado contra el propio código de Vigia

Ítem transversal de HANDOFF.md ("la idea concreta pendiente es un agente de
revisión de código con IA que complemente a Semgrep... no empezar esto antes
de que Semgrep esté conectado"). Semgrep ya quedó wireado en `agents/escaneo.py`
en la corrida anterior (commit `d8e58f3`), así que este ítem ya no estaba
bloqueado.

**Qué se construyó:** `agents/revision_ia.py` (nuevo), función
`revisar_codigo(codigo_paths, autorizacion_firmada, envio_codigo_a_llm_autorizado, semgrep_findings=None, contexto_negocio="")`.
Usa `call_claude()` (mismo helper que `priorizacion`/`remediacion`/`reporteria`/
`cumplimiento`) con un `SYSTEM_PROMPT` acotado a 5 categorías que un
analizador sintáctico no puede ver: control de acceso con el campo
equivocado, aislamiento multi-tenant que falla en un camino específico,
confianza en input del cliente para valores que debería calcular el
servidor, lógica de negocio evadible por orden de llamadas, y claims de
sesión/token obsoletos. Prohíbe explícitamente reportar lo que ya cubre
Semgrep (secretos, SQLi sintáctico, criptografía débil, cabeceras) — el
razonamiento completo de por qué el prompt es angosto a propósito, y las
dos decisiones de diseño de abajo, están documentadas en el docstring del
módulo (no se duplican aquí).

**Decisión 1 — Semgrep-aware, no standalone puro:** recibe opcionalmente
`semgrep_findings` (la forma que produce `agents/escaneo.py::node()`) y los
reduce a un digest compacto (`_resumir_semgrep()`) para que el LLM pueda (a)
evitar repetir lo que Semgrep ya marcó y (b) razonar sobre si un patrón
sintáctico es realmente explotable en el contexto de negocio concreto —
algo que Semgrep no puede decidir por sí mismo. Funciona igual sin
Semgrep (`semgrep_findings=None`), no es una dependencia dura.

**Decisión 2 — función bajo demanda, NO nodo de `orchestrator/graph.py`:**
mismo patrón que `agents/cumplimiento.py`. Motivos concretos (detalle
completo en el docstring del módulo): (1) costo/latencia no encajan con el
ciclo recurrente automático del scheduler, que ya tiene problemas de
latencia documentados con la CLI de Claude (HANDOFF.md Item 1); (2)
`scope.codigo_paths` normalmente está vacío — la mayoría de targets del
pipeline son URLs, no rutas de filesystem; (3) necesita leer contenido real
de archivos, no solo hallazgos ya extraídos, una responsabilidad distinta a
la de cualquier nodo actual; (4) implica una decisión de privacidad
DISTINTA de `autorizacion_firmada` — enviar código fuente real a un LLM de
terceros (API de Anthropic o `claude -p`) no es lo mismo que autorizar un
escaneo (Semgrep corre 100% local, nada sale de la máquina). Por eso
`revisar_codigo()` exige DOS flags separados: `autorizacion_firmada` (la
puerta de siempre) y `envio_codigo_a_llm_autorizado` (consentimiento
explícito y separado para que el CONTENIDO del código viaje a un LLM). Si
el uso bajo demanda crece, el siguiente paso natural es un endpoint
(`POST /reports/revision-ia`, mismo patrón que `GET /reports/cumplimiento`),
no un nodo del grafo.

**Probado en vivo contra el propio código de Vigia (no simulado), sin
`ANTHROPIC_API_KEY` configurada — todo corrió por el fallback de CLI
(`claude -p`):**

1. `auth/jwt_auth.py` solo: 2 hallazgos reales, ambos en la categoría
   `gestion_sesion_o_token`/`aislamiento_multitenant` sobre el mismo bug de
   fondo — `get_current_user()` confía en los claims `role`/`plan`/`tenant_id`
   embebidos en el JWT (TTL de 7 días) sin volver a consultar la base de
   datos cuando el token ya trae los 4 claims completos (el "camino
   rápido"). Si un owner degrada a un usuario, lo cambia de tenant o de
   plan, el token viejo sigue siendo válido con los valores anteriores
   hasta por 7 días. Línea citada (73 y 76) verificada contra el archivo
   real: coincide exactamente. Esto es precisamente el tipo de falla que
   Semgrep no puede ver — no hay ningún patrón sintáctico "malo", la lógica
   de autorización simplemente confía en datos que pueden quedar obsoletos.

2. `auth/jwt_auth.py` + `api/main.py` juntos (tras el fix de la CLI, ver
   abajo): 3 hallazgos. Repite el hallazgo de JWT de arriba, más dos
   nuevos:
   - **Real y verificado contra el código actual:** `POST /scan/activo`
     (`escaneo_activo_async`, `api/main.py`) solo valida que
     `payload.autorizacion_firmada` sea `true` — un booleano que el propio
     cliente envía en el mismo request, sin ligarlo a ningún registro de
     autorización real ni validar que `target_url` corresponda a un asset
     registrado del tenant autenticado. Cualquier usuario autenticado
     (incluso plan trial) podría lanzar un escaneo ACTIVO real de ZAP
     (payloads de ataque reales) contra cualquier URL de un tercero con solo
     mandar `autorizacion_firmada: true`. A diferencia de `POST /scan`, este
     endpoint no pasa por `gate_autorizacion` del grafo — es una puerta
     paralela y más débil. Confirmado leyendo el código real
     (`api/main.py` líneas 883-905): no hay contradicción, el hallazgo es
     correcto.
   - **Hallazgo correctamente matizado, no un hallazgo real (falso positivo
     evitado):** sobre `invite_token`/`InvitationIn`, el modelo señaló que
     `role` no visible-en-el-fragmento-truncado podría no validarse — pero
     el propio modelo lo condicionó explícitamente ("si el endpoint de
     creación de invitaciones... no valida ese campo"). Al revisar el
     archivo completo (`crear_invitacion`, línea ~1057), SÍ valida
     `payload.role not in ("admin", "member")`. El módulo marcó el archivo
     como truncado (superó `_MAX_BYTES_POR_ARCHIVO`, 40.000 caracteres, ya
     que `api/main.py` creció a 45.091 bytes durante esta sesión por trabajo
     concurrente de otro agente) y el modelo respetó esa incertidumbre en
     vez de afirmar un hallazgo con seguridad — comportamiento honesto, no
     una alucinación, pero buen recordatorio de que los límites de tamaño
     tienen un costo real de cobertura.

3. `agents/cumplimiento.py` solo: encontró consistentemente (en dos
   corridas distintas) el mismo hallazgo real de fondo —
   `generar_reporte_cumplimiento()` confía ciegamente en que el llamador ya
   filtró `findings` por `tenant_id` (documentado en su propio docstring)
   sin ninguna validación defensiva propia, para un artefacto que se vende
   explícitamente como evidencia ante reguladores/aseguradoras — pero
   **ninguna de las dos veces logró emitir el JSON estricto pedido**: la
   primera vez antepuso prosa y el JSON quedó cortado a mitad de un string;
   la segunda vez (tras reforzar el parser, ver abajo) respondió en prosa
   pura, sin ningún bloque JSON. Se documenta como limitación real de
   fiabilidad de formato del backend de CLI (no es una alucinación de
   contenido — el hallazgo en sí es correcto y coherente las dos veces —,
   es que el modelo no siempre obedece "responde ÚNICAMENTE con JSON" en la
   ruta de CLI). El módulo degrada con gracia en ambos casos: no crashea, no
   inventa una lista vacía silenciosa — devuelve `respuesta_cruda_no_parseable`
   con el texto real y un `trace` explicando qué pasó, mismo patrón que el
   fallback de `agents/priorizacion.py::_parsear_json`.

**Bug real encontrado y arreglado en el camino (no en `revision_ia.py`, en
el helper compartido `agents/_llm.py::_call_via_cli`):** al probar con
`api/main.py` (32KB en ese momento) como único archivo, `claude -p
<mensaje-larguísimo>` falló con
`WinError 206: El nombre del archivo o la extensión es demasiado largo`.
Causa raíz confirmada (no solo un timeout, un límite real de Windows):
`_call_via_cli` pasaba `user_message` como argumento posicional de
`subprocess.run`, y `CreateProcess` en Windows tiene un límite de ~32.767
caracteres para la línea de comando completa. Ningún otro agente lo había
disparado antes porque todos envían resúmenes/JSON de hallazgos, nunca el
contenido crudo de un archivo de código. **Fix aplicado:** `_call_via_cli`
ahora pasa `user_message` por stdin (`subprocess.run(cmd, input=user_message, ...)`)
en vez de por `argv` — confirmado con una prueba directa de `claude -p` con
un payload de 40.000 caracteres por stdin (sin el flag de prompt
posicional): no hay error de línea de comando, la CLI sí lee el prompt de
stdin. Tras el fix, la corrida combinada `jwt_auth.py` + `api/main.py`
(punto 2 arriba) corrió sin problema. Este fix beneficia a CUALQUIER agente
futuro que envíe payloads grandes por el fallback de CLI, no solo a
`revision_ia.py`.

**Mejora real aplicada al parser durante la prueba:** `_parsear_hallazgos()`
ahora también intenta aislar el primer `[` hasta el último `]` del texto
completo si el `json.loads()` directo falla (para el caso de prosa +
```json antepuesto) — no resolvió el caso de prosa pura sin JSON (punto 3
arriba), pero sí es una recuperación real para el caso más común de
"preámbulo explicativo + bloque JSON válido".

**No se hizo en esta corrida (fuera de alcance, quedó documentado en el
propio módulo):** no se agregó un endpoint HTTP (`POST /reports/revision-ia`)
ni se corrió con `ANTHROPIC_API_KEY` real (no configurada en este entorno) —
la ruta de API directa probablemente sea más confiable en el formato JSON
estricto que la CLI (mismo patrón que motiva HANDOFF.md Item 1: la CLI es
para desarrollo/demo, la key real es para producción). No se tocó
`orchestrator/graph.py` — decisión reasoned arriba, no es un nodo.

**Archivos de esta corrida:** `agents/revision_ia.py` (nuevo),
`agents/_llm.py` (fix de `_call_via_cli`, stdin en vez de argv).

## Corrida 7 (2026-07-19) — Item 4: segundo target de evaluación (DVWA), ground truth real y eval en vivo

Objetivo: `eval/ground_truth.yaml` solo cubría Juice Shop — medir contra un único
target de laboratorio arriesga sobreajustar el sistema/prompts a sus
particularidades sin que nadie lo note. Este ítem agrega DVWA como target
independiente, con su propia ground truth, y corre una evaluación real contra
ella (no simulada).

**Verificación previa (antes de asumir nada, según HANDOFF.md):**
- `eval/run_eval.py` **ya soportaba** `--ground-truth <archivo>` (línea
  344-348 del script, default `eval/ground_truth.yaml`) — HANDOFF.md lo
  marcaba como "unconfirmed". Se confirmó leyendo el código: no hacía falta
  ningún cambio, el script ya no está hardcodeado a Juice Shop. No se tocó
  `run_eval.py`.
- `docker ps` al empezar: sin contenedores corriendo — los tres agentes
  concurrentes (items 3/5/6) ya habían limpiado los suyos.

**Target levantado:** DVWA (`docker run --rm -d --name vigia-eval-dvwa -p 8078:80
vulnerables/web-dvwa`), puerto elegido para no chocar con 3050/48173/48174 ya
en uso por otra sesión. Imagen resultó ser DVWA v1.10 *Development* con
`security=low` seteado por defecto en el login.

**Ground truth nueva:** `eval/ground_truth_dvwa.yaml`, 11 vulnerabilidades
(`DVWA-001..011`), mismo schema exacto que `eval/ground_truth.yaml` (id, type,
location, severity, description), documentadas a partir del catálogo oficial
público de módulos de DVWA (README/wiki del proyecto
`digininja/DVWA`): SQL injection (normal y ciega), command injection, CSRF,
file inclusion, file upload, XSS reflejado/almacenado/DOM, brute force
(broken_authentication) y weak session IDs. Nada inventado — cada entrada
corresponde a un módulo real de `/vulnerabilities/<modulo>/`.

También se documentó `eval/ground_truth_webgoat.yaml` (11 entradas,
`WEBGOAT-001..011`, catálogo oficial de lecciones de `WebGoat/WebGoat`:
SQLi, XSS reflejado/almacenado, dos variantes de broken access control, fallas
de autenticación, deserialización insegura, XXE, SSRF, path traversal,
criptografía débil) para dejar el tercer target listo — pero la corrida en
vivo contra WebGoat **no se pudo completar esta sesión**, ver "Blocker" abajo.

**Intento de autenticación real en DVWA:** se intentó loguear (`admin`/`password`)
y correr `setup.php` (crear/reset DB) vía `curl` con manejo de cookies y token
CSRF, para poder llegar a los módulos reales bajo `/vulnerabilities/`. El login
inicial redirige a `setup.php` (DB no inicializada); el POST de creación de la
base de datos devuelve 302 pero deja un `PHP Notice: Constant
DVWA_WEB_PAGE_TO_ROOT already defined` en el log de Apache
(`docker logs vigia-eval-dvwa`) y la sesión se pierde en la siguiente petición
(vuelve a pedir login) — un bug real de esta imagen concreta (`vulnerables/web-dvwa`,
no mantenida oficialmente hace años, PHP7 con notices no fatales que rompen su
propio flujo de setup en este entorno). Se decidió no seguir peleando con la
imagen y escanear sin sesión autenticada — limitación real, no simulada, y
consistente con lo que ya advertía HANDOFF.md sobre escáneres de caja negra
sin credenciales.

**Escaneo real:** Nuclei v3.11.0 (10.538 plantillas cargadas) contra
`http://127.0.0.1:8078` (nota: usar `127.0.0.1` en vez de `localhost` fue
necesario — con `localhost` Nuclei reportó
`Skipped ... found unresponsive permanently: cause="no address found for host"`,
un problema de resolución de nombre específico de este host/entorno, no del
target). 22 detecciones brutas en 9.8s. Ninguna cayó dentro de
`/vulnerabilities/*` (esperado, están detrás de login que no se pudo
completar). Curadas a 6 findings evaluables (se colapsaron 10 hits repetidos
de `http-missing-security-headers` en uno solo, y se excluyeron 6 detecciones
puramente de reconocimiento sin categoría de vulnerabilidad asociada —
`waf-detect`, `tech-detect`, `fingerprinthub-web-fingerprints`, `robots-txt`,
`robots-txt-endpoint`): credenciales por defecto válidas admin/password
(template `dvwa-default-login`, crítico — real y explotable), listado de
`/config/` expuesto, `.gitignore`/`README.md` expuestos, cookies sin
`Secure`/`HttpOnly`/`SameSite=Strict`, cabeceras de seguridad HTTP ausentes.
Guardado en `eval/dvwa_findings.json`.

**Resultado real de `eval/run_eval.py --ground-truth eval/ground_truth_dvwa.yaml
--findings eval/dvwa_findings.json`:**

```
True Positives      0
False Positives     6
False Negatives     11
Precision           0.00%
Recall              0.00%
F1-score            0.00%
```

**Lectura honesta del resultado:** 0% de recall y precisión no es un bug del
eval ni de la ground truth — es la señal real de que un escaneo Nuclei
puramente de caja negra, sin autenticación, no alcanza ninguna de las 11
vulnerabilidades intencionales de DVWA (todas viven detrás de
`/vulnerabilities/*`, que requiere login). Lo que sí encontró Nuclei
(credenciales por defecto, exposición de archivos, cabeceras/cookies débiles)
es real pero pertenece a una categoría distinta de "vulnerabilidad de
aplicación" que la ground truth no cubre, así que cuenta correctamente como
false positive contra este catálogo — no es que el matcher esté roto. Esto es
el mismo patrón de limitación que Item 2 documentó para el AJAX Spider de ZAP:
el pipeline necesita login/sesión wireada para targets autenticados, sea DVWA,
WebGoat o Juice Shop, si se quiere medir recall real contra sus catálogos
completos. Sin esto, cualquier mejora de recall en Juice Shop (que si tiene
login resuelto en otros ítems) seguiría sin decir nada sobre el comportamiento
del sistema contra un target sin sesión — que es justo el riesgo de
sobreajuste que este ítem buscaba exponer, y lo expuso.

**Blocker real, no resuelto:** a mitad de sesión Docker Desktop se cerró solo
otra vez (mismo problema ya conocido y documentado en HANDOFF.md — "Docker
Desktop también se ha cerrado solo varias veces en esta máquina"). Se
relanzó (`Start-Process Docker Desktop.exe`) y se esperó a que el daemon
volviera a responder para poder levantar WebGoat y correr su escaneo en vivo.
Si este archivo no tiene una sección de resultados de WebGoat más abajo, es
porque Docker no volvió a tiempo dentro de esta sesión — la ground truth
(`eval/ground_truth_webgoat.yaml`) ya quedó lista para cuando se pueda
levantar el contenedor (`docker run --rm -d -p 8080:8080 -p 9090:9090
webgoat/webgoat`) y correr `eval/run_eval.py --ground-truth
eval/ground_truth_webgoat.yaml --findings <hallazgos reales>`.

**Cleanup:** contenedor `vigia-eval-dvwa` detenido (`docker stop`) al
terminar, corría con `--rm` así que no queda nada residual. Si Docker sigue
sin responder al cerrar esta sesión, el contenedor de todas formas no
sobrevive a un reinicio del daemon (ver arriba), así que no hay riesgo de
dejarlo huérfano.

## Corrida 6 (2026-07-18/19) — Item 5: listener de CertStream (vigilancia continua real)

Implementado: `api/certstream_listener.py` (nuevo módulo) + dos funciones nuevas
en `tools/antisuplantacion.py` (`generate_domain_variants`, `registrable_domain`)
+ wiring en el `lifespan` de `api/main.py`. Sigue el patrón de
`api/scheduler.py` al pie de la letra: daemon thread dentro del mismo proceso
uvicorn, arrancado/parado desde `lifespan`, `get_conn()` por operación, nunca un
proceso/servicio separado (decisión ya tomada por el usuario, ver HANDOFF.md
Item 5).

**Decisión de diseño clave, no obvia:** el motor de matching NO reimplementa
typosquatting/homoglyph desde cero ni llama al binario `dnstwist` por
subprocess (como sí hace `run_dnstwist()` para el pipeline bajo demanda,
sección 3.2). En vez de eso, `generate_domain_variants()` importa
`dnstwist.Fuzzer` directamente en proceso — descubierto durante esta sesión
que el paquete pip `dnstwist` (ya instalado, versión 20250130) expone esa
clase como librería normal además del binario CLI. Esto evita lanzar un
subprocess (y peor, resolución DNS real) por cada dominio que aparece en el
stream global de Certificate Transparency, que puede ser docenas por segundo.
Cada dominio observado en CertStream se reduce a su apex registrable con
`dnstwist.domain_tld()` (maneja sufijos compuestos reales como `.com.co`,
`.co.uk` — no un `split('.')` ingenuo) y se busca por igualdad de cadena
contra un mapa precomputado `{variante: (tenant_id, asset_id, dominio_base)}`
de todos los activos tipo `dominio` de todos los tenants, reconstruido cada
`VIGIA_CERTSTREAM_REFRESH_MINUTES` (default 30).

Cuando hay match (y no es el propio dominio legítimo del tenant), se escribe
una fila `findings` real (`tipo='dominio_variante_certstream'`,
`severidad='high'`, `confirmado=0`) enlazada a una fila `scans` sintética —
igual que hace `api/scheduler.py::run_scan_cycle_once` para cada ciclo de
recon pasivo, porque el schema exige `scan_id` NOT NULL y no hay un "scan"
real detrás de un evento de streaming.

### Qué se verificó EN VIVO de verdad (y qué no)

**Verificado en vivo:** el arranque del listener dentro de la API real.
Se levantó `uvicorn api.main:app` en un puerto de prueba (48181), con logging
a nivel INFO forzado para observarlo, y se confirmó en el log real del
proceso:

```
INFO vigia.certstream Mapa de variantes CertStream (re)construido: 0 variante(s) sobre 0 dominio(s) vigilados
INFO vigia.certstream Listener de CertStream iniciado (wss://certstream.calidog.io)
INFO websocket Websocket connected
INFO certstream Connection established to CertStream! Listening for events...
```

`GET /health` respondió 200 normalmente con el listener corriendo en paralelo
— confirma que arrancarlo no bloquea ni tumba el proceso de la API.

**NO verificado en vivo (hallazgo real, no maquillado):** el feed público
histórico `wss://certstream.calidog.io` (Cali Dog Security) **acepta el
handshake de websocket pero no transmite ningún mensaje**. Se probó de forma
aislada (fuera de la app, con `websocket-client` puro) escuchando 35 segundos
seguidos: conexión abierta, cero mensajes, cero errores — un feed de CT logs
real produce decenas de mensajes por segundo, así que "conectado pero mudo"
solo se explica por un servicio descontinuado, no por mala suerte de timing.
Confirmado también por búsqueda web: el servicio gratuito de Cali Dog Security
fue descontinuado; las alternativas activas en 2026
(`certstream-server-go` de d-Rickyy-b, `go-certstream` de LeakIX,
`certstream-server-rust`) son todas **self-hosted**, ninguna ofrece un
endpoint público gratuito hoy. `VIGIA_CERTSTREAM_URL` es configurable
exactamente por esto — apuntar a una instancia propia (Docker) es el paso
pendiente para vigilancia realmente en vivo, documentado como próximo paso
abajo.

**Verificado en vivo, de punta a punta, sustituyendo solo el feed
(no el resto de la cadena):** matching + escritura real a la base de datos.
Contra la API real corriendo (no un mock), con un tenant y un dominio de
prueba reales creados vía `POST /auth/register` + `POST /assets`
(`certstream-test@vigia.local` / dominio `miempresatest.com`), se alimentó al
callback real (`procesar_mensaje_certstream`, la misma función que
`certstream.listen_for_events` invocaría con datos reales) un mensaje
sintético con la forma exacta de un mensaje real de CertStream
(`message_type: "certificate_update"`, `data.leaf_cert.all_domains`, igual al
formato documentado por `x0rz/phishing_catcher` y por el paquete `certstream`),
usando una variante de dominio (`eiempresatest.com`) generada por el propio
motor dnstwist — no inventada a mano:

- El mapa de variantes se reconstruyó con el activo real recién creado (9,486
  variantes generadas para `miempresatest.com`).
- El match se encontró correctamente (dominio exacto y su subdominio `www.`,
  ambos resueltos al mismo apex).
- Se escribieron 2 filas `findings` reales + 2 filas `scans` sintéticas.
- Confirmado además vía `GET /findings` con el JWT real del tenant de
  prueba — el hallazgo aparece en la API, no solo en la base de datos directa.

Casos negativos también verificados explícitamente (sin mocks, contra la
misma base real): el propio dominio del cliente reapareciendo en CT (renovación
normal de certificado) **no** genera un finding; un dominio sin relación
tampoco; un mensaje que no es `certificate_update` (ej. `heartbeat`) no
crashea el callback. Los tres casos pasaron.

**Degradación graceful, verificada explícitamente:** se simuló
`ImportError` en el import de `certstream` (sin desinstalarlo, interceptando
`builtins.__import__`) y se confirmó que `start_certstream_listener()` sigue
sin lanzar ninguna excepción, crea el thread, el thread se apaga solo
(`is_alive() == False` un segundo después) tras loguear la advertencia, y el
resto de la API sigue arriba — el mismo criterio de `tools/_shared.py` para
herramientas no instaladas, aplicado aquí sin subprocess de por medio.

### Dependencia nueva

`certstream` (PyPI) instalado localmente para esta sesión
(`pip install certstream`, trae `websocket-client` y `termcolor`). **A
propósito NO se agregó a `pyproject.toml`** — sigue la misma convención ya
establecida por `dnstwist`/`sherlock-project` en este proyecto: herramientas
de seguridad opcionales se documentan inline (`pip install X` en el
docstring del módulo que las usa) y degradan con gracia si faltan, en vez de
ser dependencias duras del paquete. `pyproject.toml` no se tocó.

### Próximo paso concreto (no resuelto en esta sesión)

Para vigilancia realmente en vivo (no solo el mecanismo verificado), levantar
una instancia propia de `certstream-server-go` o `certstream-server-rust` vía
Docker (ver enlaces en el docstring de `api/certstream_listener.py`) y apuntar
`VIGIA_CERTSTREAM_URL` a ella. El código del listener ya soporta cualquier URL
de feed compatible con el formato de mensaje de CertStream sin cambios —
la variable de entorno ya existe, falta el feed real.

## Corrida 5 (2026-07-18) — Item 3: Semgrep/Trivy/Grype probados de verdad, wireados al pipeline

Los tres wrappers de `tools/scan.py` (`run_semgrep`, `run_trivy_image`, `run_grype`) nunca se habían corrido contra nada real en ninguna sesión previa (ver HANDOFF.md, Item 3). Esta corrida los probó y los conectó.

### Bug real encontrado y arreglado antes de poder confiar en los resultados

`tools/_shared.py::run_command()` llamaba a `subprocess.run(..., text=True, ...)` sin especificar `encoding`. En Windows eso usa la codificación de la consola (`cp1252`), no UTF-8. La primera corrida de `run_semgrep('.', config='auto')` reventó un hilo interno de `subprocess` con `UnicodeDecodeError` al intentar decodificar la salida de progreso de Semgrep (que sí es UTF-8) — el proceso terminaba con `returncode=0` pero `result.raw.stderr` quedaba en `None` en vez de contener la salida real. Esto habría afectado silenciosamente a cualquier herramienta cuya salida (stdout o stderr) tuviera caracteres fuera de cp1252 (barras de progreso, emojis, tildes en rutas, etc.) — no es específico de Semgrep, es un bug latente en el wrapper compartido por Nuclei/ZAP/Trivy/Grype/Semgrep/osv-scanner. Arreglado agregando `encoding="utf-8", errors="replace"` a la llamada de `subprocess.run`. Verificado: la misma corrida después del fix devolvió `stderr` real (1996 caracteres) en vez de `None`.

### Semgrep — SAST contra el propio repo de Vigia

Corrida real: `run_semgrep('.', config='auto')` desde la raíz del repo.

- **`--config auto` sí necesita red** (descarga reglas del registro remoto de Semgrep) — confirmado con `--verbose`: `running 1074 rules from 1 config remote-registry_0`.
- **Resultado real: `Ran 504 rules on 83 files: 0 findings.`** (504 tras filtrar por lenguaje detectado en el repo — Python, TS/TSX, YAML, HTML; los 83 archivos escaneados son exactamente los que trackea git, `tools/vendor/exploitdb/*.csv` se saltó por `.semgrepignore`).
- Tiempo: ~32-100s dependiendo de si las reglas ya estaban en caché local.
- **Lectura honesta:** cero hallazgos no es sospechoso aquí — el repo es relativamente joven, sin patrones obvios de inyección/secrets hardcodeados que Semgrep's ruleset genérico (`auto`) suele atrapar primero (SQL crudo, `eval`, secrets en claro, etc.). No se debe leer como "el código está libre de bugs", solo como "Semgrep con reglas genéricas no encontró nada obvio" — sigue pendiente correr con configs más específicos (`p/owasp-top-ten`, `p/secrets`) para una prueba más agresiva.

### Trivy y Grype — instalación y prueba contra imagen con CVEs conocidos

Ninguno estaba instalado. `scoop install trivy grype` corrió limpio (`trivy` 0.72.0, `grype` 0.116.0). Docker Desktop ya estaba corriendo (`docker ps` respondió vacío pero sin error).

Target elegido: `node:14` (EOL, decenas de CVEs documentadas en su base Debian) — `docker pull node:14` bajó sin problema (~520MB en caché junto a las imágenes ya usadas de Juice Shop/ZAP).

**`run_trivy_image('node:14')`** (vía el wrapper real, no la CLI a mano): primera corrida incluyó la descarga de la base de datos de vulnerabilidades de Trivy (`mirror.gcr.io/aquasec/trivy-db:2`, ~50 MiB) + detección de OS (`debian 10.13`, 413 paquetes) + análisis de dependencias Node. **227s totales, `returncode=0`, 1442 hallazgos parseados correctamente** desde el JSON (`Results[].Vulnerabilities[]`, tal como espera `run_trivy_image`). Desglose por severidad: `CRITICAL=22, HIGH=564, MEDIUM=730, LOW=122, UNKNOWN=4`. Trivy además advirtió explícitamente que Debian 10 ya no recibe soporte de seguridad de la distribución — coherente con elegir `node:14` a propósito como target EOL.

**`run_grype('docker:node:14')`** (vía el wrapper real): requirió primero `grype db update` (la base de datos no existía todavía, `grype db status` reportó `database does not exist` antes de esto — otro paso de instalación real, no solo el binario). Tras actualizar la DB, la corrida tomó 131s, `returncode=0`, **2306 hallazgos parseados correctamente** desde `matches[]`. Desglose por severidad: `Critical=41, High=566, Medium=631, Low=115, Negligible=945, Unknown=8`.

**Ambos wrappers confirmados funcionando de punta a punta contra un target real**: binario detectado (`require_binary`), comando ejecutado, JSON parseado sin excepciones, conteos de severidad coherentes con lo esperado de una imagen Debian 10 EOL con Node 14. Grype reporta ~60% más hallazgos que Trivy sobre la misma imagen (2306 vs 1442) — ambos son fuentes de vulnerabilidades reales pero con bases de datos y heurísticas de matching distintas; no se investigó el solape/discrepancia exacta en esta sesión (posible trabajo futuro: usarlos como fuentes cruzadas en `Verificación` en vez de reportar ambos crudos).

### Decisión de diseño: cómo se conectaron al pipeline

`agents/escaneo.py` solo recibía `scope.dominios` (una lista de URLs/hosts) — Semgrep opera sobre una ruta de filesystem, Trivy/Grype sobre un nombre de imagen de contenedor. Ninguno de los tres encaja en "URL a escanear".

**Opción considerada y descartada: un nodo/agente nuevo** (`agente_sast_sca` separado en el grafo). Se descartó porque Semgrep/Trivy/Grype siguen siendo, conceptualmente, la misma capa que Nuclei/ZAP: herramientas deterministas de bajo nivel que producen hallazgos crudos sin razonamiento de por medio, protegidas por la misma puerta de autorización (`autorizacion_firmada`, sección 8.1). Crear un nodo nuevo solo estaría justificado si el *razonamiento* u orquestación fuera distinto (no lo es) — lo único que cambia es el *tipo de objetivo*.

**Opción implementada: extender `Scope`.** Se agregaron dos campos opcionales a `orchestrator/state.py::Scope` (y su espejo `ScopeIn` en `api/main.py`):

- `codigo_paths: list[str]` — rutas locales de código fuente para Semgrep.
- `imagenes: list[str]` — referencias de imagen de contenedor para Trivy + Grype.

`agents/escaneo.py::node()` ahora, después del loop existente sobre `scope.dominios` (Nuclei + ZAP), agrega dos loops más: uno sobre `scope.codigo_paths` (llama `run_semgrep`) y otro sobre `scope.imagenes` (llama `run_trivy_image` y `run_grype`, este último con el target `docker:<imagen>` que espera la CLI de Grype). Mismo patrón de manejo de errores que ya existía (`try/except ToolExecutionError`, degrada a `errores` en vez de tumbar el request), mismo `trace_log`, misma puerta de autorización de arriba del nodo (nada de esto se ejecuta si `autorizacion_firmada` no es `true`). El conteo de "objetivos" en el resultado del nodo ahora suma dominios + rutas de código + imágenes.

**Wireado end-to-end real, no solo interno:** `ScopeIn` en `api/main.py` expone `codigo_paths`/`imagenes` en el payload de `POST /scan` y `POST /scan/activo`, así que un cliente real (o el propio pipeline en modo "escanéate a ti mismo") puede pedir un escaneo de código sin tocar código Python. Semgrep queda conectado de punta a punta contra el propio repo: `codigo_paths: ["."]` dispara exactamente la misma llamada probada arriba.

**Pendiente, no bloqueante:** Trivy/Grype están wireados con el mismo mecanismo pero no se probaron todavía a través del grafo completo (`orchestrator/graph.py`) contra una imagen real de un cliente — la prueba de esta sesión fue directa contra `tools/scan.py`, no vía `POST /scan`. Antes de ofrecerlo a un cliente real habría que decidir de dónde sale `scope.imagenes` en la práctica (¿el cliente da el nombre de su imagen en un registry? ¿Vigia necesita acceso a ese registry?) — eso es una pregunta de producto, no técnica, y no se resolvió en esta sesión.


## Corrida 4 (2026-07-18) — login real + AJAX Spider, límite real encontrado

Con Docker ya funcionando de una sesión anterior, se probaron dos mejoras reales sobre la corrida 3:

1. **Login real antes del escaneo activo.** Se registró un usuario de prueba en Juice Shop (`POST /api/Users`), se inició sesión (`POST /rest/user/login`) para obtener un JWT real, y se agregó `run_zap_active_scan(bearer_token=...)` (`tools/scan.py`) que inyecta `Authorization: Bearer <token>` en cada petición de ZAP vía `-config replacer.*` — sin necesidad de un script de login dentro de ZAP. **Resultado:** exactamente los mismos 15 hallazgos que sin login. El token no cambió nada porque el problema no era la autenticación — era que el crawler nunca llegaba a las rutas donde importaba estar autenticado.
2. **AJAX Spider (`-j`, navegador headless real) para cubrir la SPA.** Juice Shop es una Single Page Application en Angular: casi toda su navegación pasa por JavaScript/llamadas a API, no por `<a href>` en HTML estático, que es todo lo que el spider clásico de ZAP sabe seguir. Se intentó dos veces, subiendo el margen de timeout cada vez (25 min, luego 35 min) — **ambas veces el proceso excedió el presupuesto sin terminar**. El contenedor seguía vivo y trabajando cuando se cortó (confirmado con `docker ps`), no es que se colgara — el navegador headless dentro del contenedor simplemente necesita más tiempo del que se le dio.

**Conclusión honesta:** cubrir de verdad las rutas de una SPA con ZAP es un problema real de infraestructura/tiempo, no algo que se resuelva con una llamada más. El camino correcto no es seguir subiendo el timeout a ciegas — es correr el AJAX Spider como un job de fondo de larga duración (no bloqueante, con su propio ciclo de vida) y/o investigar por qué el navegador headless tarda tanto en este host específico (Docker Desktop + WSL2 en Windows puede tener overhead adicional de virtualización de GPU/renderizado que no tendría un host Linux nativo). Las métricas de la corrida 3 (9.09% recall) siguen siendo el número real vigente — no se infla ni se repite un "casi funcionó" como si fuera un resultado.

**Efecto secundario útil de esta corrida:** al intentar usar IA real por primera vez en la sesión, se encontró que `ANTHROPIC_API_KEY` nunca estuvo configurada en todo el proyecto — todos los agentes de razonamiento (priorización, remediación, reportería) corrieron siempre en modo de emergencia determinista. Se agregó un fallback en `agents/_llm.py::call_claude()`: si no hay API key pero el binario `claude` (Claude Code CLI) está instalado y con sesión iniciada, se invoca `claude -p` en su lugar — mismo contrato, cero costo adicional, probado funcionando de verdad. Sigue pendiente configurar una API key real para el servicio en producción (el fallback de CLI es para desarrollo/demo, no para multi-tenant real).

## Corrida 3 (2026-07-17/18) — ZAP arreglado y corriendo de verdad

Después de la corrida 2 (ver abajo), el usuario instaló Docker Desktop. Al intentar usar ZAP por primera vez aparecieron dos bugs reales más, encontrados y arreglados en esta sesión:

1. **`agents/escaneo.py` nunca llamaba a ZAP.** El docstring decía "Ejecutas Nuclei y OWASP ZAP" pero el código solo invocaba `run_nuclei` — `run_zap_baseline` existía en `tools/scan.py` pero ningún agente lo usaba. Se agregó la llamada real.
2. **`run_zap_baseline` nunca devolvía hallazgos.** No montaba ningún volumen Docker, así que el reporte JSON que ZAP escribe en `/zap/wrk/zap-report.json` quedaba encerrado dentro del contenedor descartable (`--rm`) y se perdía. La función siempre retornaba `findings=[]` sin importar qué encontrara ZAP. Se arregló montando un directorio temporal del host y parseando el JSON real al terminar.
3. **`localhost:3000` no resuelve dentro del contenedor de ZAP.** Classic Docker networking gotcha: `localhost` dentro de un contenedor apunta al contenedor mismo, no al host de Windows donde corre Juice Shop. El primer intento post-fix devolvió `Connection refused`. Se arregló reescribiendo `localhost`/`127.0.0.1` a `host.docker.internal` (con `--add-host host.docker.internal:host-gateway`) solo para targets locales — un dominio real de internet no pasa por esa rama.

Con los tres arreglos, se corrió:
- **ZAP baseline** (pasivo): 10 hallazgos reales (CSP, CORS, cabeceras de seguridad faltantes) — confirma que la integración funciona, pero baseline es intencionalmente no-intrusivo (solo spidering + reglas pasivas), así que no ataca parámetros.
- **ZAP full-scan activo** (5 min de presupuesto): 15 hallazgos reales, incluyendo dos directamente relevantes a la ground truth: *Backup File Disclosure* y *Bypassing 403*, ambos en `/ftp/` — evidencia real de archivos de respaldo expuestos y de un control de acceso evadible ahí, que es exactamente lo que documentan VULN-007 y VULN-008.

### Métricas reales (`eval/run_eval.py` contra `eval/live_run_findings.json`)

| Métrica | Corrida 2 (solo Nuclei) | Corrida 3 (Nuclei + ZAP full-scan) |
|---|---|---|
| True Positives | 0 | 1 |
| False Positives | 0 | 4 |
| False Negatives | 11 | 10 |
| Precisión | 0.00% | 20.00% |
| Recall | 0.00% | 9.09% |

El único true positive: **VULN-007** (`sensitive_data_exposure` en `/ftp/`) emparejado contra el hallazgo real de *Backup File Disclosure*. VULN-008 (`path_traversal`) tenía un hallazgo de tipo correcto (*Bypassing 403*) pero la ubicación reportada por ZAP (`/%2e/ftp/.%5C..`) no fue lo bastante similar a la ubicación de la ground truth (`/ftp/:file`) para el matcher de `run_eval.py` — cuenta como falso negativo real, no se forzó el match.

**Por qué el recall sigue bajo (9%, no más):** los 5 minutos de presupuesto de escaneo activo no alcanzaron para que ZAP explorara y atacara las rutas específicas de SQLi (`/rest/user/login`), XSS (`/rest/products/search`), IDOR (`/rest/basket/{id}`) o el flujo de JWT. Esto es esperable — Juice Shop está diseñado para requerir exploración dirigida (login, navegar el catálogo, etc.) antes de que esas rutas sean alcanzables para un crawler genérico. Con más tiempo de escaneo activo (15-30 min) o un guion de autenticación previo (login automático antes de spidering), el recall subiría más — es el siguiente paso concreto, no una limitación estructural del pipeline.

## Corrida 2 (2026-07-15) — post-fix de timeout, solo Nuclei

`POST /scan` respondió 200 OK en 31s, los 7 agentes corrieron limpio, cero excepciones. 0% de recall porque en ese momento Docker/ZAP no estaban disponibles y Nuclei (plantillas de CVEs/misconfiguración genéricas) no cubre las fallas de lógica de negocio de Juice Shop.

## Corrida 1 (2026-07-15) — encontró el bug de timeout original

`run_nuclei()` corrió con su timeout por defecto (900s) sin restricción de plantillas contra un Nuclei recién instalado que todavía sincronizaba su repositorio de plantillas. A los 900s, `subprocess.TimeoutExpired` se propagó sin capturar y tumbó la petición completa con un 500. Arreglado con `ToolExecutionError`/`ToolTimeoutError` (commit `cbd0e53`).

## Conclusión honesta acumulada

El pipeline (orquestación, gate de autorización, trazabilidad, manejo de errores, y ahora la integración real con Nuclei + ZAP) funciona de punta a punta sin bugs de ejecución. El recall de 9% es real y mejorable con más presupuesto de tiempo de escaneo activo — no es una limitación de diseño. La prueba más contundente de valor real hasta ahora es cualitativa: ZAP encontró archivos de respaldo expuestos reales en un directorio real, sin que nadie le dijera dónde buscar.

## Próximo paso concreto para subir el recall

1. Subir el presupuesto de tiempo de `zap-full-scan.py` (`-m`) de 5 a 20-30 minutos para una corrida de referencia real.
2. Agregar un script de autenticación previo al spidering (ZAP soporta scripts de login) para que el crawler alcance rutas que requieren sesión iniciada.
3. Registrar esta corrida en `eval/failure_log.md` como el primer caso real con progreso medible del loop de mejora continua (sección 8.2 del plan).
