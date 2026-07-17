"""Capa de conexión a base de datos — sección multi-tenant (Fase 2).

Hoy solo implementa el backend SQLite (`DATABASE_URL=sqlite:///...`, el
"primario" según `.env.example`). El schema (`db/schema.sql`) está escrito
en un dialecto portable a Postgres a propósito: cuando exista un proyecto
Supabase real, agregar un backend Postgres (psycopg) aquí es un cambio
localizado a este archivo — el resto del código solo llama a `get_conn()`
y `dict_from_row()`, nunca sabe qué motor hay detrás.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

DB_DIR = Path(__file__).resolve().parent
_SCHEMA_PATH = DB_DIR / "schema.sql"


def _sqlite_path_from_url(url: str) -> str:
    # "sqlite:///./ciberseguridad.db" -> "./ciberseguridad.db"
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(
            f"DATABASE_URL no reconocida: '{url}'. Solo se soporta 'sqlite:///' "
            "hasta que se agregue un backend Postgres/Supabase en db/connection.py."
        )
    return url[len(prefix):]


def get_conn() -> sqlite3.Connection:
    """Abre una conexión nueva con el schema ya aplicado (idempotente).

    Una conexión por request es intencional (SQLite + FastAPI en threadpool
    no comparte conexiones de forma segura entre threads); el costo de abrir
    es bajo comparado con el resto del pipeline.
    """
    url = os.environ.get("DATABASE_URL", "sqlite:///./ciberseguridad.db")
    path = _sqlite_path_from_url(url)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def dict_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
