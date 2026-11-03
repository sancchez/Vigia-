"""Agente de Reportería — sección 7 del plan.

Nodo de convergencia (join) del grafo: llega tanto desde `remediacion`
(camino con autorización) como desde el nodo determinista
`bloqueo_autorizacion` (camino sin autorización, definido en
`orchestrator/graph.py`) y, si está habilitada, desde `antisuplantacion`.

El aviso de "falta autorización firmada" NO depende de que Claude decida
mencionarlo: se inyecta de forma determinista al inicio del reporte si
`autorizacion_bloqueo_motivo` viene poblado, antes de siquiera llamar al
LLM. Así el mensaje crítico nunca puede perderse por un resumen del modelo.
"""

from __future__ import annotations

import json

from orchestrator.state import PipelineState, make_trace_event
from tools.reporte_determinista import construir_reporte_determinista

from ._llm import LLMNoDisponibleError, call_claude

# Open-core: el prompt real (plantilla editorial de Vigia) vive en
# `vigia_core_private` (ver docs/open-core.md). Fallback genérico si el
# paquete no está instalado.
# Fase 3: el override privado NO cambió; el texto del fallback público ahora
# sale de `eval/prompt_versions/v2_reporteria.md` (ver agents/prompts.py).
try:
    from vigia_core_private.reporteria import SYSTEM_PROMPT
except ImportError:
    from .prompts import get_prompt

    SYSTEM_PROMPT = get_prompt("reporteria")

AGENTE = "reporteria"


def _aviso_bloqueo(motivo: str) -> str:
    return (
        "# AVISO — Falta autorización firmada\n\n"
        "El pipeline NO ejecutó la fase de Escaneo Activo. Motivo registrado por el "
        "nodo determinista de autorización: "
        f"{motivo!r}. No se corrió ningún wrapper de `tools/scan.py` contra el "
        "objetivo. Este reporte solo incluye lo recolectado por el Agente de Recon "
        "(pasivo). Para completar la evaluación, el cliente debe firmar el documento "
        "de autorización de pruebas de seguridad (`legal/autorizacion-pruebas-seguridad.md`).\n\n"
    )


def node(state: PipelineState) -> dict:
    partes: list[str] = []
    motivo = state.get("autorizacion_bloqueo_motivo")
    if motivo:
        partes.append(_aviso_bloqueo(motivo))

    resumen_datos = {
        "target": state.get("target"),
        "recon_findings": state.get("recon_findings") or [],
        "verified_findings": state.get("verified_findings") or [],
        "prioritized_findings": state.get("prioritized_findings") or [],
        "remediations": state.get("remediations") or [],
        "antisuplantacion_findings": state.get("antisuplantacion_findings") or [],
    }

    # Fase 3 (memoria entre corridas): permite que el reporte diga "esto se
    # viene reportando desde hace N scans y sigue sin cerrarse" en vez de
    # presentar todo como novedad. Aditivo: sin historial el prompt no cambia.
    historico = (state.get("contexto_historico") or "").strip()
    bloque_historico = (
        f"{historico}\n\nUsa ese historial para distinguir lo NUEVO de lo "
        "REINCIDENTE en el reporte; si un hallazgo lleva varios scans abierto, "
        "dilo explícitamente.\n\n"
        if historico
        else ""
    )

    try:
        cuerpo = call_claude(
            SYSTEM_PROMPT,
            bloque_historico
            + "Datos del pipeline para compilar el reporte final (JSON):\n"
            + json.dumps(resumen_datos, ensure_ascii=False, indent=2, default=str),
        )
        fuente = "llm"
    except LLMNoDisponibleError:
        # Fase 4: antes esto era un placeholder ("[Reporte narrativo
        # pendiente...]") seguido del JSON crudo del estado -- honesto, pero
        # inentregable a un cliente. Ahora se compila un documento real con los
        # mismos datos, sin LLM (ver tools/reporte_determinista.py). El propio
        # documento declara que se generó sin análisis de modelo.
        cuerpo = construir_reporte_determinista(resumen_datos)
        fuente = "determinista_sin_llm"

    partes.append(cuerpo)
    reporte_final = "\n".join(partes)

    validos = sum(
        1
        for h in (state.get("verified_findings") or [])
        if h.get("estado_validez") != "falso_positivo_declarado"
    )
    if motivo:
        resultado = "reporte generado con aviso de bloqueo de autorización"
    else:
        resultado = (
            f"reporte generado con flujo completo: {validos} hallazgos válidos, "
            f"{len(state.get('prioritized_findings') or [])} priorizados, "
            f"{len(state.get('remediations') or [])} con remediación"
        )
    if fuente == "determinista_sin_llm":
        resultado += "; compilado de forma determinista (LLM no disponible)"

    return {
        "reporte_final": reporte_final,
        "trace_log": [
            make_trace_event(
                agente=AGENTE,
                accion="compilar_reporte_final",
                resultado=resultado,
            )
        ],
    }
