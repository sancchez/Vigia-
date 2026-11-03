"""Agente de Escaneo (activo) — sección 7 del plan.

PRECONDICIÓN OBLIGATORIA (redundante a propósito): aunque
`orchestrator/graph.py::gate_autorizacion()` ya es la puerta determinista
que decide si este nodo se alcanza siquiera, este nodo vuelve a comprobar
`autorizacion_firmada` él mismo antes de tocar `tools/scan.py`. Es defensa
en profundidad: si alguien llama `node()` directamente (fuera del grafo,
en un test, en un notebook), el nodo igual se niega a escanear sin
autorización — nunca depende únicamente de que el flujo del grafo se haya
construido bien.

SEGUNDA PRECONDICIÓN (fix C-1): `scope.dominios`/`ips`/`apps` son objetivos
REALES de escaneo activo -- este nodo se los pasa directo a Nuclei y ZAP --,
así que quien construye el `PipelineState` es responsable de haber pasado
CADA UNO por el gate de propiedad de activo, no solo `target`. Para el
camino HTTP eso lo hace `api/main.py::_autorizar_todos_los_objetivos` antes
de invocar el grafo. Este nodo no puede verificarlo por sí mismo (no tiene
acceso a la DB ni al tenant), y esa asimetría es exactamente la que produjo
el bypass: `POST /scan` validaba `target` mientras este nodo escaneaba
`scope.dominios`.

`scope.codigo_paths` y `scope.imagenes` son parámetros de OPERADOR: rutas
del filesystem del servidor y referencias de imagen para `docker pull`.
NUNCA deben llegar desde el body de un request de un tenant -- por eso
`api/main.py::ScopeIn` ya no los declara (y rechaza con 422 si se mandan).
El soporte sigue aquí para uso interno/CLI, donde se inyectan server-side.
"""

from __future__ import annotations

from orchestrator.state import PipelineState, make_trace_event
from tools._shared import ToolExecutionError
from tools.scan import run_grype, run_nuclei, run_semgrep, run_trivy_image, run_zap_baseline

from .prompts import get_prompt

# Fase 3: fuente unica de prompts. El texto vive en
# `eval/prompt_versions/v2_escaneo.md` (ver agents/prompts.py). Este agente es
# determinista (no invoca LLM): el prompt documenta su contrato, no se envia.
SYSTEM_PROMPT = get_prompt("escaneo")

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
