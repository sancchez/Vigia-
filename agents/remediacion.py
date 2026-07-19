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

# Open-core: el prompt real vive en `vigia_core_private` (ver
# docs/open-core.md). Fallback genérico si el paquete no está instalado.
try:
    from vigia_core_private.remediacion import SYSTEM_PROMPT
except ImportError:
    SYSTEM_PROMPT = (
        "Para cada hallazgo, redacta una recomendación de remediación "
        "genérica basada en buenas prácticas estándar de seguridad para ese "
        "tipo de vulnerabilidad. Escribe para alguien que puede no ser "
        "técnico. Nunca prometas que el arreglo es 100% infalible; siempre "
        "recomienda re-escanear después de aplicar el cambio. Esta es la "
        "versión community: no incluye ejemplos de código detallados ni "
        "adaptación al contexto específico del cliente (esa capa es parte "
        "del paquete privado de Vigia)."
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
