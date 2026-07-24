# Proposal: Fase 0 — Fundaciones de datos

## Intent

Vigia detecta hallazgos reales pero no tiene ciclo de vida, ni autorización
persistente de escaneo activo, ni vigilancia de marca configurable. Tres gaps
de esquema bloquean el resto del plan (`docs/plan-entregable.md`, Fase 0-3):
`findings` no tiene estado/dueño/SLA, `assets` no persiste autorización de
escaneo activo (hoy `autorizacion_firmada` es un booleano por-request en
`scans`, ver `api/scheduler.py` líneas 1-14), y no existen tablas para marcas
vigiladas, notificaciones ni reportes DMARC. Este change agrega solo el
schema y la migración idempotente — sin lógica de negocio ni endpoints.

## Scope

### In Scope
- `findings`: columnas `estado`, `owner_user_id`, `sla_due_at`,
  `estado_verificacion`, `primera_vez_visto`, `ultima_vez_visto`.
- `assets`: columnas `autorizacion_activa`, `autorizacion_doc_url`,
  `autorizado_por`, `autorizado_en`.
- Tablas nuevas: `watched_brands`, `notifications`, `notification_rules`,
  `dmarc_reports`.
- Migración idempotente SQLite + Postgres siguiendo el patrón de
  `_ASSET_VERIFICATION_COLUMNS`/`_ensure_sqlite_asset_verification_columns`
  en `db/connection.py`.

### Out of Scope
- Endpoints, UI, lógica de transición de estado (Fase 2), lógica CertStream
  sobre `watched_brands` (Fase 3b), envío real de notificaciones (Fase 5),
  ingestión de reportes DMARC (Fase 3e). Solo el schema de esas tablas.
- Backfill de `primera_vez_visto`/`ultima_vez_visto` sobre findings
  históricos (se decide en design: default a `created_at` o NULL).

## Approach

Extender `db/schema.sql` (CREATE TABLE IF NOT EXISTS + columnas nuevas en el
CREATE de `findings`/`assets` para DBs creadas desde cero) y `db/connection.py`
(listas `_FINDINGS_COLUMNS`/`_ASSETS_AUTORIZACION_COLUMNS` análogas a
`_ASSET_VERIFICATION_COLUMNS`, reconciliadas vía `PRAGMA table_info` en
SQLite y `ADD COLUMN IF NOT EXISTS` en Postgres).

## Affected Areas

| Area | Impact | Description |
|------|--------|--------------|
| `db/schema.sql` | Modified | Nuevas columnas + 4 tablas nuevas |
| `db/connection.py` | Modified | Reconciliación idempotente de columnas nuevas |
| `tests/` | New (test file) | Test de migración idempotente SQLite+Postgres |
| `vigia_core_private/` | Not applicable | Solo schema/infra, no prompts ni mapeo ISO/Ley |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Romper INSERTs existentes en `api/main.py` que no listan columnas nuevas | Low | Todas las columnas nuevas son nullable o con DEFAULT; INSERTs explícitos por nombre siguen funcionando |
| Migración Postgres no probada en CI | Medium | Reusar patrón ya validado contra Postgres real (ver `eval/live_run_report.md`) |
| CHECK constraint de `estado` muy rígido para transiciones futuras (Fase 2) | Low | Enum se decide en design.md con input de Fase 2 |

## Rollback Plan

Columnas y tablas nuevas son aditivas y nullable/con default — revertir es
un `git revert` del commit de schema/connection; ninguna columna existente
cambia de tipo ni se elimina. Si una DB ya migrada necesita revertir, las
columnas nuevas quedan sin usar (no requiere DROP para restaurar el
comportamiento anterior).

## Dependencies

- Ninguna externa. Requiere Postgres real disponible (Docker) para validar
  la rama del criterio de aceptación contra ese motor.

## Success Criteria

- [ ] Migración idempotente corre limpio en SQLite nuevo y ya existente
- [ ] Migración idempotente corre limpio en Postgres nuevo y ya existente
- [ ] Suite `tests/` (12 archivos) sigue en verde
- [ ] `vigia_core_private` no se toca
