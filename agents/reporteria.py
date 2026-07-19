"""Agente de Reportería — sección 7 del plan.

Nodo de convergencia (join) del grafo: llega tanto desde `remediacion`
(camino con autorización) como desde el nodo determinista
`bloqueo_autorizacion` (camino sin autorización, definido en
`orchestrator/graph.py`) y, si está habilitada, desde `antisuplantacion`.

El aviso de "falta autorización firmada" NO depende de que Claude decida
mencionarlo: se inyecta de forma determinista al inicio del reporte si
`autorizacion_bloqueo_motivo` viene poblado, antes de siquiera llamar al
LLM. Así el mensaje crítico nunca puede perderse por un resumen del modelo.
"""

from __future__ import annotations

import json

from orchestrator.state import PipelineState, make_trace_event

from ._llm import LLMNoDisponibleError, call_claude

# Open-core: el prompt real (plantilla editorial de Vigia) vive en
# `vigia_core_private` (ver docs/open-core.md). Fallback genérico si el
# paquete no está instalado.
try:
    from vigia_core_private.reporteria import SYSTEM_PROMPT
except ImportError:
    SYSTEM_PROMPT = (
        "Compilas todo el flujo (recon, hallazgos verificados, prioridad, "
        "remediación) en un reporte único, claro, sin tecnicismos "
        "innecesarios. Estructura básica: resumen de lo encontrado, qué "
        "hacer primero, y detalle técnico. Esta es la versión community: "
        "no usa la plantilla editorial pulida de reportes ejecutivos de "
        "Vigia (esa capa es parte del paquete privado)."
    )

AGENTE = "reporteria"


def _aviso_bloqueo(motivo: str) -> str:
    return (
        "# AVISO — Falta autorización firmada\n\n"
        "El pipeline NO ejecutó la fase de Escaneo Activo. Motivo registrado por el "
        "nodo determinista de autorización: "
        f"{motivo!r}. No se corrió ningún wrapper de `tools/scan.py` contra el "
        "objetivo. Este reporte solo incluye lo recolectado por el Agente de Recon "
        "(pasivo). Para completar la evaluación, el cliente debe firmar el documento "
        "de autorización de pruebas de seguridad (`legal/autorizacion-pruebas-seguridad.md`).\n\n"
    )


def node(state: PipelineState) -> dict:
    partes: list[str] = []
    motivo = state.get("autorizacion_bloqueo_motivo")
    if motivo:
        partes.append(_aviso_bloqueo(motivo))

    resumen_datos = {
        "target": state.get("target"),
        "recon_findings": state.get("recon_findings") or [],
        "verified_findings": state.get("verified_findings") or [],
        "prioritized_findings": state.get("prioritized_findings") or [],
        "remediations": state.get("remediations") or [],
        "antisuplantacion_findings": state.get("antisuplantacion_findings") or [],
    }

    try:
        cuerpo = call_claude(
            SYSTEM_PROMPT,
            "Datos del pipeline para compilar el reporte final (JSON):\n"
            + json.dumps(resumen_datos, ensure_ascii=False, indent=2, default=str),
        )
    except LLMNoDisponibleError as exc:
        cuerpo = (
            "[Reporte narrativo pendiente — LLM no disponible: "
            f"{exc}]\n\nDatos crudos disponibles para armar el reporte manualmente:\n"
            + json.dumps(resumen_datos, ensure_ascii=False, indent=2, default=str)
        )

    partes.append(cuerpo)
    reporte_final = "\n".join(partes)

    return {
        "reporte_final": reporte_final,
        "trace_log": [
            make_trace_event(
                agente=AGENTE,
                accion="compilar_reporte_final",
                resultado=(
                    "reporte generado con aviso de bloqueo de autorización"
                    if motivo
                    else "reporte generado con flujo completo"
                ),
            )
        ],
    }
