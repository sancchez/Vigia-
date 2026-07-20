"""Regresión del bug real: `findings.severidad` valía `"info"` para ~99% de
las filas reales de `ciberseguridad.db` (1290/1298 al momento de encontrar
esto).

Causa raíz confirmada (ver `docs/cumplimiento.md` para el mismo patrón ya
cerrado sobre el campo `tipo`, y `tools/_shared.py::normalize_severity` para
el detalle completo por herramienta): `api/main.py` leía una clave de nivel
superior (`riskdesc` en el escaneo activo checkpointed, `severidad`/
`severity` en `POST /scan`) que NINGUNA de las 5 herramientas reales
(Nuclei, ZAP, Semgrep, Trivy, Grype) pone jamás ahí -- la severidad real
vive dentro de `raw`, con nombre de campo y vocabulario propio de cada
herramienta.

Cada caso de este archivo usa un hallazgo real o realistamente-formado
capturado en esta sesión (`eval/severity_fixture_samples.json` documenta la
procedencia exacta de cada uno: corridas reales de cada herramienta contra
Juice Shop, este mismo repo, o la imagen `node:14`, más filas reales
extraídas de `ciberseguridad.db`) -- nada aquí es inventado a mano.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tools._shared import CANONICAL_SEVERITIES, normalize_severity

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "eval" / "severity_fixture_samples.json"


def _cargar_casos() -> list[dict]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return data["casos"]


_CASOS = _cargar_casos()
_IDS = [f"{c['grupo']}:{c['raw'].get('name') or c['raw'].get('VulnerabilityID') or c['raw'].get('check_id') or c['raw'].get('vulnerability', {}).get('id') or c['raw'].get('template-id')}" for c in _CASOS]


@pytest.mark.parametrize("caso", _CASOS, ids=_IDS)
def test_normalize_severity_sobre_muestras_reales(caso):
    """Cada muestra real de cada herramienta debe mapear a la severidad
    canónica correcta -- no a 'info' por defecto silencioso."""
    resultado = normalize_severity(caso["raw"], caso["herramienta"])
    assert resultado == caso["esperado"]
    assert resultado in CANONICAL_SEVERITIES


def test_zap_sql_injection_real_ya_no_es_info():
    """El caso concreto que motivó este fix: la Inyección SQL real de ZAP
    (scan d46c7342-5c2b-4007-92c7-1b96b58d5609 en ciberseguridad.db) tenía
    severidad nativa 'High' pero quedaba grabada como 'info' por el bug
    viejo (`hallazgo.get('riskdesc', 'info')` -- este hallazgo real de
    `core/view/alerts` nunca trae `riskdesc`)."""
    sqli_real = {
        "name": "SQL Injection",
        "risk": "High",
        "confidence": "Low",
        "url": "http://host.docker.internal:3050/rest/products/search?q=%27%28",
        "pluginId": "40018",
    }
    assert normalize_severity(sqli_real, "zap") == "high"


def test_zap_dos_formas_reales_distintas_mismo_resultado_logico():
    """Confirma que las DOS formas reales de un hallazgo de ZAP (API
    core/view/alerts con `risk` directo, vs. reporte JSON de
    zap-baseline.py/zap-full-scan.py con `riskdesc`) se reconocen ambas,
    sin que una tenga que fingir ser la otra."""
    forma_api = {"name": "Cross-Domain Misconfiguration", "risk": "Medium"}
    forma_reporte = {"name": "Content Security Policy (CSP) Header Not Set", "riskdesc": "Medium (High)", "riskcode": "2"}
    assert normalize_severity(forma_api, "zap") == "medium"
    assert normalize_severity(forma_reporte, "zap-baseline") == "medium"


def test_zap_riskcode_como_ultimo_respaldo_sin_risk_ni_riskdesc():
    """Si un hallazgo de ZAP no trae ni `risk` ni `riskdesc` (variante no
    observada en esta sesión, pero posible según el schema de ZAP), cae a
    `riskcode` numérico como último respaldo antes de rendirse."""
    assert normalize_severity({"riskcode": "3"}, "zap") == "high"
    assert normalize_severity({"riskcode": "0"}, "zap") == "info"


def test_valor_nativo_no_reconocido_no_cae_silenciosamente_a_info(caplog):
    """Núcleo de la regla del punto 2 del encargo: un valor de severidad
    que la función no reconoce NUNCA debe convertirse silenciosamente en
    'info' -- cae a 'medium' (default seguro) y siempre se loguea."""
    with caplog.at_level(logging.WARNING, logger="vigia.severity"):
        resultado = normalize_severity({"risk": "Catastrophic"}, "zap")
    assert resultado == "medium"
    assert resultado != "info"
    assert any("no reconocido" in r.message for r in caplog.records)


def test_herramienta_sin_campo_de_severidad_esperado_no_cae_a_info(caplog):
    """Un hallazgo real de una herramienta reconocida pero sin el campo de
    severidad esperado (dato incompleto/corrupto) tampoco debe convertirse
    en 'info' silencioso."""
    with caplog.at_level(logging.WARNING, logger="vigia.severity"):
        resultado = normalize_severity({"template-id": "algo"}, "nuclei")
    assert resultado == "medium"
    assert any("no trajo severidad" in r.message for r in caplog.records)


def test_herramienta_desconocida_no_cae_a_info(caplog):
    with caplog.at_level(logging.WARNING, logger="vigia.severity"):
        resultado = normalize_severity({"algo": "x"}, "herramienta-inventada")
    assert resultado == "medium"
    assert any("herramienta desconocida" in r.message for r in caplog.records)


def test_raw_none_no_revienta():
    """`agents/verificacion.py` puede generar hallazgos sin `raw` en casos
    borde -- normalize_severity debe degradar con gracia, no lanzar
    excepción."""
    assert normalize_severity(None, "nuclei") == "medium"
