# Tasks: Verificación de cierre y KPIs de remediación

> Gate previo (no es una tarea, es una condición de arranque): NO iniciar
> Fase 2 (implementation) hasta confirmar que `fase2a-ciclo-vida-hallazgo`
> y `fase2b-tablero-remediacion` están implementados y aplicados —
> `api/main.py` y `frontend/src/pages/Dashboard.tsx` son archivos
> compartidos con ambos.

## Phase 0: Pre-flight (bloqueante, antes de tocar código)

- [ ] 0.1 Confirmar que `fase2a` y `fase2b` tienen `estado: apply complete`
      en sus `state.yaml`; si no, detener aquí.
- [ ] 0.2 Releer el `PATCH /findings/{id}` real de `fase2a` aplicado — si
      su vocabulario de `estado` o su firma difiere de lo asumido en
      `design.md`, actualizar este `design.md` antes de continuar.
- [ ] 0.3 Releer el punto de montaje de `fase2b` aplicado (vista de
      tablero) — confirmar si existe un lugar natural para el botón
      "verificar arreglo"; si no, confirmar el fallback standalone en
      `Dashboard.tsx`.

## Phase 1: Infrastructure (tools/zap_api.py)

- [ ] 1.1 **Spike en vivo (bloqueante para 1.2)**: levantar Juice Shop +
      un daemon ZAP nuevo (sin spidering previo) y probar
      `POST /JSON/ascan/action/scan/?url=<endpoint>&recurse=false` contra
      una URL que el daemon nunca visitó. Confirmar si ZAP la escanea
      igual o si requiere que la URL ya esté en su Sites tree (`GET`
      previo). Documentar el resultado real en `design.md` (Open
      Questions #3).
- [ ] 1.2 Agregar `recurse: bool = True` a `ascan_start(base_url,
      target_url, recurse=True)`, serializado a `"true"`/`"false"`.
- [ ] 1.3 Implementar `run_single_target_verification(target_url,
      endpoint_url, bearer_token, scan_id, on_progress=None)`: arranca
      daemon, (si 1.1 confirmó que hace falta) un `GET` de registro previo,
      `ascan_start(recurse=False)`, poll corto, `get_alerts(baseurl=
      endpoint_url)`, cleanup en `finally`.
- [ ] 1.4 Docstring de la función nueva documentando el hallazgo real de
      1.1 (mismo estilo del resto del módulo: "confirmado en vivo" vs
      "no confirmado").

## Phase 2: Implementation (api/main.py)

- [ ] 2.1 Modelo `VerificarCierreOut` (pydantic) en `api/main.py`.
- [ ] 2.2 Función `_herramienta_de_finding(raw_json) -> str` (nuclei |
      zap | semgrep | trivy | grype), mismo patrón dispatch que
      `_detalle_desde_raw_json`.
- [ ] 2.3 `POST /findings/{id}/verificar-cierre`: 404 cross-tenant, 422
      para semgrep/trivy/grype, dispatch a `run_nuclei` o
      `run_single_target_verification` para los demás, `UPDATE estado`
      secuencial (`verificado` → `cerrado`) si no reaparece.
- [ ] 2.4 En caso de reaparición: llamar `verificar_un_hallazgo` sobre la
      alerta encontrada para refrescar `estado_verificacion` en
      `raw_json`, sin cambiar `estado`.
- [ ] 2.5 Manejo de error de infraestructura (ZAP/Docker no disponible):
      capturar `ToolExecutionError`/`ZapDaemonError`, responder 503, no
      tocar `estado`.
- [ ] 2.6 Modelos `KpiSeveridad`, `KpiTendenciaAsset`, `KpiOut`.
- [ ] 2.7 `GET /kpis`: `SELECT` de `findings` (+ JOIN `scans` para
      `asset_id`), cálculo de MTTR/% SLA/tendencia en Python
      (`datetime.fromisoformat`, sin funciones SQL de fecha).

## Phase 3: Frontend (frontend/src/api.ts, componente)

- [ ] 3.1 Tipos `VerificarCierreResult`, `KpiSummary` en
      `frontend/src/api.ts`.
- [ ] 3.2 Métodos `api.verificarCierre(id)` y `api.getKpis()`.
- [ ] 3.3 Según resultado de 0.3: insertar botón "verificar arreglo" en
      la fila del tablero de `fase2b`, o en la tabla actual de
      `Dashboard.tsx` si el tablero no expone ese punto.
- [ ] 3.4 Crear `KpiWidget.tsx` (o sección en `Dashboard.tsx`): 3 tarjetas
      (MTTR por severidad con badge "aproximado" cuando aplique, % dentro
      de SLA, mini-serie de tendencia por activo).

## Phase 4: Testing

- [ ] 4.1 `tests/test_verificar_cierre.py`: dispatch por herramienta
      (nuclei/zap/no-soportado), 404 cross-tenant, 422
      semgrep/trivy/grype, transición automática con mocks de
      `run_nuclei`/`run_single_target_verification`.
- [ ] 4.2 `tests/test_verificar_cierre.py`: hallazgo persiste → `estado`
      sin cambio, respuesta `reaparece: true`.
- [ ] 4.3 `tests/test_verificar_cierre.py`: falla de ZAP/Docker → 503,
      `estado` sin cambio.
- [ ] 4.4 `tests/test_kpis.py`: MTTR con datos, MTTR `null` sin hallazgos
      cerrados de esa severidad.
- [ ] 4.5 `tests/test_kpis.py`: % SLA con 0 hallazgos abiertos → `null`
      (no división por cero); con todos dentro de plazo → 100.
- [ ] 4.6 `tests/test_kpis.py`: tendencia por activo con 3 scans
      históricos de un mismo `asset_id`.
- [ ] 4.7 Correr suite completa (`pytest tests/`) y confirmar que sigue
      verde (sin romper `test_finding_detail.py` ni los de `fase2a`).
