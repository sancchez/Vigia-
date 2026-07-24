"""Migración de schema de Fase 6a -- columnas de programación de escaneo en
`assets` (`scan_frequency_hours`/`scan_paused`) y tabla nueva `llm_usage`.

Ver `openspec/changes/fase6-endurecimiento-produccion/` (proposal/spec/
design/tasks, bloque 6a). Mismo espíritu que `test_schema_migration.py`
(Fase 0): reconciliación sobre una DB "vieja" (pre-Fase-6a, hardcoded acá) +
DB nueva desde cero + idempotencia.
"""

from __future__ import annotations

import sqlite3

import pytest

from db.connection import get_conn

_ASSETS_SCHEDULING_NUEVAS = {"scan_frequency_hours", "scan_paused"}


def _crear_db_pre_fase6a(path) -> None:
    """Reproduce `assets` en su forma justo antes de Fase 6a (con todas las
    columnas de Fase 0/verificación de propiedad, pero sin programación)."""
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
                autorizacion_activa     INTEGER NOT NULL DEFAULT 0,
                autorizacion_doc_url    TEXT,
                autorizado_por          TEXT,
                autorizado_en           TIMESTAMP,
                UNIQUE(tenant_id, tipo, valor)
            );
            """
        )
        conn.execute(
            "INSERT INTO tenants (id, slug, name) VALUES ('t1', 'pyme-vieja', 'Pyme Vieja')"
        )
        conn.execute(
            "INSERT INTO assets (id, tenant_id, tipo, valor) VALUES ('a1', 't1', 'dominio', 'viejo.test')"
        )
        conn.commit()
    finally:
        conn.close()


def _columnas(conn: sqlite3.Connection, tabla: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}


def test_reconciliacion_agrega_columnas_de_programacion_sin_perder_filas(tmp_path, monkeypatch):
    db_path = tmp_path / "vieja.db"
    _crear_db_pre_fase6a(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    conn = get_conn()
    try:
        assert _ASSETS_SCHEDULING_NUEVAS <= _columnas(conn, "assets")
        fila = dict(conn.execute("SELECT * FROM assets WHERE id = 'a1'").fetchone())
        assert fila["scan_frequency_hours"] is None
        assert fila["scan_paused"] == 0
    finally:
        conn.close()


def test_get_conn_dos_veces_no_duplica_columnas(tmp_path, monkeypatch):
    db_path = tmp_path / "idempotente.db"
    _crear_db_pre_fase6a(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    conn1 = get_conn()
    conn1.close()
    conn2 = get_conn()
    try:
        columnas = [row[1] for row in conn2.execute("PRAGMA table_info(assets)").fetchall()]
        assert columnas.count("scan_frequency_hours") == 1
        assert columnas.count("scan_paused") == 1
    finally:
        conn2.close()


def test_db_nueva_crea_llm_usage_y_columnas_de_programacion(tmp_path, monkeypatch):
    db_path = tmp_path / "flamante.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_conn()
    try:
        tablas = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        assert "llm_usage" in tablas
        assert _ASSETS_SCHEDULING_NUEVAS <= _columnas(conn, "assets")

        indices = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
        }
        assert "idx_llm_usage_tenant_id" in indices
        assert "idx_llm_usage_scan_id" in indices
    finally:
        conn.close()


def test_insert_llm_usage_costo_null_para_cli(tmp_path, monkeypatch):
    """`costo_estimado` debe aceptar NULL -- el caso real del fallback CLI."""
    db_path = tmp_path / "usage.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_conn()
    try:
        conn.execute("INSERT INTO tenants (id, slug, name) VALUES ('t1', 'x', 'X')")
        conn.execute(
            """
            INSERT INTO llm_usage (id, tenant_id, scan_id, provider, model, tokens_in, tokens_out, costo_estimado)
            VALUES ('u1', 't1', NULL, 'cli', 'claude-sonnet-4-5-20250929', NULL, NULL, NULL)
            """
        )
        conn.commit()
        fila = dict(conn.execute("SELECT * FROM llm_usage WHERE id = 'u1'").fetchone())
        assert fila["costo_estimado"] is None
        assert fila["provider"] == "cli"
    finally:
        conn.close()
