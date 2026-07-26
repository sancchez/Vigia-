"""Siembra la capa de antisuplantación de la cuenta demo con datos REALES.

Cierra el WS1 de `openspec/changes/fase10-antisuplantacion-lista-demo`: la
capa estaba encendida pero `watched_brands` vacío, así que no vigilaba nada y
una demo no tenía qué mostrar.

A diferencia de `scripts/seed_demo.py` (que precarga un fixture congelado de
hallazgos de vulnerabilidades), este script corre el pipeline REAL de
antisuplantación -- `agents.antisuplantacion.node()` con `run_dnstwist` en
vivo -- y persiste el resultado con el MISMO builder que usa la API
(`_extraer_findings_antisuplantacion` + `_registrar_findings_con_ciclo_de_vida`).
Por eso hereda automáticamente el triaje ya cableado:

  - registros DEFENSIVOS del dueño de la marca (comparten nameserver con el
    dominio legítimo) se EXCLUYEN -- no se le muestran al cliente como amenaza;
  - lookalikes de terceros con MX real suben a `critical` (pueden enviar
    phishing que parezca del cliente);
  - perfiles de redes sociales quedan en `info` (revisión humana), porque
    Sherlock no da señal para atribuir suplantación (medido, ver
    `tools/antisuplantacion.py::run_sherlock`).

**Sobre los dominios usados:** son marcas públicas grandes, elegidas porque
tienen suplantadores reales y registros DNS públicos, de modo que la demo
muestre datos verdaderos y no inventados. Vigia NO las representa ni tiene
relación con ellas: son objetivos de demostración técnica sobre información
de registro público (mismo tipo de consulta que un WHOIS). Para una demo a un
cliente concreto, pasar `--dominio` con el dominio de ese cliente.

Uso:
    py scripts/seed_demo_antisuplantacion.py                 # dominio por defecto
    py scripts/seed_demo_antisuplantacion.py --dominio x.com # dominio propio
    py scripts/seed_demo_antisuplantacion.py --reset         # borra hallazgos previos de suplantación del tenant demo
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

from db.connection import dict_from_row, get_conn  # noqa: E402

DEMO_TENANT_SLUG = "vigia-demo"
DOMINIO_DEMO_DEFAULT = "bancolombia.com"


def _tenant_demo(conn) -> str:
    row = conn.execute("SELECT id FROM tenants WHERE slug = ?", (DEMO_TENANT_SLUG,)).fetchone()
    if row is None:
        raise SystemExit(
            f"No existe el tenant demo (slug='{DEMO_TENANT_SLUG}').\n"
            "Corré primero: py scripts/seed_demo.py"
        )
    return dict_from_row(row)["id"]


def _asset_para(conn, tenant_id: str, dominio: str) -> str:
    """Asset dominio verificado para el tenant demo (idempotente).

    Se marca `verificado=1` porque es un activo de demo: el gate de propiedad
    (`asset_autorizado_para_escanear`) existe para el escaneo ACTIVO, que sí
    toca los sistemas del cliente. Antisuplantación solo consulta registros
    públicos (DNS/CT), no toca la infraestructura del titular.
    """
    row = conn.execute(
        "SELECT id FROM assets WHERE tenant_id = ? AND valor = ?", (tenant_id, dominio)
    ).fetchone()
    if row is not None:
        return dict_from_row(row)["id"]
    asset_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO assets (id, tenant_id, tipo, valor, notas, verificado, verified_at) "
        "VALUES (?, ?, 'dominio', ?, ?, 1, ?)",
        (
            asset_id,
            tenant_id,
            dominio,
            "Marca vigilada de demo (datos públicos, Vigia no representa a esta marca)",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    return asset_id


def _watched_brand(conn, tenant_id: str, marca: str) -> None:
    """Alta idempotente en `watched_brands` -- lo que el listener CertStream
    consulta para saber qué vigilar en el feed de certificados."""
    row = conn.execute(
        "SELECT id FROM watched_brands WHERE tenant_id = ? AND marca = ? AND is_active = 1",
        (tenant_id, marca),
    ).fetchone()
    if row is not None:
        return
    conn.execute(
        "INSERT INTO watched_brands (id, tenant_id, marca, is_active) VALUES (?, ?, ?, 1)",
        (str(uuid.uuid4()), tenant_id, marca),
    )


def seed(dominio: str, reset: bool) -> None:
    # Import tardío: `api.main` compila el grafo LangGraph al importarse.
    from agents.antisuplantacion import node as antisuplantacion_node
    from api.main import (
        _extraer_findings_antisuplantacion,
        _registrar_findings_con_ciclo_de_vida,
    )
    from orchestrator.state import new_state

    conn = get_conn()
    try:
        tenant_id = _tenant_demo(conn)
        if reset:
            n = conn.execute(
                "DELETE FROM findings WHERE tenant_id = ? AND tipo IN "
                "('dominio_variante', 'perfil_red_social')",
                (tenant_id,),
            ).rowcount
            conn.commit()
            print(f"[reset] {n} hallazgos de suplantación previos borrados.")

        marca = dominio.split(".")[0]
        asset_id = _asset_para(conn, tenant_id, dominio)
        _watched_brand(conn, tenant_id, marca)
        conn.commit()
        print(f"[+] Tenant demo: {tenant_id}")
        print(f"[+] Activo vigilado: {dominio} (asset {asset_id})")
        print(f"[+] watched_brands: '{marca}' (lo que CertStream vigila en el feed de CT)")

        print(f"[+] Corriendo el pipeline REAL de antisuplantación contra {dominio} ...")
        resultado = antisuplantacion_node(new_state(target=dominio, antisuplantacion_habilitado=True))
        señales = resultado.get("antisuplantacion_findings") or []

        crudas = señales[0].get("señales_crudas") if señales else []
        dom_crudos = [s for s in (crudas or []) if s.get("tipo") == "dominio_variante"]
        defensivos = sum(1 for s in dom_crudos if s.get("es_defensivo"))
        print(f"[+] Detectados: {len(dom_crudos)} dominios ({defensivos} defensivos del titular, se excluyen)")

        filas = _extraer_findings_antisuplantacion(dominio, señales)
        scan_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, "
            "estado, reporte_final, trace_log_json, completed_at) "
            "VALUES (?, ?, ?, ?, 1, 'completado', ?, '[]', ?)",
            (
                scan_id,
                tenant_id,
                asset_id,
                dominio,
                f"Vigilancia de marca (demo) sobre {dominio}: datos reales de dnstwist "
                "sobre registros DNS públicos. Registros defensivos del titular excluidos; "
                "lookalikes de terceros con servidor de correo elevados a critical.",
                now,
            ),
        )

        normalizados = [
            {
                "tipo": f["tipo"],
                "severidad": f["severidad"],
                "endpoint": f["endpoint"],
                "confirmado": False,
                "raw_json": json.dumps(f["raw_json"], default=str),
            }
            for f in filas
        ]
        _registrar_findings_con_ciclo_de_vida(conn, tenant_id, scan_id, asset_id, normalizados, now)
        conn.commit()

        por_sev: dict[str, int] = {}
        for f in filas:
            por_sev[f["severidad"]] = por_sev.get(f["severidad"], 0) + 1
        print(f"[+] Persistidos {len(filas)} hallazgos: {por_sev}")
        print("\nDemo lista. Verificar con:")
        print("  GET /antisuplantacion/dominios   (login: demo@vigia.local / DemoVigia2026)")
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dominio", default=DOMINIO_DEMO_DEFAULT, help="Dominio a vigilar.")
    p.add_argument("--reset", action="store_true", help="Borra los hallazgos de suplantación previos del tenant demo.")
    args = p.parse_args()
    seed(dominio=args.dominio, reset=args.reset)


if __name__ == "__main__":
    main()
