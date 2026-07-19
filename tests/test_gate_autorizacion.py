"""HIGHEST PRIORITY -- orchestrator/graph.py::gate_autorizacion.

Es el control de seguridad más importante de todo el producto (ver
docs/produccion-readiness.md, "Top 3 gaps de mayor prioridad", punto 2, y
HANDOFF.md, regla de oro de la sección 0 del plan maestro): si esta puerta
alguna vez deja pasar un escaneo activo sin `autorizacion_firmada=true`,
Vigia atacaría un objetivo sin permiso -- consecuencias legales/éticas
reales, no solo un bug de producto.

Dos niveles de prueba, deliberadamente distintos:

1. Unidad pura sobre `gate_autorizacion()` -- sin grafo, sin red, sin LLM.
   Confirma que bloquea específicamente cuando `autorizacion_firmada` es
   `False` (no solo cuando el campo está ausente, que es un caso distinto
   y potencialmente más fácil de dejar pasar sin querer), y que exige el
   booleano `True` exacto -- no cualquier valor "truthy" (1, "true", etc.),
   que es justo el tipo de bug que un serializador/deserializador
   descuidado podría introducir aguas arriba.

2. Integración sobre el `StateGraph` compilado real (`build_graph()`,
   el mismo que usa `api/main.py` en producción) -- confirma que la puerta
   de verdad desvía el flujo del grafo, y que `agents/escaneo.py::node`
   (el único punto que puede tocar `tools/scan.py`) JAMÁS se invoca cuando
   no hay autorización, aunque el resto del pipeline (orquestador, recon,
   verificación, priorización, remediación, reportería) sí corra de punta
   a punta.
"""

from __future__ import annotations

import pytest

import agents.escaneo as escaneo_module
import agents.recon as recon_module
from orchestrator.graph import build_graph, gate_autorizacion
from orchestrator.state import new_state
from tools._shared import ToolExecutionError

# ---------------------------------------------------------------------------
# 1. Unidad pura -- gate_autorizacion() como función Python plana.
# ---------------------------------------------------------------------------


def test_bloquea_con_autorizacion_firmada_false_explicito():
    """El caso central que pide docs/produccion-readiness.md: bloquear
    específicamente cuando el campo vale `False`, no solo cuando falta."""
    assert gate_autorizacion({"autorizacion_firmada": False}) == "bloqueo_autorizacion"


def test_bloquea_cuando_el_campo_esta_ausente():
    """Caso distinto del anterior (campo ausente, no `False` explícito) --
    ambos deben bloquear, pero por la misma razón real: `is True` nunca es
    cierto para `None`."""
    assert gate_autorizacion({}) == "bloqueo_autorizacion"


def test_permite_solo_con_true_booleano_exacto():
    assert gate_autorizacion({"autorizacion_firmada": True}) == "escaneo"


@pytest.mark.parametrize("valor_truthy_no_booleano", [1, "true", "True", "1", ["si"]])
def test_no_permite_valores_truthy_que_no_sean_el_booleano_true(valor_truthy_no_booleano):
    """Defensa contra un bug real posible: si algo aguas arriba serializa
    `autorizacion_firmada` como string/int en vez de bool (ej. un
    form-data mal parseado, o un cliente HTTP que manda "true" como texto),
    la puerta NO debe confundir "truthy" con "True firmado". El código usa
    `is True`, no `bool(...)` -- este test congela esa decisión.
    """
    assert (
        gate_autorizacion({"autorizacion_firmada": valor_truthy_no_booleano})
        == "bloqueo_autorizacion"
    )


# ---------------------------------------------------------------------------
# 2. Integración -- el grafo compilado real jamás llama a escaneo.node()
#    cuando la puerta está cerrada, aunque orquestador/recon sí corran.
# ---------------------------------------------------------------------------


@pytest.fixture()
def _recon_sin_binarios_reales(monkeypatch):
    """Evita que recon.node dispare consultas pasivas reales.

    Esta máquina de desarrollo SÍ tiene `subfinder`/`amass` instalados
    (confirmado con `where subfinder`/`where amass`) -- sin este mock, el
    test de todos modos pasaría, pero haría consultas de red reales cada
    vez que corre, lo cual es lento y no determinista. `LLMNoDisponibleError`
    para Claude ya está cubierto globalmente por el fixture autouse
    `_sin_llm_real_por_defecto` en conftest.py.
    """

    def _falla_rapido(*_args, **_kwargs):
        raise ToolExecutionError("mock de test: recon deshabilitado en este test")

    monkeypatch.setattr(recon_module, "run_subfinder", _falla_rapido)
    monkeypatch.setattr(recon_module, "run_amass_passive", _falla_rapido)


def test_grafo_real_bloquea_y_jamas_invoca_escaneo(_recon_sin_binarios_reales, monkeypatch):
    """La prueba de mayor "costo de no tenerla" de todo el proyecto (cita
    textual de docs/produccion-readiness.md): si `gate_autorizacion` alguna
    vez se rompe en un refactor futuro, este test debe ser el que lo
    atrape, no un cliente real descubriéndolo en producción.
    """
    llamado = {"escaneo": False}

    def _escaneo_espia(_state):
        llamado["escaneo"] = True
        raise AssertionError(
            "gate_autorizacion dejó pasar el flujo a escaneo.node() con "
            "autorizacion_firmada=False -- es el bug de mayor severidad "
            "posible en este proyecto (ver plan-proyecto-ciberseguridad.md, "
            "sección 0: acceso no autorizado es delito bajo la Ley 1273 de 2009)."
        )

    monkeypatch.setattr(escaneo_module, "node", _escaneo_espia)

    grafo = build_graph().compile()
    estado_inicial = new_state(target="ejemplo.test", autorizacion_firmada=False)
    estado_final = grafo.invoke(estado_inicial)

    assert llamado["escaneo"] is False
    assert estado_final["autorizacion_bloqueo_motivo"] is not None
    assert "autorizacion_firmada" in estado_final["autorizacion_bloqueo_motivo"]
    assert estado_final["scan_findings"] == []


def test_grafo_real_permite_escaneo_con_autorizacion_true(_recon_sin_binarios_reales, monkeypatch):
    """Contraprueba necesaria: confirmar que la puerta NO bloquea de más --
    un test que solo verificara el camino bloqueado podría pasar incluso si
    `gate_autorizacion` bloqueara siempre, sin importar el valor real."""
    llamado = {"escaneo": False}

    def _escaneo_espia(_state):
        llamado["escaneo"] = True
        return {"scan_findings": [], "trace_log": []}

    monkeypatch.setattr(escaneo_module, "node", _escaneo_espia)

    grafo = build_graph().compile()
    estado_inicial = new_state(target="ejemplo.test", autorizacion_firmada=True)
    estado_final = grafo.invoke(estado_inicial)

    assert llamado["escaneo"] is True
    assert estado_final["autorizacion_bloqueo_motivo"] is None
