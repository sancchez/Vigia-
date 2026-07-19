"""Fixtures compartidas para toda la suite de tests de Vigia.

Reglas de diseño (ver docs/produccion-readiness.md, sección 3, y
HANDOFF.md): cada test corre contra una base SQLite temporal propia --
NUNCA contra `ciberseguridad.db` (el archivo real, gitignored, que puede
tener datos de demo/verificación real) -- y NUNCA dispara una llamada real
a Claude (API o CLI), aunque esta máquina de desarrollo tenga tanto
`ANTHROPIC_API_KEY` como el binario `claude` disponibles. Sin este segundo
punto, correr la suite podría gastar uso real de la API o tardar minutos
por llamada a `claude -p`.

**Override para smoke-test contra Postgres real** (ver
`docs/produccion-readiness.md` sección 2 y `eval/live_run_report.md`): si
la variable de entorno `VIGIA_PG_TEST_URL` está seteada (una URL
`postgresql://...` de una instancia real, nunca `ciberseguridad.db` ni una
Postgres de producción), `test_db` apunta `DATABASE_URL` a esa instancia en
vez de crear un SQLite temporal, y trunca todas las tablas antes de cada
test para mantener el mismo aislamiento por-test que ya daba `tmp_path` con
SQLite (Postgres real no tiene el equivalente de "un archivo nuevo por
test" -- hay que limpiarlo explícitamente). Esto es un override de humo
para verificar el backend Postgres de `db/connection.py` de punta a punta
con la suite ya existente, no una migración permanente de toda la suite a
correr en ambos motores por default (decisión explícita, ver el reporte de
la corrida de migración) -- por default (`VIGIA_PG_TEST_URL` sin setear)
el comportamiento es exactamente el mismo de siempre, SQLite temporal.
"""

from __future__ import annotations

import os

import pytest

_PG_TABLES = (
    "tenants", "users", "assets", "scans", "findings",
    "invitations", "subscriptions",
)


def _reset_pg_tables(pg_url: str) -> None:
    """Aplica el schema (si hace falta) y trunca todas las tablas.

    Usa `db.connection.get_conn()` real (no una conexión psycopg aparte) para
    ejercitar exactamente el mismo código de producción que usará la app
    durante el test -- si `_get_pg_conn`/`_PgConnection` tuvieran un bug, este
    reset ya lo expondría antes de llegar a los tests de endpoints.
    """
    from db.connection import get_conn

    conn = get_conn()
    try:
        tables = ", ".join(_PG_TABLES)
        conn.execute(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _sin_llm_real_por_defecto(monkeypatch):
    """Autouse: ningún test dispara una llamada real a Claude por accidente.

    Fuerza `agents._llm.call_claude` a levantar `LLMNoDisponibleError` de
    forma inmediata y determinista (sin red, sin subprocess) -- exactamente
    el mismo camino que todos los nodos/agentes ya saben manejar con
    gracia (fallback determinista). Un test que sí quiera ejercitar
    `_call_via_cli` de verdad (ver test_llm_cli_encoding.py) sobreescribe
    esto explícitamente con su propio `monkeypatch.setattr`.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("agents._llm.shutil.which", lambda _name: None)


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    """Aísla cada test en su propio archivo SQLite temporal.

    `db/connection.py::get_conn()` lee `DATABASE_URL` del entorno en cada
    llamada (no la cachea a nivel de módulo), así que basta con setearla
    antes de que el test haga su primera request/consulta -- no hace falta
    recargar ningún módulo.
    """
    pg_url = os.environ.get("VIGIA_PG_TEST_URL")
    if pg_url:
        monkeypatch.setenv("DATABASE_URL", pg_url)
        monkeypatch.setenv("JWT_SECRET", "test-secret-solo-para-pytest")
        _reset_pg_tables(pg_url)
        yield pg_url
        return

    db_path = tmp_path / "test_vigia.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("JWT_SECRET", "test-secret-solo-para-pytest")
    yield db_path


@pytest.fixture()
def client(test_db):
    """`TestClient` in-process contra la app real de `api/main.py`.

    A propósito NO se usa `with TestClient(app) as c:` -- eso dispararía el
    `lifespan` real (arranca `api/scheduler.py` y
    `api/certstream_listener.py`, ninguno de los cuales hace falta para
    estos tests y que dejarían hilos de fondo corriendo durante la suite).
    Los endpoints que sí probamos (`/auth/*`, `/me`, `/findings`,
    `/reports/cumplimiento`) no dependen de nada que el lifespan configure.
    """
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)
