"""Agente de Recon (pasivo) — sección 7 del plan.

Llama directamente a los wrappers de `tools/recon.py` (Subfinder + Amass en
modo pasivo). No hay razonamiento de LLM en la recolección: este nodo solo
ejecuta las herramientas y normaliza la salida cruda; el system prompt de
abajo documenta el contrato de comportamiento del agente (qué fuentes usa,
qué no debe hacer) para referencia y para futuros nodos que sí razonen
sobre esta salida (Priorización, Reportería).
"""

from __future__ import annotations

from orchestrator.state import PipelineState, make_trace_event
from tools._shared import ToolExecutionError
from tools.recon import run_amass_passive, run_subfinder

SYSTEM_PROMPT = (
    "Investigas la huella pública de un dominio/marca usando únicamente fuentes pasivas\n"
    "(Subfinder, Amass, crt.sh). No te conectas activamente al objetivo, no envías\n"
    "tráfico que no sea una consulta pública estándar. Devuelves: subdominios\n"
    "encontrados, tecnologías detectadas, y activos que parezcan expuestos por error\n"
    "(paneles de administración, backups públicos). Marca cada hallazgo con la fuente\n"
    "exacta de donde salió."
)

AGENTE = "recon"


def node(state: PipelineState) -> dict:
    target = state.get("target", "")
    findings: list[dict] = []
    errores: list[str] = []

    try:
        subfinder_result = run_subfinder(target)
        for sub in subfinder_result.subdomains:
            findings.append({"subdominio": sub, "fuente": "subfinder"})
    except ToolExecutionError as exc:
        errores.append(f"subfinder: {exc}")

    try:
        amass_result = run_amass_passive(target)
        for sub in amass_result.subdomains:
            findings.append({"subdominio": sub, "fuente": "amass-passive"})
    except ToolExecutionError as exc:
        errores.append(f"amass: {exc}")

    resultado = f"{len(findings)} subdominios encontrados"
    if errores:
        resultado += f"; herramientas no disponibles: {'; '.join(errores)}"

    return {
        "recon_findings": findings,
        "trace_log": [
            make_trace_event(
                agente=AGENTE,
                accion=f"recon_pasivo(target={target!r})",
                resultado=resultado,
            )
        ],
    }
