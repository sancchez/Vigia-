"""Ensamblaje del StateGraph — sección 4 (arquitectura) y 8.1 (harness) del plan.

Flujo:

    orquestador -> recon -> [gate_autorizacion] -> escaneo -> verificacion
        -> priorizacion -> remediacion -----------\
                                                     -> [gate_antisuplantacion] -> reporteria -> END
    orquestador -> recon -> [gate_autorizacion] -> bloqueo_autorizacion ----/

Decisión de diseño (documentada a propósito): el diagrama de la sección 4
dibuja Recon/Escaneo/Verificación/Priorización/Anti-Suplantación como
ramas paralelas de un mismo nivel. En LangGraph, converger ramas que
completan en supersteps distintos (por ejemplo, una rama de 1 nodo y otra
de 5 nodos) puede hacer que el nodo de convergencia se dispare más de una
vez — no es un límite conceptual del diagrama, es un detalle de ejecución
del motor. Para eliminar esa ambigüedad, Anti-Suplantación se modela como
una rama *condicional* (se ejecuta o se saltea con `gate_antisuplantacion`,
igual que Escaneo/Bloqueo) en vez de una rama concurrente independiente:
así solo un camino está activo en cada punto de convergencia, sin
condiciones de carrera ni doble ejecución de Reportería. Sigue siendo
"opcional" tal como pide el plan — simplemente se resuelve de forma
secuencial y determinista en vez de con paralelismo real a nivel de grafo.

La pieza NO NEGOCIABLE de este módulo es `gate_autorizacion`: una función
Python plana (un `if`) que decide si el grafo puede alcanzar `escaneo`.
Nunca es una instrucción de prompt — es la garantía de la sección 8.1 de
que "la seguridad del sistema no depende de que el LLM se acuerde de la
regla". Si `autorizacion_firmada` no es `True`, el grafo va a
`bloqueo_autorizacion` y esa rama JAMÁS toca `tools/scan.py`.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agents import (
    antisuplantacion,
    escaneo,
    orquestador,
    priorizacion,
    recon,
    remediacion,
    reporteria,
    verificacion,
)
from orchestrator.state import PipelineState, make_trace_event

AGENTE_GATE = "gate_autorizacion"
AGENTE_GATE_ANTISUPLANTACION = "gate_antisuplantacion"


def bloqueo_autorizacion_node(state: PipelineState) -> dict:
    """Nodo determinista alcanzado SOLO si no hay autorización firmada.

    No importa `tools/scan.py` ni ningún wrapper de escaneo activo — es
    imposible que esta función dispare un escaneo, porque no tiene el
    código para hacerlo. Su única responsabilidad es dejar constancia
    explícita (estado + trace_log) de por qué la rama de Escaneo Activo
    nunca se ejecutó, para que Reportería lo refleje sin depender de que
    un LLM decida mencionarlo.
    """
    motivo = (
        "autorizacion_firmada no es True — Escaneo Activo bloqueado antes de "
        "invocar cualquier wrapper de tools/scan.py"
    )
    return {
        "autorizacion_bloqueo_motivo": motivo,
        "trace_log": [
            make_trace_event(
                agente=AGENTE_GATE,
                accion="bloquear_escaneo_activo",
                resultado=motivo,
            )
        ],
    }


def antisuplantacion_skip_node(state: PipelineState) -> dict:
    """Nodo determinista para cuando la rama opcional Anti-Suplantación está apagada."""
    return {
        "trace_log": [
            make_trace_event(
                agente=AGENTE_GATE_ANTISUPLANTACION,
                accion="rama_opcional_deshabilitada",
                resultado="antisuplantacion_habilitado no es True, no se ejecutó ninguna herramienta",
            )
        ]
    }


def gate_autorizacion(state: PipelineState) -> str:
    """Condición de edge determinista — sección 8.1, la pieza no negociable.

    Código Python plano sobre el estado. Ningún LLM interviene. Es la
    única fuente de verdad sobre si se puede avanzar a Escaneo Activo.
    """
    if state.get("autorizacion_firmada") is True:
        return "escaneo"
    return "bloqueo_autorizacion"


def gate_antisuplantacion(state: PipelineState) -> str:
    """Condición de edge determinista para la rama opcional Anti-Suplantación."""
    if state.get("antisuplantacion_habilitado") is True:
        return "antisuplantacion"
    return "antisuplantacion_skip"


def build_graph() -> StateGraph:
    """Arma (sin compilar) el StateGraph completo (sección 4 del plan)."""
    graph = StateGraph(PipelineState)

    graph.add_node("orquestador", orquestador.node)
    graph.add_node("recon", recon.node)
    graph.add_node("escaneo", escaneo.node)
    graph.add_node("bloqueo_autorizacion", bloqueo_autorizacion_node)
    graph.add_node("verificacion", verificacion.node)
    graph.add_node("priorizacion", priorizacion.node)
    graph.add_node("remediacion", remediacion.node)
    graph.add_node("antisuplantacion", antisuplantacion.node)
    graph.add_node("antisuplantacion_skip", antisuplantacion_skip_node)
    graph.add_node("reporteria", reporteria.node)

    graph.add_edge(START, "orquestador")
    graph.add_edge("orquestador", "recon")

    # --- Puerta de autorización determinista (sección 8.1, NO NEGOCIABLE) ---
    graph.add_conditional_edges(
        "recon",
        gate_autorizacion,
        {"escaneo": "escaneo", "bloqueo_autorizacion": "bloqueo_autorizacion"},
    )

    graph.add_edge("escaneo", "verificacion")
    graph.add_edge("verificacion", "priorizacion")
    graph.add_edge("priorizacion", "remediacion")

    # Ambos caminos (autorizado / bloqueado) convergen en el gate opcional
    # de Anti-Suplantación antes de Reportería. Nunca hay dos ramas activas
    # a la vez llegando al mismo nodo (ver nota de diseño arriba).
    graph.add_conditional_edges(
        "remediacion",
        gate_antisuplantacion,
        {"antisuplantacion": "antisuplantacion", "antisuplantacion_skip": "antisuplantacion_skip"},
    )
    graph.add_conditional_edges(
        "bloqueo_autorizacion",
        gate_antisuplantacion,
        {"antisuplantacion": "antisuplantacion", "antisuplantacion_skip": "antisuplantacion_skip"},
    )

    graph.add_edge("antisuplantacion", "reporteria")
    graph.add_edge("antisuplantacion_skip", "reporteria")

    graph.add_edge("reporteria", END)

    return graph


def compile_graph() -> CompiledStateGraph:
    """Compila el grafo para ejecución (`.invoke(...)` / `.stream(...)`)."""
    return build_graph().compile()
