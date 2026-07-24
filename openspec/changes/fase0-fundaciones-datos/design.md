# Design: Fase 0 — Fundaciones de datos

## Technical Approach

Extender `db/schema.sql` (CREATE TABLE nuevo estado para DBs desde cero) y
`db/connection.py` (reconciliación idempotente para DBs existentes), mismo
patrón dual ya probado con `_ASSET_VERIFICATION_COLUMNS`. Todo aditivo:
columnas nullable/con DEFAULT, tablas nuevas con `CREATE TABLE IF NOT
EXISTS`. Ningún endpoint ni lógica de negocio se toca (fuera de scope).

## Architecture Decisions

### Decision: Listas de columnas separadas por tabla, no genéricas

**Choice**: `_FINDINGS_LIFECYCLE_COLUMNS` y `_ASSETS_AUTORIZACION_COLUMNS`,
dos listas `list[tuple[str, str]]`, cada una con su propia función
`_ensure_sqlite_findings_columns(conn)` / `_ensure_sqlite_assets_
autorizacion_columns(conn)`.
**Alternativas**: una función genérica `_ensure_sqlite_columns(conn, table,
columns)`.
**Rationale**: el patrón existente (`_ensure_sqlite_asset_verification_
columns`) ya está hard-coded por tabla y funciona bien para el volumen
actual (2 tablas, 10 columnas totales). Generalizar ahora es una
refactorización del código existente fuera del scope de este change
(tocaría `_ensure_sqlite_asset_verification_columns`, ya en producción, sin
necesidad). Seguir el patrón tal cual minimiza el diff y el riesgo.

### Decision: Sí incluir CHECK constraint en el ALTER TABLE de SQLite

**Choice**: `("estado", "TEXT NOT NULL DEFAULT 'descubierto' CHECK(estado IN
('descubierto','triage','asignado','en_progreso','mitigado','verificado',
'cerrado','aceptado_riesgo'))")` — CHECK completo en la tupla de ALTER.
**Alternativas**: omitir el CHECK en el ALTER (como hace hoy
`verification_method`, que en `_ASSET_VERIFICATION_COLUMNS` es solo `"TEXT"`
sin el CHECK que sí tiene en `schema.sql`).
**Rationale**: verificado empíricamente contra sqlite3 3.50.4 (misma versión
que documenta `schema.sql`): `ALTER TABLE t ADD COLUMN estado TEXT NOT NULL
DEFAULT 'descubierto' CHECK(estado IN (...))` es sintaxis válida, la
columna se puebla con el default en filas existentes, y el CHECK SÍ se
enforce en inserts nuevos (confirmado con un INSERT inválido → 
`IntegrityError`). El precedente de `verification_method` omitiendo el
CHECK es una inconsistencia existente, no una limitación real de SQLite —
para `estado` sí conviene el CHECK inline porque da paridad de enforcement
entre DB fresca (CREATE TABLE) y DB migrada (ALTER TABLE), cerrando el gap
que sí existe hoy para `verification_method`. No se toca ese código
existente (fuera de scope), solo se documenta el hallazgo.

### Decision: `dmarc_reports` con `tenant_id` directo (no vía asset_id)

**Choice**: `tenant_id TEXT NOT NULL REFERENCES tenants(id)`, más `dominio
TEXT NOT NULL` (no FK a `assets` — un reporte DMARC llega por dominio
exacto vía email, no hay garantía de que ese dominio tenga fila en `assets`
al momento de la ingesta).
**Alternativas**: FK a `asset_id`.
**Rationale**: la ingesta de DMARC (Fase 3e) llega por email/API externa,
antes de que el tenant necesariamente haya registrado el dominio como
asset. Acoplar a `asset_id` obligaría a resolver o crear el asset en el
mismo insert. Aislar por `tenant_id` directo replica el patrón de `scans`
(que referencia `asset_id` nullable con `ON DELETE SET NULL` pero no
depende de él para el aislamiento).

## Data Flow

```
get_conn() [SQLite]
  └─ conn.executescript(schema.sql)          # CREATE TABLE/INDEX IF NOT EXISTS (idempotente)
  └─ _ensure_sqlite_asset_verification_columns(conn)   # ya existe
  └─ _ensure_sqlite_findings_columns(conn)             # NUEVO
  └─ _ensure_sqlite_assets_autorizacion_columns(conn)  # NUEVO
  └─ conn.commit()

_get_pg_conn(url) [Postgres, una vez por url]
  └─ conn.execute(schema.sql)                 # simple query, multi-statement
  └─ ALTER TABLE assets ADD COLUMN IF NOT EXISTS ... (verification, ya existe)
  └─ ALTER TABLE findings ADD COLUMN IF NOT EXISTS ... (NUEVO, 6 columnas)
  └─ ALTER TABLE assets ADD COLUMN IF NOT EXISTS ... (NUEVO, 4 columnas)
  └─ conn.commit(); _pg_schema_ready.add(url)
```

## File Changes

| File | Action | Description |
|------|--------|--------------|
| `db/schema.sql` | Modify | Columnas nuevas en CREATE de `findings`/`assets`; 4 CREATE TABLE nuevos (`watched_brands`, `notifications`, `notification_rules`, `dmarc_reports`) + índices `idx_*_tenant_id` |
| `db/connection.py` | Modify | 2 listas de columnas nuevas, 2 funciones `_ensure_sqlite_*`, llamadas en `get_conn()`, ALTERs análogos en `_get_pg_conn()` |
| `tests/test_schema_migration.py` | Create | Test de idempotencia SQLite (Postgres opcional, skip si no hay `DATABASE_URL` de test) |

## Interfaces / Contracts

```python
_FINDINGS_LIFECYCLE_COLUMNS: list[tuple[str, str]] = [
    ("estado", "TEXT NOT NULL DEFAULT 'descubierto' CHECK(estado IN "
               "('descubierto','triage','asignado','en_progreso','mitigado',"
               "'verificado','cerrado','aceptado_riesgo'))"),
    ("owner_user_id", "TEXT"),
    ("sla_due_at", "TIMESTAMP"),
    ("estado_verificacion", "TEXT"),
    ("primera_vez_visto", "TIMESTAMP"),
    ("ultima_vez_visto", "TIMESTAMP"),
]
_ASSETS_AUTORIZACION_COLUMNS: list[tuple[str, str]] = [
    ("autorizacion_activa", "INTEGER NOT NULL DEFAULT 0"),
    ("autorizacion_doc_url", "TEXT"),
    ("autorizado_por", "TEXT"),
    ("autorizado_en", "TIMESTAMP"),
]
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|--------------|----------|
| Unit (SQLite) | Reconciliación sobre DB "vieja" | Crear `findings`/`assets` con el CREATE TABLE pre-Fase-0 (hardcoded en el test, sin columnas nuevas) en `:memory:`, llamar la función de reconciliación, `PRAGMA table_info` para confirmar columnas + defaults |
| Unit (SQLite) | Idempotencia | Llamar `get_conn()` dos veces sobre el mismo archivo temporal, no debe lanzar excepción |
| Unit (SQLite) | CHECK constraint enforced | INSERT con `estado='bogus'` debe lanzar `sqlite3.IntegrityError` |
| Integration (Postgres, opcional) | Mismo flujo contra Postgres real | Marcar `@pytest.mark.skipif` si no hay `DATABASE_URL` de Postgres en el entorno de test (mismo criterio que otros tests que dependen de Docker) |
| Regresión | Suite completa | `pytest tests/` sin cambios en los 12 archivos existentes |

## Migration / Rollout

Ninguna migración de datos requerida (todo nullable/default). Rollout es
el deploy normal del repo — la reconciliación corre en la primera conexión
de cada proceso, sin downtime ni script separado.

## Open Questions

- [ ] `primera_vez_visto`/`ultima_vez_visto`: ¿se backfilleaan con
      `created_at` para findings históricos, o quedan NULL? Se difiere a
      la implementación de Fase 2 (drift detection), que es quien primero
      lee estos campos — este change solo agrega la columna.
- [ ] Índices adicionales sobre `findings.estado`/`sla_due_at` (para
      queries de dashboard de Fase 2) — no se agregan en este change para
      no especular sobre patrones de consulta aún no escritos.
