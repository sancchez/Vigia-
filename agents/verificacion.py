"""Agente de Verificación — sección 7 y 8.1 del plan.

DETERMINISTA, SIN LLM. No importa `agents/_llm.py` a propósito: el plan es
explícito en que esta capa debe estar "separada del razonamiento de la
IA" (sección 3.1, fila "Verificación determinista") para evitar que un
falso positivo llegue al cliente solo porque un modelo de lenguaje
"sonó convincente". Toda decisión aquí es un `if` sobre datos, no una
inferencia.

Cruza cada hallazgo crudo del Agente de Escaneo contra el índice offline
de Exploit-DB (`tools/exploit_intel.py::find_exploits_for_cve`) — la
"segunda consulta controlada" del prompt de sección 7 es, en esta
implementación, la búsqueda determinista en ese índice local (no una
llamada de red ni un juicio de un modelo).
"""

from __future__ import annotations

import json
import re

from orchestrator.state import PipelineState, make_trace_event
from tools._shared import ToolNotInstalledError
from tools.exploit_intel import find_exploits_for_cve

SYSTEM_PROMPT = (
    "Recibes hallazgos crudos del Agente de Escaneo. Para cada uno: confirmas contra\n"
    "la base OSV/Exploit-DB si el CVE referenciado es real y vigente, y vuelves a\n"
    "intentar reproducir el hallazgo con una segunda consulta controlada. Solo\n"
    "los hallazgos que pasan esta doble verificación avanzan al siguiente agente.\n"
    "Todo lo demás se descarta o se marca como \"no confirmado\" — nunca se reporta al\n"
    "cliente como si fuera un hecho."
)

AGENTE = "verificacion"

_CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)


def _extraer_cves(raw_finding: dict) -> list[str]:
    """Busca patrones CVE-YYYY-NNNN en todo el JSON del hallazgo crudo."""
    try:
        texto = json.dumps(raw_finding)
    except (TypeError, ValueError):
        texto = str(raw_finding)
    return sorted({m.upper() for m in _CVE_PATTERN.findall(texto)})


def _verificar_un_hallazgo(hallazgo: dict) -> dict:
    raw = hallazgo.get("raw", hallazgo)
    cves = _extraer_cves(raw)

    if not cves:
        return {
            **hallazgo,
            "confirmado": False,
            "estado_verificacion": "no_verificable_sin_cve",
            "cves_detectados": [],
            "evidencia_exploitdb": [],
        }

    evidencia: list[dict] = []
    cves_confirmados: list[str] = []
    error_herramienta: str | None = None

    for cve in cves:
        try:
            registros = find_exploits_for_cve(cve)
        except ToolNotInstalledError as exc:
            error_herramienta = str(exc)
            break
        if registros:
            cves_confirmados.append(cve)
            evidencia.extend(
                {"cve": cve, "edb_id": r.edb_id, "descripcion": r.description}
                for r in registros
            )

    if error_herramienta:
        return {
            **hallazgo,
            "confirmado": False,
            "estado_verificacion": "no_confirmable_error_herramienta",
            "cves_detectados": cves,
            "evidencia_exploitdb": [],
            "error": error_herramienta,
        }

    if cves_confirmados:
        return {
            **hallazgo,
            "confirmado": True,
            "estado_verificacion": "confirmado_via_exploitdb",
            "cves_detectados": cves,
            "cves_confirmados": cves_confirmados,
            "evidencia_exploitdb": evidencia,
        }

    return {
        **hallazgo,
        "confirmado": False,
        "estado_verificacion": "no_confirmado_cve_sin_poc_publica",
        "cves_detectados": cves,
        "evidencia_exploitdb": [],
    }


def node(state: PipelineState) -> dict:
    hallazgos_crudos = state.get("scan_findings") or []
    verificados = [_verificar_un_hallazgo(h) for h in hallazgos_crudos]
    confirmados = sum(1 for v in verificados if v["confirmado"])

    return {
        "verified_findings": verificados,
        "trace_log": [
            make_trace_event(
                agente=AGENTE,
                accion=f"verificar_deterministico({len(hallazgos_crudos)} hallazgos crudos)",
                resultado=(
                    f"{confirmados}/{len(hallazgos_crudos)} confirmados via Exploit-DB; "
                    "resto marcado no_confirmado (no se descartan silenciosamente, "
                    "quedan en verified_findings con confirmado=False)"
                ),
            )
        ],
    }
