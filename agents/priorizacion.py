"""Agente de Priorización de Riesgo — sección 7 del plan.

Traduce severidad técnica (riskcode de ZAP / severity de Nuclei) a impacto
de negocio usando Claude.

Fase 4 — de qué hallazgos parte (cambio medido, no cosmético)
-------------------------------------------------------------
ANTES: `[h for h in verified_findings if h.get("confirmado")]`. Es decir, la
existencia de un exploit público para un CVE era el FILTRO BINARIO de
entrada. Contra Juice Shop real eso dio 0 de 10: cabeceras de seguridad
ausentes, CORS abierto y cookies sin flags no tienen CVE por naturaleza y
nunca van a "confirmarse", así que el cliente recibía remediación vacía
teniendo 10 hallazgos reales. Falso verde.

AHORA: parte de TODOS los hallazgos válidos (`tools/triage.py::es_valido`),
y el exploit público pasó a ser lo que siempre debió ser — un AGRAVANTE de
urgencia (`urgencia_base`), no la puerta de entrada. Lo único que se
descarta es lo que la propia herramienta declaró falso positivo
(`confidence: "0"` de ZAP), que nunca se presenta como hallazgo activo.

Anti-inflado (regla dura)
-------------------------
`urgencia_base` es un TECHO determinista derivado de la severidad nativa.
El LLM puede BAJAR la urgencia con contexto de negocio ("ese sistema no
está expuesto a internet"), nunca subirla. Sin esa regla, la forma más
fácil de que el producto "muestre algo" sería que el modelo llame crítico a
un aviso informativo — exactamente el falso positivo que el cliente pidió
evitar con el mismo énfasis que el falso negativo.
"""

from __future__ import annotations

import json

from orchestrator.state import PipelineState, make_trace_event
from tools.triage import acotar_urgencia, es_valido, ordenar_por_riesgo, urgencia_base

from ._llm import LLMNoDisponibleError, call_claude
from .prompts import get_prompt

# Open-core: el prompt real (que pondera impacto de negocio, no solo CVSS)
# vive en el paquete privado `vigia_core_private` (ver docs/open-core.md).
# Si no está instalado, se usa un prompt genérico que solo reordena por
# severidad técnica reportada — sigue siendo útil y funcional, solo menos
# sofisticado que la capa de priorización real de Vigia.
#
# Fase 3: el override privado NO cambió; lo que cambió es que el texto del
# fallback público dejó de ser un literal suelto y ahora sale de
# `eval/prompt_versions/v2_priorizacion.md` (ver agents/prompts.py).
try:
    from vigia_core_private.priorizacion import SYSTEM_PROMPT

    _MODO_PRIORIZACION = "privado"
except ImportError:
    SYSTEM_PROMPT = get_prompt("priorizacion")
    _MODO_PRIORIZACION = "fallback_publico"

AGENTE = "priorizacion"

_INSTRUCCION_FORMATO = (
    "\n\nCada hallazgo trae `urgencia_base`: el TECHO de urgencia ya calculado de "
    "forma determinista a partir de la severidad que reportó la herramienta. "
    "Podés BAJAR la urgencia si el contexto de negocio lo justifica; NUNCA "
    "subirla (si lo hacés, se ignora y se usa `urgencia_base`)."
    "\n\nResponde ÚNICAMENTE con JSON válido (sin texto extra, sin markdown fences), "
    "una lista de objetos con esta forma exacta:\n"
    '[{"hallazgo_id": <indice entero del hallazgo en la lista de entrada>, '
    '"urgencia_negocio": "alta|media|baja|informativa", "justificacion": "..."}]'
)


def _techo_urgencia(hallazgo: dict) -> str:
    """Techo de urgencia del hallazgo.

    Normalmente lo trae ya calculado `tools/triage.py` (vía
    `agents/verificacion.py`). Se re-deriva de la severidad cuando no está —
    hallazgos construidos a mano (datasets de eval, tests, integraciones que
    no pasan por el grafo) no deben quedar todos acotados a "media" por no
    traer el campo.
    """
    techo = hallazgo.get("urgencia_base")
    if isinstance(techo, str) and techo.strip():
        return techo.strip().lower()
    return urgencia_base(
        hallazgo.get("severidad", ""), bool(hallazgo.get("exploit_publico"))
    )


def _justificacion_determinista(hallazgo: dict) -> str:
    """Texto de urgencia sin LLM: dice de dónde sale el número, no lo adorna."""
    severidad = hallazgo.get("severidad", "desconocida")
    herramienta = hallazgo.get("herramienta", "la herramienta de escaneo")
    partes = [
        f"Urgencia derivada de forma determinista de la severidad {severidad} "
        f"reportada por {herramienta}"
    ]
    if hallazgo.get("exploit_publico"):
        cves = hallazgo.get("cves_confirmados") or hallazgo.get("cves_detectados") or []
        partes.append(
            "agravada porque existe exploit público conocido en Exploit-DB"
            + (f" para {', '.join(cves)}" if cves else "")
        )
    else:
        partes.append(
            "sin exploit público conocido asociado (no es un atenuante: el hallazgo "
            "lo detectó la herramienta con evidencia propia)"
        )
    return "; ".join(partes) + "."


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
    verificados = state.get("verified_findings") or []
    descartados = len(verificados) - sum(1 for h in verificados if es_valido(h))
    # Orden determinista ANTES del LLM: la salida respeta severidad aunque el
    # modelo devuelva la lista en cualquier orden (o no esté disponible).
    confirmados = ordenar_por_riesgo([h for h in verificados if es_valido(h)])
    con_exploit = sum(1 for h in confirmados if h.get("exploit_publico"))

    if not confirmados:
        detalle = (
            f"sin hallazgos válidos que priorizar ({descartados} descartados por "
            "falso positivo declarado por la herramienta)"
            if descartados
            else "sin hallazgos que priorizar: el escaneo no devolvió ninguno"
        )
        return {
            "prioritized_findings": [],
            "trace_log": [
                make_trace_event(agente=AGENTE, accion="priorizar", resultado=detalle)
            ],
        }

    contexto = state.get("contexto_negocio") or "(sin contexto de negocio adicional)"
    entrada = json.dumps(confirmados, ensure_ascii=False, indent=2, default=str)

    # Fase 3 (memoria entre corridas): si hay historial, un hallazgo reincidente
    # o ya descartado por el cliente debe pesar distinto que uno nuevo. Bloque
    # aditivo: sin historial, el prompt es byte por byte el de antes.
    historico = (state.get("contexto_historico") or "").strip()
    bloque_historico = f"{historico}\n\n" if historico else ""

    try:
        respuesta = call_claude(
            SYSTEM_PROMPT,
            f"{bloque_historico}"
            f"Contexto de negocio del cliente:\n{contexto}\n\n"
            f"Hallazgos verificados (índice = posición en esta lista):\n{entrada}"
            f"{_INSTRUCCION_FORMATO}",
        )
    except LLMNoDisponibleError as exc:
        # Fallback determinista: sin LLM, la urgencia sale de `urgencia_base`
        # (severidad nativa + agravante de exploit público) en vez de quedar
        # en "sin_evaluar", que dejaba el reporte sin nada accionable.
        prioritized = [
            {
                **h,
                "urgencia_negocio": _techo_urgencia(h),
                "justificacion": _justificacion_determinista(h),
            }
            for h in confirmados
        ]
        return {
            "prioritized_findings": prioritized,
            "trace_log": [
                make_trace_event(
                    agente=AGENTE,
                    accion="priorizar",
                    resultado=(
                        f"{len(prioritized)} hallazgos priorizados de forma determinista "
                        f"por severidad ({con_exploit} con exploit público); "
                        f"sin contexto de negocio porque el LLM no está disponible: {exc}"
                    ),
                )
            ],
        }

    orden = _parsear_json(respuesta)
    if orden is None:
        # Mismo criterio que el fallback sin LLM: una respuesta ilegible no
        # puede dejar al cliente sin urgencia -- se cae a la determinista.
        prioritized = [
            {
                **h,
                "urgencia_negocio": _techo_urgencia(h),
                "justificacion": _justificacion_determinista(h),
            }
            for h in confirmados
        ]
        resultado = (
            f"respuesta del LLM no era JSON parseable; {len(prioritized)} hallazgos "
            "priorizados de forma determinista por severidad"
        )
    else:
        anotaciones = {
            item.get("hallazgo_id"): item
            for item in orden
            if isinstance(item.get("hallazgo_id"), int)
        }
        acotados = 0
        prioritized = []
        # Se itera sobre `confirmados`, NO sobre la respuesta del modelo: si el
        # LLM omite un hallazgo (o alucina un índice), el hallazgo igual llega
        # al reporte con su urgencia determinista. Antes, un hallazgo omitido
        # por el modelo desaparecía del pipeline sin dejar rastro.
        for idx, hallazgo in enumerate(confirmados):
            item = anotaciones.get(idx) or {}
            techo = _techo_urgencia(hallazgo)
            propuesta = item.get("urgencia_negocio")
            urgencia = acotar_urgencia(propuesta, techo)
            if propuesta and urgencia != str(propuesta).strip().lower():
                acotados += 1
            justificacion = str(item.get("justificacion") or "").strip()
            prioritized.append(
                {
                    **hallazgo,
                    "urgencia_negocio": urgencia,
                    "justificacion": justificacion or _justificacion_determinista(hallazgo),
                }
            )
        resultado = (
            f"{len(prioritized)} hallazgos priorizados ({con_exploit} con exploit "
            f"público conocido, {descartados} descartados antes por falso positivo "
            "declarado por la herramienta)"
        )
        if acotados:
            resultado += (
                f"; {acotados} urgencia(s) propuestas por el LLM se acotaron al techo "
                "determinista de severidad (no se infla nada hacia arriba)"
            )

    return {
        "prioritized_findings": prioritized,
        "trace_log": [
            make_trace_event(agente=AGENTE, accion="priorizar", resultado=resultado)
        ],
    }
