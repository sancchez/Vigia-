"""Utilidades comunes para todos los wrappers de herramientas.

Provee: detección de binarios instalados, ejecución de subprocesos con
timeout y captura de salida, y una excepción clara para el caso
"herramienta no instalada" (en vez de fallar silenciosamente o con un
traceback críptico de `FileNotFoundError`).
"""

from __future__ import annotations

import json
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
