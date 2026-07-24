# Design: Confianza y calidad — evals de agentes IA y registro de auditoría

## Technical Approach

Dos piezas independientes que comparten el objetivo "confianza medible":

1. **Auditoría** — puramente aditiva sobre datos ya persistidos. El
   `trace_log` se escribe hoy en cada nodo del grafo vía
   `orchestrator/state.py::make_trace_event` (`TraceEvent{timestamp, agente,
   accion, resultado}`, reducer `operator.add`) y se guarda en
   `scans.trace_log_json` (`db/schema.sql:97`) al persistir el scan
   (`api/main.py` `POST /scan` ~1772, `POST /scan/activo` ~2124). **Ningún
   endpoint lo devuelve**: `ScanDetail` (`GET /scans/{id}`, ~2169) omite el
   campo. Este change agrega 2 rutas de solo lectura que leen ese JSON ya
   almacenado, más una UI de timeline. No se toca cómo se escribe el trace,
   ni el esquema SQL.

2. **Evals de calidad** — extensión de `eval/`, que hoy solo mide detección
   (precisión/recall de hallazgos vs ground truth en `run_eval.py`). Se
   agrega: FP-rate al reporte, un runner de regresión con baseline, y evals de
   calidad de los agentes de razonamiento corriendo contra su **fallback
   público**. Los evals que asertan el CONTENIDO de los prompts privados se
   PLANEAN aquí pero se ejecutan en el repo privado (ver split abajo).

## Architecture Decisions

### Decision: Auditoría solo-lectura sobre `trace_log_json`, sin tabla nueva

**Choice**: leer y proyectar `scans.trace_log_json` en los endpoints; no crear
tabla `audit_events`.
**Alternatives considered**: (a) tabla de auditoría normalizada con una fila
por evento; (b) log estructurado a archivo/servicio externo (LangSmith).
**Rationale**: el dato ya está persistido por scan y por tenant. Una tabla
nueva duplicaría la fuente de verdad y requeriría migración + doble escritura
en el grafo. Para "registro de auditoría por tenant" (alcance de Fase 7) leer
el JSON existente es suficiente y retrocompatible. LangSmith queda como
evolución futura (fuera de alcance, ver proposal).

### Decision: `GET /audit` agrega en Python desde `scans`, no con SQL JSON

**Choice**: `SELECT id, target, created_at, trace_log_json FROM scans WHERE
tenant_id=? ORDER BY created_at DESC LIMIT ?`, luego expandir cada
`trace_log_json` a eventos en Python, anotando `scan_id`/`target`.
**Alternatives considered**: funciones JSON de SQLite (`json_each`).
**Rationale**: mismo criterio que `fase2c` (`GET /kpis` calcula en Python para
no acoplarse a funciones de fecha/JSON de SQLite y mantener paridad con el
fallback Postgres). Evita SQL divergente entre backends.

### Decision: Evals de calidad corren SOLO contra `_MODO == "fallback_publico"`

**Choice**: la suite pública verifica el comportamiento degradado (esquema
JSON, orden por severidad, no-alucinación), nunca el texto de los prompts
privados.
**Alternatives considered**: evals de contenido en el repo público con
`skipif` cuando falta el paquete privado.
**Rationale**: `docs/open-core.md` ya fijó el patrón — asertar contenido de IP
privada en el repo público falla por diseño o filtra IP. `skipif` deja tests
que "no corren" en CI público (ruido). El split limpio (público=fallback,
privado=contenido) espeja `test_cumplimiento_fallback.py` (público) vs
`vigia_core_private/tests/test_cumplimiento_categorizacion.py` (privado).

### Decision: Runner de regresión separado de `pytest`, judge opcional

**Choice**: `eval/regression_runner.py` como script standalone (como
`run_eval.py`); checks deterministas en `pytest`, LLM-as-judge aislado tras un
flag/marcador, no en la suite por defecto.
**Rationale**: `run_eval.py` ya es standalone y sin deps de red. Meter un juez
LLM no-determinista y con costo en `pytest tests/` rompería CI. El gate de
regresión es una herramienta de release, no un test unitario.

## Data Flow — Auditoría

    grafo (nodos) ──make_trace_event──▶ PipelineState.trace_log
         │ (operator.add)
         ▼
    POST /scan · /scan/activo ──json.dumps──▶ scans.trace_log_json  (YA EXISTE)
         ┊  (NUEVO, solo lectura)
         ▼
    GET /scans/{id}/trace  ─┐
    GET /audit             ─┴─▶ AuditLog.tsx (timeline solo-lectura)

## Split público / privado — qué toca `sdd-apply` en CADA repo

**IMPORTANTE**: esta ronda planifica ambos, pero `sdd-apply` de esta ronda
ejecuta SOLO la columna pública. La columna privada es un plan con su propio
commit en `vigia_core_private/` (repo separado), nunca mezclado con el público.

### Repo PÚBLICO (`ciberseguridad`) — lo aplica este `sdd-apply`

| File | Action | Description |
|------|--------|-------------|
| `api/main.py` | Modify | Modelos `TraceEventOut`/`AuditEventOut`; `GET /scans/{id}/trace` (404 cross-tenant); `GET /audit` (agrega scans del tenant, paginado) |
| `frontend/src/api.ts` | Modify | Tipos `TraceEvent`/`AuditEvent` + `api.getScanTrace(id)`, `api.getAudit()` |
| `frontend/src/components/AuditLog.tsx` | Create | Timeline solo-lectura (sin controles de mutación) |
| `frontend/src/pages/Dashboard.tsx` | Modify | Montaje del registro de auditoría |
| `eval/run_eval.py` | Modify | `fp_rate` en `compute_metrics`/reporte + salida JSON parseable |
| `eval/regression_runner.py` | Create | Baseline + gate de regresión (exit≠0 si degrada) |
| `eval/baseline.json` | Create | Métricas de referencia versionadas |
| `eval/agent_quality/` | Create | Datasets + checks de calidad del fallback público (priorización/remediación/revisión) |
| `eval/README.md` | Modify | Documentar FP-rate, runner de regresión y evals de calidad |
| `tests/test_audit_log.py` | Create | Traza propia, 404 cross-tenant, traza vacía, aislamiento tenant en `GET /audit` |
| `tests/test_agent_quality_fallback.py` | Create | Esquema de salida + orden del fallback público, sin depender de IP privada |
| `docs/open-core.md` | Modify | Documentar el split de los evals de contenido (público=fallback, privado=contenido) |

### Repo PRIVADO (`vigia_core_private`) — plan, NO se ejecuta esta ronda

| File | Action | Description |
|------|--------|-------------|
| `vigia_core_private/tests/test_priorizacion_contenido.py` | Create (planned) | Aserta ponderación por impacto de negocio del `SYSTEM_PROMPT` real de priorización |
| `vigia_core_private/tests/test_remediacion_contenido.py` | Create (planned) | Aserta estilo/estructura del prompt real de remediación |
| `vigia_core_private/tests/test_revision_ia_contenido.py` | Create (planned) | Aserta las 5 categorías finas del prompt privado de revisión IA |
| `vigia_core_private/eval/` (fixtures) | Create (planned) | Datasets de calidad que referencian contenido privado |

Regla de routing (de `MEMORY.md` / `open-core.md`): todo lo que asere contenido
de prompts privados vive en `vigia_core_private/`, commit aparte, nunca en el
repo público.

## Interfaces / Contracts

```python
# api/main.py — solo lectura
class TraceEventOut(BaseModel):
    timestamp: str; agente: str; accion: str; resultado: str

class AuditEventOut(TraceEventOut):
    scan_id: str; target: str

@app.get("/scans/{scan_id}/trace", response_model=list[TraceEventOut])
@app.get("/audit", response_model=list[AuditEventOut])  # ?limit=, scoped por tenant
```

```python
# eval/regression_runner.py
# exit 0 si recall y fp_rate dentro de umbral vs baseline.json; exit 1 si degrada.
# --update-baseline es explícito (no se reescribe solo).
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit (público) | FP-rate en `compute_metrics`; gate de regresión sube/baja | pytest sobre funciones puras |
| Unit (público) | Esquema + orden del fallback de priorización/remediación/revisión | pytest forzando `_MODO=fallback_publico` |
| Integration (público) | `GET /scans/{id}/trace`, `GET /audit`: 200, traza vacía, 404 cross-tenant, aislamiento | TestClient + fixtures multi-tenant (patrón `test_multi_tenant_isolation.py`) |
| Frontend | Build `tsc -b && vite build`; render de timeline | compilación + smoke |
| Privado (planned) | Contenido de prompts privados | pytest DENTRO de `vigia_core_private/` |

## Migration / Rollout

No requiere migración: `trace_log_json` ya existe con default `'[]'`. Endpoints
y UI aditivos. Los archivos de `eval/`/`tests/` no afectan runtime de producto.

## Open Questions

- [ ] ¿`GET /audit` necesita filtro por rango de fechas / por `agente` en v1, o
      basta `limit` + orden desc? (default asumido: `limit` + desc; filtros = futuro).
- [ ] Umbral exacto del gate de regresión (FP-rate y recall) — se fija al
      generar el primer `baseline.json` contra una corrida real, no antes.
- [ ] Secuencia de `sdd-apply` vs hermanos que tocan `api/main.py`/`Dashboard.tsx`
      (`fase3*`,`fase4`,`fase5`,`fase6`) — la impone el orquestador; este change
      es aditivo y se aplica después para evitar conflictos de merge.
