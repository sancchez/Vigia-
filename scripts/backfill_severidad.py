"""Backfill de una sola vez: re-deriva `findings.severidad` desde `raw_json`
usando `tools._shared.normalize_severity`, para las filas reales que quedaron
mal marcadas por el bug cerrado esta sesión (ver `api/main.py`,
`tools/_shared.py::normalize_severity`, `eval/live_run_report.md`).

Contexto: `findings.severidad` valía `"info"` para ~99% de las filas reales
de `ciberseguridad.db` porque el código viejo leía una clave de nivel
superior (`riskdesc`/`severidad`/`severity`) que ninguna herramienta real
pone ahí -- la severidad real siempre estuvo disponible dentro de
`raw_json`, solo que nunca se leyó de ahí. El fix ya está en `api/main.py`
para escaneos NUEVOS; este script corrige las filas VIEJAS que ya quedaron
grabadas con el valor incorrecto.

Qué filas SÍ toca: cualquier fila cuyo `raw_json` tenga forma reconocible de
un hallazgo de escaneo (Nuclei/ZAP/Semgrep/Trivy/Grype) -- tanto el wrapper
`{"objetivo","herramienta","raw"}` de `agents/escaneo.py` (POST /scan) como
el alert crudo de ZAP sin envolver (`tools/zap_api.py::get_alerts()`, usado
por el escaneo activo checkpointed).

Qué filas NO toca (a propósito): hallazgos de `agents/antisuplantacion.py`
(`raw_json.fuente == "antisuplantacion_bajo_demanda"`) y de
`api/certstream_listener.py` (`raw_json.fuente == "certstream"`) -- esos ya
usan valores hardcodeados correctos ("high"/"medium") escritos a propósito
en el código, no son parte de este bug (ver el encargo original). Se
cuentan aparte en el resumen para que quede explícito que se revisaron y se
decidió dejarlos como están, no que se pasaron por alto.

Uso:
    py scripts/backfill_severidad.py            # aplica el backfill de verdad
    py scripts/backfill_severidad.py --dry-run  # solo reporta qué cambiaría

Antes de escribir nada, hace un backup del archivo SQLite
(`<db>.bak-<timestamp>`) si `DATABASE_URL` apunta a un archivo local -- no
hay backup automático para Postgres (fuera de alcance de este script).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import os  # noqa: E402

from db.connection import dict_from_row, get_conn  # noqa: E402
from tools._shared import normalize_severity  # noqa: E402

_FUENTES_EXCLUIDAS = {"antisuplantacion_bajo_demanda", "certstream"}


def _backup_sqlite_si_aplica() -> str | None:
    url = os.environ.get("DATABASE_URL", "sqlite:///./ciberseguridad.db")
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    db_path = Path(url[len(prefix):])
    if not db_path.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.name}.bak-{ts}")
    shutil.copy2(db_path, backup_path)
    return str(backup_path)


def _clasificar_y_normalizar(raw_json_text: str) -> tuple[str | None, dict | None]:
    """Devuelve `(nueva_severidad, motivo_si_se_omite)`.

    `nueva_severidad` es `None` si la fila se omite a propósito (fuente
    excluida, o `raw_json` no tiene una forma reconocible de hallazgo de
    escaneo) -- en ese caso el segundo elemento trae contexto para el
    resumen final.
    """
    try:
        d = json.loads(raw_json_text)
    except (json.JSONDecodeError, TypeError):
        return None, {"motivo": "raw_json no parseable"}
    if not isinstance(d, dict):
        return None, {"motivo": "raw_json no es un objeto"}

    fuente = d.get("fuente")
    if fuente in _FUENTES_EXCLUIDAS:
        return None, {"motivo": f"excluida a propósito (fuente={fuente})"}

    if "herramienta" in d and "raw" in d:
        # Wrapper de agents/escaneo.py (POST /scan): {"objetivo","herramienta","raw",...verificación}
        tool = d.get("herramienta") or ""
        raw = d.get("raw")
        return normalize_severity(raw, tool), None

    if "pluginId" in d or "risk" in d or "riskcode" in d:
        # Alert crudo de tools/zap_api.py::get_alerts() (escaneo activo checkpointed),
        # guardado sin envolver -- ver api/main.py::_correr_escaneo_activo_en_background.
        return normalize_severity(d, "zap"), None

    return None, {"motivo": "forma de raw_json no reconocida (ni wrapper ni alert de ZAP)"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe nada, solo reporta.")
    args = parser.parse_args()

    conn = get_conn()
    try:
        antes = Counter(
            dict_from_row(r)["severidad"] for r in conn.execute("SELECT severidad FROM findings").fetchall()
        )
        total = sum(antes.values())
        print(f"Total de filas en findings: {total}")
        print(f"Distribución ANTES: {dict(antes)}")

        filas = conn.execute("SELECT id, severidad, raw_json FROM findings").fetchall()
        filas = [dict_from_row(r) for r in filas]

        cambios: list[tuple[str, str, str]] = []  # (id, severidad_vieja, severidad_nueva)
        omitidas = Counter()
        sin_cambio = 0

        for fila in filas:
            nueva, motivo = _clasificar_y_normalizar(fila["raw_json"])
            if nueva is None:
                omitidas[motivo["motivo"]] += 1
                continue
            if nueva == fila["severidad"]:
                sin_cambio += 1
                continue
            cambios.append((fila["id"], fila["severidad"], nueva))

        print(f"\nFilas a actualizar: {len(cambios)}")
        print(f"Filas ya correctas (sin cambio): {sin_cambio}")
        print(f"Filas omitidas a propósito: {dict(omitidas)}")

        muestra = cambios[:10]
        if muestra:
            print("\nMuestra de cambios (hasta 10):")
            for id_, vieja, nueva in muestra:
                print(f"  {id_}: {vieja!r} -> {nueva!r}")

        if args.dry_run:
            print("\n--dry-run: no se escribió nada.")
            return 0

        if not cambios:
            print("\nNada que actualizar.")
            return 0

        backup = _backup_sqlite_si_aplica()
        if backup:
            print(f"\nBackup creado: {backup}")

        for id_, _vieja, nueva in cambios:
            conn.execute("UPDATE findings SET severidad = ? WHERE id = ?", (nueva, id_))
        conn.commit()

        despues = Counter(
            dict_from_row(r)["severidad"] for r in conn.execute("SELECT severidad FROM findings").fetchall()
        )
        print(f"\nDistribución DESPUÉS: {dict(despues)}")
        print(f"\n{len(cambios)} filas actualizadas.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
