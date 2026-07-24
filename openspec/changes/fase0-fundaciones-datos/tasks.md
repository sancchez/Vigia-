# Tasks: Fase 0 — Fundaciones de datos

## Phase 1: Schema (`db/schema.sql`)

- [x] 1.1 Agregar a `CREATE TABLE findings`: `estado TEXT NOT NULL DEFAULT
      'descubierto' CHECK(estado IN ('descubierto','triage','asignado',
      'en_progreso','mitigado','verificado','cerrado','aceptado_riesgo'))`,
      `owner_user_id TEXT`, `sla_due_at TIMESTAMP`, `estado_verificacion
      TEXT`, `primera_vez_visto TIMESTAMP`, `ultima_vez_visto TIMESTAMP`.
- [x] 1.2 Agregar a `CREATE TABLE assets`: `autorizacion_activa INTEGER NOT
      NULL DEFAULT 0`, `autorizacion_doc_url TEXT`, `autorizado_por TEXT`,
      `autorizado_en TIMESTAMP`.
- [x] 1.3 Crear `CREATE TABLE IF NOT EXISTS watched_brands` (id, tenant_id
      FK, marca/keyword TEXT, is_active INTEGER DEFAULT 1, created_at) +
      `idx_watched_brands_tenant_id`.
- [x] 1.4 Crear `CREATE TABLE IF NOT EXISTS notifications` (id, tenant_id
      FK, canal TEXT, destino TEXT, evento TEXT, enviado_en TIMESTAMP,
      created_at) + `idx_notifications_tenant_id`.
- [x] 1.5 Crear `CREATE TABLE IF NOT EXISTS notification_rules` (id,
      tenant_id FK, evento TEXT, canal TEXT, destino TEXT, is_active
      INTEGER DEFAULT 1) + `idx_notification_rules_tenant_id`.
- [x] 1.6 Crear `CREATE TABLE IF NOT EXISTS dmarc_reports` (id, tenant_id
      FK, dominio TEXT, rango_inicio TIMESTAMP, rango_fin TIMESTAMP,
      raw_json TEXT DEFAULT '{}', created_at) + `idx_dmarc_reports_tenant_id`.

## Phase 2: Reconciliación (`db/connection.py`)

- [x] 2.1 Definir `_FINDINGS_LIFECYCLE_COLUMNS` (6 tuplas, mismo tipo/CHECK
      que 1.1) y `_ASSETS_AUTORIZACION_COLUMNS` (4 tuplas, mismo tipo que
      1.2), junto a `_ASSET_VERIFICATION_COLUMNS` existente.
- [x] 2.2 Escribir `_ensure_sqlite_findings_columns(conn)` (mismo patrón
      `PRAGMA table_info(findings)` que `_ensure_sqlite_asset_verification_
      columns`).
- [x] 2.3 Escribir `_ensure_sqlite_assets_autorizacion_columns(conn)`
      (mismo patrón sobre `assets`).
- [x] 2.4 Llamar ambas funciones nuevas en `get_conn()`, justo después de
      `_ensure_sqlite_asset_verification_columns(conn)` y antes de
      `conn.commit()`.
- [x] 2.5 En `_get_pg_conn()`, agregar los `ALTER TABLE findings ADD COLUMN
      IF NOT EXISTS ...` (6) y `ALTER TABLE assets ADD COLUMN IF NOT
      EXISTS ...` (4) en el mismo bloque que ya hace el ALTER de
      verificación, antes de `conn.commit()`.

## Phase 3: Testing (`tests/test_schema_migration.py`)

- [x] 3.1 Fixture: crear un SQLite `:memory:`/archivo temporal con
      `findings`/`assets` en su forma PRE-Fase-0 (sin columnas nuevas,
      hardcoded en el test) para simular una DB de sesión anterior.
- [x] 3.2 Test: correr la reconciliación sobre esa DB vieja → `PRAGMA
      table_info` confirma las 10 columnas nuevas presentes con su
      default correcto (spec: "SQLite existente de sesión anterior").
- [x] 3.3 Test: `INSERT INTO findings` sin `estado` → fila queda con
      `estado='descubierto'` (spec: "estado por default").
- [x] 3.4 Test: `INSERT INTO findings (..., estado) VALUES (..., 'bogus')`
      → `sqlite3.IntegrityError` (CHECK enforced tras ALTER).
- [x] 3.5 Test: llamar `get_conn()` dos veces sobre el mismo archivo → no
      lanza excepción (idempotencia).
- [x] 3.6 Test: `get_conn()` sobre DB nueva → las 4 tablas nuevas existen
      con sus columnas (spec: "primera corrida crea las 4 tablas").
- [x] 3.7 Test opcional Postgres: mismo set de asserts contra
      `DATABASE_URL` de Postgres, marcado `@pytest.mark.skipif` si la
      variable de entorno de test no está seteada.
- [x] 3.8 Test: INSERT existente de `api/main.py` (sin columnas nuevas)
      reproducido literal en el test → no falla, valores quedan en
      default/NULL (no-regresión).

## Phase 4: Verificación final

- [x] 4.1 Correr `pytest tests/` completo (12 archivos existentes + el
      nuevo) → todo en verde. (125 passed, 1 skipped — el skip es el test
      opcional de Postgres real, 3.7, sin `VIGIA_PG_TEST_URL` seteado.)
- [ ] 4.2 Confirmar manualmente contra Postgres real (Docker, si
      disponible) siguiendo el mismo procedimiento de
      `docs/produccion-readiness.md` sección 2. **No ejecutado en esta
      sesión**: no había una instancia Postgres/Docker disponible en el
      entorno de este agente ni `VIGIA_PG_TEST_URL` seteada. El código
      Postgres (`_get_pg_conn`) sigue el patrón ya validado en
      `eval/live_run_report.md` para `_ASSET_VERIFICATION_COLUMNS`, y el
      test 3.7 queda listo para correr apenas se disponga de esa instancia
      (`@pytest.mark.skipif` sobre `VIGIA_PG_TEST_URL`). Reportado como
      desviación, no ocultado.
- [x] 4.3 Checklist de criterios de aceptación del proposal: migración
      idempotente SQLite ✓, Postgres código listo pero no verificado en
      vivo (ver 4.2) ⚠, suite verde ✓, `vigia_core_private/` sin tocar ✓
      (`git diff --stat` no lista nada bajo esa carpeta).
