# Corridas en vivo del pipeline — 2026-07-15 y 2026-07-17/18

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
