# Proposal: Confianza y calidad — evals de agentes IA y registro de auditoría

## Intent

Fase 7 del plan entregable (transversal): dos brechas que separan "demo" de
"producto vendible con confianza mes a mes".

1. **Calidad de agentes IA medible**: el loop de eval existente (`eval/`,
   sección 8.2) mide SOLO precisión/recall de DETECCIÓN (hallazgos vs
   ground truth). No hay ninguna medición de la CALIDAD de los agentes de
   razonamiento — priorización, remediación, revisión IA — ni una barrera de
   regresión automatizada que ate `eval/prompt_versions/` a métricas. Tampoco
   una medición explícita de reducción de falsos positivos como métrica de
   producto.
2. **Auditoría/trazabilidad expuesta**: `trace_log` ya se puebla en cada nodo
   del grafo (`make_trace_event` → `scans.trace_log_json`, por tenant) pero
   **ningún endpoint lo expone** — `GET /scans/{id}` (`ScanDetail`) no lo
   devuelve, y solo el `POST /scan` síncrono lo retorna inline al crearse. No
   hay registro de auditoría consultable ni UI de solo lectura.

## Scope

### In Scope
- **Extender `eval/`** (no crear de cero): runner de regresión con baseline
  versionado + gate (falla si baja recall / suben FP), y evals de calidad para
  el COMPORTAMIENTO DEL FALLBACK PÚBLICO de priorización/remediación/revisión
  (conformidad de esquema JSON, orden por severidad, no-alucinación) —
  determinista donde se pueda, LLM-as-judge donde haga falta.
- **Métrica de reducción de falsos positivos** como KPI del harness, sobre los
  ground truth ya existentes (Juice Shop / DVWA / WebGoat).
- **Registro de auditoría por tenant**: `GET /audit` (+ `GET /scans/{id}/trace`),
  solo lectura, scoped por `tenant_id`, sobre el `trace_log` ya persistido.
- **UI de solo lectura** del registro de auditoría (timeline por scan).
- **Plan separado para el repo privado** (documentado, NO ejecutado esta ronda):
  evals que asertan el CONTENIDO de los prompts privados
  (`vigia_core_private/`), espejando el patrón ya establecido de
  `test_cumplimiento_categorizacion.py`.

### Out of Scope
- Ejecutar cualquier cambio en `vigia_core_private/` (se documenta como plan
  con su propio commit en ese repo; esta ronda es solo planificación pública).
- LangSmith / trazabilidad SaaS externa (el `trace_log` propio ya cubre la
  necesidad on-prem; se menciona como evolución futura, no se cablea).
- Escritura/mutación del `trace_log` desde la API (es append-only del grafo;
  el endpoint es solo lectura).
- Cambios a los prompts de los agentes (esto MIDE calidad, no re-prompt-ea).

## Approach

Reusar el harness `eval/run_eval.py` y la convención `eval/prompt_versions/`
tal cual, agregando: (a) un runner de regresión con `baseline.json` +
comparación con umbral (el README ya describe el proceso manual — se
automatiza), (b) datasets/fixtures de calidad de agentes que corren contra el
FALLBACK público (nunca contra IP privada), (c) una métrica FP-rate derivada
de los TP/FP que `run_eval.py` ya calcula. Para auditoría, exponer el
`trace_log` ya persistido con endpoints aditivos de solo lectura + un
componente de timeline, sin tocar cómo se escribe. El split público/privado se
detalla explícitamente en `design.md` (qué archivo toca cada repo).

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `eval/run_eval.py` | Modified | Exponer FP-rate; hook para runner de regresión |
| `eval/regression_runner.py` | New | Baseline + gate de regresión automatizado |
| `eval/baseline.json` | New | Métricas de referencia versionadas |
| `eval/agent_quality/` | New | Datasets + checks de calidad del fallback público |
| `eval/README.md` | Modified | Documentar los nuevos evals de calidad + FP metric |
| `api/main.py` | Modified | `GET /audit`, `GET /scans/{id}/trace` (solo lectura) |
| `frontend/src/api.ts` | Modified | Tipos + métodos de auditoría |
| `frontend/src/components/AuditLog.tsx` | New | Timeline de solo lectura |
| `frontend/src/pages/Dashboard.tsx` | Modified | Montaje del registro de auditoría |
| `tests/` | New | Tests de endpoints de auditoría + aislamiento por tenant |
| `docs/open-core.md` | Modified | Documentar split de los evals de contenido privado |
| **[PRIVADO]** `vigia_core_private/tests/` + `eval/` | Planned (no ejecutado) | Evals de contenido de prompts privados — commit aparte en ese repo |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Un eval de calidad público termina asertando contenido de IP privada por accidente | Medium | Regla explícita en design.md: los evals públicos corren SOLO contra `_MODO=fallback_publico`; los de contenido privado viven en `vigia_core_private/` |
| Conflicto en `api/main.py` / `Dashboard.tsx` con changes hermanos (`fase3*`,`fase4`,`fase5`,`fase6`) aplicándose en paralelo | High | Endpoints/UI aditivos; `sdd-apply` de este change se secuencia tras los hermanos (orden lo impone el orquestador, no este proposal) |
| LLM-as-judge introduce no-determinismo/costo en CI | Medium | Preferir checks deterministas; el judge se aísla en un runner opcional marcado, no en la suite `pytest` por defecto |
| Baseline de regresión se "actualiza" ocultando una degradación real | Medium | Actualización de baseline es paso manual explícito y revisado (mismo criterio que ya exige `prompt_versions/README.md`) |
| `trace_log` de scans viejos vacío/heterogéneo | Low | Endpoint tolera `[]`; UI muestra estado "sin traza" sin romper |

## Rollback Plan

Todo es aditivo. Auditoría: eliminar las 2 rutas nuevas de `api/main.py`, el
componente `AuditLog.tsx` y su montaje — sin migración de datos (el
`trace_log_json` ya existía). Evals: son archivos nuevos bajo `eval/` +
`tests/`; borrarlos no afecta runtime de producto. Nada cambia el esquema SQL
ni contratos de endpoints existentes.

## Dependencies

- Ninguna dependencia dura de los changes hermanos para el DISEÑO. Para
  `sdd-apply`: se secuencia DESPUÉS de los que tocan `api/main.py` /
  `Dashboard.tsx` (`fase3*`,`fase4`,`fase5`,`fase6`) para evitar conflictos —
  orden impuesto por el orquestador.
- El repo privado `vigia_core_private/` debe existir para aplicar la parte
  privada (fuera de esta ronda), con su propio commit aislado.

## Success Criteria

- [ ] Runner de regresión corre `eval/` contra un baseline y falla si baja
      recall o sube FP-rate, sin intervención manual.
- [ ] Existen evals de calidad para el fallback público de
      priorización/remediación/revisión (esquema + orden + no-alucinación).
- [ ] FP-rate expuesto como métrica del harness sobre los ground truth
      existentes.
- [ ] `GET /audit` y `GET /scans/{id}/trace` devuelven el `trace_log` scoped
      por tenant, solo lectura, con aislamiento verificado por test.
- [ ] UI de timeline de auditoría de solo lectura sin romper `Dashboard.tsx`.
- [ ] `design.md` documenta explícitamente qué archivos toca `sdd-apply` en
      CADA repo (público/privado por separado), y el split queda reflejado en
      `docs/open-core.md`.
