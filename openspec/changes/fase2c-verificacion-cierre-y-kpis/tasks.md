# Tasks: Verificación de cierre y KPIs de remediación

> Gate previo (no es una tarea, es una condición de arranque): NO iniciar
> Fase 2 (implementation) hasta confirmar que `fase2a-ciclo-vida-hallazgo`
> y `fase2b-tablero-remediacion` están implementados y aplicados —
> `api/main.py` y `frontend/src/pages/Dashboard.tsx` son archivos
> compartidos con ambos.

## Phase 0: Pre-flight (bloqueante, antes de tocar código)

- [x] 0.1 Confirmar que `fase2a` y `fase2b` tienen `estado: apply complete`
      en sus `state.yaml`; si no, detener aquí. **Confirmado por el
      orquestador antes de lanzar este `sdd-apply`** (ambos mergeados a
      `main`) -- no se re-verificó `state.yaml` de esos changes archivados
      dentro de esta sesión, se tomó como dado según la instrucción de
      arranque.
- [x] 0.2 Releer el `PATCH /findings/{id}` real de `fase2a` aplicado — si
      su vocabulario de `estado` o su firma difiere de lo asumido en
      `design.md`, actualizar este `design.md` antes de continuar.
      **Resultado**: el vocabulario/firma coinciden exactamente con lo
      asumido (`TRANSICIONES_VALIDAS`, 8 estados). La diferencia real fue
      otra: `fase2a` SÍ persiste `finding_transiciones.created_at` real
      (design.md asumía esto como incierto) -- design.md actualizado
      (sección "Actualizado durante `sdd-apply`").
- [x] 0.3 Releer el punto de montaje de `fase2b` aplicado (vista de
      tablero) — confirmar si existe un lugar natural para el botón
      "verificar arreglo"; si no, confirmar el fallback standalone en
      `Dashboard.tsx`. **Resultado**: sí existe
      (`frontend/src/components/RemediationBoard.tsx`, tabla con una fila
      por hallazgo) -- botón insertado ahí en una columna "Acciones" nueva,
      no se necesitó el fallback standalone.

## Phase 1: Infrastructure (tools/zap_api.py)

- [x] 1.1 **Spike en vivo (bloqueante para 1.2)**: levantar Juice Shop +
      un daemon ZAP nuevo (sin spidering previo) y probar
      `POST /JSON/ascan/action/scan/?url=<endpoint>&recurse=false` contra
      una URL que el daemon nunca visitó. Confirmar si ZAP la escanea
      igual o si requiere que la URL ya esté en su Sites tree (`GET`
      previo). Documentar el resultado real en `design.md` (Open
      Questions #3). **No se pudo ejecutar**: Docker Desktop no tenía el
      daemon corriendo en este entorno sandbox (`docker ps` falló
      conectando al pipe; `docker --version` sí respondía). Investigación
      de código en su lugar (leyendo `tools/zap_api.py`): se aplicó la
      mitigación ya prevista incondicionalmente (`access_url()` antes de
      `ascan_start(recurse=False)`, ver 1.3) en vez de dejar la pregunta
      sin resolver o fingir "no soportado" -- decisión y su justificación
      completa documentadas en `design.md`, Open Questions #3.
- [x] 1.2 Agregar `recurse: bool = True` a `ascan_start(base_url,
      target_url, recurse=True)`, serializado a `"true"`/`"false"`.
- [x] 1.3 Implementar `run_single_target_verification(target_url,
      endpoint_url, bearer_token, scan_id, on_progress=None)`: arranca
      daemon, un `access_url()` de registro previo (ver 1.1),
      `ascan_start(recurse=False)`, poll corto, `get_alerts(baseurl=
      endpoint_url)`, cleanup en `finally`.
- [x] 1.4 Docstring de la función nueva documentando el hallazgo real de
      1.1 (mismo estilo del resto del módulo: "confirmado en vivo" vs
      "no confirmado").

## Phase 2: Implementation (api/main.py)

- [x] 2.1 Modelo `VerificarCierreOut` (pydantic) en `api/main.py`.
- [x] 2.2 Función `_herramienta_de_finding(raw_json) -> str` (nuclei |
      zap | semgrep | trivy | grype), mismo patrón dispatch que
      `_detalle_desde_raw_json`.
- [x] 2.3 `POST /findings/{id}/verificar-cierre`: 404 cross-tenant, 422
      para semgrep/trivy/grype, dispatch a `run_nuclei` o
      `run_single_target_verification` para los demás, `UPDATE estado`
      secuencial (`verificado` → `cerrado`) si no reaparece. Guard extra
      (no en el plan original, agregado durante `sdd-apply`): un finding ya
      en estado terminal (`cerrado`/`aceptado_riesgo`) responde 200 no-op
      sin volver a escanear.
- [x] 2.4 En caso de reaparición: llamar `verificar_un_hallazgo` sobre la
      alerta encontrada para refrescar `estado_verificacion` en
      `raw_json`, sin cambiar `estado`.
- [x] 2.5 Manejo de error de infraestructura (ZAP/Docker no disponible):
      capturar `ToolExecutionError`/`ZapDaemonError`, responder 503, no
      tocar `estado`.
- [x] 2.6 Modelos `KpiSeveridad`, `KpiTendenciaAsset`, `KpiOut`. Desviación
      de la firma original de design.md: `KpiSeveridad.precision` default
      cambiado de `"aproximado"` a `"exacto"` (ver nota de design.md) --
      `fase2a` confirmó persistir el historial con timestamp real.
- [x] 2.7 `GET /kpis`: `SELECT` de `findings` (+ JOIN `scans` para
      `asset_id`), cálculo de MTTR/% SLA/tendencia en Python
      (`datetime.fromisoformat` vía `_parse_dt_utc_naive`, que además
      normaliza el formato mixto de timestamp entre transiciones
      manuales/automáticas -- sin funciones SQL de fecha).

## Phase 3: Frontend (frontend/src/api.ts, componente)

- [x] 3.1 Tipos `VerificarCierreResult`, `KpiSummary` en
      `frontend/src/api.ts`.
- [x] 3.2 Métodos `api.verificarCierre(id)` y `api.getKpis()`.
- [x] 3.3 Botón "verificar arreglo" insertado en la fila del tablero real
      de `fase2b` (`RemediationBoard.tsx`, columna "Acciones" nueva),
      visible solo cuando `estado` es `mitigado` o `verificado`.
- [x] 3.4 Creado `KpiWidget.tsx` (componente propio, montado en
      `Dashboard.tsx` justo antes de `RemediationBoard`): 3 secciones (MTTR
      por severidad con badge de precisión, % dentro de SLA con chip de
      color, mini-serie de tendencia por activo con barras + resumen del
      punto más reciente).

## Phase 4: Testing

- [x] 4.1 `tests/test_verificar_cierre.py`: dispatch por herramienta
      (nuclei/zap/no-soportado), 404 cross-tenant, 422
      semgrep/trivy/grype, transición automática con mocks de
      `run_nuclei`/`run_single_target_verification`.
- [x] 4.2 `tests/test_verificar_cierre.py`: hallazgo persiste → `estado`
      sin cambio, respuesta `reaparece: true`.
- [x] 4.3 `tests/test_verificar_cierre.py`: falla de ZAP/Docker → 503,
      `estado` sin cambio.
- [x] 4.4 `tests/test_kpis.py`: MTTR con datos, MTTR `null` sin hallazgos
      cerrados de esa severidad.
- [x] 4.5 `tests/test_kpis.py`: % SLA con 0 hallazgos abiertos → `null`
      (no división por cero); con todos dentro de plazo → 100.
- [x] 4.6 `tests/test_kpis.py`: tendencia por activo con 3 scans
      históricos de un mismo `asset_id`.
- [x] 4.7 Correr suite completa (`pytest tests/`) y confirmar que sigue
      verde (sin romper `test_finding_detail.py` ni los de `fase2a`).
      **Resultado**: 188 passed, 1 skipped (baseline 165 passed/1 skipped +
      19 tests nuevos de este change + 4 tests nuevos de
      `tools/zap_api.py`). Frontend: `npm run build`
      (`tsc -b && vite build`) compila sin errores.

**Tests adicionales no listados originalmente en este plan** (agregados
durante `sdd-apply` por cobertura, ver `tests/test_zap_checkpointed_scan.py`):
`ascan_start(recurse=...)` serializa correctamente, y
`run_single_target_verification` limita el ataque a un endpoint y limpia el
daemon incluso ante una excepción -- mismo patrón que los tests ya
existentes de `run_checkpointed_active_scan` en ese archivo.
