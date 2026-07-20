"""Utilidades comunes para todos los wrappers de herramientas.

Provee: detección de binarios instalados, ejecución de subprocesos con
timeout y captura de salida, y una excepción clara para el caso
"herramienta no instalada" (en vez de fallar silenciosamente o con un
traceback críptico de `FileNotFoundError`).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

# Carpetas donde buscar binarios instalados por este mismo proyecto
# (go install deja los binarios en $GOPATH/bin, normalmente ~/go/bin,
# que puede no estar en PATH todavía en la sesión actual).
_EXTRA_BIN_DIRS = [
    Path.home() / "go" / "bin",
]

TOOLS_DIR = Path(__file__).resolve().parent
VENDOR_DIR = TOOLS_DIR / "vendor"


class ToolExecutionError(RuntimeError):
    """Base para fallos esperables al ejecutar una herramienta externa.

    Los agentes (`agents/*.py`) atrapan esta clase base — no cada subtipo
    por separado — para que cualquier forma nueva de fallo "normal" (no
    instalada, timeout, lo que venga después) degrade con gracia al log
    de trazabilidad en vez de tumbar el request con un 500. Un fallo que
    NO hereda de esta clase (ej. un bug real de programación) sí debe
    propagarse y romper el pipeline — eso es intencional.
    """


class ToolNotInstalledError(ToolExecutionError):
    """Se lanza cuando una herramienta subyacente no está disponible.

    Nunca se lanza al importar un módulo de wrappers — solo cuando el
    orquestador intenta ejecutar la función que depende del binario.
    """

    def __init__(self, tool: str, install_hint: str):
        self.tool = tool
        self.install_hint = install_hint
        super().__init__(
            f"'{tool}' no está instalado o no se encontró en PATH. "
            f"Para instalarlo: {install_hint}"
        )


class ToolTimeoutError(ToolExecutionError):
    """Se lanza cuando el proceso de la herramienta excede su timeout.

    Un escaneo real contra un objetivo con muchos activos puede tardar
    más que el timeout por defecto (ej. Nuclei sin plantillas acotadas).
    Esto es esperable, no un bug — el pipeline debe seguir con lo que sí
    alcanzó a correr en vez de crashear la petición completa.
    """

    def __init__(self, tool: str, timeout: int, cmd: Sequence[str]):
        self.tool = tool
        self.timeout = timeout
        super().__init__(
            f"'{tool}' no terminó dentro del límite de {timeout}s y fue "
            f"interrumpido. Comando: {' '.join(cmd)}. Considera acotar el "
            f"alcance (plantillas, severidad) o subir el timeout."
        )


def find_binary(name: str) -> str | None:
    """Busca un binario en PATH y en carpetas conocidas de instalación local."""
    found = shutil.which(name)
    if found:
        return found
    for extra_dir in _EXTRA_BIN_DIRS:
        candidate = extra_dir / name
        if candidate.exists():
            return str(candidate)
        candidate_exe = extra_dir / f"{name}.exe"
        if candidate_exe.exists():
            return str(candidate_exe)
    return None


def require_binary(name: str, install_hint: str) -> str:
    """Igual que find_binary pero lanza ToolNotInstalledError si no existe."""
    path = find_binary(name)
    if path is None:
        raise ToolNotInstalledError(name, install_hint)
    return path


@dataclass
class ToolResult:
    """Resultado normalizado de ejecutar una herramienta externa."""

    tool: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    parsed: Any = field(default=None)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_command(
    tool: str,
    cmd: Sequence[str],
    timeout: int = 300,
    cwd: str | Path | None = None,
) -> ToolResult:
    """Ejecuta un comando externo y normaliza el resultado.

    No lanza excepción si el proceso retorna código != 0 (muchos
    escáneres usan códigos de salida != 0 para "se encontraron
    hallazgos", no para "error"). El llamador decide qué hacer con
    `returncode`/`stderr`.
    """
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        raise ToolNotInstalledError(tool, f"binario no encontrado: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolTimeoutError(tool, timeout, list(cmd)) from exc
    return ToolResult(
        tool=tool,
        command=list(cmd),
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def docker_container_running(binary: str, name: str) -> bool:
    """True si el contenedor `name` sigue vivo del lado del daemon de Docker.

    Usado para el diagnóstico de timeout de `tools/scan.py::_run_zap_script`:
    cuando `subprocess.run(..., timeout=...)` expira, el proceso cliente
    `docker run` muere pero el contenedor puede seguir corriendo del lado
    del daemon (`--rm` solo limpia si el contenedor termina por sí mismo).
    Si `docker inspect` no encuentra el contenedor (código != 0), ya se
    removió (terminó por su cuenta y `--rm` lo limpió) -- se interpreta
    como "no está corriendo", no como error.
    """
    try:
        proc = subprocess.run(
            [binary, "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    if proc.returncode != 0:
        return False
    return proc.stdout.strip() == "true"


def docker_force_remove_container(binary: str, name: str) -> None:
    """Detiene y elimina `name` sin propagar errores.

    Best-effort a propósito: el contenedor puede ya no existir (terminó
    solo y `--rm` ya lo limpió), y este helper se llama justo en el
    manejo de una excepción de timeout -- no debe enmascarar esa
    excepción original con un fallo secundario de limpieza.
    """
    for args in ([binary, "stop", "-t", "5", name], [binary, "rm", "-f", name]):
        try:
            subprocess.run(args, capture_output=True, text=True, timeout=20)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass


def parse_jsonl(text: str) -> list[dict]:
    """Parsea salida JSON Lines (un objeto JSON por línea) ignorando líneas vacías/no-JSON."""
    findings: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            findings.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return findings


# ---------------------------------------------------------------------------
# Normalización de severidad — bug real cerrado en esta sesión (ver
# api/main.py, eval/live_run_report.md).
# ---------------------------------------------------------------------------
#
# `findings.severidad` (db/schema.sql, CHECK IN critical/high/medium/low/info
# -- mismo vocabulario que usan `frontend/src/components/ScanHistoryChart.tsx`
# y `agents/cumplimiento.py`) valía "info" para 1290/1298 filas reales en
# `ciberseguridad.db` al momento de escribir esto. Causa raíz confirmada:
# ambos sitios de inserción en `api/main.py` leían una clave de nivel
# superior que NINGUNA herramienta real pone ahí --
# `hallazgo.get("riskdesc", "info")` (el escaneo activo checkpointed,
# `tools/zap_api.py::get_alerts()`, nunca trae `riskdesc`) y
# `hallazgo.get("severidad") or hallazgo.get("severity") or "info"` (POST
# /scan -- `agents/verificacion.py` nunca setea esas claves, la severidad
# real vive dentro de `raw`, con nombre de campo y vocabulario propio de
# cada herramienta). Mismo patrón de bug ya documentado para el campo
# `tipo` en `docs/cumplimiento.md`.
#
# Cada mapeo de abajo fue verificado contra salida REAL de la herramienta
# en esta sesión (no solo documentación) -- ver eval/live_run_report.md
# para el detalle completo de cada corrida real.

CANONICAL_SEVERITIES = ("critical", "high", "medium", "low", "info")

_SEVERITY_LOGGER = logging.getLogger("vigia.severity")

# Nuclei: JSONL, severidad en `info.severity`, minúsculas. Verificado en
# vivo (`nuclei -jsonl` contra Juice Shop real, este mismo repo): "info" y
# "medium" observados con esos nombres exactos. "critical"/"high"/"low" son
# el mismo vocabulario documentado por projectdiscovery/nuclei-templates
# (schema de severidad compartido por todas las plantillas); "unknown" (el
# quinto valor real del schema) se deja fuera del mapa a propósito -- cae
# al warning de "valor no reconocido" en vez de fingir que sabemos qué
# tan grave es.
_NUCLEI_SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "info": "info",
}

# ZAP: DOS formas reales distintas del mismo hallazgo, según qué ruta de
# código lo produjo (confirmado inspeccionando raw_json real de ambas en
# `ciberseguridad.db`):
#   1. `tools/zap_api.py::get_alerts()` (API real, `core/view/alerts/`,
#      usada por el escaneo activo checkpointed): campo `risk` directo,
#      Title Case -- "High"/"Medium"/"Low"/"Informational". NUNCA trae
#      `riskdesc` (por eso `hallazgo.get("riskdesc", "info")` siempre caía
#      al default).
#   2. `tools/scan.py::_run_zap_script()` (reporte JSON de
#      `zap-baseline.py`/`zap-full-scan.py`, usado por POST /scan): SIN
#      `risk`, pero con `riskdesc` ("Medium (High)" = "Risk (Confidence)")
#      y `riskcode` numérico como string ("0".."3") de respaldo.
_ZAP_RISK_MAP = {
    "high": "high",
    "medium": "medium",
    "low": "low",
    "informational": "info",
}
_ZAP_RISKCODE_MAP = {
    "3": "high",
    "2": "medium",
    "1": "low",
    "0": "info",
}

# Semgrep: `extra.severity`, MAYÚSCULAS. Verificado en vivo corriendo
# `semgrep --config auto --json` contra este mismo repo: ERROR y WARNING
# confirmados con esos nombres exactos. INFO es el tercer valor del enum
# oficial de Semgrep (no salió en esta corrida puntual, no hay reglas de
# severidad "info" que hayan matcheado, pero el campo/vocabulario en sí
# está documentado y consistente con lo verificado).
_SEMGREP_SEVERITY_MAP = {
    "error": "high",
    "warning": "medium",
    "info": "info",
}

# Trivy: `Vulnerabilities[].Severity`, MAYÚSCULAS. Verificado en vivo con
# `trivy image node:14` (imagen vieja con CVEs reales documentados): las 5
# severidades observadas con esos nombres exactos -- UNKNOWN (4), LOW
# (122), MEDIUM (730), HIGH (564), CRITICAL (22).
_TRIVY_SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "unknown": "medium",
}

# Grype: `matches[].vulnerability.severity`, Title Case -- vocabulario
# DISTINTO de Trivy pese a ser dos escáneres de CVEs (confirmado corriendo
# ambos contra la MISMA imagen `node:14` en esta sesión): Grype distingue
# "Negligible" de "Unknown" (Trivy los colapsa en un solo "UNKNOWN"), y usa
# Title Case en vez de MAYÚSCULAS. Las 6 severidades observadas con esos
# nombres exactos -- Unknown (8), Negligible (945), Low (115), Medium
# (631), High (566), Critical (41).
_GRYPE_SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "negligible": "info",
    "unknown": "medium",
}


def normalize_severity(raw: dict | None, herramienta: str) -> str:
    """Traduce la severidad NATIVA de un hallazgo crudo a una de las 5
    severidades canónicas de Vigia: critical/high/medium/low/info (mismo
    vocabulario que `db/schema.sql` -- `findings.severidad CHECK(...)` --,
    `frontend/src/components/ScanHistoryChart.tsx` y `agents/cumplimiento.py`).

    Cada herramienta reporta severidad con un nombre de campo y un
    vocabulario NATIVO propio -- ver los diccionarios `_*_SEVERITY_MAP`
    arriba para el detalle de cada uno, verificados contra salida real (no
    solo documentación) en esta sesión.

    Nunca devuelve silenciosamente "info" para un valor que no reconoce --
    eso repetiría la clase exacta de bug que esta función existe para
    cerrar (ver el módulo arriba). Un valor nativo ausente o no reconocido
    cae a "medium" (default seguro: ni lo esconde como benigno, ni asume
    que es lo peor posible) y siempre se loguea vía
    `logging.getLogger("vigia.severity")` en vez de fallar en silencio.

    Args:
        raw: el hallazgo crudo tal como lo devuelve la herramienta -- para
            ZAP/Nuclei/Semgrep/Trivy/Grype vía `agents/escaneo.py`, esto es
            el valor de la clave `"raw"` de
            `{"objetivo":..., "herramienta":..., "raw":...}`; para el
            escaneo activo checkpointed, directamente un elemento de
            `tools/zap_api.py::get_alerts()`.
        herramienta: identificador de la herramienta que produjo `raw`.
            Acepta "nuclei", "zap"/"zap-baseline"/"zap-full-scan",
            "semgrep", "trivy", "grype" (case-insensitive). Cualquier otro
            valor (incluido vacío/None) se trata como desconocido.

    Returns:
        Una de "critical"/"high"/"medium"/"low"/"info".
    """
    if not isinstance(raw, dict):
        raw = {}
    tool = (herramienta or "").strip().lower()

    nativo: Any = None
    mapa: dict[str, str] = {}

    if tool == "nuclei":
        info = raw.get("info")
        nativo = info.get("severity") if isinstance(info, dict) else None
        mapa = _NUCLEI_SEVERITY_MAP
    elif tool in ("zap", "zap-baseline", "zap-full-scan"):
        nativo = raw.get("risk")
        if nativo is not None:
            mapa = _ZAP_RISK_MAP
        else:
            riskdesc = raw.get("riskdesc")
            if riskdesc:
                # "Medium (High)" -> "Medium" ("Risk (Confidence)").
                nativo = str(riskdesc).split(" ")[0]
                mapa = _ZAP_RISK_MAP
            else:
                riskcode = raw.get("riskcode")
                nativo = str(riskcode).strip() if riskcode is not None else None
                mapa = _ZAP_RISKCODE_MAP
    elif tool == "semgrep":
        extra = raw.get("extra")
        nativo = extra.get("severity") if isinstance(extra, dict) else None
        mapa = _SEMGREP_SEVERITY_MAP
    elif tool == "trivy":
        nativo = raw.get("Severity")
        mapa = _TRIVY_SEVERITY_MAP
    elif tool == "grype":
        vuln = raw.get("vulnerability")
        nativo = vuln.get("severity") if isinstance(vuln, dict) else None
        mapa = _GRYPE_SEVERITY_MAP
    else:
        _SEVERITY_LOGGER.warning(
            "normalize_severity: herramienta desconocida %r -- no se puede "
            "mapear severidad con confianza, usando 'medium' como default seguro",
            herramienta,
        )
        return "medium"

    if nativo is None:
        _SEVERITY_LOGGER.warning(
            "normalize_severity: %s no trajo severidad en el campo esperado "
            "(claves disponibles en raw: %s) -- usando 'medium' como default seguro",
            tool,
            sorted(raw.keys()),
        )
        return "medium"

    canonical = mapa.get(str(nativo).strip().lower())
    if canonical is None:
        _SEVERITY_LOGGER.warning(
            "normalize_severity: valor de severidad no reconocido para %s: %r "
            "-- usando 'medium' (nunca 'info' silencioso para un valor desconocido)",
            tool,
            nativo,
        )
        return "medium"
    return canonical
