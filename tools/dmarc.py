"""Parser de reportes DMARC agregados (RUA, RFC 7489) para Fase 3e.

Ingesta v1 = **upload manual**: el usuario apunta su propio `rua=mailto:` a
un buzón que ya controla (config DNS de su dominio) y sube aquí el XML que
ese buzón recibe -- ver `openspec/changes/fase3e-monitoreo-dmarc/design.md`
para el porqué (cero infra nueva vs. un buzón dedicado + poller IMAP,
diferido). La evolución futura (auto-ingesta) reutilizaría el mismo parser.

**Seguridad**: un reporte RUA es entrada NO CONFIABLE -- cualquiera puede
enviar un XML manipulado al buzón `rua=` que el cliente luego sube aquí.
Por eso este módulo usa `defusedxml.ElementTree`, NUNCA
`xml.etree.ElementTree` directo, para evitar XXE (entidades externas) y
"billion laughs" (expansión recursiva de entidades). `defusedxml` lanza una
excepción controlada ante cualquiera de esos ataques -- el llamador
(`api/main.py`) la traduce a 422, no la deja convertirse en un 500 ni,
peor, en una lectura de archivo/colgada del proceso.

Mismo patrón que `fase1-deteccion-visible`/`fase2c-verificacion-cierre-y-kpis`:
parser tolerante sobre bytes -> `raw_json` normalizado -> agregación en
Python en lectura (sin funciones de fecha SQL, portable entre SQLite y
Postgres).
"""

from __future__ import annotations

import gzip
import io
import zipfile
from datetime import datetime, timezone
from typing import Any

import defusedxml.ElementTree as DefusedET

# Magic bytes para detectar el formato real del archivo subido,
# independientemente de la extensión que el usuario le haya puesto.
_GZIP_MAGIC = b"\x1f\x8b"
_ZIP_MAGIC = b"PK"


class DmarcParseError(Exception):
    """El archivo no es un reporte DMARC agregado válido/seguro de parsear.

    Cubre: XML mal formado, XXE/entidad recursiva detectada por
    `defusedxml`, raíz distinta de `<feedback>` (RFC 7489), o un
    `.zip`/`.gz` corrupto o vacío. El llamador (`api/main.py`) traduce
    SIEMPRE esta excepción a 422 -- nunca persiste una fila a medias.
    """


def _decompress(data: bytes) -> bytes:
    """Descomprime `data` según sus magic bytes; XML plano pasa directo.

    - `.xml.gz` (magic `1f 8b`): un solo miembro gzip, se descomprime tal cual.
    - `.zip` (magic `PK`, típico de reportes de Microsoft/algunos receptores):
      se toma el PRIMER archivo del zip que no sea un directorio -- un
      reporte agregado real trae un único XML adentro.
    - Cualquier otro contenido se asume XML plano y se devuelve sin tocar.
    """
    if not data:
        raise DmarcParseError("Archivo vacío.")

    if data[:2] == _GZIP_MAGIC:
        try:
            return gzip.decompress(data)
        except OSError as exc:
            raise DmarcParseError(f"No se pudo descomprimir el .gz: {exc}") from exc

    if data[:2] == _ZIP_MAGIC:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                nombres = [n for n in zf.namelist() if not n.endswith("/")]
                if not nombres:
                    raise DmarcParseError("El .zip no contiene ningún archivo.")
                return zf.read(nombres[0])
        except zipfile.BadZipFile as exc:
            raise DmarcParseError(f"El .zip está corrupto: {exc}") from exc

    return data


def _texto(elem: Any, path: str, default: str = "") -> str:
    """`elem.findtext` tolerante -- devuelve `default` si el nodo no existe."""
    if elem is None:
        return default
    valor = elem.findtext(path)
    return valor if valor is not None else default


def _epoch_a_iso(valor: str | None) -> str | None:
    """Convierte un epoch Unix (string, como lo trae `<date_range>`) a ISO
    UTC. Tolerante: valores ausentes o no numéricos devuelven `None` en vez
    de reventar -- un campo de fecha faltante no debe tumbar el parseo de
    todo el reporte (ver spec.md, "parser tolerante")."""
    if not valor:
        return None
    try:
        return datetime.fromtimestamp(int(valor), tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def parse_aggregate_report(data: bytes) -> dict:
    """Parsea los bytes de un reporte DMARC agregado (`.xml`/`.xml.gz`/`.zip`).

    Devuelve:
        {
            "org": str, "dominio": str,
            "rango_inicio": str | None, "rango_fin": str | None,
            "records": [
                {"source_ip": str, "count": int, "disposition": str,
                 "spf_aligned": bool, "dkim_aligned": bool, "autorizado": bool},
                ...
            ],
        }

    Lanza `DmarcParseError` si el archivo no descomprime, el XML dispara la
    protección de `defusedxml` (XXE/entidad recursiva), o la raíz no es
    `<feedback>` (RFC 7489) -- en ningún caso persiste una fila vacía el
    llamador.
    """
    xml_bytes = _decompress(data)

    try:
        root = DefusedET.fromstring(xml_bytes)
    except DefusedET.ParseError as exc:
        raise DmarcParseError(f"XML mal formado: {exc}") from exc
    except Exception as exc:
        # defusedxml expone excepciones propias (EntitiesForbidden,
        # ExternalReferenceForbidden, etc.) para XXE/billion-laughs -- no
        # todas heredan de ParseError, así que se capturan aquí también y
        # se traducen igual a DmarcParseError (nunca se propaga cruda).
        raise DmarcParseError(f"XML rechazado por seguridad (XXE/expansión de entidades): {exc}") from exc

    if root is None or root.tag != "feedback":
        raise DmarcParseError(
            f"Raíz XML inválida: se esperaba <feedback> (RFC 7489), se encontró "
            f"<{root.tag if root is not None else None}>."
        )

    metadata = root.find("report_metadata")
    policy = root.find("policy_published")

    org = _texto(metadata, "org_name", default="desconocido")
    dominio = _texto(policy, "domain", default="")
    date_range = metadata.find("date_range") if metadata is not None else None
    rango_inicio = _epoch_a_iso(_texto(date_range, "begin", default="") or None)
    rango_fin = _epoch_a_iso(_texto(date_range, "end", default="") or None)

    records: list[dict] = []
    for record_elem in root.findall("record"):
        row = record_elem.find("row")
        source_ip = _texto(row, "source_ip", default="desconocido")
        try:
            count = int(_texto(row, "count", default="1") or "1")
        except ValueError:
            count = 1

        policy_evaluated = row.find("policy_evaluated") if row is not None else None
        disposition = _texto(policy_evaluated, "disposition", default="none")
        # `policy_evaluated/dkim` y `.../spf` YA son el veredicto CON
        # alineación aplicada (no el resultado crudo de auth_results) --
        # es el campo correcto de RFC 7489 para decidir "autorizado".
        spf_aligned = _texto(policy_evaluated, "spf", default="fail") == "pass"
        dkim_aligned = _texto(policy_evaluated, "dkim", default="fail") == "pass"

        records.append(
            {
                "source_ip": source_ip,
                "count": count,
                "disposition": disposition,
                "spf_aligned": spf_aligned,
                "dkim_aligned": dkim_aligned,
                # Autorizado = SPF O DKIM alineados (definición DMARC de
                # "pasa la política del dominio") -- ver spec.md.
                "autorizado": spf_aligned or dkim_aligned,
            }
        )

    return {
        "org": org,
        "dominio": dominio,
        "rango_inicio": rango_inicio,
        "rango_fin": rango_fin,
        "records": records,
    }


def aggregate_senders(reports: list[dict]) -> list[dict]:
    """Agrega los `records` de varios reportes (ya parseados, `raw_json`
    normalizado) por `source_ip`.

    Devuelve una lista ordenada por `mensajes` descendente:
        [{"source_ip": str, "mensajes": int, "spf_pass": int, "dkim_pass": int,
          "autorizado": bool}, ...]

    `autorizado` a nivel de IP es `False` si CUALQUIER mensaje de esa IP
    falló alineación (ni SPF ni DKIM) en cualquier reporte -- una IP con
    tráfico mixto (a veces autorizado, a veces no) se marca como no
    autorizada para no ocultar el abuso parcial.
    """
    por_ip: dict[str, dict[str, Any]] = {}

    for reporte in reports:
        for record in reporte.get("records", []):
            source_ip = record.get("source_ip", "desconocido")
            count = int(record.get("count", 1) or 1)
            spf_aligned = bool(record.get("spf_aligned"))
            dkim_aligned = bool(record.get("dkim_aligned"))
            autorizado = bool(record.get("autorizado", spf_aligned or dkim_aligned))

            entry = por_ip.setdefault(
                source_ip,
                {"source_ip": source_ip, "mensajes": 0, "spf_pass": 0, "dkim_pass": 0, "autorizado": True},
            )
            entry["mensajes"] += count
            if spf_aligned:
                entry["spf_pass"] += count
            if dkim_aligned:
                entry["dkim_pass"] += count
            if not autorizado:
                entry["autorizado"] = False

    resultado = list(por_ip.values())
    resultado.sort(key=lambda e: e["mensajes"], reverse=True)
    return resultado
