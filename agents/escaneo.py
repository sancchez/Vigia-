"""Agente de Escaneo (activo) — sección 7 del plan.

PRECONDICIÓN OBLIGATORIA (redundante a propósito): aunque
`orchestrator/graph.py::gate_autorizacion()` ya es la puerta determinista
que decide si este nodo se alcanza siquiera, este nodo vuelve a comprobar
`autorizacion_firmada` él mismo antes de tocar `tools/scan.py`. Es defensa
en profundidad: si alguien llama `node()` directamente (fuera del grafo,
en un test, en un notebook), el nodo igual se niega a escanear sin
autorización — nunca depende únicamente de que el flujo del grafo se haya
construido bien.
"""

from __future__ import annotations

from orchestrator.state import PipelineState, make_trace_event
from tools._shared import ToolNotInstalledError
from tools.scan import run_nuclei

SYSTEM_PROMPT = (
    "Ejecutas Nuclei y OWASP ZAP contra el objetivo especificado en `scope`.\n"
    "PRECONDICIÓN OBLIGATORIA: si `autorizacion_firmada` no es true, rechaza la tarea\n"
    "y no ejecutes nada. Reporta cada hallazgo crudo con: plantilla/regla que lo\n"
    "disparó, endpoint afectado, severidad reportada por la herramienta. No\n"
    "interpretes ni prioricés todavía — eso lo hace otro agente."
)

AGENTE = "escaneo"


def node(state: PipelineState) -> dict:
    if not state.get("autorizacion_firmada"):
        # Rechazo determinista, sin excepción: nunca se llama tools/scan.py.
        return {
            "trace_log": [
                make_trace_event(
                    agente=AGENTE,
                    accion="rechazar_escaneo_sin_autorizacion",
                    resultado=(
                        "tarea rechazada: autorizacion_firmada no es true, "
                        "no se ejecutó ningún wrapper de tools/scan.py"
                    ),
                )
            ]
        }

    scope = state.get("scope") or {}
    objetivos = list(scope.get("dominios") or [])
    if not objetivos and state.get("target"):
        objetivos = [state["target"]]

    findings: list[dict] = []
    errores: list[str] = []

    for objetivo in objetivos:
        try:
            resultado_nuclei = run_nuclei(objetivo, severity="critical,high,medium")
            for hallazgo in resultado_nuclei.findings:
                findings.append({"objetivo": objetivo, "herramienta": "nuclei", "raw": hallazgo})
        except ToolNotInstalledError as exc:
            errores.append(f"nuclei[{objetivo}]: {exc}")

    resultado = f"{len(findings)} hallazgos crudos sobre {len(objetivos)} objetivo(s)"
    if errores:
        resultado += f"; herramientas no disponibles: {'; '.join(errores)}"

    return {
        "scan_findings": findings,
        "trace_log": [
            make_trace_event(
                agente=AGENTE,
                accion=f"escaneo_activo(objetivos={objetivos})",
                resultado=resultado,
            )
        ],
    }
