"""Siembra la capa de antisuplantación de la cuenta demo REPRODUCIENDO un
snapshot grabado y anonimizado -- **sin una sola llamada de red**.

Cierra el WS1 de `openspec/changes/fase10-antisuplantacion-lista-demo`: la
capa estaba encendida pero `watched_brands` vacío, así que no vigilaba nada y
una demo no tenía qué mostrar.

Por qué reproduce en vez de escanear en vivo
--------------------------------------------
La versión anterior de este script corría `agents.antisuplantacion.node()` con
`run_dnstwist` EN VIVO y traía por defecto el dominio de un banco colombiano
real. Eso significaba que cada `py scripts/seed_demo_antisuplantacion.py`
generaba tráfico de reconocimiento (DNS masivo sobre ~1.5k variantes) contra la
infraestructura y las marcas de un tercero que nunca autorizó nada. Se quitó.

La demo ahora **reproduce** `eval/baseline_real/antisup_bancoejemplo_2026-07-25_snapshot.json`:
el mismo snapshot congelado que usa `eval/run_eval_antisuplantacion.py`, ya
anonimizado a la marca ficticia `bancoejemplo` / `bancoejemplo.com`. Ese
snapshot deriva de una corrida real de dnstwist del 2026-07-25 y preserva
intactas las propiedades de las que depende el triaje: número de registros (45
variantes + el original), clases de equivalencia de nameservers (de las que
sale "defensivo") y presencia/ausencia de MX (de la que sale "urgente"). Por
eso la demo sigue mostrando la misma partición 10/28/7 que la medición real.

Beneficio lateral: la demo es **reproducible y determinista**. Ya no se cae en
vivo delante de un cliente porque un DNS tardó, porque dnstwist no está
instalado, o porque la red del sitio bloquea las consultas. Corre igual offline
y produce exactamente los mismos números en cada corrida.

Qué NO cambia: el triaje. La reproducción alimenta el pipeline REAL
(`tools.antisuplantacion.triar_lookalikes`) y persiste con el MISMO builder que
usa la API (`_extraer_findings_antisuplantacion` +
`_registrar_findings_con_ciclo_de_vida`). Se hereda automáticamente:

  - registros DEFENSIVOS del dueño de la marca (comparten nameserver con el
    dominio legítimo) se EXCLUYEN -- no se le muestran al cliente como amenaza;
  - lookalikes de terceros con MX real suben a `critical` (pueden enviar
    phishing que parezca del cliente);
  - perfiles de redes sociales quedan en `info` (revisión humana), porque
    Sherlock no da señal para atribuir suplantación (medido, ver
    `tools/antisuplantacion.py::run_sherlock`). El snapshot no contiene
    perfiles, así que en modo reproducción no se siembra ninguno.

Modo en vivo (`--live`)
-----------------------
Se conserva, pero **nunca por defecto** y **sin dominio por defecto**: exige
`--dominio` explícito. Úsalo SOLO contra un dominio propio o de un cliente que
firmó autorización. Apuntarlo a la marca de un tercero genera reconocimiento no
autorizado sobre su infraestructura.

Uso:
    py scripts/seed_demo_antisuplantacion.py                      # reproduce el snapshot (default, offline)
    py scripts/seed_demo_antisuplantacion.py --reset              # borra hallazgos previos de suplantación del tenant demo
    py scripts/seed_demo_antisuplantacion.py --snapshot otro.json # reproduce otro snapshot
    py scripts/seed_demo_antisuplantacion.py --live --dominio midominio.com   # SOLO dominio propio/autorizado
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
SNAPSHOT_DEMO_DEFAULT = (
    REPO_ROOT / "eval" / "baseline_real" / "antisup_bancoejemplo_2026-07-25_snapshot.json"
)


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
    públicos (DNS/CT), no toca la infraestructura del titular -- y en el modo
    por defecto de este script ni siquiera eso: reproduce datos grabados.
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
            "Marca vigilada de demo (dominio ficticio de datos reproducidos y anonimizados)",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    return asset_id


def _watched_brand(conn, tenant_id: str, marca: str, dominio: str) -> None:
    """Alta idempotente en `watched_brands` -- lo que el listener CertStream
    consulta para saber qué vigilar en el feed de certificados.

    `dominio_asociado` es obligatorio desde el cierre del hueco de propiedad
    de marcas (ver `api/main.py::_dominio_verificado_para_marca`): sin él, el
    listener ignora la fila. Acá se ancla al mismo dominio de demo que
    `_asset_para` acaba de registrar como asset verificado."""
    row = conn.execute(
        "SELECT id FROM watched_brands WHERE tenant_id = ? AND marca = ? AND is_active = 1",
        (tenant_id, marca),
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE watched_brands SET dominio_asociado = ? WHERE id = ?", (dominio, row["id"])
        )
        return
    conn.execute(
        "INSERT INTO watched_brands (id, tenant_id, marca, dominio_asociado, is_active) "
        "VALUES (?, ?, ?, ?, 1)",
        (str(uuid.uuid4()), tenant_id, marca, dominio),
    )


# ---------------------------------------------------------------------------
# Modo por defecto: reproducción del snapshot grabado (cero red)
# ---------------------------------------------------------------------------

def señales_desde_snapshot(ruta: Path) -> tuple[str, list[dict]]:
    """Reconstruye la salida de `agents.antisuplantacion.node()` desde el
    snapshot congelado, sin tocar la red.

    Deliberadamente NO reimplementa el triaje: rehidrata los mismos
    dataclasses (`DomainVariant`/`AntiSuplantacionResult`) que produce
    `run_dnstwist` -- exactamente como hace `eval/run_eval_antisuplantacion.py`
    -- y llama a `triar_lookalikes()` real. La forma de cada señal es idéntica
    a la que emite `node()`, de modo que `_extraer_findings_antisuplantacion`
    la consume sin saber que vino de un archivo.

    Devuelve `(target, [hallazgo])` con la misma estructura que
    `state["antisuplantacion_findings"]`.
    """
    from tools.antisuplantacion import (
        AntiSuplantacionResult,
        DomainVariant,
        triar_lookalikes,
    )

    with open(ruta, "r", encoding="utf-8") as f:
        data = json.load(f)

    target = data.get("target") or ""
    if not target:
        raise SystemExit(f"El snapshot {ruta} no declara 'target'.")

    variantes = [
        DomainVariant(
            fuzzer=item["fuzzer"],
            domain=item["domain"],
            dns_a=item.get("dns_a") or [],
            registered=bool(item.get("registered", True)),
            raw=item.get("raw") or {},
        )
        for item in data.get("variants", [])
    ]
    resultado = AntiSuplantacionResult(
        target=target, tool=data.get("tool", "dnstwist"), findings=variantes
    )

    triaje = triar_lookalikes(resultado)
    defensivos = {v.domain for v in triaje.propios}
    urgentes = {v.domain for v in triaje.con_correo}

    señales = [
        {
            "tipo": "dominio_variante",
            "fuzzer": v.fuzzer,
            "dominio": v.domain,
            "dns_a": v.dns_a,
            "dns_ns": (v.raw or {}).get("dns_ns", []),
            "registrado": v.registered,
            "es_defensivo": v.domain in defensivos,
            "tiene_correo": v.domain in urgentes,
        }
        for v in resultado.findings
    ]

    # `analisis_claude` se rellena con una nota determinista en vez de llamar
    # al LLM: el modo demo no debe depender de una API key ni de la red, y
    # además el texto debe ser el mismo en cada corrida (si no, la demo deja de
    # ser reproducible). El campo viaja completo en `raw_json`, así que la nota
    # documenta la procedencia del dato allí donde alguien la va a leer.
    analisis = (
        "DEMO REPRODUCIDA (sin análisis de LLM). Estos hallazgos provienen de un "
        f"snapshot grabado y ANONIMIZADO ({ruta.name}) de una corrida real de dnstwist "
        f"del {data.get('captured_at', 'n/d')}. Marca, dominios, nameservers, MX e IPs "
        "son ficticios; se preservaron el número de registros, las clases de "
        "equivalencia de nameservers y la presencia de MX, que son las señales de las "
        "que dependen el triaje 'defensivo' y la severidad 'urgente'. El triaje que "
        "produjo estas severidades es el de producción "
        "(tools.antisuplantacion.triar_lookalikes)."
    )
    return target, [{"señales_crudas": señales, "analisis_claude": analisis}]


def señales_en_vivo(dominio: str) -> list[dict]:
    """Camino en vivo -- SOLO bajo `--live` con un dominio propio o autorizado."""
    # Import tardío: `agents.antisuplantacion` arrastra dnstwist/Sherlock.
    from agents.antisuplantacion import node as antisuplantacion_node
    from orchestrator.state import new_state

    resultado = antisuplantacion_node(
        new_state(target=dominio, antisuplantacion_habilitado=True)
    )
    return resultado.get("antisuplantacion_findings") or []


def seed(reset: bool, live: bool, dominio: str | None, snapshot: Path) -> None:
    # Import tardío: `api.main` compila el grafo LangGraph al importarse.
    from api.main import (
        _extraer_findings_antisuplantacion,
        _registrar_findings_con_ciclo_de_vida,
    )

    # Validación de argumentos ANTES de tocar nada: en modo `--live` esto evita
    # disparar el reconocimiento y recién después descubrir que falta el tenant.
    if live and not dominio:
        raise SystemExit(
            "--live exige --dominio explícito. No hay dominio por defecto a propósito: "
            "el modo en vivo hace reconocimiento DNS masivo y solo debe apuntarse a un "
            "dominio propio o de un cliente que firmó autorización."
        )
    if not live and not snapshot.is_file():
        raise SystemExit(f"No se encontró el snapshot: {snapshot}")

    conn = get_conn()
    try:
        tenant_id = _tenant_demo(conn)

        if live:
            target, señales = dominio, señales_en_vivo(dominio)  # type: ignore[arg-type]
            procedencia = (
                f"Vigilancia de marca (demo EN VIVO) sobre {dominio}: corrida real de dnstwist "
                "sobre registros DNS públicos, autorizada explícitamente con --live --dominio."
            )
        else:
            target, señales = señales_desde_snapshot(snapshot)
            procedencia = (
                f"Vigilancia de marca (demo REPRODUCIDA) sobre {target}: datos de "
                f"{snapshot.name}, snapshot grabado y anonimizado de una corrida real de "
                "dnstwist. Sin tráfico de red. Registros defensivos del titular excluidos; "
                "lookalikes de terceros con servidor de correo elevados a critical."
            )

        if reset:
            n = conn.execute(
                "DELETE FROM findings WHERE tenant_id = ? AND tipo IN "
                "('dominio_variante', 'perfil_red_social')",
                (tenant_id,),
            ).rowcount
            conn.commit()
            print(f"[reset] {n} hallazgos de suplantación previos borrados.")

        marca = target.split(".")[0]
        asset_id = _asset_para(conn, tenant_id, target)
        _watched_brand(conn, tenant_id, marca, target)
        conn.commit()
        print(f"[+] Tenant demo: {tenant_id}")
        print(f"[+] Activo vigilado: {target} (asset {asset_id})")
        print(f"[+] watched_brands: '{marca}' (lo que CertStream vigila en el feed de CT)")
        print(
            f"[+] Modo: {'EN VIVO (reconocimiento real autorizado)' if live else f'REPRODUCCIÓN offline de {snapshot.name}'}"
        )

        crudas = señales[0].get("señales_crudas") if señales else []
        dom_crudos = [s for s in (crudas or []) if s.get("tipo") == "dominio_variante"]
        # `*original` es el propio dominio vigilado, no un lookalike.
        lookalikes = [s for s in dom_crudos if s.get("fuzzer") != "*original"]
        defensivos = sum(1 for s in lookalikes if s.get("es_defensivo"))
        urgentes = sum(1 for s in lookalikes if s.get("tiene_correo") and not s.get("es_defensivo"))
        print(
            f"[+] Lookalikes registrados: {len(lookalikes)} "
            f"({defensivos} defensivos del titular, se excluyen; {urgentes} urgentes con MX real)"
        )

        filas = _extraer_findings_antisuplantacion(target, señales)
        scan_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO scans (id, tenant_id, asset_id, target, autorizacion_firmada, "
            "estado, reporte_final, trace_log_json, completed_at) "
            "VALUES (?, ?, ?, ?, 1, 'completado', ?, '[]', ?)",
            (scan_id, tenant_id, asset_id, target, procedencia, now),
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
    p = argparse.ArgumentParser(
        description=(
            "Siembra la demo de antisuplantación. Por defecto REPRODUCE un snapshot "
            "grabado y anonimizado, sin ninguna llamada de red."
        )
    )
    p.add_argument(
        "--live",
        action="store_true",
        help=(
            "Corre el reconocimiento REAL en vez de reproducir el snapshot. Exige --dominio. "
            "Usar SOLO contra un dominio propio o de un cliente con autorización firmada."
        ),
    )
    p.add_argument(
        "--dominio",
        default=None,
        help="Dominio a vigilar en modo --live. Sin valor por defecto a propósito.",
    )
    p.add_argument(
        "--snapshot",
        type=Path,
        default=SNAPSHOT_DEMO_DEFAULT,
        help=f"Snapshot a reproducir (default: {SNAPSHOT_DEMO_DEFAULT.name}).",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Borra los hallazgos de suplantación previos del tenant demo.",
    )
    args = p.parse_args()
    if args.dominio and not args.live:
        raise SystemExit(
            "--dominio solo aplica con --live. El modo por defecto reproduce un snapshot "
            "grabado (usá --snapshot para elegir otro)."
        )
    seed(reset=args.reset, live=args.live, dominio=args.dominio, snapshot=args.snapshot)


if __name__ == "__main__":
    main()
