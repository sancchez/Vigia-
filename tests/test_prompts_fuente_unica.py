"""Fase 3 — `agents/prompts.py` como fuente única de los system prompts.

Lo que este archivo protege: que los `.md` de `eval/prompt_versions/` y el
código no vuelvan a divergir en silencio (que es exactamente lo que había
pasado: 8 archivos versionados que ningún `.py` leía). Si alguien rompe la
estructura de un `.md`, o cambia el pin de versión a un archivo inexistente,
esto falla acá y no en producción con un `ImportError` al arrancar la API.

NO se asertan los textos literales de los prompts: eso los volvería a duplicar
en dos lugares, que es el problema original. Se aserta la ESTRUCTURA (parseo
correcto, no vacío, sin restos de markdown) y la INTEGRACIÓN (que cada agente
exponga el mismo texto que devuelve `get_prompt`).
"""

from __future__ import annotations

import pytest

from agents import prompts
from agents.prompts import (
    PromptNoEncontradoError,
    agentes_disponibles,
    get_prompt,
    parsear_prompt,
    ruta_prompt,
)


def test_los_ocho_agentes_del_plan_tienen_prompt_vigente():
    """Los 8 agentes de la sección 7 del plan, ni uno menos."""
    assert agentes_disponibles() == [
        "anti_suplantacion",
        "escaneo",
        "orquestador",
        "priorizacion",
        "recon",
        "remediacion",
        "reporteria",
        "verificacion",
    ]


@pytest.mark.parametrize("agente", agentes_disponibles())
def test_el_archivo_pineado_existe(agente):
    """Un pin en `_VERSION_VIGENTE` que apunte a un archivo que no existe
    reventaría al importar el agente. Mejor que reviente acá."""
    assert ruta_prompt(agente).is_file(), f"falta {ruta_prompt(agente)}"


@pytest.mark.parametrize("agente", agentes_disponibles())
def test_el_prompt_parsea_y_no_queda_markdown_adentro(agente):
    texto = get_prompt(agente)

    assert texto.strip(), "el prompt parseado no puede estar vacío"
    # Señales de que el parseo se comió el archivo entero en vez del bloque:
    assert "```" not in texto, "quedaron fences dentro del prompt"
    assert "## Changelog" not in texto, "el parseo se pasó de la sección '## Prompt'"
    assert not texto.lstrip().startswith("#"), "el parseo agarró el encabezado del .md"
    assert "**Version:**" not in texto
    # Un prompt real de este proyecto no es una línea suelta de tres palabras.
    assert len(texto) > 100, f"el prompt de {agente} parece truncado: {texto!r}"


def test_alias_del_modulo_resuelve_al_mismo_archivo():
    """`agents/antisuplantacion.py` pide "antisuplantacion"; el archivo se
    llama `v1_anti_suplantacion.md`. Ambos nombres deben dar lo mismo."""
    assert get_prompt("antisuplantacion") == get_prompt("anti_suplantacion")


@pytest.mark.parametrize(
    "modulo, agente",
    [
        ("agents.recon", "recon"),
        ("agents.escaneo", "escaneo"),
        ("agents.verificacion", "verificacion"),
        ("agents.orquestador", "orquestador"),
        ("agents.antisuplantacion", "antisuplantacion"),
    ],
)
def test_agente_expone_exactamente_el_prompt_de_la_fuente_unica(modulo, agente):
    """Estos 5 agentes no tienen capa privada: su `SYSTEM_PROMPT` DEBE ser,
    byte por byte, lo que devuelve `get_prompt`."""
    import importlib

    mod = importlib.import_module(modulo)
    assert mod.SYSTEM_PROMPT == get_prompt(agente)


@pytest.mark.parametrize(
    "modulo, agente",
    [
        ("agents.priorizacion", "priorizacion"),
        ("agents.remediacion", "remediacion"),
        ("agents.reporteria", "reporteria"),
    ],
)
def test_agentes_open_core_usan_la_fuente_unica_o_la_capa_privada(modulo, agente):
    """Estos tres SÍ tienen override de `vigia_core_private` (docs/open-core.md).

    En modo fallback público el prompt debe salir de la fuente única; en modo
    privado debe salir del paquete privado y NO coincidir con el `.md` público
    (si coincidiera, el override estaría roto o el texto privado se habría
    filtrado al repo público). Se acepta cualquiera de los dos modos, igual que
    `tests/test_agent_quality_fallback.py::test_modo_open_core_es_detectable`.
    """
    import importlib.util

    mod = importlib.import_module(modulo)
    hay_capa_privada = importlib.util.find_spec(f"vigia_core_private.{agente}") is not None

    if hay_capa_privada:
        assert mod.SYSTEM_PROMPT != get_prompt(agente)
    else:
        assert mod.SYSTEM_PROMPT == get_prompt(agente)


# --- Robustez del parser (casos sintéticos, sin tocar el filesystem) ---------


def test_parseo_toma_solo_el_bloque_de_la_seccion_prompt():
    md = (
        "# Agente X — v1\n\n"
        "- **Version:** v1\n\n"
        "## Prompt\n\n"
        "```\nEste es el prompt real.\n```\n\n"
        "## Changelog\n\n"
        "```\nEsto NO es el prompt.\n```\n"
    )
    assert parsear_prompt(md) == "Este es el prompt real."


def test_parseo_falla_ruidosamente_si_no_hay_seccion_prompt():
    with pytest.raises(PromptNoEncontradoError):
        parsear_prompt("# Agente X\n\n## Changelog\n\n```\nhola\n```\n")


def test_parseo_falla_ruidosamente_si_la_seccion_no_tiene_fences():
    with pytest.raises(PromptNoEncontradoError):
        parsear_prompt("## Prompt\n\nprompt suelto sin bloque de codigo\n\n## Changelog\n")


def test_parseo_falla_ruidosamente_si_el_bloque_esta_vacio():
    with pytest.raises(PromptNoEncontradoError):
        parsear_prompt("## Prompt\n\n```\n\n```\n\n## Changelog\n")


def test_get_prompt_falla_si_la_version_no_existe():
    with pytest.raises(PromptNoEncontradoError):
        get_prompt("recon", version=999)


def test_el_pin_de_version_coincide_con_la_tabla_del_readme():
    """El README de `eval/prompt_versions/` publica una tabla de versiones
    vigentes; si el pin del código y esa tabla se separan, la documentación
    miente sobre qué está corriendo."""
    readme = (prompts.PROMPT_DIR / "README.md").read_text(encoding="utf-8")
    for agente, version in prompts._VERSION_VIGENTE.items():
        archivo = f"`v{version}_{agente}.md`"
        assert archivo in readme, (
            f"el README no lista {archivo} como version vigente de {agente}"
        )
