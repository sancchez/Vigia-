# Tasks: Fase 2a — Ciclo de vida del hallazgo

## Phase 1: Schema (fundación)

- [x] 1.1 En `db/schema.sql`, agregar `CREATE TABLE IF NOT EXISTS finding_transiciones` (ver design.md, campo por campo) + índice `idx_finding_transiciones_finding_id`.
- [x] 1.2 En `db/schema.sql`, agregar `CREATE TABLE IF NOT EXISTS tenant_sla_config` (ver design.md).
- [x] 1.3 En `db/connection.py`, confirmar que `executescript`/`_get_pg_conn` ya crean ambas tablas (tablas nuevas, no ALTER de existentes) — no requiere reconciliación adicional; documentar esa conclusión en un comentario corto.
- [x] 1.4 Test `tests/test_schema_migration.py` (extender el existente si ya cubre Fase 0): confirmar que ambas tablas se crean sobre una DB `:memory:` nueva.

## Phase 2: Lógica de dominio (Python plano)

- [x] 2.1 En `api/main.py`, agregar `TRANSICIONES_VALIDAS: dict[str, set[str]]` y `DEFAULT_SLA_DIAS: dict[str, int | None]` (valores exactos de design.md).
- [x] 2.2 Agregar `_calcular_sla_due_at(conn, tenant_id: str, severidad: str, created_at: str) -> str | None`: lee `tenant_sla_config` si existe, si no usa `DEFAULT_SLA_DIAS`.
- [x] 2.3 Test `tests/test_sla_calculo.py`: crítico→7d default, low/info→`None`, tenant con `tenant_sla_config` propia anula el default.

## Phase 3: Drift / reincidencia

- [x] 3.1 En `api/main.py`, agregar `_registrar_findings_con_ciclo_de_vida(conn, tenant_id, scan_id, asset_id, hallazgos, now)` (firma y comportamiento exactos de design.md: SELECT match por `tenant_id+asset_id+tipo+endpoint` vía `scans.asset_id`, UPDATE si match, INSERT si no).
- [x] 3.2 Dentro de esa función, si el match está en `cerrado/verificado/aceptado_riesgo`: UPDATE a `descubierto` + INSERT en `finding_transiciones` con `changed_by=NULL`, nota fija "reincidencia detectada automáticamente".
- [x] 3.3 En `_asset_verificado_para_target`-callers (`scan()` línea ~1030, `escaneo_activo_async` línea ~1436), dejar de descartar el asset resuelto (`_asset` → `asset`) y pasar `asset["id"]` al `INSERT INTO scans` (agregar columna `asset_id` al INSERT en ambos).
- [x] 3.4 Reemplazar los bucles `for hallazgo in ...: conn.execute(INSERT INTO findings ...)` en `scan()` (verified + antisuplantacion) y en `_correr_escaneo_activo_en_background` por llamadas a `_registrar_findings_con_ciclo_de_vida`.
- [x] 3.5 Test `tests/test_drift_reincidencia.py`: segundo scan mismo asset/tipo/endpoint → no duplica, actualiza `ultima_vez_visto`; finding `cerrado` reaparece → vuelve a `descubierto` + fila en `finding_transiciones`; scan con `asset_id=NULL` no rompe ni genera falso match.

## Phase 4: `PATCH /findings/{id}`

- [x] 4.1 Extender `FindingOut` y `FindingDetailOut` con los 6 campos nuevos (`estado, owner_user_id, owner_email, sla_due_at, primera_vez_visto, ultima_vez_visto`), incluyendo el `LEFT JOIN users` para `owner_email` en las 3 queries que arman estos modelos (`listar_findings`, `detalle_finding`, y la nueva de PATCH).
- [x] 4.2 Agregar `FindingPatchIn` (BaseModel: `estado`, `owner_user_id`, `nota`, todos opcionales).
- [x] 4.3 Implementar `PATCH /findings/{finding_id}`: 404 (no existe/otro tenant), 422 (body vacío; `nota` faltante con `estado`; `owner_user_id` fuera del tenant), 409 (transición no está en `TRANSICIONES_VALIDAS[estado_actual]`), 200 con `FindingOut` actualizado.
- [x] 4.4 Dentro del PATCH, INSERT en `finding_transiciones` con `changed_by=user.user_id`.
- [x] 4.5 Test `tests/test_finding_patch.py`: un caso por escenario de spec.md (transición válida, inválida→409, salida lateral a `aceptado_riesgo`, nota faltante→422, reasignar owner sin nota, owner ajeno→422, 404 otro tenant, 401 sin auth).

## Phase 5: Verificación end-to-end

- [x] 5.1 Correr `pytest tests/` completo — suite debe quedar verde (incluye `test_finding_detail.py` existente, que no debe romperse por los campos nuevos).
- [x] 5.2 Confirmar manualmente (o con test) que `GET /findings` y `GET /findings/{id}` devuelven los 6 campos nuevos con valores coherentes tras un scan real/mockeado. Ver `tests/test_ciclo_vida_integracion.py`.
- [x] 5.3 Confirmar que el contrato de `PATCH /findings/{id}` fijado en design.md no cambió durante la implementación (si cambió, actualizar design.md ANTES de cerrar, porque `fase2b` construye contra ese documento). Confirmado sin cambios: `FindingOut`/`FindingPatchIn`/errores 404/409/422/401 implementados exactamente como en design.md.
