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

import logging
import os
import sqlite3
from typing import Any, Optional

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


_logger = logging.getLogger("vigia.orchestrator")

# Un saver por `DATABASE_URL` resuelta. La clave es la URL (no un singleton
# global) a propósito: la suite de tests apunta `DATABASE_URL` a un SQLite
# temporal distinto por test, y un singleton dejaría el grafo compilado
# escribiendo checkpoints contra un archivo que ya no existe.
_saver_cache: dict[str, Any] = {}

_CHECKPOINTER_OFF = {"0", "off", "false", "no"}


def _checkpointer_habilitado() -> bool:
    """Kill-switch de entorno: `VIGIA_CHECKPOINTER=off` vuelve al `.compile()` desnudo."""
    return os.environ.get("VIGIA_CHECKPOINTER", "on").strip().lower() not in _CHECKPOINTER_OFF


def _default_saver() -> Optional[Any]:
    """Checkpointer por defecto, apuntando a la MISMA base que el resto del proyecto.

    Soporte dual, igual que `db/connection.py`:

    - `sqlite:///...`  -> `SqliteSaver` sobre el mismo archivo `.db`. LangGraph
      crea sus propias tablas (`checkpoints`, `checkpoint_writes`, ...), que no
      chocan con `db/schema.sql`.
    - `postgresql://`  -> `PostgresSaver`, SOLO si `langgraph-checkpoint-postgres`
      está instalado. No es una dependencia declarada del proyecto hoy, así que
      si falta se devuelve `None` y el grafo compila sin checkpointer — el
      comportamiento exacto de antes de esta fase, nunca un crash en arranque.

    Cualquier fallo (import, permisos, disco) degrada a `None` con un warning:
    el checkpointer es una mejora de resiliencia, jamás un punto de falla nuevo
    para el pipeline.
    """
    if not _checkpointer_habilitado():
        return None

    url = os.environ.get("DATABASE_URL", "sqlite:///./ciberseguridad.db")
    if url in _saver_cache:
        return _saver_cache[url]

    saver: Optional[Any] = None
    try:
        if url.startswith("postgresql://") or url.startswith("postgres://"):
            try:
                from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
            except ImportError:
                _logger.warning(
                    "DATABASE_URL es Postgres pero langgraph-checkpoint-postgres no está "
                    "instalado — el grafo corre SIN checkpointer (comportamiento previo)."
                )
            else:
                import psycopg
                from psycopg.rows import dict_row

                conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
                saver = PostgresSaver(conn)
                saver.setup()
        else:
            from langgraph.checkpoint.sqlite import SqliteSaver

            path = _sqlite_path_from_url(url)
            # `check_same_thread=False`: FastAPI corre los endpoints sync en un
            # threadpool y APScheduler en su propio thread; el saver es un
            # objeto de proceso, compartido entre ambos.
            conn = sqlite3.connect(path, check_same_thread=False)
            saver = SqliteSaver(conn)
            saver.setup()
    except Exception as exc:  # pragma: no cover - degradación defensiva
        _logger.warning("No se pudo inicializar el checkpointer (%s); se sigue sin él.", exc)
        saver = None

    _saver_cache[url] = saver
    return saver


def _sqlite_path_from_url(url: str) -> str:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"DATABASE_URL no reconocida para el checkpointer: {url!r}")
    return url[len(prefix):]


def compile_graph(checkpointer: Optional[Any] = None) -> CompiledStateGraph:
    """Compila el grafo para ejecución (`.invoke(...)` / `.stream(...)`).

    Con checkpointer, cada `.invoke()` DEBE recibir
    `config={"configurable": {"thread_id": scan_id}}` — el `scan_id` ya se
    genera antes de invocar el grafo tanto en `api/main.py` como en
    `api/scheduler.py`, así que es el identificador natural del hilo.
    """
    return build_graph().compile(checkpointer=checkpointer or _default_saver())
