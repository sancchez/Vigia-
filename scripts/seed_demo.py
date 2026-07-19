"""Siembra datos de demo reproducibles para Vigia.

Resuelve el punto 2 del checklist "demo-ready" de
`docs/produccion-readiness.md`: la cuenta `demo@vigia.local` vivía solo en
`ciberseguridad.db` (gitignored) de una sesión anterior -- si esa sesión se
perdió o la demo corre en una máquina/deploy nuevo, la cuenta no existe. Este
script la recrea de cero, sobre cualquier `DATABASE_URL` configurada
(SQLite o Postgres, vía `db.connection.get_conn()` -- mismo backend que usa
la API real, no un script paralelo con su propio acceso a datos).

Qué crea:
  - Tenant "Vigia Demo" (owner: demo@vigia.local / DemoVigia2026).
  - 3 assets (dominio) plausibles para una pyme colombiana de e-commerce.
  - 1 scan "completado" contra Juice Shop y sus 10 hallazgos reales de ZAP
    baseline -- los mismos ya capturados y congelados en
    `eval/cumplimiento_fixture_juiceshop.json` (ver
    `tests/test_cumplimiento_categorizacion.py`, que usa el mismo fixture).
    Así el dashboard, `GET /findings` y `GET /reports/cumplimiento` tienen
    contenido real sin depender de que Docker/ZAP/Nuclei funcionen en vivo
    en el momento exacto de una demo.

Idempotente por diseño: si el tenant demo ya existe, no falla ni duplica --
usa `--reset` para borrarlo primero y recrearlo de cero (útil si el fixture
o los assets de demo cambiaron).

Uso:
    py scripts/seed_demo.py           # crea si no existe, no toca si ya existe
    py scripts/seed_demo.py --reset   # borra el tenant demo (si existe) y recrea
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from auth.jwt_auth import get_password_hash  # noqa: E402
from db.connection import dict_from_row, get_conn  # noqa: E402

DEMO_TENANT_SLUG = "vigia-demo"
DEMO_TENANT_NAME = "Vigia Demo"
DEMO_EMAIL = "demo@vigia.local"
DEMO_PASSWORD = "DemoVigia2026"

DEMO_ASSETS = [
    {"tipo": "dominio", "valor": "demo-tienda.co", "notas": "Sitio público principal (demo)"},
    {"tipo": "dominio", "valor": "app.demo-tienda.co", "notas": "Panel de clientes (demo)"},
    {"tipo": "dominio", "valor": "api.demo-tienda.co", "notas": "API pública (demo)"},
]

FIXTURE_PATH = REPO_ROOT / "eval" / "cumplimiento_fixture_juiceshop.json"

# Mapeo severidad ZAP (riskcode) -> severidad del schema de Vigia
# (findings.severidad CHECK IN critical/high/medium/low/info). El fixture ya
# trae "severidad": "info" para las 10 filas (todas informativas/bajo riesgo
# en la corrida real de ZAP baseline contra Juice Shop, ver
# docs/cumplimiento.md) -- se respeta tal cual, no se reinventa.


def _slugify_available(conn, base_slug: str) -> str:
    """Mismo criterio que `api/main.py::_unique_slug` -- reimplementado aquí
    a propósito en vez de importar desde `api.main` (ese módulo compila el
    grafo de LangGraph y arranca el scheduler/CertStream listener al
    importarse -- side effects que un script de seed no debería disparar)."""
    slug = base_slug
    n = 1
    while conn.execute("SELECT 1 FROM tenants WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base_slug}-{n}"
    return slug


def _borrar_tenant_demo(conn) -> None:
    row = conn.execute("SELECT id FROM tenants WHERE slug = ?", (DEMO_TENANT_SLUG,)).fetchone()
    if row is None:
        return
    tenant_id = dict_from_row(row)["id"]
    # ON DELETE CASCADE en el schema se encarga de users/assets/scans/
    # findings/invitations/subscriptions colgados de este tenant.
    conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
    conn.commit()
    print(f"[reset] Tenant demo anterior ({tenant_id}) borrado junto con todos sus datos.")


def seed(reset: bool) -> None:
    if not FIXTURE_PATH.exists():
        raise SystemExit(
            f"No se encontró el fixture de hallazgos reales: {FIXTURE_PATH}\n"
            "(eval/cumplimiento_fixture_juiceshop.json debería existir en el repo)."
        )
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    findings_fixture = fixture["findings"]

    conn = get_conn()
    try:
        if reset:
            _borrar_tenant_demo(conn)

        existing = conn.execute(
            "SELECT id FROM tenants WHERE slug = ?", (DEMO_TENANT_SLUG,)
        ).fetchone()
        if existing is not None:
            print(
                f"El tenant demo ya existe (slug='{DEMO_TENANT_SLUG}') -- nada que hacer.\n"
                "Usa --reset si quieres borrarlo y recrearlo de cero."
            )
            print(f"\nCuenta de demo: {DEMO_EMAIL} / {DEMO_PASSWORD}")
            return

        tenant_id = str(uuid.uuid4())
        slug = _slugify_available(conn, DEMO_TENANT_SLUG)
        conn.execute(
            "INSERT INTO tenants (id, slug, name, plan) VALUES (?, ?, ?, 'trial')",
            (tenant_id, slug, DEMO_TENANT_NAME),
        )

        user_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, tenant_id, email, hashed_password, role) "
            "VALUES (?, ?, ?, ?, 'owner')",
            (user_id, tenant_id, DEMO_EMAIL, get_password_hash(DEMO_PASSWORD)),
        )

        conn.execute(
            "INSERT INTO subscriptions (tenant_id, plan, estado) VALUES (?, 'trial', 'trial')",
            (tenant_id,),
        )

        asset_ids: list[str] = []
        for asset in DEMO_ASSETS:
            asset_id = str(uuid.uuid4())
            asset_ids.append(asset_id)
            conn.execute(
                "INSERT INTO assets (id, tenant_id, tipo, valor, notas) VALUES (?, ?, ?, ?, ?)",
                (asset_id, tenant_id, asset["tipo"], asset["valor"], asset["notas"]),
            )

        # El scan de demo se cuelga del primer asset (dominio público) --
        # el target real fue Juice Shop (http://localhost:3050) en la
        # corrida que generó el fixture, se conserva tal cual como
        # trazabilidad honesta del origen de los datos, no se disfraza como
        # si hubiera sido contra demo-tienda.co.
        scan_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO scans "
            "(id, tenant_id, asset_id, target, autorizacion_firmada, estado, "
            " reporte_final, trace_log_json, completed_at) "
            "VALUES (?, ?, ?, ?, 1, 'completado', ?, '[]', ?)",
            (
                scan_id,
                tenant_id,
                asset_ids[0],
                "http://localhost:3050",
                "Escaneo de demo (ZAP baseline, datos reales de una corrida "
                "anterior contra OWASP Juice Shop) -- ver eval/cumplimiento_"
                "fixture_juiceshop.json para el origen exacto de estos "
                "hallazgos. Precargado por scripts/seed_demo.py para que la "
                "demo no dependa de Docker/ZAP en vivo.",
                now,
            ),
        )

        for f in findings_fixture:
            finding_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO findings "
                "(id, scan_id, tenant_id, tipo, severidad, endpoint, confirmado, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    finding_id,
                    scan_id,
                    tenant_id,
                    f["tipo"],
                    f["severidad"],
                    f["endpoint"],
                    int(f["confirmado"]),
                    json.dumps(f["raw_json"], ensure_ascii=False),
                ),
            )

        conn.commit()
    finally:
        conn.close()

    print("Demo sembrada correctamente:")
    print(f"  Tenant:  {DEMO_TENANT_NAME} (slug={slug})")
    print(f"  Cuenta:  {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"  Assets:  {len(asset_ids)} ({', '.join(a['valor'] for a in DEMO_ASSETS)})")
    print(f"  Scan:    1 completado, {len(findings_fixture)} hallazgos reales (Juice Shop)")
    print(
        "\nListo para iniciar sesión en el frontend o pegar el flujo "
        "POST /auth/login -> GET /findings / GET /reports/cumplimiento."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Borra el tenant demo existente (y todo lo colgado de él) antes de recrearlo.",
    )
    args = parser.parse_args()
    seed(reset=args.reset)


if __name__ == "__main__":
    main()
