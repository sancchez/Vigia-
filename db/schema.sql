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
CREATE TABLE IF NOT EXISTS assets (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tipo            TEXT NOT NULL CHECK(tipo IN ('dominio','app','ip')),
    valor           TEXT NOT NULL,
    notas           TEXT DEFAULT '',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
CREATE TABLE IF NOT EXISTS findings (
    id              TEXT PRIMARY KEY,
    scan_id         TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tipo            TEXT NOT NULL,
    severidad       TEXT NOT NULL DEFAULT 'info'
                        CHECK(severidad IN ('critical','high','medium','low','info')),
    endpoint        TEXT DEFAULT '',
    confirmado      INTEGER NOT NULL DEFAULT 0,
    raw_json        TEXT NOT NULL DEFAULT '{}',
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_findings_tenant_id ON findings(tenant_id);
CREATE INDEX IF NOT EXISTS idx_findings_scan_id ON findings(scan_id);

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
