# Corridas en vivo del pipeline — 2026-07-15 a 2026-07-18

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
