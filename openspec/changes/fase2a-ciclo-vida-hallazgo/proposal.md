# Proposal: Fase 2a — Ciclo de vida del hallazgo

## Intent

Fase 0 ya agregó las columnas de ciclo de vida a `findings`
(`estado`/`owner_user_id`/`sla_due_at`/`primera_vez_visto`/`ultima_vez_visto`),
pero nada las escribe ni las cambia todavía: todo hallazgo nace y muere en
`descubierto`, sin dueño, sin plazo, y cada escaneo nuevo sobre el mismo
activo duplica hallazgos ya vistos en vez de actualizarlos. Sin esto no hay
bucle de remediación real — es el requisito central de "proteger, no solo
detectar" (Fase 2 de `docs/plan-entregable.md`).

## Scope

### In Scope
- `PATCH /findings/{id}`: transición de `estado` (máquina de estados sobre
  el enum ya existente + `aceptado_riesgo` como salida lateral), reasignación
  de `owner_user_id`, nota de auditoría obligatoria por cambio.
- Tabla `finding_transiciones` (historial de auditoría, nueva — ver design.md).
- Cálculo de `sla_due_at` al insertar, en `POST /scan` y `POST /scan/activo`,
  por severidad y por tenant (config con default embebido — ver design.md).
- Detección de reincidencia (drift): al insertar findings de un scan nuevo,
  comparar contra hallazgos existentes del mismo asset (`tipo`+`endpoint`);
  actualizar `ultima_vez_visto` en vez de duplicar, y reabrir (`descubierto`)
  un hallazgo `cerrado`/`verificado`/`aceptado_riesgo` que reaparece.
- Poblar `scans.asset_id` (columna ya existe, nunca se escribe hoy) — hace
  falta como llave de scoping para el drift.

### Out of Scope
- Frontend (tablero, badges de SLA) — `fase2b-tablero-remediacion`, en paralelo.
- Botón "verificar arreglo" / cierre automático — `fase2c-verificacion-cierre-y-kpis`.
- MTTR/KPIs — `fase2c`.
- Notificaciones de escalamiento de SLA — Fase 5.

## Approach

Extender `api/main.py` (mismo archivo que Fase 0/1 ya tocaron): un endpoint
`PATCH` nuevo con su propia máquina de estados en Python plano (sin LLM,
testeable en aislamiento, mismo espíritu que `gate_autorizacion`), y una
función `_registrar_findings_con_ciclo_de_vida()` compartida por los dos
paths de inserción (`scan()` y `_correr_escaneo_activo_en_background()`) que
calcula SLA y aplica drift antes del INSERT/UPDATE.

## Affected Areas

| Area | Impact | Description |
|------|--------|--------------|
| `api/main.py` | Modified | `PATCH /findings/{id}`, helper de inserción con SLA+drift, `scans.asset_id` poblado |
| `db/schema.sql` | Modified | Tabla nueva `finding_transiciones` |
| `db/connection.py` | Modified | Reconciliación SQLite/Postgres de la tabla nueva |
| `tests/` | New | Tests de transición, SLA, drift, scoping por tenant |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Drift mal scopeado (compara entre tenants o activos distintos) | Med | Comparar siempre `tenant_id` + `asset_id` (nunca solo `tipo`+`endpoint` global) |
| `fase2b` construye contra un contrato que luego cambia | Low | Contrato exacto fijado en design.md antes de cualquier código |
| `scans.asset_id` nulo en scans viejos rompe el drift para historial previo | Med | Drift solo compara contra scans del mismo asset con `asset_id` no nulo; scans viejos simplemente no participan (documentado, no silencioso) |

## Rollback Plan

Todo aditivo: tabla nueva, columna ya existente que empieza a poblarse,
endpoint nuevo. `git revert` sin downtime ni migración destructiva —
`finding_transiciones` puede quedar huérfana sin romper nada si se revierte.

## Dependencies

- Requiere fase0-fundaciones-datos (ya aplicada): columnas de `findings`.
- `fase2b-tablero-remediacion` (frontend, en paralelo) consume el contrato
  de `PATCH /findings/{id}` fijado en este design.md sin ver código.
- `fase2c-verificacion-cierre-y-kpis` (futura) reutilizará
  `finding_transiciones` para MTTR.

## Success Criteria

- [ ] `PATCH /findings/{id}` valida transiciones, rechaza inválidas con 409/422, persiste nota.
- [ ] `sla_due_at` se calcula en ambos paths de inserción según severidad.
- [ ] Un segundo scan sobre el mismo asset actualiza `ultima_vez_visto` en vez de duplicar, y reabre hallazgos cerrados que reaparecen.
- [ ] Suite `pytest tests/` sigue verde.
