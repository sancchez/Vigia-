# Primera corrida en vivo del pipeline — 2026-07-15

Primera prueba end-to-end real del MVP (Fase 1) contra un target de laboratorio, disparada vía `POST /scan` de `api/main.py`, con `autorizacion_firmada: true` (autorizado por tratarse de OWASP Juice Shop, la app de laboratorio de referencia del plan maestro).

## Entorno

- OWASP Juice Shop v20.1.1, clonado con `git clone --depth 1` y levantado con `npm install && npm start` (Docker no está instalado en esta máquina, así que se usó la vía sin contenedor documentada en `eval/setup_juiceshop.md`).
- Servicio Vigia (`api/main.py`) corriendo con `uvicorn` en `localhost:8010`.
- `ANTHROPIC_API_KEY` no configurada en esta corrida — los agentes que razonan con Claude (priorización, remediación, reportería narrativa) degradaron con gracia al fallback documentado, tal como se diseñó. No es un bug, es la ausencia de la key.
- Herramientas de escaneo activo disponibles: solo **Nuclei** (instalado durante esta sesión). Subfinder, Amass, OWASP ZAP, Trivy, Grype y Metasploit no están disponibles (ZAP/Trivy/Grype dependen de Docker, que no está instalado; Subfinder/Amass no se alcanzaron a compilar).

## Qué pasó (dos corridas)

**Corrida 1 — encontró un bug real.** `run_nuclei()` corrió con su timeout por defecto (900s) sin restricción de plantillas contra un Nuclei recién instalado que todavía estaba sincronizando su repositorio de plantillas (`~/nuclei-templates`, +12.000 archivos). A los 900s, `subprocess.TimeoutExpired` se propagó sin capturar y tumbó la petición completa con un 500 sin manejar — un bug real, no solo "falta una herramienta". Ver commit `cbd0e53`: se introdujo `ToolExecutionError` como clase base compartida por `ToolNotInstalledError` y el nuevo `ToolTimeoutError`, y los cuatro agentes que llaman herramientas por subprocess (`recon`, `escaneo`, `verificacion`, `antisuplantacion`) ahora atrapan la clase base — cualquier timeout futuro degrada al log de trazabilidad en vez de tumbar el request.

**Corrida 2 — post-fix, limpia.** `POST /scan` respondió `200 OK` en 31 segundos. Los 7 agentes corrieron en orden (orquestador → recon → escaneo → verificación → priorización → remediación → gate anti-suplantación → reportería), con trazabilidad completa en `trace_log`. Cero excepciones sin manejar.

## Hallazgos y métricas reales

`scan_findings`: 0. Nuclei corrió sin errores pero no reportó nada. Se corrió `eval/run_eval.py` contra `eval/live_run_findings.json` (0 hallazgos) comparado con las 11 vulnerabilidades documentadas en `eval/ground_truth.yaml`:

| Métrica | Valor |
|---|---|
| True Positives | 0 |
| False Positives | 0 |
| False Negatives | 11 |
| Precisión | 0.00% (indefinida, sin denominador real de comparación) |
| Recall | 0.00% |

## Por qué el recall salió en 0% (esto no es el mismo bug que el del timeout)

Las 11 vulnerabilidades de la ground truth de Juice Shop son fallas de **lógica de negocio a medida** (bypass de login por SQLi en un campo específico, IDOR en la cesta de compras, JWT firmado con clave débil, flujo de recuperación de contraseña con preguntas predecibles, etc.) — no son CVEs conocidos ni configuraciones por defecto. Nuclei está diseñado para lo segundo (+12.000 plantillas de CVEs, credenciales default, exposiciones genéricas), así que un Nuclei sin plantillas custom escritas específicamente para Juice Shop **no tiene forma de encontrar estas vulnerabilidades**, con o sin bugs.

Esto es consistente con el diseño del plan maestro (sección 3.1): OWASP ZAP (DAST activo, con spidering y ataque real a formularios/parámetros) es la herramienta pensada para este tipo de hallazgo, no Nuclei. ZAP no está disponible aquí porque requiere Docker.

**Conclusión honesta:** el pipeline (orquestación, gate de autorización, trazabilidad, manejo de errores) funciona correctamente de punta a punta. El recall de 0% en esta corrida es una limitación de **cobertura de herramientas en este entorno** (falta ZAP/Docker, plantillas Nuclei genéricas), no un defecto del diseño del sistema. Antes de usar este resultado como prueba de valor ante un cliente, hay que instalar Docker + ZAP (o escribir plantillas Nuclei custom para los challenges de Juice Shop) y volver a correr.

## Próximo paso concreto para subir el recall

1. Instalar Docker Desktop y correr `run_zap_baseline()` (ya implementado en `tools/scan.py`, solo requiere el binario).
2. Alternativa sin Docker: escribir 3-4 plantillas Nuclei custom dirigidas a los endpoints conocidos de Juice Shop (`/rest/user/login`, `/rest/products/search`, `/rest/basket/{id}`) — más rápido de configurar que Docker, pero es trabajo manual por objetivo, no escala a clientes reales.
3. Registrar este resultado en `eval/failure_log.md` como el primer caso real del loop de mejora continua (sección 8.2 del plan).
