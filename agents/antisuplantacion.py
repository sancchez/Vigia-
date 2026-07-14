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
from tools._shared import ToolNotInstalledError
from tools.antisuplantacion import run_dnstwist, run_sherlock

from ._llm import LLMNoDisponibleError, call_claude

SYSTEM_PROMPT = (
    "Buscas señales de que la marca/dominio del cliente está siendo suplantada: dominios\n"
    "similares recién registrados (dnstwist + CertStream), perfiles en redes sociales\n"
    "usando el mismo nombre o logo (Sherlock), URLs ya reportadas como maliciosas\n"
    "(Safe Browsing). Para cada hallazgo, evalúas qué tan probable es que sea\n"
    "suplantación real vs. coincidencia (empresa legítima con nombre parecido) y\n"
    "explicas tu razonamiento. Adjuntas un borrador de solicitud de eliminación\n"
    "(takedown) listo para enviar a la plataforma correspondiente."
)

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
        for variante in dnstwist_result.findings:
            señales.append(
                {
                    "tipo": "dominio_variante",
                    "fuzzer": variante.fuzzer,
                    "dominio": variante.domain,
                    "dns_a": variante.dns_a,
                    "registrado": variante.registered,
                }
            )
    except ToolNotInstalledError as exc:
        errores.append(f"dnstwist: {exc}")

    username = _username_desde_target(target)
    try:
        sherlock_result = run_sherlock(username)
        for url in sherlock_result.findings:
            señales.append({"tipo": "perfil_red_social", "username": username, "url": url})
    except ToolNotInstalledError as exc:
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
