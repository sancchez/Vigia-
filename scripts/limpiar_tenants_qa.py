"""Borra de la base de desarrollo los tenants que quedaron de pruebas de QA.

Por qué existe
--------------
La base de desarrollo (`ciberseguridad.db`) acumuló tenants creados por
corridas de prueba manuales y de E2E ("E2E Checkpoint Test 1/2/3", "Pyme Test
QA", "Sim", …). No molestan a nadie mientras la base sea local, pero
convierten cualquier inspección de la base en un ejercicio de adivinar qué es
dato real y qué es basura de prueba — y si esa base se usa como punto de
partida para un despliegue, esos tenants viajan con ella.

Este script NO es parte del arranque de la aplicación ni de ninguna
migración: se corre a mano, y **imprime todo lo que va a borrar antes de
borrar nada**.

Qué borra (cascada explícita)
-----------------------------
SQLite no aplica `ON DELETE CASCADE` salvo que `PRAGMA foreign_keys` esté
activo, y varias tablas de este esquema ni declaran la FK. Por eso el borrado
enumera las tablas una por una, en orden de dependencia (hijos antes que
padres). `findings` y `finding_transiciones` se borran tanto por `tenant_id`
como por su vínculo con `scans`, para no dejar filas huérfanas si alguna fila
antigua tuviera el `tenant_id` en blanco.

Uso:
    py scripts/limpiar_tenants_qa.py                  # solo muestra qué se borraría
    py scripts/limpiar_tenants_qa.py --aplicar        # borra los tenants de QA
    py scripts/limpiar_tenants_qa.py --aplicar --incluir-demo
                                                      # además borra el tenant sembrado
                                                      # por scripts/seed_demo.py
    py scripts/limpiar_tenants_qa.py --aplicar --slug otro-tenant
                                                      # borra un tenant puntual extra
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from db.connection import dict_from_row, get_conn  # noqa: E402

# Tenants creados por pruebas manuales o E2E, identificados uno por uno en la
# base actual. Lista EXPLÍCITA a propósito: un filtro heurístico por nombre
# (`LIKE '%test%'`) borraría el día de mañana un cliente real que se llame
# "Testing S.A.S.".
SLUGS_QA = (
    "certstream-test-co",
    "pyme-test-qa",
    "miempresatest-s-a-s",
    "e2e-checkpoint-test",
    "e2e-checkpoint-test-2",
    "e2e-checkpoint-test-3",
    "demo-onboarding-real",
    "sim-6c7dfff7",
)

# Tenant sembrado por `scripts/seed_demo.py` / `seed_demo_antisuplantacion.py`.
# Separado de los anteriores porque es RECREABLE de un comando y alguien puede
# querer conservarlo mientras prepara una demostración.
SLUG_DEMO = "vigia-demo"

# Tablas con `tenant_id` propio, en orden de borrado (hijos primero).
TABLAS_POR_TENANT = (
    "finding_transiciones",
    "findings",
    "notifications",
    "notification_rules",
    "dmarc_reports",
    "llm_usage",
    "invitations",
    "watched_brands",
    "tenant_sla_config",
    "subscriptions",
    "scans",
    "assets",
    "users",
)


def _inventario(conn, slugs: tuple[str, ...]) -> list[dict]:
    """SELECT previo: qué tenants existen de la lista y cuánto cuelga de cada uno."""
    marcadores = ",".join("?" for _ in slugs)
    rows = conn.execute(
        f"""
        SELECT t.id, t.slug, t.name, t.created_at,
               (SELECT COUNT(*) FROM users u    WHERE u.tenant_id = t.id) AS users,
               (SELECT COUNT(*) FROM assets a   WHERE a.tenant_id = t.id) AS assets,
               (SELECT COUNT(*) FROM scans s    WHERE s.tenant_id = t.id) AS scans,
               (SELECT COUNT(*) FROM findings f WHERE f.tenant_id = t.id) AS findings
        FROM tenants t
        WHERE t.slug IN ({marcadores})
        ORDER BY t.created_at
        """,
        slugs,
    ).fetchall()
    return [dict_from_row(r) for r in rows]


def _borrar_tenant(conn, tenant_id: str) -> dict[str, int]:
    """Borra un tenant y todo lo que cuelga de él. Devuelve filas borradas por tabla."""
    borradas: dict[str, int] = {}

    # Huérfanos por scan: filas cuyo `tenant_id` pudiera estar vacío en datos
    # antiguos pero que sí cuelgan de un scan de este tenant.
    for tabla, columna in (("finding_transiciones", "finding_id"), ("findings", "id")):
        if tabla == "findings":
            n = conn.execute(
                "DELETE FROM findings WHERE scan_id IN (SELECT id FROM scans WHERE tenant_id = ?)",
                (tenant_id,),
            ).rowcount
        else:
            n = conn.execute(
                f"DELETE FROM {tabla} WHERE {columna} IN "
                "(SELECT id FROM findings WHERE scan_id IN (SELECT id FROM scans WHERE tenant_id = ?))",
                (tenant_id,),
            ).rowcount
        if n:
            borradas[f"{tabla} (vía scans)"] = n

    for tabla in TABLAS_POR_TENANT:
        n = conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = ?", (tenant_id,)).rowcount
        if n:
            borradas[tabla] = n

    n = conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,)).rowcount
    borradas["tenants"] = n
    return borradas


def _respaldo(ruta_db: Path) -> Path | None:
    if not ruta_db.is_file():
        return None
    marca = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destino = ruta_db.with_name(f"{ruta_db.name}.bak-{marca}")
    shutil.copy2(ruta_db, destino)
    return destino


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--aplicar", action="store_true", help="Ejecuta el borrado. Sin esto, solo muestra.")
    p.add_argument(
        "--incluir-demo",
        action="store_true",
        help=f"Borra también el tenant sembrado '{SLUG_DEMO}' (recreable con scripts/seed_demo.py).",
    )
    p.add_argument("--slug", action="append", default=[], help="Slug adicional a borrar (repetible).")
    args = p.parse_args()

    slugs = list(SLUGS_QA) + list(args.slug)
    if args.incluir_demo:
        slugs.append(SLUG_DEMO)

    conn = get_conn()
    try:
        inventario = _inventario(conn, tuple(slugs))
        if not inventario:
            print("No hay ninguno de los tenants indicados en esta base. Nada que hacer.")
            return

        print("Tenants que se borrarían (SELECT previo):\n")
        for t in inventario:
            print(
                f"  - {t['name']!r} (slug={t['slug']}, id={t['id']}, creado={t['created_at']})\n"
                f"      users={t['users']}  assets={t['assets']}  scans={t['scans']}  findings={t['findings']}"
            )
        faltantes = sorted(set(slugs) - {t["slug"] for t in inventario})
        if faltantes:
            print(f"\n  (no existen en esta base, se ignoran: {', '.join(faltantes)})")

        if not args.aplicar:
            print("\nModo de solo lectura. Volvé a correrlo con --aplicar para borrar.")
            return

        ruta_db = Path("ciberseguridad.db")
        respaldo = _respaldo(ruta_db)
        if respaldo:
            print(f"\n[respaldo] Copia previa en {respaldo.name}")

        print("\nBorrando…")
        total: dict[str, int] = {}
        for t in inventario:
            borradas = _borrar_tenant(conn, t["id"])
            print(f"  - {t['name']!r}: {borradas}")
            for tabla, n in borradas.items():
                total[tabla] = total.get(tabla, 0) + n
        conn.commit()

        print(f"\nTotal de filas borradas por tabla: {total}")
        restantes = conn.execute("SELECT slug, name FROM tenants ORDER BY created_at").fetchall()
        print(f"\nTenants restantes ({len(restantes)}):")
        for r in restantes:
            print(f"  - {dict_from_row(r)['name']!r} (slug={dict_from_row(r)['slug']})")
        if not restantes:
            print("  (ninguno — la base queda lista para el primer cliente real vía POST /auth/register)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
