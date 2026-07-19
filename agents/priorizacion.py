"""Agente de Priorización de Riesgo — sección 7 del plan.

Traduce severidad técnica (CVSS/severity de Nuclei) a impacto de negocio
usando Claude. Solo trabaja sobre hallazgos que el Agente de Verificación
ya marcó como `confirmado=True` — nunca prioriza algo no confirmado, para
no darle al cliente una falsa sensación de urgencia sobre un posible falso
positivo.
"""

from __future__ import annotations

import json

from orchestrator.state import PipelineState, make_trace_event

from ._llm import LLMNoDisponibleError, call_claude

# Open-core: el prompt real (que pondera impacto de negocio, no solo CVSS)
# vive en el paquete privado `vigia_core_private` (ver docs/open-core.md).
# Si no está instalado, se usa un prompt genérico que solo reordena por
# severidad técnica reportada — sigue siendo útil y funcional, solo menos
# sofisticado que la capa de priorización real de Vigia.
try:
    from vigia_core_private.priorizacion import SYSTEM_PROMPT

    _MODO_PRIORIZACION = "privado"
except ImportError:
    SYSTEM_PROMPT = (
        "Recibes hallazgos de seguridad ya verificados. Ordena los hallazgos "
        "de mayor a menor urgencia usando ÚNICAMENTE la severidad técnica "
        "reportada (CVSS/severity). Esta es la versión community: no "
        "incorpora contexto de negocio del cliente para ponderar impacto "
        "real (esa capa es parte del paquete privado de Vigia)."
    )
    _MODO_PRIORIZACION = "fallback_publico"

AGENTE = "priorizacion"

_INSTRUCCION_FORMATO = (
    "\n\nResponde ÚNICAMENTE con JSON válido (sin texto extra, sin markdown fences), "
    "una lista de objetos con esta forma exacta:\n"
    '[{"hallazgo_id": <indice entero del hallazgo en la lista de entrada>, '
    '"urgencia_negocio": "alta|media|baja", "justificacion": "..."}]'
)


def _parsear_json(texto: str) -> list[dict] | None:
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
    try:
        data = json.loads(texto)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass
    return None


def node(state: PipelineState) -> dict:
    confirmados = [h for h in (state.get("verified_findings") or []) if h.get("confirmado")]

    if not confirmados:
        return {
            "prioritized_findings": [],
            "trace_log": [
                make_trace_event(
                    agente=AGENTE,
                    accion="priorizar",
                    resultado="sin hallazgos confirmados que priorizar",
                )
            ],
        }

    contexto = state.get("contexto_negocio") or "(sin contexto de negocio adicional)"
    entrada = json.dumps(confirmados, ensure_ascii=False, indent=2, default=str)

    try:
        respuesta = call_claude(
            SYSTEM_PROMPT,
            f"Contexto de negocio del cliente:\n{contexto}\n\n"
            f"Hallazgos verificados (índice = posición en esta lista):\n{entrada}"
            f"{_INSTRUCCION_FORMATO}",
        )
    except LLMNoDisponibleError as exc:
        # Fallback determinista: sin LLM disponible, se conserva el orden de
        # llegada en vez de bloquear todo el pipeline.
        prioritized = [
            {**h, "urgencia_negocio": "sin_evaluar", "justificacion": str(exc)}
            for h in confirmados
        ]
        return {
            "prioritized_findings": prioritized,
            "trace_log": [
                make_trace_event(
                    agente=AGENTE,
                    accion="priorizar",
                    resultado=f"LLM no disponible, fallback sin reordenar: {exc}",
                )
            ],
        }

    orden = _parsear_json(respuesta)
    if orden is None:
        prioritized = [
            {**h, "urgencia_negocio": "sin_parsear", "justificacion": respuesta[:500]}
            for h in confirmados
        ]
        resultado = "respuesta del LLM no era JSON parseable; se conserva orden original"
    else:
        prioritized = []
        for item in orden:
            idx = item.get("hallazgo_id")
            if isinstance(idx, int) and 0 <= idx < len(confirmados):
                prioritized.append(
                    {
                        **confirmados[idx],
                        "urgencia_negocio": item.get("urgencia_negocio", "sin_evaluar"),
                        "justificacion": item.get("justificacion", ""),
                    }
                )
        resultado = f"{len(prioritized)} hallazgos ordenados por urgencia de negocio"

    return {
        "prioritized_findings": prioritized,
        "trace_log": [
            make_trace_event(agente=AGENTE, accion="priorizar", resultado=resultado)
        ],
    }
