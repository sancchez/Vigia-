"""Migración de schema de Fase 0 -- ciclo de vida de `findings`,
autorización persistente de `assets`, y las 4 tablas nuevas (`watched_brands`,
`notifications`, `notification_rules`, `dmarc_reports`).

Ver `openspec/changes/fase0-fundaciones-datos/` (proposal/spec/design/tasks)
-- este test cubre únicamente el schema/migración (Fase 0), sin endpoints ni
lógica de negocio.

Estructura, mismo espíritu que `test_asset_verification.py`:

1. Reconciliación sobre una DB SQLite "vieja" (pre-Fase-0, hardcoded acá
   mismo -- no depende de `db/schema.sql` para simular el estado anterior,
   porque ese archivo ya tiene las columnas nuevas después de este change).
2. Default de `estado` e integridad del CHECK constraint.
3. Idempotencia de `get_conn()`.
4. DB nueva: las 4 tablas nuevas se crean desde cero.
5. Postgres opcional (skip si no hay `VIGIA_PG_TEST_URL`).
6. No-regresión: los INSERT literales de `api/main.py` (sin columnas nuevas)
   siguen funcionando.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from db.connection import get_conn

_FINDINGS_NUEVAS = {
    "estado", "owner_user_id", "sla_due_at", "estado_verificacion",
    "primera_vez_visto", "ultima_vez_visto",
}
_ASSETS_NUEVAS = {
    "autorizacion_activa", "autorizacion_doc_url", "autorizado_por", "autorizado_en",
}
_TABLAS_NUEVAS = ("watched_brands", "notifications", "notification_rules", "dmarc_reports")
_TABLAS_NUEVAS_FASE2A = ("finding_transiciones", "tenant_sla_config")


def _crear_db_pre_fase0(path) -> None:
    """Crea un archivo SQLite con `findings`/`assets` en su forma PRE-Fase-0.

    Hardcoded a propósito (no lee `db/schema.sql`, que ya trae las columnas
    nuevas después de este change) -- reproduce la forma exacta que tenían
    estas tablas antes de este change (incluyendo las columnas de
    verificación de propiedad, que son de una migración anterior y ya
    existían), para simular una `ciberseguridad.db` real de una sesión
    anterior.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE tenants (
                id              TEXT PRIMARY KEY,
                slug            TEXT UNIQUE NOT NULL,
                name            TEXT NOT NULL,
                plan            TEXT NOT NULL DEFAULT 'trial',
                wompi_customer_id TEXT,
                is_active       INTEGER NOT NULL DEFAULT 1,
                created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE assets (
                id                      TEXT PRIMARY KEY,
                tenant_id               TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                tipo                    TEXT NOT NULL CHECK(tipo IN ('dominio','app','ip')),
                valor                   TEXT NOT NULL,
                notas                   TEXT DEFAULT '',
                is_active               INTEGER NOT NULL DEFAULT 1,
                created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                verificado              INTEGER NOT NULL DEFAULT 0,
                verification_token      TEXT,
                verification_method     TEXT,
                verified_at             TIMESTAMP,
                UNIQUE(tenant_id, tipo, valor)
            );
            CREATE TABLE scans (
                id                      TEXT PRIMARY KEY,
                tenant_id               TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                asset_id                TEXT REFERENCES assets(id) ON DELETE SET NULL,
                target                  TEXT NOT NULL,
                autorizacion_firmada    INTEGER NOT NULL DEFAULT 0,
                estado                  TEXT NOT NULL DEFAULT 'completado',
                reporte_final           TEXT,
                trace_log_json          TEXT NOT NULL DEFAULT '[]',
                created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at            TIMESTAMP
            );
            CREATE TABLE findings (
                id              TEXT PRIMARY KEY,
                scan_id         TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
                tenant_id       TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                tipo            TEXT NOT NULL,
                severidad       TEXT NOT NULL DEFAULT 'info',
                endpoint        TEXT DEFAULT '',
                confirmado      INTEGER NOT NULL DEFAULT 0,
                raw_json        TEXT NOT NULL DEFAULT '{}',
                created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # Datos preexistentes -- deben sobrevivir intactos a la reconciliación.
        conn.execute(
            "INSERT INTO tenants (id, slug, name) VALUES ('t1', 'pyme-vieja', 'Pyme Vieja')"
        )
        conn.execute(
            "INSERT INTO assets (id, tenant_id, tipo, valor) VALUES ('a1', 't1', 'dominio', 'viejo.test')"
        )
        conn.execute(
            "INSERT INTO scans (id, tenant_id, asset_id, target) VALUES ('s1', 't1', 'a1', 'viejo.test')"
        )
        conn.execute(
            "INSERT INTO findings (id, scan_id, tenant_id, tipo) VALUES ('f1', 's1', 't1', 'xss')"
        )
        conn.commit()
    finally:
        conn.close()


def _columnas(conn: sqlite3.Connection, tabla: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}


# ---------------------------------------------------------------------------
# 1. Reconciliación sobre una DB SQLite "vieja" (sesión anterior)
# ---------------------------------------------------------------------------


def test_reconciliacion_agrega_columnas_nuevas_sin_perder_filas(tmp_path, monkeypatch):
    db_path = tmp_path / "vieja.db"
    _crear_db_pre_fase0(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    conn = get_conn()
    try:
        columnas_findings = _columnas(conn, "findings")
        columnas_assets = _columnas(conn, "assets")
        assert _FINDINGS_NUEVAS <= columnas_findings
        assert _ASSETS_NUEVAS <= columnas_assets

        # La fila preexistente sigue ahí, con los defaults correctos en las
        # columnas nuevas (no se perdió nada, no quedó NULL donde hay DEFAULT).
        fila = conn.execute("SELECT * FROM findings WHERE id = 'f1'").fetchone()
        assert fila is not None
        fila_dict = dict(fila)
        assert fila_dict["estado"] == "descubierto"
        assert fila_dict["owner_user_id"] is None
        assert fila_dict["sla_due_at"] is None

        asset_fila = dict(conn.execute("SELECT * FROM assets WHERE id = 'a1'").fetchone())
        assert asset_fila["autorizacion_activa"] == 0
        assert asset_fila["autorizacion_doc_url"] is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Default de `estado` + CHECK constraint
# ---------------------------------------------------------------------------


def test_estado_por_default_en_insert_sin_estado(tmp_path, monkeypatch):
    db_path = tmp_path / "nueva.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_conn()
    try:
        conn.execute("INSERT INTO tenants (id, slug, name) VALUES ('t1', 'x', 'X')")
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target) VALUES ('s1', 't1', 'x.test')"
        )
        conn.execute(
            "INSERT INTO findings (id, scan_id, tenant_id, tipo) VALUES ('f1', 's1', 't1', 'xss')"
        )
        conn.commit()
        fila = dict(conn.execute("SELECT * FROM findings WHERE id = 'f1'").fetchone())
        assert fila["estado"] == "descubierto"
    finally:
        conn.close()


def test_estado_check_constraint_enforced_tras_alter(tmp_path, monkeypatch):
    """El CHECK inline agregado vía ALTER TABLE (ver design.md, decisión sobre
    incluir el CHECK completo en la tupla de reconciliación) se enforce igual
    que en una tabla creada desde cero con CREATE TABLE."""
    db_path = tmp_path / "vieja.db"
    _crear_db_pre_fase0(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_conn()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO findings (id, scan_id, tenant_id, tipo, estado) "
                "VALUES ('f2', 's1', 't1', 'xss', 'bogus')"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Idempotencia
# ---------------------------------------------------------------------------


def test_get_conn_dos_veces_no_lanza_excepcion(tmp_path, monkeypatch):
    db_path = tmp_path / "idempotente.db"
    _crear_db_pre_fase0(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    conn1 = get_conn()
    conn1.close()
    # Segunda apertura sobre el mismo archivo -- no debe fallar ni duplicar columnas.
    conn2 = get_conn()
    try:
        columnas = [row[1] for row in conn2.execute("PRAGMA table_info(findings)").fetchall()]
        assert columnas.count("estado") == 1
    finally:
        conn2.close()


# ---------------------------------------------------------------------------
# 4. DB nueva: las 4 tablas nuevas + columnas nuevas desde cero
# ---------------------------------------------------------------------------


def test_db_nueva_crea_las_4_tablas_nuevas(tmp_path, monkeypatch):
    db_path = tmp_path / "flamante.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_conn()
    try:
        tablas_existentes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for tabla in _TABLAS_NUEVAS:
            assert tabla in tablas_existentes

        assert _FINDINGS_NUEVAS <= _columnas(conn, "findings")
        assert _ASSETS_NUEVAS <= _columnas(conn, "assets")

        indices = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert "idx_watched_brands_tenant_id" in indices
        assert "idx_notifications_tenant_id" in indices
        assert "idx_notification_rules_tenant_id" in indices
        assert "idx_dmarc_reports_tenant_id" in indices
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4b. Fase 2a -- `finding_transiciones`/`tenant_sla_config` sobre DB nueva
# ---------------------------------------------------------------------------


def test_db_nueva_crea_las_tablas_de_fase2a(tmp_path, monkeypatch):
    db_path = tmp_path / "fase2a.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_conn()
    try:
        tablas_existentes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for tabla in _TABLAS_NUEVAS_FASE2A:
            assert tabla in tablas_existentes

        indices = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert "idx_finding_transiciones_finding_id" in indices
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Postgres opcional -- mismo criterio que tests/conftest.py::test_db
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("VIGIA_PG_TEST_URL"),
    reason="requiere VIGIA_PG_TEST_URL (Postgres real) -- ver tests/conftest.py",
)
def test_reconciliacion_contra_postgres_real(monkeypatch):
    pg_url = os.environ["VIGIA_PG_TEST_URL"]
    monkeypatch.setenv("DATABASE_URL", pg_url)
    conn = get_conn()
    try:
        conn.execute(
            "TRUNCATE TABLE tenants, users, assets, scans, findings, "
            "invitations, subscriptions, watched_brands, notifications, "
            "notification_rules, dmarc_reports RESTART IDENTITY CASCADE"
        )
        conn.commit()

        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'findings'"
        )
        columnas_findings = {row["column_name"] for row in cur.fetchall()}
        cur = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'assets'"
        )
        columnas_assets = {row["column_name"] for row in cur.fetchall()}
        assert _FINDINGS_NUEVAS <= columnas_findings
        assert _ASSETS_NUEVAS <= columnas_assets

        for tabla in _TABLAS_NUEVAS:
            cur = conn.execute(
                "SELECT to_regclass(%s) AS existe".replace("%s", "?"), (tabla,)
            )
            assert cur.fetchone()["existe"] is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. No-regresión: INSERT literales de api/main.py sin columnas nuevas
# ---------------------------------------------------------------------------


def test_insert_asset_literal_de_api_main_no_falla(tmp_path, monkeypatch):
    db_path = tmp_path / "regresion.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_conn()
    try:
        conn.execute("INSERT INTO tenants (id, slug, name) VALUES ('t1', 'x', 'X')")
        conn.execute(
            """
            INSERT INTO assets (id, tenant_id, tipo, valor, notas, verification_token)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("a1", "t1", "dominio", "regresion.test", "", "tok-123"),
        )
        conn.commit()
        fila = dict(conn.execute("SELECT * FROM assets WHERE id = 'a1'").fetchone())
        assert fila["autorizacion_activa"] == 0
        assert fila["autorizacion_doc_url"] is None
        assert fila["autorizado_por"] is None
        assert fila["autorizado_en"] is None
    finally:
        conn.close()


def test_insert_findings_literal_de_api_main_no_falla(tmp_path, monkeypatch):
    db_path = tmp_path / "regresion2.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_conn()
    try:
        conn.execute("INSERT INTO tenants (id, slug, name) VALUES ('t1', 'x', 'X')")
        conn.execute(
            "INSERT INTO scans (id, tenant_id, target) VALUES ('s1', 't1', 'x.test')"
        )
        conn.execute(
            """
            INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("f1", "s1", "t1", "xss", "high", "/login", 0, "{}"),
        )
        conn.commit()
        fila = dict(conn.execute("SELECT * FROM findings WHERE id = 'f1'").fetchone())
        assert fila["estado"] == "descubierto"
        assert fila["owner_user_id"] is None
        assert fila["sla_due_at"] is None
        assert fila["estado_verificacion"] is None
        assert fila["primera_vez_visto"] is None
        assert fila["ultima_vez_visto"] is None
    finally:
        conn.close()
