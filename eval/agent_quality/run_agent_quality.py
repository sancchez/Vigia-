#!/usr/bin/env python3
"""
eval/agent_quality/run_agent_quality.py
========================================

Fase 7 (openspec/changes/fase7-confianza-calidad): script standalone
(mismo patrón que `eval/run_eval.py` y `eval/regression_runner.py`) que
mide la CALIDAD de los agentes de razonamiento (priorización, remediación,
revisión de código IA) -- a diferencia de `run_eval.py`, que mide
precisión/recall de DETECCIÓN de hallazgos.

Alcance: corre ÚNICAMENTE contra el modo `fallback_publico` de esos
agentes, con `agents._llm.call_claude` MOCKEADO con respuestas canned
(`fixture_respuesta_*.json`) -- determinista, sin red, sin costo. Nunca
invoca un LLM real ni asegura contenido de `vigia_core_private` (ver
`eval/agent_quality/__init__.py`).

Un modo LLM-as-judge real (contra la API real de Claude) queda fuera de
esta suite por defecto -- ver design.md, "Runner de regresión separado de
pytest, judge opcional": este script es la parte determinista; un
juez LLM real, si se agrega en el futuro, debe vivir aislado tras su
propio flag, nunca acá.

Uso
---
    python eval/agent_quality/run_agent_quality.py
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager

_AQUI = os.path.dirname(os.path.abspath(__file__))
_RAIZ = os.path.dirname(os.path.dirname(_AQUI))  # .../eval/agent_quality -> raíz del repo
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from eval.agent_quality import checks  # noqa: E402


def _cargar_json(nombre: str) -> dict:
    with open(os.path.join(_AQUI, nombre), "r", encoding="utf-8") as f:
        return json.load(f)


@contextmanager
def _mock_call_claude(modulo, respuesta_o_callable):
    """Reemplaza temporalmente `call_claude` importado dentro de `modulo`
    (los agentes hacen `from ._llm import call_claude`, así que el nombre a
    parchear vive en el propio módulo del agente, no en `agents._llm`)."""
    original = modulo.call_claude
    if callable(respuesta_o_callable) and not isinstance(respuesta_o_callable, str):
        modulo.call_claude = respuesta_o_callable
    else:
        modulo.call_claude = lambda *a, **k: respuesta_o_callable
    try:
        yield
    finally:
        modulo.call_claude = original


def eval_priorizacion() -> list[str]:
    import agents.priorizacion as priorizacion

    dataset = _cargar_json("dataset_priorizacion.json")
    fixture = _cargar_json("fixture_respuesta_priorizacion.json")
    state = {"verified_findings": dataset["verified_findings"]}

    with _mock_call_claude(priorizacion, json.dumps(fixture["respuesta_conforme"])):
        resultado = priorizacion.node(state)

    salida = resultado["prioritized_findings"]
    errores = checks.validar_esquema_priorizacion(salida)
    errores += checks.validar_orden_por_severidad(salida)
    return errores


def eval_remediacion() -> list[str]:
    import agents.remediacion as remediacion

    dataset = _cargar_json("dataset_remediacion.json")
    fixture = _cargar_json("fixture_respuesta_remediacion.json")
    state = {"prioritized_findings": dataset["prioritized_findings"]}

    with _mock_call_claude(remediacion, fixture["respuesta_conforme"]):
        resultado = remediacion.node(state)

    salida = resultado["remediations"]
    errores = checks.validar_esquema_remediacion(salida)
    errores += checks.validar_no_sobre_promesa(salida)
    return errores


def eval_revision_ia() -> list[str]:
    import agents.revision_ia as revision_ia

    fixture = _cargar_json("fixture_respuesta_revision_ia.json")
    ruta_codigo = os.path.join(_AQUI, "fixtures", "muestra_control_acceso.py")

    with _mock_call_claude(revision_ia, json.dumps(fixture["respuesta_conforme"])):
        resultado = revision_ia.revisar_codigo(
            codigo_paths=[ruta_codigo],
            autorizacion_firmada=True,
            envio_codigo_a_llm_autorizado=True,
        )

    hallazgos = resultado["hallazgos_logica"]
    archivos = {ruta_codigo: open(ruta_codigo, "r", encoding="utf-8").read()}
    errores = checks.validar_esquema_revision_ia(hallazgos)
    errores += checks.validar_no_alucinacion_evidencia(hallazgos, archivos)
    return errores


def main() -> int:
    if not checks.es_entorno_fallback_publico():
        print(
            "ADVERTENCIA: vigia_core_private está instalado en esta máquina -- "
            "este eval está diseñado para el modo fallback_publico. Continuando "
            "de todas formas (los checks son sobre la FORMA de la salida, no "
            "sobre qué SYSTEM_PROMPT esté activo).",
            file=sys.stderr,
        )

    resultados = {
        "priorizacion": eval_priorizacion(),
        "remediacion": eval_remediacion(),
        "revision_ia": eval_revision_ia(),
    }

    line = "=" * 78
    print(line)
    print("EVAL DE CALIDAD DE AGENTES (fallback público) — eval/agent_quality/run_agent_quality.py")
    print(line)

    hubo_error = False
    for agente, errores in resultados.items():
        estado = "OK" if not errores else "FALLÓ"
        print(f"\n[{agente}] {estado}")
        for e in errores:
            hubo_error = True
            print(f"  - {e}")

    print()
    print(line)
    return 1 if hubo_error else 0


if __name__ == "__main__":
    sys.exit(main())
