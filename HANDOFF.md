# Handoff — próxima sesión

Este archivo existe para que el próximo agente (u otra sesión tuya) entienda en 5 minutos dónde está el proyecto de verdad, sin releer todo el historial de commits. Sigue el patrón de `NEXT-EMPLOYEE.md` en `paperclip` — instrucciones concretas, no aspiracionales.

**Regla de oro antes de tocar nada:** este proyecto solo escanea activamente contra objetivos autorizados por escrito o aplicaciones de laboratorio (OWASP Juice Shop, DVWA). Ver `plan-proyecto-ciberseguridad.md` sección 0.

## Estado real ahora mismo (2026-07-18)

Todo lo de abajo está **probado en vivo**, no solo escrito:

- Pipeline LangGraph completo (`orchestrator/` + `agents/`), con IA real conectada (ver "Item 1" abajo).
- Backend multi-tenant (`api/`, `db/`, `auth/`): registro, login, dominios, historial de escaneos, hallazgos.
- Frontend real (`frontend/`, React+Vite+TS): login + dashboard con nivel de riesgo, inventario de dominios, actividad.
- Nuclei + ZAP (baseline y activo) funcionando de verdad vía Docker, con los bugs reales que aparecieron ya arreglados (timeouts, volumen no montado, `localhost` vs `host.docker.internal`) — ver `eval/live_run_report.md` para el detalle completo de cada corrida.
- Escaneo recurrente (`api/scheduler.py`) — cada N horas, recon pasivo sobre todos los dominios activos de todos los tenants.
- Escaneo activo asíncrono (`POST /scan/activo` + `GET /scans/{id}`) — no bloquea el request, corre en background thread.
- Repo público en GitHub, historial limpio de datos reales de terceros (se usó `git filter-repo` + force-push).

## Cómo levantar todo para verlo (puertos poco comunes, no chocan con otros proyectos)

```bash
# Backend
cd D:/freestyle/ciberseguridad
CORS_ORIGINS="http://localhost:48174" py -m uvicorn api.main:app --port 48173

# Frontend (en otra terminal)
cd D:/freestyle/ciberseguridad/frontend
echo "VITE_API_URL=http://localhost:48173" > .env.local
npx vite --port 48174 --strictPort
```

Abrir `http://localhost:48174`. Cuenta de demo ya creada en la corrida anterior: `demo@vigia.local` / `DemoVigia2026` (vive en `ciberseguridad.db`, gitignored — si no existe ese archivo, regístrate de cero, es gratis).

**Ojo con:** los servers lanzados en esta sesión mueren si Claude Code se reinicia o la sesión termina — para dejarlos corriendo de verdad hace falta un mecanismo persistente (no resuelto). Docker Desktop también se ha cerrado solo varias veces en esta máquina — si `docker ps` falla, ábrelo manualmente, no asumas que quedó prendido entre sesiones.

## Los 6 items que el usuario pidió, en orden — estado real

### Item 1 — Usar IA real en el pipeline ✅ HECHO esta sesión
`agents/_llm.py::call_claude()` ahora tiene dos backends: API directa (`ANTHROPIC_API_KEY`) o CLI de Claude Code (`claude -p`, sin costo, reusa la sesión autenticada de quien corre el proyecto) como fallback. **Probado con datos reales**: `priorizacion.py`, `remediacion.py` y `reporteria.py` los tres invocados directamente con hallazgos de ejemplo — el razonamiento es genuinamente bueno (conectó un hallazgo de backup expuesto con el riesgo específico de una pasarela de pagos, sin que se lo dijera explícitamente).

**Hallazgo real, no resuelto del todo:** la latencia de `claude -p` varía — una llamada tardó más de 180s y expiró. Se subió el timeout default a 300s y se hizo configurable (`VIGIA_CLAUDE_CLI_TIMEOUT`), pero no hay garantía de que 300s alcance siempre. Si esto se vuelve un problema real en producción, la solución correcta es la API key real (`ANTHROPIC_API_KEY`), no seguir subiendo el timeout de la CLI — la CLI es para desarrollo/demo, nunca para el servicio multi-tenant real con carga concurrente.

### Item 2 — Escaneo activo como job de fondo ✅ HECHO, ✅ el bloqueador de infraestructura de Corrida 12 se cerró de verdad en Corrida 17
Nuevo `POST /scan/activo` (requiere `autorizacion_firmada: true` explícito en el mismo request, a propósito más estricto que `POST /scan`) que arranca el escaneo activo en un `threading.Thread` y responde 202 de inmediato con `scan_id`. `GET /scans/{id}` consulta el estado (`corriendo`/`completado`/`error`) y los hallazgos cuando ya terminó. **Corrida 12 cerró un gap de seguridad real que un agente de revisión de IA (`agents/revision_ia.py`) había encontrado en este mismo endpoint**: no validaba que `target_url` fuera un asset del tenant. **Corrida 16 fue más allá**: registrar el asset ya no basta, ahora hace falta verificación real de propiedad (DNS TXT / archivo HTTP) salvo para localhost/IP privada — ver `tools/asset_verification.py` y `tests/test_asset_verification.py`.

**Corrida 12 (2026-07-19) — diagnóstico honesto de un bloqueador real, no resuelto en el momento:** el escaneo ZAP en sí (con AJAX Spider) excedió el presupuesto de tiempo repetidamente (3 corridas: 25min, 35min, 30min) contra Juice Shop real, incluso con el gate de autorización nuevo funcionando bien. Diagnóstico real (no una suposición): `docker top`/`docker exec ps aux` confirmó un Firefox headless real activamente renderizando pestañas — lento de verdad en ese host, no colgado. Se encontró además un bug real: al expirar el timeout de Python, el contenedor Docker queda huérfano (`--rm` no lo limpia porque solo actúa si el contenedor termina por sí mismo), y el directorio temporal del reporte ya estaba borrado para ese momento — un timeout era irrecuperable por diseño.

**Corrida 17 (2026-07-19) — ambos problemas de Corrida 12 cerrados de verdad, con Docker/ZAP real, no solo en el papel:**

1. **Contenedor huérfano + reporte irrecuperable (Bug 1): arreglado.**
   `tools/scan.py::_run_zap_script` ahora nombra el contenedor explícitamente
   (`--name vigia-zap-<scan_id>`), y en timeout consulta el estado real del
   contenedor (`tools._shared.docker_container_running`) antes de decidir:
   si sigue corriendo, lo detiene de verdad (`docker_force_remove_container`,
   nunca queda huérfano) y relanza el timeout real; si ya terminó (incluso
   dentro de una ventana de gracia corta para la carrera real que Corrida 12
   planteaba), recupera el reporte del workdir ANTES de borrarlo (ya no se
   usa `tempfile.TemporaryDirectory()`, que borraba el bind-mount en cuanto
   la excepción se propagaba). Cubierto por 7 tests deterministas
   (`tests/test_zap_timeout_cleanup.py`), sin depender de un ZAP real lento
   en cada corrida de CI.

2. **El giant blocking call (Bug 2): reemplazado por un daemon de ZAP + polling HTTP real, verificado en vivo.**
   Nuevo `tools/zap_api.py` conduce la API HTTP real de ZAP (`zap.sh -daemon`,
   sin el script de conveniencia `zap-full-scan.py`) en vez de un único
   `subprocess.run` con timeout fijo — arrancar cada fase (spider/AJAX
   spider/escaneo activo) es una llamada HTTP que responde de inmediato con
   un id, y el progreso se consulta con polls cortos y repetidos.
   `api/main.py::_correr_escaneo_activo_en_background` persiste cada
   checkpoint en `scans.reporte_final` de inmediato -- `GET /scans/{id}`
   ahora refleja progreso real ("[spider] Spider clásico: 56%") en vez de
   solo `corriendo` durante 20-35 minutos. **Verificado de punta a punta
   contra Juice Shop real, a través del endpoint HTTP real**: `POST
   /scan/activo` con `ajax_spider: true`, sondeado con `GET /scans/{id}` en
   llamadas HTTP separadas, mostró progreso real e incremental (spider
   56%→85%, AJAX Spider corriendo, escaneo activo iniciado) y terminó en
   `completado` con **631 hallazgos reales** persistidos — antes: 0, en los
   4 intentos de Corrida 12, porque el timeout siempre llegaba primero.
   Contenedor confirmado limpiado (`docker ps -a` vacío) tanto en éxito como
   en un camino de error forzado. Cubierto por 9 tests deterministas
   (`tests/test_zap_checkpointed_scan.py`).

**Dos bugs reales adicionales encontrados y arreglados durante la propia
verificación en vivo de Corrida 17** (no en el camino feliz mockeado): el
reloj del presupuesto de `minutes` arrancaba antes de que el daemon
estuviera listo (un arranque lento bajo contención real de Docker en esta
sesión, >120s confirmado, se comía todo el presupuesto sin escanear nada);
y un único `ReadTimeout` transitorio durante el AJAX Spider tumbaba el
escaneo completo aunque el daemon seguía vivo (ahora `tools/zap_api.py::_zap_get`
reintenta 2 veces con backoff). Detalle completo, incluyendo el hallazgo no
documentado en ningún lado obvio de que ZAP requiere el header `Host:
localhost:<puerto interno>` en cada llamada a su API sin importar el puerto
mapeado del host Docker, y que el namespace correcto del AJAX Spider es
`ajaxSpider` (no `spiderAjax`, que devuelve `no_implementor`), en
`eval/live_run_report.md` Corrida 17.

**Limitación real, no resuelta, documentada sin ocultar:** cada contenedor
daemon arranca "en frío" -- reinstala los add-ons de ZAP en cada escaneo
(mismo costo que ya pagaba el enfoque anterior, no empeorado ni mejorado
esta sesión). Un volumen persistente en `/home/zap/.ZAP` lo evitaría a
partir del segundo escaneo, pero compartirlo entre escaneos concurrentes de
tenants distintos sin diseñarlo con cuidado podría filtrar contexto entre
escaneos -- se dejó fuera de esta sesión a propósito. Tampoco hay
resumibilidad real across-restart del proceso de la API (si el proceso de
Vigia se cae a mitad de un escaneo, ese escaneo específico no se retoma
solo, aunque el contenedor sí se limpia en el próximo timeout que lo
detecte) -- necesitaría persistir `container_name`/scan id de ZAP en la
fila `scans` y un job de reconciliación al arrancar.

Los únicos números reales de recall/precisión autenticado-vs-no-autenticado
con AJAX Spider funcionando de punta a punta siguen pendientes de una
corrida contra `eval/ground_truth.yaml` (Corrida 17 verificó que el
mecanismo funciona y produce hallazgos reales, pero no corrió
`eval/run_eval.py` contra ellos — próximo paso natural, ahora sí viable sin
que el timeout llegue primero). Los números previos siguen siendo los de
Corrida 4 (9.09%/20% en ambos casos, spider clásico sin AJAX Spider).

### Item 3 — Probar Trivy/Grype/Semgrep de verdad ⬜ NO HECHO
Wrappers ya existen en `tools/scan.py` (`run_trivy_image`, `run_grype`, `run_semgrep`), nunca se corrieron contra nada real en ninguna sesión. Son el frente de "código y dependencias del cliente" — sin esto Vigia solo cubre la mitad de la superficie de ataque real de una pyme (la web pública, no su código fuente ni sus dependencias).

Cómo probarlos rápido y real:
- **Semgrep**: ya está instalado como dependencia Python del proyecto (`pip install semgrep` en `tools/scan.py`). Correr `run_semgrep('.', config='auto')` contra el propio repo de Vigia sería la prueba más rápida y honesta — encontraría fallas reales en nuestro propio código antes de ofrecérselo a un cliente.
- **Trivy/Grype**: requieren instalación aparte (`scoop install trivy grype` en Windows). Una vez instalados, correr `run_trivy_image` o `run_grype` contra una imagen Docker conocida con CVEs documentados (ej. una versión vieja de `node:14` tiene decenas) para confirmar que el parseo de resultados funciona antes de conectarlos al pipeline.
- Ninguno de los tres está wireado en `agents/escaneo.py` todavía — hay que decidir si van ahí (activo, requiere autorización) o si necesitan su propio nodo/agente porque operan sobre código fuente, no sobre una URL (`escaneo.py` hoy solo recibe `scope.dominios`, no una ruta de código).

### Item 4 — Más targets de evaluación (DVWA, WebGoat) 🟡 PARCIAL esta sesión (DVWA en vivo, WebGoat bloqueado por Docker)
`eval/ground_truth.yaml` solo tenía las 11 vulnerabilidades de Juice Shop. Medir contra un solo target de laboratorio puede hacer que el sistema (o los prompts) se sobreajusten a sus particularidades sin que nadie lo note.

**Hecho de verdad esta sesión:**
- `eval/run_eval.py` **ya soportaba `--ground-truth <archivo>`** (no estaba hardcodeado a Juice Shop como decía este mismo archivo antes — se verificó leyendo el código, no se asumió). No hizo falta tocar el script.
- `eval/ground_truth_dvwa.yaml` (11 entradas, `DVWA-001..011`) documentada a partir del catálogo público oficial de módulos de DVWA, mismo schema que la ground truth de Juice Shop.
- `eval/ground_truth_webgoat.yaml` (11 entradas, `WEBGOAT-001..011`) documentada igual, a partir del catálogo público oficial de lecciones de WebGoat.
- DVWA levantado real (`vulnerables/web-dvwa`, puerto 8078) y escaneado en vivo con Nuclei (10.538 plantillas). Resultado real contra `ground_truth_dvwa.yaml`: **0% recall, 0% precisión** (0 TP / 6 FP / 11 FN) — el escaneo sin sesión autenticada no alcanza ninguno de los módulos reales de DVWA (todos detrás de `/vulnerabilities/*`, requieren login). Detalle completo, incluyendo el intento fallido de automatizar el login/setup de DVWA (bug real de esa imagen concreta), en `eval/live_run_report.md` Corrida 7.

**No completado — bloqueador real:** Docker Desktop se cerró solo a mitad de sesión (mismo problema ya conocido, ver sección "Cómo levantar todo" arriba). No dio tiempo a relevantar WebGoat y correr su escaneo en vivo antes de cerrar esta sesión. Próximo paso concreto: confirmar `docker ps` responde, `docker run --rm -d -p 8080:8080 -p 9090:9090 webgoat/webgoat`, esperar a que levante (`curl http://localhost:8080/WebGoat/login`), correr Nuclei contra él y `eval/run_eval.py --ground-truth eval/ground_truth_webgoat.yaml --findings <hallazgos reales>` — la ground truth ya está lista, solo falta la corrida.

**Relacionado (no resuelto tampoco):** una sesión posterior (Corrida 12, `eval/live_run_report.md`) intentó cerrar el ángulo de "escaneo autenticado" que este item y el Item 2 comparten — montó un intento real y propiamente autorizado contra Juice Shop (bearer token real, gate de asset nuevo pasado correctamente) pero topó con el mismo tipo de bloqueador de infraestructura (timeout de ZAP) antes de poder producir números nuevos. Sigue pendiente repetir esto (con DVWA o Juice Shop, lo que esté más a mano) en una sesión sin la contención de recursos que documentó esa corrida.

### Item 5 — Monitoreo continuo real de suplantación (CertStream) ✅ HECHO, y la limitación de feed real que quedaba abierta ✅ CERRADA esta sesión (Corrida 13)
`api/certstream_listener.py` (nuevo) implementa el listener como daemon thread dentro del mismo proceso uvicorn, arrancado/parado desde el `lifespan` de `api/main.py` — mismo patrón que `api/scheduler.py`, decisión ya tomada por el usuario. El matching reutiliza el motor de `dnstwist` (mismo que `run_dnstwist()` en `tools/antisuplantacion.py`, sección 3.2), pero llamando a `dnstwist.Fuzzer` directamente en proceso (`tools/antisuplantacion.py::generate_domain_variants()`) en vez de por subprocess, porque el stream global de CT logs no puede darse el lujo de un subprocess+DNS por dominio observado. Cuando un dominio del feed coincide con una variante de un activo vigilado, escribe un finding real (`tipo='dominio_variante_certstream'`) en la tabla `findings`, con una fila `scans` sintética asociada (mismo patrón que `run_scan_cycle_once`).

**Verificado en vivo:** el listener arranca dentro de la API real sin tumbarla (`GET /health` responde normal con el thread corriendo), conecta el handshake de websocket al feed configurado, y degrada con gracia (probado con `ImportError` simulado del paquete `certstream`) sin crashear la API. El matching + la escritura real a `findings` se verificó de punta a punta contra la API real corriendo (tenant y dominio de prueba reales, `POST /assets` real, mensaje sintético con la forma exacta de un mensaje CertStream, confirmado también vía `GET /findings`), incluyendo casos negativos (dominio propio, dominio ajeno, mensaje no-certificado — ningún falso positivo).

**Limitación de Corrida 6 (feed público muerto), cerrada en Corrida 13:** el feed público histórico `wss://certstream.calidog.io` acepta el handshake pero no transmite ningún mensaje — el servicio gratuito de Cali Dog Security está descontinuado, sin alternativa pública gratuita hoy. Esta sesión levantó `certstream-server-go` (`0rickyy0/certstream-server-go` en Docker Hub, imagen real confirmada antes de usarla) vía `docker run -d --name vigia-certstream-test -p 48182:8080 0rickyy0/certstream-server-go`, sin config custom (usa `config.sample.yaml` por defecto, 45 CT logs reales monitoreados desde el primer segundo). Con `VIGIA_CERTSTREAM_URL=ws://localhost:48182`, la API real (`uvicorn api.main:app`) conectó y **recibió mensajes reales de Certificate Transparency logs del mundo real** (miles por minuto, confirmado por `docker stats` con 904 MB de tráfico de red en ~90s) — la primera vez que este listener procesa datos reales en vez de mensajes sintéticos. La lógica de matching (`procesar_mensaje_certstream`) corrió contra ese volumen real con **0 excepciones**, y la API se mantuvo saludable (`GET /health` 200 durante todo el run). No hubo un match real nuevo de un dominio-variante vigilado dentro de la ventana de observación (~2 min) — esperado y anticipado, no es la parte que esta corrida necesitaba probar. Detalle completo, incluyendo el setup exacto reproducible para un deploy permanente futuro (fuera de alcance aquí — lo cubre la tarea de deployment ya en curso), en `eval/live_run_report.md`, Corrida 13. Contenedor de prueba detenido y eliminado al terminar; no se tocó `.env.example` (fuera de alcance, instrucción previa del usuario) ni se hizo commit.

### Item 6 — Cumplimiento normativo (Ley 2573/ISO 27001) como producto ✅ HECHO esta sesión
`agents/cumplimiento.py` (nuevo) genera un reporte de cumplimiento bajo demanda sobre el historial completo de `findings` de un tenant (no es un nodo del grafo LangGraph, a diferencia de `agents/reporteria.py`) — porque el gancho de venta más fuerte según `docs/market-research.md` (nadie en Colombia lo está usando comercialmente) es evidencia acumulada de trazabilidad, no una foto de un solo escaneo. Una taxonomía interna de 13 categorías mapea cada hallazgo a controles reales del Anexo A de ISO/IEC 27001:2022 y a las 4 obligaciones concretas de la Ley 2573 documentadas en `docs/market-research.md` sección 3 (validar identidad, atender reportes de suplantación, trazabilidad, demostrar prevención de fraude) — sin inventar numeración de artículos que no existe en ninguna fuente del repo. Cada reporte generado incluye siempre una advertencia explícita de alcance (la ley apunta primero a entidades financieras/telco/crédito, no a "toda pyme" automáticamente) y un disclaimer de que el mapeo ISO es interpretación propia de Vigia, no una auditoría certificada. Nuevo `GET /reports/cumplimiento` en `api/main.py`, mismo patrón de auth (`Depends(get_current_user)`) que el resto.

Dos bugs reales encontrados y arreglados en el camino: (1) `findings.tipo` vale `"desconocido"` para casi todo hallazgo real de ZAP/Nuclei (la clave no existe en el nivel superior del hallazgo verificado) — se resolvió categorizando desde `raw_json` en vez de confiar en `tipo`; (2) el fallback de CLI en `agents/_llm.py` decodificaba el stdout UTF-8 de Claude como cp1252 en Windows, corrompiendo tildes/ñ de forma no recuperable — arreglado con `encoding="utf-8"` explícito en el `subprocess.run` (bug preexistente que afectaba a todos los agentes con fallback de CLI, no solo a este item).

**Verificado contra datos reales:** se levantó Juice Shop vía Docker, se corrió un escaneo autorizado real (10 hallazgos reales de ZAP baseline), se golpeó el endpoint por HTTP real con un JWT real, se confirmó la codificación UTF-8 inspeccionando los bytes crudos de la respuesta, y se confirmó aislamiento multi-tenant (hallazgos de otro tenant no aparecieron en el reporte). Detalle completo del diseño y la verificación en `docs/cumplimiento.md`.

**Brecha cerrada en una sesión posterior (ver `eval/live_run_report.md` Corrida 10):** `agents/antisuplantacion.py` (dnstwist/Sherlock bajo demanda) ahora sí persiste sus hallazgos en la tabla `findings` — `api/main.py::_extraer_findings_antisuplantacion()` aplana `antisuplantacion_findings` a filas reales colgadas del mismo `scan_id` del propio `POST /scan` (no hizo falta tocar `agents/cumplimiento.py`, la categorización ya reconocía esos `tipo` desde que se escribió). De paso se encontró y corrigió un bug real: `agents/antisuplantacion.py::node()` no excluía la entrada `fuzzer='*original'` de `run_dnstwist()` (el propio dominio del cliente), que sin el filtro se habría persistido como falso positivo. Verificado en vivo contra un dominio sintético propio (`miempresatest.com`, nunca un tercero real) vía `GET /findings` y `GET /reports/cumplimiento`. Detalle completo en `eval/live_run_report.md` Corrida 10 y en `docs/cumplimiento.md`.

## Además, lo que el usuario pidió que quedara anotado (no son items 1-6, son transversales)

- **Mejorar el frontend más allá del dashboard actual**: ✅ HECHO esta sesión, las tres cosas pedidas. (1) Reportes descargables: `tools/report_export.py` (nuevo, `fpdf2`/`python-docx`, ya dependencias reales del entorno, sin binarios de sistema) convierte a PDF/DOCX el markdown que ya generaban `agents/reporteria.py` y `agents/cumplimiento.py`, vía `GET /scans/{id}/report/download` y `GET /reports/cumplimiento/download` (`?formato=pdf|docx`), con botones nuevos en `Dashboard.tsx`. (2) Comparar escaneos en el tiempo: `frontend/src/components/ScanHistoryChart.tsx` (nuevo, SVG puro, sin librería de charting — agregación 100% client-side sobre `GET /scans` + `GET /findings`, que ya alcanzaban). (3) Invitar usuarios al tenant: tabla `invitations` nueva en `db/schema.sql`, endpoints `POST/GET /tenant/invitations`, `DELETE /tenant/invitations/{id}`, `GET /tenant/members`, `GET /tenant/invitations/preview/{token}` en `api/main.py`, y `POST /auth/register` ahora acepta `invite_token` opcional para unirse a un tenant existente en vez de crear uno nuevo (sin envío de email real: el link se comparte manualmente). Frontend nuevo: `frontend/src/components/Equipo.tsx`, más banner de invitación en `Login.tsx`. Las tres probadas de punta a punta con la API y el frontend reales corriendo (no solo "compila") — incluyendo un flujo completo de invitación con dos usuarios reales terminando en el mismo tenant. Dos bugs reales de `fpdf2` encontrados y arreglados en el camino (cursor pegado al margen derecho tras cada línea; em dash fuera de latin-1 convirtiéndose en `?`). Detalle completo, incluyendo qué NO se pudo probar con datos 100% reales por falta de tiempo (varias severidades distintas en el mismo gráfico), en `eval/live_run_report.md` Corrida 11.
- **Ajustar y probar más en general**: no hay tests automatizados todavía (`pytest` está en `pyproject.toml` como dependencia, cero archivos `test_*.py` reales). Todo lo "probado" en este proyecto hasta ahora ha sido manual, en vivo, en esta sesión — vale la pena convertir al menos los casos ya validados manualmente (registro, login, escaneo, findings) en tests reales de pytest para que dejen de depender de que alguien los corra a mano cada vez.
- **Usar Claude para construir mejores herramientas** (lo que el usuario llamó "Fable" — cualquier modelo Claude sirve, ya está resuelto vía CLI): ✅ HECHO esta sesión. `agents/revision_ia.py` (nuevo) es el agente de revisión de código con IA que complementa a Semgrep — un `SYSTEM_PROMPT` acotado a 5 categorías de lógica de negocio que un analizador sintáctico no puede ver (control de acceso con campo equivocado, aislamiento multi-tenant que falla en un camino específico, confianza en input del cliente, lógica de negocio evadible, claims de sesión/token obsoletos), explícitamente prohibido de repetir lo que Semgrep ya cubre. Es Semgrep-aware (recibe opcionalmente sus hallazgos como contexto) pero función bajo demanda, no nodo de `orchestrator/graph.py` (mismo patrón que `agents/cumplimiento.py` — razonamiento completo en el docstring del módulo). Probado en vivo contra el propio código de Vigia (`auth/jwt_auth.py`, `api/main.py`, `agents/cumplimiento.py`): encontró un hallazgo real y genuino (claims de rol/tenant/plan embebidos en el JWT de 7 días, nunca revalidados contra la DB en el "camino rápido" de `get_current_user()` — un usuario degradado o removido de un tenant conserva su token viejo con privilegios anteriores hasta por 7 días) y un segundo hallazgo real (`POST /scan/activo` solo valida un booleano que el propio cliente envía, sin validar `target_url` contra los assets del tenant ni contra ningún registro real de autorización — puerta de autorización más débil que `POST /scan`, que sí pasa por `gate_autorizacion`). También expuso un bug real preexistente en `agents/_llm.py::_call_via_cli` (pasar `user_message` como argumento de línea de comando revienta en Windows pasado ~32KB — límite real de `CreateProcess`, no un timeout) — arreglado pasándolo por stdin, beneficia a cualquier agente con fallback de CLI, no solo a este. Limitación real documentada: el formato JSON estricto no siempre lo respeta el backend de CLI (dos corridas contra `agents/cumplimiento.py` encontraron el mismo hallazgo real pero nunca en JSON parseable) — degrada con gracia, no alucina ni crashea. Detalle completo, incluyendo el hallazgo correctamente matizado que casi fue un falso positivo (rol de invitación), en `eval/live_run_report.md` Corrida 8. Pendiente real, no bloqueante: no se probó con `ANTHROPIC_API_KEY` real (no configurada en este entorno) ni se agregó un endpoint HTTP — el siguiente paso natural si el uso crece es `POST /reports/revision-ia`, mismo patrón que `GET /reports/cumplimiento`.

## Cosas que NO hacer sin preguntar primero

- No reescribir el historial de git de nuevo (ya se hizo una vez esta sesión, con backup y verificación — es una operación destructiva, solo se repite si hay una razón concreta y nueva).
- No hacer privado el repo — el usuario decidió explícitamente que se quede público (transparencia = confianza, es parte del posicionamiento de producto).
- No retomar la integración de pagos (Wompi) sin que el usuario lo pida — está en pausa a propósito.
- No asumir que Docker/el servidor de prueba siguen corriendo entre sesiones — siempre verificar (`docker ps`, `curl .../health`) antes de asumir estado.
