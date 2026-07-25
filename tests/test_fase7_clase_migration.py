"""Migración de schema de Fase 7 -- columna `clase` en `findings`.

Mismo espíritu que `test_schema_migration.py` (Fase 0) y
`test_fase6a_migration.py`: reconciliación sobre una DB "vieja" (pre-Fase-7,
hardcoded acá) + DB nueva desde cero + idempotencia + preservación de las
filas históricas.

Lo importante de este caso particular: la columna es NULLABLE y SIN DEFAULT a
propósito (ver `db/schema.sql`). Un hallazgo guardado antes de esta migración
NO se reescribe con una etiqueta adivinada -- queda en NULL y se deriva en
lectura. Este test congela esa decisión.
"""

from __future__ import annotations

import sqlite3

from db.connection import get_conn

_ESQUEMA_PRE_FASE7 = """
    CREATE TABLE tenants (
        id              TEXT PRIMARY KEY,
        slug            TEXT UNIQUE NOT NULL,
        name            TEXT NOT NULL,
        plan            TEXT NOT NULL DEFAULT 'trial',
        wompi_customer_id TEXT,
        is_active       INTEGER NOT NULL DEFAULT 1,
        created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE scans (
        id                      TEXT PRIMARY KEY,
        tenant_id               TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        asset_id                TEXT,
        target                  TEXT NOT NULL,
        autorizacion_firmada    INTEGER NOT NULL DEFAULT 0,
        estado                  TEXT NOT NULL DEFAULT 'pendiente',
        reporte_final           TEXT,
        trace_log_json          TEXT NOT NULL DEFAULT '[]',
        created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at            TIMESTAMP
    );
    CREATE TABLE findings (
        id                      TEXT PRIMARY KEY,
        scan_id                 TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
        tenant_id               TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        tipo                    TEXT NOT NULL,
        severidad               TEXT NOT NULL DEFAULT 'info',
        endpoint                TEXT DEFAULT '',
        confirmado              INTEGER NOT NULL DEFAULT 0,
        raw_json                TEXT NOT NULL DEFAULT '{}',
        created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        estado                  TEXT NOT NULL DEFAULT 'descubierto',
        owner_user_id           TEXT,
        sla_due_at              TIMESTAMP,
        estado_verificacion     TEXT,
        primera_vez_visto       TIMESTAMP,
        ultima_vez_visto        TIMESTAMP
    );
"""


def _crear_db_pre_fase7(path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_ESQUEMA_PRE_FASE7)
        conn.execute("INSERT INTO tenants (id, slug, name) VALUES ('t1', 'pyme-vieja', 'Pyme Vieja')")
        conn.execute(
            """
            INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado)
            VALUES ('s1', 't1', 'viejo.test', 1, 'completado')
            """
        )
        conn.execute(
            """
            INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, endpoint)
            VALUES ('f1', 's1', 't1', 'SQL Injection', 'high', 'https://viejo.test/buscar')
            """
        )
        conn.commit()
    finally:
        conn.close()


def _columnas(conn: sqlite3.Connection, tabla: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({tabla})").fetchall()}


def test_reconciliacion_agrega_clase_sin_tocar_filas_existentes(tmp_path, monkeypatch):
    db_path = tmp_path / "vieja.db"
    _crear_db_pre_fase7(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    conn = get_conn()
    try:
        assert "clase" in _columnas(conn, "findings")
        fila = dict(conn.execute("SELECT * FROM findings WHERE id = 'f1'").fetchone())
        # La fila histórica sigue entera y su `clase` queda en NULL: no se
        # inventa una etiqueta sobre datos viejos, se deriva en lectura.
        assert fila["clase"] is None
        assert fila["tipo"] == "SQL Injection"
        assert fila["severidad"] == "high"
    finally:
        conn.close()


def test_get_conn_dos_veces_no_duplica_la_columna(tmp_path, monkeypatch):
    db_path = tmp_path / "idempotente.db"
    _crear_db_pre_fase7(db_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")

    conn1 = get_conn()
    conn1.close()
    conn2 = get_conn()
    try:
        columnas = [row[1] for row in conn2.execute("PRAGMA table_info(findings)").fetchall()]
        assert columnas.count("clase") == 1
    finally:
        conn2.close()


def test_db_nueva_ya_trae_clase_desde_el_create_table(tmp_path, monkeypatch):
    db_path = tmp_path / "flamante.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_conn()
    try:
        assert "clase" in _columnas(conn, "findings")
        # Sin CHECK: NULL es un estado legítimo ("todavía no clasificado en la
        # fila"), y el vocabulario de clases vive en `tools/clasificacion.py`,
        # no duplicado en el schema.
        conn.execute("INSERT INTO tenants (id, slug, name) VALUES ('t1', 'x', 'X')")
        conn.execute(
            """
            INSERT INTO scans (id, tenant_id, target, autorizacion_firmada, estado)
            VALUES ('s1', 't1', 'nuevo.test', 1, 'completado')
            """
        )
        conn.execute(
            """
            INSERT INTO findings (id, scan_id, tenant_id, tipo, severidad, clase)
            VALUES ('f1', 's1', 't1', 'SQL Injection', 'high', 'vulnerabilidad_explotable')
            """
        )
        conn.commit()
        fila = dict(conn.execute("SELECT * FROM findings WHERE id = 'f1'").fetchone())
        assert fila["clase"] == "vulnerabilidad_explotable"
    finally:
        conn.close()
