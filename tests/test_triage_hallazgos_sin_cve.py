"""Fase 4 — el falso verde medido contra Juice Shop no puede volver.

Corrida real que originó estos tests (ZAP contra OWASP Juice Shop, target de
laboratorio con autorización firmada, 10 hallazgos verdaderos):

    escaneo      -> "10 hallazgos crudos sobre 1 objetivo(s)"
    verificacion -> "0/10 confirmados via Exploit-DB"
    priorizacion -> "sin hallazgos confirmados que priorizar"
    remediacion  -> "sin hallazgos priorizados, nada que remediar todavia"

Los 10 eran reales; el pipeline los tiró porque `agents/priorizacion.py`
filtraba por `confirmado=True` y ninguna cabecera de seguridad ausente tiene
CVE. Cada test de acá fija una pata de ese arreglo.

`conftest.py::_sin_llm_real_por_defecto` fuerza `LLMNoDisponibleError`, así
que estos tests ejercitan además el camino determinista completo (sin LLM),
que es exactamente el que tiene que quedar entregable.
"""

from __future__ import annotations

import agents.priorizacion as priorizacion
import agents.remediacion as remediacion
import agents.reporteria as reporteria
import agents.verificacion as verificacion
from tools.triage import FALSO_POSITIVO_DECLARADO, VALIDO, acotar_urgencia, urgencia_base


def _alerta_zap(
    nombre: str,
    riskcode: str,
    confidence: str = "2",
    uri: str = "http://localhost:3000/",
    evidence: str = "",
    solution: str = "",
    count: str = "1",
) -> dict:
    """Alerta con la forma REAL del reporte de `zap-baseline.py`
    (`site[].alerts[]`), que es la que llega por `POST /scan`."""
    return {
        "objetivo": "localhost:3000",
        "herramienta": "zap-baseline",
        "raw": {
            "pluginid": "10038",
            "name": nombre,
            "riskcode": riskcode,
            "confidence": confidence,
            "count": count,
            "solution": solution,
            "instances": [{"uri": uri, "method": "GET", "param": "", "evidence": evidence}],
        },
    }


CSP = _alerta_zap(
    "Content Security Policy (CSP) Header Not Set",
    riskcode="2",
    confidence="3",
    count="5",
    solution="<p>Ensure that your web server sets the Content-Security-Policy header.</p>",
)
CORS = _alerta_zap(
    "Cross-Domain Misconfiguration",
    riskcode="2",
    confidence="2",
    uri="http://localhost:3000/rest/products",
    evidence="Access-Control-Allow-Origin: *",
)
INFORMATIVO = _alerta_zap("Modern Web Application", riskcode="0", confidence="2")
FALSO_POSITIVO = _alerta_zap("Alerta Que ZAP Mismo Descarta", riskcode="3", confidence="0")


def _correr_pipeline(crudos: list[dict]) -> dict:
    """Encadena verificación -> priorización -> remediación -> reportería.

    No usa el grafo de LangGraph a propósito: acá se está midiendo el
    contrato de los nodos, no el ruteo (que ya cubren los tests del gate).
    """
    state: dict = {"target": "localhost:3000", "scan_findings": crudos, "trace_log": []}
    for nodo in (verificacion.node, priorizacion.node, remediacion.node, reporteria.node):
        salida = nodo(state)
        trace = state["trace_log"] + (salida.pop("trace_log", []) or [])
        state.update(salida)
        state["trace_log"] = trace
    return state


def _trace(state: dict, agente: str) -> str:
    return next(e["resultado"] for e in state["trace_log"] if e["agente"] == agente)


# ---------------------------------------------------------------------------
# (a) Un hallazgo de ZAP SIN CVE llega a priorización y al reporte final
# ---------------------------------------------------------------------------

def test_hallazgo_sin_cve_llega_a_priorizacion_remediacion_y_reporte():
    """EL test de esta fase: el escenario exacto que producía el falso verde."""
    state = _correr_pipeline([CSP, CORS])

    verificados = state["verified_findings"]
    assert [v["confirmado"] for v in verificados] == [False, False], (
        "sin CVE no puede haber confirmación por Exploit-DB -- eso NO cambió"
    )
    assert [v["estado_validez"] for v in verificados] == [VALIDO, VALIDO]

    priorizados = state["prioritized_findings"]
    assert len(priorizados) == 2, "ANTES: 0 -- los hallazgos morían en verificación"
    nombres = {h["tipo"] for h in priorizados}
    assert nombres == {
        "Content Security Policy (CSP) Header Not Set",
        "Cross-Domain Misconfiguration",
    }

    assert len(state["remediations"]) == 2
    reporte = state["reporte_final"]
    for nombre in nombres:
        assert nombre in reporte
    # Evidencia concreta, no solo el nombre del hallazgo.
    assert "Access-Control-Allow-Origin: *" in reporte
    assert "http://localhost:3000/rest/products" in reporte


def test_el_trace_log_ya_no_comunica_que_no_hay_nada():
    """El trace decía "0/10 confirmados" y "sin hallazgos confirmados que
    priorizar": técnicamente cierto, pero comunicaba "no hay nada" teniendo
    10 hallazgos reales encima."""
    state = _correr_pipeline([CSP, CORS, INFORMATIVO, FALSO_POSITIVO])

    verif = _trace(state, "verificacion")
    assert "3/4 hallazgos válidos" in verif
    assert "0 con exploit público" in verif
    assert "1 descartados por confidence=0" in verif

    prior = _trace(state, "priorizacion")
    assert "sin hallazgos confirmados que priorizar" not in prior
    assert "3 hallazgos priorizados" in prior


def test_exploit_publico_agrava_la_urgencia_pero_no_es_el_filtro():
    """La existencia de PoC pública SUBE la prioridad; su ausencia no excluye."""
    assert urgencia_base("medium", exploit_publico=False) == "media"
    assert urgencia_base("medium", exploit_publico=True) == "alta"
    assert urgencia_base("low", exploit_publico=True) == "media"
    # Informativo NO escala: subirlo sería inflar para "mostrar algo".
    assert urgencia_base("info", exploit_publico=True) == "informativa"


# ---------------------------------------------------------------------------
# (b) confidence: "0" (falso positivo declarado por la herramienta)
# ---------------------------------------------------------------------------

def test_confidence_cero_no_se_presenta_como_hallazgo_activo():
    state = _correr_pipeline([CSP, FALSO_POSITIVO])

    descartado = next(
        v for v in state["verified_findings"] if v["tipo"] == "Alerta Que ZAP Mismo Descarta"
    )
    assert descartado["estado_validez"] == FALSO_POSITIVO_DECLARADO
    # Sigue en verified_findings (auditable), pero no avanza.
    assert all(h["tipo"] != "Alerta Que ZAP Mismo Descarta" for h in state["prioritized_findings"])
    assert all(
        r.get("tipo") != "Alerta Que ZAP Mismo Descarta" for r in state["remediations"]
    )

    reporte = state["reporte_final"]
    assert "falso positivo" in reporte.lower(), (
        "no se esconde: se declara por qué se descartó"
    )
    # No aparece bajo ninguna sección de riesgo -- solo en la de descartados.
    seccion_descartados = reporte.split("## Alertas descartadas")[1]
    assert "Alerta Que ZAP Mismo Descarta" in seccion_descartados
    assert "Alerta Que ZAP Mismo Descarta" not in reporte.split("## Alertas descartadas")[0]


def test_solo_zap_declara_falsos_positivos():
    """Ninguna otra herramienta tiene el concepto -- descartar por heurística
    propia sería inventarse un criterio."""
    nuclei = {
        "objetivo": "localhost:3000",
        "herramienta": "nuclei",
        "raw": {
            "template-id": "tech-detect",
            "info": {"name": "Tecnología detectada", "severity": "info"},
            "matched-at": "http://localhost:3000/",
            "confidence": "0",
        },
    }
    resultado = verificacion.verificar_un_hallazgo(nuclei)
    assert resultado["estado_validez"] == VALIDO


# ---------------------------------------------------------------------------
# (c) Un hallazgo informativo no se presenta con severidad/urgencia alta
# ---------------------------------------------------------------------------

def test_hallazgo_informativo_no_se_infla():
    state = _correr_pipeline([INFORMATIVO, CSP])

    informativo = next(
        h for h in state["prioritized_findings"] if h["tipo"] == "Modern Web Application"
    )
    assert informativo["severidad"] == "info", "riskcode 0 de ZAP es info, no se eleva"
    assert informativo["urgencia_negocio"] == "informativa"

    reporte = state["reporte_final"]
    # Llega al reporte, pero clasificado.
    assert "Modern Web Application" in reporte
    assert "### Informativos" in reporte
    assert "Modern Web Application" not in reporte.split("### Informativos")[0].split(
        "## Hallazgos"
    )[-1]


def test_el_llm_no_puede_subir_la_urgencia_por_encima_del_techo(monkeypatch):
    """Anti-inflado: la severidad la deciden los datos de la herramienta, no
    el modelo. Puede bajar la urgencia con contexto de negocio, nunca subirla."""
    import json as _json

    monkeypatch.setattr(
        priorizacion,
        "call_claude",
        lambda *a, **k: _json.dumps(
            # Los índices son la posición en la lista YA ordenada por riesgo
            # que el nodo le pasa al modelo: 0 = CSP (medium), 1 = informativo.
            [
                {"hallazgo_id": 1, "urgencia_negocio": "alta", "justificacion": "inflado"},
                {"hallazgo_id": 0, "urgencia_negocio": "baja", "justificacion": "atenuado"},
            ]
        ),
    )

    verificados = [verificacion.verificar_un_hallazgo(h) for h in (INFORMATIVO, CSP)]
    salida = priorizacion.node({"verified_findings": verificados})
    por_tipo = {h["tipo"]: h for h in salida["prioritized_findings"]}

    assert por_tipo["Modern Web Application"]["urgencia_negocio"] == "informativa", (
        "el LLM pidió 'alta' para un informativo -- se acota al techo"
    )
    assert por_tipo["Content Security Policy (CSP) Header Not Set"]["urgencia_negocio"] == "baja", (
        "bajar SÍ se permite: es contexto de negocio legítimo"
    )
    assert "se acotaron al techo determinista" in salida["trace_log"][0]["resultado"]


def test_acotar_urgencia_ignora_valores_invalidos():
    assert acotar_urgencia("critiquísima", "media") == "media"
    assert acotar_urgencia(None, "baja") == "baja"
    assert acotar_urgencia("media", "media") == "media"


# ---------------------------------------------------------------------------
# (d) El reporte determinista sin LLM es legible y contiene los hallazgos
# ---------------------------------------------------------------------------

def test_reporte_sin_llm_es_entregable_no_un_placeholder():
    state = _correr_pipeline([CSP, CORS, INFORMATIVO])
    reporte = state["reporte_final"]

    # Lo que hacía antes, y que ya no puede volver a pasar.
    assert "[Reporte narrativo pendiente" not in reporte
    assert "verified_findings" not in reporte, "no se vuelca el JSON crudo del estado"

    # Estructura de un documento real.
    for seccion in (
        "# Reporte de seguridad",
        "## Resumen de postura",
        "## Hallazgos",
        "## Remediación recomendada",
        "## Alcance y limitaciones",
    ):
        assert seccion in reporte

    # Honesto sobre cómo se generó, sin que eso lo vuelva inútil.
    assert "sin análisis de un modelo de lenguaje" in reporte
    # Remediación concreta y accionable, no "pendiente de redacción".
    assert "Content-Security-Policy" in reporte
    assert "Access-Control-Allow-Origin: *" in reporte
    assert "pendiente de redacción" not in reporte


def test_remediacion_sin_llm_es_determinista_y_declarada():
    state = _correr_pipeline([CORS])
    remediacion_cors = state["remediations"][0]

    assert remediacion_cors["fuente"] == "catalogo_determinista"
    assert "Access-Control-Allow-Origin" in remediacion_cors["remediacion"]
    assert "pendiente de redacción" not in remediacion_cors["remediacion"]
    assert "deterministas" in _trace(state, "remediacion")


def test_reporte_sin_hallazgos_no_promete_invulnerabilidad():
    state = _correr_pipeline([])
    reporte = state["reporte_final"]
    assert "no que el sistema sea invulnerable" in reporte
