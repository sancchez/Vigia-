-- ============================================================
-- Vigia — Schema multi-tenant
-- ============================================================
-- Dialecto portable a propósito (sin JSONB, sin gen_random_uuid(),
-- sin BIGSERIAL): corre hoy sobre SQLite (DATABASE_URL=sqlite:///...)
-- y corre sin cambios el día que DATABASE_URL apunte a Postgres/Supabase.
-- Los UUID se generan en Python (uuid.uuid4()), no en la base de datos.
--
-- Patrón adaptado de metads-100adsproto/backend/schema.sql (el único
-- patrón multi-tenant realmente probado en el ecosistema): tenant_id
-- como FK explícita en cada tabla, aislamiento resuelto en la capa de
-- queries (WHERE tenant_id = ...), no con row-level security todavía.

CREATE TABLE IF NOT EXISTS tenants (
    id              TEXT PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    plan            TEXT NOT NULL DEFAULT 'trial'
                        CHECK(plan IN ('trial','costero','flota','armada')),
    wompi_customer_id TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           TEXT NOT NULL UNIQUE,
    hashed_password TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'owner'
                        CHECK(role IN ('owner','admin','member')),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, email)
);
CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users(tenant_id);

-- Dominios/marcas/IPs que un tenant registra para que Vigia los vigile.
--
-- Columnas de verificación de propiedad (`verificado`/`verification_token`/
-- `verification_method`/`verified_at`) -- patrón Google Search Console /
-- Detectify: token único por asset + prueba de control vía DNS TXT o
-- archivo bien-conocido, ver `tools/asset_verification.py` para la lógica y
-- el razonamiento completo, y `HANDOFF.md`/`docs/produccion-readiness.md`
-- para el gap real que esto cierra: antes de esto, `POST /assets` dejaba
-- registrar CUALQUIER dominio (ej. 'microsoft.com') como propio sin ninguna
-- prueba de control, y ese registro por sí solo bastaba para autorizar
-- `POST /scan`/`POST /scan/activo` contra ese target.
--
-- Esta es la primera vez que este proyecto necesita agregar columnas a una
-- tabla que ya existía en sesiones anteriores (a diferencia de
-- `invitations`/`subscriptions`, que se sumaron como tablas nuevas
-- completas) -- así que este CREATE TABLE ya trae las columnas nuevas para
-- cualquier base de datos creada desde cero a partir de ahora, pero eso NO
-- alcanza para una `ciberseguridad.db`/Postgres ya existente de una sesión
-- anterior (CREATE TABLE IF NOT EXISTS no toca una tabla que ya existe).
-- Verificado empíricamente: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` es
-- sintaxis válida en Postgres (>= 9.6) pero NO en SQLite -- el parser de
-- SQLite (probado contra la versión real empaquetada con este Python,
-- 3.50.4) rechaza `IF NOT EXISTS` después de `ADD COLUMN` con un error de
-- sintaxis, no de semántica, así que no se puede resolver con una sola
-- sentencia portable aquí como si fuera un `CREATE TABLE`. La reconciliación
-- de una base de datos existente con esta forma nueva vive por eso en
-- `db/connection.py` (`_ensure_asset_verification_columns()`), que sí
-- distingue de verdad entre los dos motores -- mismo espíritu que
-- `_PgConnection`/`_register_timestamp_str_loader`, ya en ese archivo por
-- la misma razón (algo que de verdad no es portable entre los dos motores).
CREATE TABLE IF NOT EXISTS assets (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tipo                    TEXT NOT NULL CHECK(tipo IN ('dominio','app','ip')),
    valor                   TEXT NOT NULL,
    notas                   TEXT DEFAULT '',
    is_active               INTEGER NOT NULL DEFAULT 1,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verificado              INTEGER NOT NULL DEFAULT 0,
    verification_token      TEXT,
    verification_method     TEXT CHECK(verification_method IN ('dns_txt','http_file') OR verification_method IS NULL),
    verified_at             TIMESTAMP,
    autorizacion_activa     INTEGER NOT NULL DEFAULT 0,
    autorizacion_doc_url    TEXT,
    autorizado_por          TEXT,
    autorizado_en           TIMESTAMP,
    -- Programación de escaneo por activo (Fase 6a,
    -- openspec/changes/fase6-endurecimiento-produccion) -- columnas 1:1 con
    -- el activo en vez de una tabla `scan_schedules` separada (ver
    -- design.md, Decisión 6a.4: evita un JOIN por ciclo del scheduler para
    -- un dato de cardinalidad 1). `scan_frequency_hours IS NULL` significa
    -- "usa el intervalo global de VIGIA_SCAN_INTERVAL_HOURS" -- no todo
    -- activo necesita una frecuencia propia. Mismo caso que las columnas de
    -- verificación/autorización de arriba: este CREATE TABLE ya las trae
    -- para una base nueva, pero una `ciberseguridad.db`/Postgres existente
    -- de una sesión anterior necesita la reconciliación de
    -- `db/connection.py` (`_ensure_sqlite_assets_scheduling_columns()`).
    scan_frequency_hours    INTEGER,
    scan_paused             INTEGER NOT NULL DEFAULT 0,
    UNIQUE(tenant_id, tipo, valor)
);
CREATE INDEX IF NOT EXISTS idx_assets_tenant_id ON assets(tenant_id);

-- Una corrida del pipeline (orchestrator/graph.py) contra un asset.
CREATE TABLE IF NOT EXISTS scans (
    id                      TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    asset_id                TEXT REFERENCES assets(id) ON DELETE SET NULL,
    target                  TEXT NOT NULL,
    autorizacion_firmada    INTEGER NOT NULL DEFAULT 0,
    estado                  TEXT NOT NULL DEFAULT 'completado'
                                CHECK(estado IN ('pendiente','corriendo','completado','error')),
    reporte_final           TEXT,
    trace_log_json          TEXT NOT NULL DEFAULT '[]',
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at            TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scans_tenant_id ON scans(tenant_id);

-- Hallazgos verificados/priorizados de un scan (proyección de verified_findings
-- + prioritized_findings del PipelineState). Denormaliza tenant_id a propósito
-- para no depender de un JOIN para aislar por tenant.
-- Columnas de ciclo de vida (`estado`/`owner_user_id`/`sla_due_at`/
-- `estado_verificacion`/`primera_vez_visto`/`ultima_vez_visto`) agregadas en
-- Fase 0 (`openspec/changes/fase0-fundaciones-datos`) -- mismo caso que
-- `assets`/verificación de propiedad más arriba: este CREATE TABLE ya trae
-- las columnas nuevas para cualquier base de datos creada desde cero a
-- partir de ahora, pero una `ciberseguridad.db`/Postgres ya existente de una
-- sesión anterior necesita la reconciliación de `db/connection.py`
-- (`_ensure_sqlite_findings_columns()`/`_FINDINGS_LIFECYCLE_COLUMNS`).
CREATE TABLE IF NOT EXISTS findings (
    id                      TEXT PRIMARY KEY,
    scan_id                 TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    tenant_id               TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tipo                    TEXT NOT NULL,
    severidad               TEXT NOT NULL DEFAULT 'info'
                                CHECK(severidad IN ('critical','high','medium','low','info')),
    endpoint                TEXT DEFAULT '',
    confirmado              INTEGER NOT NULL DEFAULT 0,
    raw_json                TEXT NOT NULL DEFAULT '{}',
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    estado                  TEXT NOT NULL DEFAULT 'descubierto'
                                CHECK(estado IN ('descubierto','triage','asignado','en_progreso','mitigado','verificado','cerrado','aceptado_riesgo')),
    owner_user_id           TEXT,
    sla_due_at              TIMESTAMP,
    estado_verificacion     TEXT,
    primera_vez_visto       TIMESTAMP,
    ultima_vez_visto        TIMESTAMP,
    -- Segundo eje de clasificación, ORTOGONAL a `severidad` (Fase 7,
    -- `tools/clasificacion.py`): `severidad` dice qué tan grave sería si se
    -- explota, `clase` dice QUÉ TIPO DE COSA ES. Un "CSP Header Not Set"
    -- puede ser `medium` de severidad y aun así ser higiene, no una puerta
    -- abierta -- sin este eje, un SQLi real y una cabecera faltante se veían
    -- igual en el panel.
    --
    -- NULLABLE y SIN DEFAULT a propósito: toda fila nueva la escribe
    -- `_registrar_findings_con_ciclo_de_vida`, pero las filas históricas
    -- (guardadas antes de esta migración) quedan en NULL y se derivan EN
    -- LECTURA con `tools/clasificacion.py::clasificar_fila_finding()`, que
    -- aplica exactamente el mismo criterio determinista sobre `tipo`/
    -- `raw_json`/`confirmado`. Un DEFAULT habría congelado una etiqueta
    -- incorrecta ('informativo') sobre hallazgos reales viejos; un backfill
    -- destructivo habría hecho lo mismo sin posibilidad de recalcular cuando
    -- se afinen los patrones. Sin CHECK por la misma razón: NULL es un
    -- estado legítimo "todavía no clasificado en la fila".
    clase                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_tenant_id ON findings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_findings_scan_id ON findings(scan_id);

-- Historial de auditoría de cada cambio de `estado`/`owner_user_id` de un
-- finding (openspec/changes/fase2a-ciclo-vida-hallazgo) -- una fila por
-- cambio, no un campo "última nota" en `findings`: se necesita el
-- historial completo (quién, cuándo, qué cambió) para que
-- `fase2c-verificacion-cierre-y-kpis` calcule MTTR real a partir de
-- timestamps reales entre transiciones, no aproximados. `changed_by NULL`
-- significa que el cambio lo hizo el sistema (detección de reincidencia al
-- insertar findings de un scan nuevo), no un usuario vía `PATCH /findings/{id}`.
CREATE TABLE IF NOT EXISTS finding_transiciones (
    id              TEXT PRIMARY KEY,
    finding_id      TEXT NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    estado_anterior TEXT,
    estado_nuevo    TEXT,
    owner_anterior  TEXT,
    owner_nuevo     TEXT,
    nota            TEXT NOT NULL DEFAULT '',
    changed_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_finding_transiciones_finding_id ON finding_transiciones(finding_id);

-- Config de SLA por tenant (opcional, 1:1 con tenants) -- si un tenant no
-- tiene fila aquí, `_calcular_sla_due_at` usa el default embebido
-- (`DEFAULT_SLA_DIAS` en api/main.py). `dias_low` puede quedar NULL a
-- propósito (best-effort, sin plazo) -- ver design.md.
CREATE TABLE IF NOT EXISTS tenant_sla_config (
    tenant_id     TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    dias_critical INTEGER NOT NULL DEFAULT 7,
    dias_high     INTEGER NOT NULL DEFAULT 30,
    dias_medium   INTEGER NOT NULL DEFAULT 90,
    dias_low      INTEGER
);

-- Invitaciones a un tenant existente — item transversal de HANDOFF.md
-- ("invitar más usuarios al mismo tenant"). `users.email` es UNIQUE global
-- (una cuenta = un tenant), así que una invitación aceptada NO crea un
-- tenant nuevo: agrega al invitado como fila `users` con `tenant_id` de la
-- invitación (ver `api/main.py::register()`).
CREATE TABLE IF NOT EXISTS invitations (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    token           TEXT NOT NULL UNIQUE,
    role            TEXT NOT NULL DEFAULT 'member'
                        CHECK(role IN ('admin','member')),
    invited_by      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    estado          TEXT NOT NULL DEFAULT 'pendiente'
                        CHECK(estado IN ('pendiente','aceptada','revocada')),
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at      TIMESTAMP,
    accepted_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_invitations_tenant_id ON invitations(tenant_id);
CREATE INDEX IF NOT EXISTS idx_invitations_token ON invitations(token);

-- Estado de suscripción/cobro del tenant (Wompi). 1:1 con tenants.
CREATE TABLE IF NOT EXISTS subscriptions (
    tenant_id               TEXT PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    plan                    TEXT NOT NULL DEFAULT 'trial',
    wompi_subscription_id   TEXT,
    estado                  TEXT NOT NULL DEFAULT 'trial'
                                CHECK(estado IN ('trial','activa','pendiente','cancelada')),
    renovacion_en           TIMESTAMP,
    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Marcas/keywords que un tenant quiere vigilar (fuera de scope de este
-- change la lógica de CertStream que las consume -- Fase 3b de
-- docs/plan-entregable.md -- acá solo el schema).
-- `dominio_asociado` ancla la marca a un dominio-activo YA VERIFICADO del
-- tenant (mismo patrón Google Search Console de `assets.verification_token`,
-- ver tools/asset_verification.py). Sin ese ancla, cualquier tenant podía
-- registrar "bancolombia" y alimentar el monitoreo CertStream con una marca
-- que no le pertenece -- exactamente el agujero que ya se había cerrado para
-- dominios y que quedaba abierto para marcas.
--
-- NULLABLE a propósito: filas creadas ANTES de esta migración no tienen
-- dominio y no se les puede inventar uno. Se conservan (nada se borra, el
-- arranque no rompe) pero cuentan como NO VERIFICADAS: `GET /watched-brands`
-- las devuelve con `verificada=false` y el listener CertStream
-- (api/certstream_listener.py) las ignora. El tenant las re-registra con un
-- `dominio_asociado` verificado para reactivarlas.
CREATE TABLE IF NOT EXISTS watched_brands (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    marca             TEXT NOT NULL,
    dominio_asociado  TEXT,
    is_active         INTEGER NOT NULL DEFAULT 1,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_watched_brands_tenant_id ON watched_brands(tenant_id);

-- Notificaciones ya enviadas a un tenant (fuera de scope de este change el
-- envío real -- Fase 5 -- acá solo el schema para registrar lo enviado).
CREATE TABLE IF NOT EXISTS notifications (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    canal           TEXT NOT NULL,
    destino         TEXT NOT NULL,
    evento          TEXT NOT NULL,
    enviado_en      TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notifications_tenant_id ON notifications(tenant_id);

-- Reglas de notificación configuradas por un tenant (qué evento dispara qué
-- canal/destino). Fuera de scope de este change la evaluación de reglas.
CREATE TABLE IF NOT EXISTS notification_rules (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    evento          TEXT NOT NULL,
    canal           TEXT NOT NULL,
    destino         TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_notification_rules_tenant_id ON notification_rules(tenant_id);

-- Reportes DMARC agregados recibidos para un dominio de un tenant. `dominio`
-- es directo (no FK a `assets`) porque la ingesta (Fase 3e, fuera de scope
-- de este change) llega por email/API externa antes de que el tenant
-- necesariamente haya registrado ese dominio como asset -- ver design.md.
CREATE TABLE IF NOT EXISTS dmarc_reports (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    dominio         TEXT NOT NULL,
    rango_inicio    TIMESTAMP,
    rango_fin       TIMESTAMP,
    raw_json        TEXT NOT NULL DEFAULT '{}',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dmarc_reports_tenant_id ON dmarc_reports(tenant_id);

-- Registro de uso/costo de LLM por llamada (Fase 6a,
-- openspec/changes/fase6-endurecimiento-produccion) -- ver `agents/_llm.py`
-- (`set_llm_context`, `_registrar_uso`). Tabla enteramente nueva, así que
-- `CREATE TABLE IF NOT EXISTS` alcanza en ambos motores (no necesita el
-- patrón `_ensure_sqlite_*_columns` de `db/connection.py`, ver el comentario
-- de esa función para el porqué). `costo_estimado IS NULL` significa "no
-- medible" (fallback CLI de `claude -p`, que no expone conteo de tokens) --
-- nunca un número inventado.
CREATE TABLE IF NOT EXISTS llm_usage (
    id                TEXT PRIMARY KEY,
    tenant_id         TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    scan_id           TEXT REFERENCES scans(id) ON DELETE SET NULL,
    provider          TEXT NOT NULL,          -- 'anthropic' | 'openai' | 'cli'
    model             TEXT NOT NULL,
    tokens_in         INTEGER,
    tokens_out        INTEGER,
    costo_estimado    REAL,                   -- USD; NULL si no medible (CLI)
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_tenant_id ON llm_usage(tenant_id);
CREATE INDEX IF NOT EXISTS idx_llm_usage_scan_id ON llm_usage(scan_id);
