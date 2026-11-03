"""Fase 7 (openspec/changes/fase7-confianza-calidad) -- evals de calidad
del FALLBACK PÚBLICO de priorización/remediación/revisión de código IA.

Ver `eval/agent_quality/` para el harness reusable (checks + datasets +
fixtures) y `docs/open-core.md` para el split público/privado: este test
verifica el CONTRATO de forma/orden/no-alucinación del fallback, nunca el
contenido de los prompts reales de `vigia_core_private` -- eso es
explícitamente Fase 6 del `tasks.md` de este change, planificada pero NO
ejecutada acá (vive en el repo privado, commit aparte).

`agents._llm.call_claude` se mockea explícitamente en cada test con las
respuestas canned de `eval/agent_quality/fixture_respuesta_*.json` --
determinista, sin red, sin depender de la fixture `_sin_llm_real_por_defecto`
de `conftest.py` (que fuerza `LLMNoDisponibleError`; acá SÍ queremos
ejercitar la rama de "LLM respondió", no la de fallback sin LLM).
"""

from __future__ import annotations

import json
from pathlib import Path

import agents.priorizacion as priorizacion
import agents.remediacion as remediacion
import agents.revision_ia as revision_ia
from eval.agent_quality import checks

_AGENT_QUALITY_DIR = Path(__file__).resolve().parent.parent / "eval" / "agent_quality"


def _cargar(nombre: str) -> dict:
    return json.loads((_AGENT_QUALITY_DIR / nombre).read_text(encoding="utf-8"))


def test_modo_open_core_es_detectable():
    """El entorno debe saber SIEMPRE en qué modo está, sin importar cuál sea.

    No se asume que `vigia_core_private` esté ausente: un checkout de
    desarrollo que trabaja el repo privado lo tiene presente en la raíz (y
    `pythonpath = ["."]` de pytest lo resuelve como namespace package), y
    eso NO es un fallo -- es el modo "privado" legítimo. Mismo criterio que
    `test_cumplimiento_fallback.py::test_modo_es_privado_o_fallback`: se
    verifica que la detección funcione, no que dé un valor en particular.
    Los evals de fallback de abajo mockean la respuesta del LLM, así que
    son válidos en ambos modos."""
    assert isinstance(checks.es_entorno_fallback_publico(), bool)


def test_priorizacion_fallback_esquema_y_orden_por_severidad(monkeypatch):
    dataset = _cargar("dataset_priorizacion.json")
    fixture = _cargar("fixture_respuesta_priorizacion.json")
    state = {"verified_findings": dataset["verified_findings"]}

    monkeypatch.setattr(
        priorizacion, "call_claude", lambda *a, **k: json.dumps(fixture["respuesta_conforme"])
    )

    resultado = priorizacion.node(state)
    salida = resultado["prioritized_findings"]

    assert checks.validar_esquema_priorizacion(salida) == []
    assert checks.validar_orden_por_severidad(salida) == []


def test_check_de_orden_es_capaz_de_fallar():
    """El check en sí debe ser capaz de FALLAR ante una salida mal ordenada
    -- si esto no fallara nunca, el check anterior no estaría probando
    nada real.

    Fase 4: antes esta capacidad se ejercitaba pasándole al nodo una
    respuesta de LLM desordenada, pero `agents/priorizacion.py` ya no puede
    producir esa salida (ordena de forma determinista ANTES de llamar al
    modelo, ver `tools/triage.py::ordenar_por_riesgo`). Que el nodo ya no
    pueda desordenarse es la mejora, no la pérdida del test: el check se
    ejercita ahora contra una lista mal ordenada construida a mano, y el
    test de abajo cubre el caso real que reemplazó a este.
    """
    mal_ordenada = [
        {"severidad": "low", "urgencia_negocio": "baja", "justificacion": "x"},
        {"severidad": "critical", "urgencia_negocio": "alta", "justificacion": "x"},
    ]
    assert checks.validar_orden_por_severidad(mal_ordenada) != []


def test_priorizacion_ignora_el_orden_propuesto_por_el_llm(monkeypatch):
    """Un LLM que devuelve los hallazgos desordenados (el más leve primero)
    NO puede desordenar la salida: el orden lo fija `ordenar_por_riesgo`."""
    dataset = _cargar("dataset_priorizacion.json")
    fixture = _cargar("fixture_respuesta_priorizacion.json")
    state = {"verified_findings": dataset["verified_findings"]}

    monkeypatch.setattr(
        priorizacion, "call_claude", lambda *a, **k: json.dumps(fixture["respuesta_no_conforme"])
    )

    resultado = priorizacion.node(state)
    salida = resultado["prioritized_findings"]

    assert checks.validar_esquema_priorizacion(salida) == []
    assert checks.validar_orden_por_severidad(salida) == []
    assert [h["severidad"] for h in salida] == ["critical", "high", "medium", "low"]
    # La respuesta no conforme pedía urgencia "alta" para un hallazgo `low`:
    # se acota al techo determinista en vez de inflarse.
    por_severidad = {h["severidad"]: h["urgencia_negocio"] for h in salida}
    assert por_severidad["low"] == "baja"


def test_remediacion_fallback_esquema_y_sin_sobre_promesa(monkeypatch):
    dataset = _cargar("dataset_remediacion.json")
    fixture = _cargar("fixture_respuesta_remediacion.json")
    state = {"prioritized_findings": dataset["prioritized_findings"]}

    monkeypatch.setattr(remediacion, "call_claude", lambda *a, **k: fixture["respuesta_conforme"])

    resultado = remediacion.node(state)
    salida = resultado["remediations"]

    assert checks.validar_esquema_remediacion(salida) == []
    assert checks.validar_no_sobre_promesa(salida) == []


def test_remediacion_fallback_detecta_sobre_promesa(monkeypatch):
    dataset = _cargar("dataset_remediacion.json")
    fixture = _cargar("fixture_respuesta_remediacion.json")
    state = {"prioritized_findings": dataset["prioritized_findings"][:1]}

    monkeypatch.setattr(
        remediacion, "call_claude", lambda *a, **k: fixture["respuesta_no_conforme"]
    )

    resultado = remediacion.node(state)
    salida = resultado["remediations"]

    assert checks.validar_no_sobre_promesa(salida) != []


def test_revision_ia_fallback_esquema_y_sin_alucinacion_de_evidencia(monkeypatch):
    fixture = _cargar("fixture_respuesta_revision_ia.json")
    ruta_codigo = str(_AGENT_QUALITY_DIR / "fixtures" / "muestra_control_acceso.py")

    monkeypatch.setattr(
        revision_ia, "call_claude", lambda *a, **k: json.dumps(fixture["respuesta_conforme"])
    )

    resultado = revision_ia.revisar_codigo(
        codigo_paths=[ruta_codigo],
        autorizacion_firmada=True,
        envio_codigo_a_llm_autorizado=True,
    )
    hallazgos = resultado["hallazgos_logica"]
    archivos = {ruta_codigo: Path(ruta_codigo).read_text(encoding="utf-8")}

    assert checks.validar_esquema_revision_ia(hallazgos) == []
    assert checks.validar_no_alucinacion_evidencia(hallazgos, archivos) == []


def test_revision_ia_fallback_detecta_evidencia_alucinada(monkeypatch):
    fixture = _cargar("fixture_respuesta_revision_ia.json")
    ruta_codigo = str(_AGENT_QUALITY_DIR / "fixtures" / "muestra_control_acceso.py")

    monkeypatch.setattr(
        revision_ia, "call_claude", lambda *a, **k: json.dumps(fixture["respuesta_no_conforme"])
    )

    resultado = revision_ia.revisar_codigo(
        codigo_paths=[ruta_codigo],
        autorizacion_firmada=True,
        envio_codigo_a_llm_autorizado=True,
    )
    hallazgos = resultado["hallazgos_logica"]
    archivos = {ruta_codigo: Path(ruta_codigo).read_text(encoding="utf-8")}

    assert checks.validar_no_alucinacion_evidencia(hallazgos, archivos) != []


def test_evals_de_calidad_no_dependen_de_vigia_core_private():
    """Spec.md, escenario 'No asertar contenido privado': los evals públicos
    no deben referenciar el paquete privado en absoluto.

    Se verifica sobre el CÓDIGO FUENTE de `eval/agent_quality/`, no sobre si
    el paquete está instalado: en un checkout de desarrollo del repo privado
    la carpeta `vigia_core_private/` sí está presente (gitignored pero en
    disco), y eso no debe hacer fallar la suite pública -- lo que importa es
    que estos evals midan el contrato/fallback y nunca el contenido privado.
    Los evals de contenido real viven en `vigia_core_private/tests/`, ver
    `docs/open-core.md`."""
    import ast

    for archivo in _AGENT_QUALITY_DIR.rglob("*.py"):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.ImportFrom):
                modulos = [nodo.module or ""]
            elif isinstance(nodo, ast.Import):
                modulos = [alias.name for alias in nodo.names]
            else:
                continue
            for modulo in modulos:
                assert not modulo.startswith("vigia_core_private"), (
                    f"{archivo.name} importa {modulo} -- los evals de contenido "
                    "privado van en vigia_core_private/tests/, no acá"
                )
