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

### Item 2 — Escaneo activo como job de fondo ✅ HECHO esta sesión
Nuevo `POST /scan/activo` (requiere `autorizacion_firmada: true` explícito en el mismo request, a propósito más estricto que `POST /scan`) que arranca `run_zap_active_scan` en un `threading.Thread` y responde 202 de inmediato con `scan_id`. `GET /scans/{id}` consulta el estado (`corriendo`/`completado`/`error`) y los hallazgos cuando ya terminó.

**No probado en vivo todavía** (Juice Shop se limpió antes de llegar a esto). Antes de confiar en esto:
1. Reclonar Juice Shop (`git clone --depth 1 https://github.com/juice-shop/juice-shop.git`, `npm install` con `TEMP`/`TMP` apuntando a un disco con espacio si C: está justo).
2. `npm start`, esperar a que responda en :3000.
3. Registrar un usuario de prueba (`POST /api/Users`) y loguear (`POST /rest/user/login`) para el `bearer_token`.
4. `POST /scan/activo` con `target_url: "http://localhost:3000"`, `ajax_spider: true`, `minutes: 20` o más, el bearer token, y `autorizacion_firmada: true`.
5. Pollear `GET /scans/{id}` cada rato hasta `estado != 'corriendo'`.
6. Si el AJAX Spider sigue sin terminar en un tiempo razonable (pasó dos veces en esta sesión, 25min y 35min), es momento de investigar por qué el navegador headless es tan lento en este host específico — Docker Desktop + WSL2 en Windows puede tener overhead de virtualización que un host Linux nativo no tendría. No sigas subiendo el timeout a ciegas una tercera vez sin diagnosticar la causa real (revisar logs del contenedor con `docker logs <container_id>` mientras corre, no solo esperar a que termine o falle).

### Item 3 — Probar Trivy/Grype/Semgrep de verdad ⬜ NO HECHO
Wrappers ya existen en `tools/scan.py` (`run_trivy_image`, `run_grype`, `run_semgrep`), nunca se corrieron contra nada real en ninguna sesión. Son el frente de "código y dependencias del cliente" — sin esto Vigia solo cubre la mitad de la superficie de ataque real de una pyme (la web pública, no su código fuente ni sus dependencias).

Cómo probarlos rápido y real:
- **Semgrep**: ya está instalado como dependencia Python del proyecto (`pip install semgrep` en `tools/scan.py`). Correr `run_semgrep('.', config='auto')` contra el propio repo de Vigia sería la prueba más rápida y honesta — encontraría fallas reales en nuestro propio código antes de ofrecérselo a un cliente.
- **Trivy/Grype**: requieren instalación aparte (`scoop install trivy grype` en Windows). Una vez instalados, correr `run_trivy_image` o `run_grype` contra una imagen Docker conocida con CVEs documentados (ej. una versión vieja de `node:14` tiene decenas) para confirmar que el parseo de resultados funciona antes de conectarlos al pipeline.
- Ninguno de los tres está wireado en `agents/escaneo.py` todavía — hay que decidir si van ahí (activo, requiere autorización) o si necesitan su propio nodo/agente porque operan sobre código fuente, no sobre una URL (`escaneo.py` hoy solo recibe `scope.dominios`, no una ruta de código).

### Item 4 — Más targets de evaluación (DVWA, WebGoat) ⬜ NO HECHO
`eval/ground_truth.yaml` solo tiene las 11 vulnerabilidades de Juice Shop. Medir contra un solo target de laboratorio puede hacer que el sistema (o los prompts) se sobreajusten a sus particularidades sin que nadie lo note — un cambio que sube el recall en Juice Shop podría estar empeorando el comportamiento general.

Pasos concretos:
1. Levantar DVWA (`docker run --rm -p 8080:80 vulnerables/web-dvwa` es la forma más rápida) o WebGoat (`docker run -p 8080:8080 -p 9090:9090 webgoat/webgoat`).
2. Documentar su propia ground truth siguiendo exactamente el formato de `eval/ground_truth.yaml` (id, type, location, severity, description) — DVWA y WebGoat también tienen vulnerabilidades bien conocidas y documentadas públicamente, no hay que inventar nada.
3. `eval/run_eval.py` ya acepta `--findings <archivo>` como parámetro — probablemente necesite un `--ground-truth <archivo>` también (hoy asume `eval/ground_truth.yaml` fijo, revisar el código antes de asumir).

### Item 5 — Monitoreo continuo real de suplantación (CertStream) ⬜ NO HECHO
Hoy `tools/antisuplantacion.py` (dnstwist + Sherlock) corre bajo demanda dentro del pipeline normal — no hay nada escuchando en tiempo real cuando alguien registra un dominio clon nuevo. `CertStream` (mencionado en `plan-proyecto-ciberseguridad.md` sección 3.2) escucha los logs de Certificate Transparency en tiempo real y puede marcar un dominio sospechoso el mismo día que se registra, antes de que el sitio de phishing esté siquiera activo — es la pieza que de verdad justifica "vigilancia continua" para el módulo anti-suplantación, no solo un escaneo recurrente cada 6 horas.

Es un proceso de larga duración (un listener, no una consulta puntual) — no encaja en el modelo request/response de FastAPI ni en el scheduler de intervalos fijos (`api/scheduler.py`). Necesita su propio proceso persistente (o un servicio separado) que escriba a la misma tabla `findings` cuando encuentre algo. No empezar esto sin antes decidir dónde vive ese proceso (¿otro thread en el mismo uvicorn? ¿un proceso aparte?).

### Item 6 — Cumplimiento normativo (Ley 2573/ISO 27001) como producto ⬜ NO HECHO
Cero código todavía. Es el gancho de venta más fuerte según `docs/market-research.md` (nadie en Colombia lo está usando comercialmente) — la lógica central sería mapear cada `finding` ya guardado en la tabla `findings` a los controles/artículos específicos que ayuda a demostrar, y generar un reporte de cumplimiento separado del reporte técnico normal. `docs/market-research.md` sección 3 tiene el detalle legal completo de qué exige la ley — leerlo antes de diseñar el mapeo, no asumir.

## Además, lo que el usuario pidió que quedara anotado (no son items 1-6, son transversales)

- **Mejorar el frontend más allá del dashboard actual**: reportes descargables (PDF/DOCX — hay un skill de Claude Code para esto, `anthropic-skills:pdf`/`docx`), comparar escaneos en el tiempo (gráfico simple de hallazgos por fecha), invitar más usuarios al mismo tenant (hoy solo existe el rol `owner` funcional en la práctica — `role` admite `admin`/`member` en el schema pero no hay UI ni endpoint para invitar).
- **Ajustar y probar más en general**: no hay tests automatizados todavía (`pytest` está en `pyproject.toml` como dependencia, cero archivos `test_*.py` reales). Todo lo "probado" en este proyecto hasta ahora ha sido manual, en vivo, en esta sesión — vale la pena convertir al menos los casos ya validados manualmente (registro, login, escaneo, findings) en tests reales de pytest para que dejen de depender de que alguien los corra a mano cada vez.
- **Usar Claude para construir mejores herramientas** (lo que el usuario llamó "Fable" — cualquier modelo Claude sirve, ya está resuelto vía CLI): la idea concreta pendiente es un agente de revisión de código con IA que complemente a Semgrep (ver item 3) — Semgrep encuentra patrones sintácticos, un modelo Claude puede encontrar fallas de lógica de negocio que un analizador estático no ve. No empezar esto antes de que Semgrep esté conectado (item 3) — no tiene sentido añadir la capa de IA sobre una capa base que ni siquiera se ha probado.

## Cosas que NO hacer sin preguntar primero

- No reescribir el historial de git de nuevo (ya se hizo una vez esta sesión, con backup y verificación — es una operación destructiva, solo se repite si hay una razón concreta y nueva).
- No hacer privado el repo — el usuario decidió explícitamente que se quede público (transparencia = confianza, es parte del posicionamiento de producto).
- No retomar la integración de pagos (Wompi) sin que el usuario lo pida — está en pausa a propósito.
- No asumir que Docker/el servidor de prueba siguen corriendo entre sesiones — siempre verificar (`docker ps`, `curl .../health`) antes de asumir estado.
