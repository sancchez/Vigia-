"""Fase 3 — `tools/registry.py`: schema válido + frontera pasivo/activo.

Dos cosas distintas se prueban acá:

1. **Contrato de LangChain**: cada tool del registro tiene nombre, descripción
   y un `args_schema` introspectable, que es lo único que un futuro
   `llm.bind_tools(...)` necesita para funcionar. Si un decorador se rompe o
   alguien borra un docstring, se ve acá.

2. **La regla de seguridad no negociable**: ninguna herramienta de escaneo
   ACTIVO (Nuclei, ZAP, Semgrep, Trivy, Grype) puede quedar expuesta como
   `@tool` invocable por un LLM. Esto no es un test de estilo — es la
   verificación de que la decisión de atacar infraestructura sigue viviendo en
   `orchestrator/graph.py::gate_autorizacion` (un `if` de Python) y no en un
   prompt. Se comprueba sobre el CÓDIGO FUENTE del módulo, no solo sobre los
   objetos importados, para que ni siquiera un import "por si acaso" pase.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from langchain_core.tools import BaseTool

from tools import registry

_FUENTE_REGISTRY = Path(inspect.getfile(registry)).read_text(encoding="utf-8")


def test_el_registro_no_esta_vacio():
    assert len(registry.get_tools_pasivas()) >= 4


def test_get_tools_pasivas_devuelve_una_copia():
    """Un caller que haga `.append(...)` sobre el resultado no puede envenenar
    el registro global (que es la superficie de tools de todo el sistema)."""
    copia = registry.get_tools_pasivas()
    copia.append("no soy una tool")
    assert len(registry.get_tools_pasivas()) == len(registry.TOOLS_PASIVAS)


@pytest.mark.parametrize("herramienta", registry.TOOLS_PASIVAS, ids=lambda t: t.name)
def test_cada_tool_tiene_schema_valido(herramienta):
    """Introspección estándar de LangChain: nombre, descripción, args."""
    assert isinstance(herramienta, BaseTool)

    assert herramienta.name and " " not in herramienta.name
    # La descripción es lo ÚNICO que un LLM ve para decidir si llamarla; una
    # de dos líneas sueltas no alcanza para elegir bien.
    assert herramienta.description and len(herramienta.description) > 60

    esquema = herramienta.args_schema.model_json_schema()
    assert esquema["type"] == "object"
    assert esquema["properties"], f"{herramienta.name} no declara ningún argumento"
    for nombre_arg, definicion in esquema["properties"].items():
        assert definicion.get("description"), (
            f"el argumento '{nombre_arg}' de {herramienta.name} no está documentado; "
            "un LLM no puede rellenarlo a ciegas"
        )


def test_nombres_de_tools_son_unicos():
    nombres = [t.name for t in registry.TOOLS_PASIVAS]
    assert len(nombres) == len(set(nombres))


# --- Frontera de seguridad ---------------------------------------------------


@pytest.mark.parametrize("prohibida", registry.HERRAMIENTAS_ACTIVAS_PROHIBIDAS)
def test_ninguna_tool_de_escaneo_activo_esta_expuesta(prohibida):
    """Nuclei/ZAP/Semgrep/Trivy/Grype NUNCA como `@tool` de un LLM."""
    for herramienta in registry.TOOLS_PASIVAS:
        assert prohibida not in herramienta.name.lower(), (
            f"la tool '{herramienta.name}' expone escaneo activo a un LLM"
        )


@pytest.mark.parametrize("modulo", registry.MODULOS_ESCANEO_ACTIVO_PROHIBIDOS)
def test_el_registry_no_importa_modulos_de_escaneo_activo(modulo):
    """Ni siquiera importados: si `tools/scan.py` no está en el módulo, es
    imposible que una tool futura lo envuelva por descuido."""
    hoja = modulo.split(".")[-1]
    for linea in _FUENTE_REGISTRY.splitlines():
        limpia = linea.strip()
        if not (limpia.startswith("import ") or limpia.startswith("from ")):
            continue
        assert hoja not in limpia, (
            f"tools/registry.py importa {modulo!r} en: {linea!r}. "
            "Ver la REGLA DE SEGURIDAD NO NEGOCIABLE del docstring del módulo."
        )


def test_el_gate_de_autorizacion_no_depende_del_registry_ni_de_un_llm():
    """La pieza no negociable de la sección 8.1 sigue siendo Python plano.

    `gate_autorizacion` debe poder decidir con un `dict` en la mano, sin
    ninguna tool, sin ningún modelo, sin red. Si alguien intentara moverlo a
    un LLM, este test lo detiene.
    """
    from orchestrator import graph

    fuente_gate = inspect.getsource(graph.gate_autorizacion)
    assert "call_claude" not in fuente_gate
    assert "tool" not in fuente_gate.lower().replace("autorizacion", "")

    fuente_modulo = inspect.getsource(graph)
    assert "tools.registry" not in fuente_modulo
    assert "bind_tools" not in fuente_modulo

    # Y funciona de verdad, sin nada más que el estado.
    assert graph.gate_autorizacion({"autorizacion_firmada": True}) == "escaneo"
    assert graph.gate_autorizacion({"autorizacion_firmada": False}) == "bloqueo_autorizacion"
    assert graph.gate_autorizacion({}) == "bloqueo_autorizacion"
    assert graph.gate_autorizacion({"autorizacion_firmada": "true"}) == "bloqueo_autorizacion"
