"""Agente de Remediación — sección 7 del plan.

Para cada hallazgo priorizado, le pide a Claude una corrección concreta y
redactada para un lector no técnico. No bloquea el pipeline si Claude no
está disponible: deja un remedio genérico marcado como "pendiente" para
que Reportería igual pueda armar el documento y un humano complete el
detalle (revisión humana obligatoria en los primeros clientes — sección 8.2).
"""

from __future__ import annotations

from orchestrator.state import PipelineState, make_trace_event

from ._llm import LLMNoDisponibleError, call_claude

SYSTEM_PROMPT = (
    "Para cada hallazgo priorizado, redactas la corrección específica: qué cambiar,\n"
    "en qué archivo o configuración, con un ejemplo concreto cuando aplique. Escribes\n"
    "para alguien que puede no ser técnico — evita jerga sin explicarla la primera vez.\n"
    "Nunca prometes que el arreglo es 100% infalible; siempre recomienda re-escanear\n"
    "después de aplicar el cambio."
)

AGENTE = "remediacion"


def _describir_hallazgo(hallazgo: dict) -> str:
    raw = hallazgo.get("raw", {})
    objetivo = hallazgo.get("objetivo", "desconocido")
    urgencia = hallazgo.get("urgencia_negocio", "sin_evaluar")
    cves = hallazgo.get("cves_confirmados") or hallazgo.get("cves_detectados") or []
    return f"objetivo={objetivo} urgencia={urgencia} cves={cves} detalle_crudo={raw}"


def node(state: PipelineState) -> dict:
    priorizados = state.get("prioritized_findings") or []

    if not priorizados:
        return {
            "remediations": [],
            "trace_log": [
                make_trace_event(
                    agente=AGENTE,
                    accion="redactar_remediaciones",
                    resultado="sin hallazgos priorizados, nada que remediar todavía",
                )
            ],
        }

    remediaciones: list[dict] = []
    llm_ok = True
    for i, hallazgo in enumerate(priorizados):
        try:
            texto = call_claude(
                SYSTEM_PROMPT,
                f"Hallazgo #{i + 1} a remediar:\n{_describir_hallazgo(hallazgo)}",
            )
        except LLMNoDisponibleError as exc:
            llm_ok = False
            texto = (
                "[Remediación pendiente de redacción — LLM no disponible: "
                f"{exc}. Un analista humano debe completar esta sección antes "
                "de enviar el reporte al cliente.]"
            )
        remediaciones.append({"hallazgo_indice": i, "remediacion": texto})

    resultado = f"{len(remediaciones)} remediaciones redactadas"
    if not llm_ok:
        resultado += "; una o más quedaron pendientes por falta de LLM"

    return {
        "remediations": remediaciones,
        "trace_log": [
            make_trace_event(agente=AGENTE, accion="redactar_remediaciones", resultado=resultado)
        ],
    }
