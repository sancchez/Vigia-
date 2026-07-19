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
from tools._shared import ToolExecutionError
from tools.scan import run_grype, run_nuclei, run_semgrep, run_trivy_image, run_zap_baseline

SYSTEM_PROMPT = (
    "Ejecutas Nuclei y OWASP ZAP contra el objetivo especificado en `scope.dominios`,\n"
    "y Semgrep/Trivy/Grype contra `scope.codigo_paths`/`scope.imagenes` cuando estén\n"
    "presentes. Mismo agente, misma puerta de autorización — la única diferencia es\n"
    "el tipo de objetivo (URL vs ruta de código/imagen de contenedor), no la capa a\n"
    "la que pertenecen (sección 3.1 del plan: siguen siendo 'Agente de Escaneo').\n"
    "PRECONDICIÓN OBLIGATORIA: si `autorizacion_firmada` no es true, rechaza la tarea\n"
    "y no ejecutes nada. Reporta cada hallazgo crudo con: plantilla/regla que lo\n"
    "disparó, endpoint/archivo/paquete afectado, severidad reportada por la\n"
    "herramienta. No interpretes ni prioricés todavía — eso lo hace otro agente."
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
        except ToolExecutionError as exc:
            errores.append(f"nuclei[{objetivo}]: {exc}")

        try:
            url = objetivo if objetivo.startswith(("http://", "https://")) else f"http://{objetivo}"
            resultado_zap = run_zap_baseline(url)
            for hallazgo in resultado_zap.findings:
                findings.append({"objetivo": objetivo, "herramienta": "zap-baseline", "raw": hallazgo})
        except ToolExecutionError as exc:
            errores.append(f"zap-baseline[{objetivo}]: {exc}")

    # --- Código y dependencias del cliente (SAST/SCA) — mismo agente, mismo
    # gate de autorización, distinto tipo de objetivo (sección 3.1 del plan,
    # ver docstring de `Scope` en orchestrator/state.py). ---
    codigo_paths = list(scope.get("codigo_paths") or [])
    for ruta in codigo_paths:
        try:
            resultado_semgrep = run_semgrep(ruta, config="auto")
            for hallazgo in resultado_semgrep.findings:
                findings.append({"objetivo": ruta, "herramienta": "semgrep", "raw": hallazgo})
        except ToolExecutionError as exc:
            errores.append(f"semgrep[{ruta}]: {exc}")

    imagenes = list(scope.get("imagenes") or [])
    for imagen in imagenes:
        try:
            resultado_trivy = run_trivy_image(imagen)
            for hallazgo in resultado_trivy.findings:
                findings.append({"objetivo": imagen, "herramienta": "trivy", "raw": hallazgo})
        except ToolExecutionError as exc:
            errores.append(f"trivy[{imagen}]: {exc}")

        try:
            resultado_grype = run_grype(f"docker:{imagen}")
            for hallazgo in resultado_grype.findings:
                findings.append({"objetivo": imagen, "herramienta": "grype", "raw": hallazgo})
        except ToolExecutionError as exc:
            errores.append(f"grype[{imagen}]: {exc}")

    total_objetivos = len(objetivos) + len(codigo_paths) + len(imagenes)
    resultado = f"{len(findings)} hallazgos crudos sobre {total_objetivos} objetivo(s)"
    if errores:
        resultado += f"; herramientas no disponibles: {'; '.join(errores)}"

    return {
        "scan_findings": findings,
        "trace_log": [
            make_trace_event(
                agente=AGENTE,
                accion=(
                    f"escaneo_activo(objetivos={objetivos}, "
                    f"codigo_paths={codigo_paths}, imagenes={imagenes})"
                ),
                resultado=resultado,
            )
        ],
    }
