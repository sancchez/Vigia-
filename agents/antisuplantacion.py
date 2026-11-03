"""Agente Anti-Suplantación (opcional) — sección 7 del plan.

Rama opcional del grafo (activada por `state["antisuplantacion_habilitado"]`,
ver `orchestrator/graph.py`). Primero recolecta señales deterministas con
`tools/antisuplantacion.py` (dnstwist + Sherlock), luego le pide a Claude
que evalúe qué tan probable es que cada señal sea suplantación real (vs.
coincidencia) y redacte un borrador de takedown, exactamente como pide el
system prompt de la sección 7.
"""

from __future__ import annotations

import json

from orchestrator.state import PipelineState, make_trace_event
from tools._shared import ToolExecutionError
from tools.antisuplantacion import run_dnstwist, run_sherlock, triar_lookalikes

from ._llm import LLMNoDisponibleError, call_claude

from .prompts import get_prompt

# Fase 3: fuente unica de prompts. El texto vive en
# `eval/prompt_versions/v1_anti_suplantacion.md` (ver agents/prompts.py).
SYSTEM_PROMPT = get_prompt("antisuplantacion")

AGENTE = "antisuplantacion"


def _username_desde_target(target: str) -> str:
    """Deriva un nombre de marca/usuario razonable a partir del dominio objetivo."""
    base = target.split("//")[-1].split("/")[0]
    return base.split(".")[0] if base else target


def node(state: PipelineState) -> dict:
    target = state.get("target", "")
    señales: list[dict] = []
    errores: list[str] = []

    try:
        dnstwist_result = run_dnstwist(target, registered_only=True)
        # Triaje ANTES de emitir señales: separa los registros DEFENSIVOS del
        # propio dueño de la marca (comparten nameserver con el dominio
        # legítimo) de las amenazas de terceros, y marca los urgentes (tercero
        # con MX real -> puede enviar phishing). Sin esto el informe reportaría
        # los registros defensivos del cliente como amenazas -- falso positivo
        # comercial grave, medido contra un banco real el 2026-07-25 (10 de 45
        # eran del propio banco). Ver `tools.antisuplantacion.triar_lookalikes`.
        triaje = triar_lookalikes(dnstwist_result)
        defensivos = {v.domain for v in triaje.propios}
        urgentes = {v.domain for v in triaje.con_correo}
        for variante in dnstwist_result.findings:
            señales.append(
                {
                    "tipo": "dominio_variante",
                    "fuzzer": variante.fuzzer,
                    "dominio": variante.domain,
                    "dns_a": variante.dns_a,
                    # Nameservers: dato que el triaje aguas abajo necesita para
                    # auditar la atribución de propiedad (antes se descartaba).
                    "dns_ns": (variante.raw or {}).get("dns_ns", []),
                    "registrado": variante.registered,
                    "es_defensivo": variante.domain in defensivos,
                    "tiene_correo": variante.domain in urgentes,
                }
            )
    except ToolExecutionError as exc:
        errores.append(f"dnstwist: {exc}")

    username = _username_desde_target(target)
    try:
        sherlock_result = run_sherlock(username)
        # A diferencia de dominios (triaje por nameserver compartido, ver
        # arriba), acá NO se marca ningún perfil como "oficial" ni como
        # "amenaza": medido (2026-07-26) que Sherlock solo confirma existencia
        # + URL, sin bio ni enlace al dominio del cliente -- no hay señal para
        # atribuir. `_extraer_findings_antisuplantacion` persiste todos estos
        # hallazgos como severidad `info` para revisión humana, no como
        # amenaza confirmada. Ver `tools.antisuplantacion.run_sherlock`.
        for url in sherlock_result.findings:
            señales.append({"tipo": "perfil_red_social", "username": username, "url": url})
    except ToolExecutionError as exc:
        errores.append(f"sherlock: {exc}")

    if not señales:
        return {
            "antisuplantacion_findings": [],
            "trace_log": [
                make_trace_event(
                    agente=AGENTE,
                    accion=f"vigilar_suplantacion(target={target!r})",
                    resultado=(
                        "sin señales crudas que analizar"
                        + (f"; errores: {'; '.join(errores)}" if errores else "")
                    ),
                )
            ],
        }

    try:
        analisis = call_claude(
            SYSTEM_PROMPT,
            f"Marca/dominio del cliente: {target}\n\n"
            "Señales crudas detectadas (dnstwist + Sherlock):\n"
            + json.dumps(señales, ensure_ascii=False, indent=2, default=str),
        )
        hallazgo = {"señales_crudas": señales, "analisis_claude": analisis}
        resultado = f"{len(señales)} señales analizadas por Claude"
    except LLMNoDisponibleError as exc:
        hallazgo = {
            "señales_crudas": señales,
            "analisis_claude": None,
            "error": f"LLM no disponible, señales crudas sin analizar: {exc}",
        }
        resultado = f"{len(señales)} señales crudas recolectadas, análisis pendiente (LLM no disponible)"

    if errores:
        resultado += f"; herramientas no disponibles: {'; '.join(errores)}"

    return {
        "antisuplantacion_findings": [hallazgo],
        "trace_log": [
            make_trace_event(
                agente=AGENTE,
                accion=f"vigilar_suplantacion(target={target!r})",
                resultado=resultado,
            )
        ],
    }
