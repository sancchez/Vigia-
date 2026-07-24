# Spec: Fase 0 — Fundaciones de datos

Dominio nuevo (no hay spec previo en `openspec/specs/` para migraciones de
schema) — spec completo, no delta.

## Purpose

Definir el comportamiento requerido de la migración de schema que agrega
ciclo de vida a `findings`, autorización persistente a `assets`, y las
tablas nuevas `watched_brands`, `notifications`, `notification_rules`,
`dmarc_reports`. Cubre solo el schema/migración — no endpoints ni UI.

## Requirements

### Requirement: Migración idempotente de columnas en `findings`

El sistema MUST agregar a `findings` las columnas `estado` (TEXT, default
`'descubierto'`, CHECK enum `descubierto|triage|asignado|en_progreso|
mitigado|verificado|cerrado|aceptado_riesgo`), `owner_user_id` (TEXT,
nullable), `sla_due_at` (TIMESTAMP, nullable), `estado_verificacion` (TEXT,
nullable), `primera_vez_visto` (TIMESTAMP, nullable), `ultima_vez_visto`
(TIMESTAMP, nullable), sin fallar sobre una tabla `findings` ya existente.

#### Scenario: SQLite existente de sesión anterior
- GIVEN un archivo `ciberseguridad.db` con `findings` creada antes de este change
- WHEN se llama `get_conn()`
- THEN las 6 columnas nuevas existen en `findings` sin perder filas existentes

#### Scenario: SQLite nuevo
- GIVEN una base SQLite que no existe todavía
- WHEN se llama `get_conn()` por primera vez
- THEN `findings` se crea ya con las 6 columnas nuevas

#### Scenario: Postgres existente y nuevo
- GIVEN `DATABASE_URL` apunta a un Postgres con o sin `findings` previa
- WHEN se abre la primera conexión del proceso
- THEN las 6 columnas existen tras la conexión, vía `ADD COLUMN IF NOT EXISTS`

#### Scenario: Re-ejecución no falla (idempotencia)
- GIVEN `findings` ya tiene las columnas nuevas
- WHEN `get_conn()` se llama de nuevo (SQLite o Postgres)
- THEN no se lanza excepción y no se duplican columnas

### Requirement: Migración idempotente de columnas en `assets`

El sistema MUST agregar a `assets` `autorizacion_activa` (INTEGER, default
0), `autorizacion_doc_url` (TEXT, nullable), `autorizado_por` (TEXT,
nullable), `autorizado_en` (TIMESTAMP, nullable), con la misma garantía de
idempotencia en SQLite y Postgres, nuevos y existentes, que `findings`.

#### Scenario: Asset existente sin autorización persistida
- GIVEN un `assets` ya existente sin las columnas nuevas
- WHEN se llama `get_conn()`
- THEN `autorizacion_activa` queda en `0` (falsy) para las filas existentes

### Requirement: `estado` por default en findings nuevos

Un `INSERT INTO findings` que no mencione `estado` MUST resultar en
`estado = 'descubierto'`.

#### Scenario: Insert existente sin columna `estado`
- GIVEN el INSERT actual de `api/main.py` (no lista `estado`)
- WHEN se inserta un finding
- THEN la fila queda con `estado = 'descubierto'`

### Requirement: No-regresión de INSERTs existentes

Los INSERT existentes en `api/main.py` que no mencionan las columnas nuevas
de `findings`/`assets` MUST seguir ejecutándose sin modificación de código
ni error de constraint.

#### Scenario: Insert de asset sin autorización activa
- GIVEN el INSERT actual de `assets` (no lista columnas de autorización)
- WHEN se registra un asset nuevo
- THEN la fila se crea con `autorizacion_activa = 0` y demás columnas NULL

### Requirement: Tablas nuevas vía `CREATE TABLE IF NOT EXISTS`

El sistema MUST crear `watched_brands`, `notifications`,
`notification_rules`, `dmarc_reports` con `CREATE TABLE IF NOT EXISTS`,
sin necesitar reconciliación de columnas (son tablas nuevas, no
alteraciones a tablas existentes).

#### Scenario: Primera corrida crea las 4 tablas
- GIVEN una base de datos sin estas tablas
- WHEN se llama `get_conn()`
- THEN las 4 tablas existen con sus columnas definidas en `schema.sql`

### Requirement: Aislamiento multi-tenant en tablas nuevas

`watched_brands`, `notifications` y `notification_rules` MUST llevar
`tenant_id` como FK NOT NULL a `tenants(id)`, siguiendo el patrón de
aislamiento por `WHERE tenant_id = ...` ya usado en el resto del schema.

#### Scenario: Fila de otro tenant no es visible
- GIVEN dos tenants con `watched_brands` propias
- WHEN se consulta `WHERE tenant_id = ?` para el tenant A
- THEN solo aparecen filas de A, nunca de B

### Requirement: Suite de tests sin regresión

La suite `tests/` (12 archivos existentes) MUST seguir pasando después de
aplicar esta migración, sin modificar tests existentes.

#### Scenario: Ejecución de pytest tras migrar
- GIVEN el repo con la migración aplicada
- WHEN se corre `pytest tests/`
- THEN los 12 archivos de test pasan igual que antes del change
