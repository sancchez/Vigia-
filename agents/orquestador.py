"""Agente Orquestador — sección 7 del plan.

IMPORTANTE (sección 8.1): este nodo es *informativo/narrativo* únicamente.
Su llamada a Claude sirve para dejar en el log de trazabilidad una
explicación en lenguaje natural de qué va a pasar y por qué — nunca decide
el enrutamiento real del grafo. El enrutamiento real (si se avanza a
Escaneo Activo o no) vive en `orchestrator/graph.py::gate_autorizacion()`,
una función Python determinista. Esto es intencional: "la seguridad del
sistema no depende de que el LLM se acuerde de la regla" (sección 8.1).
"""

from __future__ import annotations

from orchestrator.state import PipelineState, make_trace_event

from ._llm import LLMNoDisponibleError, call_claude

SYSTEM_PROMPT = (
    "Eres el orquestador de un pipeline de evaluación de seguridad. Tu única función es\n"
    "decidir el siguiente paso del flujo según el estado actual (qué se ha descubierto,\n"
    "qué falta, si hay autorización firmada para el objetivo).\n"
    "Nunca ejecutas escaneos tú mismo. Nunca avanzas a la fase de Escaneo Activo si el\n"
    "campo `autorizacion_firmada` del estado no es `true`. Si no existe autorización,\n"
    "detén el flujo y reporta que falta el documento firmado."
)

AGENTE = "orquestador"


def _resumen_estado(state: PipelineState) -> str:
    return (
        f"target: {state.get('target')}\n"
        f"autorizacion_firmada: {state.get('autorizacion_firmada')}\n"
        f"scope: {state.get('scope')}\n"
        f"antisuplantacion_habilitado: {state.get('antisuplantacion_habilitado')}\n"
        f"recon_findings ya recolectados: {len(state.get('recon_findings') or [])}\n"
        f"scan_findings ya recolectados: {len(state.get('scan_findings') or [])}\n"
    )


def node(state: PipelineState) -> dict:
    """Nodo de entrada del grafo. No modifica campos de control de flujo."""
    try:
        decision = call_claude(
            SYSTEM_PROMPT,
            "Estado actual del pipeline:\n"
            f"{_resumen_estado(state)}\n"
            "Describe en 2-3 líneas el siguiente paso que corresponde y por qué, "
            "recordando que la verificación real de autorizacion_firmada la hace "
            "código determinista, no tú.",
        )
        resultado = f"decision_narrativa={decision.strip()[:300]!r}"
    except LLMNoDisponibleError as exc:
        resultado = f"LLM no disponible, se continúa con enrutamiento determinista igual: {exc}"

    return {
        "trace_log": [
            make_trace_event(
                agente=AGENTE,
                accion="iniciar_pipeline_y_narrar_decision",
                resultado=resultado,
            )
        ]
    }
