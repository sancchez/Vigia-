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

Fase 4 — dos ejes, no uno
-------------------------
Hasta esta fase, el ÚNICO eje era `confirmado` (hay exploit público para el
CVE reportado). Medido contra Juice Shop real: 10 hallazgos verdaderos, 0
con CVE, y `agents/priorizacion.py` filtraba por `confirmado=True` -> el
cliente recibía un reporte con remediación vacía. Falso verde.

Ahora este nodo emite DOS ejes independientes, ambos deterministas:

    confirmado / exploit_publico  -> ¿hay PoC pública para un CVE real?
    estado_validez               -> ¿el hallazgo entra al flujo?

`confirmado` y `estado_verificacion` conservan EXACTAMENTE su semántica y
sus valores anteriores (hay tests de regresión que los fijan). Lo que se
agrega es el triage determinista de `tools/triage.py`: severidad nativa ya
normalizada, clase, evidencia concreta (URI/parámetro), y `estado_validez`,
que solo vale `falso_positivo_declarado` cuando la PROPIA herramienta lo
dice (`confidence: "0"` de ZAP). Sigue sin haber LLM acá: todo es un `if`
sobre datos que la herramienta ya produjo.
"""

from __future__ import annotations

import json
import re

from orchestrator.state import PipelineState, make_trace_event
from tools._shared import ToolExecutionError
from tools.exploit_intel import find_exploits_for_cve
from tools.triage import FALSO_POSITIVO_DECLARADO, triage_hallazgo

from .prompts import get_prompt

# Fase 3: fuente unica de prompts. El texto vive en
# `eval/prompt_versions/v1_verificacion.md` (ver agents/prompts.py). Este agente
# es determinista por diseño (no invoca LLM): el prompt documenta su contrato.
SYSTEM_PROMPT = get_prompt("verificacion")

AGENTE = "verificacion"

_CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)


def _extraer_cves(raw_finding: dict) -> list[str]:
    """Busca patrones CVE-YYYY-NNNN en todo el JSON del hallazgo crudo."""
    try:
        texto = json.dumps(raw_finding)
    except (TypeError, ValueError):
        texto = str(raw_finding)
    return sorted({m.upper() for m in _CVE_PATTERN.findall(texto)})


def _cruzar_exploitdb(hallazgo: dict) -> dict:
    """Eje 1 (sin cambios desde Fase 1): ¿hay PoC pública para su CVE?"""
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
        except ToolExecutionError as exc:
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


def _verificar_un_hallazgo(hallazgo: dict) -> dict:
    """Eje 1 (Exploit-DB) + eje 2 (triage determinista de `tools/triage.py`).

    El resultado de `_cruzar_exploitdb` se conserva TAL CUAL y el triage se
    fusiona encima solo con claves nuevas (`tipo`, `severidad`, `clase`,
    `endpoint`, `parametro`, `evidencia`, `estado_validez`, `urgencia_base`,
    ...). `confirmado` y `estado_verificacion` no se tocan: son el contrato
    que fijan los tests de regresión de fases anteriores.
    """
    verificado = _cruzar_exploitdb(hallazgo)
    triage = triage_hallazgo(hallazgo, exploit_publico=bool(verificado.get("confirmado")))
    return {**verificado, **triage}


def verificar_un_hallazgo(hallazgo: dict) -> dict:
    """Wrapper público de `_verificar_un_hallazgo` -- mismo contrato exacto,
    pensado para reutilizarse fuera del grafo LangGraph (ver
    `api/main.py::_correr_escaneo_activo_en_background`, que aplica esta
    misma verificación determinista sobre cada alerta de ZAP antes de
    persistirla en `findings`, cerrando la brecha de "todo se inserta con
    confirmado=0 fijo" -- ver openspec/changes/fase1-deteccion-visible).

    `node()` sigue llamando a `_verificar_un_hallazgo` directamente -- este
    wrapper no cambia el comportamiento del grafo (`POST /scan`) en nada.
    """
    return _verificar_un_hallazgo(hallazgo)


def node(state: PipelineState) -> dict:
    hallazgos_crudos = state.get("scan_findings") or []
    verificados = [_verificar_un_hallazgo(h) for h in hallazgos_crudos]

    con_exploit = sum(1 for v in verificados if v.get("confirmado"))
    descartados = sum(
        1 for v in verificados if v.get("estado_validez") == FALSO_POSITIVO_DECLARADO
    )
    validos = len(verificados) - descartados

    # El trace_log tiene que decir la verdad de lo que pasó. El texto anterior
    # ("0/10 confirmados via Exploit-DB") era técnicamente cierto y comunicaba
    # "no hay nada", teniendo 10 hallazgos reales encima. Ahora se reporta el
    # número que importa (cuántos siguen al resto del pipeline) y el exploit
    # público queda donde corresponde: como agravante, no como filtro.
    por_severidad: dict[str, int] = {}
    for v in verificados:
        if v.get("estado_validez") == FALSO_POSITIVO_DECLARADO:
            continue
        sev = str(v.get("severidad") or "desconocida")
        por_severidad[sev] = por_severidad.get(sev, 0) + 1
    desglose = ", ".join(f"{sev}={n}" for sev, n in sorted(por_severidad.items())) or "ninguno"

    return {
        "verified_findings": verificados,
        "trace_log": [
            make_trace_event(
                agente=AGENTE,
                accion=f"verificar_deterministico({len(hallazgos_crudos)} hallazgos crudos)",
                resultado=(
                    f"{validos}/{len(hallazgos_crudos)} hallazgos válidos pasan a priorización "
                    f"(severidad: {desglose}); {con_exploit} con exploit público conocido en "
                    f"Exploit-DB (agravante de urgencia, no requisito de entrada); "
                    f"{descartados} descartados por confidence=0 (falso positivo declarado por "
                    "la propia herramienta). Nada se descarta en silencio: todo queda en "
                    "verified_findings con su estado_validez."
                ),
            )
        ],
    }
