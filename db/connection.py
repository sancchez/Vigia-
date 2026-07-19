"""Capa de conexión a base de datos — sección multi-tenant (Fase 2).

Implementa dos backends detrás de `get_conn()`/`dict_from_row()`:
SQLite (`DATABASE_URL=sqlite:///...`, default, sigue siendo el "primario"
para quien no configure `DATABASE_URL`) y Postgres (`DATABASE_URL=postgresql://...`
o `postgres://...`, vía `psycopg` — psycopg3 moderno, no SQLAlchemy/ORM,
mismo estilo de SQL crudo que ya usa el resto del proyecto).

**Verificado de verdad contra Postgres 16 real (Docker), no solo asumido**
(ver `eval/live_run_report.md`, corrida de migración Postgres, y la sección
"2. Migración SQLite → Postgres" de `docs/produccion-readiness.md`):
`db/schema.sql` corrió SIN NINGÚN CAMBIO contra Postgres real — el dialecto
portable que el docstring de este módulo llevaba tiempo prometiendo resultó
ser cierto en la práctica, no solo en el diseño. Dos decisiones concretas
hicieron falta para que el resto del código (que siempre ha escrito SQL
crudo en estilo sqlite3: placeholders `?`, `conn.execute(...).fetchone()`,
`conn.commit()`, `conn.close()`) funcione sin cambios contra cualquiera de
los dos motores:

1. `_PgConnection` traduce placeholders `?` (estilo sqlite3) a `%s` (estilo
   psycopg) con un `str.replace` simple — no hace falta un parser SQL real
   porque se confirmó (grep sobre todo el repo) que ninguna query real del
   proyecto usa `?` o `%` literal dentro de una cadena SQL (sin `LIKE` con
   comodines propios, sin `lastrowid`, todos los IDs son UUID generados en
   Python). Si en el futuro una query necesita un `?` o `%` literal, esa
   traducción ingenua dejaría de ser segura y habría que revisarla.
2. `psycopg.rows.dict_row` como `row_factory` hace que `fetchone()`/
   `fetchall()` devuelvan `dict` en vez de una fila especial de psycopg —
   `dict_from_row()` ya hacía `dict(row)`, que funciona igual de bien sobre
   un `dict` que sobre un `sqlite3.Row` (ambos exponen `keys()`), así que
   `dict_from_row()` no necesitó ningún cambio.

**Lo que SÍ resultó genuinamente no portable (encontrado corriendo la suite
de tests real contra Postgres real, no adivinado):** las columnas `TIMESTAMP`
del schema. `sqlite3` (sin `detect_types=PARSE_DECLTYPES`, que este proyecto
nunca configuró) devuelve el valor crudo tal cual está guardado -- un `str`
como `"2026-07-19 15:36:19"` -- y varios endpoints de `api/main.py`
(`GET /scans`, `GET /findings`, etc.) construyen sus modelos Pydantic
(`created_at: str`) leyendo `row["created_at"]` directo, sin pasar por
`dict_from_row()`. `psycopg`, en cambio, parsea `TIMESTAMP` a un objeto
`datetime.datetime` real -- comportamiento más "correcto" en abstracto, pero
que revienta esos mismos modelos Pydantic (`ValidationError: Input should be
a valid string`) apenas se corrió contra Postgres real. Arreglarlo en cada
endpoint (o cambiar cada campo Pydantic a `datetime`) habría dejado de ser
un cambio localizado a este archivo -- en su lugar, `_get_pg_conn()` registra
un loader custom (`_TimestampStrLoader`) que hace que psycopg devuelva
`TIMESTAMP`/`TIMESTAMPTZ` como `str` igual que sqlite3, truncando a segundos
(Postgres guarda microsegundos por default en `CURRENT_TIMESTAMP`, sqlite
no) para que el formato de string sea idéntico entre motores, no solo el
tipo Python. Con este loader puesto, los 44 tests de `tests/` pasan
exactamente igual contra Postgres real que contra SQLite (ver
`eval/live_run_report.md`).

El schema se aplica una sola vez por proceso por cada `DATABASE_URL` de
Postgres distinta (cacheado en `_pg_schema_ready`, protegido por un lock),
a diferencia de SQLite donde `executescript()` corre en cada conexión — la
razón es que abrir una conexión nueva por request (patrón ya existente,
intencional por el comentario de `get_conn()` de abajo) sería un costo de
DDL redundante en cada request real contra un Postgres remoto, mientras que
contra un archivo SQLite local ese costo es despreciable. `CREATE TABLE/
INDEX IF NOT EXISTS` sigue siendo idempotente en ambos motores, así que
cachear no cambia el comportamiento, solo evita trabajo repetido.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = DB_DIR / "schema.sql"

_pg_schema_ready: set[str] = set()
_pg_schema_lock = threading.Lock()

# Columnas de verificación de propiedad de dominio agregadas a `assets` en
# esta sesión (ver comentario largo en db/schema.sql) -- se listan aquí
# también (no solo en el CREATE TABLE) porque una base de datos SQLite o
# Postgres que ya existía de una sesión anterior necesita una reconciliación
# explícita que el CREATE TABLE IF NOT EXISTS no le da. Nombre y tipo deben
# coincidir con las columnas del CREATE TABLE de `assets` en schema.sql.
_ASSET_VERIFICATION_COLUMNS: list[tuple[str, str]] = [
    ("verificado", "INTEGER NOT NULL DEFAULT 0"),
    ("verification_token", "TEXT"),
    ("verification_method", "TEXT"),
    ("verified_at", "TIMESTAMP"),
]


def _sqlite_path_from_url(url: str) -> str:
    # "sqlite:///./ciberseguridad.db" -> "./ciberseguridad.db"
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(
            f"DATABASE_URL no reconocida: '{url}'. Solo se soportan 'sqlite:///' "
            "y 'postgresql://'/'postgres://' (ver db/connection.py)."
        )
    return url[len(prefix):]


class _PgConnection:
    """Envuelve `psycopg.Connection` para que el resto del código pueda
    seguir llamando `conn.execute(sql_con_signos_de_interrogacion, params)`
    exactamente como con `sqlite3`, sin saber qué motor hay detrás — ver
    docstring del módulo para la justificación de la traducción `?` -> `%s`.
    """

    __slots__ = ("_conn",)

    def __init__(self, pg_conn: Any) -> None:
        self._conn = pg_conn

    def execute(self, query: str, params: tuple | list = ()):
        return self._conn.execute(query.replace("?", "%s"), params)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _register_timestamp_str_loader(conn: Any) -> None:
    """Hace que `TIMESTAMP`/`TIMESTAMPTZ` vuelvan como `str`, no `datetime`.

    Ver docstring del módulo ("Lo que SÍ resultó genuinamente no portable")
    -- sqlite3 siempre devolvió estas columnas como texto crudo y varios
    endpoints de `api/main.py` construyen sus modelos Pydantic (`created_at:
    str`) a partir de eso directamente. Truncar a segundos además compensa
    que Postgres guarda microsegundos en `CURRENT_TIMESTAMP` y sqlite no.
    """
    import psycopg.adapt

    class _TimestampStrLoader(psycopg.adapt.Loader):
        def load(self, data: bytes | bytearray | memoryview) -> str:
            text = bytes(data).decode()
            return text.split(".", 1)[0]

    conn.adapters.register_loader("timestamp", _TimestampStrLoader)
    conn.adapters.register_loader("timestamptz", _TimestampStrLoader)


def _get_pg_conn(url: str) -> _PgConnection:
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
    _register_timestamp_str_loader(conn)
    if url not in _pg_schema_ready:
        with _pg_schema_lock:
            if url not in _pg_schema_ready:
                # Sin params -> psycopg usa el protocolo "simple query", que sí
                # soporta múltiples sentencias separadas por ';' en una sola
                # llamada (verificado contra Postgres real) -- el equivalente
                # a `sqlite3.Connection.executescript()` para este motor.
                conn.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
                # Reconcilia una tabla `assets` que ya existía antes de la
                # migración de verificación de propiedad (ver comentario en
                # db/schema.sql) -- Postgres SÍ soporta `ADD COLUMN IF NOT
                # EXISTS` nativamente, así que aquí no hace falta el chequeo
                # manual que sí necesita SQLite (ver _ensure_sqlite_asset_
                # verification_columns más abajo).
                for column, coltype in _ASSET_VERIFICATION_COLUMNS:
                    conn.execute(
                        f"ALTER TABLE assets ADD COLUMN IF NOT EXISTS {column} {coltype}"
                    )
                conn.commit()
                _pg_schema_ready.add(url)
    return _PgConnection(conn)


def _ensure_sqlite_asset_verification_columns(conn: sqlite3.Connection) -> None:
    """Agrega a `assets` las columnas de verificación si la tabla es de antes de esta migración.

    SQLite no soporta `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (verificado
    empíricamente contra la versión real empaquetada con este Python -- ver
    comentario en db/schema.sql), así que el chequeo de "¿ya existe la
    columna?" hay que hacerlo a mano con `PRAGMA table_info`, una sola
    consulta barata, antes de decidir si hace falta el `ALTER TABLE`. Sin
    este paso, una `ciberseguridad.db` real de una sesión anterior (con
    `assets` ya creada sin estas columnas) rompería en el primer `INSERT
    INTO assets` que mencione `verification_token`, etc.
    """
    columnas_existentes = {
        row[1] for row in conn.execute("PRAGMA table_info(assets)").fetchall()
    }
    for column, coltype in _ASSET_VERIFICATION_COLUMNS:
        if column not in columnas_existentes:
            conn.execute(f"ALTER TABLE assets ADD COLUMN {column} {coltype}")


def get_conn() -> sqlite3.Connection | _PgConnection:
    """Abre una conexión nueva con el schema ya aplicado (idempotente).

    Una conexión por request es intencional (SQLite + FastAPI en threadpool
    no comparte conexiones de forma segura entre threads); el costo de abrir
    es bajo comparado con el resto del pipeline. El mismo patrón de "una
    conexión por request" se mantiene para Postgres -- es lo que motivó la
    migración en primer lugar (concurrencia real de escritura multi-tenant,
    ver `docs/produccion-readiness.md` sección 2), y con un pool de
    conexiones real (`psycopg_pool`) del lado del servidor de Postgres esto
    escala mejor que abrir/cerrar conexiones SQLite bajo carga concurrente.
    """
    url = os.environ.get("DATABASE_URL", "sqlite:///./ciberseguridad.db")
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return _get_pg_conn(url)
    path = _sqlite_path_from_url(url)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    _ensure_sqlite_asset_verification_columns(conn)
    conn.commit()
    return conn


def dict_from_row(row: sqlite3.Row | dict | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
