"""Fase 3 — checkpointer del grafo (`orchestrator/graph.py::compile_graph`).

Lo que se prueba de verdad acá NO es "el objeto SqliteSaver existe", sino la
propiedad que justifica el paso: **el estado de una corrida queda persistido y
se puede recuperar/reanudar por `thread_id`** (que en producción es el
`scan_id`, generado antes del `.invoke()` en `api/main.py` y `api/scheduler.py`).

También se protege la propiedad aditiva: si el checkpointer no se puede
inicializar (o se apaga con `VIGIA_CHECKPOINTER=off`), `compile_graph()` sigue
devolviendo un grafo usable, con el comportamiento exacto de antes de Fase 3.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from orchestrator import graph as graph_mod
from orchestrator.graph import compile_graph
from orchestrator.state import new_state


@pytest.fixture(autouse=True)
def _saver_cache_limpio():
    """El saver se cachea por `DATABASE_URL`; entre tests hay que limpiarlo
    para que cada uno arranque contra su propia base temporal."""
    graph_mod._saver_cache.clear()
    yield
    graph_mod._saver_cache.clear()


def _estado(target: str = "ejemplo-fase3.test"):
    # `autorizacion_firmada=False` a propósito: el grafo toma la rama de
    # bloqueo, que es determinista y no ejecuta ninguna herramienta real.
    return new_state(target=target, autorizacion_firmada=False)


def test_compile_graph_usa_checkpointer_por_defecto(test_db):
    grafo = compile_graph()
    assert grafo.checkpointer is not None


def test_kill_switch_de_entorno_vuelve_al_compile_desnudo(test_db, monkeypatch):
    monkeypatch.setenv("VIGIA_CHECKPOINTER", "off")
    graph_mod._saver_cache.clear()
    grafo = compile_graph()
    # Sin checkpointer, LangGraph deja el atributo en None/falsy y el grafo
    # se invoca sin `thread_id`, exactamente como antes de Fase 3.
    assert not grafo.checkpointer
    estado_final = grafo.invoke(_estado())
    assert estado_final["autorizacion_bloqueo_motivo"]


def test_una_corrida_queda_persistida_y_se_puede_recuperar_por_thread_id(test_db):
    """El caso de uso real: invocar con `thread_id=scan_id` y volver a leer
    ese estado después, desde el checkpointer, sin re-ejecutar nada."""
    grafo = compile_graph()
    scan_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": scan_id}}

    estado_final = grafo.invoke(_estado(), config=config)
    assert estado_final["reporte_final"]

    # Recuperación: un proceso/handler distinto que solo tiene el scan_id.
    snapshot = grafo.get_state(config)
    assert snapshot.values["target"] == "ejemplo-fase3.test"
    assert snapshot.values["reporte_final"] == estado_final["reporte_final"]
    assert snapshot.values["autorizacion_bloqueo_motivo"]
    # El trace_log (reducer `operator.add`) también viajó al checkpoint.
    assert len(snapshot.values["trace_log"]) >= 3


def test_los_checkpoints_intermedios_quedan_guardados(test_db):
    """No solo el estado final: el historial permite auditar/reanudar."""
    grafo = compile_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    grafo.invoke(_estado(), config=config)

    historial = list(grafo.get_state_history(config))
    assert len(historial) > 1, "el checkpointer no guardó supersteps intermedios"

    agentes_vistos = {
        evento.get("agente")
        for snapshot in historial
        for evento in (snapshot.values.get("trace_log") or [])
    }
    assert "orquestador" in agentes_vistos


def test_dos_scans_no_se_pisan_el_estado(test_db):
    """Aislamiento por hilo: dos scans concurrentes = dos `thread_id`."""
    grafo = compile_graph()
    cfg_a = {"configurable": {"thread_id": str(uuid.uuid4())}}
    cfg_b = {"configurable": {"thread_id": str(uuid.uuid4())}}

    grafo.invoke(_estado("cliente-a.test"), config=cfg_a)
    grafo.invoke(_estado("cliente-b.test"), config=cfg_b)

    assert grafo.get_state(cfg_a).values["target"] == "cliente-a.test"
    assert grafo.get_state(cfg_b).values["target"] == "cliente-b.test"


def test_el_checkpointer_escribe_en_la_misma_base_del_proyecto(test_db, monkeypatch):
    """Requisito explícito del paso 1: no una base paralela propia."""
    if not str(test_db).endswith(".db"):
        pytest.skip("smoke de rutas SQLite; con VIGIA_PG_TEST_URL no aplica")

    # Aplica el schema del proyecto (`db/schema.sql`) sobre el mismo archivo.
    from db.connection import get_conn

    get_conn().close()

    grafo = compile_graph()
    grafo.invoke(_estado(), config={"configurable": {"thread_id": str(uuid.uuid4())}})

    conn = sqlite3.connect(str(test_db))
    try:
        tablas = {
            fila[0]
            for fila in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    # Tablas de LangGraph y del proyecto conviviendo en el mismo archivo.
    assert "checkpoints" in tablas
    assert "findings" in tablas


def test_checkpointer_explicito_gana_sobre_el_default(test_db):
    """`compile_graph(checkpointer=...)` permite inyectar uno en memoria
    (útil para tests de terceros y para el futuro modo efímero)."""
    from langgraph.checkpoint.memory import InMemorySaver

    saver = InMemorySaver()
    grafo = compile_graph(checkpointer=saver)
    assert grafo.checkpointer is saver


def test_fallo_al_crear_el_saver_degrada_a_sin_checkpointer(test_db, monkeypatch, tmp_path):
    """La regla aditiva: el checkpointer nunca es un punto de falla nuevo.

    Una ruta dentro de un directorio inexistente hace que `sqlite3.connect`
    levante `OperationalError` — el módulo debe tragárselo y seguir.
    """
    ruta_imposible = tmp_path / "directorio" / "que" / "no" / "existe" / "x.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{ruta_imposible.as_posix()}")
    graph_mod._saver_cache.clear()
    assert graph_mod._default_saver() is None
    grafo = compile_graph()
    assert not grafo.checkpointer
    # Y el grafo sigue corriendo, sin `thread_id`, como antes de Fase 3.
    assert grafo.invoke(_estado())["reporte_final"]
