"""Verifica el modo open-core (community/fallback) de `agents/cumplimiento.py`.

Contexto (ver `docs/open-core.md`): la taxonomía real de 13 categorías +
mapeo ISO 27001 / Ley 2573 vive en el paquete privado `vigia_core_private`,
que NO se instala en este repo público ni en CI. `agents/cumplimiento.py`
cae a una taxonomía genérica de 3 categorías cuando ese paquete falta.

Este archivo REEMPLAZA a `tests/test_cumplimiento_categorizacion.py` (movido
a `vigia_core_private/tests/`, ver esa carpeta): aquel test asertaba nombres
de categoría exactos de la taxonomía real (`cabeceras_seguridad_faltantes`,
etc.), que es precisamente la IP que se sacó de este repo -- correrlo aquí
fallaría siempre por diseño. Este test en cambio verifica que el modo
degradado sigue siendo funcional y consistente, sin asumir qué paquete está
instalado en la máquina que corre la suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import agents.cumplimiento as cumplimiento

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "eval" / "cumplimiento_fixture_juiceshop.json"


def _cargar_fixture() -> list[dict]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return data["findings"]


def test_modo_es_privado_o_fallback():
    """El módulo siempre sabe en qué modo está -- nunca un estado ambiguo."""
    assert cumplimiento._MODO_CUMPLIMIENTO in ("privado", "fallback_publico")


def test_categorizar_hallazgo_nunca_devuelve_categoria_inventada():
    """Sea cual sea el modo activo (privado o fallback), toda categoría que
    devuelve `_categorizar_hallazgo` debe existir en el `CATEGORIA_MAPEO`
    activo -- el reporte nunca debe intentar buscar una categoría que no
    está en el mapeo (eso reventaría con KeyError en `_resumir_por_categoria`)."""
    for finding in _cargar_fixture():
        categoria = cumplimiento._categorizar_hallazgo(finding)
        assert categoria in cumplimiento.CATEGORIA_MAPEO


def test_reporte_cumplimiento_es_funcional_sobre_datos_reales(monkeypatch):
    """Extremo a extremo sobre los 10 hallazgos reales de Juice Shop: el
    reporte se genera sin excepciones, con la estructura esperada, sin
    importar si `vigia_core_private` está instalado en esta máquina o no."""
    findings = _cargar_fixture()

    resultado = cumplimiento.generar_reporte_cumplimiento(
        findings, contexto_negocio="Tienda de e-commerce de prueba"
    )

    assert resultado["modo"] == cumplimiento._MODO_CUMPLIMIENTO
    assert isinstance(resultado["reporte_markdown"], str) and resultado["reporte_markdown"]
    assert resultado["resumen_por_categoria"], "debe agrupar los hallazgos en al menos una categoría"
    assert resultado["advertencia_alcance_legal"]
    assert resultado["advertencia_iso27001"]

    total_categorizado = sum(entrada["cantidad"] for entrada in resultado["resumen_por_categoria"])
    assert total_categorizado == len(findings)

    # Cobertura de obligaciones: la(s) obligación(es) activas en el modo
    # actual deben aparecer, cualquiera sea su nombre exacto.
    assert resultado["cobertura_ley2573"]


def test_reporte_sin_hallazgos_no_revienta():
    resultado = cumplimiento.generar_reporte_cumplimiento([], contexto_negocio="")
    assert resultado["resumen_por_categoria"] == []
    assert "No hay hallazgos" in resultado["reporte_markdown"]


def test_fallback_publico_no_expone_taxonomia_privada_si_paquete_ausente():
    """Si `vigia_core_private` NO está instalado en esta máquina (el caso
    normal para quien clona el repo público), el mapeo activo debe ser el
    genérico de 3 categorías, nunca las 13 categorías reales -- si esto
    falla, algo está reimportando la taxonomía privada por un camino
    inesperado."""
    if cumplimiento._MODO_CUMPLIMIENTO != "fallback_publico":
        import pytest

        pytest.skip("vigia_core_private está instalado en esta máquina; nada que verificar aquí")

    assert set(cumplimiento.CATEGORIA_MAPEO.keys()) == {
        "hallazgo_tecnico",
        "suplantacion_marca",
        "sin_clasificar",
    }
