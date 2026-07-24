# Tasks: Confianza y calidad — evals de agentes IA y registro de auditoría

> Gate previo (condición de arranque, no tarea): `sdd-apply` de este change se
> corre DESPUÉS de los hermanos que tocan `api/main.py` / `Dashboard.tsx`
> (`fase3*`, `fase4`, `fase5`, `fase6`), por ser aditivo — el orquestador
> confirma el orden antes de lanzar apply.
>
> Split de repos: las Fases 1-5 son del repo PÚBLICO (`ciberseguridad`) y son
> lo único que ejecuta este `sdd-apply`. La Fase 6 (repo PRIVADO) es un plan
> documentado — se ejecuta en `vigia_core_private/` con su propio commit, NO en
> esta ronda ni mezclado con el repo público.

## Phase 1: Auditoría — backend (`api/main.py`)

- [ ] 1.1 Agregar modelos `TraceEventOut{timestamp,agente,accion,resultado}` y
      `AuditEventOut(TraceEventOut){scan_id,target}`.
- [ ] 1.2 `GET /scans/{scan_id}/trace`: leer `scans.trace_log_json` scoped por
      `tenant_id`, parsear JSON, devolver lista; 404 cross-tenant (patrón
      `detalle_scan`); `[]` → 200 lista vacía.
- [ ] 1.3 `GET /audit?limit=`: `SELECT id,target,created_at,trace_log_json FROM
      scans WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?`, expandir cada
      traza a `AuditEventOut` en Python, anotando `scan_id`/`target`.

## Phase 2: Auditoría — frontend

- [ ] 2.1 `frontend/src/api.ts`: tipos `TraceEvent`/`AuditEvent` + métodos
      `api.getScanTrace(id)`, `api.getAudit(limit?)`.
- [ ] 2.2 Crear `frontend/src/components/AuditLog.tsx`: timeline de solo
      lectura (sin controles de mutación); estado "sin traza registrada" para
      traza vacía.
- [ ] 2.3 Montar `AuditLog` en `frontend/src/pages/Dashboard.tsx` sin romper el
      layout existente.

## Phase 3: Evals — FP-rate y regresión (`eval/`)

- [ ] 3.1 `eval/run_eval.py`: agregar `fp_rate = fp/(tp+fp)` a
      `compute_metrics` y a `print_report`; flag `--json` que emite métricas
      parseables.
- [ ] 3.2 Crear `eval/regression_runner.py`: carga `baseline.json`, corre
      `run_eval.py`, compara recall/fp_rate con umbral; exit≠0 si degrada;
      `--update-baseline` explícito (no reescribe solo).
- [ ] 3.3 Crear `eval/baseline.json` inicial a partir de una corrida contra
      `sample_findings.json` (placeholder; se re-genera contra corrida real).

## Phase 4: Evals — calidad del fallback público (`eval/agent_quality/`)

- [ ] 4.1 Crear `eval/agent_quality/` con dataset de hallazgos verificados de
      entrada para priorización/remediación/revisión.
- [ ] 4.2 Checks deterministas: conformidad de esquema JSON de salida por
      agente + ausencia de marcadores de alucinación/formato inválido.
- [ ] 4.3 Check de orden por severidad técnica del fallback de priorización.
- [ ] 4.4 (Opcional, aislado tras flag) LLM-as-judge — NO en la suite `pytest`
      por defecto.

## Phase 5: Tests y docs (repo público)

- [ ] 5.1 `tests/test_audit_log.py`: traza propia, `[]`→200, 404 cross-tenant,
      aislamiento de `GET /audit` entre tenants (patrón
      `test_multi_tenant_isolation.py`).
- [ ] 5.2 `tests/test_agent_quality_fallback.py`: fuerza
      `_MODO=fallback_publico`; esquema + orden; pasa sin `vigia_core_private`
      instalado.
- [ ] 5.3 Test de `regression_runner`: sube/baja simulada de métricas →
      exit code correcto.
- [ ] 5.4 `eval/README.md`: documentar FP-rate, runner de regresión y evals de
      calidad; `docs/open-core.md`: documentar el split público=fallback /
      privado=contenido.
- [ ] 5.5 Correr `pytest tests/` (verde) y `tsc -b && vite build` (sin errores).

## Phase 6: [REPO PRIVADO — PLAN, NO ejecutar esta ronda]

> Se ejecuta en `vigia_core_private/`, commit aparte, nunca en el repo público.

- [ ] 6.1 `vigia_core_private/tests/test_priorizacion_contenido.py`: aserta la
      ponderación por impacto de negocio del `SYSTEM_PROMPT` privado.
- [ ] 6.2 `vigia_core_private/tests/test_remediacion_contenido.py`: aserta
      estilo/estructura del prompt privado de remediación.
- [ ] 6.3 `vigia_core_private/tests/test_revision_ia_contenido.py`: aserta las
      5 categorías finas del prompt privado de revisión IA.
- [ ] 6.4 Fixtures de calidad en `vigia_core_private/eval/`; correr `pytest`
      DENTRO del repo privado (verde).
