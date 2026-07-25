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


def test_entorno_es_fallback_publico_en_este_checkout():
    """Precondición del resto de la suite: `vigia_core_private` NO debe estar
    instalado en el entorno que corre la suite pública -- si lo estuviera,
    estos evals de fallback dejarían de tener sentido tal cual están
    escritos (ver `tests/test_cumplimiento_fallback.py` para el mismo
    patrón de precondición sobre `agents.cumplimiento`)."""
    assert checks.es_entorno_fallback_publico()


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


def test_priorizacion_fallback_detecta_orden_incorrecto(monkeypatch):
    """El check en sí debe ser capaz de FALLAR ante una salida mal ordenada
    -- si esto no fallara nunca, el check anterior no estaría probando
    nada real."""
    dataset = _cargar("dataset_priorizacion.json")
    fixture = _cargar("fixture_respuesta_priorizacion.json")
    state = {"verified_findings": dataset["verified_findings"]}

    monkeypatch.setattr(
        priorizacion, "call_claude", lambda *a, **k: json.dumps(fixture["respuesta_no_conforme"])
    )

    resultado = priorizacion.node(state)
    salida = resultado["prioritized_findings"]

    assert checks.validar_esquema_priorizacion(salida) == []
    assert checks.validar_orden_por_severidad(salida) != []


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


def test_evals_de_calidad_no_dependen_de_vigia_core_private_instalado():
    """Spec.md, escenario 'No asertar contenido privado': la suite pública
    debe pasar sin `vigia_core_private` instalado -- lo confirmamos
    directamente en vez de solo asumirlo (mismo espíritu que
    `test_fallback_publico_no_expone_taxonomia_privada_si_paquete_ausente`
    de `test_cumplimiento_fallback.py`)."""
    import importlib.util

    assert importlib.util.find_spec("vigia_core_private") is None
