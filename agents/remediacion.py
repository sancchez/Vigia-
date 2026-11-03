"""Agente de Remediación — sección 7 del plan.

Para cada hallazgo priorizado, le pide a Claude una corrección concreta y
redactada para un lector no técnico.

Fase 4 — el fallback ya no es un placeholder
--------------------------------------------
ANTES, sin `ANTHROPIC_API_KEY` cada remediación quedaba en
"[Remediación pendiente de redacción — LLM no disponible: ...]". Honesto,
pero inentregable: el cliente recibía un placeholder por hallazgo. Y el
arreglo de un CSP ausente o de un `Access-Control-Allow-Origin: *` no
requiere un modelo de lenguaje para ser correcto y útil — es conocido y
documentado.

Ahora el fallback usa `tools/remediacion_determinista.py`: la solución que
documenta la propia herramienta (`solution` de ZAP / `info.remediation` de
Nuclei) más un catálogo curado por familia de hallazgo. Cuando no hay ni una
ni otra, el texto lo DICE y deriva a revisión humana (revisión humana
obligatoria en los primeros clientes — sección 8.2), en vez de rellenar con
generalidades que suenan a algo.
"""

from __future__ import annotations

from orchestrator.state import PipelineState, make_trace_event
from tools.remediacion_determinista import remediacion_determinista

from ._llm import LLMNoDisponibleError, call_claude

# Open-core: el prompt real vive en `vigia_core_private` (ver
# docs/open-core.md). Fallback genérico si el paquete no está instalado.
# Fase 3: el override privado NO cambió; el texto del fallback público ahora
# sale de `eval/prompt_versions/v2_remediacion.md` (ver agents/prompts.py).
try:
    from vigia_core_private.remediacion import SYSTEM_PROMPT
except ImportError:
    from .prompts import get_prompt

    SYSTEM_PROMPT = get_prompt("remediacion")

AGENTE = "remediacion"


def _describir_hallazgo(hallazgo: dict) -> str:
    raw = hallazgo.get("raw", {})
    objetivo = hallazgo.get("objetivo", "desconocido")
    urgencia = hallazgo.get("urgencia_negocio", "sin_evaluar")
    cves = hallazgo.get("cves_confirmados") or hallazgo.get("cves_detectados") or []
    # Fase 4: `tipo`/`severidad`/`endpoint`/`evidencia` los agrega
    # `tools/triage.py`. Sin ellos, el prompt le pasaba a Claude el dict crudo
    # entero y nada más — el modelo tenía que adivinar de qué hallazgo se
    # trataba en vez de recibirlo nombrado.
    tipo = hallazgo.get("tipo", "desconocido")
    severidad = hallazgo.get("severidad", "desconocida")
    endpoint = hallazgo.get("endpoint", "")
    evidencia = hallazgo.get("evidencia", "")
    return (
        f"tipo={tipo} severidad={severidad} urgencia={urgencia} objetivo={objetivo} "
        f"endpoint={endpoint} evidencia={evidencia} cves={cves} detalle_crudo={raw}"
    )


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
    con_llm = 0
    deterministas = 0
    for i, hallazgo in enumerate(priorizados):
        try:
            texto = call_claude(
                SYSTEM_PROMPT,
                f"Hallazgo #{i + 1} a remediar:\n{_describir_hallazgo(hallazgo)}",
            )
            fuente = "llm"
            con_llm += 1
        except LLMNoDisponibleError:
            texto = remediacion_determinista(hallazgo)
            fuente = "catalogo_determinista"
            deterministas += 1
        remediaciones.append(
            {
                "hallazgo_indice": i,
                "remediacion": texto,
                # Trazabilidad (sección 8.1): el reporte declara cómo se
                # produjo cada remediación en vez de presentarlas todas igual.
                "fuente": fuente,
                "tipo": hallazgo.get("tipo", ""),
                "severidad": hallazgo.get("severidad", ""),
                "urgencia_negocio": hallazgo.get("urgencia_negocio", ""),
            }
        )

    resultado = f"{len(remediaciones)} remediaciones redactadas"
    if deterministas:
        resultado += (
            f" ({con_llm} con LLM, {deterministas} deterministas desde la solución "
            "documentada por la herramienta + catálogo curado, sin LLM disponible)"
        )

    return {
        "remediations": remediaciones,
        "trace_log": [
            make_trace_event(agente=AGENTE, accion="redactar_remediaciones", resultado=resultado)
        ],
    }
